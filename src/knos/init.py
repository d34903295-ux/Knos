"""`knos init`: install the Stop hook and the `knos mcp` server for the coding agents on this machine.
`knos init --undo` removes both.

    Claude Code   Stop -> `knos hook proof --client claude` in ~/.claude/settings.json
                  mcpServers.knos in ~/.claude.json
    Codex         Stop -> `knos hook proof --client codex` in ~/.codex/hooks.json (Codex asks you to trust it once)
                  [mcp_servers.knos] in ~/.codex/config.toml
    Cursor        mcpServers.knos in ~/.cursor/mcp.json       (only when ~/.cursor exists)
    Gemini CLI    mcpServers.knos in ~/.gemini/settings.json  (only when ~/.gemini exists)

Nothing else is written. A settings file that is not JSON Knos understands is left exactly as it is. Before a file is
changed for the first time it is copied to <file>.knos-backup.

Knos before 0.3.10 also installed a memory MCP server and edit hooks (now in drexthealpha/knos-labs). `knos init`
removes those entries, so an upgrade does not leave a host calling commands that no longer exist. A server named
`knos` that runs `... mcp` with a command that still exists is left as it is (it starts this server); any other is
an older one, or points at a command that is gone, and is replaced.
"""

from __future__ import annotations

import json
import os
import re
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


def knos_argv() -> list[str]:
    """How a host starts knos: the script by its absolute path (a host runs it with its own PATH, not the shell's);
    without a script, this interpreter with -m."""
    exe = own_script()
    return [os.path.abspath(exe)] if exe else [sys.executable, "-m", "knos"]


def hook_cmd(client: str) -> str:
    """The hook's command line, with forward slashes (a Windows path through bash loses its backslashes)."""
    exe, *rest = knos_argv()
    return " ".join([f'"{exe.replace(os.sep, "/")}"', *rest, "hook", "proof", "--client", client]) + f" #{MARK}"


def mcp_server() -> tuple[str, list[str]]:
    """(command, args) a host runs to start `knos mcp`."""
    exe, *rest = knos_argv()
    return exe, [*rest, "mcp"]


def claude_settings() -> Path:
    return Path.home() / ".claude" / "settings.json"


def codex_hooks() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "hooks.json"


def codex_config() -> Path:
    return codex_hooks().parent / "config.toml"


HOSTS = {"claude": claude_settings, "codex": codex_hooks}
# Where each host keeps its MCP servers. Claude Code and Codex also get the Stop hook; Cursor and Gemini CLI have no
# hook Knos uses, so they are touched only when their folder is already there.
MCP = {"claude": lambda: Path.home() / ".claude.json", "codex": codex_config,
       "cursor": lambda: Path.home() / ".cursor" / "mcp.json",
       "gemini": lambda: Path.home() / ".gemini" / "settings.json"}


def present(host: str) -> bool:
    """Whether that agent is installed here: its config folder exists, or (Claude Code, Codex) its CLI is on PATH."""
    if host not in HOSTS:
        return MCP[host]().parent.is_dir()
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


def _backup(path: Path) -> Path:
    return path.with_name(path.name + ".knos-backup")


def _write(path: Path, text: str) -> None:
    """Replace the file in one step (a crash never leaves half a settings file), keeping its permissions: these
    files can hold other servers' tokens. The first time, the original is kept beside it, with its permissions."""
    if path.exists() and not _backup(path).exists():
        shutil.copy2(path, _backup(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".knos-tmp")
    tmp.write_bytes(text.encode("utf-8"))
    if path.exists():
        shutil.copymode(path, tmp)
    os.replace(tmp, path)


def _save(path: Path, data: dict) -> None:
    _write(path, json.dumps(data, indent=2) + "\n")


def _put_back(path: Path, text: str) -> None:
    """Write what is left after Knos took its entry out. A file Knos itself created (it has no backup) and that is
    empty again is removed, so undo leaves no file behind."""
    if text.strip() in ("", "{}") and not _backup(path).exists():
        path.unlink()
    else:
        _write(path, text)


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


# ---- the `knos mcp` server ------------------------------------------------------------------------------------------

_HEADER = re.compile(r"""^\s*\[\s*mcp_servers\s*\.\s*(?:knos|"knos"|'knos')\s*(\.[^\]]*)?\]\s*(?:\#.*)?$""")
_ANY_HEADER = re.compile(r"^\s*\[")


def _ours(entry) -> bool:
    """Whether a server named `knos` is exactly what this install writes."""
    command, args = mcp_server()
    return isinstance(entry, dict) and entry.get("command") == command and entry.get("args") == args


def _works(entry) -> bool:
    """Whether a server named `knos` starts this product's server: it runs `... mcp` with a command that exists (the
    one this install writes, an older install's that is still there, or `uvx knos mcp` from a registry). Such an
    entry is left as it is. Anything else named knos is an older version's server, or points at a command that is
    gone, and is replaced."""
    if not isinstance(entry, dict) or not isinstance(entry.get("command"), str):
        return False
    args = entry.get("args")
    if not isinstance(args, list) or not args or args[-1] != "mcp":
        return False
    command = entry["command"]
    if "knos" not in Path(command).name.lower() and not any("knos" in str(a).lower() for a in args):
        return False
    return (os.path.isabs(command) and Path(command).is_file()) or shutil.which(command) is not None


def _toml(text: str, path: Path) -> dict:
    try:
        import tomllib
    except ModuleNotFoundError:   # Python 3.10
        import tomli as tomllib  # type: ignore[no-redef]
    try:
        return tomllib.loads(text)
    except ValueError as why:
        raise Unreadable(f"{path} is not TOML knos can read, so knos left it alone") from why


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as why:
        raise Unreadable(f"{path} is not readable text, so knos left it alone") from why


def _toml_without(text: str) -> str:
    """The file's text without the [mcp_servers.knos] table and its sub-tables. Codex's config is edited as text, so
    every other line stays exactly as written, line endings included. The table runs from its header to its last
    setting; comments and blank lines after that belong to whatever follows and are kept. One blank line before the
    header goes with it, so adding the table and taking it out again leaves the file as it was."""
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    tail: list[str] = []          # blank and comment lines seen since the table's last setting
    inside = False
    for line in lines:
        if _HEADER.match(line):
            if not inside and out and not out[-1].strip():
                out.pop()
            inside, tail = True, []      # (a sub-table of ours: what lay between is ours too)
            continue
        if inside and _ANY_HEADER.match(line):
            inside = False
            out += tail
            tail = []
        if not inside:
            out.append(line)
        elif not line.strip() or line.lstrip().startswith("#"):
            tail.append(line)
        else:
            tail = []
    if inside:
        out += [t for t in tail if t.lstrip().startswith("#")]
    return "".join(out)


def _mcp_toml(path: Path, install: bool) -> bool:
    text = _read_text(path)
    before = _toml(text, path)
    servers = before.get("mcp_servers") if isinstance(before.get("mcp_servers"), dict) else {}
    have = servers.get("knos")
    if install and have is not None and _works(have):
        return False                          # this product's server is already there: leave the user's file alone
    if not install and not _ours(have):
        return False
    rest = _toml_without(text) if have is not None else text
    if have is not None and "knos" in (_toml(rest, path).get("mcp_servers") or {}):
        raise Unreadable(f"{path} sets mcp_servers.knos in a form knos does not edit, so knos left it alone")
    new = rest
    if install:
        nl = "\r\n" if "\r\n" in text else "\n"
        command, args = mcp_server()
        gap = "" if not rest else nl if rest.endswith(("\n", "\r\n")) else nl + nl
        new = rest + gap + nl.join(["[mcp_servers.knos]", f"command = {json.dumps(command)}",
                                    f"args = [{', '.join(json.dumps(a) for a in args)}]"]) + nl
    # the edit may change nothing but mcp_servers.knos; anything else and the file is left alone
    after = _toml(new, path)
    strip = lambda d: {**d, "mcp_servers": {k: v for k, v in (d.get("mcp_servers") or {}).items() if k != "knos"}}  # noqa: E731
    if strip(after) != strip(before):
        raise Unreadable(f"{path} could not be edited without touching other settings, so knos left it alone")
    if install:
        _write(path, new)
    else:
        _put_back(path, new)
    return True


def _mcp_json(host: str, path: Path, install: bool) -> bool:
    data = _load(path)
    before = json.dumps(data, sort_keys=True)
    servers = data.get("mcpServers", {})
    if not isinstance(servers, dict):
        raise Unreadable(f"{path} has an mcpServers that is not an object, so knos left it alone")
    if install and not _works(servers.get("knos")):
        command, args = mcp_server()
        entry = {"command": command, "args": args}
        servers["knos"] = entry if host == "gemini" else {"type": "stdio", **entry}
    elif not install and _ours(servers.get("knos")):
        servers.pop("knos")
    if servers:
        data["mcpServers"] = servers
    else:
        data.pop("mcpServers", None)   # to a host, no table and an empty one are the same
    if json.dumps(data, sort_keys=True) == before:
        return False
    if install:
        _save(path, data)
    else:
        _put_back(path, json.dumps(data, indent=2) + "\n")
    return True


def _mcp(host: str, install: bool) -> Path | None:
    """Register (or remove) the `knos mcp` server with one host. Returns the file when it was changed."""
    path = MCP[host]()
    changed = _mcp_toml(path, install) if host == "codex" else _mcp_json(host, path, install)
    return path if changed else None


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
    """Take out the `knos` MCP server entries and the edit hooks earlier versions installed. Returns what it removed.
    An entry that starts this product's server is not one of them (see _works); `undo` removes the one this install
    wrote."""
    gone: list[str] = []
    for path, key in _legacy_json():
        try:
            data = _load(path)
        except Unreadable:
            continue
        servers = data.get(key)
        if isinstance(servers, dict) and "knos" in servers and not _works(servers["knos"]):
            servers.pop("knos")
            _save(path, data)
            gone.append(f"the knos MCP server in {path}")
    codex = codex_config()
    try:
        text = _read_text(codex)
        have = (_toml(text, codex).get("mcp_servers") or {}).get("knos") if text else None
        if have is not None and not _works(have):
            rest = _toml_without(text)
            if "knos" not in (_toml(rest, codex).get("mcp_servers") or {}):
                _write(codex, rest)
                gone.append(f"the knos MCP server in {codex}")
    except Unreadable:
        pass
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
    """{"done": [(host, hook file)], "mcp": [(host, MCP file)], "skipped": [(host, why)], "removed": [what]}"""
    rep: dict = {"done": [], "mcp": [], "skipped": [], "removed": remove_legacy()}
    for host in hosts or list(MCP):
        if host not in MCP:
            rep["skipped"].append((host, f"unknown host: use {', '.join(MCP)}"))
            continue
        if not present(host) and not (hosts and host in HOSTS):
            if hosts or host in HOSTS:
                rep["skipped"].append((host, "not installed here"))
            continue
        for kind, table, change in (("done", HOSTS, _set), ("mcp", MCP, _mcp)):
            if host in table:
                try:
                    change(host, True)
                    rep[kind].append((host, str(table[host]())))
                except Unreadable as why:
                    rep["skipped"].append((host, str(why)))
    return rep


def undo(hosts: list[str] | None = None) -> dict:
    rep: dict = {"done": [], "mcp": [], "skipped": [], "removed": remove_legacy()}
    for host in hosts or list(MCP):
        for kind, table, change in (("done", HOSTS, _set), ("mcp", MCP, _mcp)):
            if host in table:
                try:
                    if change(host, False):
                        rep[kind].append((host, str(table[host]())))
                except Unreadable as why:
                    rep["skipped"].append((host, str(why)))
    return rep
