"""Refuse to shadow-trade a strategy that has not cleared the fast gate.

The paper executor can fix a decision at a moment in time and cannot revise it,
which is the one property a backtest can never have. That makes it valuable — and
it makes what it is pointed at consequential, because a forward test of a strategy
with no established edge produces a forward record of noise, which then looks like
evidence because it was collected prospectively.

So the queue is gated on the registry. A strategy may be shadowed only once
`core/strategy_evaluation.py` has recorded that it beat a matched random-entry
control and held up on a locked holdout, which is what `FAST_BACKTESTED` means in
`core/research_platform/promotion.py`.

As of 2026-08-08 nothing clears it. `edge.rsi2_reversion` passed on the ten symbols
it was selected on and failed on ten it was not, so the catalog currently contains
zero strategies with an out-of-sample result. The executor therefore starts, reports
healthy, and processes nothing — and that empty queue is the honest state, not a
malfunction. It should be displayed as such rather than filled with signals from a
detector that has already failed its own tests.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = ROOT / "data" / "governance" / "research_registry.sqlite"

# States at or above which a strategy has demonstrated something forward testing
# can build on. Mirrors core/research_platform/promotion.py's ladder.
ELIGIBLE_STATES = (
    "FAST_BACKTESTED",
    "WALK_FORWARD_PASSED",
    "PROSPECTIVE_SHADOW",
    "PAPER_ELIGIBLE",
    "PAPER_TRADING",
)


def eligible_strategies(registry_path: Path = REGISTRY_PATH) -> set[str]:
    """Strategy ids that have reached a state worth forward testing.

    A missing or unreadable registry yields an empty set rather than an
    exception: failing open here would mean shadow-trading everything, which is
    precisely the outcome the gate exists to prevent.
    """
    if not registry_path.exists():
        return set()
    try:
        conn = sqlite3.connect(f"file:{registry_path}?mode=ro", uri=True)
    except sqlite3.Error:
        return set()
    try:
        # The canonical registry stores the materialized promotion state on
        # strategies.current_state. Older paper-runtime registries stored an event
        # stream with a `state` column, while current registries call it `to_state`.
        # Support all three shapes and fail closed on anything else.
        strategy_columns = {
            str(row[1]) for row in conn.execute("pragma table_info(strategies)")
        }
        event_columns = {
            str(row[1]) for row in conn.execute("pragma table_info(promotion_events)")
        }
        if {"strategy_id", "current_state"}.issubset(strategy_columns):
            rows = conn.execute(
                "select strategy_id, current_state from strategies"
            ).fetchall()
        elif {"strategy_id", "to_state"}.issubset(event_columns):
            rows = conn.execute(
                "select strategy_id, to_state from promotion_events order by decided_at"
            ).fetchall()
        elif {"strategy_id", "state"}.issubset(event_columns):
            rows = conn.execute(
                "select strategy_id, state from promotion_events"
            ).fetchall()
        else:
            return set()
    except sqlite3.Error:
        return set()
    finally:
        conn.close()

    latest: dict[str, str] = {}
    for strategy_id, state in rows:
        latest[strategy_id] = state
    return {sid for sid, state in latest.items() if state in ELIGIBLE_STATES}


def gate_status(registry_path: Path = REGISTRY_PATH) -> dict:
    """What the executor would accept, and why it is currently nothing."""
    eligible = eligible_strategies(registry_path)
    return {
        "eligible_strategies": sorted(eligible),
        "eligible_count": len(eligible),
        "minimum_state": ELIGIBLE_STATES[0],
        "queue_empty_is_expected": not eligible,
        "reason": (
            "No strategy has cleared the fast gate, so there is nothing eligible to "
            "shadow. The executor running with an empty queue is the correct state: "
            "forward-testing a strategy with no established edge would produce a "
            "prospective record of noise, which is harder to discard than a backtest "
            "because it was collected forward."
        ) if not eligible else (
            f"{len(eligible)} strategy(ies) have cleared the fast gate and may be "
            f"shadowed."
        ),
    }


def is_eligible(strategy_id: str, registry_path: Path = REGISTRY_PATH) -> bool:
    return strategy_id in eligible_strategies(registry_path)
