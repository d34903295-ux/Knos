"""Path A (Tier 1): one transaction pays Knos and Sibyl; Sibyl's reference verifier confirms it with RPC reads only;
mainnet stays off without a Sibyl-signed partner config. Run on a local validator with a test "Sibyl" address."""

from __future__ import annotations

import json
import time

import pytest

from _devchain import URL, devchain, funded
from knos import bundle
from knos.pro import sol_budget as sb
from knos.team import rpc
from solders.keypair import Keypair
from solders.system_program import CreateAccountParams, create_account


def test_mainnet_is_off_without_a_signed_config(tmp_path):
    with pytest.raises(bundle.BundleDisabled):
        bundle.partner_config("mainnet")
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    sibyl = Ed25519PrivateKey.generate()
    pub = sibyl.public_key().public_bytes_raw().hex()
    cfg = {"partner": "knos", "chains": {"solana": {"address": "X"}}}
    body = json.dumps(cfg, sort_keys=True, separators=(",", ":")).encode()
    good = tmp_path / "good.json"
    good.write_text(json.dumps({"config": cfg, "signature": sibyl.sign(body).hex()}))
    assert bundle.partner_config("mainnet", good, pub) == cfg
    forged = tmp_path / "forged.json"
    forged.write_text(json.dumps({"config": {**cfg, "chains": {"solana": {"address": "ATTACKER"}}},
                                  "signature": sibyl.sign(body).hex()}))
    with pytest.raises(bundle.BundleDisabled):
        bundle.partner_config("mainnet", forged, pub)


@devchain
def test_one_transaction_pays_both_and_sibyl_can_verify_it():
    owner = funded(3)
    buyer = funded(2)
    knos_addr, sibyl_addr = Keypair().pubkey(), Keypair().pubkey()  # test addresses only
    mint = Keypair()
    lam = rpc.call(URL, "getMinimumBalanceForRentExemption", [sb.MINT_SIZE])
    rpc.send(URL, [create_account(CreateAccountParams(from_pubkey=owner.pubkey(), to_pubkey=mint.pubkey(),
                                                      lamports=lam, space=sb.MINT_SIZE, owner=sb.TOKEN_PROGRAM)),
                   sb.initialize_mint2(mint.pubkey(), 6, owner.pubkey())], owner, [mint])
    rpc.send(URL, [sb.create_ata_idempotent(owner.pubkey(), buyer.pubkey(), mint.pubkey()),
                   sb.mint_to_checked(mint.pubkey(), sb.ata(buyer.pubkey(), mint.pubkey()), owner.pubkey(),
                                      50_000_000, 6)], owner)
    m = bundle.memo("sibyl-account-123", "pro", "month")
    sig = rpc.send(URL, bundle.split_solana_ixs(buyer.pubkey(), mint.pubkey(), 6, knos_addr, 10_000_000, sibyl_addr,
                                                12_000_000, m), buyer)
    for _ in range(60):  # finalized
        st = rpc.call(URL, "getSignatureStatuses", [[sig]])["value"][0]
        if st and st.get("confirmationStatus") == "finalized":
            break
        time.sleep(1)
    seen: set[str] = set()
    ok = bundle.verify_solana(URL, sig, str(sibyl_addr), str(mint.pubkey()), 12_000_000, m, seen)
    assert ok.ok, ok.reason
    assert not bundle.verify_solana(URL, sig, str(sibyl_addr), str(mint.pubkey()), 12_000_000, m, seen).ok  # replay
    other = bundle.memo("someone-else", "pro", "month")
    assert not bundle.verify_solana(URL, sig, str(sibyl_addr), str(mint.pubkey()), 12_000_000, other, set()).ok
    assert not bundle.verify_solana(URL, sig, str(sibyl_addr), str(mint.pubkey()), 13_000_000, m, set()).ok
