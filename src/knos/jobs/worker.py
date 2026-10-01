"""The reference worker (`knos work`): find an open job, claim it, do it with the operator's own model, run the brief's
checks and the buyer's shared preferences locally, and deliver the sealed result with its hash on chain.

It never delivers work that fails the brief's own checks: it retries with the failure in the prompt, and if it still
fails it lets the work deadline pass, so the buyer is refunded in full by the escrow. A worker is paid only when the
buyer accepts, or stays silent through the review window.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from solders.keypair import Keypair

from . import checks, market, models


@dataclass
class Outcome:
    job_id: bytes
    title: str
    delivered: bool
    note: str
    seconds: float = 0.0


@dataclass
class Worker:
    ledger: object
    relay: object
    key: Keypair
    model: Callable[[str], str]
    kinds: tuple[str, ...] = market.KINDS
    min_price: int = 0
    tries: int = 2
    log: Callable[[str], None] = field(default=lambda s: None)
    on_delivered: Callable[[bytes, str], None] = field(default=lambda jid, title: None)

    def prompt(self, brief: market.Brief, failure: str = "") -> str:
        parts = [f"JOB: {brief.title}", "", brief.task]
        if brief.kind == "python":
            parts += ["", "Return one Python module (no fences). It will be imported as `solution`."]
        if brief.kind in ("csv", "json"):
            parts += ["", f"Return only the {brief.kind.upper()}."]
        if brief.checks and brief.checks.get("tests"):
            parts += ["", "It must pass these tests:", brief.checks["tests"]]
        rules = (brief.checks or {})
        if rules.get("must_include"):
            parts += ["", "It must include: " + "; ".join(rules["must_include"])]
        if rules.get("must_not_include"):
            parts += ["", "It must not include: " + "; ".join(rules["must_not_include"])]
        if rules.get("max_words"):
            parts += ["", f"At most {rules['max_words']} words."]
        if brief.preferences:
            parts += ["", "This buyer's standing preferences (follow every one):"] + [f"- {p}" for p in brief.preferences]
        if failure:
            parts += ["", f"A previous attempt failed this check: {failure}. Fix it."]
        if brief.scripted_answer is not None:
            parts += ["", "SCRIPTED_ANSWER:\n" + brief.scripted_answer + "\n:END"]
        return "\n".join(parts)

    def wants(self, j, brief: market.Brief) -> bool:
        return brief.kind in self.kinds and j.amount >= self.min_price and j.buyer != self.key.pubkey()

    def do(self, j, brief: market.Brief) -> Outcome:
        start = time.monotonic()
        jid = market.job_id_for(self.ledger, j.address, brief)
        if jid is None or not market.claim(self.ledger, self.key, jid):
            return Outcome(jid or b"", brief.title, False, "someone else claimed it")
        self.log(f"claimed  {brief.title}  ({j.amount / 1e6:.2f} USDC)")
        failure = ""
        for _ in range(self.tries + 1):
            text = models.strip_fences(self.model(self.prompt(brief, failure)))
            ok, why = checks.run(brief.kind, brief.checks, text, brief.preferences)
            if ok:
                market.deliver(self.ledger, self.relay, self.key, jid, j.buyer, text.encode(), brief.seal_to)
                self.log(f"delivered {brief.title}: checks passed, sealed to the buyer, hash on chain")
                self.on_delivered(jid, brief.title)
                return Outcome(jid, brief.title, True, "delivered", time.monotonic() - start)
            failure = why
        self.log(f"gave up   {brief.title}: {failure} (the buyer is refunded at the deadline)")
        return Outcome(jid, brief.title, False, f"checks failed: {failure}", time.monotonic() - start)

    def once(self) -> list[Outcome]:
        done = []
        for j, brief in market.open_jobs(self.ledger, self.relay):
            if self.wants(j, brief):
                done.append(self.do(j, brief))
        return done

    def run(self, every: float = 5.0, until: float | None = None) -> None:
        while until is None or time.monotonic() < until:
            try:
                self.once()
            except Exception as why:  # noqa: BLE001 - a flaky RPC or relay must not stop the worker
                self.log(f"retrying: {type(why).__name__}: {why}")
            time.sleep(every)
