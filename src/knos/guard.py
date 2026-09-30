"""The edit guard: refuse an edit to files another agent has claimed. Nothing else.

Every host ships a hook that runs before a tool call and can refuse it:

    Claude Code   PreToolUse on Edit|Write|MultiEdit|NotebookEdit   permissionDecision "deny", or exit 2
    Cursor        preToolUse                                        permission "deny", or exit 2
    OpenCode      tool.execute.before                               throw

The guard refuses one thing: a path covered by a live claim held by a different agent (`claims.py`, `identity.py`),
including a claimed file that has been renamed. Git sees the rename, or the new file is byte-identical to the
committed one, so `git mv parser.py helper.py` does not launder the claim.

The rules it keeps (the product's invariants):

  - the agent that holds a claim is never refused (same host and the same session or host process);
  - it refuses only a verified collision, or (Knos Pro, only when a person set one) an agent spend cap that has
    been reached. Rules in CLAUDE.md, withdrawn decisions and text similarity never block;
  - it guards edits, not reads. Shell commands that write files (`sed -i`, `mv`, a script) are not seen by any of
    these hooks, and this does not pretend otherwise;
  - anything unexpected (no store, a crash, an old version, an unreadable payload) exits 0 and writes one line to
    ~/.knos/hook.log. A broken install must never stand between an agent and its own repository.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import paths
from .identity import Agent

REFUSE = 2
ALLOW = 0

CLIENTS = ("claude", "cursor", "opencode")


@dataclass(frozen=True)
class Verdict:
    allow: bool
    reason: str = ""

    @property
    def code(self) -> int:
        return ALLOW if self.allow else REFUSE


def log(line: str) -> None:
    """One line in ~/.knos/hook.log; never raises."""
    try:
        with open(paths.home() / "hook.log", "a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now(timezone.utc).isoformat()} {line}\n")
    except Exception:
        pass


def _as_agent(who: Agent | str) -> Agent:
    if isinstance(who, Agent):
        return who
    from .identity import host_from_client
    return Agent(host=host_from_client(str(who)))


# ---- renames: a claimed file moved to a new name is still claimed -------------------

def _moved(repo: Path) -> tuple[list[str], set[str], dict[str, str]]:
    """(deleted tracked paths, untracked paths, {new: old} renames git spotted), from `git status`."""
    try:
        # --untracked-files=all: a file moved into a brand-new folder must show up as that file, not as the folder
        out = subprocess.run(["git", "status", "--porcelain", "--find-renames", "--untracked-files=all"], cwd=repo,
                             capture_output=True,
                             text=True, timeout=10, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return [], set(), {}
    gone: list[str] = []
    fresh: set[str] = set()
    renamed: dict[str, str] = {}
    for line in out.splitlines():
        if len(line) < 4:
            continue
        code, name = line[:2], line[3:].strip()
        if code.startswith("R") and " -> " in name:
            old, new = (part.strip().strip('"') for part in name.split(" -> ", 1))
            renamed[new] = old
        elif "D" in code:
            gone.append(name.strip('"'))
        elif code == "??":
            fresh.add(name.strip('"'))
    return gone, fresh, renamed


def _committed(repo: Path, rel: str) -> bytes | None:
    try:
        done = subprocess.run(["git", "cat-file", "blob", f"HEAD:{rel}"], cwd=repo, capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.replace(b"\r\n", b"\n") if done.returncode == 0 else None


def may_have_moved(repo: Path, claims) -> bool:
    """Cheap test before asking git: could a claimed file have been moved away since it was claimed?

    A literal claimed path that still exists was not moved. For a glob, a file moved out of a folder changes that
    folder's modification time, so if no folder under the glob's literal prefix changed since the claim was taken,
    nothing left it. Anything unsure answers True and git decides."""
    for claim in claims:
        try:
            since = datetime.fromisoformat(claim.taken_at.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return True
        for g in claim.globs:
            literal = not re.search(r"[*?\[]", g)
            if literal and not (repo / g).exists():
                return True
            if literal and not (repo / g).is_dir():
                continue  # a single file that is still there was not moved
            if literal:
                top = repo / g.rstrip("/")  # a folder claim covers everything under it: watch the folder
            else:
                prefix = re.split(r"[*?\[]", g, 1)[0].rsplit("/", 1)[0] if "/" in g else ""
                top = repo / prefix if prefix else repo
            if not top.is_dir():
                return True
            seen = 0
            for root, dirs, _files in os.walk(top):
                dirs[:] = [d for d in dirs if d not in (".git", "node_modules", ".venv", "__pycache__")]
                seen += 1
                if seen > 2000:
                    return True
                try:
                    if os.stat(root).st_mtime > since - 1:
                        return True
                except OSError:
                    return True
    return False


def renamed_from(repo: Path, rel: str, is_claimed) -> str | None:
    """The claimed path `rel` used to be, when this is really a rename (git says so, or the bytes are identical)."""
    gone, fresh, renamed = _moved(repo)
    was = renamed.get(rel)
    if was is not None and is_claimed(was):
        return was
    if rel not in fresh:
        return None
    here = None
    for old in gone:
        if not is_claimed(old):
            continue
        before = _committed(repo, old)
        if before is None:
            continue
        if here is None:
            try:
                here = (repo / rel).read_bytes().replace(b"\r\n", b"\n")
            except OSError:
                return None
        if here == before:
            return old
    return None


# ---- the decision ---------------------------------------------------------------

def _since(ts: str) -> str:
    try:
        t = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone()
        return t.strftime("%H:%M")
    except ValueError:
        return "earlier"


def refusal(rel: str, claim, was: str | None = None) -> str:
    """The one line an agent (and the person watching) reads."""
    what = f"{rel} (renamed from {was})" if was else rel
    left = max(1, round(claim.minutes_left))
    return (f"knos: {what} is claimed by {claim.label} since {_since(claim.taken_at)} ({claim.description}). "
            f"Ask them, or take other work; the claim lapses in {left} min. A person can release it: knos done --all")


def check(repo: Path, target: str, who: Agent | str) -> Verdict:
    """Whether `who` may edit `target` in `repo`, and the one-line reason if not."""
    from .claims import Claims, claims_db

    agent = _as_agent(who)
    repo = Path(repo).resolve()
    try:
        rel = Path(target).resolve().relative_to(repo).as_posix()
    except (ValueError, OSError):
        return Verdict(True)  # outside the repo: nothing recorded, nothing to refuse
    if not claims_db(repo).exists() and not (paths.home() / "team.json").exists():
        return Verdict(True)  # no local claims and no team server: nothing can be held
    try:
        with Claims(repo) as c:
            live = [x for x in c.live() if not x.advisory and not x.held_by(agent)]
            if not live:
                return Verdict(True)
            for claim in live:
                if claim.covers(rel):
                    c.note_block(agent, rel, claim)
                    return Verdict(False, refusal(rel, claim))
            if not may_have_moved(repo, live):
                return Verdict(True)
            was = renamed_from(repo, rel, lambda p: any(x.covers(p) for x in live))
            if was is not None:
                claim = next(x for x in live if x.covers(was))
                c.note_block(agent, rel, claim)
                return Verdict(False, refusal(rel, claim, was))
    except Exception as exc:
        log(f"guard allowed {rel}: {type(exc).__name__}: {exc}")
        return Verdict(True)
    return Verdict(True)


# ---- talking to each client ------------------------------------------------------------

EDIT_TOOLS = {"edit", "write", "multiedit", "notebookedit", "str_replace_editor", "apply_patch", "edit_file", "write_file"}


def target_of(client: str, event: dict) -> str:
    """The path an edit hook payload is about, or "" when it is not an edit."""
    if client == "claude":
        got = event.get("tool_input") or {}
        return str(got.get("file_path") or got.get("notebook_path") or "")
    if client == "cursor":
        tool = str(event.get("tool_name") or "").lower().replace(" ", "_")
        if tool and not any(t in tool for t in ("edit", "write", "patch", "create", "replace")):
            return ""  # reads and searches are never guarded
        if event.get("file_path"):
            return str(event["file_path"])
        got = event.get("tool_input") or event.get("arguments") or {}
        return str(got.get("file_path") or got.get("path") or got.get("target_file") or "")
    got = event.get("args") or event.get("tool_input") or {}
    return str(got.get("filePath") or got.get("file_path") or got.get("path") or "")


def render(client: str, verdict: Verdict) -> str:
    if verdict.allow:
        return ""
    if client == "claude":
        return json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                  "permissionDecisionReason": verdict.reason}})
    if client == "cursor":
        return json.dumps({"permission": "deny", "user_message": verdict.reason, "agent_message": verdict.reason})
    return json.dumps({"deny": True, "reason": verdict.reason})


def decide(client: str, event: dict, repo: Path | None = None) -> Verdict:
    from . import identity

    target = target_of(client, event)
    if not target:
        return Verdict(True)
    root = Path(repo or event.get("cwd") or Path.cwd())
    found = paths.repo_here(root)
    if found is not None:
        root = found
    if not Path(target).is_absolute():
        target = str(root / target)
    capped = _over_budget(root)
    if capped:
        return Verdict(False, capped)
    return check(root, target, identity.for_hook(client, event))


def _over_budget(repo: Path | None = None) -> str | None:
    """Knos Pro's spend cap, when one is set: a one-line refusal once it is reached. No cap, no cost (one stat)."""
    home = paths.home()
    mine, team = (home / "budget.json").exists(), (home / "team.json").exists()
    if not mine and not team:
        return None
    try:
        from .pro import budget
    except ImportError:
        return None
    return (budget.refusal(repo) if mine else None) or (budget.team_refusal(budget.CHECK_EVERY) if team else None)


def run(client: str, stdin_text: str) -> tuple[str, int]:
    """Read one payload, return what to print and what to exit with."""
    try:
        event = json.loads(stdin_text or "{}")
    except ValueError:
        log(f"guard ({client}): unreadable payload, allowed")
        return "", ALLOW
    if not isinstance(event, dict):
        return "", ALLOW
    try:
        verdict = decide(client, event)
    except Exception as exc:
        log(f"guard ({client}) allowed: {type(exc).__name__}: {exc}")
        return "", ALLOW
    return render(client, verdict), verdict.code


# ---- installing and removing ---------------------------------------------------------

MARK = "knos-guard"


def _script() -> str | None:
    from .init import own_script

    return own_script() or shutil.which("knos")


def knos_cmd() -> list[str]:
    """How a hook calls knos: the `knos` script installed with the knos that ran `knos init` (a stale copy earlier on
    PATH is not used), by absolute path, quoted with forward slashes (a Windows path through bash loses its
    backslashes). Without a script, this interpreter with -m."""
    exe = _script()
    if exe:
        return [f'"{exe.replace(os.sep, "/")}"']
    return [f'"{sys.executable.replace(os.sep, "/")}"', "-m", "knos"]


def knos_cmd_argv() -> list[str]:
    """knos_cmd for subprocess (no shell quoting)."""
    exe = _script()
    return [exe] if exe else [sys.executable, "-m", "knos"]


def _knos_cmd() -> list[str]:  # kept for callers from 0.1
    return knos_cmd()


def hook_cmd(kind: str, client: str) -> str:
    return " ".join(knos_cmd() + ["hook", kind, "--client", client])


def claude_settings() -> Path:
    return Path.home() / ".claude" / "settings.json"


def cursor_hooks() -> Path:
    return Path.home() / ".cursor" / "hooks.json"


def opencode_plugin() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "opencode" / "plugin" / "knos-guard.js"


class Unreadable(Exception):
    """A settings file that is not JSON knos understands. It is left exactly as it is, never overwritten."""


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


def _peek(path: Path) -> dict:
    """_load for reading only: an unreadable file reads as empty."""
    try:
        return _load(path)
    except Unreadable:
        return {}


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def install_claude() -> Path:
    """PreToolUse on edits -> the guard; SessionStart -> the notice (and the session record identity needs)."""
    path = claude_settings()
    data = _load(path)
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        hooks = data["hooks"] = {}
    pre = [h for h in (hooks.get("PreToolUse") or []) if MARK not in json.dumps(h)]
    pre.append({"matcher": "Edit|Write|MultiEdit|NotebookEdit",
                "hooks": [{"type": "command", "command": hook_cmd("guard", "claude") + f" #{MARK}"}]})
    hooks["PreToolUse"] = pre
    start = [h for h in (hooks.get("SessionStart") or []) if MARK not in json.dumps(h)]
    start.append({"hooks": [{"type": "command", "command": hook_cmd("start", "claude") + f" #{MARK}"}]})
    hooks["SessionStart"] = start
    _save(path, data)
    return path


def install_cursor() -> Path:
    path = cursor_hooks()
    data = _load(path)
    data.setdefault("version", 1)
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        hooks = data["hooks"] = {}
    for event in ("preToolUse", "beforeReadFile"):   # beforeReadFile only to remove what 0.1 put there
        kept = [h for h in (hooks.get(event) or []) if MARK not in json.dumps(h)]
        if event == "preToolUse":
            kept.append({"command": hook_cmd("guard", "cursor") + f" #{MARK}"})
        if kept:
            hooks[event] = kept
        else:
            hooks.pop(event, None)
    _save(path, data)
    return path


_OPENCODE_JS = """// knos-guard - installed by `knos init`, removed by `knos init --undo`.
// Refuses an edit to files another agent has claimed. Remove this file and nothing else changes.
export const KnosGuard = async ({{ $ }}) => ({{
  "tool.execute.before": async (input, output) => {{
    const name = String(input?.tool ?? "");
    if (!/edit|write|patch/i.test(name)) return;
    const payload = JSON.stringify({{ args: output?.args ?? {{}}, cwd: process.cwd() }});
    const done = await ${quoted}.catch((e) => e);
    if ((done?.exitCode ?? 0) === 2) {{
      const said = String(done?.stdout ?? "");
      let why = "knos: another agent has claimed this file.";
      try {{ why = JSON.parse(said).reason || why; }} catch {{}}
      throw new Error(why);
    }}
  }},
}});
"""


def install_opencode() -> Path:
    path = opencode_plugin()
    path.parent.mkdir(parents=True, exist_ok=True)
    quoted = "`echo ${payload} | " + hook_cmd("guard", "opencode") + "`"
    path.write_text(_OPENCODE_JS.format(quoted=quoted), encoding="utf-8")
    return path


def uninstall_claude() -> bool:
    path = claude_settings()
    data = _peek(path)
    hooks = data.get("hooks") or {}
    took = False
    for event in ("PreToolUse", "SessionStart"):
        kept = [h for h in (hooks.get(event) or []) if MARK not in json.dumps(h)]
        if len(kept) != len(hooks.get(event) or []):
            took = True
        if kept:
            hooks[event] = kept
        else:
            hooks.pop(event, None)
    if took:
        _save(path, data)
    return took


def uninstall_cursor() -> bool:
    path = cursor_hooks()
    data = _peek(path)
    hooks = data.get("hooks") or {}
    took = False
    for event in ("preToolUse", "beforeReadFile"):
        kept = [h for h in (hooks.get(event) or []) if MARK not in json.dumps(h)]
        if len(kept) != len(hooks.get(event) or []):
            took = True
        if kept:
            hooks[event] = kept
        else:
            hooks.pop(event, None)
    if took:
        _save(path, data)
    return took


def uninstall_opencode() -> bool:
    path = opencode_plugin()
    if not path.exists():
        return False
    path.unlink()
    return True


def installed() -> dict[str, bool]:
    return {"claude": MARK in json.dumps(_peek(claude_settings()).get("hooks") or {}),
            "cursor": MARK in json.dumps(_peek(cursor_hooks()).get("hooks") or {}),
            "opencode": opencode_plugin().exists()}
