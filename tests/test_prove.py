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


def test_a_merge_mints_the_token_in_a_job_that_runs_no_pr_code():
    merged = _yaml(ROOT / ".github" / "workflows" / "prove.yml")["jobs"]["merged"]
    assert merged["if"] == "github.event.action == 'closed' && github.event.pull_request.merged == true"
    assert merged["permissions"] == {"id-token": "write"}
    assert not any(str(s.get("uses", "")).startswith("actions/checkout") for s in merged["steps"])
    run = "\n".join(str(s.get("run", "")) for s in merged["steps"])
    assert 'aud="knos:pay:$REPO_ID:$ISSUE:$author:$HEAD:' in run and run.count(":0\"") == 1     # mode 0, zero checks
    envs = {k: v for s in merged["steps"] for k, v in (s.get("env") or {}).items()}
    assert envs["AUTHOR_ID"] == "${{ github.event.pull_request.user.id }}" and envs["REPO_ID"] == "${{ github.repository_id }}"
    assert "pull_request.body" not in json.dumps(merged) and "github.event" not in run          # event data via env only


def test_check_runs_on_every_bounty_pr_with_no_token_it_could_misuse():
    check = _yaml(ROOT / ".github" / "workflows" / "prove.yml")["jobs"]["check"]
    assert check["if"] == "github.event.action != 'closed'" and "needs" not in check       # both modes: the status to require
    assert check["permissions"] == {"contents": "read", "checks": "read"}                  # read-only; no id-token
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
    assert envs["CHECKS"] == "${{ needs.check.outputs.checks_hash }}" and envs["AUTHOR_ID"] == "${{ github.event.pull_request.user.id }}"
    assert "pip install --system \"knos==" in runs                  # knos from PyPI, never from the PR
    # the judge is the code at this workflow file's own commit (what the bounty pinned); no caller input can swap it
    assert set(doc["on"]["workflow_call"]["inputs"]) == {"issue", "setup"}
    for job in ("check", "attest"):
        install = next(s for s in jobs[job]["steps"] if "install knos" in str(s.get("name", "")))
        assert install["env"] == {"REF": "${{ job.workflow_sha }}", "REPO": "${{ job.workflow_repository }}"}
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
    assert prove["permissions"] == {"contents": "read", "checks": "read", "id-token": "write"}
    finder = doc["jobs"]["job"]
    assert "pull_request.body" not in json.dumps(finder["steps"][0]["run"])   # via env, never inlined into a script
    assert not any(str(s.get("uses", "")).startswith("actions/checkout") for s in finder["steps"])


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
