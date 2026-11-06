"""Credit ledger tests.

The ledger exists so a metered source cannot consume its own demo budget. These
tests assert the three ways it refuses, and that the count survives a restart.
"""

from __future__ import annotations

import pytest

from socmint.settings import CreditPolicy
from socmint.spend import CreditLedger
from socmint.store import Store


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "s.db") as s:
        yield s


def test_a_fresh_ledger_has_spent_nothing(store) -> None:
    ledger = CreditLedger(store, CreditPolicy())
    assert ledger.spent == 0
    assert ledger.remaining == 500
    assert ledger.available_for_testing == 300


def test_spending_is_refused_until_explicitly_enabled(store) -> None:
    """The default policy must not spend, even with plenty of allowance."""
    ledger = CreditLedger(store, CreditPolicy())
    decision = ledger.check(2)
    assert decision.allowed is False
    assert "disabled" in decision.reason


def test_spend_is_allowed_once_enabled(store) -> None:
    ledger = CreditLedger(store, CreditPolicy(allow_spend=True))
    assert ledger.check(2).allowed is True


def test_the_demo_reserve_cannot_be_crossed(store) -> None:
    """300 is testable; the 201st credit of testing must be refused."""
    ledger = CreditLedger(store, CreditPolicy(allow_spend=True, max_per_run=1000))
    ledger.charge(300)
    assert ledger.available_for_testing == 0
    decision = ledger.check(2)
    assert decision.allowed is False
    assert "demo reserve" in decision.reason


def test_the_per_run_ceiling_is_enforced(store) -> None:
    ledger = CreditLedger(store, CreditPolicy(allow_spend=True, max_per_run=10))
    assert ledger.check(8, run_spent=4).allowed is False
    assert ledger.check(2, run_spent=4).allowed is True


def test_a_refund_restores_the_balance(store) -> None:
    ledger = CreditLedger(store, CreditPolicy(allow_spend=True))
    ledger.charge(20, note="call started")
    assert ledger.spent == 20
    ledger.refund(20, note="call failed")
    assert ledger.spent == 0


def test_the_ledger_survives_a_restart(tmp_path) -> None:
    """Spend must persist: an in-memory counter would reset on every run."""
    path = tmp_path / "s.db"
    with Store(path) as first:
        CreditLedger(first, CreditPolicy(allow_spend=True)).charge(60)
    with Store(path) as second:
        assert CreditLedger(second, CreditPolicy()).spent == 60


def test_history_is_newest_first(store) -> None:
    ledger = CreditLedger(store, CreditPolicy(allow_spend=True))
    ledger.charge(2, note="first")
    ledger.charge(4, note="second")
    notes = [row["note"] for row in ledger.history()]
    assert notes[0] == "second"
    assert notes[1] == "first"


def test_summary_reports_the_reserve(store) -> None:
    summary = CreditLedger(store, CreditPolicy()).summary()
    assert summary["demo_reserve"] == 200
    assert summary["available_for_testing"] == 300
    assert summary["allow_spend"] is False


def test_zero_units_is_not_a_spend(store) -> None:
    assert CreditLedger(store, CreditPolicy(allow_spend=True)).check(0).allowed is False


# ---------------------------------------------------------------------------
# Cooldown
# ---------------------------------------------------------------------------


def test_a_fresh_ledger_has_no_cooldown(store) -> None:
    assert CreditLedger(store, CreditPolicy()).cooldown_remaining() == 0.0


def test_a_charge_starts_the_cooldown(store) -> None:
    ledger = CreditLedger(store, CreditPolicy(allow_spend=True, min_seconds_between_calls=30))
    ledger.charge(2, note="x search")
    assert ledger.cooldown_remaining() > 25


def test_the_cooldown_expires(store) -> None:
    from datetime import UTC, datetime, timedelta

    ledger = CreditLedger(store, CreditPolicy(allow_spend=True, min_seconds_between_calls=30))
    ledger.charge(2, note="x search")
    later = datetime.now(UTC) + timedelta(seconds=31)
    assert ledger.cooldown_remaining(now=later) == 0.0


def test_a_refund_does_not_restart_the_cooldown(store) -> None:
    """The call still hit the API, so the gap is measured from the charge."""
    ledger = CreditLedger(store, CreditPolicy(allow_spend=True, min_seconds_between_calls=30))
    ledger.charge(2, note="x search")
    ledger.refund(2, note="failed")
    assert ledger.cooldown_remaining() > 25


def test_a_zero_cooldown_disables_the_gate(store) -> None:
    ledger = CreditLedger(store, CreditPolicy(allow_spend=True, min_seconds_between_calls=0))
    ledger.charge(2, note="x search")
    assert ledger.cooldown_remaining() == 0.0
