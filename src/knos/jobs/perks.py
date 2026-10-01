"""Sibyl Pro, paid for by Knos: once a buyer's fees on released jobs reach $12 (one month of Sibyl Pro), Knos buys
that buyer a month of Sibyl Pro, so their memory across jobs has no 5 MB cap.

Fees are counted from the chain (5% of every job of theirs that was released), never from a Knos database. The
purchase goes through a `Checkout`. Sibyl's partner checkout is not live yet, so the only checkout in this release is
`SimulatedCheckout`, which records what would be bought and says so (`simulated: true`) everywhere it appears.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from .. import paths

UNITS = 1_000_000
FEE_BPS = 500
THRESHOLD_UNITS = 12 * UNITS
MONTH_UNITS = 12 * UNITS


def fees_paid(jobs: list, wallet: str) -> int:
    return sum(j.amount * FEE_BPS // 10_000 for j in jobs if j.state == "released" and str(j.buyer) == wallet)


@dataclass
class Grant:
    wallet: str
    months: int
    fees_units: int
    at: int
    simulated: bool


class SimulatedCheckout:
    """Records grants in ~/.knos/jobs/perks.json. Moves no money and calls no one."""
    simulated = True

    def __init__(self, path: Path | None = None):
        self.path = path or (paths.home() / "jobs" / "perks.json")

    def granted(self, wallet: str) -> list[dict]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8")).get(wallet, [])
        except (OSError, ValueError):
            return []

    def buy(self, wallet: str, fees_units: int) -> Grant:
        try:
            book = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            book = {}
        g = Grant(wallet, 1, fees_units, int(time.time()), True)
        book.setdefault(wallet, []).append(g.__dict__)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(book, indent=1), encoding="utf-8")
        return g


def status(jobs: list, wallet: str, checkout=None) -> dict:
    checkout = checkout or SimulatedCheckout()
    fees = fees_paid(jobs, wallet)
    earned = fees // MONTH_UNITS
    have = len(checkout.granted(wallet))
    return {"wallet": wallet, "fees_paid_usdc": fees / UNITS, "months_earned": earned, "months_granted": have,
            "next_month_at_usdc": (earned + 1) * MONTH_UNITS / UNITS, "simulated": checkout.simulated}


def claim(jobs: list, wallet: str, checkout=None) -> list[Grant]:
    """Buy every month of Sibyl Pro this buyer's fees have earned and not yet received. Idempotent."""
    checkout = checkout or SimulatedCheckout()
    fees = fees_paid(jobs, wallet)
    owed = fees // MONTH_UNITS - len(checkout.granted(wallet))
    return [checkout.buy(wallet, fees) for _ in range(max(0, owed))]
