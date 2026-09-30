"""The Knos + Sibyl bundle (Path A): one transaction pays both companies, and either can verify it without the other.

The buyer funds their own Knos wallet, confirms locally, and Knos signs ONE transaction with two transfers (Knos's
part to the Knos address, Sibyl's part to the address Sibyl publishes for this purpose) and the memo

    knos-bundle:v1:<sha256(sibyl_account_id)>:<plan>:<period>

`verify_solana` is the open-source reference verifier: pure RPC reads, no Knos server. Sibyl can run it to confirm a
payment before switching Pro on for the account whose id hashes to the memo.

Switch-on rule: on mainnet, Path A refuses until Knos finds a partner config signed by a key Sibyl publishes (chains,
receiving addresses, endpoint). Until then it runs only on devnet, localnet, Moderato and anvil, paying a test
"Sibyl" address. Knos never sends value to any Sibyl address Sibyl has not published for this purpose.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

TEST_NETWORKS = {"devnet", "localnet", "moderato", "anvil"}


class BundleDisabled(Exception):
    pass


def memo(sibyl_account_id: str, plan: str, period: str) -> str:
    return f"knos-bundle:v1:{hashlib.sha256(sibyl_account_id.encode()).hexdigest()}:{plan}:{period}"


def partner_config(network: str, path: Path | None = None, sibyl_public_key_hex: str = "") -> dict:
    """The Sibyl-signed partner config. On a test network a local test config is allowed; on mainnet the config must
    carry a valid Ed25519 signature from Sibyl's published key, or Path A is disabled."""
    if network in TEST_NETWORKS:
        if path is None:
            raise BundleDisabled("no test partner config given")
        return json.loads(Path(path).read_text(encoding="utf-8"))
    if not sibyl_public_key_hex or path is None:
        raise BundleDisabled("Path A is off on mainnet until Sibyl publishes a signed partner config")
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    body = json.dumps(doc["config"], sort_keys=True, separators=(",", ":")).encode()
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(sibyl_public_key_hex)).verify(bytes.fromhex(doc["signature"]),
                                                                                       body)
    except (InvalidSignature, ValueError, KeyError):
        raise BundleDisabled("the partner config's signature does not verify against Sibyl's published key") from None
    return doc["config"]


@dataclass
class Verdict:
    ok: bool
    reason: str = ""


def verify_solana(url: str, signature: str, sibyl_owner: str, mint: str, min_units: int, expected_memo: str,
                  seen: set[str]) -> Verdict:
    """Sibyl's check of a bundle payment on Solana, with RPC reads only: the transaction is final and succeeded, it
    paid `sibyl_owner`'s token account at least `min_units` of `mint`, it carries exactly `expected_memo`, and it was
    not claimed before."""
    from .team import rpc
    if signature in seen:
        return Verdict(False, "already claimed")
    tx = rpc.call(url, "getTransaction", [signature, {"encoding": "jsonParsed", "commitment": "finalized",
                                                     "maxSupportedTransactionVersion": 0}])
    if not tx:
        return Verdict(False, "not final (or not found)")
    meta = tx.get("meta") or {}
    if meta.get("err"):
        return Verdict(False, "the transaction failed")
    pre = {(b["owner"], b["mint"]): int(b["uiTokenAmount"]["amount"]) for b in meta.get("preTokenBalances", [])}
    post = {(b["owner"], b["mint"]): int(b["uiTokenAmount"]["amount"]) for b in meta.get("postTokenBalances", [])}
    got = post.get((sibyl_owner, mint), 0) - pre.get((sibyl_owner, mint), 0)
    if got < min_units:
        return Verdict(False, f"paid {got} units to Sibyl, needs {min_units}")
    memos = [ix.get("parsed") for ix in tx["transaction"]["message"]["instructions"]
             if ix.get("program") == "spl-memo"]
    if expected_memo not in memos:
        return Verdict(False, "memo does not match this account and plan")
    seen.add(signature)
    return Verdict(True)


def split_solana_ixs(buyer, mint, decimals: int, knos_owner, knos_units: int, sibyl_owner, sibyl_units: int,
                     memo_text: str) -> list:
    """The one transaction: two TransferChecked from the buyer's wallet (to each company's token account) + Memo."""
    from .pro import sol_budget as sb
    src = sb.ata(buyer, mint)
    return [sb.create_ata_idempotent(buyer, knos_owner, mint), sb.create_ata_idempotent(buyer, sibyl_owner, mint),
            sb.transfer_checked(src, mint, sb.ata(knos_owner, mint), buyer, knos_units, decimals),
            sb.transfer_checked(src, mint, sb.ata(sibyl_owner, mint), buyer, sibyl_units, decimals),
            sb.memo(memo_text, buyer)]
