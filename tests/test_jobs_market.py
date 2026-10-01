"""The job market end to end in the Solana runtime: a brief on a relay, the price in escrow, one claim wins, a sealed
deliverable checked against the hash on chain, and the 95/5 split on accept."""

from __future__ import annotations

import pytest

pytest.importorskip("solders.litesvm")

from knos.jobs import checks, market  # noqa: E402
from knos.jobs.ledger import LocalLedger  # noqa: E402
from knos.jobs.localsvm import Escrow  # noqa: E402
from knos.jobs.relay import DirRelay, RelayError  # noqa: E402

USDC = 1_000_000


@pytest.fixture
def net(tmp_path):
    env = Escrow()
    return env, LocalLedger(env), DirRelay(tmp_path / "relay")


def test_post_claim_deliver_accept(net):
    env, ledger, relay = net
    buyer, buyer_tok = env.party(10 * USDC)
    worker, worker_tok = env.party()
    rival, _ = env.party()
    brief = market.Brief("Dedupe a CSV", "Remove duplicate rows.", kind="csv", checks={"expected": "a,b\n1,2\n"})
    jid = market.post(ledger, relay, buyer, brief, 2 * USDC)
    assert env.balance(buyer_tok) == 8 * USDC and env.balance(env.vault) == 2 * USDC

    found = market.open_jobs(ledger, relay)
    assert [b.title for _, b in found] == ["Dedupe a CSV"] and found[0][1].buyer == str(buyer.pubkey())

    assert market.claim(ledger, worker, jid)
    assert not market.claim(ledger, rival, jid)          # the escrow is the mutex
    assert market.open_jobs(ledger, relay) == []

    out = b"a,b\n1,2\n"
    assert checks.run("csv", brief.checks, out.decode())[0]
    h = market.deliver(ledger, relay, worker, jid, buyer.pubkey(), out)
    assert relay.get_delivery(h) != out           # sealed: the relay cannot read it
    assert market.fetch_delivery(ledger, relay, buyer, jid) == out

    market.accept(ledger, buyer, jid)
    assert env.balance(worker_tok) == 1_900_000 and env.balance(env.fee_token) == 100_000
    assert env.balance(env.vault) == 0 and market.job(ledger, jid).state == "released"


def test_tampered_delivery_and_brief_are_refused(net, tmp_path):
    env, ledger, relay = net
    buyer, _ = env.party(5 * USDC)
    worker, _ = env.party()
    jid = market.post(ledger, relay, buyer, market.Brief("Copy", "Write a tagline."), USDC)
    market.claim(ledger, worker, jid)
    h = market.deliver(ledger, relay, worker, jid, buyer.pubkey(), b"Fast, honest work.")
    (relay.root / "deliveries" / h).write_bytes(market.seal_for(buyer.pubkey(), b"swapped"))
    with pytest.raises(ValueError):
        market.fetch_delivery(ledger, relay, buyer, jid)
    h = market.job(ledger, jid).brief.hex()
    (relay.root / "briefs" / h).write_bytes(b"{}")
    with pytest.raises(RelayError):
        relay.get_brief(h)


def test_reject_refunds_and_silent_buyer_releases(net):
    env, ledger, relay = net
    buyer, buyer_tok = env.party(4 * USDC)
    worker, worker_tok = env.party()
    a = market.post(ledger, relay, buyer, market.Brief("A", "x"), USDC, review_s=60)
    b = market.post(ledger, relay, buyer, market.Brief("B", "y"), USDC, review_s=60)
    for j in (a, b):
        market.claim(ledger, worker, j)
        market.deliver(ledger, relay, worker, j, buyer.pubkey(), b"done")
    market.reject(ledger, buyer, a)
    assert env.balance(buyer_tok) == 3 * USDC
    with pytest.raises(RuntimeError):
        market.release(ledger, worker, b)                  # not before the review window ends
    env.warp(61)
    market.release(ledger, worker, b)
    assert env.balance(worker_tok) == 950_000


def test_buyer_preferences_are_checked_before_delivery():
    prefs = ["Never use exclamation marks.", "Always sign off with — Ada"]
    assert not checks.run("copy", {}, "Great news!\n— Ada", prefs)[0]
    assert not checks.run("copy", {}, "Great news.", prefs)[0]
    assert checks.run("copy", {}, "Great news.\n— Ada", prefs)[0]
    assert checks.run("python", {"tests": "from solution import add\nassert add(2, 3) == 5\n"},
                      "def add(a, b):\n    return a + b\n")[0]
    assert not checks.run("json", {"expected": {"a": 1}}, '{"a": 2}')[0]


def test_reference_worker_does_the_job_and_learns_nothing_it_was_not_given(net):
    from knos.jobs import models
    from knos.jobs.worker import Worker
    env, ledger, relay = net
    buyer, _ = env.party(5 * USDC)
    wkey, wtok = env.party()
    tests = "from solution import add\nassert add(2, 3) == 5\n"
    brief = market.Brief("add()", "Write add(a, b).", kind="python", checks={"tests": tests},
                         scripted_answer="def add(a, b):\n    return a + b\n")
    jid = market.post(ledger, relay, buyer, brief, USDC)
    bad = market.Brief("Tagline", "One line.", kind="copy", preferences=["Never use exclamation marks."],
                       scripted_answer="Ship it!")
    jid_bad = market.post(ledger, relay, buyer, bad, USDC)
    got = {o.title: o for o in Worker(ledger, relay, wkey, models.scripted).once()}
    assert got["add()"].delivered and not got["Tagline"].delivered   # never delivers work that fails the checks
    assert b"return a + b" in market.fetch_delivery(ledger, relay, buyer, jid)
    market.accept(ledger, buyer, jid)
    assert env.balance(wtok) == 950_000
    assert market.job(ledger, jid_bad).state == "claimed"            # left to expire: the buyer gets a refund
    env.warp(3601)
    market.refund(ledger, buyer, jid_bad)
    assert market.job(ledger, jid_bad).state == "refunded"


def test_buyer_memory_captures_preferences_without_sibyl(tmp_path):
    from knos.jobs.buyer_memory import BuyerMemory, capture
    assert capture("Write a launch tweet. Never use exclamation marks. Keep it under 20 words.") == [
        "Never use exclamation marks.", "Keep it under 20 words."]
    mem = BuyerMemory("wallet-A", root=tmp_path, use_sibyl=False)
    assert mem.learn_from("Always sign off with — Ada") == ["Always sign off with — Ada"]
    assert mem.learn_from("Always sign off with — Ada") == []        # once
    assert BuyerMemory("wallet-B", root=tmp_path, use_sibyl=False).preferences() == []   # one tenant per wallet
    assert mem.forget("Always sign off with — Ada") and mem.preferences() == []


def test_http_relay_is_content_addressed(tmp_path):
    import threading
    from knos.jobs.relay import HttpRelay, serve
    srv = serve(tmp_path, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        r = HttpRelay(f"http://127.0.0.1:{srv.server_address[1]}")
        h = r.put_brief(b'{"title":"x"}')
        assert r.get_brief(h) == b'{"title":"x"}'
        d = r.put_delivery(b"sealed")
        assert r.get_delivery(d) == b"sealed"
        with pytest.raises(RelayError):
            r.get_brief("0" * 64)
    finally:
        srv.shutdown()


def test_the_four_agent_calls(net):
    from knos.jobs import api
    env, ledger, relay = net
    buyer, _ = env.party(5 * USDC)
    worker, _ = env.party()
    posted = api.post_job("Name it", "Name a CLI for hiring agents.", 1.0, "copy", '{"must_include": ["knos"]}',
                          ctx=(ledger, relay, buyer))
    jid = posted.split()[2]
    assert jid in api.find_jobs(ctx=(ledger, relay, worker))
    assert "CHECKS" in api.claim_job(jid, ctx=(ledger, relay, worker))
    assert "someone else" in api.claim_job(jid, ctx=(ledger, relay, buyer))
    assert api.deliver_job(jid, "hire", ctx=(ledger, relay, worker)).startswith("Not delivered")
    assert api.deliver_job(jid, "knos", ctx=(ledger, relay, buyer)) == "knos: this job is not yours to deliver"
    assert api.deliver_job(jid, "knos jobs", ctx=(ledger, relay, worker)).startswith("Delivered")


def test_worker_sdk_example_is_short_and_works(net):
    from pathlib import Path
    from knos.jobs import api
    root = Path(__file__).resolve().parents[1] / "examples"
    for name in ("worker.py", "worker.mjs"):
        code = [ln for ln in (root / name).read_text(encoding="utf-8").splitlines() if ln.strip()]
        assert len(code) <= 10, name
    env, ledger, relay = net
    buyer, _ = env.party(2 * USDC)
    worker, _ = env.party()
    market.post(ledger, relay, buyer, market.Brief("Echo", "Say ok.", kind="copy", checks={"must_include": ["ok"]}),
                USDC)
    done = api.serve(lambda prompt: "ok", ctx=(ledger, relay, worker), once=True)
    assert [o.delivered for o in done] == [True]


def test_reputation_is_recomputed_from_job_accounts(net):
    from knos.jobs import stats
    env, ledger, relay = net
    buyer, _ = env.party(10 * USDC)
    good, _ = env.party()
    flaky, _ = env.party()
    ids = [market.post(ledger, relay, buyer, market.Brief(f"j{i}", "x"), USDC, work_s=100, review_s=50)
           for i in range(4)]
    for jid, w in zip(ids, (good, good, flaky, flaky)):
        market.claim(ledger, w, jid)
    for jid in ids[:3]:
        market.deliver(ledger, relay, good if jid in ids[:2] else flaky, jid, buyer.pubkey(), b"x")
    market.accept(ledger, buyer, ids[0])
    market.reject(ledger, buyer, ids[2])
    env.warp(101)
    market.refund(ledger, buyer, ids[3])
    market.release(ledger, buyer, ids[1])
    rows = {r["agent"]: r for r in stats.agents(ledger.jobs(), ledger.now())}
    assert rows[str(good.pubkey())]["paid"] == 2 and rows[str(good.pubkey())]["acceptance"] == 1.0
    assert (rows[str(flaky.pubkey())]["rejected"], rows[str(flaky.pubkey())]["expired"]) == (1, 1)
    n = stats.network(ledger.jobs())
    assert n["jobs"] == 4 and n["paid_to_agents_usdc"] == 1.9 and n["agents"] == 2


def test_a_web_buyer_gets_the_work_sealed_to_their_browser_key(net):
    from nacl.public import PrivateKey, SealedBox
    env, ledger, relay = net
    buyer, _ = env.party(2 * USDC)
    worker, _ = env.party()
    browser = PrivateKey.generate()
    jid = market.post(ledger, relay, buyer, market.Brief("x", "y", seal_to=bytes(browser.public_key).hex()), USDC)
    market.claim(ledger, worker, jid)
    from knos.jobs import api
    assert api.deliver_job(jid.hex(), "the work", ctx=(ledger, relay, worker)).startswith("Delivered")
    blob = relay.get_delivery(market.job(ledger, jid).result.hex())
    assert SealedBox(browser).decrypt(blob) == b"the work"


def test_preference_capture_dev_72_of_72_and_heldout(tmp_path):
    import json
    from pathlib import Path
    from knos.jobs.buyer_memory import BuyerMemory, capture
    data = Path(__file__).parent / "data"
    dev = json.loads((data / "preferences_dev.json").read_text(encoding="utf-8"))
    recalled = 0
    for buyer, spec in dev["buyers"].items():
        mem = BuyerMemory(buyer, root=tmp_path, use_sibyl=False)
        for i, q in enumerate(dev["noise"]):          # the buyer's past requests, preferences said once among them
            mem.learn_from(q)
            if i in (6, 15, 23):
                mem.learn_from(spec["prefs"][(6, 15, 23).index(i)])
        got = mem.preferences()
        assert len(got) == 3 and all(sum(g in p for g in got) == 1 for p in spec["prefs"])   # each one, nothing else
        recalled += sum(len(mem.preferences()) for job, who in dev["jobs"] if who == buyer)
    assert recalled == 72
    held = json.loads((data / "preferences_heldout.json").read_text(encoding="utf-8"))
    assert sum(bool(capture(p)) for p in held["preferences"]) >= 21     # measured 21/24 on first run, never tuned
    assert not [n for n in held["noise"] if capture(n)]


def test_brief_lint_says_what_to_fix_before_money_moves():
    from knos.jobs.brief_lint import lint
    assert lint(market.Brief("x", "Write a function that parses ISO dates and rejects bad ones.", kind="python"),
                USDC) == ["Python job with no tests (--tests FILE): acceptance can't be checked before delivery."]
    got = lint(market.Brief("x", "Tagline, use exclamation marks. ASAP", kind="copy"), 10_000,
               ["Never use exclamation marks."])
    assert any("contradict" in w for w in got) and any("below" in w for w in got) and any("Urgency" in w for w in got)
    assert lint(market.Brief("x", "Write a two line tagline for a lamp shop in Leeds.", kind="copy",
                             checks={"must_include": ["Leeds"]}), USDC) == []


def test_sibyl_pro_is_bought_by_knos_at_12_dollars_of_fees(net, tmp_path):
    from knos.jobs import perks
    env, ledger, relay = net
    buyer, _ = env.party(400 * USDC)
    worker, _ = env.party()
    checkout = perks.SimulatedCheckout(tmp_path / "perks.json")
    for price in (100, 100, 39):                                 # fees 5 + 5 + 1.95 = 11.95: not yet
        jid = market.post(ledger, relay, buyer, market.Brief("j", "x"), price * USDC)
        market.claim(ledger, worker, jid)
        market.deliver(ledger, relay, worker, jid, buyer.pubkey(), b"x")
        market.accept(ledger, buyer, jid)
    w = str(buyer.pubkey())
    assert perks.claim(ledger.jobs(), w, checkout) == [] and perks.status(ledger.jobs(), w, checkout)["fees_paid_usdc"] == 11.95
    jid = market.post(ledger, relay, buyer, market.Brief("j", "x"), USDC)   # + 0.05 = 12.00
    market.claim(ledger, worker, jid)
    market.deliver(ledger, relay, worker, jid, buyer.pubkey(), b"x")
    rejected = market.post(ledger, relay, buyer, market.Brief("r", "x"), 100 * USDC)
    market.claim(ledger, worker, rejected)
    market.deliver(ledger, relay, worker, rejected, buyer.pubkey(), b"x")
    market.reject(ledger, buyer, rejected)                     # refunded jobs pay no fee and count for nothing
    market.accept(ledger, buyer, jid)
    got = perks.claim(ledger.jobs(), w, checkout)
    assert len(got) == 1 and got[0].simulated
    assert perks.claim(ledger.jobs(), w, checkout) == []       # once per $12
    assert perks.status(ledger.jobs(), w, checkout)["months_granted"] == 1
