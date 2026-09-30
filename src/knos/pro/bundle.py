# Knos Pro. Licensed under the Functional Source License 1.1 (MIT future licence): see src/knos/pro/LICENSE.
"""Sibyl Pro in the same `knos pro buy` command (Path B): two payments, one command, nobody charged twice.

After the Knos payment is verified, Knos asks Sibyl for the person's tier. Pro or Staker: the Sibyl part is skipped.
Free: Knos runs Sibyl's own official client, `sibyl upgrade`, for the person's own account; that opens Sibyl's
checkout (card, or USDC on Base) and waits until Pro is active. The founder's money is never involved and Knos never
touches Sibyl's payment. Renewals of Sibyl Pro are Sibyl's own subscription; every Knos renewal checks the tier again
first.
"""

from __future__ import annotations

from typing import Callable

from .. import sibyl

SIBYL_PRO_USD = {"month": 12, "year": 108}  # Sibyl's list price (sibyllabs.org/pro, read 30 Sep 2026)


def step(say: Callable[[str], None], confirm: Callable[[str, bool], bool], period: str = "month",
         detect=sibyl.detect, upgrade=sibyl.upgrade) -> str:
    """Returns 'skipped' (already paid), 'bought', 'declined', 'missing-cli', 'unknown' or 'not-completed'."""
    consent = confirm("Check your Sibyl tier with Sibyl's server, using your own Sibyl credentials?", True)
    tier = detect(consent_to_access=consent)
    if tier.paid:
        say(f"Sibyl Pro: already active ({tier.name}, from {tier.source}); not charged again.")
        return "skipped"
    if tier.name == "unknown":
        say("Sibyl tier: unknown (no answer from Sibyl). Not buying Sibyl Pro blind: check with `sibyl status`, "
            "then `sibyl upgrade` if you are on the free tier.")
        return "unknown"
    price = SIBYL_PRO_USD.get(period, SIBYL_PRO_USD["month"])
    if not confirm(f"Sibyl is on the free tier. Get Sibyl Pro now ({price} USD per {period}, paid to Sibyl through "
                   "Sibyl's own checkout)?", True):
        say("Sibyl Pro: skipped. Later:  sibyl upgrade")
        return "declined"
    got = upgrade()
    if got == "missing-cli":
        say("Sibyl's own client is not installed. Install it, then run it:\n  pip install sibyl-memory-cli\n"
            "  sibyl upgrade")
        return "missing-cli"
    after = detect(consent_to_access=consent)
    if after.paid:
        say("Sibyl Pro: active.")
        return "bought"
    say("Sibyl Pro: not active yet. Finish Sibyl's checkout, then check with `sibyl status`.")
    return "not-completed"
