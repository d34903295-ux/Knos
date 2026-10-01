"""A repo's proof history, in its Sibyl store: every claim and verdict, what the evidence showed later, and the checks
that history makes required.

    record(...)   a claim, the checks run, the verdict (entity `proof_claim`)
    observe(...)  later evidence about a commit, e.g. CI failed at 54ce98ad (entity `proof_outcome`)
    lint()        claims the evidence contradicts: "shipped" at a commit whose CI failed
    learn()       each contradiction becomes a required check (entity `proof_rule`): from then on, a claim of that kind
                  runs that check whatever the message says. Sibyl's own MemoryClient.learn() runs too when the
                  account has Sibyl Pro (playbooks across the journal); the rules here need no tier.

`NullStore` keeps nothing: the same engine with no memory, which is what a plain hook amounts to.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass


class NullStore:
    """No memory: records nothing, knows nothing."""

    def put(self, category: str, name: str, body: dict) -> None:
        return None

    def all(self, category: str) -> list[dict]:
        return []


class SibylStore:
    """The repo's Sibyl store (the same one Knos's memory uses), through the public MemoryClient API."""

    def __init__(self, client):
        self.client = client

    @classmethod
    def for_repo(cls, repo) -> "SibylStore":
        from ..memory import Memory
        mem = Memory(repo)
        store = cls(mem.client)
        store._mem = mem   # keep the connection open for the store's lifetime
        return store

    def put(self, category: str, name: str, body: dict) -> None:
        self.client.set_entity(category, name, body, status="active")

    def all(self, category: str) -> list[dict]:
        out = []
        for row in self.client.list_entities(category, status="active", limit=10000):
            body = row.get("body")
            if isinstance(body, str):
                try:
                    body = json.loads(body)
                except ValueError:
                    continue
            if isinstance(body, dict):
                out.append(body)
        return out


@dataclass
class Contradiction:
    sha: str
    claimed: list[str]
    failed: str
    claim: str


def _id(*parts) -> str:
    return hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:24]


def record(store, sha: str, claim_text: str, kinds: list[str], results: list[dict], ok: bool,
           at: float | None = None) -> None:
    store.put("proof_claim", _id(sha, claim_text), {"sha": sha, "claim": claim_text[:2000], "kinds": sorted(kinds),
                                                    "results": results, "ok": ok, "at": at or time.time()})


def observe(store, sha: str, check: str, ok: bool, detail: str = "", at: float | None = None) -> None:
    store.put("proof_outcome", _id(sha, check), {"sha": sha, "check": check, "ok": ok, "detail": detail,
                                                 "at": at or time.time()})


def lint(store) -> list[Contradiction]:
    """Claims the evidence later contradicted: a "done" whose commit failed a check."""
    fails = {}
    for o in store.all("proof_outcome"):
        if not o.get("ok"):
            fails.setdefault(o["sha"], []).append(o["check"])
    out = []
    for c in store.all("proof_claim"):
        for check in fails.get(c.get("sha"), []):
            ran = {r.get("name") for r in c.get("results", []) if r.get("ok")}
            if check not in ran:   # claimed done without that check passing, and the check then failed
                out.append(Contradiction(c["sha"], c.get("kinds", []), check, c.get("claim", "")[:200]))
    return out


def learn(store) -> list[dict]:
    """Turn every contradiction into a required check for that kind of claim. Returns the rules now in force."""
    for x in lint(store):
        for kind in x.claimed or ["done"]:
            store.put("proof_rule", _id(kind, x.failed), {"when": kind, "require": x.failed,
                                                          "because": f"{x.sha[:8]} was claimed ({kind}) and its "
                                                                     f"{x.failed} failed"})
    try:   # Sibyl Pro's own self-learning over the journal, when the account has it; never required
        store.client.learn()
    except Exception:  # noqa: BLE001 - free tier, or a NullStore
        pass
    return rules(store)


def rules(store) -> list[dict]:
    return store.all("proof_rule")


def required(store, kinds: set[str]) -> set[str]:
    return {r["require"] for r in rules(store) if r.get("when") in kinds}


# ---- a delivery against what the buyer told us before (Sibyl) ---------------------------------------------------

@dataclass
class PreferenceViolation:
    preference: str
    line: int          # 1-based line of the delivery
    text: str
    why: str

    def __str__(self) -> str:
        return (f"line {self.line}: {self.text.strip()!r} contradicts the buyer's preference "
                f"{self.preference!r} ({self.why})")


_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⭐⭕‼⁉]")
_BULLET = re.compile(r"^\s*([-*+•]|\d+[.)])\s+")
_WORDS = re.compile(r"\b(?:under|fewer than|less than|at most|max(?:imum)?(?: of)?|no more than|up to)\s+(\d+)\s+words?\b")
_US_UK = {"color": "colour", "colors": "colours", "colored": "coloured", "favor": "favour", "favorite": "favourite",
          "behavior": "behaviour", "honor": "honour", "labor": "labour", "neighbor": "neighbour", "flavor": "flavour",
          "humor": "humour", "organize": "organise", "organization": "organisation", "realize": "realise",
          "recognize": "recognise", "analyze": "analyse", "optimize": "optimise", "apologize": "apologise",
          "center": "centre", "theater": "theatre", "catalog": "catalogue", "gray": "grey", "defense": "defence",
          "traveled": "travelled", "canceled": "cancelled", "customize": "customise", "prioritize": "prioritise"}


def _prefs_of(store_or_memory, buyer: str | None) -> list[str]:
    if store_or_memory is None:
        return []
    if isinstance(store_or_memory, (list, tuple)):
        return [str(p) for p in store_or_memory]
    if hasattr(store_or_memory, "preferences"):       # jobs.buyer_memory.BuyerMemory
        return list(store_or_memory.preferences())
    rows = store_or_memory.all("buyer_preference")    # a proof store: {"buyer", "preference"}
    return [r["preference"] for r in rows if r.get("preference") and (buyer is None or r.get("buyer") == buyer)]


def lint_preferences(store_or_memory, buyer: str | None, delivery_text: str) -> list[PreferenceViolation]:
    """Each place the delivery contradicts a preference the buyer stated before (recalled from Sibyl), citing the
    delivery's line. Deterministic patterns, no model: no emojis, British spelling, under/at most N words, no bullet
    points, no exclamation marks."""
    lines = delivery_text.splitlines()
    out: list[PreferenceViolation] = []
    for pref in _prefs_of(store_or_memory, buyer):
        p = pref.lower()
        if re.search(r"\b(no|without|avoid|never|don'?t)\b.*\bemoji", p):
            out += [PreferenceViolation(pref, i, ln, "emoji") for i, ln in enumerate(lines, 1) if _EMOJI.search(ln)]
        if re.search(r"\b(british|uk)\b.*\b(spelling|english)\b", p):
            for i, ln in enumerate(lines, 1):
                for w in re.findall(r"[A-Za-z]+", ln):
                    if w.lower() in _US_UK:
                        out.append(PreferenceViolation(pref, i, ln, f"US spelling {w!r}; British is "
                                                                    f"{_US_UK[w.lower()]!r}"))
        if re.search(r"\b(no|without|avoid|never|don'?t)\b.*\bbullet", p):
            out += [PreferenceViolation(pref, i, ln, "bullet point") for i, ln in enumerate(lines, 1)
                    if _BULLET.match(ln)]
        if re.search(r"\b(no|without|avoid|never|don'?t)\b.*\bexclamation", p):
            out += [PreferenceViolation(pref, i, ln, "exclamation mark") for i, ln in enumerate(lines, 1) if "!" in ln]
        m = _WORDS.search(p)
        if m:
            strict = re.match(r"(under|fewer than|less than)", m.group(0))
            limit = int(m.group(1)) - (1 if strict else 0)
            n = 0
            for i, ln in enumerate(lines, 1):
                n += len(ln.split())
                if n > limit:
                    out.append(PreferenceViolation(pref, i, ln, f"{len(delivery_text.split())} words; the limit is "
                                                                f"passed on this line"))
                    break
    return out
