"""Nothing is published unless the whole test workflow passed on that exact commit: release.yml runs tests.yml on the
tagged commit and every publishing job needs that job. One mechanism, and no other workflow publishes a release."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"

# Every way a workflow can publish something outside the repository, and the one place each is allowed.
PUBLISHES = {
    r"\buv publish\b|\btwine upload\b|pypa/gh-action-pypi-publish": {"release.yml"},
    r"\bnpm publish\b|\bcargo publish\b": {"release.yml"},
    r"\bmcp-publisher publish\b": {"release.yml"},
    r"\bgh release (create|upload)\b": {"release.yml", "index.yml"},     # index.yml: the index-<date> data release
    r"actions/deploy-pages@": {"network.yml"},                           # the site, not a release
}


def _yaml(name: str) -> dict:
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    doc["on"] = doc.pop(True, doc.get("on"))   # YAML 1.1 reads the key `on` as True
    return doc


def _needs(job: dict) -> list[str]:
    needs = job.get("needs", [])
    return [needs] if isinstance(needs, str) else list(needs)


def test_a_release_runs_the_whole_test_workflow_on_the_tagged_commit_before_anything_is_published():
    rel, tests = _yaml("release.yml"), _yaml("tests.yml")
    # a tag starts it (or the same run again, by hand, for a tag); publishing a release by hand starts nothing
    assert set(rel["on"]) == {"push", "workflow_dispatch"} and rel["on"]["push"] == {"tags": ["v[0-9]+.[0-9]+.[0-9]+"]}
    assert rel["permissions"] == {}
    gate = rel["jobs"]["tests"]
    assert gate["uses"] == "./.github/workflows/tests.yml"         # this commit's own tests.yml, on this commit
    assert gate["if"] == "github.ref_type == 'tag'" and "needs" not in gate
    assert "workflow_call" in tests["on"]
    # the whole workflow: no job of tests.yml is skipped when it is called from a release
    assert not [name for name, job in tests["jobs"].items() if "if" in job]
    assert set(tests["jobs"]) >= {"pytest", "sdk", "claims", "deadcode"}
    oses = {m["os"] for m in tests["jobs"]["pytest"]["strategy"]["matrix"]["include"]}
    assert oses == {"ubuntu-latest", "macos-latest", "windows-latest"}
    # the caller and the called workflow must not share a concurrency group (GitHub cancels the run if they do)
    assert rel["concurrency"]["group"].startswith("release-") and tests["concurrency"]["group"].startswith("tests-")
    assert "github.workflow" not in tests["concurrency"]["group"]


def test_every_publishing_job_needs_the_gate_and_none_can_run_when_it_did_not_pass():
    rel = _yaml("release.yml")
    text = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
    jobs = {name: job for name, job in rel["jobs"].items() if name != "tests"}
    assert set(jobs) >= {"build", "pypi", "github-release", "registry", "site"}
    for name, job in jobs.items():                                  # every job, whatever it publishes and whoever adds it
        assert "tests" in _needs(job), f"{name} does not need the tests"
        # a job with needs runs only when they all succeeded, unless its own `if` says otherwise
        assert "if" not in job and "continue-on-error" not in json.dumps(job), name
        # PyPI first: nothing else leaves unless PyPI took the release
        assert name in ("build", "pypi") or {"pypi", "github-release"} & set(_needs(job)), name
    # a tag starts the run, so there is no release event: a job that waited for one or read its payload would never run
    assert "github.event" not in "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    # the only secrets are the two registry tokens and PyPI's fallback token (the MCP registry takes this run's OIDC token)
    assert sorted(re.findall(r"secrets\.(\w+)", text)) == ["CARGO_REGISTRY_TOKEN", "NPM_TOKEN", "PYPI_API_TOKEN"]
    # PyPI by trusted publishing first: the upload is accepted on this run's own token, and only this job can ask for it
    pypi = jobs["pypi"]
    assert pypi["permissions"] == {"id-token": "write"} and pypi["environment"] == "pypi"
    run = "\n".join(str(s.get("run", "")) for s in pypi["steps"])
    assert "uv publish --trusted-publishing always" in run and "--check-url https://pypi.org/simple/" in run
    assert not any(str(s.get("uses", "")).startswith("actions/checkout") for s in pypi["steps"])   # it uploads what build built
    # what is uploaded was built once, from the tagged commit, with the tag as its version
    build = "\n".join(str(s.get("run", "")) for s in jobs["build"]["steps"])
    assert 'version="${GITHUB_REF_NAME#v}"' in build and build.count('= "$version"') == 4
    assert _needs(jobs["github-release"]) == ["tests", "build", "pypi"] and _needs(jobs["registry"]) == ["tests", "pypi"]
    assert jobs["github-release"]["permissions"] == {"contents": "write"}
    assert jobs["registry"]["permissions"] == {"contents": "read", "id-token": "write"}


def test_no_other_workflow_publishes_a_release():
    for wf in sorted(WORKFLOWS.glob("*.yml")):
        text = "\n".join(ln for ln in wf.read_text(encoding="utf-8").splitlines() if not ln.lstrip().startswith("#"))
        for pattern, allowed in PUBLISHES.items():
            if re.search(pattern, text):
                assert wf.name in allowed, f"{wf.name} publishes ({pattern}) outside the release gate"
    # the Pages build is no longer started by a release being published: release.yml starts it, after the gate
    net = _yaml("network.yml")
    assert "release" not in net["on"] and "workflow_dispatch" in net["on"]
    site = _yaml("release.yml")["jobs"]["site"]
    assert "gh workflow run network.yml" in site["steps"][0]["run"] and site["permissions"] == {"actions": "write"}


# ---- PyPI: trusted publishing first, the PYPI_API_TOKEN secret second, plain words when neither works ------------------

def _pypi_steps() -> tuple[dict, dict]:
    steps = _yaml("release.yml")["jobs"]["pypi"]["steps"]
    [trusted] = [s for s in steps if s.get("id") == "trusted"]
    [token] = [s for s in steps if "secrets.PYPI_API_TOKEN" in json.dumps(s)]
    assert steps.index(trusted) < steps.index(token)               # the token is the second way, never the first
    return trusted, token


def test_pypi_publishes_by_trusted_publishing_and_falls_back_to_the_token_secret():
    pypi = _yaml("release.yml")["jobs"]["pypi"]
    trusted, token = _pypi_steps()
    # the first attempt has no token in its environment, so it is trusted publishing and nothing else
    assert "env" not in trusted and "uv publish --trusted-publishing always --check-url https://pypi.org/simple/" in trusted["run"]
    assert "secrets." not in json.dumps(trusted)
    # the second runs only when the first did not, with the token as uv reads it, and never asks uv to try trusted publishing
    assert token["if"] == "steps.trusted.outputs.done != 'true'"
    assert token["env"] == {"UV_PUBLISH_TOKEN": "${{ secrets.PYPI_API_TOKEN }}"}
    assert "uv publish --trusted-publishing never --check-url https://pypi.org/simple/" in token["run"]
    # the secret is read by that one step; nothing is allowed to fail quietly; the job installs nothing from a cache
    assert (WORKFLOWS / "release.yml").read_text(encoding="utf-8").count("secrets.PYPI_API_TOKEN") == 1
    assert "continue-on-error" not in json.dumps(pypi) and pypi["permissions"] == {"id-token": "write"}
    [uv] = [s for s in pypi["steps"] if str(s.get("uses", "")).startswith("astral-sh/setup-uv@")]
    assert uv["with"] == {"enable-cache": False}
    # the message for neither way names both, in words someone can act on
    message = re.search(r'echo "::error title=PyPI::(.*)"', token["run"]).group(1)
    for words in ("trusted publisher", "pypi.org", "owner drexthealpha", "repository Knos", "workflow release.yml", "environment pypi", "PYPI_API_TOKEN"):
        assert words in message, words


@pytest.mark.skipif(os.name == "nt" or not shutil.which("bash"), reason="runs the two steps' shell with a stand-in uv")
@pytest.mark.parametrize("trusted_works, secret, token_works, outcome", [
    (True, "", True, "trusted"),              # trusted publishing works: the token is not used, and not even looked at
    (True, "pypi-secret", True, "trusted"),
    (False, "pypi-secret", True, "token"),    # trusted publishing fails or is not set up: the token publishes
    (False, "pypi-secret", False, "fail"),    # and when PyPI refuses that too, the job fails
    (False, "", True, "neither"),             # neither works: it fails, and says what to do
])
def test_the_two_pypi_steps_do_what_the_job_says(tmp_path, trusted_works, secret, token_works, outcome):
    trusted, token = _pypi_steps()
    bin_dir, calls, out = tmp_path / "bin", tmp_path / "calls", tmp_path / "output"
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_text(
        "#!/bin/sh\n"
        'echo "uv $* token=${UV_PUBLISH_TOKEN:-none}" >> "$CALLS"\n'
        'case "$*" in\n'
        '  *"--trusted-publishing always"*) [ "$TRUSTED" = 1 ] && exit 0; echo "trusted publishing is not set up" >&2; exit 2;;\n'
        '  *"--trusted-publishing never"*) [ -n "$UV_PUBLISH_TOKEN" ] && [ "$TOKEN_OK" = 1 ] && exit 0; exit 3;;\n'
        "esac\nexit 9\n", encoding="utf-8")
    uv.chmod(0o755)
    env = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "CALLS": str(calls), "GITHUB_OUTPUT": str(out),
           "TRUSTED": "1" if trusted_works else "0", "TOKEN_OK": "1" if token_works else "0"}
    out.write_text("", encoding="utf-8")
    calls.write_text("", encoding="utf-8")

    def run(step: dict, extra: dict):                       # the shell GitHub starts for a `run`: bash -eo pipefail
        return subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", step["run"]], cwd=str(tmp_path),
                              env={**env, **extra}, capture_output=True, text=True, check=False)

    first = run(trusted, {})
    assert first.returncode == 0                            # it never fails the job: the next step decides
    assert "done=" + ("true" if trusted_works else "false") in out.read_text(encoding="utf-8")
    seen = calls.read_text(encoding="utf-8").splitlines()
    assert len(seen) == 1 and "--trusted-publishing always" in seen[0] and seen[0].endswith("token=none")
    if outcome == "trusted":
        return                                              # the second step's condition is false: it does not run
    second = run(token, {"UV_PUBLISH_TOKEN": secret})
    seen = calls.read_text(encoding="utf-8").splitlines()
    if outcome == "token":
        assert second.returncode == 0 and len(seen) == 2
        assert "--trusted-publishing never" in seen[1] and seen[1].endswith(f"token={secret}")
    elif outcome == "fail":
        assert second.returncode != 0 and len(seen) == 2
    else:
        assert second.returncode == 1 and len(seen) == 1    # no second upload was tried
        assert "trusted publisher" in second.stdout and "PYPI_API_TOKEN" in second.stdout and second.stdout.startswith("::error")
