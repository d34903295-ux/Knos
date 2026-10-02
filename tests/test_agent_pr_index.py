"""The Agent PR Index, offline: fixture PRs through agent_pr_ci's classification, then a deterministic Merkle root;
Wilson intervals, self-repo exclusion, and a root that refuses an edited list."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import agent_pr_ci  # noqa: E402
import agent_pr_index  # noqa: E402

RUNS = {
    "aaa": [{"name": "build", "status": "completed", "conclusion": "failure"},
            {"name": "Copilot", "status": "completed", "conclusion": "success"}],
    "bbb": [{"name": "test", "status": "completed", "conclusion": "success"}],
    "ccc": [{"name": "Copilot", "status": "completed", "conclusion": "failure"}],  # agent's own run only
}
WINDOW = ("2026-09-25", "2026-10-01")


def fake_gh(path, params=None, kind="core"):
    parts = path.split("/")
    if "/refs/pull/" in path:  # the PR ref's combined status names the head SHA
        sha = {"1": "aaa", "2": "bbb", "3": "ccc"}[parts[parts.index("pull") + 1]]
        return {"ok": True, "json": {"sha": sha, "statuses": []}}
    sha = parts[parts.index("commits") + 1]
    if path.endswith("check-runs"):
        return {"ok": True, "json": {"check_runs": RUNS[sha]}}
    return {"ok": True, "json": {"check_suites": []}}


def fixtures():
    bodies = ["Fixed the parser. All tests pass.", "Done; `npm test` passes", "CI is green ✅",
              "Please make sure tests pass before merging"]
    out = []
    for i, (agent, body) in enumerate(zip(["copilot", "codex", "devin", "copilot"], bodies), 1):
        phrase, line = agent_pr_ci.find_claim(body)
        out.append({"agent": agent, "repo": f"o/r{i}", "number": i, "phrase": phrase, "claim_line": line})
    return out


def test_classification_and_deterministic_root(monkeypatch):
    monkeypatch.setattr(agent_pr_ci, "gh_get", fake_gh)
    cands = fixtures()
    assert [bool(c["phrase"]) for c in cands] == [True, True, True, False]  # "make sure ... pass" is not a claim
    claimed = [c for c in cands if c["phrase"]]
    assert agent_pr_ci.run_checks(claimed)
    assert [c["class"] for c in claimed] == ["failed", "passed", "no-ci"]
    assert claimed[0]["failed_checks"] == ["build"]  # the agent's own "Copilot" run is not project CI

    a = agent_pr_index.build(claimed, "2026-10-01", WINDOW)
    b = agent_pr_index.build(list(reversed(claimed)), "2026-10-01", WINDOW)
    assert a == b and len(a["root"]) == 64
    assert a["agents"]["copilot"] == {"claimed_green": 1, "actually_failed": 1, "share": 1.0,
                                      "ci95": agent_pr_index.wilson(1, 1),
                                      "by_repo": {"repos": 1, "failed": 1, "share": 1.0, "ci95": agent_pr_index.wilson(1, 1)}}
    assert a["agents"]["codex"]["share"] == 0.0 and a["agents"]["codex"]["ci95"][0] == 0.0
    assert a["agents"]["devin"] == {"claimed_green": 0, "actually_failed": 0, "share": None, "ci95": None,   # no CI is not green
                                    "by_repo": {"repos": 0, "failed": 0, "share": None, "ci95": None}}
    assert a["overall"]["claimed_green"] == 2 and a["overall"]["actually_failed"] == 1
    assert set(a["prs"][0]) == set(agent_pr_index.PR_KEYS)
    assert a["n_prs"] == 2  # the no-CI PR is not listed: only finished CI at the head SHA counts

    claimed[1]["class"] = "failed"  # any change to a counted record changes the root
    assert agent_pr_index.build(claimed, "2026-10-01", WINDOW)["root"] != a["root"]


def test_wilson_interval():
    lo, hi = agent_pr_index.wilson(55, 303)  # the 0.3.4 sample's published 14.2%-22.9%
    assert (round(lo, 3), round(hi, 3)) == (0.142, 0.229)
    assert agent_pr_index.wilson(0, 0) is None
    assert agent_pr_index.wilson(0, 10)[0] == 0.0 and agent_pr_index.wilson(10, 10)[1] == 1.0


def item(owner, author, assignees=()):
    return {"repository_url": f"https://api.github.com/repos/{owner}/r", "user": {"login": author},
            "assignees": [{"login": x} for x in assignees]}


def test_self_repo_exclusion():
    assert agent_pr_ci.excluded_self(item("alice", "alice"))  # Claude Code / Codex PR on the author's own repo
    assert agent_pr_ci.excluded_self(item("Alice", "Copilot", ["alice"]))  # the human who assigned Copilot owns it
    assert not agent_pr_ci.excluded_self(item("acme", "Copilot", ["alice"]))
    assert not agent_pr_ci.excluded_self(item("acme", "devin-ai-integration[bot]"))


def test_scan_windows_cover_the_range_newest_first():
    w = agent_pr_ci.scan_windows("2026-10-01", 10, 3)
    assert w[0] == ("2026-09-29", "2026-10-01") and w[-1] == ("2026-09-22", "2026-09-22") and len(w) == 4


def test_scan_excludes_self_repos_and_counts_them(monkeypatch):
    items = [dict(item("alice", "alice"), number=1, body="All tests pass.", created_at="t"),
             dict(item("acme", "alice"), number=2, body="All tests pass.", created_at="t"),
             dict(item("acme", "bob"), number=3, body="no claim here", created_at="t")]
    monkeypatch.setattr(agent_pr_ci, "gh_get", lambda *a, **k: {"ok": True, "json": {"items": items}})
    kept, n = agent_pr_ci.scan_agent("codex", "x", "2026-10-01", 2, 10)
    assert [k["number"] for k in kept] == [2] and n == {"hits": 3, "excluded": 1, "no_claim": 1}


def test_check_refuses_a_tampered_list(tmp_path):
    import json

    import pytest
    idx = agent_pr_index.build([{"agent": "codex", "repo": "o/r", "number": 1, "sha": "a", "class": "failed"}],
                               "2026-10-02", ("a", "b"), excluded=7)
    assert idx["excluded_self_repo"] == 7 and idx["n_prs"] == 1
    p = tmp_path / "index.json"
    p.write_text(json.dumps(idx), encoding="utf-8")
    assert agent_pr_index.check(str(p))["root"] == idx["root"]
    idx["prs"][0]["class"] = "passed"  # edited after the count: the root no longer matches
    p.write_text(json.dumps(idx), encoding="utf-8")
    with pytest.raises(SystemExit):
        agent_pr_index.check(str(p))


def test_one_pull_request_per_repository_is_counted_beside_every_pull_request():
    """A repository whose agent opened many failing pull requests counts once: the figure the docs quote first."""
    rows = [{"agent": "devin", "repo": "busy/Repo", "number": n, "sha": "a" * 40, "class": "failed", "failed_checks": ["ci"], "phrase": "tests pass"}
            for n in range(2, 12)]
    rows.append({"agent": "devin", "repo": "busy/repo", "number": 1, "sha": "b" * 40, "class": "passed", "failed_checks": [], "phrase": "tests pass"})
    rows += [{"agent": "devin", "repo": f"quiet/r{i}", "number": 5, "sha": "c" * 40, "class": "passed" if i else "failed",
              "failed_checks": [], "phrase": "tests pass"} for i in range(4)]
    ix = agent_pr_index.build(rows, "2026-10-02", ["2026-06-04", "2026-10-01"])
    assert ix["overall"]["actually_failed"] == 11 and ix["overall"]["claimed_green"] == 15           # 73% of pull requests
    # five repositories; busy/repo's first pull request (#1) passed, so only quiet/r0 counts as failed
    assert ix["overall"]["by_repo"] == {"repos": 5, "failed": 1, "share": 0.2, "ci95": agent_pr_index.wilson(1, 5)}
    assert ix["agents"]["devin"]["by_repo"]["repos"] == 5 and ix["agents"]["codex"]["by_repo"]["repos"] == 0
    bench = json.loads((Path(__file__).resolve().parents[1] / "docs" / "bench.json").read_text(encoding="utf-8"))["market"]["index"]
    assert bench["overall"]["by_repo"]["failed"] <= bench["overall"]["actually_failed"]
    assert bench["overall"]["by_repo"]["repos"] <= bench["n_prs"]
