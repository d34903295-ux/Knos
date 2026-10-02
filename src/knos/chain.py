"""Solana, as much of it as Knos needs: a JSON-RPC client on the standard library, `solders` for signing, and the
`Ledger` that knos.settle.relay talks to (the tests hand it LiteSVM behind the same four methods).

    KNOS_CLUSTER   devnet (default) or localnet. Mainnet is refused: the programs are not deployed there.
    KNOS_RPC       a custom RPC endpoint for that cluster
    KNOS_RELAY_KEY the key that pays the transaction fees (a JSON array of 64 bytes, or base58); default
                   ~/.knos/relay-key.json, created on first use. It holds no one's money and decides nothing.
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from solders.compute_budget import ID as COMPUTE_BUDGET
from solders.compute_budget import set_compute_unit_limit
from solders.hash import Hash
from solders.instruction import Instruction
from solders.keypair import Keypair
from solders.message import Message
from solders.pubkey import Pubkey
from solders.transaction import Transaction

CLUSTERS = {"devnet": "https://api.devnet.solana.com", "localnet": "http://127.0.0.1:8899"}
MAX_COMPUTE_UNITS = 1_400_000   # the most one transaction may use
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


class Refused(Exception):
    pass


class RpcError(ValueError):
    def __init__(self, message: str, data: Any = None):
        logs = (data or {}).get("logs") if isinstance(data, dict) else None
        super().__init__(message + (" | " + " | ".join(logs[-4:]) if logs else ""))   # a failing program's own words
        self.data = data


def call(url: str, method: str, params: list, timeout: float = 10.0) -> Any:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", "User-Agent": "knos"})
    got: dict = {}
    for wait in (1, 2, 4, 8, None):     # public endpoints rate-limit (429): back off rather than fail
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - the configured cluster endpoint
                got = json.loads(resp.read())
            break
        except urllib.error.HTTPError as e:
            if e.code != 429 or wait is None:
                raise
            time.sleep(wait)
    if "error" in got:
        err = got["error"]
        raise RpcError(str(err.get("message", err)), err.get("data"))
    return got.get("result")


def b58(raw: bytes) -> str:
    n, out = int.from_bytes(raw, "big"), ""
    while n:
        n, r = divmod(n, 58)
        out = _B58[r] + out
    return "1" * (len(raw) - len(raw.lstrip(b"\0"))) + out


def wait(url: str, signature: str, within: float = 60.0, commitment: str = "confirmed") -> dict:
    """Wait until `signature` reaches `commitment`; RpcError if it failed on chain, TimeoutError if it never landed."""
    end = time.monotonic() + within
    good = ("finalized",) if commitment == "finalized" else ("confirmed", "finalized")
    while time.monotonic() < end:
        got = call(url, "getSignatureStatuses", [[signature]])["value"][0]
        if got and got.get("confirmationStatus") in good:
            if got.get("err"):
                raise RpcError(f"transaction failed: {got['err']}", got)
            return got
        time.sleep(0.4)
    raise TimeoutError(f"{signature} not confirmed within {within:.0f}s")


@dataclass
class Ledger:
    """A cluster reached over JSON-RPC."""
    url: str
    commitment: str = "confirmed"

    def send(self, ixs, payer: Keypair, signers: list[Keypair] | None = None) -> str:
        """Sign, send (with preflight, so a failing program returns its logs) and wait for `commitment`."""
        got = call(self.url, "getLatestBlockhash", [{"commitment": "confirmed"}])
        blockhash = Hash.from_string(got["value"]["blockhash"])
        everyone = {bytes(k.pubkey()): k for k in [payer, *(signers or [])]}
        ixs = list(ixs)
        if not any(ix.program_id == COMPUTE_BUDGET for ix in ixs):
            # A cluster gives an instruction 200,000 compute units unless asked; GitHub's RSA signature takes more.
            ixs.insert(0, set_compute_unit_limit(MAX_COMPUTE_UNITS))
        msg = Message.new_with_blockhash(ixs, payer.pubkey(), blockhash)
        raw = base64.b64encode(bytes(Transaction(list(everyone.values()), msg, blockhash))).decode()
        sig = call(self.url, "sendTransaction", [raw, {"encoding": "base64", "preflightCommitment": "confirmed"}])
        wait(self.url, sig, 60.0, self.commitment)
        return sig

    def account(self, address: Pubkey) -> bytes | None:
        got = call(self.url, "getAccountInfo", [str(address), {"encoding": "base64", "commitment": self.commitment}])
        return base64.b64decode(got["value"]["data"][0]) if got["value"] else None

    def program_accounts(self, program: Pubkey, size: int, memcmp: dict[int, bytes] | None = None) -> list[tuple[Pubkey, bytes]]:
        """(address, data) of every account of `program` with this size whose bytes at each offset match."""
        filters: list[dict] = [{"dataSize": size}]
        filters += [{"memcmp": {"offset": off, "bytes": b58(raw)}} for off, raw in (memcmp or {}).items()]
        got = call(self.url, "getProgramAccounts", [str(program), {"encoding": "base64", "commitment": self.commitment,
                                                                   "filters": filters}], timeout=30) or []
        return [(Pubkey.from_string(i["pubkey"]), base64.b64decode(i["account"]["data"][0])) for i in got]

    def now(self) -> int:
        """Unix time of the latest confirmed slot (the clock the programs see), never the local clock."""
        slot = call(self.url, "getSlot", [{"commitment": "confirmed"}])
        for s in (slot, slot - 1, slot - 2):
            try:
                t = call(self.url, "getBlockTime", [s])
            except RpcError:
                continue
            if t is not None:
                return int(t)
        raise RpcError("no block time")


def ledger() -> Ledger:
    c = os.environ.get("KNOS_CLUSTER", "devnet")
    if c not in CLUSTERS:
        raise Refused(f"Knos runs on devnet (or a localnet) only; {c!r} is not available.")
    return Ledger(os.environ.get("KNOS_RPC") or CLUSTERS[c])


def _keypair(text: str) -> Keypair:
    text = text.strip()
    if text.startswith("["):
        return Keypair.from_bytes(bytes(json.loads(text)))
    return Keypair.from_base58_string(text)


def key() -> Keypair:
    """The fee payer: KNOS_RELAY_KEY (KNOS_MEMBER_KEY, its name before 0.3.10, still works), else a key file made on
    first use, readable by its owner only."""
    env = os.environ.get("KNOS_RELAY_KEY") or os.environ.get("KNOS_MEMBER_KEY")
    if env:
        return _keypair(env)
    from . import paths
    p = paths.home() / "relay-key.json"
    try:
        return _keypair(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    k = Keypair()
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(list(bytes(k))))
    return k
