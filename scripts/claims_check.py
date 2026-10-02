"""Check every number in the pitch-facing text against docs/facts.json, and every fact against its source.

    python scripts/claims_check.py --offline   # file-backed facts (the test suite runs this)
    python scripts/claims_check.py             # also the live facts: devnet and GitHub

Pitch-facing text is README.md, the home page's first view (web/index.html), and docs/submission/*.md. Every number in
it must be one a fact in docs/facts.json says ("say"), and every fact must hold:

    {"say": ["27.2%"], "what": "...", "json": "docs/bench.json", "path": "market.index.overall.share", "equals": 0.2715}
    {"say": ["21"], "what": "...", "file": "docs/TAMPER.md", "has": "fooled 17/21"}
    {"say": ["1,470"], "what": "...", "source": "https://...", "read": "2026-10-02"}      an outside number, cited
    {"say": [...], "what": "...", "live": "immutable"}                                   checked on devnet

Numbers that are not claims are ignored: versions, dates, clock times in the scripts, list numbering, names such as
RS256, and anything inside code or a link's address. The generated benchmark table is checked by bench_docs.py.
Exit 1 on any number without a fact, any fact whose source no longer says it, or (online) a live fact that fails.
"""

from __future__ import annotations

import html
import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

PITCH = ["README.md", "web/index.html", "docs/submission/SUBMISSION.md", "docs/submission/pitch_script.md",
         "docs/submission/demo_script.md"]
SENTENCE = "AI agent work gets paid only when GitHub's own signature, checked by Solana, proves it passed."
NUMBER = re.compile(r"\$?\d[\d,]*(?:\.\d+)?%?")
NOT_CLAIMS = [
    r"<!-- bench:(\w+) -->.*?<!-- /bench:\1 -->",            # generated; bench_docs.py --check covers it
    r"```.*?```", r"`[^`\n]*`", r"<pre.*?</pre>", r"<code>.*?</code>",   # code
    r"\]\([^)]*\)", r"https?://\S+",                           # link addresses
    r"\b\d+\.\d+\.\d+\b", r"\bKnos 0\.\d+\b", r"\b0\.\d–0\.\d\.\d\b",     # versions
    r"\b\d{1,2}(?:–\d{1,2})? (?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*(?: \d{4})?\b", r"\b(?:Sep|Oct)[a-z]* 20\d\d\b",
    r"\b20[12]\d\b",                                           # years
    r"\(\d:\d\d\)",                                            # clock times in the scripts
    r"(?m)^\s*\d+\.\s",                                        # list numbering
    r"(?m)^#+ \d+\.\s", r"\bsection \d+\b", r"\babout \d+ words\b",       # headings, cross-references, the word count
    r"\b0\.\d and\b",                                         # "0.2 and 0.3.0-0.3.9"
    r"bounty 20\b", r"\b20\.00 USDC\b",                        # the amount typed in the example
    r"#\d+\b|#N\b", r"\bRS256\b|\bSHA-256\b|\bRSA-\d+\b|\bHS256\b|\buid \d+\b|\b360px\b",
]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def pitch_text(path: str) -> str:
    text = read(path)
    if path.endswith(".html"):
        m = re.search(r'<section id="view-check".*?</section>', text, re.S)
        text = m.group(0) if m else ""
    for pat in NOT_CLAIMS:
        text = re.sub(pat, " ", text, flags=re.S)
    if path.endswith(".html"):
        text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    return text


def numbers(path: str) -> list[tuple[str, str]]:
    """(number, the line it is on) for every number in the file's pitch-facing text."""
    out = []
    for line in pitch_text(path).splitlines():
        for m in NUMBER.finditer(line):
            tok = m.group(0).rstrip(",.")
            if tok and tok not in ("$",):
                out.append((tok, " ".join(line.split())[:140]))
    return out


# ---- checking a fact ----------------------------------------------------------------------------------------------

def _dig(obj, path: str):
    for part in path.split("."):
        obj = obj[int(part)] if isinstance(obj, list) else obj[part]
    return obj


def _rpc(method: str, params: list):
    import os
    url = os.environ.get("KNOS_RPC", "https://api.devnet.solana.com")
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    with urllib.request.urlopen(urllib.request.Request(url, body, {"Content-Type": "application/json"}), timeout=20) as r:  # noqa: S310
        return json.load(r)["result"]


def live_immutable() -> tuple[bool, str]:
    """Both programs are deployed on devnet and have no upgrade authority."""
    import base64

    from knos import mainnet_check as mc
    from knos.settle import oidc

    def account(addr: str):
        v = _rpc("getAccountInfo", [addr, {"encoding": "base64", "commitment": "confirmed"}])["value"]
        return (v["owner"], base64.b64decode(v["data"][0])) if v else None
    fetch = mc.Fetch(account=account, verified=lambda n: None, program_checks=lambda: (False, ""), get=lambda u: None,
                     audit=lambda: (False, ""))
    got = [(name, *mc.program_data(fetch, oidc.IDS[name])[:2]) for name in mc.PROGRAMS]
    ok = all(deployed and authority is None for _n, deployed, authority in got)
    return ok, "; ".join(f"{n}: {'not deployed' if not d else 'immutable' if a is None else f'upgradeable by {a}'}" for n, d, a in got)


def live_run() -> tuple[bool, str]:
    """A whole bounty has run on the deployed programs: at least one GitHub account has been paid on devnet."""
    import base64

    from knos.settle import pay
    got = _rpc("getProgramAccounts", [str(pay.PAY_ID), {"encoding": "base64", "commitment": "confirmed",
                                                         "filters": [{"dataSize": 32}]}]) or []
    paid = sum(pay.read_rep(base64.b64decode(a["account"]["data"][0])).paid_jobs for a in got)
    return paid > 0, f"{paid} payment(s) to {len(got)} GitHub account(s) recorded by knos-pay on devnet"


LIVE = {"immutable": live_immutable, "run": live_run}


def check(fact: dict, offline: bool) -> tuple[bool | None, str]:
    """(ok, detail); ok is None for a fact that is not checked in this mode."""
    if "json" in fact:
        got = _dig(json.loads(read(fact["json"])), fact["path"])
        return got == fact["equals"], f"{fact['json']} {fact['path']} = {got!r}"
    if "file" in fact:
        text = read(fact["file"])
        found = re.search(fact["matches"], text) if "matches" in fact else fact["has"] in text
        return bool(found), f"{fact['file']} {'has' if found else 'does not have'} {fact.get('has') or fact.get('matches')!r}"
    if "source" in fact:
        ok = fact["source"].startswith("https://") and bool(fact.get("read"))
        return ok, f"cited: {fact['source']} (read {fact.get('read')})"
    if "live" in fact:
        if offline:
            return None, f"live: {fact['live']}"
        return LIVE[fact["live"]]()
    return False, "a fact needs json, file, source or live"


def main(argv: list[str] | None = None) -> int:
    offline = "--offline" in (argv if argv is not None else sys.argv[1:])
    facts = json.loads(read("docs/facts.json"))["facts"]
    allowed = {s for f in facts for s in f["say"]}
    fails = 0
    used: set[str] = set()
    for path in PITCH:
        for tok, line in numbers(path):
            used.add(tok)
            if tok not in allowed:
                fails += 1
                print(f"FAIL  {path}: {tok!r} has no fact in docs/facts.json: {line}")
    for path in ("README.md", "docs/submission/SUBMISSION.md", "docs/submission/pitch_script.md", "web/index.html"):
        if SENTENCE not in html.unescape(read(path)):
            fails += 1
            print(f"FAIL  {path}: the one sentence is missing")
    for f in facts:
        if not (set(f["say"]) & used) and "live" not in f:
            fails += 1
            print(f"FAIL  docs/facts.json: nothing says {f['say']} any more ({f['what']}): remove the fact")
            continue
        try:
            ok, detail = check(f, offline)
        except Exception as why:  # noqa: BLE001 - a checker that cannot run is a failure, not a pass
            ok, detail = False, f"{type(why).__name__}: {why}"
        fails += ok is False
        print(f"{'SKIP' if ok is None else 'PASS' if ok else 'FAIL'}  {', '.join(f['say']) or f['what'][:40]}: {detail}")
    print(f"{len(facts)} facts, {len(used)} numbers in the pitch-facing text, {fails} failures" + (" (offline)" if offline else ""))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
