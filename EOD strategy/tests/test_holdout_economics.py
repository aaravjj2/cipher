"""Invariants for translating pooled per-trade percentages into a return.

The deep-dive report's headline was a *pooled* sum: every trade's percentage added together
across ten symbols. That is a reasonable way to measure a signal and a misleading way to state
a return -- on the crowned candidate it read +6.999% where equal-weight compounding gives
+0.735%, a 9.5x overstatement, and the annualized figure (+3.84%) is actually below the 4%
risk-free rate the capital would earn sitting still.

These tests pin the translation and the two things most likely to be quietly dropped from it:
the comparison against the risk-free rate, and the concentration check that shows the crowned
candidate turns negative without a single symbol.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (str(ROOT), str(ROOT / "core")):
    if path not in sys.path:
        sys.path.insert(0, path)

from core.holdout_economics import (  # noqa: E402
    RISK_FREE_PCT,
    _compound_pct,
    _holdout_economics,
)

START = date(2026, 6, 1)
END = date(2026, 8, 11)


def _trade(symbol: str, net: float, gross: float | None = None) -> dict:
    return {
        "symbol": symbol,
        "net_return_pct": net,
        "gross_return_pct": net if gross is None else gross,
    }


def test_compounding_is_not_summation():
    """Two +10% trades compound to +21%, not +20%. The report used to only ever sum."""
    assert _compound_pct([10.0, 10.0]) == 21.000000000000018
    assert round(_compound_pct([10.0, -10.0]), 10) == -1.0
    assert _compound_pct([]) == 0.0


def test_pooling_across_symbols_overstates_by_about_the_symbol_count():
    """Ten symbols each returning 1% is a 1% equal-weight return, not 10%."""
    trades = [_trade(f"S{i}", 1.0) for i in range(10)]
    econ = _holdout_economics(trades, START, END)
    assert econ["symbols"] == 10
    assert econ["pooled_sum_pct"] == 10.0
    assert round(econ["equal_weight_pct"], 6) == 1.0
    assert round(econ["overstatement_ratio"], 1) == 10.0


def test_the_risk_free_comparison_is_reported_in_both_directions():
    """A positive return that loses to cash must not be reported as a win."""
    # ~0.7% over 71 days annualizes to under 4%.
    weak = _holdout_economics([_trade("A", 0.7), _trade("B", 0.7)], START, END)
    assert weak["equal_weight_pct"] > 0, "the raw return is positive"
    assert weak["annualized_pct"] < RISK_FREE_PCT
    assert weak["beats_risk_free"] is False
    assert weak["excess_vs_risk_free_pp"] < 0

    strong = _holdout_economics([_trade("A", 5.0), _trade("B", 5.0)], START, END)
    assert strong["beats_risk_free"] is True
    assert strong["excess_vs_risk_free_pp"] > 0


def test_leave_one_out_exposes_a_single_carrying_symbol():
    """The crowned candidate's shape: one symbol supplies the entire positive result."""
    trades = [_trade("CARRIER", 9.0)] + [_trade(f"S{i}", -0.2) for i in range(4)]
    econ = _holdout_economics(trades, START, END)
    assert econ["equal_weight_pct"] > 0, "the full universe looks profitable"

    without_carrier = next(r for r in econ["leave_one_out"] if r["symbol"] == "CARRIER")
    assert without_carrier["equal_weight_pct"] < 0, "dropping it must flip the sign"
    assert without_carrier["clears_hurdle"] is False
    # Sorted worst-first, so the carrying symbol surfaces at the top of the rendered table
    # rather than having to be searched for.
    assert econ["leave_one_out"][0]["symbol"] == "CARRIER"


def test_slippage_drag_is_derived_from_the_trades_not_assumed():
    trades = [_trade("A", 0.8, gross=1.0), _trade("B", 0.5, gross=1.0)]
    econ = _holdout_economics(trades, START, END)
    assert round(econ["pre_cost_sum_pct"], 6) == 2.0
    assert round(econ["pooled_sum_pct"], 6) == 1.3
    assert round(econ["slippage_drag_pct"], 6) == 0.7
    assert round(econ["slippage_share_of_pre_cost_pct"], 1) == 35.0


def test_positive_symbol_count_uses_compounded_not_pooled_signs():
    """A symbol can have a positive trade sum and still compound negative."""
    trades = [_trade("MIXED", 50.0), _trade("MIXED", -40.0), _trade("UP", 1.0)]
    econ = _holdout_economics(trades, START, END)
    # 1.5 * 0.6 = 0.9 -> -10%, despite the trade percentages summing to +10.
    assert econ["per_symbol_compounded_pct"]["MIXED"] < 0
    assert econ["positive_symbols"] == 1


def test_declared_universe_keeps_zero_trade_symbols_at_zero_weighted_return():
    econ = _holdout_economics(
        [_trade("ACTIVE", 10.0)],
        START,
        END,
        universe=("ACTIVE", "IDLE"),
    )
    assert econ["symbols"] == 2
    assert econ["symbols_with_trades"] == 1
    assert econ["per_symbol_compounded_pct"]["IDLE"] == 0.0
    assert econ["equal_weight_pct"] == 5.0


def test_risk_free_hurdle_is_run_configurable():
    econ = _holdout_economics(
        [_trade("A", 0.7), _trade("B", 0.7)],
        START,
        END,
        risk_free_pct=3.0,
    )
    assert econ["risk_free_pct"] == 3.0
    assert econ["beats_risk_free"] is True
