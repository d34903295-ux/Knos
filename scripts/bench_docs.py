"""Write every benchmark number in the docs from docs/bench.json, the one source.

    python scripts/bench_docs.py           rewrite the marked blocks
    python scripts/bench_docs.py --check   exit 1 if a doc has drifted from the source (the test suite runs this)

A block is `<!-- bench:NAME -->` ... `<!-- /bench:NAME -->` in README.md, docs/BENCH.md or docs/WHY.md.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ["README.md", "docs/BENCH.md", "docs/WHY.md"]


def blocks(src: dict) -> dict[str, str]:
    a = src["acceptance"]
    runs = a["runs"]
    sonnet = {r["memory"].split(" (")[0]: r for r in runs if r["worker"] == "Claude Sonnet"}
    gem = [r for r in runs if r["worker"].startswith("Gemini")]
    head = (f"On a {a['jobs']}-job benchmark (measured {a['date']}), buyers accepted "
            f"{sonnet['preferences captured when said']['accepted']} of {a['jobs']} jobs with Knos's buyer memory and "
            f"{sonnet['none']['accepted']} without it (Claude Sonnet); with a live Gemini worker "
            f"({gem[0]['worker'].split(' (')[0].replace('Gemini ', '')}), "
            f"{gem[0]['accepted']} and {gem[1]['accepted']}.")
    table = ["| worker | buyer memory | preferences recalled | task correct | accepted |", "|---|---|---|---|---|"]
    table += [f"| {r['worker']} | {r['memory']} | {r['prefs']} | {r['correct']} | **{r['accepted']}/{a['jobs']}** |"
              for r in runs]
    rc = src["recall"]
    recall = [f"{rc['benchmark']}, measured {rc['date']}.", "",
              "| | baseline (0.3.1) | dev | **held-out** |", "|---|---|---|---|"]
    recall += [f"| {r['what']} | {r['baseline']} | {r['dev']} | **{r['heldout']}** |" for r in rc["rows"]]
    out = {"acceptance-headline": head, "acceptance-table": "\n".join(table), "recall-table": "\n".join(recall),
           "models": "Modelled, not measured: " + src["models"]["advisory_arm"]}
    if src.get("market"):
        m = src["market"]
        out["market"] = m["sentence"]
    return out


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
