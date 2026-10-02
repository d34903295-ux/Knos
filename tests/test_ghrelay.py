"""The zero-secret GitHub relay (ghrelay), the caller workflow."""

import base64
import hashlib
import json
import re
import time
from pathlib import Path

import pytest

from knos.proof import ghrelay
from knos.settle import pay

ROOT = Path(__file__).resolve().parents[1]
JOB = "ab" * 32
PAYOUT = "EwSxyJFNQkNN9qtss4Qd7DTvrNYb62vgvhdwqgfErXDz"


def jwt(aud: str, exp: float | None = None) -> str:
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
    return f"{enc({'alg': 'RS256', 'kid': 'k1'})}.{enc({'aud': aud, 'exp': exp or time.time() + 300})}.c2ln"


def test_checks_hash_matches_fund_yml(tmp_path):
    d = tmp_path / ".knos" / "acceptance" / "1"
    (d / "sub").mkdir(parents=True)
    (d / "test_accept.py").write_bytes(b"def test_x():\n    assert 1\n")
    (d / "sub" / "data.txt").write_bytes(b"x")
    want = hashlib.sha256()
    for rel, data in sorted([("sub/data.txt", b"x"), ("test_accept.py", b"def test_x():\n    assert 1\n")]):
        want.update(f"{rel}\0{hashlib.sha256(data).hexdigest()}\n".encode())
    assert ghrelay.checks_hash(d) == want.hexdigest()
    src = (ROOT / ".github" / "workflows" / "fund.yml").read_text(encoding="utf-8")
    assert 'h.update(f"{f.relative_to(root).as_posix()}\\0{hashlib.sha256(f.read_bytes()).hexdigest()}\\n".encode())' \
        in src


def test_relay_one_reports_in_words_and_checks_the_marker():
    paid = {"ok": True, "kind": "pay", "sigs": ["s1", "s2", "s3", "s4"], "issue": 7, "author_id": 4242,
            "paid": [{"job": "J1", "amount": 5_000_000, "fee": 125_000, "mint": "M", "waits": 0}]}
    r = ghrelay.relay_one(None, None, "proof", "x.y.z", submit=lambda *a: dict(paid))
    assert r["ok"] and "4.88 is now waiting under GitHub user id 4242 for issue #7" in r["note"] and "#claim" in r["note"]
    line = ghrelay.log_line("proof", "o/r", 9, "x.y.z", r)
    assert line.startswith(f"knos-relay proof o/r#9 {ghrelay.token_id('x.y.z')} ok sig=s2,s3,s4 note=")
    waits = dict(paid, paid=[dict(paid["paid"][0], waits=3600)])
    note = ghrelay.relay_one(None, None, "proof", "x.y.z", submit=lambda *a: waits)["note"]
    assert "4.88 will be released in 1 h (a maintainer's /knos veto on the issue takes it back) to GitHub user id 4242" in note
    funded = {"ok": True, "kind": "fund", "sigs": ["f"], "job": "J", "issue": 3, "amount": 5_000_000, "mode": 0, "review": 3600}
    note = ghrelay.relay_one(None, None, "fund", "t", submit=lambda *a: dict(funded))["note"]
    assert "5.00 test USDC is in escrow for issue #3, paid when a maintainer merges" in note and "released 1 h after that" in note
    # a fund token posted under a proof marker is refused; a failure is passed through and logged as fail
    assert not ghrelay.relay_one(None, None, "proof", "t", submit=lambda *a: dict(funded))["ok"]
    bad = ghrelay.relay_one(None, None, "fund", "t", submit=lambda *a: {"ok": False, "why": "token expired"})
    assert ghrelay.log_line("fund", "o/r", 1, "t", bad).endswith("fail token expired")


def test_found_and_discover():
    t = jwt("knos:fund:3:1:0:" + "0" * 64 + ":1209600:0")
    comments = [{"body": f"knos-fund: {t}\n\n<sub>x</sub>", "issue_url": "https://api.github.com/repos/o/r/issues/3",
                 "user": {"login": "github-actions[bot]"}}, {"body": "hello", "issue_url": "u/4", "user": {"login": "a"}}]
    assert ghrelay.found("o/r", "2026-10-02T00:00:00Z", getter=lambda p: comments) == \
        [("fund", 3, t, "github-actions[bot]")]

    def getter(path):
        if path.startswith("search/"):
            return {"items": [{"repository_url": "https://api.github.com/repos/a/b"}]}
        return [{"full_name": "drexthealpha/knos-e2e-1", "pushed_at": "2026-10-02T01:00:00Z"},
                {"full_name": "drexthealpha/Knos", "pushed_at": "2026-10-02T01:00:00Z"},
                {"full_name": "drexthealpha/old", "pushed_at": "2025-01-01T00:00:00Z"}]
    assert ghrelay.discover("2026-10-02T00:00:00Z", {}, getter=getter) == {"a/b", "drexthealpha/knos-e2e-1", ghrelay.ROTATE_REPO,
                                                                            ghrelay.HOME_REPO}
    for kind in ("veto", "claim", "key", "proof"):
        assert ghrelay.TOKEN.findall(f"knos-{kind}: {t}") == [(kind, t)]


def test_caller_workflow_has_no_secret_and_front_matches():
    wf = (ROOT / "examples" / "knos-workflow.yml").read_text(encoding="utf-8")
    assert "secrets." not in wf and "secrets:" not in wf
    # the workflows are pinned by one commit sha; the template carries a placeholder the Pages build replaces with the
    # commit it is built from (network.yml), so the site hands out the workflows of its own commit
    pins = set(re.findall(r"drexthealpha/Knos/\.github/workflows/(?:fund|prove|relay)\.yml@(\S+)", wf))
    assert pins == {"KNOS_COMMIT_SHA"}
    assert "kind: refused" in wf
    assert "pull_request_target" in wf and "issue_comment" in wf and "closed" in wf
    js = (ROOT / "web" / "front.js").read_text(encoding="utf-8")
    assert 'KNOS_SHA = "KNOS_COMMIT_SHA"' in js and 'KNOS_RELAY_SHA = "KNOS_COMMIT_SHA"' in js
    wf_js = re.search(r"export const WORKFLOW = `(.*?)`;\n", js, re.DOTALL).group(1)
    wf_js = wf_js.replace("${KNOS_SHA}", "KNOS_COMMIT_SHA").replace("${KNOS_RELAY_SHA}", "KNOS_COMMIT_SHA")
    assert wf_js.replace("\\${{", "${{").replace("\\\\", "\\") == wf
    import importlib.util
    spec = importlib.util.spec_from_file_location("front_workflow", ROOT / "scripts" / "front_workflow.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.main(["--check"]) == 0          # both templates the site hands out are the examples, byte for byte
    check = re.search(r"export const CHECK_WORKFLOW = `(.*?)`;\n", js, re.DOTALL).group(1)
    assert "check.yml@${KNOS_SHA}" in check and "id-token" not in check
    net = (ROOT / ".github" / "workflows" / "network.yml").read_text(encoding="utf-8")
    assert 'bash scripts/build_site.sh _site "$GITHUB_SHA"' in net
    assert 's/KNOS_COMMIT_SHA/$sha/g' in (ROOT / "scripts" / "build_site.sh").read_text(encoding="utf-8")
    assert "settings/rules/new?target=branch&enforcement=active" in js
    fund = (ROOT / ".github" / "workflows" / "fund.yml").read_text(encoding="utf-8")
    assert '"OWNER","MEMBER","COLLABORATOR"' in fund and "id-token: write" in fund and "name: knos-${{ steps.parse.outputs.kind }}" in fund


def test_judge_learns_a_contributing_violation_and_requires_it_next(tmp_path):
    from knos import judge as prove
    from knos.proof import history
    base = tmp_path / "base"
    (base / ".knos" / "acceptance" / "1").mkdir(parents=True)
    (base / ".knos" / "acceptance" / "1" / "test_accept.py").write_bytes(b"def test_a():\n    assert 1\n")
    (base / "CONTRIBUTING.md").write_bytes(b"# Rules\n\n- Do not leave print() debug statements in code.\n")
    diff = ("diff --git a/calc.py b/calc.py\n--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,3 @@\n def add(a, b):\n"
            "+    print(a, b)\n     return a + b\n")
    store = history.SibylStore.local(tmp_path / "store")
    v = prove.judge_with_rules(base, tmp_path / "pr", {"issue": "1"}, ["calc.py"], diff, store, "o/r", "bot")
    assert not v["passed"] and v["reasons"][0].startswith("repo rule: calc.py:2")
    assert v["evidence"]["required_by_history"] == [] and v["evidence"]["learned"] == ["tamper:rule:no_debug"]
    again = history.SibylStore.local(tmp_path / "store")   # a later run: the memory restored from the cache
    v2 = prove.judge_with_rules(base, tmp_path / "pr", {"issue": "1"}, ["calc.py"], diff, again, "o/r", "other")
    assert v2["evidence"]["required_by_history"] == ["tamper:rule:no_debug"]


def _fund_script() -> str:
    import yaml
    doc = yaml.safe_load((ROOT / ".github" / "workflows" / "fund.yml").read_text(encoding="utf-8"))
    run = next(s for s in doc["jobs"]["mint"]["steps"] if s.get("id") == "parse")["run"]
    return run.split("<<'PY' >> \"$GITHUB_OUTPUT\"\n", 1)[1].rsplit("\nPY", 1)[0]


def _fund(tmp_path, body: str, event: str = "issue_comment", issue: int = 7):
    import subprocess
    import sys
    import textwrap
    env = {"BODY": body, "EVENT": event, "ISSUE": str(issue), "REPO_ID": "555", "DECIMALS": "6", "WORK_DAYS": "14",
           "PATH": __import__("os").environ.get("PATH", "")}
    r = subprocess.run([sys.executable, "-c", textwrap.dedent(_fund_script())], cwd=tmp_path, env=env, capture_output=True, text=True)
    return r.returncode, dict(x.split("=", 1) for x in r.stdout.splitlines() if "=" in x)


def test_a_comment_or_a_new_issues_description_funds_a_bounty(tmp_path):
    zeros = "0" * 64
    rc, out = _fund(tmp_path, "/knos bounty 20")
    assert rc == 0 and out == {"issue": "7", "kind": "fund", "aud": f"knos:fund:7:20000000:0:{zeros}:1209600:3600"}
    assert out["aud"] == pay.fund_audience(7, 20_000_000, review_s=3600)       # exactly what knos-pay expects; held an hour
    assert _fund(tmp_path, "/knos bounty 20 review 0")[1]["aud"] == pay.fund_audience(7, 20_000_000)   # paid at once
    rc, out = _fund(tmp_path, "Slugify keeps punctuation.\n\nSteps: ...\n\n/knos bounty 12.5\n", event="issues")
    assert rc == 0 and out["aud"] == f"knos:fund:7:12500000:0:{zeros}:1209600:3600"
    rc, out = _fund(tmp_path, "/knos veto")
    assert rc == 0 and out["kind"] == "veto" and out["aud"] == "knos:veto:555:7" == pay.veto_audience(555, 7)
    assert _fund(tmp_path, "/knos bounty lots")[0] != 0 and _fund(tmp_path, "please /knos bounty 5")[0] != 0
    # tests mode: an acceptance bundle on the default branch fixes its hash in the audience, with a review window
    bundle = tmp_path / ".knos" / "acceptance" / "7"
    bundle.mkdir(parents=True)
    (bundle / "test_x.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")
    from knos import judge
    rc, out = _fund(tmp_path, "/knos bounty 5 review 3600")
    assert rc == 0 and out["aud"] == f"knos:fund:7:5000000:1:{judge.checks_hash(bundle)}:1209600:3600"
    assert out["aud"] == pay.fund_audience(7, 5_000_000, pay.TESTS, bytes.fromhex(judge.checks_hash(bundle)), 14 * 86_400, 3600)
