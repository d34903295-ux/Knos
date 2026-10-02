"""`knos init`: install the Stop hook for the coding agents on this machine. `knos init --undo` removes it.

    Claude Code   Stop -> `knos hook proof --client claude` in ~/.claude/settings.json
    Codex         Stop -> `knos hook proof --client codex` in ~/.codex/hooks.json (Codex asks you to trust it once)

Nothing else is written. A settings file that is not JSON Knos understands is left exactly as it is. Before a file is
changed for the first time it is copied to <file>.knos-backup.

Knos before 0.3.10 also installed an MCP server and edit hooks (now in drexthealpha/knos-labs). `knos init` removes
those entries, so an upgrade does not leave a host calling commands that no longer exist.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

MARK = "knos-guard"   # every hook Knos ever installed carries this marker in its command


class Unreadable(Exception):
    """A settings file that is not JSON knos understands. It is left exactly as it is, never overwritten."""


def own_script() -> str | None:
    """The `knos` script installed beside the interpreter running this (preferred over a stale one on PATH)."""
    folder = Path(sys.executable).parent
    for name in ("knos.exe", "knos") if os.name == "nt" else ("knos",):
        if (folder / name).is_file():
            return str(folder / name)
    return shutil.which("knos")


def hook_cmd(client: str) -> str:
    """How a hook calls knos: by absolute path with forward slashes (a Windows path through bash loses its
    backslashes); without a script, this interpreter with -m."""
    exe = own_script()
    head = [f'"{exe.replace(os.sep, "/")}"'] if exe else [f'"{sys.executable.replace(os.sep, "/")}"', "-m", "knos"]
    return " ".join(head + ["hook", "proof", "--client", client]) + f" #{MARK}"


def claude_settings() -> Path:
    return Path.home() / ".claude" / "settings.json"


def codex_hooks() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "hooks.json"


HOSTS = {"claude": claude_settings, "codex": codex_hooks}


def present(host: str) -> bool:
    """Whether that agent is installed here: its config folder exists, or its CLI is on PATH."""
    return HOSTS[host]().parent.is_dir() or shutil.which(host) is not None


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        got = json.loads(path.read_text(encoding="utf-8-sig") or "{}")
    except (ValueError, OSError) as why:
        raise Unreadable(f"{path} is not readable JSON, so knos left it alone") from why
    if not isinstance(got, dict):
        raise Unreadable(f"{path} is not a JSON object, so knos left it alone")
    return got


def _save(path: Path, data: dict) -> None:
    backup = path.with_name(path.name + ".knos-backup")
    if path.exists() and not backup.exists():
        shutil.copyfile(path, backup)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _strip(hooks: dict) -> bool:
    """Remove every Knos hook (this version's and older ones) from a host's hooks table. True if any was there."""
    took = False
    for event in list(hooks):
        entries = hooks.get(event)
        if not isinstance(entries, list):
            continue
        kept = [h for h in entries if MARK not in json.dumps(h)]
        took = took or len(kept) != len(entries)
        if kept:
            hooks[event] = kept
        else:
            hooks.pop(event)
    return took


def _set(host: str, install: bool) -> Path | None:
    """Install (or remove) the Stop hook for one host. Returns the file when it was changed."""
    path = HOSTS[host]()
    data = _load(path)
    before = json.dumps(data, sort_keys=True)
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
    _strip(hooks)
    if install:
        entry = {"type": "command", "command": hook_cmd(host), "timeout": 600}
        if host == "codex":
            entry["statusMessage"] = "Knos: proving what you said is done"
        hooks.setdefault("Stop", []).append({"hooks": [entry]})
    if hooks:
        data["hooks"] = hooks
    else:
        data.pop("hooks", None)
    if json.dumps(data, sort_keys=True) == before:
        return None
    _save(path, data)
    return path


# ---- what Knos before 0.3.10 installed, removed on init and on undo ------------------------------------------------

def _legacy_json() -> list[tuple[Path, str]]:
    home = Path.home()
    if sys.platform == "darwin":
        desktop = home / "Library/Application Support/Claude/claude_desktop_config.json"
    elif sys.platform.startswith("win"):
        desktop = Path(os.environ.get("APPDATA", home / "AppData/Roaming")) / "Claude" / "claude_desktop_config.json"
    else:
        desktop = home / ".config/Claude/claude_desktop_config.json"
    xdg = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
    return [(home / ".claude.json", "mcpServers"), (desktop, "mcpServers"), (home / ".cursor" / "mcp.json", "mcpServers"),
            (Path(os.environ.get("OPENCODE_CONFIG") or xdg / "opencode" / "opencode.json"), "mcp")]


def remove_legacy() -> list[str]:
    """Take out the `knos` MCP server entries and the edit hooks earlier versions installed. Returns what it removed."""
    gone: list[str] = []
    for path, key in _legacy_json():
        try:
            data = _load(path)
        except Unreadable:
            continue
        servers = data.get(key)
        if isinstance(servers, dict) and "knos" in servers:
            servers.pop("knos")
            _save(path, data)
            gone.append(f"the knos MCP server in {path}")
    codex = codex_hooks().parent / "config.toml"
    if codex.exists():
        text = codex.read_text(encoding="utf-8")
        out, skipping = [], False
        for line in text.splitlines(keepends=True):
            head = line.strip()
            if head == "[mcp_servers.knos]" or head.startswith("[mcp_servers.knos."):
                skipping = True
                continue
            if skipping and head.startswith("["):
                skipping = False
            if not skipping:
                out.append(line)
        if "".join(out) != text:
            shutil.copyfile(codex, codex.with_name(codex.name + ".knos-backup"))
            codex.write_text("".join(out), encoding="utf-8")
            gone.append(f"the knos MCP server in {codex}")
    cursor = Path.home() / ".cursor" / "hooks.json"
    try:
        data = _load(cursor)
        if isinstance(data.get("hooks"), dict) and _strip(data["hooks"]):
            _save(cursor, data)
            gone.append(f"the edit hook in {cursor}")
    except Unreadable:
        pass
    plugin = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "opencode" / "plugin" / "knos-guard.js"
    if plugin.exists():
        plugin.unlink()
        gone.append(f"the edit plugin {plugin}")
    return gone


def install(hosts: list[str] | None = None) -> dict:
    """{"done": [(host, file)], "skipped": [(host, why)], "removed": [what]}"""
    rep: dict = {"done": [], "skipped": [], "removed": remove_legacy()}
    for host in hosts or list(HOSTS):
        if host not in HOSTS:
            rep["skipped"].append((host, "unknown host: use claude or codex"))
        elif not present(host) and not hosts:
            rep["skipped"].append((host, "not installed here"))
        else:
            try:
                _set(host, True)
                rep["done"].append((host, str(HOSTS[host]())))
            except Unreadable as why:
                rep["skipped"].append((host, str(why)))
    return rep


def undo(hosts: list[str] | None = None) -> dict:
    rep: dict = {"done": [], "skipped": [], "removed": remove_legacy()}
    for host in hosts or list(HOSTS):
        if host not in HOSTS:
            continue
        try:
            if _set(host, False):
                rep["done"].append((host, str(HOSTS[host]())))
        except Unreadable as why:
            rep["skipped"].append((host, str(why)))
    return rep
