"""Sibyl is load-bearing, Knos never routes around its gate or cap, and Sibyl Pro is bought in the same command
without anyone paying twice. Tiers are mocked here, and only here (never in src/)."""

from __future__ import annotations

import json
import os
import re
import stat
import sys
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from knos import sibyl
from knos.cli import main
from knos.memory import Fact, Memory

SRC = Path(__file__).parent.parent / "src"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture()
def paid(monkeypatch):
    """A Sibyl Pro account, as Sibyl's own gate would see it after a server-verified check (tests only)."""
    from sibyl_memory_client import MemoryClient
    monkeypatch.setattr(MemoryClient, "_effective_tier", lambda self: "pro")


# ---- never around the gate or the cap -------------------------------------------------------------------------------

def test_src_never_bypasses_the_gate():
    bad = re.compile(r"""tier\s*=\s*["'](pro|lifetime|team|stake|sync|enterprise)["']|\bLearner\(|\bLinter\(|"""
                     r"""tier_cache\.json|MemoryClient\([^)]*\btier\s*=""")
    hits = []
    for f in SRC.rglob("*.py"):
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if bad.search(line):
                hits.append(f"{f.relative_to(SRC)}:{n}: {line.strip()}")
    assert not hits, hits


def test_every_repo_lives_where_sibyls_cap_counts_it(knos_home, repo, tmp_path):
    from sibyl_memory_client._capcheck import aggregate_db_size
    with Memory(repo) as mem:
        mem.record(Fact(text="one", source="note", where="you", when=_now()))
        assert mem.db_path == Path.home() / ".sibyl-memory" / "memory.db"
        counted = aggregate_db_size(tmp_path / "some-other-store.db")  # whatever store Sibyl is writing to
    assert counted >= mem.db_path.stat().st_size > 0
    other = tmp_path / "second"
    (other / ".git").mkdir(parents=True)
    with Memory(other) as mem2:
        assert mem2.db_path == mem.db_path and mem2.tenant != mem.tenant  # one store, a tenant per repo


def test_a_020_store_is_migrated_into_the_counted_store_once(knos_home, repo):
    from sibyl_memory_client import MemoryClient, Storage

    from knos import paths
    legacy = paths.legacy_store_for(repo)
    st = Storage(str(legacy))
    MemoryClient(st).write_event(evaluated="we chose sqlite in 0.2", acted="note", extra={"text": "we chose sqlite"})
    st.close()
    with Memory(repo) as mem:
        assert mem.migrated > 0
        assert any("sqlite in 0.2" in str(e.get("evaluated")) for e in mem.journal())
    assert not legacy.exists() and Path(str(legacy) + ".migrated").exists()  # kept, never deleted
    with Memory(repo) as mem:
        assert mem.migrated == 0


# ---- learn and lint: Sibyl Pro through MemoryClient only ------------------------------------------------------------

def test_learn_and_lint_on_the_free_tier_say_how_to_get_pro(knos_home, repo, capsys):
    with Memory(repo) as mem:
        mem.record(Fact(text="x", source="note", where="you", when=_now()))
    assert main(["learn"]) == 1
    assert "needs Sibyl Pro; get it with `knos pro buy`" in capsys.readouterr().out
    assert main(["lint"]) == 1
    assert "needs Sibyl Pro" in capsys.readouterr().out


def test_accepted_playbooks_are_exported_and_imported_on_every_machine(knos_home, repo, paid):
    with Memory(repo) as mem:
        with mem.storage.transaction() as conn:  # a proposal as Sibyl's learner would leave it
            conn.execute("INSERT INTO skill_proposals (id, tenant_id, pattern_kind, proposed_slug, proposed_title, "
                         "proposed_body, evidence, confidence, summarizer) VALUES (?,?,?,?,?,?,?,?,?)",
                         ("p1", mem.tenant, "repeated_action", "run-migrations-first", "Run migrations first",
                          "Before editing models, run the migrations.", "[]", 0.9, "local-deterministic"))
        got = sibyl.learn(mem, run=False)
        assert [p["id"] for p in got["pending"]] == ["p1"]
        path = sibyl.accept(mem, repo, "p1")
    assert path == repo / ".knos" / "playbooks" / "run-migrations-first.md"
    assert "run the migrations" in path.read_text(encoding="utf-8")
    other = repo.parent / "clone"
    (other / ".git").mkdir(parents=True)
    (other / ".knos" / "playbooks").mkdir(parents=True)
    (other / ".knos" / "playbooks" / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    with Memory(other) as mem2:
        assert sibyl.import_playbooks(mem2, other) == 1
        assert "run the migrations" in mem2.reference("playbook/run-migrations-first")["body"]
        assert sibyl.import_playbooks(mem2, other) == 0


def test_lint_finds_agents_that_recorded_opposite_things(knos_home, repo, paid):
    with Memory(repo) as mem:
        mem.record(Fact(text="we use redis for the session cache", source="note",
                        where="claude/aaaa said so, 2026-09-30", when=_now(), about="cache"))
        mem.record(Fact(text="we do not use redis for the session cache", source="note",
                        where="codex/bbbb said so, 2026-09-30", when=_now(), about="cache"))
        got = sibyl.lint(mem)
    assert got["contradictions"], got
    c = got["contradictions"][0]
    assert {c["a_by"], c["b_by"]} == {"claude/aaaa", "codex/bbbb"}
    assert not got["ok"]


def test_contradiction_rule():
    assert sibyl.contradicts("we use redis for sessions", "we don't use redis for sessions")
    assert not sibyl.contradicts("we use redis for sessions", "we use redis for sessions")
    assert not sibyl.contradicts("we use redis", "we never deploy on fridays")


# ---- remove Sibyl and each of the five breaks -----------------------------------------------------------------------

def test_without_sibyl_there_is_no_memory(monkeypatch, knos_home, repo):
    monkeypatch.setitem(sys.modules, "sibyl_memory_client", None)
    with pytest.raises(ImportError):
        Memory(repo)


def test_without_sibyls_learner_there_are_no_playbooks(knos_home, repo, paid, monkeypatch):
    from sibyl_memory_client import MemoryClient
    monkeypatch.delattr(MemoryClient, "learn")
    with Memory(repo) as mem, pytest.raises(AttributeError):
        sibyl.learn(mem)


def test_without_sibyls_linter_and_search_there_is_no_lint(knos_home, repo, paid, monkeypatch):
    from sibyl_memory_client import MemoryClient
    monkeypatch.delattr(MemoryClient, "lint")
    with Memory(repo) as mem, pytest.raises(AttributeError):
        sibyl.lint(mem)
    monkeypatch.setitem(sys.modules, "sibyl_memory_client.multi_record", None)
    with Memory(repo) as mem, pytest.raises((ImportError, AttributeError)):
        sibyl.lint(mem)


def test_without_sibyls_langgraph_store_the_framework_example_has_no_memory(monkeypatch):
    monkeypatch.setitem(sys.modules, "sibyl_memory_langgraph", None)
    sys.path.insert(0, str(SRC.parent / "examples"))
    try:
        sys.modules.pop("langgraph_team", None)
        with pytest.raises(ImportError):
            import langgraph_team  # noqa: F401
    finally:
        sys.path.remove(str(SRC.parent / "examples"))
        sys.modules.pop("langgraph_team", None)


def test_records_anchor_sibyl_journal_entries(knos_home, repo):
    from knos.team import records
    with Memory(repo) as mem:
        mem.record(Fact(text="decided to shard by tenant", source="note", where="codex said so", when=_now()))
        with_journal = records.build(b"s" * 32, b"h" * 32, records.period_start(), [], mem.journal())
    without = records.build(b"s" * 32, b"h" * 32, records.period_start(), [], [])
    assert with_journal.data().merkle_root != without.data().merkle_root


# ---- Path B: one command, two payments, never twice -----------------------------------------------------------------

class _MockSibyl:
    """Sibyl's /api/plugin/access, with an account whose tier `sibyl upgrade` flips to pro."""

    def __init__(self, tier: str):
        self.tier, self.calls = tier, []
        mock = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                mock.calls.append((self.path, body))
                if self.path == "/test/upgrade":
                    mock.tier = "pro"
                out = json.dumps({"tier": mock.tier, "source": "stripe" if mock.tier == "pro" else ""}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

        self.server = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


def _fake_sibyl_cli(tmp_path: Path, url: str) -> None:
    """A stand-in for Sibyl's official `sibyl` CLI: `status` prints the server tier, `upgrade` completes checkout."""
    script = tmp_path / "bin" / "sibyl_fake.py"
    script.parent.mkdir()
    script.write_text(f'''import json, sys, urllib.request
def post(p):
    r = urllib.request.Request("{url}" + p, data=b"{{}}", headers={{"Content-Type": "application/json"}})
    return json.loads(urllib.request.urlopen(r).read())
if sys.argv[1] == "upgrade":
    post("/test/upgrade")
print("server")
print("Tier  " + post("/api/plugin/access")["tier"].upper())
''', encoding="utf-8")
    if os.name == "nt":
        (tmp_path / "bin" / "sibyl.bat").write_text(f'@"{sys.executable}" "{script}" %*\n', encoding="utf-8")
    else:
        exe = tmp_path / "bin" / "sibyl"
        exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        exe.chmod(exe.stat().st_mode | stat.S_IEXEC)


def test_path_b_buys_sibyl_pro_through_sibyls_own_client(tmp_path, monkeypatch):
    from knos.pro import bundle
    mock = _MockSibyl("free")
    _fake_sibyl_cli(tmp_path, mock.url)
    monkeypatch.setenv("PATH", str(tmp_path / "bin") + os.pathsep + os.environ["PATH"])
    said = []
    got = bundle.step(said.append, lambda q, d: True, "month")
    assert got == "bought", said
    assert said[-1] == "Sibyl Pro: active."
    assert ("/test/upgrade", {}) in mock.calls  # the checkout ran through Sibyl's own client


def test_nobody_pays_sibyl_twice(tmp_path, monkeypatch):
    from knos.pro import bundle
    mock = _MockSibyl("pro")
    _fake_sibyl_cli(tmp_path, mock.url)
    monkeypatch.setenv("PATH", str(tmp_path / "bin") + os.pathsep + os.environ["PATH"])
    upgraded = []
    said = []
    got = bundle.step(said.append, lambda q, d: True, "month", upgrade=lambda: upgraded.append(1) or "done")
    assert got == "skipped" and not upgraded
    assert "not charged again" in said[0]
    # a staker is Pro too
    staker = bundle.step(said.append, lambda q, d: True, detect=lambda consent_to_access: sibyl.Tier("staker", "access"),
                         upgrade=lambda: upgraded.append(1) or "done")
    assert staker == "skipped" and not upgraded


def test_unknown_tier_is_never_bought_blind(monkeypatch):
    from knos.pro import bundle
    said, upgraded = [], []
    got = bundle.step(said.append, lambda q, d: False, detect=lambda consent_to_access: sibyl.Tier("unknown", "none"),
                      upgrade=lambda: upgraded.append(1) or "done")
    assert got == "unknown" and not upgraded


def test_status_output_parsing():
    assert sibyl.tier_from_status_output("local\nTier  FREE\n\nserver\nTier  PRO\n") == "pro"
    assert sibyl.tier_from_status_output("local\nTier  FREE\n") == "free"
