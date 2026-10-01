"""The Solana escrow program in the Solana runtime (LiteSVM + real SPL Token): pay only for accepted work.

Every attack from the live Tempo run is ported, plus Solana-specific ones (paying an attacker's token account, a fake
fee account, dust on the job address, an attacker's vault), and 1,000 random interleavings that must conserve every
token and leave nothing stuck. Runs in about a second; no validator, no network.
"""

from __future__ import annotations

import hashlib
import random

import pytest

pytest.importorskip("solders.litesvm")

from knos.jobs.localsvm import Escrow  # noqa: E402

PRICE = 5_000_000  # $5 in 6-decimal units


@pytest.fixture(scope="module")
def env():
    e = Escrow(fee_bps=500)
    e.buyer, e.b_tok = e.party(10 ** 12)
    e.worker, e.w_tok = e.party()
    e.rival, e.r_tok = e.party()
    e.attacker, e.a_tok = e.party()
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

    from knos.jobs import sol
    e, j = env, jid("dust")
    assert e.send([transfer(TransferParams(from_pubkey=e.attacker.pubkey(), to_pubkey=sol.job_pda(e.pid, j),
                                           lamports=e.svm.minimum_balance_for_rent_exemption(0)))],
                  e.attacker, [e.attacker])
    assert e.post(e.buyer, e.b_tok, j, PRICE), "dust on the job address must not block posting"
    assert not e.post(e.buyer, e.b_tok, jid("foreign-vault"), PRICE, vault=e.a_tok)


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
        a = rng.randrange(9)
        if a == 0:
            e.post(who, tok, j, rng.randint(1, 3) * 1_000_000, work=rng.choice([5, 60]), review=rng.choice([5, 60]))
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
