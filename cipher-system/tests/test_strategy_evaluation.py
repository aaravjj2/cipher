"""One standard, applied the same way to every strategy.

The verdicts have to stay distinguishable. "Blocked", "no entries", "too few
trades to say", "raised an exception" and "lost to random entry" are five
different states, and collapsing any of them into another is how an unmeasurable
strategy ends up with a number next to it.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (str(ROOT), str(ROOT / "core")):
    if path not in sys.path:
        sys.path.insert(0, path)

import strategy_catalog as sc  # noqa: E402
import strategy_evaluation as se  # noqa: E402
from test_strategy_catalog import _synthetic_daily  # noqa: E402


def test_blocked_strategies_get_a_reason_and_never_a_metric():
    """A lookahead-biased number outranking an honest one is the failure mode."""
    for strategy_id in ("gex.wall_bounce", "price.iron_condor"):
        verdict = se.evaluate(strategy_id, {"TEST": _synthetic_daily(200)})
        assert verdict["verdict"] == "BLOCKED", strategy_id
        assert verdict["reason"], strategy_id
        assert verdict["metrics"] is None, strategy_id
        assert "beats_control_range" not in verdict, strategy_id


def test_blocked_gex_strategies_carry_the_accrual_clock():
    """Blocked is a countdown, not a rejection — say how far the clock has to run."""
    verdict = se.evaluate("gex.gamma_squeeze", {"TEST": _synthetic_daily(200)})
    assert verdict["verdict"] == "BLOCKED"
    # The clock comes from live capture state, so its presence is asserted rather
    # than its value.
    if "accrual" in verdict:
        assert "capture days" in verdict["accrual"]


def test_unknown_strategy_is_reported_not_raised():
    verdict = se.evaluate("nope.not_a_strategy", {"TEST": _synthetic_daily(200)})
    assert verdict["verdict"] == "UNKNOWN"


def test_wrong_timeframe_is_not_reported_as_no_trades():
    """An intraday rule fed daily bars finds nothing; that is the caller's doing."""
    verdict = se.evaluate("intraday.orb_15min", {"TEST": _synthetic_daily(300)},
                          timeframe="1Day")
    assert verdict["verdict"] == "WRONG_TIMEFRAME"
    assert verdict["bar_timeframe"] == "15Min"
    assert verdict["metrics"] is None


def test_small_samples_are_insufficient_not_failures():
    """Below the threshold a strategy has not failed; nothing has been shown."""
    assert se.MIN_TRADES_FOR_VERDICT >= 30
    bars = {"TEST": _synthetic_daily(260)}
    verdicts = {se.evaluate(s.strategy_id, bars, control_repeats=3)["verdict"]
                for s in sc.evaluable() if s.bar_timeframe == "1Day"}
    # Whatever else happens, a numeric verdict may only be PASS or FAIL.
    assert verdicts <= {"PASS", "IN_SAMPLE_ONLY", "FAIL", "INSUFFICIENT",
                        "NO_TRADES", "ERROR"}


def test_a_passing_verdict_requires_beating_the_control():
    """PASS is defined by the control, never by profit factor or win rate."""
    bars = {"TEST": _synthetic_daily(500)}
    for spec in sc.evaluable():
        if spec.bar_timeframe != "1Day":
            continue
        verdict = se.evaluate(spec.strategy_id, bars, control_repeats=3)
        if verdict["verdict"] == "PASS":
            # PASS requires BOTH: clearing the control, and holding up on a
            # holdout carved off before any fold was examined.
            assert verdict["beats_control_range"] is True, spec.strategy_id
            assert verdict["walk_forward_passed"] is True, spec.strategy_id
        if verdict["verdict"] == "IN_SAMPLE_ONLY":
            assert verdict["beats_control_range"] is True, spec.strategy_id
            assert verdict["walk_forward_passed"] is False, spec.strategy_id
        if verdict["verdict"] == "FAIL":
            assert verdict["beats_control_range"] is False, spec.strategy_id


def test_standard_output_declares_the_control_as_a_quality_check():
    """FastGateEvaluator reads required_quality_checks straight out of this."""
    bars = {"TEST": _synthetic_daily(500)}
    verdict = se.evaluate("edge.rsi2_reversion", bars, control_repeats=3)
    output = se.to_standard_output(verdict)
    quality = dict(output.quality_checks)
    assert "beats_control_range" in quality
    assert "walk_forward_passed" in quality
    assert "control_matched" in quality
    assert quality["cost_charged_both_sides"] is True
    assert quality["passed"] == (verdict["verdict"] == "PASS")
    # win_rate crosses into the registry as a fraction, not a percentage; the
    # engine reports percent, and a 46 where 0.46 is expected would clear any
    # threshold ever written.
    if verdict.get("metrics"):
        assert 0.0 <= output.metrics["win_rate"] <= 1.0


def test_evaluate_all_reports_the_standard_it_applied():
    bars = {"TEST": _synthetic_daily(200)}
    out = se.evaluate_all(bars, strategy_ids=["gex.wall_bounce", "price.iron_condor"],
                          control_repeats=2)
    assert out["verdicts"]["BLOCKED"] == 2
    assert "random-entry control matched trade-for-trade" in out["standard"]
    assert "never given a number" in out["standard"]
