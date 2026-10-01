"""The Tempo escrow through the Python client, on a local anvil: full jobs, the attacks, the guard rails, and the
bench. Skipped without Foundry (the contract's own Foundry suite is test_escrow_tempo.py)."""

from __future__ import annotations

import hashlib

import pytest

pytest.importorskip("eth_account")

from knos.jobs import bench  # noqa: E402
from knos.jobs.tempo import TempoError  # noqa: E402

ANVIL = bench.anvil_path()
pytestmark = pytest.mark.skipif(ANVIL is None, reason="Foundry (anvil) not installed")
U = 1_000_000


@pytest.fixture(scope="module")
def dev():
    with bench.anvil(ANVIL) as url:
        yield bench.tempo_setup(url, review_s=60, cap_units=500 * U)


def _id(tag: str) -> bytes:
    return hashlib.sha256(tag.encode()).digest()


def test_full_job_pays_95_and_5(dev):
    chain, esc, token, buyer, worker, fee = dev
    jid = _id("full")
    before = chain.balance(token, buyer.address)
    esc.post(buyer, jid, 2 * U, 600)
    assert chain.balance(token, buyer.address) == before - 2 * U and esc.job(jid).state == "open"
    esc.claim(worker, jid)
    esc.deliver(worker, jid, _id("work"))
    assert esc.job(jid).result == _id("work")
    esc.accept(buyer, jid)
    assert esc.job(jid).state == "released"
    assert chain.balance(token, fee) >= 100_000 and chain.balance(token, worker.address) >= 1_900_000


def test_attacks_and_guard_rails_revert(dev):
    from eth_account import Account
    chain, esc, token, buyer, worker, fee = dev
    rival = Account.create()
    chain.rpc("eth_sendTransaction", [{"from": chain.rpc("eth_accounts", [])[0], "to": rival.address,
                                       "value": hex(10**18)}])
    jid = _id("attacks")
    esc.post(buyer, jid, U, 600)
    with pytest.raises(TempoError):
        esc.post(buyer, jid, U, 600)                 # the same id twice
    esc.claim(worker, jid)
    with pytest.raises(TempoError):
        esc.claim(rival, jid)                        # a second worker
    with pytest.raises(TempoError):
        esc.deliver(rival, jid, _id("x"))            # delivered by someone else
    esc.deliver(worker, jid, _id("x"))
    for who in (worker, rival):
        with pytest.raises(TempoError):
            esc.accept(who, jid)                     # only the buyer accepts
    with pytest.raises(TempoError):
        esc.release(rival, jid)                      # not before the review window ends
    with pytest.raises(TempoError):
        esc.post(buyer, _id("too big"), 501 * U, 600)   # the per-job cap
    chain.rpc("evm_increaseTime", [61])
    chain.rpc("evm_mine", [])
    esc.release(rival, jid)                          # the buyer went silent: anyone releases to the worker
    assert esc.job(jid).state == "released"


def test_reject_and_refund_return_everything(dev):
    chain, esc, token, buyer, worker, fee = dev
    a, b = _id("reject"), _id("refund")
    start = chain.balance(token, buyer.address)
    esc.post(buyer, a, U, 600)
    esc.post(buyer, b, U, 30)
    esc.claim(worker, a)
    esc.deliver(worker, a, _id("bad"))
    esc.reject(buyer, a)
    esc.claim(worker, b)
    chain.rpc("evm_increaseTime", [31])
    chain.rpc("evm_mine", [])
    esc.refund(buyer, b)
    assert chain.balance(token, buyer.address) == start
    assert (esc.job(a).state, esc.job(b).state) == ("refunded", "refunded")


def test_bench_jobs_tempo_conserves_money():
    got = bench.tempo_local(jobs=5, binary=ANVIL)
    assert got["worker_paid_usdc"] == 4.75 and got["fee_usdc"] == 0.25 and "anvil" in got["where"]


def test_knos_work_takes_a_passkey_style_tempo_job_and_is_paid(dev, tmp_path):
    """The web app's passkey buyer: brief on the relay under its own sha256 (the job id), price in the Tempo escrow,
    a device key to seal to. The reference worker finds it in the Posted logs, claims, delivers sealed; accept pays."""
    import json
    pytest.importorskip("solders.litesvm")
    from nacl.public import PrivateKey, SealedBox
    from knos.jobs.relay import DirRelay
    from knos.jobs.tempo import TempoVenue
    from knos.jobs.worker import Worker
    from _jobharness import LocalLedger
    from _jobharness import Escrow as SolEscrow
    chain, esc, token, buyer, worker, fee = dev
    relay = DirRelay(tmp_path / "relay")
    device = PrivateKey.generate()
    brief = json.dumps({"title": "Tagline", "task": "Write a tagline for a Lagos bakery. Mention Lagos.",
                        "kind": "copy", "checks": {"must_include": ["Lagos"]}, "buyer": buyer.address,
                        "seal_to": bytes(device.public_key).hex()}).encode()
    jid = bytes.fromhex(relay.put_brief(brief))
    esc.post(buyer, jid, U, 600)
    venue = TempoVenue(esc, relay, worker, from_block=0)
    from solders.keypair import Keypair
    w = Worker(LocalLedger(SolEscrow()), relay, Keypair(), lambda p: "Warm bread, Lagos mornings.", tempo=venue)
    assert [o.delivered for o in w.once()] == [True]
    j = esc.job(jid)
    assert j.state == "delivered" and j.worker.lower() == worker.address.lower()
    assert SealedBox(device).decrypt(relay.get_delivery(j.result.hex())) == b"Warm bread, Lagos mornings.\n"
    before = chain.balance(token, worker.address)
    esc.accept(buyer, jid)
    assert chain.balance(token, worker.address) - before == U * 95 // 100
