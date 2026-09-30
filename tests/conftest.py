"""Shared fixtures. Every test runs in its own throwaway home, with no network and no KNOS_* settings from the
machine running it, and the session fails if a test touched the real home's knos or agent settings."""

from __future__ import annotations

import hashlib
import os
import socket
import subprocess
from pathlib import Path

import pytest

_REAL_HOME = Path.home()
_WATCHED = [
    _REAL_HOME / ".claude" / "settings.json",
    _REAL_HOME / ".claude.json",
    _REAL_HOME / ".cursor" / "hooks.json",
    _REAL_HOME / ".cursor" / "mcp.json",
    _REAL_HOME / ".knos" / "config.json",
    _REAL_HOME / ".knos" / "licence.json",
    _REAL_HOME / ".knos" / "budget.json",
    _REAL_HOME / ".sibyl-memory" / "memory.db",  # knos 0.3 keeps memory in Sibyl's own store: tests must never touch yours
]


def _digest(p: Path) -> str:
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError:
        return "absent"


@pytest.fixture(scope="session", autouse=True)
def _real_home_untouched():
    before = {p: _digest(p) for p in _WATCHED}
    yield
    changed = [str(p) for p in _WATCHED if _digest(p) != before[p]]
    assert not changed, f"a test changed the real home: {changed}"


_real_connect = socket.socket.connect


def _loopback_only(self, address):
    host = address[0] if isinstance(address, tuple) else address
    if isinstance(host, str) and host not in ("127.0.0.1", "::1", "localhost") and self.family in (
            socket.AF_INET, socket.AF_INET6):
        raise OSError(f"tests do not open the network (tried {host})")
    return _real_connect(self, address)


@pytest.fixture(autouse=True)
def _isolated(tmp_path_factory, monkeypatch):
    fake = tmp_path_factory.mktemp("home")
    for k in list(os.environ):
        if k.startswith("KNOS_"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HOME", str(fake))
    monkeypatch.setenv("USERPROFILE", str(fake))
    monkeypatch.setenv("APPDATA", str(fake / "AppData" / "Roaming"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(fake / ".config"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(fake / ".claude"))
    monkeypatch.setenv("CODEX_HOME", str(fake / ".codex"))
    monkeypatch.setenv("KNOS_HOME", str(fake / ".knos"))
    monkeypatch.delenv("OPENCODE_CONFIG", raising=False)
    monkeypatch.setattr(socket.socket, "connect", _loopback_only)
    from knos import code, paths, refresh

    def _reset() -> None:
        refresh._last_check.clear()
        paths.shared_root.cache_clear()
        paths.work_root.cache_clear()
        for cached in (code._find_binary, code.readtags):  # a test may have swapped one for a plain function
            getattr(cached, "cache_clear", lambda: None)()

    _reset()
    yield fake
    _reset()


@pytest.fixture()
def knos_home(_isolated):
    home = Path(os.environ["KNOS_HOME"])
    home.mkdir(parents=True, exist_ok=True)
    return home


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    """A small real git repo, with one secret in it. Tests stand inside it, as a person or an agent would."""
    r = tmp_path / "repo"
    (r / "src").mkdir(parents=True)
    (r / "src" / "auth.py").write_text("def login():\n    return True\n", encoding="utf-8")
    (r / ".env").write_text("STRIPE_KEY=sk_live_quokka_9931\n", encoding="utf-8")
    (r / "README.md").write_text("# demo\n", encoding="utf-8")

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=str(r), check=True, capture_output=True, text=True)

    git("init", "-q")
    git("config", "user.email", "tess@example.com")
    git("config", "user.name", "Tess Marlow")
    git("add", "-A")
    git("commit", "-q", "-m", "Add login, and drop redis for sqlite\n\nRedis was one dependency for one counter.")
    monkeypatch.chdir(r)
    return r
