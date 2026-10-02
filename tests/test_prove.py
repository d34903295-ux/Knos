"""prove.yml keeps untrusted pull request code and the OIDC token apart, the judge refuses what it should, and
a token the chain would refuse before it costs a transaction, and `knos proof run` is the check job's verdict."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from knos.cli import main

ROOT = Path(__file__).resolve().parents[1]


def run(capsys, *args: str) -> tuple[int, str]:
    rc = main(list(args))
    got = capsys.readouterr()
    return rc, got.out + got.err


def _yaml(path: Path) -> dict:
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    doc["on"] = doc.pop(True, doc.get("on"))   # YAML 1.1 reads the key `on` as True
    return doc


# ---- prove.yml ---------------------------------------------------------------------------------------------------

def test_prove_yml_is_a_reusable_workflow_taking_the_issue_and_no_secret():
    doc = _yaml(ROOT / ".github" / "workflows" / "prove.yml")
    call = doc["on"]["workflow_call"]
    assert set(doc["on"]) == {"workflow_call"}
    assert call["inputs"]["issue"]["required"] is True and "job" not in call["inputs"]
    assert "secrets" not in call                                   # permissionless: anyone relays the token
    assert doc["permissions"] == {}


def test_a_merge_pays_only_when_the_check_holds_at_the_merged_head_and_runs_no_pr_code():
    merged = _yaml(ROOT / ".github" / "workflows" / "prove.yml")["jobs"]["merged"]
    assert merged["if"] == "github.event.action == 'closed' && github.event.pull_request.merged == true"
    assert merged["permissions"] == {"contents": "read", "checks": "read", "issues": "read", "id-token": "write"}
    co = [s for s in merged["steps"] if str(s.get("uses", "")).startswith("actions/checkout")]
    assert len(co) == 1 and co[0]["with"]["ref"] == "${{ github.event.pull_request.base.sha }}"   # the base, never the PR
    assert co[0]["with"]["persist-credentials"] is False
    run = "\n".join(str(s.get("run", "")) for s in merged["steps"])
    # no pull request file is ever checked out or executed: only its diff and its description are read
    assert "archive" not in run and "tar -x" not in run and "knos proof judge" not in run and "pytest" not in run
    gate, payee, mint = run.index("knos proof gate"), run.index("knos proof payee"), run.index("ACTIONS_ID_TOKEN_REQUEST_URL")
    assert gate < payee < mint and "set -euo pipefail" in run          # a refusal stops before any token exists
    assert '--event "$GITHUB_EVENT_PATH"' in run and '--head "$HEAD"' in run
    assert 'aud="knos:pay:$REPO_ID:$ISSUE:$author:$HEAD:' in run and run.count(":0\"") == 1     # mode 0, zero checks
    assert "pull_request.body" not in run and "github.event" not in run                          # event data via env only
    names = [s["with"]["name"] for s in merged["steps"] if str(s.get("uses", "")).startswith("actions/upload-artifact")]
    assert names == ["knos-evidence", "knos-proof"]                    # the reasons of a refusal reach the comment


def test_check_runs_on_every_bounty_pr_with_no_token_it_could_misuse():
    check = _yaml(ROOT / ".github" / "workflows" / "prove.yml")["jobs"]["check"]
    assert check["if"] == "github.event.action != 'closed'" and "needs" not in check       # both modes: the status to require
    assert check["permissions"] == {"contents": "read", "checks": "read", "issues": "read"}   # read-only; no id-token
    text = json.dumps(check)
    assert "secrets." not in text and "id-token" not in text
    co = next(s for s in check["steps"] if str(s.get("uses", "")).startswith("actions/checkout"))
    assert co["with"]["ref"] == "${{ github.event.pull_request.base.sha }}"      # the base, not the PR
    assert co["with"]["persist-credentials"] is False
    runs = "\n".join(str(s.get("run", "")) for s in check["steps"])
    assert 'archive "$HEAD" | tar -x -C pr' in runs                               # PR source in its own dir
    assert "knos proof gate" in runs and "knos proof judge" in runs
    assert "--sandbox require" in runs                                            # PR code never runs unboxed
    assert "pull_request.body" not in runs and "github.event" not in runs         # event data via env only
    assert set(check["outputs"]) == {"passed", "mode", "head_sha", "checks_hash"}
    # the pull request's own dependency install is the judge's to run, in the sandbox: no step runs it as the runner
    assert not any("inputs.setup" in str(s.get("run", "")) or s.get("working-directory") == "pr" for s in check["steps"])
    judge = next(s for s in check["steps"] if s.get("id") == "judge")
    assert judge["env"]["SETUP"] == "${{ inputs.setup }}" and set(judge["env"]) == {"ISSUE", "HEAD", "AUTHOR", "SETUP", "GH_TOKEN"}


def test_memory_is_saved_only_after_a_refusal_that_ran_no_pr_code():
    check = _yaml(ROOT / ".github" / "workflows" / "prove.yml")["jobs"]["check"]
    save = next(s for s in check["steps"] if str(s.get("uses", "")).startswith("actions/cache/save"))
    assert save["if"] == "always() && steps.judge.outputs.learn == 'true'"
    run = next(s for s in check["steps"] if s.get("id") == "judge")["run"]
    assert 'startswith("repo rule:") or startswith("touches protected path") or startswith("claim:")' in run
    restore = next(s for s in check["steps"] if str(s.get("uses", "")).startswith("actions/cache/restore"))
    assert restore["with"]["key"] != save["with"]["key"] and save["with"]["key"].startswith(restore["with"]["key"])


def test_attest_runs_no_pr_code_and_mints_the_pay_audience():
    doc = _yaml(ROOT / ".github" / "workflows" / "prove.yml")
    jobs = doc["jobs"]
    attest = jobs["attest"]
    assert attest["needs"] == "check"
    assert attest["if"] == "needs.check.outputs.passed == 'true' && needs.check.outputs.mode == 'tests'"
    assert attest["permissions"] == {"id-token": "write"}
    co = [s for s in attest["steps"] if str(s.get("uses", "")).startswith("actions/checkout")]
    assert len(co) == 1 and co[0]["with"]["ref"] == "${{ github.event.pull_request.base.sha }}"
    assert co[0]["with"]["sparse-checkout"] == ".knos/acceptance/${{ inputs.issue }}"
    runs = "\n".join(str(s.get("run", "")) for s in attest["steps"])
    assert 'test "$got" = "$CHECKS"' in runs                                     # recomputed from the base
    assert 'aud="knos:pay:$REPO_ID:$ISSUE:$author:$HEAD:$CHECKS:1"' in runs      # the author's GitHub id, no address
    assert "knos-payout" not in runs and "KNOS_PROVER_KEY" not in json.dumps(attest)
    assert "audience=$aud" in runs and "ACTIONS_ID_TOKEN_REQUEST_URL" in runs
    up = next(s for s in attest["steps"] if str(s.get("uses", "")).startswith("actions/upload-artifact"))
    assert up["with"]["name"] == "knos-proof"
    envs = {k: v for s in attest["steps"] for k, v in (s.get("env") or {}).items()}
    assert envs["CHECKS"] == "${{ needs.check.outputs.checks_hash }}"
    assert 'author=$(knos proof payee --event "$GITHUB_EVENT_PATH"' in runs      # worked out again here, from GitHub's event
    assert "needs.check.outputs.payee" not in json.dumps(attest)                # never taken from the job that ran the code
    assert "pip install --system \"knos==" in runs                  # knos from PyPI, never from the PR
    # the judge is the code at this workflow file's own commit (what the bounty pinned); no caller input can swap it
    assert set(doc["on"]["workflow_call"]["inputs"]) == {"issue", "setup"}
    for job in ("check", "attest", "merged"):
        install = next(s for s in jobs[job]["steps"] if "install knos" in str(s.get("name", "")))
        # knos at the workflow's own commit, and its dependencies as they were published when this release was made
        assert install["env"] == {"REF": "${{ job.workflow_sha }}", "REPO": "${{ job.workflow_repository }}",
                                  "UV_EXCLUDE_NEWER": "2026-10-05T00:00:00Z"}
        assert "inputs." not in install["run"]
    try:
        import tomllib
    except ModuleNotFoundError:   # Python 3.10
        import tomli as tomllib  # type: ignore[no-redef]
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    assert f'"knos=={version}"' in runs                              # the fallback is this very release
    # a token exists only after tests mode passed or a maintainer merged: a merge-mode check that passes mints nothing
    assert doc["on"]["workflow_call"]["outputs"]["passed"]["value"] == \
        "${{ jobs.attest.result == 'success' || jobs.merged.result == 'success' }}"


def test_the_caller_template_uses_pull_request_target_and_a_pinned_commit():
    doc = _yaml(ROOT / "examples" / "knos-workflow.yml")
    assert set(doc["on"]) == {"pull_request_target", "issue_comment", "issues"}
    prove = doc["jobs"]["prove"]
    pin = prove["uses"].split("@")
    # a commit, never a tag: the placeholder becomes the site's own commit sha when Pages is built (network.yml)
    assert pin[0] == "drexthealpha/Knos/.github/workflows/prove.yml" and pin[1] == "KNOS_COMMIT_SHA"
    assert "closed" in doc["on"]["pull_request_target"]["types"]                 # a merge is what pays in merge mode
    assert prove["with"] == {"issue": "${{ needs.job.outputs.issue }}"}        # the caller chooses nothing else
    assert prove["permissions"] == {"contents": "read", "checks": "read", "issues": "read", "id-token": "write"}
    finder = doc["jobs"]["job"]
    assert "pull_request.body" not in json.dumps(finder["steps"][0]["run"])   # via env, never inlined into a script
    assert not any(str(s.get("uses", "")).startswith("actions/checkout") for s in finder["steps"])


def test_the_free_check_alone_needs_no_token_and_no_money():
    doc = _yaml(ROOT / ".github" / "workflows" / "check.yml")
    assert set(doc["on"]) == {"workflow_call"} and doc["permissions"] == {} and "secrets" not in doc["on"]["workflow_call"]
    job = doc["jobs"]["claims"]
    assert job["permissions"] == {"contents": "read", "checks": "read"}
    text = json.dumps(job)
    assert "id-token" not in text and "secrets." not in text
    runs = "\n".join(str(s.get("run", "")) for s in job["steps"])
    assert "knos proof gate" in runs and "knos proof judge" not in runs and "tar -x" not in runs   # no PR code runs
    caller = _yaml(ROOT / "examples" / "knos-check.yml")
    assert set(caller["on"]) == {"pull_request_target"} and caller["permissions"] == {}
    assert caller["jobs"]["check"]["uses"] == "drexthealpha/Knos/.github/workflows/check.yml@KNOS_COMMIT_SHA"
    assert caller["jobs"]["check"]["permissions"] == {"contents": "read", "checks": "read"}


def test_every_action_is_pinned_to_a_commit():
    """A tag can be moved to other code (the tj-actions compromise of 2025 did exactly that); a commit cannot."""
    import re
    pins = json.loads((ROOT / "scripts" / "action_pins.json").read_text(encoding="utf-8"))["pins"]
    seen = set()
    for f in sorted((ROOT / ".github" / "workflows").glob("*.yml")) + sorted((ROOT / "examples").glob("*.yml")):
        for name, ref in re.findall(r"uses:\s*([\w./-]+)@(\S+)", f.read_text(encoding="utf-8")):
            if name.startswith("./"):
                continue
            if name.startswith("drexthealpha/Knos/"):
                assert ref == "KNOS_COMMIT_SHA" or re.fullmatch(r"[0-9a-f]{40}", ref), (f.name, name, ref)
                continue
            assert re.fullmatch(r"[0-9a-f]{40}", ref), f"{f.name}: {name}@{ref} is not pinned to a commit"
            seen.add(ref)
    assert seen - {"6ddf031b64febecfcd510763ecee37637a8047fa"} <= set(pins.values())     # the rotate pin is in pins.rs


# ---- who is paid --------------------------------------------------------------------------------------------------

def _pull(login="mona", uid=4242, kind="User", body="Fixes #7", assignees=()):
    return {"number": 12, "body": body, "user": {"login": login, "id": uid, "type": kind},
            "assignees": [{"login": a, "id": i, "type": "User"} for a, i in assignees], "head": {"sha": "a" * 40}}


def test_a_persons_pull_request_pays_that_person_and_an_agents_pays_whoever_ran_it():
    from knos import judge
    users = {"users/mona": {"login": "mona", "id": 4242, "type": "User"}, "user/4242": {"login": "mona", "id": 4242, "type": "User"},
             "users/some-bot": {"login": "some-bot", "id": 9, "type": "Bot"}}

    def get(path):
        if path not in users:
            raise OSError("404")
        return users[path]
    assert judge.payee(_pull(), get=get)["id"] == 4242
    bot = dict(login="devin-ai-integration[bot]", uid=158243242, kind="Bot")
    assert judge.payee(_pull(**bot, assignees=[("mona", 4242)]), get=get) == \
        {"id": 4242, "login": "mona", "why": "assignee of devin-ai-integration[bot]'s pull request"}
    for line in ("Link to Devin session: https://x\nRequested by: @mona", "PR created automatically by Jules for task 7 started by @mona",
                 "Knos-Pay-To: @mona"):
        got = judge.payee(_pull(**bot, body="Fixes #7\n\n" + line), get=get)
        assert got["id"] == 4242 and "named in the description" in got["why"], line
    # Copilot and Cursor credit the person in the commit, with GitHub's no-reply address (it carries the user id)
    msg = "Fix the slug\n\nCo-authored-by: mona <4242+mona@users.noreply.github.com>"
    assert judge.payee(_pull(**bot), msg, get)["why"] == "co-author of devin-ai-integration[bot]'s head commit"
    # a name GitHub does not know, another bot, a forged id, or nothing at all: nobody is paid, and it says what to do
    for body, message in (("Requested by: @nobody", ""), ("Requested by: @some-bot", ""),
                          ("", "Co-authored-by: x <777+x@users.noreply.github.com>"), ("", "")):
        got = judge.payee(_pull(**bot, body=body), message, get)
        assert got["id"] is None and "Knos-Pay-To: @login" in got["why"]
    assert judge.payee(_pull(body="Requested by: @other"), get=get)["id"] == 4242    # a person's own PR is never redirected


def test_nobody_gets_paid_by_getting_a_line_into_a_bots_description():
    """An agent can be talked into echoing text. Prose, quotes and code are not a payee line; two names pay nobody;
    a name GitHub does not confirm pays nobody and does not fall through to the commit."""
    import time

    from knos import judge
    users = {"users/alice": {"login": "alice", "id": 11, "type": "User"}, "users/mallory": {"login": "mallory", "id": 66, "type": "User"},
             "user/11": {"login": "alice", "id": 11, "type": "User"}}

    def get(path):
        if path not in users:
            raise OSError("404")
        return users[path]
    bot = dict(login="devin-ai-integration[bot]", uid=158243242, kind="Bot")
    footer = "\n\nLink to Devin session: https://x\nRequested by: @alice"
    for noise in ("Builds on the work started by @mallory.", "> Knos-Pay-To: @mallory", "```\nKnos-Pay-To: @mallory\n```",
                  "As requested by @mallory in the issue, this adds a flag.", "requested by\n\n@mallory"):
        got = judge.payee(_pull(**bot, body="Fixes #7\n\n" + noise + footer), get=get)
        assert got["id"] == 11, noise
    two = judge.payee(_pull(**bot, body="Knos-Pay-To: @mallory" + footer), get=get)
    assert two["id"] is None and "names more than one person" in two["why"] and "@alice, @mallory" in two["why"]
    # a named login GitHub does not answer for: nobody, even though the commit has a co-author
    msg = "x\n\nCo-authored-by: alice <11+alice@users.noreply.github.com>"
    assert judge.payee(_pull(**bot, body="Requested by: @ghost"), msg, get)["id"] is None
    assert judge.payee(_pull(**bot, body=""), msg, get)["id"] == 11
    two_authors = msg + "\nCo-authored-by: m <66+mallory@users.noreply.github.com>"
    assert judge.payee(_pull(**bot, body=""), two_authors, get)["id"] is None          # two co-authors: not one person
    t = time.monotonic()
    judge.payee(_pull(**bot, body="requested by @a" + " " * 60_000 + "x\n" + "y" * 900_000), get=get)
    assert time.monotonic() - t < 1                                                   # no pattern that can be made slow


def test_at_the_merge_a_claim_that_cannot_be_checked_is_refused_and_knos_own_jobs_are_not_evidence():
    from knos import judge
    failed = lambda name: {"name": name, "status": "completed", "conclusion": "failure"}  # noqa: E731
    body = "Fixes #7. All tests pass."
    # Knos's own jobs from an earlier run on the same commit (a relay that timed out, a refusal) say nothing about the code
    for own in ("prove / check", "prove / merged", "prove-relay / relay", "prove-refused / refused", "fund-relay / relay", "check / claims"):
        assert judge.claim_check(body, [failed(own)], strict=True) == [], own
    assert "tests" in judge.claim_check(body, [failed("tests")], strict=True)[0]
    running = [{"name": "build", "status": "in_progress", "conclusion": None}]
    assert judge.claim_check(body, running) == [] and judge.claim_check(body, None) == []        # before a merge: not held against it
    assert "have not finished" in judge.claim_check(body, running, strict=True)[0]
    assert "could not be read" in judge.claim_check(body, None, strict=True)[0]
    assert judge.claim_check("Fixes #7.", None, strict=True) == []                               # no claim, nothing to check
    # more than 100 check runs on one commit are all read
    pages = {1: [{"name": f"job{i}", "status": "completed", "conclusion": "success"} for i in range(100)], 2: [failed("late")]}
    got = judge.check_runs("o/r", "a" * 40, get=lambda url: {"check_runs": pages.get(int(url.rsplit("page=", 1)[1]), [])})
    assert len(got) == 101 and "late" in judge.claim_check(body, got)[0]
    merged = _yaml(ROOT / ".github" / "workflows" / "prove.yml")["jobs"]["merged"]
    assert "--strict" in "\n".join(str(s.get("run", "")) for s in merged["steps"])


def test_an_assigned_issue_pays_only_its_assignee(tmp_path, capsys):
    from knos import judge
    issue = {"number": 7, "assignees": [{"login": "hubot", "id": 1}, {"login": "mona", "id": 4242}]}
    mine, theirs = _pull(), _pull(login="eve", uid=666)
    assert judge.assignment_check(issue, mine, judge.payee(mine)) == []
    assert judge.assignment_check({"number": 7, "assignees": []}, theirs, judge.payee(theirs)) == []      # open to anyone
    assert judge.assignment_check(None, theirs, judge.payee(theirs)) == []                                # GitHub did not answer
    said = judge.assignment_check(issue, theirs, judge.payee(theirs))
    assert said == ["assigned: issue #7 is assigned to @hubot, @mona; only an assignee's pull request is paid for it"]
    # an agent an assignee ran: the bot wrote the pull request, the assignee is paid
    bot = _pull(login="copilot-swe-agent[bot]", uid=198982749, kind="Bot", assignees=[("mona", 4242)])
    assert judge.assignment_check(issue, bot, judge.payee(bot)) == []
    # through the command the workflow runs: the event file in, the refusal out, the payee in the evidence
    base = tmp_path / "base"
    base.mkdir()
    event, issue_file, ev = tmp_path / "event.json", tmp_path / "issue.json", tmp_path / "evidence.json"
    issue_file.write_text(json.dumps(issue), encoding="utf-8")
    event.write_text(json.dumps({"pull_request": theirs}), encoding="utf-8")
    rc, out = run(capsys, "proof", "gate", "--base", str(base), "--issue", "7", "--event", str(event),
                  "--issue-file", str(issue_file), "--evidence", str(ev))
    assert rc == 1 and "NO  assigned: issue #7 is assigned to @hubot, @mona" in out
    assert json.loads(ev.read_text(encoding="utf-8"))["evidence"]["payee"]["id"] == 666
    event.write_text(json.dumps({"pull_request": mine}), encoding="utf-8")
    rc, out = run(capsys, "proof", "gate", "--base", str(base), "--issue", "7", "--event", str(event),
                  "--issue-file", str(issue_file), "--evidence", str(ev))
    assert rc == 0 and "paid to @mona (GitHub user id 4242): the pull request's author" in out
    rc, out = run(capsys, "proof", "payee", "--event", str(event))
    assert rc == 0 and out.strip() == "4242"
    event.write_text(json.dumps({"pull_request": _pull(login="x[bot]", uid=5, kind="Bot")}), encoding="utf-8")
    rc, out = run(capsys, "proof", "payee", "--event", str(event))
    assert rc == 1 and "Knos-Pay-To: @login" in out


# ---- knos proof run ----------------------------------------------------------------------------------------------

def _repo(tmp_path: Path, toml: str) -> Path:
    repo = tmp_path / "repo"
    (repo / ".knos").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / ".knos" / "proof.toml").write_text(toml, encoding="utf-8")
    return repo


def test_proof_run_passes_when_every_check_passes(knos_home, tmp_path, capsys):
    repo = _repo(tmp_path, '[[check]]\nname = "a"\nrun = "exit 0"\n[[check]]\nname = "b"\nrun = "exit 0"\n')
    rc, said = run(capsys, "proof", "run", "--in", str(repo))
    assert rc == 0, said
    assert "custom:a" in said and "custom:b" in said and "proven" in said


def test_proof_run_fails_when_one_check_fails(knos_home, tmp_path, capsys):
    repo = _repo(tmp_path, '[[check]]\nname = "a"\nrun = "exit 0"\n[[check]]\nname = "b"\nrun = "exit 3"\n')
    rc, said = run(capsys, "proof", "run", "--in", str(repo))
    assert rc == 1
    assert "NO  custom:b" in said and "not proven" in said


def test_proof_run_reads_the_toml_it_is_given(knos_home, tmp_path, capsys):
    repo = _repo(tmp_path, '[[check]]\nname = "pr"\nrun = "exit 0"\n')   # what the pull request says
    base = tmp_path / "base.toml"
    base.write_text('[[check]]\nname = "base"\nrun = "exit 1"\n', encoding="utf-8")   # what the base branch says
    rc, said = run(capsys, "proof", "run", "--in", str(repo), "--toml", str(base))
    assert rc == 1 and "custom:base" in said and "custom:pr" not in said


def test_proof_run_with_no_checks_proves_nothing(knos_home, tmp_path, capsys):
    repo = _repo(tmp_path, 'tests = "pytest"\n')
    rc, said = run(capsys, "proof", "run", "--in", str(repo))
    assert rc == 1 and "nothing to prove" in said
