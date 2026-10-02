"""Write the Agent PR Index numbers in the docs from docs/bench.json, the one source.

    python scripts/bench_docs.py           rewrite the marked blocks
    python scripts/bench_docs.py --check   exit 1 if a doc has drifted from the source (the test suite runs this)

A block is `<!-- bench:market -->` ... `<!-- /bench:market -->` in README.md or docs/BENCH.md.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ["README.md", "docs/BENCH.md"]


def blocks(src: dict) -> dict[str, str]:
    return {"market": market_index(src["market"]["index"])}


def _pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def market_index(ix: dict) -> str:
    """The Agent PR Index scan (scripts/agent_pr_index.py): one pull request per repository first (a busy repository
    counts once), then every pull request; shares with 95% Wilson intervals, per agent and overall."""
    o, r = ix["overall"], ix["overall"]["by_repo"]
    lines = [f"**Agent PR Index, {ix['date']}:** in {r['repos']:,} repositories, the first pull request by an AI coding "
             f"agent whose description said tests or CI pass had **a failing check in {r['failed']:,} ({_pct(r['share'])})** "
             f"(95% Wilson interval {_pct(r['ci95'][0])}–{_pct(r['ci95'][1])}). Counting every such pull request "
             f"instead of one per repository, it is {o['actually_failed']:,} of {o['claimed_green']:,} ({_pct(o['share'])}); "
             f"that figure leans on a few busy repositories, so the per-repository one is the one to quote. Pull requests "
             f"created {ix['window'][0]} – {ix['window'][1]} whose CI had finished at the head commit; "
             f"{ix['excluded_self_repo']:,} on repositories owned by the pull request's author or the person who "
             f"assigned the agent were left out. A failing check is GitHub's record, not a judgment of why it failed. "
             f"Published as `index.json` on the Pages site; rebuilt every 6 hours by `.github/workflows/index.yml`.", "",
             "| agent | repositories | first claiming PR failed CI | 95% interval | all claiming PRs | failed CI |",
             "|---|---|---|---|---|---|"]
    for label, a in ix["agents"]:
        if a["claimed_green"]:
            b = a["by_repo"]
            lines.append(f"| {label} | {b['repos']:,} | {b['failed']:,} ({_pct(b['share'])}) | "
                         f"{_pct(b['ci95'][0])}–{_pct(b['ci95'][1])} | {a['claimed_green']:,} | "
                         f"{a['actually_failed']:,} ({_pct(a['share'])}) |")
    lines.append(f"| **all** | **{r['repos']:,}** | **{r['failed']:,} ({_pct(r['share'])})** | "
                 f"**{_pct(r['ci95'][0])}–{_pct(r['ci95'][1])}** | {o['claimed_green']:,} | "
                 f"{o['actually_failed']:,} ({_pct(o['share'])}) |")
    return "\n".join(lines)


def apply(text: str, gen: dict[str, str]) -> str:
    def repl(m):
        name = m.group(1)
        return f"<!-- bench:{name} -->\n{gen[name]}\n<!-- /bench:{name} -->" if name in gen else m.group(0)
    return re.sub(r"<!-- bench:([\w-]+) -->.*?<!-- /bench:\1 -->", repl, text, flags=re.S)


def main(check: bool = False) -> int:
    gen = blocks(json.loads((ROOT / "docs" / "bench.json").read_text(encoding="utf-8")))
    drift = []
    for d in DOCS:
        p = ROOT / d
        old = p.read_text(encoding="utf-8")
        new = apply(old, gen)
        if new != old:
            drift.append(d)
            if not check:
                p.write_text(new, encoding="utf-8")
    if check and drift:
        print("benchmark numbers drifted from docs/bench.json in: " + ", ".join(drift))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main("--check" in sys.argv))
