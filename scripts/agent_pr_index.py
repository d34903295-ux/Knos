#!/usr/bin/env python3
"""agent_pr_index.py -- the Agent PR Index: of PRs by AI coding agents whose body claims tests/CI pass, what did CI
actually say at the head SHA? Same agents, claim regexes and CI verdict as agent_pr_ci.py.

Two steps, so the Pages build never scans:

    python scripts/agent_pr_index.py scan --rows rows.json     # >=2,000 PRs (slow; .github/workflows/index.yml, 6 h)
    python scripts/agent_pr_index.py build --rows rows.json --out index.json   # offline

index.yml uploads index.json as the asset of a GitHub Release tagged index-<date> (a release, not a data commit);
network.yml downloads the newest one into the Pages site.

The scan searches each agent over date windows (GitHub caps a query at 1,000 results), drops PRs on repos owned by
the PR's author or the human who assigned the agent (self repos are not a market observation; the count is kept),
and classifies CI at each head SHA. The build writes:

    {"date", "window", "n_prs", "excluded_self_repo", "overall": {...}, "agents": {name: {claimed_green,
     actually_failed, share, ci95: [lo, hi], by_repo: {repos, failed, share, ci95}}}, "prs": [...], "root"}

`ci95` is the 95% Wilson interval of `share`. `by_repo` counts one pull request per repository (its first in the
window), so one busy repository cannot move the figure; it is the one the docs quote first. `root` is the sha256 Merkle root over the per-PR records, so anyone can
check that the published list is the one that was counted: recompute it with `python scripts/agent_pr_index.py check`.
"""
import argparse
import datetime as dt
import hashlib
import json
import math
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agent_pr_ci  # noqa: E402


def merkle_root(leaves: list[bytes]) -> bytes:
    level = sorted(leaves) or [hashlib.sha256(b"").digest()]
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        level = [hashlib.sha256(level[i] + level[i + 1]).digest() for i in range(0, len(level), 2)]
    return level[0]

PR_KEYS = ("agent", "repo", "number", "sha", "class", "failed_checks", "phrase")
COMPLETED = ("failed", "passed", "other")


def record(c):
    return {k: c.get(k) for k in PR_KEYS}


def leaf(rec):
    return hashlib.sha256(json.dumps(rec, sort_keys=True, ensure_ascii=False).encode()).digest()


def wilson(k, n, z=1.96):
    """95% Wilson score interval for k successes in n trials; None when n == 0."""
    if not n:
        return None
    p = k / n
    d = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(max(0.0, mid - half), 4), round(min(1.0, mid + half), 4)]


def tally(mine):
    green = sum(1 for r in mine if r["class"] in COMPLETED)
    failed = sum(1 for r in mine if r["class"] == "failed")
    return {"claimed_green": green, "actually_failed": failed,
            "share": round(failed / green, 4) if green else None, "ci95": wilson(failed, green)}


def by_repo(mine):
    """One pull request per repository: the first (lowest-numbered) one in the window. A repository whose agent opened
    150 failing pull requests then counts once, like a repository with one; this is the figure to quote."""
    first = {}
    for r in mine:
        key = r["repo"].lower()
        if key not in first or r["number"] < first[key]["number"]:
            first[key] = r
    failed = sum(1 for r in first.values() if r["class"] == "failed")
    n = len(first)
    return {"repos": n, "failed": failed, "share": round(failed / n, 4) if n else None, "ci95": wilson(failed, n)}


def build(rows, date, window, excluded=0):
    """rows: classified candidates with a claim; only those whose CI had finished at the head SHA are counted and
    listed. Deterministic for the same rows (order-independent)."""
    prs = sorted((record(r) for r in rows if r.get("class") in COMPLETED), key=lambda r: (r["repo"].lower(), r["number"]))
    agents = {name: {**tally(mine), "by_repo": by_repo(mine)}
              for name, _ in agent_pr_ci.AGENTS for mine in [[r for r in prs if r["agent"] == name]]}
    root = merkle_root([leaf(r) for r in prs])
    return {"date": date, "window": list(window), "n_prs": len(prs), "excluded_self_repo": excluded,
            "overall": {**tally(prs), "by_repo": by_repo(prs)}, "agents": agents, "prs": prs, "root": root.hex()}


def check(path):
    """Recompute the root from the published per-PR records; exits non-zero if the list was edited after the count."""
    with open(path, encoding="utf-8") as f:
        index = json.load(f)
    got = merkle_root([leaf(r) for r in index["prs"]]).hex()
    if got != index["root"]:
        raise SystemExit(f"{path}: root {index['root']} does not match its {len(index['prs'])} records ({got})")
    return index


def scan(end, days, per_agent, max_seconds):
    agent_pr_ci.ARGS = SimpleNamespace(max_seconds=max_seconds)
    agent_pr_ci.SEARCH_PAUSE = 2.1  # concurrent agents share one 30/min search pacing; gh_get backs off on 403/429
    try:
        kept, n = agent_pr_ci.scan_collect(end, days, per_agent)
    except agent_pr_ci.OutOfTime:
        kept, n = [], {"hits": 0, "excluded": 0, "no_claim": 0}
    agent_pr_ci.run_checks(kept)
    start = (dt.date.fromisoformat(end) - dt.timedelta(days=days - 1)).isoformat()
    return {"window": [start, end], "counts": n,
            "rows": [c for c in kept if c.get("class") not in (None, "error")]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["scan", "build", "check"])
    ap.add_argument("--rows", default="rows.json")
    ap.add_argument("--out", default="_site/index.json")
    ap.add_argument("--end", default=(dt.date.today() - dt.timedelta(days=1)).isoformat())
    ap.add_argument("--days", type=int, default=120)
    ap.add_argument("--per-agent", type=int, default=480)
    ap.add_argument("--max-seconds", type=int, default=3 * 3600)
    a = ap.parse_args()
    if a.step == "scan":
        got = scan(a.end, a.days, a.per_agent, a.max_seconds)
        with open(a.rows, "w", encoding="utf-8") as f:
            json.dump(got, f, ensure_ascii=False)
        print(f"scanned {len(got['rows'])} PRs, counts {got['counts']}", file=sys.stderr)
        return 0
    if a.step == "check":
        index = check(a.out)
        print(f"agent PR index: {index['n_prs']} PRs, root {index['root']} matches", file=sys.stderr)
        return 0
    with open(a.rows, encoding="utf-8") as f:
        got = json.load(f)
    index = build(got["rows"], dt.date.today().isoformat(), got["window"], got["counts"]["excluded"])
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False)
    print(f"agent PR index: {index['n_prs']} PRs, {index['excluded_self_repo']} self-repo PRs excluded, "
          f"root {index['root']}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
