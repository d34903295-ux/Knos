#!/usr/bin/env python3
"""agent_pr_index.py -- the Agent PR Index, rebuilt with the Pages site (every 30 minutes).

For recent PRs by AI coding agents whose body claims tests/CI pass, what did CI actually say at the head SHA? Uses the
classification in agent_pr_ci.py (same agents, same claim regexes, same CI verdict), over the last --days days, capped
at about --cap PRs. Writes JSON for the web app's front door:

    {"date", "window": [start, end], "agents": {name: {claimed_green, actually_failed, share}},
     "prs": [{agent, repo, number, sha, class, failed_checks, phrase}], "root",
     "attestation"?, "signature"?, "attest_error"?}

`root` is the sha256 Merkle root (knos.proof.receipt.merkle_root: leaves sorted) over the per-PR records. With env
KNOS_ATTEST_KEY (a JSON keypair) the root is attested on Solana devnet SAS, so anyone can check the published list
is the one that was counted.

    python scripts/agent_pr_index.py --out _site/index.json
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agent_pr_ci  # noqa: E402
from knos.proof.receipt import merkle_root  # noqa: E402

DEVNET = "https://api.devnet.solana.com"
PR_KEYS = ("agent", "repo", "number", "sha", "class", "failed_checks", "phrase")
COMPLETED = ("failed", "passed", "other")


def record(c):
    return {k: c.get(k) for k in PR_KEYS}


def leaf(rec):
    return hashlib.sha256(json.dumps(rec, sort_keys=True, ensure_ascii=False).encode()).digest()


def build(rows, date, window):
    """rows: classified candidates with a claim. Deterministic for the same rows (order-independent)."""
    prs = sorted((record(r) for r in rows), key=lambda r: (r["repo"].lower(), r["number"]))
    agents = {}
    for name, _ in agent_pr_ci.AGENTS:
        mine = [r for r in prs if r["agent"] == name]
        green = sum(1 for r in mine if r["class"] in COMPLETED)
        failed = sum(1 for r in mine if r["class"] == "failed")
        agents[name] = {"claimed_green": green, "actually_failed": failed,
                        "share": round(failed / green, 4) if green else None}
    root = merkle_root([leaf(r) for r in prs])
    return {"date": date, "window": list(window), "agents": agents, "prs": prs, "root": root.hex()}


def attest(index, key_json):
    from solders.keypair import Keypair

    from knos.proof import receipt
    key = Keypair.from_bytes(bytes(json.loads(key_json)))
    sig, att = receipt.publish(DEVNET, key, bytes.fromhex(index["root"]), "", f"agent-pr-index {index['date']}")
    index.update({"attestation": str(att), "signature": str(sig), "attester": str(key.pubkey())})


def scan(days, cap, max_seconds):
    agent_pr_ci.ARGS = SimpleNamespace(max_seconds=max_seconds)
    end = dt.date.today()
    per = max(1, cap // len(agent_pr_ci.AGENTS))
    cfg = {"end": end.isoformat(), "days": days, "windows": 1, "per_window": min(100, per)}
    try:
        cands = agent_pr_ci.collect(cfg)
    except agent_pr_ci.OutOfTime:
        cands = []
    claimed = [c for c in cands if c["phrase"]][:cap]
    agent_pr_ci.run_checks(claimed)
    rows = [c for c in claimed if c.get("class") not in (None, "error")]
    return rows, agent_pr_ci.windows(cfg)[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="_site/index.json")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--cap", type=int, default=200)
    ap.add_argument("--max-seconds", type=int, default=420)
    a = ap.parse_args()
    rows, window = scan(a.days, a.cap, a.max_seconds)
    index = build(rows, dt.date.today().isoformat(), window)
    if os.environ.get("KNOS_ATTEST_KEY"):
        try:
            attest(index, os.environ["KNOS_ATTEST_KEY"])
        except Exception as e:  # the site still ships; the missing attestation is visible in the JSON
            index["attest_error"] = str(e)[:300]
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False)
    print(f"agent PR index: {len(index['prs'])} PRs, root {index['root']}, "
          f"attestation {index.get('attestation') or index.get('attest_error') or 'none'}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
