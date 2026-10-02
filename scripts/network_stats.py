"""The public Knos numbers, counted from Solana devnet: what was funded, what was paid, and to and by whom.

    python scripts/network_stats.py --out _site/stats.json

Every number comes from knos-pay's own log lines in its transaction history (`knos:funded`, `knos:paid`,
`knos:claimed`, `knos:refunded`, `knos:vetoed`) and from its live accounts. Nothing is self-reported.

The split that matters is "outside": a bounty counts as outside only if neither the GitHub account that funded it nor
the one that was paid is one of Knos's own (OWN, below), and they are two different accounts. Bounties Knos funded or
earned itself, and ones where the funder paid themselves, are counted separately and never added to the outside total.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from knos import chain  # noqa: E402
from knos.settle import oidc, pay  # noqa: E402

# GitHub account ids that are Knos's own: the maintainer, and the accounts its live tests run as. Activity that
# touches any of these is "own", never "outside". (ids, not logins: a login can be renamed.)
OWN = frozenset(json.loads((ROOT / "scripts" / "own_github_ids.json").read_text(encoding="utf-8"))["ids"])
EVENT = re.compile(r"Program log: knos:(\w+) (.*)")


def events_of(tx: dict | None) -> list[dict]:
    """knos-pay's log lines in one confirmed transaction, as dicts with "event", "at" and "signer"."""
    if not tx or (tx.get("meta") or {}).get("err") is not None:
        return []
    out = []
    for line in (tx.get("meta") or {}).get("logMessages") or []:
        m = EVENT.match(line)
        if not m:
            continue
        ev = {"event": m.group(1), "at": tx.get("blockTime") or 0,
              "signer": tx["transaction"]["message"]["accountKeys"][0]}
        for part in m.group(2).split():
            k, _, v = part.partition("=")
            ev[k] = int(v) if v.lstrip("-").isdigit() else v
        out.append(ev)
    return out


def history(url: str, limit: int = 1000) -> tuple[list[dict], int]:
    """(events oldest first, transactions left unread because the public RPC throttled)."""
    sigs = [s["signature"] for s in chain.call(url, "getSignaturesForAddress", [str(pay.PAY_ID), {"limit": limit}],
                                               timeout=30) or [] if s.get("err") is None]
    events, unread = [], 0
    for sig in reversed(sigs):
        try:
            tx = chain.call(url, "getTransaction", [sig, {"encoding": "json", "commitment": "confirmed",
                                                          "maxSupportedTransactionVersion": 0}], timeout=30)
        except Exception:  # noqa: BLE001 - still throttled after the backoff: counted, never guessed
            unread += 1
            continue
        events += [{**ev, "tx": sig} for ev in events_of(tx)]
    return events, unread


def summarize(events: list[dict], own: frozenset = OWN) -> dict:
    funded = {}      # (repo, issue) -> the funding event
    paid = []
    for ev in events:
        if ev["event"] == "funded":
            funded[(ev.get("repo"), ev.get("issue"))] = ev
        elif ev["event"] == "paid":
            src = funded.get((ev.get("repo"), ev.get("issue")), {})
            funder = src.get("by", f"wallet:{src.get('signer', '?')}")
            if funder in own or ev.get("author") in own:
                kind = "own"
            elif funder == ev.get("author"):
                kind = "self"
            else:
                kind = "outside"
            paid.append({"repo": ev.get("repo"), "issue": ev.get("issue"), "author": ev.get("author"), "funder": funder,
                         "amount": ev.get("amount", 0), "fee": ev.get("fee", 0), "kind": kind, "tx": ev.get("tx"),
                         "seconds": ev["at"] - src["at"] if src.get("at") and ev.get("at") else None})

    def side(kind: str) -> dict:
        mine = [p for p in paid if p["kind"] == kind]
        took = sorted(p["seconds"] for p in mine if p["seconds"] is not None)
        return {"paid": len(mine), "amount": sum(p["amount"] for p in mine), "fees": sum(p["fee"] for p in mine),
                "repositories": len({p["repo"] for p in mine}), "funders": len({p["funder"] for p in mine}),
                "authors": len({p["author"] for p in mine}),
                "median_seconds_fund_to_paid": int(statistics.median(took)) if took else None}

    count = lambda name: sum(1 for e in events if e["event"] == name)  # noqa: E731
    return {"funded": count("funded"), "funded_amount": sum(e.get("amount", 0) for e in events if e["event"] == "funded"),
            "outside": side("outside"), "own": side("own"), "self_funded": side("self"),
            "claimed": count("claimed"), "claimed_amount": sum(e.get("amount", 0) for e in events if e["event"] == "claimed"),
            "refunded": count("refunded"), "vetoed": count("vetoed"), "recent": paid[-20:][::-1]}


def live(ledger) -> dict:
    """What is in escrow now and what is waiting to be claimed, from the program's accounts."""
    jobs = [pay.read_job(d) for _, d in ledger.program_accounts(pay.PAY_ID, 256)]
    dues = [pay.read_due(d) for _, d in ledger.program_accounts(pay.PAY_ID, 48)]
    return {"open": sum(1 for j in jobs if j and j.state == "open"),
            "open_amount": sum(j.amount for j in jobs if j and j.state == "open"),
            "proven_waiting": sum(1 for j in jobs if j and j.state == "proven"),
            "unclaimed_accounts": sum(1 for d in dues if d), "unclaimed_amount": sum(dues)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="_site/stats.json")
    a = ap.parse_args()
    url = os.environ.get("KNOS_RPC") or chain.CLUSTERS["devnet"]
    data: dict = {"updated": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "cluster": "devnet",
                  "programs": {"knos_oidc": str(oidc.OIDC_ID), "knos_pay": str(pay.PAY_ID)}}
    try:
        events, unread = history(url)
        data.update(summarize(events))
        data["live"] = live(chain.Ledger(url))
        if unread:
            data["error"] = f"{unread} transactions unread (the public RPC throttled); counts are a lower bound"
    except Exception as e:  # noqa: BLE001 - the site still ships, and says the numbers could not be read
        data["error"] = f"{type(e).__name__}: {str(e)[:200]}"
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(data, indent=1), encoding="utf-8")
    print(json.dumps(data, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
