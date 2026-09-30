# Knos Pro. Licensed under the Functional Source License 1.1 (MIT future licence): see src/knos/pro/LICENSE.
"""Knos Team: `knos serve`, a self-hosted server that makes claims, notes and a spend budget shared across machines.

The customer hosts it; nothing runs on Knos's side. Each machine joins with `knos init --remote <url> --token <t>`.
From then on:
- that machine's claims live on the server, keyed by the repo's first commit, so two clones of one repo on two
  machines share one claim list;
- its notes are shared;
- its spend counts toward one pooled team budget.

The same `claims.Claims` code decides every claim on the server as it does on one machine: exactly one winner, paths
not words, advisory when nothing resolves.

Safety, by design (each was a bug in the archived plane server):
- **Tokens:** one bearer token per seat, created by the host with `knos serve seat add <name>`. The server stores
  only its SHA-256 and compares in constant time. No cookies, so a web page cannot ride a session.
- **Host allow-list:** a request whose Host header is not one the server was told it is (the bind address, localhost,
  or `--name`) is refused. A DNS-rebinding page cannot reach it.
- **Loopback by default:** listening on the network takes an explicit `--host`, and says so.
- **Every handler is wrapped:** an error becomes a JSON 500, never a dropped connection or a traceback on the wire.
- **Bounded requests:** bodies over 64 KB are refused.

A machine that cannot reach its team server fails open, exactly like a crashed hook: the edit is allowed, and one
line goes to ~/.knos/hook.log.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import socket
import sqlite3
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .. import paths

MAX_BODY = 64 * 1024
TIMEOUT = 2.0  # seconds a machine waits for its team server before failing open


class TeamUnreachable(Exception):
    """The team server did not answer: callers fail open."""


# ---- identity of a repo and of a machine ------------------------------------------------------------------------


def repo_id(repo: Path) -> str:
    """The same for every clone of a repo: its first commit. Falls back to the folder name for a repo with none."""
    try:
        got = subprocess.run(["git", "-C", str(repo), "rev-list", "--max-parents=0", "HEAD"], capture_output=True,
                             text=True, timeout=10)
        first = sorted(got.stdout.split())
        if first:
            return first[0][:40]
    except (OSError, subprocess.SubprocessError):
        pass
    return "name:" + Path(repo).name


def machine() -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "-", socket.gethostname() or "machine")[:40]


# ---- the client: this machine's side ---------------------------------------------------------------------------


def config_path() -> Path:
    return paths.home() / "team.json"


def config() -> dict | None:
    try:
        got = json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(got, dict) and got.get("url") and got.get("token"):
        return got
    return None


def join(url: str, token: str) -> dict:
    url = url.rstrip("/")
    if not re.match(r"^https?://", url):
        raise ValueError("the team server address starts with http:// or https://")
    body = {"url": url, "token": token, "machine": machine(), "joined": datetime.now(timezone.utc).isoformat()}
    path = config_path()
    import os

    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(body, fh, indent=2)
    return body


def leave() -> bool:
    try:
        config_path().unlink()
        return True
    except FileNotFoundError:
        return False


def _call(cfg: dict, method: str, route: str, body: dict | None = None, timeout: float = TIMEOUT) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(cfg["url"] + route, data=data, method=method,
                                 headers={"Authorization": "Bearer " + cfg["token"], "Content-Type": "application/json",
                                          "User-Agent": "knos"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - the user's own server
            return json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as why:
        try:
            msg = json.loads(why.read()).get("error", str(why))
        except Exception:
            msg = str(why)
        raise TeamUnreachable(f"team server said {why.code}: {msg}") from why
    except (OSError, ValueError) as why:
        raise TeamUnreachable(f"team server unreachable: {why}") from why


def _agent_wire(agent) -> dict:
    """An agent as the team sees it: host plus a session that names the machine, so two machines never merge.
    A process id is meaningless on another machine, so it stays here."""
    session = agent.session or (f"pid{agent.anchor}" if agent.anchor else "")
    return {"host": agent.host, "session": f"{session}@{machine()}", "label": f"{agent.label}@{machine()}"}


class RemoteClaims:
    """claims.Claims's interface, over HTTP, for one repo on one team server."""

    def __init__(self, cfg: dict, repo: Path) -> None:
        self.cfg = cfg
        self.repo_id = repo_id(repo)

    @staticmethod
    def _local(wire: dict):
        """A claim as this machine sees it: one made here gets its local identity back (so its holder is never
        refused); one made on another machine keeps its @machine tag, so no local agent can be mistaken for it."""
        from dataclasses import replace

        from ..claims import Claim

        c = Claim.from_wire(wire)
        tag = "@" + machine()
        if c.session.endswith(tag):
            bare = c.session[: -len(tag)]
            if bare.startswith("pid") and bare[3:].isdigit():
                return replace(c, session="", anchor=int(bare[3:]))
            return replace(c, session=bare)
        return c

    def _claims(self, rows) -> list:
        return [self._local(r) for r in rows or []]

    def live(self) -> list:
        return self._claims(_call(self.cfg, "GET", f"/v1/claims/live?repo={self.repo_id}"))

    def take(self, agent, description: str, globs: list[str], holds_min: int):
        from ..claims import Claim

        got = _call(self.cfg, "POST", "/v1/claims/take", {"repo": self.repo_id, "agent": _agent_wire(agent),
                                                          "description": description, "globs": globs,
                                                          "holds_min": holds_min})
        c = got.get("conflict")
        m = got.get("mine")
        return bool(got.get("taken")), (self._local(c) if c else None), (self._local(m) if m else None)

    def release(self, agent, description: str = "", everyone: bool = False) -> list:
        got = _call(self.cfg, "POST", "/v1/claims/release", {"repo": self.repo_id, "agent": _agent_wire(agent),
                                                             "description": description, "everyone": everyone})
        return self._claims(got)

    def note_block(self, agent, rel: str, claim) -> None:
        try:
            _call(self.cfg, "POST", "/v1/claims/blocked", {"repo": self.repo_id, "agent": _agent_wire(agent),
                                                           "path": rel, "claim": claim.to_wire()})
        except TeamUnreachable:
            pass

    def events(self, kind: str | None = None, limit: int = 200) -> list:
        return _call(self.cfg, "GET", f"/v1/claims/events?repo={self.repo_id}&limit={int(limit)}"
                     + (f"&kind={kind}" if kind else ""))


class _WireAgent:
    """An agent rebuilt on the server from what a client sent."""

    def __init__(self, d: dict) -> None:
        self.host = str(d.get("host", ""))
        self.session = str(d.get("session", ""))
        self.anchor = None
        self.label = str(d.get("label") or f"{self.host}/{self.session}")

    def owns(self, host: str, session: str, anchor) -> bool:
        return host == self.host and bool(session) and session == self.session


def client_for(repo: Path) -> RemoteClaims | None:
    cfg = config()
    return RemoteClaims(cfg, repo) if cfg else None


def share_note(fact: str, about: str, who: str, repo: Path) -> None:
    cfg = config()
    if not cfg:
        return
    try:
        _call(cfg, "POST", "/v1/notes", {"repo": repo_id(repo), "fact": fact, "about": about, "who": who})
    except TeamUnreachable:
        pass


def team_notes(query: str, repo: Path, limit: int = 5) -> list[dict]:
    cfg = config()
    if not cfg:
        return []
    try:
        return _call(cfg, "GET", "/v1/notes?repo=" + repo_id(repo) + "&limit=" + str(int(limit)) + "&q="
                     + urllib.request.quote(query[:200]))
    except TeamUnreachable:
        return []


def report_spend(usd_by_period: dict[str, float]) -> dict | None:
    """Tell the team what this machine spent today, this week and this month; get back the pooled total against the
    team cap. None when there is no team or it did not answer (fail open)."""
    cfg = config()
    if not cfg:
        return None
    try:
        return _call(cfg, "POST", "/v1/budget/report",
                     {"machine": machine(), "usd": {k: round(float(v), 4) for k, v in usd_by_period.items()}},
                     timeout=10.0)  # a write on the server; asked at most once a minute, so it may take longer
    except TeamUnreachable:
        return None


# ---- the server -----------------------------------------------------------------------------------------------------


def team_dir() -> Path:
    d = paths.home() / "team-server"
    d.mkdir(parents=True, exist_ok=True)
    return d


_SCHEMA = """
CREATE TABLE IF NOT EXISTS seats (name TEXT PRIMARY KEY, token_sha TEXT NOT NULL, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS notes (ts TEXT, repo TEXT, who TEXT, about TEXT, fact TEXT);
CREATE TABLE IF NOT EXISTS spend (machine TEXT, per TEXT, period TEXT, usd REAL, ts TEXT,
                                  PRIMARY KEY (machine, per, period));
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""


def _db(root: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str((root or team_dir()) / "team.db"), timeout=10, isolation_level=None)
    conn.execute("PRAGMA busy_timeout = 10000")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.executescript(_SCHEMA)
    return conn


def seat_add(name: str) -> str:
    """A new seat and its token. The token is shown once, to hand to that person; only its hash is kept."""
    if not re.match(r"^[A-Za-z0-9_.@-]{1,64}$", name):
        raise ValueError("a seat name is letters, digits, '.', '_', '-' or '@'")
    token = "knos_team_" + secrets.token_urlsafe(24)
    conn = _db()
    try:
        conn.execute("INSERT INTO seats VALUES (?,?,?) ON CONFLICT(name) DO UPDATE SET token_sha=excluded.token_sha",
                     (name, hashlib.sha256(token.encode()).hexdigest(), datetime.now(timezone.utc).isoformat()))
    finally:
        conn.close()
    return token


def seat_remove(name: str) -> bool:
    conn = _db()
    try:
        return conn.execute("DELETE FROM seats WHERE name=?", (name,)).rowcount > 0
    finally:
        conn.close()


def seats() -> list[str]:
    conn = _db()
    try:
        return [r[0] for r in conn.execute("SELECT name FROM seats ORDER BY name")]
    finally:
        conn.close()


def set_team_cap(usd: float, per: str) -> None:
    conn = _db()
    try:
        conn.execute("INSERT INTO meta VALUES ('cap', ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                     (json.dumps({"usd": usd, "per": per}),))
    finally:
        conn.close()


def _seat_for(header: str, root: Path | None = None) -> str | None:
    if not header.startswith("Bearer "):
        return None
    given = hashlib.sha256(header[7:].strip().encode()).hexdigest()
    conn = _db(root)
    try:
        rows = conn.execute("SELECT name, token_sha FROM seats").fetchall()
    finally:
        conn.close()
    found = None
    for name, sha in rows:  # compare against every seat, in constant time each
        if hmac.compare_digest(sha, given):
            found = name
    return found


def _period(per: str) -> str:
    now = datetime.now(timezone.utc)
    if per == "week":
        return (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d")
    if per == "month":
        return now.strftime("%Y-%m")
    return now.strftime("%Y-%m-%d")


_REPO = re.compile(r"^(?:[0-9a-f]{7,40}|name:[A-Za-z0-9_.-]{1,100})$")


def _claims(repo: str, root: Path | None = None):
    from ..claims import Claims

    if not _REPO.match(repo or ""):
        raise ValueError("bad repo id")
    d = (root or team_dir()) / "repos"
    d.mkdir(exist_ok=True)
    return Claims(".", db=d / f"{repo.replace(':', '_')}.claims.db")


def make_server(host: str = "127.0.0.1", port: int = 8766, names: tuple[str, ...] = ()):
    allowed: set[str] = set()  # filled in once the port is bound (port 0 means "pick one")
    root = team_dir()  # fixed when the server starts: every request reads and writes here

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a) -> None:
            pass

        def _send(self, code: int, body: Any) -> None:
            raw = json.dumps(body, default=str).encode()
            try:
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                if code >= 400:
                    self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass  # the client hung up first (e.g. after a refused oversized body)

        def _guarded(self, method: str) -> None:
            try:
                if (self.headers.get("Host") or "").lower() not in allowed:
                    return self._send(421, {"error": "this server does not answer to that host name"})
                seat = _seat_for(self.headers.get("Authorization") or "", root)
                if seat is None:
                    return self._send(401, {"error": "a seat token is needed: knos serve seat add <name>"})
                body = {}
                if method == "POST":
                    n = int(self.headers.get("Content-Length") or 0)
                    if n > MAX_BODY:
                        return self._send(413, {"error": "request too large"})
                    body = json.loads(self.rfile.read(n) or b"{}")
                    if not isinstance(body, dict):
                        return self._send(400, {"error": "a JSON object is expected"})
                self._send(200, self._route(method, seat, body))
            except ValueError as why:
                self._send(400, {"error": str(why)})
            except Exception as why:  # never a dropped connection or a traceback on the wire
                self._send(500, {"error": f"{type(why).__name__}"})

        def do_GET(self) -> None:  # noqa: N802
            self._guarded("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._guarded("POST")

        def _route(self, method: str, seat: str, body: dict) -> Any:
            from urllib.parse import parse_qs, urlparse

            url = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            route = (method, url.path)
            if route == ("GET", "/v1/whoami"):
                return {"seat": seat, "version": _version()}
            if route == ("GET", "/v1/claims/live"):
                with _claims(q.get("repo", ""), root) as c:
                    return [x.to_wire() for x in c.live()]
            if route == ("POST", "/v1/claims/take"):
                agent = _WireAgent(body.get("agent") or {})
                globs = [str(g) for g in body.get("globs") or []][:50]
                with _claims(str(body.get("repo", "")), root) as c:
                    took, conflict, mine = c.take(agent, str(body.get("description", ""))[:300], globs or None,
                                                  int(body.get("holds_min") or 30), resolve_names=False)
                return {"taken": took, "conflict": conflict.to_wire() if conflict else None,
                        "mine": mine.to_wire() if mine else None}
            if route == ("POST", "/v1/claims/release"):
                agent = _WireAgent(body.get("agent") or {})
                with _claims(str(body.get("repo", "")), root) as c:
                    return [x.to_wire() for x in c.release(agent, str(body.get("description", "")),
                                                           bool(body.get("everyone")))]
            if route == ("POST", "/v1/claims/blocked"):
                from ..claims import Claim

                with _claims(str(body.get("repo", "")), root) as c:
                    c.note_block(_WireAgent(body.get("agent") or {}), str(body.get("path", "")),
                                 Claim.from_wire(body.get("claim") or {}))
                return {"ok": True}
            if route == ("GET", "/v1/claims/events"):
                with _claims(q.get("repo", ""), root) as c:
                    return c.events(q.get("kind"), min(int(q.get("limit", 200)), 1000))
            if route == ("POST", "/v1/notes"):
                conn = _db(root)
                try:
                    conn.execute("INSERT INTO notes VALUES (?,?,?,?,?)",
                                 (datetime.now(timezone.utc).isoformat(), str(body.get("repo", ""))[:60],
                                  f"{str(body.get('who', ''))[:80]} ({seat})", str(body.get("about", ""))[:200],
                                  str(body.get("fact", ""))[:4000]))
                finally:
                    conn.close()
                return {"ok": True}
            if route == ("GET", "/v1/notes"):
                words = [w for w in re.findall(r"\w+", (q.get("q") or "").lower()) if len(w) > 2][:8]
                conn = _db(root)
                try:
                    rows = conn.execute("SELECT ts, who, about, fact FROM notes WHERE repo=? ORDER BY ts DESC LIMIT 500",
                                        (q.get("repo", ""),)).fetchall()
                finally:
                    conn.close()
                scored = [(sum(w in (r[2] + " " + r[3]).lower() for w in words), r) for r in rows]
                hits = [r for s, r in sorted(scored, key=lambda x: -x[0]) if s > 0 or not words]
                return [{"ts": r[0], "who": r[1], "about": r[2], "fact": r[3]}
                        for r in hits[:min(int(q.get("limit", 5)), 50)]]
            if route == ("POST", "/v1/budget/report"):
                by = body.get("usd") if isinstance(body.get("usd"), dict) else {}
                who = f"{str(body.get('machine', ''))[:40]}/{seat}"
                conn = _db(root)
                try:
                    conn.execute("BEGIN")
                    for per in ("day", "week", "month"):
                        if per in by:
                            conn.execute("INSERT INTO spend VALUES (?,?,?,?,?) ON CONFLICT(machine, per, period) DO "
                                         "UPDATE SET usd=excluded.usd, ts=excluded.ts",
                                         (who, per, _period(per), float(by[per] or 0),
                                          datetime.now(timezone.utc).isoformat()))
                    conn.execute("COMMIT")
                    cap = conn.execute("SELECT v FROM meta WHERE k='cap'").fetchone()
                    cap = json.loads(cap[0]) if cap else None
                    cper = (cap or {}).get("per", "day")
                    total = conn.execute("SELECT COALESCE(SUM(usd),0) FROM spend WHERE per=? AND period=?",
                                         (cper, _period(cper))).fetchone()[0]
                finally:
                    conn.close()
                return {"team_usd": total, "cap": cap, "over": bool(cap) and total >= float(cap["usd"])}
            raise ValueError(f"no route {method} {url.path}")

    server = ThreadingHTTPServer((host, port), Handler)
    bound = server.server_address[1]
    allowed.update(f"{h}:{bound}".lower() for h in {"127.0.0.1", "localhost", "[::1]", host, *names})
    allowed.update(h.lower() for h in names)  # a name given with its port already, or behind a proxy on :443
    return server


def _version() -> str:
    from .. import version

    return version()


def serve(host: str = "127.0.0.1", port: int = 8766, names: tuple[str, ...] = (), say=print) -> None:
    server = make_server(host, port, names)
    where = f"http://{host}:{server.server_address[1]}"
    say(f"Knos team server on {where}. Seats: {', '.join(seats()) or 'none yet (knos serve seat add <name>)'}")
    if host not in ("127.0.0.1", "localhost", "::1"):
        say("It is listening on the network. Put it behind HTTPS (a reverse proxy) before it leaves your LAN.")
    say("Machines join with:  knos init --remote " + where + " --token <their seat token>")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def run_in_thread(host: str = "127.0.0.1", port: int = 0):
    """For tests and the demo: a server on a free loopback port, in a daemon thread. Returns (server, url)."""
    server = make_server(host, port)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    time.sleep(0.05)
    return server, f"http://{host}:{server.server_address[1]}"
