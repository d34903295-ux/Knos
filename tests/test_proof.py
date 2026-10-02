"""Proof: AI agent work counts only when Knos proves it. The Stop hook blocks an unproven "done", lets a stop through
after 3 blocks on unchanged evidence, and Sibyl history is load-bearing: the replay of Knos's own releases."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from knos.proof import checks, claims, engine, history, hook

import _replay as replay

DATA = Path(__file__).parent / "data" / "release_replay.json"


def _sibyl(tmp_path):
    from sibyl_memory_client import MemoryClient
    return history.SibylStore(MemoryClient.local(str(tmp_path / "proof.db"), tenant_id="repo"))


def test_claims_are_read_by_kind_not_phrasing():
    c = claims.read("Knos 0.3.4 is shipped: tagged, on PyPI, 801 passed, CI is green. "
                    "Removed src/knos/bundle.py. Live at https://drexthealpha.github.io/Knos/.")
    assert {"release", "pypi", "tests", "ci", "deleted", "urls"} <= c.kinds
    assert c.deleted == ["src/knos/bundle.py"] and c.urls == ["https://drexthealpha.github.io/Knos/"]
    assert c.version == "0.3.4"
    assert not claims.read("I looked at the parser; here is what I think we should do.").says_done


def test_replay_sibyl_blocks_031_and_032_and_null_store_passes_them(tmp_path, repo):
    data = json.loads(DATA.read_text(encoding="utf-8"))
    store = _sibyl(tmp_path)
    replay.seed(store, data["history"])
    assert {r["require"] for r in history.rules(store)} == {"ci"}          # learned from 0.2.0 false done
    got = {v: ok for v, ok, _ in replay.with_sibyl(store, data["replay"], repo)}
    assert got == {"0.3.0": True, "0.3.1": False, "0.3.2": False, "0.3.3": True}
    base = {v: ok for v, ok, _ in replay.null_baseline(data["replay"], repo)}
    assert base == {"0.3.0": True, "0.3.1": True, "0.3.2": True, "0.3.3": True}   # wrongly passes 0.3.1, 0.3.2
    assert {x.failed for x in history.lint(store)} == {"ci"}


def test_the_stop_hook_blocks_then_lets_go_after_three_on_unchanged_evidence(tmp_path, repo, knos_home):
    payload = {"cwd": str(repo), "session_id": "s1", "last_assistant_message": "All done: CI is green."}
    fail = {"ci": lambda r, c, cfg: checks.Result("ci", False, "CI failed for abc", {"sha": "abc"})}
    said = [hook.stop(payload, history.NullStore(), fail)[0] for _ in range(4)]
    assert said == ["block", "block", "block", "warn"]
    ok = {"ci": lambda r, c, cfg: checks.Result("ci", True, "green", {"sha": "abc"})}
    assert hook.stop(payload, history.NullStore(), ok)[0] == "allow"
    assert hook.stop({**payload, "last_assistant_message": "Here is a plan."}, history.NullStore())[0] == "allow"


def test_the_stop_hook_reads_claude_codes_transcript(tmp_path):
    t = tmp_path / "t.jsonl"
    t.write_text("\n".join(json.dumps(r) for r in [
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "working"}]}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Shipped; tests pass."}]}}]) + "\n",
        encoding="utf-8")
    assert hook.last_message({"transcript_path": str(t)}) == "Shipped; tests pass."


def test_checks_run_by_knos_not_the_agent(tmp_path, repo):
    assert not checks.deleted(repo, ["README.md"]).ok if (repo / "README.md").exists() else True
    assert checks.deleted(repo, ["nope.txt"]).ok
    assert checks.urls(["https://x.test/a"], getter=lambda u: (404, b"")).ok is False
    assert checks.urls(["https://x.test/a"], getter=lambda u: (200, b"")).ok
    (repo / "pyproject.toml").write_text('[project]\nname = "demo-pkg"\nversion = "9.9.9"\n', encoding="utf-8")
    assert checks.pypi(repo, getter=lambda u: (404, b"")).ok is False
    runs = {("run", "list"): (0, json.dumps([{"databaseId": 1, "status": "completed", "conclusion": "failure",
                                               "workflowName": "tests"}])),
            ("run", "view"): (0, json.dumps({"jobs": [{"name": "pytest", "conclusion": "failure"}]}))}
    got = checks.ci(repo, "abc123", runner=lambda a: runs[tuple(a[:2])])
    assert not got.ok and "tests/pytest: failure" in got.detail


def test_a_bare_done_is_a_claim_and_runs_the_tests_when_there_is_a_test_command(tmp_path, repo, knos_home):
    for said in ("Done.", "All done!", "It's fixed.", "The task is complete.", "Everything is implemented.",
                 "I updated the parser.\nDone."):
        assert "done" in claims.read(said).kinds, said
    for said in ("It is not done yet.", "Here is a plan.", "What should be done about the parser?",
                 "I have done some reading."):
        assert "done" not in claims.read(said).kinds, said
    payload = {"cwd": str(repo), "session_id": "s-done", "last_assistant_message": "Done."}
    assert hook.stop(payload, history.NullStore())[0] == "allow"        # no test command here: nothing to run
    (repo / "package.json").write_text('{"scripts": {"test": "node -e \\"process.exit(1)\\""}}', encoding="utf-8")
    assert checks.test_command(repo) == "npm test"
    fail = {"tests": lambda r, c, cfg: checks.Result("tests", False, "`npm test`: 1 failing", {})}
    verdict, why = hook.stop(payload, history.NullStore(), fail)
    assert verdict == "block" and "npm test" in why


def test_the_test_command_follows_the_language(tmp_path):
    for name, body, want in (("pyproject.toml", "[project]\nname='x'\n", "pytest -q"), ("Cargo.toml", "[package]\n", "cargo test"),
                             ("go.mod", "module x\n", "go test ./..."), ("package.json", '{"scripts":{"test":"vitest"}}', "npm test")):
        d = tmp_path / name.replace(".", "_")
        d.mkdir()
        (d / name).write_text(body, encoding="utf-8")
        assert checks.test_command(d) == want
    assert checks.test_command(tmp_path) == "" and checks.test_command(tmp_path, "make test") == "make test"


def test_init_installs_only_the_stop_hook_and_undo_removes_it(knos_home, _isolated):
    from knos import init
    home = _isolated
    (home / ".claude").mkdir()
    settings = home / ".claude" / "settings.json"
    settings.write_text(json.dumps({"model": "x", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "mine"}]}]}}), "utf-8")
    rep = init.install()
    assert [h for h, _ in rep["done"]] == ["claude"] and ("codex", "not installed here") in rep["skipped"]
    data = json.loads(settings.read_text())
    assert set(data["hooks"]) == {"Stop"} and data["model"] == "x"
    cmds = [h["command"] for e in data["hooks"]["Stop"] for h in e["hooks"]]
    assert cmds[0] == "mine" and "hook proof --client claude" in cmds[1] and len(cmds) == 2
    init.install()                                                     # a second init changes nothing
    assert json.loads(settings.read_text()) == data
    assert (home / ".claude" / "settings.json.knos-backup").exists()
    init.undo()
    after = json.loads(settings.read_text())
    assert [h["command"] for e in after["hooks"]["Stop"] for h in e["hooks"]] == ["mine"] and after["model"] == "x"


def test_init_removes_what_older_versions_installed(knos_home, _isolated):
    from knos import init
    home = _isolated
    (home / ".claude").mkdir()
    old = {"hooks": {"PreToolUse": [{"matcher": "Edit", "hooks": [{"type": "command", "command": "knos hook guard --client claude #knos-guard"}]},
                                    {"matcher": "Bash", "hooks": [{"type": "command", "command": "other"}]}],
                     "SessionStart": [{"hooks": [{"type": "command", "command": "knos hook start --client claude #knos-guard"}]}],
                     "Stop": [{"hooks": [{"type": "command", "command": "knos hook proof --client claude #knos-guard"}]}]}}
    (home / ".claude" / "settings.json").write_text(json.dumps(old), "utf-8")
    (home / ".claude.json").write_text(json.dumps({"mcpServers": {"knos": {"command": "knos", "args": ["mcp"]},
                                                                  "other": {"command": "x"}}}), "utf-8")
    (home / ".codex").mkdir()
    (home / ".codex" / "config.toml").write_text('model = "x"\n\n[mcp_servers.knos]\ncommand = "knos"\nargs = ["mcp"]\n\n[other]\na = 1\n', "utf-8")
    rep = init.install()
    assert any("MCP server" in x for x in rep["removed"])
    hooks = json.loads((home / ".claude" / "settings.json").read_text())["hooks"]
    assert set(hooks) == {"PreToolUse", "Stop"} and json.dumps(hooks).count("knos-guard") == 1
    assert hooks["PreToolUse"] == [{"matcher": "Bash", "hooks": [{"type": "command", "command": "other"}]}]
    assert json.loads((home / ".claude.json").read_text())["mcpServers"] == {"other": {"command": "x"}}
    toml = (home / ".codex" / "config.toml").read_text()
    assert "mcp_servers.knos" not in toml and "[other]" in toml and 'model = "x"' in toml


def test_a_settings_file_knos_cannot_read_is_left_alone(knos_home, _isolated):
    from knos import init
    (_isolated / ".claude").mkdir()
    bad = _isolated / ".claude" / "settings.json"
    bad.write_text("{not json", "utf-8")
    rep = init.install()
    assert bad.read_text() == "{not json" and rep["done"] == [] and "left it alone" in rep["skipped"][0][1]


def test_a_hook_name_from_an_older_version_does_nothing(knos_home, capsys):
    from knos.cli import main
    for name in ("guard", "start", "safety"):
        assert main(["hook", name, "--client", "claude"]) == 0
    assert capsys.readouterr().out == ""


def test_ci_reads_gh_json_followed_by_a_notice(tmp_path):
    from knos.proof import checks
    notice = "\nA new release of gh is available: 2.80.0\n"

    def gh(args):
        if args[1] == "list":
            return 0, '[{"databaseId": 1, "status": "completed", "conclusion": "failure", "workflowName": "ci"}]' + notice
        return 0, '{"jobs": [{"name": "tests", "conclusion": "failure"}]}' + notice
    r = checks.ci(tmp_path, "a" * 40, runner=gh)
    assert not r.ok and "ci/tests: failure" in r.detail
