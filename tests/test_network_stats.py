"""The public numbers: counted from knos-pay's own log lines, with Knos's own activity and self-funded bounties kept
out of the "outside" total."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import network_stats  # noqa: E402


def tx(at: int, signer: str, *lines: str, err=None) -> dict:
    return {"blockTime": at, "meta": {"err": err, "logMessages": ["Program X invoke [1]", *[f"Program log: {x}" for x in lines]]},
            "transaction": {"message": {"accountKeys": [signer, "other"]}}}


def test_events_come_only_from_successful_transactions():
    good = tx(100, "relayer", "knos:funded repo=5 issue=7 amount=50000000 mode=0 by=900", "something else")
    assert network_stats.events_of(good) == [{"event": "funded", "at": 100, "signer": "relayer", "repo": 5, "issue": 7,
                                             "amount": 50000000, "mode": 0, "by": 900}]
    assert network_stats.events_of(tx(100, "r", "knos:paid repo=5 issue=7 author=1 amount=1 fee=1", err={"x": 1})) == []
    assert network_stats.events_of(None) == []


def test_outside_excludes_knos_own_accounts_and_self_funding():
    own = frozenset({900})
    events = []
    for t in [
        tx(100, "relayer", "knos:funded repo=5 issue=7 amount=50000000 mode=0 by=900"),      # Knos funds its own repo
        tx(400, "relayer", "knos:paid repo=5 issue=7 author=111 amount=48750000 fee=1250000"),
        tx(500, "relayer", "knos:funded repo=6 issue=1 amount=20000000 mode=0 by=222"),      # a stranger funds
        tx(1100, "relayer", "knos:paid repo=6 issue=1 author=333 amount=19500000 fee=500000"),   # another is paid
        tx(1200, "relayer", "knos:funded repo=8 issue=2 amount=10000000 mode=0 by=444"),     # pays themselves
        tx(1300, "relayer", "knos:paid repo=8 issue=2 author=444 amount=9750000 fee=250000"),
        tx(1400, "sponsorWallet", "knos:funded repo=9 issue=3 amount=5000000 mode=0"),       # a wallet sponsors
        tx(1500, "relayer", "knos:paid repo=9 issue=3 author=555 amount=4875000 fee=125000"),
        tx(1600, "relayer", "knos:claimed user=333 amount=19500000"),
        tx(1700, "relayer", "knos:funded repo=6 issue=4 amount=7000000 mode=1 by=222"),
        tx(1800, "relayer", "knos:refunded amount=7000000"),
    ]:
        events += network_stats.events_of(t)
    s = network_stats.summarize(events, own)
    assert s["funded"] == 5 and s["funded_amount"] == 92_000_000
    assert s["own"]["paid"] == 1 and s["self_funded"]["paid"] == 1
    assert s["outside"] == {"paid": 2, "amount": 24_375_000, "fees": 625_000, "repositories": 2, "funders": 2,
                            "authors": 2, "median_seconds_fund_to_paid": 350}
    assert s["claimed"] == 1 and s["claimed_amount"] == 19_500_000 and s["refunded"] == 1 and s["vetoed"] == 0
    assert s["recent"][0]["funder"] == "wallet:sponsorWallet" and s["recent"][0]["kind"] == "outside"


def test_a_payout_to_a_knos_account_is_never_outside():
    events = network_stats.events_of(tx(1, "r", "knos:funded repo=1 issue=1 amount=1000000 mode=0 by=222"))
    events += network_stats.events_of(tx(2, "r", "knos:paid repo=1 issue=1 author=900 amount=950000 fee=50000"))
    s = network_stats.summarize(events, frozenset({900}))
    assert s["outside"]["paid"] == 0 and s["own"]["paid"] == 1
