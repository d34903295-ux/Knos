"""Model-based fuzz of the Solana escrow program in the Solana runtime (LiteSVM + real SPL Token). Nightly (`-m slow`).

KNOS_FUZZ_N random steps (default 10,000; KNOS_FUZZ_SEED picks the run) by five funded actors on a pool of job ids:
post (with or without a verifier, sometimes under the minimum or over the cap), claim, deliver, accept, release,
reject, refund, verify_release (right and wrong result hash), pause/unpause, lower the cap, and clock jumps. A plain
Python model of the escrow predicts, before every step, whether the program must accept or refuse it and what every
token account must hold afterwards. Checked after every step:

    the program agrees with the model (no action succeeds that should fail, and none fails that should succeed)
    conservation: every token account matches the model, and their sum never changes
    no double payout: the vault holds exactly the price of every job still open, claimed or delivered, and the fee
                      account exactly the fee of every released job
Every 1,000 steps the episode ends: every job is driven to a terminal state (release or refund, which must succeed)
and the vault must be empty: no stuck funds.
"""

from __future__ import annotations

import hashlib
import os
import random
import time

import pytest

pytest.importorskip("solders.litesvm")

from _jobharness import Escrow  # noqa: E402

from knos.jobs import sol  # noqa: E402

pytestmark = pytest.mark.slow
U = 1_000_000
EPISODE = 1_000
# Read at import: the suite's autouse fixture strips KNOS_* settings from each test's environment.
N = int(os.environ.get("KNOS_FUZZ_N", "10000"))
SEED = int(os.environ.get("KNOS_FUZZ_SEED", "3405"))


class Model:
    def __init__(self, e: Escrow, actors):
        self.e, self.actors = e, actors
        self.bal = {t: e.balance(t) for t in [t for _, t in actors] + [e.fee_token, e.vault]}
        self.jobs: dict[bytes, dict] = {}
        self.paused, self.cap = False, 0
        self.released_fees = 0
        self.tried = self.ok = 0

    def now(self) -> int:
        return int(self.e.svm.get_clock().unix_timestamp)

    def held(self) -> int:
        return sum(j["amount"] for j in self.jobs.values() if j["state"] in ("open", "claimed", "delivered"))

    def pay(self, j: dict, payee_tok) -> None:
        fee = sol.fee_for(j["amount"])
        self.bal[self.e.vault] -= j["amount"]
        self.bal[payee_tok] += j["amount"] - fee
        self.bal[self.e.fee_token] += fee
        self.released_fees += fee
        j["state"] = "released"


def _step(rng: random.Random, m: Model, ids: list[bytes]) -> None:
    e, actors = m.e, m.actors
    a = rng.randrange(100)
    live = [i for i in ids if i in m.jobs and m.jobs[i]["state"] in ("open", "claimed", "delivered")]
    fresh = [i for i in ids if i not in m.jobs]
    if a < 18 and fresh and rng.random() < 0.8:
        jid = rng.choice(fresh)                                   # mostly, new posts on new ids
    elif a >= 18 and live and rng.random() < 0.8:
        jid = rng.choice(live)                                    # mostly, act on jobs under way
    else:
        jid = rng.choice(ids)
    j = m.jobs.get(jid)
    now = m.now()
    tok_of = {bytes(k.pubkey()): t for k, t in actors}
    who, tok = rng.choice(actors)
    if j is not None and rng.random() < 0.6:   # most of the time, the party the job is waiting for acts
        want = {"deliver": j["worker"], "accept": j["buyer"], "reject": j["buyer"], "refund": j["buyer"],
                "verify": j["verifier"]}.get("deliver" if 30 <= a < 42 else "accept" if 42 <= a < 52 else
                                             "verify" if 60 <= a < 72 else "reject" if 72 <= a < 80 else
                                             "refund" if 80 <= a < 88 else "")
        if want is not None:
            who, tok = next((k, t) for k, t in actors if k.pubkey() == want)
    if a < 18:                                                   # post
        amount = rng.choice([U - 1, U, 2 * U, 3 * U, 7 * U])
        work, review = rng.choice([30, 120, 600]), rng.choice([30, 120, 600])
        ver = rng.choice(actors)[0] if rng.random() < 0.5 else None
        ok = (j is None and not m.paused and amount >= sol.MIN_JOB_UNITS and (m.cap == 0 or amount <= m.cap))
        got = e.post(who, tok, jid, amount, work=work, review=review, verifier=ver.pubkey() if ver else None)
        if ok:
            m.jobs[jid] = {"state": "open", "buyer": who.pubkey(), "worker": None, "amount": amount,
                           "deadline": now + work, "review": review, "result": None,
                           "verifier": ver.pubkey() if ver else None}
            m.bal[tok] -= amount
            m.bal[e.vault] += amount
    elif a < 30:                                                 # claim
        ok = j is not None and j["state"] == "open" and now <= j["deadline"]
        got = e.claim(who, jid)
        if ok:
            j["state"], j["worker"] = "claimed", who.pubkey()
    elif a < 42:                                                 # deliver
        ok = j is not None and j["state"] == "claimed" and j["worker"] == who.pubkey()
        got = e.deliver(who, jid)
        if ok:
            j["state"], j["result"], j["deadline"] = "delivered", e.result_hash(jid), now + j["review"]
    elif a < 52:                                                 # accept (sometimes paying the wrong account)
        payee = tok_of[bytes(j["worker"])] if j and j["worker"] and rng.random() < 0.8 else tok
        ok = (j is not None and j["state"] == "delivered" and j["buyer"] == who.pubkey()
              and payee == tok_of[bytes(j["worker"])])
        got = e.accept(who, jid, payee)
        if ok:
            m.pay(j, payee)
    elif a < 60:                                                 # release by anyone after the review window
        payee = tok_of[bytes(j["worker"])] if j and j["worker"] else tok
        ok = j is not None and j["state"] == "delivered" and now > j["deadline"]
        got = e.accept(who, jid, payee, release=True)
        if ok:
            m.pay(j, payee)
    elif a < 72:                                                 # verify_release, right or wrong hash
        payee = tok_of[bytes(j["worker"])] if j and j["worker"] else tok
        signer = next((k for k, _ in actors if j and j["verifier"] == k.pubkey()), who) if rng.random() < 0.7 else who
        right = rng.random() < 0.75
        h = e.result_hash(jid) if right else hashlib.sha256(b"unproven" + jid).digest()
        ok = (j is not None and j["verifier"] is not None and j["verifier"] == signer.pubkey()
              and j["worker"] != signer.pubkey() and j["state"] == "delivered" and right)
        got = e.verify_release(signer, jid, payee, result_hash=h, proof_root=hashlib.sha256(h).digest())
        if ok:
            m.pay(j, payee)
            j["proof"] = hashlib.sha256(h).digest()
    elif a < 80:                                                 # reject inside the review window
        ok = j is not None and j["state"] == "delivered" and j["buyer"] == who.pubkey() and now <= j["deadline"]
        got = e.reject(who, jid, tok)
        if ok:
            m.bal[e.vault] -= j["amount"]
            m.bal[tok] += j["amount"]
            j["state"] = "refunded"
    elif a < 88:                                                 # refund after the work deadline
        ok = (j is not None and j["state"] in ("open", "claimed") and j["buyer"] == who.pubkey()
              and now > j["deadline"])
        got = e.refund(who, jid, tok)
        if ok:
            m.bal[e.vault] -= j["amount"]
            m.bal[tok] += j["amount"]
            j["state"] = "refunded"
    elif a < 89:                                                 # the admin pauses or unpauses (or a stranger tries)
        admin = rng.random() < 0.7
        on = not m.paused and rng.random() < 0.2
        got = e.set_pause(on, admin=None if admin else who)
        ok = admin
        if ok:
            m.paused = on
    elif a < 90 and rng.random() < 0.1:                         # lower the cap (only downward, never under 1 USDC)
        cap = rng.choice([U - 1, 3 * U, 5 * U, 10 * U, 20 * U])
        ok = cap >= sol.MIN_JOB_UNITS and (m.cap == 0 or cap <= m.cap)
        got = e.lower_cap(cap)
        if ok:
            m.cap = cap
    elif a >= 96:                                                # time passes
        e.warp(rng.choice([1, 5, 20, 90]))
        return
    else:
        return
    m.tried += 1
    m.ok += ok
    assert got == ok, f"program {'accepted' if got else 'refused'} what the model {'allows' if ok else 'refuses'}"
    if ok and jid in m.jobs:
        chain = e.job(jid)
        assert chain.state == m.jobs[jid]["state"] and chain.amount == m.jobs[jid]["amount"]


def _check(m: Model, total: int) -> None:
    e = m.e
    for t, want in m.bal.items():
        assert e.balance(t) == want, "a token account differs from the model"
    assert sum(m.bal.values()) == total, "tokens created or destroyed"
    assert e.balance(e.vault) == m.held(), "the vault does not hold exactly the unsettled jobs"
    assert e.balance(e.fee_token) == m.released_fees, "a fee was paid twice or not at all"


def _drain(m: Model) -> None:
    e = m.e
    e.warp(10_000)
    by_key = {bytes(k.pubkey()): (k, t) for k, t in m.actors}
    for jid, j in m.jobs.items():
        if j["state"] == "delivered":
            k, t = by_key[bytes(j["worker"])]
            assert e.accept(k, jid, t, release=True), "a delivered job could not be released"
            m.pay(j, t)
        elif j["state"] in ("open", "claimed"):
            k, t = by_key[bytes(j["buyer"])]
            assert e.refund(k, jid, t), "an undelivered job could not be refunded"
            m.bal[e.vault] -= j["amount"]
            m.bal[t] += j["amount"]
            j["state"] = "refunded"
        assert e.job(jid).state == j["state"] and j["state"] in ("released", "refunded")
    assert e.balance(e.vault) == 0, "funds stuck in escrow"


def test_escrow_fuzz_model_conservation_no_double_payout_no_stuck_funds():
    n, seed = N, SEED
    rng = random.Random(seed)
    e = Escrow()
    actors = [e.party(10 ** 12) for _ in range(5)]
    m = Model(e, actors)
    total = sum(m.bal.values())
    t0, steps, episode = time.perf_counter(), 0, 0
    while steps < n:
        ids = [hashlib.sha256(f"fz-{seed}-{episode}-{k}".encode()).digest() for k in range(150)]
        for _ in range(min(EPISODE, n - steps)):
            _step(rng, m, ids)
            _check(m, total)
            steps += 1
        _drain(m)
        _check(m, total)
        episode += 1
    took = time.perf_counter() - t0
    released = sum(j["state"] == "released" for j in m.jobs.values())
    refunded = sum(j["state"] == "refunded" for j in m.jobs.values())
    proven = sum("proof" in j for j in m.jobs.values())
    print(f"\nescrow fuzz: {steps} steps ({m.tried} transactions: {m.ok} accepted, {m.tried - m.ok} refused, all as "
          f"the model predicted), {episode} episodes, {len(m.jobs)} jobs ({released} released, {proven} of them on "
          f"proof, {refunded} refunded), seed {seed}, {took:.1f} s")
    assert steps == n and released and refunded and (proven or n < 2000)
