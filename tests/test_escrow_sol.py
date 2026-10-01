"""The Solana escrow program in the Solana runtime (LiteSVM + real SPL Token): pay only for accepted (or proven) work.

Every attack from the live Tempo run is ported, plus Solana-specific ones (paying an attacker's token account, a fake
fee account, dust on the job address, an attacker's vault), the 0.3.4 additions (paid on proof by a named verifier,
the minimum job and fee floor, the pause and the per-job cap, jobs posted before the upgrade), and 1,000 random
interleavings that must conserve every token and leave nothing stuck. Runs in seconds; no validator, no network.
The 10,000-step run is tests/test_escrow_fuzz.py (slow).
"""

from __future__ import annotations

import hashlib
import random
import struct

import pytest

pytest.importorskip("solders.litesvm")

from _jobharness import Escrow  # noqa: E402

from knos.jobs import sol  # noqa: E402

PRICE = 5_000_000  # $5 in 6-decimal units
U = 1_000_000


@pytest.fixture(scope="module")
def env():
    e = Escrow(fee_bps=500)
    e.buyer, e.b_tok = e.party(10 ** 12)
    e.worker, e.w_tok = e.party()
    e.rival, e.r_tok = e.party()
    e.attacker, e.a_tok = e.party()
    e.verifier, e.v_tok = e.party()
    return e


def jid(tag: str) -> bytes:
    return hashlib.sha256(tag.encode()).digest()


def test_full_jobs_pay_the_worker_95_percent_and_knos_5(env):
    w0, f0 = env.balance(env.w_tok), env.balance(env.fee_token)
    for i in range(20):
        j = jid(f"full-{i}")
        assert env.post(env.buyer, env.b_tok, j, PRICE)
        assert env.job(j).state == "open"
        assert env.claim(env.worker, j)
        assert env.deliver(env.worker, j)
        assert env.accept(env.buyer, j, env.w_tok)
        assert env.job(j).state == "released"
    assert env.balance(env.w_tok) - w0 == 20 * PRICE * 95 // 100
    assert env.balance(env.fee_token) - f0 == 20 * PRICE * 5 // 100


ATTACKS = ["second_claim_by_rival", "deliver_by_non_worker", "refund_before_deadline", "accept_by_worker",
           "accept_by_attacker", "release_before_review_ends", "accept_paying_attacker_token_account",
           "accept_with_fake_fee_account", "reject_by_worker", "double_accept", "repost_same_id"]


@pytest.mark.parametrize("attack", ATTACKS)
def test_every_attack_is_refused(env, attack):
    e = env
    j = jid("attack-" + attack)
    assert e.post(e.buyer, e.b_tok, j, PRICE) and e.claim(e.worker, j)
    vault0 = e.balance(e.vault)
    if attack == "second_claim_by_rival":
        assert not e.claim(e.rival, j)
    elif attack == "deliver_by_non_worker":
        assert not e.deliver(e.attacker, j)
    elif attack == "refund_before_deadline":
        assert not e.refund(e.buyer, j, e.b_tok)
    else:
        assert e.deliver(e.worker, j)
        if attack == "accept_by_worker":
            assert not e.accept(e.worker, j, e.w_tok)
        elif attack == "accept_by_attacker":
            assert not e.accept(e.attacker, j, e.a_tok)
        elif attack == "release_before_review_ends":
            assert not e.accept(e.attacker, j, e.w_tok, release=True)
        elif attack == "accept_paying_attacker_token_account":
            assert not e.accept(e.buyer, j, e.a_tok)
        elif attack == "accept_with_fake_fee_account":
            assert not e.accept(e.buyer, j, e.w_tok, fee_token=e.a_tok)
        elif attack == "reject_by_worker":
            assert not e.reject(e.worker, j, e.w_tok)
        elif attack == "double_accept":
            assert e.accept(e.buyer, j, e.w_tok)
            assert not e.accept(e.buyer, j, e.w_tok)
            return
        elif attack == "repost_same_id":
            assert not e.post(e.buyer, e.b_tok, j, PRICE)
    assert e.balance(e.vault) == vault0, "an attack moved money"


def test_reject_refunds_the_buyer_in_full(env):
    e, j = env, jid("reject")
    b0 = e.balance(e.b_tok)
    assert e.post(e.buyer, e.b_tok, j, PRICE) and e.claim(e.worker, j) and e.deliver(e.worker, j)
    assert e.reject(e.buyer, j, e.b_tok)
    assert e.balance(e.b_tok) == b0 and e.job(j).state == "refunded"


def test_a_silent_buyer_cannot_stall_payment(env):
    e, j = env, jid("silent")
    w0 = e.balance(e.w_tok)
    assert e.post(e.buyer, e.b_tok, j, PRICE, review=40) and e.claim(e.worker, j) and e.deliver(e.worker, j)
    assert not e.accept(e.worker, j, e.w_tok, release=True)
    e.warp(41)
    assert e.accept(e.worker, j, e.w_tok, release=True)
    assert e.balance(e.w_tok) - w0 == PRICE * 95 // 100


def test_no_delivery_refunds_after_the_deadline(env):
    e, j = env, jid("undelivered")
    b0 = e.balance(e.b_tok)
    assert e.post(e.buyer, e.b_tok, j, PRICE, work=20)
    assert not e.refund(e.buyer, j, e.b_tok)
    e.warp(21)
    assert not e.claim(e.rival, j)
    assert e.refund(e.buyer, j, e.b_tok)
    assert e.balance(e.b_tok) == b0


def test_dust_and_a_foreign_vault_do_not_work(env):
    from solders.system_program import TransferParams, transfer
    e, j = env, jid("dust")
    assert e.send([transfer(TransferParams(from_pubkey=e.attacker.pubkey(), to_pubkey=sol.job_pda(e.pid, j),
                                           lamports=e.svm.minimum_balance_for_rent_exemption(0)))],
                  e.attacker, [e.attacker])
    assert e.post(e.buyer, e.b_tok, j, PRICE), "dust on the job address must not block posting"
    assert not e.post(e.buyer, e.b_tok, jid("foreign-vault"), PRICE, vault=e.a_tok)


# ---- paid on proof -------------------------------------------------------------------------------------------------

def _delivered(e, tag: str, verifier=None, price: int = PRICE) -> bytes:
    j = jid(tag)
    assert e.post(e.buyer, e.b_tok, j, price, verifier=verifier.pubkey() if verifier else None)
    assert e.claim(e.worker, j) and e.deliver(e.worker, j)
    return j


def test_paid_on_proof_the_verifier_releases_with_no_buyer_step(env):
    e = env
    w0, f0 = e.balance(e.w_tok), e.balance(e.fee_token)
    j = _delivered(e, "proof-ok", e.verifier)
    assert e.job(j).verifier == e.verifier.pubkey() and e.job(j).proof is None
    root = hashlib.sha256(b"merkle root of the proof").digest()
    assert e.verify_release(e.verifier, j, e.w_tok, proof_root=root)
    got = e.job(j)
    assert got.state == "released" and got.proof == root
    assert e.balance(e.w_tok) - w0 == PRICE * 95 // 100 and e.balance(e.fee_token) - f0 == PRICE * 5 // 100


def test_verifier_releases_unproven_work_refused(env):
    e = env
    j = _delivered(e, "proof-wrong-hash", e.verifier)
    vault0 = e.balance(e.vault)
    assert not e.verify_release(e.verifier, j, e.w_tok, result_hash=hashlib.sha256(b"other work").digest())
    assert e.job(j).state == "delivered" and e.balance(e.vault) == vault0
    assert e.verify_release(e.verifier, j, e.w_tok)          # the committed hash: released


@pytest.mark.parametrize("who", ["buyer", "worker", "attacker"])
def test_only_the_named_verifier_can_verify_release(env, who):
    e = env
    j = _delivered(e, "proof-not-verifier-" + who, e.verifier)
    assert not e.verify_release(getattr(e, who), j, e.w_tok)
    assert e.job(j).state == "delivered"


def test_verify_release_needs_a_verifier_and_a_delivery(env):
    e = env
    j = _delivered(e, "proof-none")                          # posted without a verifier
    assert not e.verify_release(e.verifier, j, e.w_tok)
    assert not e.verify_release(e.buyer, j, e.w_tok)
    k = jid("proof-early")
    assert e.post(e.buyer, e.b_tok, k, PRICE, verifier=e.verifier.pubkey())
    assert not e.verify_release(e.verifier, k, e.w_tok, result_hash=bytes(32))   # open: nothing delivered
    assert e.claim(e.worker, k)
    assert not e.verify_release(e.verifier, k, e.w_tok, result_hash=bytes(32))   # claimed: nothing delivered
    assert e.job(k).state == "claimed"


def test_verify_release_pays_once(env):
    e = env
    j = _delivered(e, "proof-double", e.verifier)
    assert e.verify_release(e.verifier, j, e.w_tok)
    vault0 = e.balance(e.vault)
    assert not e.verify_release(e.verifier, j, e.w_tok, proof_root=b"\x02" * 32)
    assert not e.accept(e.buyer, j, e.w_tok)
    e.warp(1000)
    assert not e.accept(e.rival, j, e.w_tok, release=True)
    assert e.balance(e.vault) == vault0


def test_verify_release_cannot_pay_anyone_but_the_worker(env):
    e = env
    j = _delivered(e, "proof-payee", e.verifier)
    assert not e.verify_release(e.verifier, j, e.v_tok)                       # the verifier's own account
    assert not e.verify_release(e.verifier, j, e.w_tok, fee_token=e.a_tok)    # a fake fee account
    assert e.job(j).state == "delivered"


def test_a_worker_cannot_verify_its_own_work(env):
    e = env
    j = jid("proof-self")
    assert e.post(e.buyer, e.b_tok, j, PRICE, verifier=e.worker.pubkey())
    assert e.claim(e.worker, j) and e.deliver(e.worker, j)
    assert not e.verify_release(e.worker, j, e.w_tok)
    assert e.accept(e.buyer, j, e.w_tok)                     # the buyer still can


def test_a_silent_verifier_leaves_every_old_path_open(env):
    e = env
    a = _delivered(e, "proof-silent-accept", e.verifier)
    assert e.accept(e.buyer, a, e.w_tok)                     # the buyer accepts as before
    b = _delivered(e, "proof-silent-reject", e.verifier)
    b0 = e.balance(e.b_tok)
    assert e.reject(e.buyer, b, e.b_tok) and e.balance(e.b_tok) - b0 == PRICE
    c = _delivered(e, "proof-silent-release", e.verifier)
    e.warp(41)
    assert e.accept(e.rival, c, e.w_tok, release=True)       # anyone after the review window
    d = jid("proof-silent-refund")
    assert e.post(e.buyer, e.b_tok, d, PRICE, work=20, verifier=e.verifier.pubkey())
    e.warp(21)
    assert e.refund(e.buyer, d, e.b_tok)                     # nothing delivered: refund


# ---- economics -----------------------------------------------------------------------------------------------------

def test_below_minimum_post_refused(env):
    e = env
    assert not e.post(e.buyer, e.b_tok, jid("min-1"), U - 1)
    assert not e.post(e.buyer, e.b_tok, jid("min-0"), 0)
    assert e.post(e.buyer, e.b_tok, jid("min-ok"), U)
    assert e.config()["min_amount"] == U and e.config()["min_fee"] == 50_000


def test_fee_floor_applied():
    e = Escrow(min_amount=100_000)                           # a config where the floor binds: 5% of 0.5 = 0.025
    buyer, btok = e.party(10 * U)
    worker, wtok = e.party()
    for tag, price, fee in (("half", 500_000, 50_000), ("one", U, 50_000), ("three", 3 * U, 150_000)):
        j = jid("floor-" + tag)
        f0, w0 = e.balance(e.fee_token), e.balance(wtok)
        assert e.post(buyer, btok, j, price) and e.claim(worker, j) and e.deliver(worker, j)
        assert e.accept(buyer, j, wtok)
        assert e.balance(e.fee_token) - f0 == fee == sol.fee_for(price, 500, 50_000, 100_000)
        assert e.balance(wtok) - w0 == price - fee
    assert e.balance(e.vault) == 0


def test_paused_post_refused_but_settlement_still_works():
    e = Escrow()
    buyer, btok = e.party(100 * U)
    worker, wtok = e.party()
    verifier, _ = e.party()
    a, b, c, d = (jid(f"pause-{k}") for k in "abcd")
    assert e.post(buyer, btok, a, U) and e.claim(worker, a) and e.deliver(worker, a)
    assert e.post(buyer, btok, b, U, verifier=verifier.pubkey()) and e.claim(worker, b) and e.deliver(worker, b)
    assert e.post(buyer, btok, c, U) and e.claim(worker, c) and e.deliver(worker, c)
    assert e.post(buyer, btok, d, U, work=20)
    assert not e.set_pause(True, admin=buyer)                # admin only
    assert e.set_pause(True) and e.config()["paused"]
    assert not e.post(buyer, btok, jid("pause-new"), U)
    assert e.accept(buyer, a, wtok)
    assert e.verify_release(verifier, b, wtok)
    assert e.reject(buyer, c, btok)
    e.warp(21)
    assert e.refund(buyer, d, btok)
    assert e.balance(e.vault) == 0
    assert e.set_pause(False) and e.post(buyer, btok, jid("pause-new"), U)


def test_cap_enforced_and_only_lowered_by_the_admin():
    e = Escrow()
    buyer, btok = e.party(100 * U)
    assert e.config()["max_amount"] == 0 and e.post(buyer, btok, jid("cap-none"), 50 * U)   # 0 = no cap
    assert not e.lower_cap(10 * U, admin=buyer)
    assert not e.lower_cap(0)                                # "no cap" cannot come back
    assert not e.lower_cap(U - 1)                            # never under the minimum job
    assert e.lower_cap(10 * U) and e.config()["max_amount"] == 10 * U
    assert not e.post(buyer, btok, jid("cap-over"), 10 * U + 1)
    assert e.post(buyer, btok, jid("cap-at"), 10 * U)
    assert not e.lower_cap(11 * U)                           # only downward
    assert e.lower_cap(5 * U) and not e.post(buyer, btok, jid("cap-6"), 6 * U)


def test_init_is_once_and_the_retired_init_is_refused(env):
    from solders.instruction import Instruction
    e = env
    assert not e.send([sol.init2(e.pid, e.attacker.pubkey(), e.a_tok)], e.attacker, [e.attacker])
    assert not e.send([Instruction(e.pid, bytes([0]) + struct.pack("<H", 0), [])], e.attacker, [e.attacker])
    assert e.config()["admin"] == e.admin.pubkey()


def test_init2_after_an_upgrade_is_the_old_admins_alone():
    from solders.account import Account
    e = Escrow()                                             # then roll its config back to "just upgraded"
    admin = e.keypair()
    attacker = e.keypair()
    fee_tok = e.token_account(admin.pubkey())
    legacy = bytes(admin.pubkey()) + bytes(e.mint.pubkey()) + bytes(fee_tok) + struct.pack("<HB", 500, 255)
    e.svm.set_account(sol.legacy_config_pda(e.pid), Account(10 ** 9, legacy, e.pid, False, 0))
    e.svm.set_account(sol.config_pda(e.pid), Account(0, b"", sol.SYSTEM, False, 0))   # as before Init2 ran
    assert not e.send([sol.init2(e.pid, attacker.pubkey(), fee_tok)], attacker, [attacker])
    assert e.send([sol.init2(e.pid, admin.pubkey(), fee_tok)], admin, [admin])
    assert e.config()["admin"] == admin.pubkey() and e.config()["fee_token"] == fee_tok


def test_a_job_posted_before_the_upgrade_still_settles(env):
    """A 153-byte job from 0.3.1 (no verifier field): accept/release/refund work; it pays the old 5% even under the
    new minimum; nobody can verify-release it."""
    from solders.account import Account
    e = env
    j = jid("legacy-job")
    now = int(e.svm.get_clock().unix_timestamp)
    amount = 10_000                                          # a 0.01 USDC job from before the minimum
    raw = (bytes([3]) + bytes(e.buyer.pubkey()) + bytes(e.worker.pubkey()) + struct.pack("<Qqq", amount, now + 100, 40)
           + jid("legacy-brief") + e.result_hash(j))
    assert len(raw) == sol.LEGACY_JOB_LEN
    e.svm.set_account(sol.job_pda(e.pid, j), Account(e.svm.minimum_balance_for_rent_exemption(153), raw, e.pid,
                                                     False, 0))
    e.mint_to(e.vault, amount)
    got = e.job(j)
    assert got.state == "delivered" and got.verifier is None and got.amount == amount
    assert not e.verify_release(e.verifier, j, e.w_tok)
    w0, f0 = e.balance(e.w_tok), e.balance(e.fee_token)
    assert e.accept(e.buyer, j, e.w_tok)
    assert e.balance(e.w_tok) - w0 == 9_500 and e.balance(e.fee_token) - f0 == 500
    assert e.job(j).state == "released" and len(bytes(e.svm.get_account(sol.job_pda(e.pid, j)).data)) == 153


# ---- conservation --------------------------------------------------------------------------------------------------

def test_1000_random_interleavings_conserve_every_token():
    e = Escrow()
    actors = [e.party(10 ** 9) for _ in range(4)]
    ids = [jid(f"fz-{k}") for k in range(60)]
    toks = [t for _, t in actors] + [e.fee_token, e.vault]
    total = lambda: sum(e.balance(t) for t in toks)  # noqa: E731
    t0 = total()
    rng = random.Random(31)
    for _ in range(1000):
        j = rng.choice(ids)
        who, tok = rng.choice(actors)
        a = rng.randrange(10)
        if a == 0:
            e.post(who, tok, j, rng.randint(1, 3) * 1_000_000, work=rng.choice([5, 60]), review=rng.choice([5, 60]),
                   verifier=rng.choice(actors)[0].pubkey() if rng.random() < 0.5 else None)
        elif a == 1:
            e.claim(who, j)
        elif a == 2:
            e.deliver(who, j)
        elif a in (3, 4):
            e.accept(who, j, tok, release=a == 4)
        elif a == 5:
            e.reject(who, j, tok)
        elif a == 6:
            e.refund(who, j, tok)
        elif a == 7:
            got = e.job(j)
            if got and got.worker:
                e.verify_release(who, j, e.accounts[bytes(got.worker)])
        else:
            e.warp(rng.choice([1, 10, 70]))
        assert total() == t0, "tokens created or destroyed"
    by_key = {bytes(k.pubkey()): (k, t) for k, t in actors}
    e.warp(1000)
    for j in ids:
        got = e.job(j)
        if got is None:
            continue
        if got.state == "delivered":
            k, t = by_key[bytes(got.worker)]
            assert e.accept(k, j, t, release=True)
        elif got.state in ("open", "claimed"):
            k, t = by_key[bytes(got.buyer)]
            assert e.refund(k, j, t)
    assert all(e.job(j).state in ("released", "refunded") for j in ids if e.job(j))
    assert e.balance(e.vault) == 0, "funds stuck in escrow"
