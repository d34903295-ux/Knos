"""Sibyl's paid features, through Sibyl's own client, and never around its gate.

    knos learn   MemoryClient.learn() over this repo's journal: repeated patterns across agents become proposed team
                 playbooks. A person accepts one; it is exported to .knos/playbooks/<slug>.md to review and commit,
                 and every machine imports committed playbooks into its Sibyl REFERENCE tier.
    knos lint    MemoryClient.lint() (duplicates, stale entities, schema drift, FTS mismatch), plus Knos's own
                 cross-agent contradiction check: agent A recorded X, agent B recorded not-X, found with Sibyl's
                 multi_record search.

Both are Sibyl Pro features. Knos calls only `MemoryClient.learn()`, `list_skill_proposals()`,
`accept_skill_proposal()` and `lint()`. It never constructs Sibyl's Learner or Linter, never passes a tier, and never
touches Sibyl's tier cache or credentials: on the free tier Sibyl's own gate says no, and Knos says how to get Pro.

Tier detection (for `knos pro buy`, so nobody pays Sibyl twice) asks Sibyl: its official CLI (`sibyl status`), or,
after the person consents, the documented `/api/plugin/access` with their own credentials. Credentials are read only
from the person's own ~/.sibyl-memory/credentials.json, sent only to api.sibyllabs.org, and never printed.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SIBYL_API = "https://api.sibyllabs.org"
PAID = {"pro", "sync", "team", "lifetime", "stake", "staker", "enterprise"}
NEEDS_PRO = "needs Sibyl Pro; get it with `knos pro buy` (Knos and Sibyl Pro in one command)"


class NeedsPro(Exception):
    pass


def credentials_path() -> Path:
    return Path(os.environ.get("SIBYL_CREDENTIALS") or Path.home() / ".sibyl-memory" / "credentials.json")


def credentials() -> dict:
    try:
        got = json.loads(credentials_path().read_text(encoding="utf-8"))
        return got if isinstance(got, dict) else {}
    except (OSError, ValueError):
        return {}


def redacted(value: str) -> str:
    return (value[:6] + "…") if value else ""


# ---- tier ---------------------------------------------------------------------------------------------------------

@dataclass
class Tier:
    name: str        # free | pro | staker | ... | unknown
    source: str      # sibyl-status | access | credentials | none
    detail: str = ""

    @property
    def paid(self) -> bool:
        return self.name in PAID


def tier_from_status_output(text: str) -> str | None:
    """The server tier `sibyl status` printed (its 'server' section), or its local tier if the server was not asked."""
    server = text.split("server", 1)[1] if "server" in text else ""
    for block in (server, text):
        m = re.search(r"Tier\s+([A-Za-z]+)", block)
        if m:
            return m.group(1).lower()
    return None


def tier_via_cli(timeout: float = 30.0) -> Tier | None:
    exe = shutil.which("sibyl")
    if not exe:
        return None
    try:
        done = subprocess.run([exe, "status"], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    got = tier_from_status_output(done.stdout)
    return Tier(got, "sibyl-status") if got else None


def tier_via_access(timeout: float = 10.0) -> Tier | None:
    """POST /api/plugin/access with the person's own account id and session token (what `sibyl status` sends)."""
    c = credentials()
    if not c.get("account_id") or not c.get("session_token"):
        return None
    body = json.dumps({"account_id": c["account_id"], "session_token": c["session_token"]}).encode()
    req = urllib.request.Request(SIBYL_API + "/api/plugin/access", data=body,
                                 headers={"Content-Type": "application/json", "User-Agent": "knos"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - Sibyl's own API only
            got = json.loads(resp.read())
    except (OSError, ValueError):
        return None
    name = str(got.get("tier") or "free").lower()
    if got.get("staker", {}).get("qualified"):
        name = "staker" if name == "free" else name
    return Tier(name, "access", str(got.get("source") or ""))


def detect(consent_to_access: bool = False) -> Tier:
    """Sibyl's word on this person's tier: its own CLI first, then (only with consent) the access endpoint.
    Without either, 'unknown': the purchase then asks rather than guesses."""
    got = tier_via_cli()
    if got:
        return got
    if consent_to_access:
        got = tier_via_access()
        if got:
            return got
    if not credentials():
        return Tier("free", "none", "no Sibyl account on this machine")
    return Tier("unknown", "none")


# ---- learn / lint -------------------------------------------------------------------------------------------------

def _gate(call):
    from sibyl_memory_client.exceptions import TierGateError
    try:
        return call()
    except TierGateError:
        raise NeedsPro(NEEDS_PRO) from None


def learn(mem, run: bool = True) -> dict:
    """One learning pass over this repo's journal (unless run=False); returns it and the pending proposals."""
    report = _gate(lambda: mem.client.learn()) if run else None
    pending = _gate(lambda: mem.client.list_skill_proposals(status="pending"))
    return {"events_scanned": report.events_scanned if report else 0,
            "proposals_made": report.proposals_made if report else 0,
            "pending": [{"id": p.id, "slug": p.proposed_slug, "title": p.proposed_title,
                         "confidence": p.confidence, "body": p.proposed_body} for p in pending]}


def playbook_dir(repo: Path) -> Path:
    return Path(repo) / ".knos" / "playbooks"


def accept(mem, repo: Path, proposal_id: str) -> Path:
    """Accept a proposal (Sibyl writes it to REFERENCE) and export it to .knos/playbooks/ for review and commit."""
    pending = {p.id: p for p in _gate(lambda: mem.client.list_skill_proposals(status="pending", limit=500))}
    p = pending.get(proposal_id)
    if p is None:
        raise LookupError(f"no pending proposal {proposal_id}")
    _gate(lambda: mem.client.accept_skill_proposal(proposal_id))
    slug = re.sub(r"[^a-z0-9-]+", "-", p.proposed_slug.lower()).strip("-")[:60] or proposal_id[:8]
    d = playbook_dir(repo)
    d.mkdir(parents=True, exist_ok=True)
    out = d / f"{slug}.md"
    out.write_text(f"# {p.proposed_title or slug}\n\n{p.proposed_body.strip()}\n\n"
                   f"<!-- accepted from Sibyl proposal {p.id} ({p.pattern_kind}, confidence {p.confidence:.2f}) -->\n",
                   encoding="utf-8")
    return out


def import_playbooks(mem, repo: Path) -> int:
    """Every committed playbook into this machine's Sibyl REFERENCE tier (free: it is the team's own text)."""
    d = playbook_dir(repo)
    if not d.is_dir():
        return 0
    n = 0
    for f in sorted(d.glob("*.md")):
        body = f.read_text(encoding="utf-8")
        key = f"playbook/{f.stem}"
        have = mem.reference(key)
        if have and have.get("body") == body:
            continue
        mem.set_reference(key, body)
        n += 1
    return n


_NEG = re.compile(r"\b(not|no|never|don't|dont|do not|doesn't|isn't|stop|stopped|drop|dropped|avoid|removed?|"
                  r"without|against)\b", re.I)
_WORD = re.compile(r"[a-z0-9]+")


def _core(text: str) -> set[str]:
    return {w for w in _WORD.findall(_NEG.sub(" ", text.lower())) if len(w) > 2}


def contradicts(a: str, b: str) -> bool:
    """X vs not-X: the same claim once with and once without a negation, near-identical otherwise."""
    na, nb = bool(_NEG.search(a)), bool(_NEG.search(b))
    if na == nb:
        return False
    ca, cb = _core(a), _core(b)
    if not ca or not cb:
        return False
    return len(ca & cb) / len(ca | cb) >= 0.6


def recorded_notes(mem) -> list[tuple[str, str]]:
    """(text, who) for every note an agent or a person recorded, from the COLD journal."""
    from .memory import _flatten
    out = []
    for e in mem.journal(limit=5000):
        f = _flatten(e)
        if f.get("source") != "note" or not f.get("text"):
            continue
        who = str(f.get("where") or "").split(" said so", 1)[0]
        out.append((str(f["text"]), who))
    return out


def lint(mem) -> dict:
    """Sibyl's linter, then the cross-agent contradiction pass: for each recorded note, Sibyl's multi_record search
    finds what else was recorded about it, and a pair by different agents that says X and not-X is reported."""
    report = _gate(lambda: mem.client.lint())
    from sibyl_memory_client.multi_record import multi_record_search
    notes = recorded_notes(mem)
    by_text = {t: w for t, w in notes}
    found, seen = [], set()
    for text, who in notes:
        for hit in multi_record_search(mem.client, text, limit=8):
            other = _hit_text(hit)
            if not other or other == text:
                continue
            other_who = by_text.get(other, _hit_who(hit))
            if who and other_who and who == other_who:
                continue
            key = tuple(sorted((text, other)))
            if key in seen or not contradicts(text, other):
                continue
            seen.add(key)
            found.append({"a": text, "a_by": who, "b": other, "b_by": other_who})
    return {"ok": bool(getattr(report, "ok", True)) and not found, "contradictions": found,
            "text": report.to_ascii() if hasattr(report, "to_ascii") else ""}


def _hit_text(hit: dict[str, Any]) -> str:
    body = hit.get("body")
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except ValueError:
            return body
    if isinstance(body, dict):
        for k in ("note", "text", "decision"):
            if body.get(k):
                return str(body[k])
        extra = body.get("extra")
        if isinstance(extra, dict) and extra.get("text"):
            return str(extra["text"])
        ev = body.get("evaluated")
        if isinstance(ev, str):
            return ev
    return str(hit.get("snippet") or "")


def _hit_who(hit: dict[str, Any]) -> str:
    body = hit.get("body")
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except ValueError:
            return ""
    return str((body or {}).get("who") or (body or {}).get("by") or "") if isinstance(body, dict) else ""


# ---- Path B: Sibyl Pro through Sibyl's own checkout ---------------------------------------------------------------

def upgrade(run=subprocess.run) -> str:
    """Run Sibyl's own `sibyl upgrade` for the person's own account: it opens Sibyl's checkout (card, or USDC on
    Base) and polls until Pro is active. Knos never touches that payment. Returns what happened, in words."""
    exe = shutil.which("sibyl")
    if not exe:
        return "missing-cli"
    try:
        done = run([exe, "upgrade"], timeout=60 * 30)
    except (OSError, subprocess.SubprocessError):
        return "failed"
    return "done" if getattr(done, "returncode", 1) == 0 else "not-completed"
