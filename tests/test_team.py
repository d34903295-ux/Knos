"""Knos Team: `knos serve` shares claims, notes and one budget across machines, and refuses anyone without a seat.

"Two machines" are two KNOS_HOMEs, each with its own clone of the same repo, talking to one server on 127.0.0.1. The
test the prompt asks for: a claim on one machine blocks an edit on the other.
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from knos import guard
from knos.claims import Claims
from knos.identity import Agent
from knos.pro import agentpay, budget, licence, team


@pytest.fixture()
def server(tmp_path, monkeypatch):
    host_home = tmp_path / "server-home"
    monkeypatch.setenv("KNOS_HOME", str(host_home))
    # The product waits 2 s for its server before failing open; a loaded test machine (parallel workers) can take
    # longer to answer from the in-thread server, and these tests are about the answer, not the patience.
    monkeypatch.setattr(team._call, "__defaults__", (None, 30.0))
    token_a, token_b = team.seat_add("alice"), team.seat_add("bob")
    srv, url = team.run_in_thread()
    yield {"url": url, "a": token_a, "b": token_b, "home": host_home}
    srv.shutdown()
    srv.server_close()


def _raw(url: str, route: str, token: str | None = None, host: str | None = None, body: bytes | None = None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    if host:
        headers["Host"] = host
    req = urllib.request.Request(url + route, data=body, method="POST" if body is not None else "GET", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


# ---- the server's own safety ---------------------------------------------------------------------------------------


def test_no_seat_no_answer(server) -> None:
    assert _raw(server["url"], "/v1/whoami")[0] == 401
    assert _raw(server["url"], "/v1/whoami", token="knos_team_guessed")[0] == 401
    code, body = _raw(server["url"], "/v1/whoami", token=server["a"])
    assert code == 200 and body["seat"] == "alice"


def test_a_foreign_host_name_is_refused(server) -> None:
    """DNS rebinding: a web page that points its own name at 127.0.0.1 gets nothing."""
    code, _ = _raw(server["url"], "/v1/whoami", token=server["a"], host="evil.example.com")
    assert code == 421


def test_tokens_are_kept_only_as_hashes(server) -> None:
    raw = (server["home"] / "team-server" / "team.db").read_bytes()
    assert server["a"].encode() not in raw and server["b"].encode() not in raw


def test_big_bodies_bad_routes_and_errors_come_back_as_json(server) -> None:
    code, body = _raw(server["url"], "/v1/notes", token=server["a"], body=b"x" * (team.MAX_BODY + 10))
    assert code == 413 and "error" in body
    code, body = _raw(server["url"], "/v1/nothing", token=server["a"])
    assert code == 400 and "error" in body
    code, body = _raw(server["url"], "/v1/claims/live?repo=../../etc", token=server["a"])
    assert code == 400 and "bad repo" in body["error"]


def test_a_removed_seat_stops_working(server) -> None:
    team.seat_remove("bob")
    assert _raw(server["url"], "/v1/whoami", token=server["b"])[0] == 401


# ---- two machines --------------------------------------------------------------------------------------------------


@pytest.fixture()
def machines(server, repo, tmp_path, monkeypatch):
    clone = tmp_path / "clone-on-b"
    subprocess.run(["git", "clone", "-q", str(repo), str(clone)], check=True, capture_output=True)
    homes = {"a": tmp_path / "home-a", "b": tmp_path / "home-b"}

    def on(name: str) -> Path:
        monkeypatch.setenv("KNOS_HOME", str(homes[name]))
        from knos import paths

        paths.shared_root.cache_clear()
        paths.work_root.cache_clear()
        return repo if name == "a" else clone

    on("a")
    team.join(server["url"], server["a"])
    on("b")
    team.join(server["url"], server["b"])
    return on


@pytest.mark.critical
def test_a_claim_on_one_machine_blocks_an_edit_on_the_other(machines) -> None:
    repo_a = machines("a")
    alice = Agent("claude", "sess-a")
    with Claims(repo_a) as c:
        took, _, mine = c.take(alice, "the auth rewrite", ["src/auth.py"])
    assert took and mine.globs == ("src/auth.py",)

    repo_b = machines("b")
    bob = Agent("cursor", "sess-b")
    v = guard.check(repo_b, str(repo_b / "src" / "auth.py"), bob)
    assert not v.allow and "claude/sess-a" in v.reason
    with Claims(repo_b) as c:
        took, conflict, _ = c.take(bob, "auth too", ["src/**"])
    assert not took and conflict.globs == ("src/auth.py",)

    repo_a = machines("a")
    assert guard.check(repo_a, str(repo_a / "src" / "auth.py"), alice).allow  # the holder is never blocked
    with Claims(repo_a) as c:
        assert [x.description for x in c.release(alice)] == ["the auth rewrite"]
    repo_b = machines("b")
    assert guard.check(repo_b, str(repo_b / "src" / "auth.py"), bob).allow


def test_the_same_session_id_on_two_machines_is_two_agents(machines) -> None:
    repo_a = machines("a")
    with Claims(repo_a) as c:
        assert c.take(Agent("claude", "same-id"), "auth", ["src/auth.py"])[0]
    repo_b = machines("b")
    import socket

    # both "machines" share this computer's name, so make b look like another machine
    import knos.pro.team as t

    real = t.machine
    t.machine = lambda: "other-box"
    try:
        assert not guard.check(repo_b, str(repo_b / "src" / "auth.py"), Agent("claude", "same-id")).allow
    finally:
        t.machine = real
    assert socket.gethostname()


def test_notes_are_shared_across_machines(machines) -> None:
    repo_a = machines("a")
    team.share_note("the retry queue was dropped: the importer is idempotent", "retry queue", "claude", repo_a)
    repo_b = machines("b")
    got = team.team_notes("why was the retry queue dropped", repo_b)
    assert got and "idempotent" in got[0]["fact"] and "alice" in got[0]["who"]


def test_the_team_budget_is_pooled(machines, server, monkeypatch) -> None:
    monkeypatch.setenv("KNOS_HOME", str(server["home"]))
    team.set_team_cap(1.0, "day")  # on the server host
    machines("a")
    got = team.report_spend({"day": 0.6, "week": 0.6, "month": 0.6})
    assert got is not None and not got["over"] and abs(got["team_usd"] - 0.6) < 1e-9
    machines("b")
    got = team.report_spend({"day": 0.5, "week": 0.5, "month": 0.5})
    assert got["over"] and abs(got["team_usd"] - 1.1) < 1e-9  # neither machine alone is over; together they are
    # the guard's path: machine b's real metered spend (a $5 Claude Code call) is reported, and the team is over
    from datetime import datetime, timezone

    logs = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects" / "-x"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "s.jsonl").write_text(json.dumps({
        "type": "assistant", "requestId": "r1", "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "message": {"id": "m1", "model": "claude-opus-5", "usage": {"input_tokens": 1_000_000, "output_tokens": 0}}}) + "\n")
    said = budget.team_refusal() or ""
    assert "team's day spend cap of $1.00" in said and "knos serve budget" in said


def test_an_unreachable_team_server_fails_open(repo, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KNOS_HOME", str(tmp_path / "lonely"))
    team.join("http://127.0.0.1:9", "knos_team_x")  # nothing listens on port 9
    v = guard.check(repo, str(repo / "src" / "auth.py"), Agent("cursor", "s"))
    assert v.allow
    assert "guard allowed" in (tmp_path / "lonely" / "hook.log").read_text(encoding="utf-8")


def test_joining_checks_the_token_first(server, repo, capsys) -> None:
    from knos.cli import main

    assert main(["init", "--remote", server["url"], "--token", "knos_team_wrong", "--no-test", "--no-read",
                 "--hosts", "cursor"]) == 1
    assert "Not joined" in capsys.readouterr().out and team.config() is None
    (Path(os.environ["KNOS_HOME"]).parent / ".cursor").mkdir(exist_ok=True)
    rc = main(["init", "--remote", server["url"], "--token", server["a"], "--no-test", "--no-read", "--hosts", "cursor"])
    assert rc == 0 and team.config()["url"] == server["url"]
    assert main(["init", "--leave-team"]) == 0 and team.config() is None
