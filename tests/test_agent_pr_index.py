"""The Agent PR Index, offline: fixture PRs through agent_pr_ci's classification, then a deterministic Merkle root."""

from __future__ import annotations

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
    if "/pulls/" in path:
        sha = {"1": "aaa", "2": "bbb", "3": "ccc"}[parts[-1]]
        return {"ok": True, "json": {"head": {"sha": sha}, "merged_at": None, "state": "open"}}
    sha = parts[parts.index("commits") + 1]
    if path.endswith("check-runs"):
        return {"ok": True, "json": {"check_runs": RUNS[sha]}}
    if path.endswith("status"):
        return {"ok": True, "json": {"statuses": []}}
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
    assert a["agents"]["copilot"] == {"claimed_green": 1, "actually_failed": 1, "share": 1.0}
    assert a["agents"]["codex"] == {"claimed_green": 1, "actually_failed": 0, "share": 0.0}
    assert a["agents"]["devin"] == {"claimed_green": 0, "actually_failed": 0, "share": None}  # no CI is not green
    assert set(a["prs"][0]) == set(agent_pr_index.PR_KEYS)

    claimed[1]["class"] = "failed"  # any change to a counted record changes the root
    assert agent_pr_index.build(claimed, "2026-10-01", WINDOW)["root"] != a["root"]
