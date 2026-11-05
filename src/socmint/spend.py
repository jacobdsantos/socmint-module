"""Spend control for metered APIs.

Every other source in this module is free, so an unlimited test run costs
nothing. Xpoz is metered and its free tier is a **one-time** grant: credits
spent while testing are gone, and the demo is the thing that needs them.

This module exists so that cannot happen by accident:

* spending is **off** unless ``allow_spend`` is set explicitly;
* a ``demo_reserve`` is held back that testing cannot cross, no matter how many
  runs are made;
* each run has its own ceiling, so one runaway query cannot consume the rest;
* the ledger is append-only in SQLite, so the count survives restarts and cannot
  be quietly reset by re-running.

The ledger charges **before** the call and refunds on failure. Charging after a
successful call would under-count if the process died mid-request, and
under-counting is the direction that overspends.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .settings import CreditPolicy
from .store import Store

logger = logging.getLogger(__name__)

XPOZ = "xpoz"


@dataclass(frozen=True)
class CreditDecision:
    """Outcome of a spend check, with the reason a human can act on."""

    allowed: bool
    reason: str
    units: int = 0


class CreditLedger:
    """Spend policy over an append-only SQLite ledger."""

    def __init__(self, store: Store, policy: CreditPolicy, provider: str = XPOZ) -> None:
        self.store = store
        self.policy = policy
        self.provider = provider

    # -- state ------------------------------------------------------------
    @property
    def spent(self) -> int:
        return self.store.spend_total(self.provider)

    @property
    def remaining(self) -> int:
        """Credits left on the account, ignoring the reserve."""
        return max(0, self.policy.allowance - self.spent)

    @property
    def available_for_testing(self) -> int:
        """Credits the module may still spend without touching the reserve."""
        return max(0, self.remaining - self.policy.demo_reserve)

    def history(self, limit: int = 50) -> list[dict]:
        return self.store.spend_history(self.provider, limit)

    # -- decisions --------------------------------------------------------
    def check(self, units: int, *, run_spent: int = 0) -> CreditDecision:
        """Decide whether ``units`` more credits may be spent right now."""
        if units <= 0:
            return CreditDecision(False, "nothing to spend", 0)

        if not self.policy.allow_spend:
            return CreditDecision(
                False,
                "spending is disabled (allow_spend=False); "
                "pass --allow-spend to permit it",
                0,
            )

        if run_spent + units > self.policy.max_per_run:
            return CreditDecision(
                False,
                f"run ceiling reached ({run_spent} of {self.policy.max_per_run} "
                f"credits used this run; {units} more requested)",
                0,
            )

        if self.spent + units > self.policy.testable:
            return CreditDecision(
                False,
                f"would cross the demo reserve: {self.spent} spent, "
                f"{self.available_for_testing} available for testing, "
                f"{self.policy.demo_reserve} reserved",
                0,
            )

        return CreditDecision(True, "within policy", units)

    # -- mutations --------------------------------------------------------
    def charge(self, units: int, *, run_id: str | None = None, note: str | None = None) -> None:
        """Record a spend before the call is made."""
        self.store.record_spend(self.provider, units, run_id=run_id, note=note)
        logger.info("charged %s credit(s) to %s (%s)", units, self.provider, note or "-")

    def refund(self, units: int, *, run_id: str | None = None, note: str | None = None) -> None:
        """Reverse a charge whose call failed. Recorded, never deleted."""
        if units <= 0:
            return
        self.store.record_spend(
            self.provider, -units, run_id=run_id, note=note or "refund: call failed"
        )

    def summary(self) -> dict:
        return {
            "provider": self.provider,
            "allowance": self.policy.allowance,
            "spent": self.spent,
            "remaining": self.remaining,
            "demo_reserve": self.policy.demo_reserve,
            "available_for_testing": self.available_for_testing,
            "max_per_run": self.policy.max_per_run,
            "allow_spend": self.policy.allow_spend,
        }


__all__ = ["XPOZ", "CreditDecision", "CreditLedger"]
