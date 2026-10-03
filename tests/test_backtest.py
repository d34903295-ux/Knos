"""The backtest (scripts/backtest.py): of merged agent pull requests that said tests pass, how many had a failed check
at the head commit. Offline: the committed docs/backtest.json is what the script computes from the sample in the
repository; made-up pull requests cover what that sample cannot (times, the index, reading merges from GitHub)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import agent_pr_ci  # noqa: E402
import agent_pr_index  # noqa: E402
import backtest  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def pr(n, agent="copilot", cls="passed", checks=(), merged=False, state="closed", created="2026-09-01T00:00:00Z",
       ended=None, repo="acme/r"):
    return {"agent": agent, "repo": repo, "number": n, "sha": "a" * 40, "class": cls, "failed_checks": list(checks),
            "phrase": "tests pass", "merged": merged, "pr_state": state, "created_at": created,
            **({"merged_at" if merged else "closed_at": ended} if ended else {})}


def test_the_committed_backtest_is_what_the_script_computes_from_the_sample_in_the_repository():
    sample = json.loads((ROOT / "docs" / "agent_pr_ci.json").read_text(encoding="utf-8"))
    out = json.loads((ROOT / "docs" / "backtest.json").read_text(encoding="utf-8"))
    assert out["sample"] == json.loads(json.dumps(backtest.from_sample(sample)))
    s, merged = out["sample"], out["sample"]["merged"]["overall"]
    # the numbers, pinned: 349 claimed, 303 with finished CI, 241 of those merged, 30 of the merged with a failed check
    assert (s["claimed_prs"], s["with_finished_ci"], s["prs"], merged["prs"]) == (349, 303, 303, 241)
    assert merged["any_check_failed"] == {"prs": 30, "share": 0.1245, "ci95": agent_pr_index.wilson(30, 241)} and \
        merged["any_check_failed"]["ci95"] == [0.0886, 0.1721]
    assert merged["test_or_build_check_failed"]["prs"] == 16 and merged["cancelled_no_failure"]["prs"] == 6
    by_agent = {a: (v["prs"], v["any_check_failed"]["prs"]) for a, v in s["merged"]["agents"].items()}
    assert by_agent == {"copilot": (44, 15), "devin": (43, 5), "claude-bot": (85, 5), "claude-code": (22, 2), "codex": (47, 3)}
    assert sum(n for n, _ in by_agent.values()) == 241 and sum(k for _, k in by_agent.values()) == 30
    assert s["fate"]["any_check_failed"] == {"prs": 55, "merged": 30, "closed_unmerged": 13, "open": 12, "merged_share": 0.5455,
                                             "merged_ci95": agent_pr_index.wilson(30, 55)}
    assert s["fate"]["no_check_failed"]["prs"] == 248 and s["fate"]["no_check_failed"]["merged"] == 211
    # what this data cannot show is said, not guessed
    assert s["time_open"] is None and any(line.startswith("How long pull requests stayed open") for line in out["cannot_show"])
    # the index part is about the published index the docs quote, and says how little of it has a known merge state
    ix = out["index"]
    bench = json.loads((ROOT / "docs" / "bench.json").read_text(encoding="utf-8"))["market"]["index"]
    assert ix["root"] == bench["root"] and ix["listed_prs"] == bench["n_prs"] == 2431
    assert ix["merge_state_known"] == ix["prs"] == 72 and ix["merged"]["overall"]["prs"] == 50
    assert any("known for 72 of its 2,431" in line for line in out["cannot_show"])


def test_merged_pull_requests_with_a_failed_check_are_counted_by_agent_with_their_interval():
    rows = [pr(1, cls="failed", checks=["pytest"], merged=True), pr(2, cls="failed", checks=["Vercel"], merged=True),
            pr(3, merged=True), pr(4, merged=True), pr(5, cls="other", merged=True),
            pr(6, cls="failed", checks=["build"], merged=False, state="open"), pr(7, cls="failed", checks=["lint"]),
            pr(8, agent="codex", cls="failed", checks=["unit"], merged=True), pr(9, agent="codex", merged=True),
            pr(10, cls="pending", merged=True), pr(11, cls="no-ci", merged=True),            # CI not finished: not counted
            {**pr(12, cls="failed", merged=True), "merged": None, "pr_state": None}]          # merge state unknown
    got = backtest.backtest(rows)
    assert got["prs"] == 9
    all_ = got["merged"]["overall"]
    assert all_["prs"] == 7 and all_["any_check_failed"] == {"prs": 3, "share": round(3 / 7, 4), "ci95": agent_pr_index.wilson(3, 7)}
    assert all_["test_or_build_check_failed"]["prs"] == 2 and all_["cancelled_no_failure"]["prs"] == 1
    assert got["merged"]["agents"]["copilot"]["prs"] == 5 and got["merged"]["agents"]["copilot"]["any_check_failed"]["prs"] == 2
    assert got["merged"]["agents"]["codex"]["any_check_failed"] == {"prs": 1, "share": 0.5, "ci95": agent_pr_index.wilson(1, 2)}
    assert got["merged"]["agents"]["devin"]["prs"] == 0 and got["merged"]["agents"]["devin"]["any_check_failed"]["share"] is None
    assert got["fate"]["any_check_failed"] == {"prs": 5, "merged": 3, "closed_unmerged": 1, "open": 1, "merged_share": 0.6,
                                               "merged_ci95": agent_pr_index.wilson(3, 5)}
    assert got["fate"]["no_check_failed"]["merged"] == 4 and got["time_open"] is None      # no row says when it closed


def test_time_open_is_compared_when_the_data_has_the_times_and_those_still_open_are_not_timed():
    day = lambda n: f"2026-09-{n:02d}T00:00:00Z"   # noqa: E731
    rows = [pr(1, cls="failed", checks=["test"], merged=True, ended=day(11)),                 # 10 days
            pr(2, cls="failed", checks=["test"], ended=day(5)),                               # 4 days, closed unmerged
            pr(3, cls="failed", checks=["test"], state="open"),
            pr(4, merged=True, ended=day(2)), pr(5, merged=True, ended=day(2)), pr(6, merged=True, ended=day(4))]
    got = backtest.backtest(rows)["time_open"]
    assert got["any_check_failed"] == {"prs": 3, "closed": 2, "still_open": 1, "median_hours": 168.0, "quartile_hours": [132.0, 204.0]}
    assert got["no_check_failed"]["closed"] == 3 and got["no_check_failed"]["median_hours"] == 24.0
    assert got["no_check_failed"]["still_open"] == 0
    out = {"sample": {"time_open": got}, "index": None}
    assert not any("How long" in line for line in backtest.cannot_show(out))
    assert any("was not given one" in line for line in backtest.cannot_show(out))


def test_merge_state_is_read_from_github_and_a_stopped_run_says_what_is_left(monkeypatch):
    monkeypatch.setattr(agent_pr_ci, "ARGS", None)                    # fetch sets the time budget there: put it back after
    index = agent_pr_index.build([pr(n, cls="passed") for n in range(1, 7)], "2026-10-02", ("a", "b"))
    asked = []

    def get(path, max_age=None):
        asked.append((path, max_age))
        n = int(path.rsplit("/", 1)[1])
        if n == 4:
            return {"ok": False, "error": "gh: Not Found (HTTP 404)"}
        if n == 5:
            return {"ok": False, "error": "gh: Bad Gateway (HTTP 502)", "transient": True}
        if n == 6:
            raise agent_pr_ci.OutOfTime()
        return {"ok": True, "json": {"state": "open" if n == 3 else "closed", "created_at": "2026-09-01T00:00:00Z",
                                     "merged_at": "2026-09-02T00:00:00Z" if n == 1 else None,
                                     "closed_at": None if n == 3 else "2026-09-02T00:00:00Z"}}
    pulls, left = backtest.fetch(index, 100, get)
    assert left == 2 and set(pulls) == {"acme/r#1", "acme/r#2", "acme/r#3", "acme/r#4"}
    assert pulls["acme/r#1"]["merged"] is True and pulls["acme/r#2"]["merged"] is False and "error" in pulls["acme/r#4"]
    assert ("repos/acme/r/pulls/3", 86400) in asked and ("repos/acme/r/pulls/1", 86400) not in asked   # open: asked again daily
    got = backtest.from_index(index, pulls)
    assert got["listed_prs"] == 6 and got["merge_state_known"] == 3 and got["merge_state_from"] == "GitHub (backtest.py fetch)"
    assert got["merged"]["overall"]["prs"] == 1 and got["time_open"]["no_check_failed"]["closed"] == 2
    assert got["fate"]["no_check_failed"] == {"prs": 3, "merged": 1, "closed_unmerged": 1, "open": 1, "merged_share": 0.3333,
                                              "merged_ci95": agent_pr_index.wilson(1, 3)}


def test_the_command_writes_the_file_and_the_index_is_used_only_if_its_root_matches(tmp_path):
    import pytest
    rows = [{**pr(1, cls="failed", checks=["test"], merged=True), "author": "bot"}, {**pr(2, merged=True), "author": "acme"}]
    sample = tmp_path / "sample.json"
    sample.write_text(json.dumps({"generated_utc": "2026-10-01T14:42:51+00:00", "prs": rows}), encoding="utf-8")
    index = agent_pr_index.build(rows, "2026-10-02", ("2026-06-04", "2026-10-01"))
    (tmp_path / "index.json").write_text(json.dumps(index), encoding="utf-8")
    out = tmp_path / "backtest.json"
    assert backtest.main(["--sample", str(sample), "--index", str(tmp_path / "index.json"), "--out", str(out)]) == 0
    got = json.loads(out.read_text(encoding="utf-8"))
    assert got["sample"]["merged"]["overall"]["any_check_failed"]["prs"] == 1
    assert got["sample"]["without_author_owned_repos"]["merged"]["overall"]["prs"] == 1        # acme's own pull request is out
    assert got["index"]["merge_state_known"] == 2 and got["index"]["root"] == index["root"]
    assert set(got["definitions"]) >= {"prs", "merged", "any_check_failed", "test_or_build_check_failed", "fate", "time_open"}
    index["prs"][0]["class"] = "passed"                                                       # an edited list is not counted
    (tmp_path / "index.json").write_text(json.dumps(index), encoding="utf-8")
    with pytest.raises(SystemExit):
        backtest.main(["--sample", str(sample), "--index", str(tmp_path / "index.json"), "--out", str(out)])
