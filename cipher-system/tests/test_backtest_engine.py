"""Guards on the backtest engine's honesty properties.

These are not accuracy tests — there is no ground truth to compare against. They
pin the assumptions that stop the backtest inventing edge: next-bar-open fills,
stop-before-target, costs charged both sides, and a random-entry control that is
matched to the detector rather than run under easier rules.
"""
import math
import random

import pytest

from core import backtest_engine as be


def _bars(closes, *, spread=1.0, volume=1_000_000):
    """Build OHLCV bars with a fixed high/low spread around each close."""
    out = []
    for i, c in enumerate(closes):
        out.append({
            "time": f"2026-01-01T{i:04d}",
            "open": c, "high": c + spread, "low": c - spread,
            "close": c, "volume": volume,
        })
    return out


def _flat_atr(n, value=1.0):
    return [value] * n


def test_entry_fills_on_next_bar_open_not_signal_close():
    # Signal on bar 5; bar 6 opens far from bar 5's close. A backtest that filled
    # at the signal bar's close would report the gap as free profit.
    bars = _bars([100.0] * 6 + [110.0] + [110.0] * 30)
    trade, _ = be._simulate(
        bars, _flat_atr(len(bars)), 5, "TEST", "FLOOR BOUNCE", "LONG",
        stop_atr=1.0, target_atr=1.5, max_hold_bars=10, cost_bps=0.0,
    )
    assert trade.entry_price == 110.0, "must fill on bar 6's open, not bar 5's close"
    assert trade.entry_time == bars[6]["time"]


def test_stop_assumed_before_target_when_both_touched():
    # Bar 6 spans both levels: entry 100, stop 99, target 101.5, range 97..103.
    bars = _bars([100.0] * 6 + [100.0] * 10, spread=3.0)
    trade, _ = be._simulate(
        bars, _flat_atr(len(bars)), 5, "TEST", "FLOOR BOUNCE", "LONG",
        stop_atr=1.0, target_atr=1.5, max_hold_bars=10, cost_bps=0.0,
    )
    assert trade.exit_reason == "stop", "ambiguous intrabar must resolve pessimistically"


def test_cost_charged_on_both_sides():
    bars = _bars([100.0] * 6 + [100.0] * 10, spread=3.0)
    kw = dict(stop_atr=1.0, target_atr=1.5, max_hold_bars=10)
    free, _ = be._simulate(bars, _flat_atr(len(bars)), 5, "T", "S", "LONG", cost_bps=0.0, **kw)
    paid, _ = be._simulate(bars, _flat_atr(len(bars)), 5, "T", "S", "LONG", cost_bps=5.0, **kw)
    # 5 bps per side = 0.10 percentage points round trip.
    assert math.isclose(free.return_pct - paid.return_pct, 0.10, abs_tol=1e-6)


def test_short_direction_pnl_sign_is_inverted():
    # Entry fills at bar 6's open (100); the drop to 95 happens after that.
    bars = _bars([100.0] * 7 + [95.0] * 10, spread=0.1)
    long_t, _ = be._simulate(
        bars, _flat_atr(len(bars)), 5, "T", "S", "LONG",
        stop_atr=50.0, target_atr=50.0, max_hold_bars=5, cost_bps=0.0,
    )
    short_t, _ = be._simulate(
        bars, _flat_atr(len(bars)), 5, "T", "S", "SHORT",
        stop_atr=50.0, target_atr=50.0, max_hold_bars=5, cost_bps=0.0,
    )
    assert long_t.return_pct < 0 < short_t.return_pct


def test_control_matches_detector_trade_count_and_direction():
    """The control must be matched, or it is not a control.

    If the random arm traded a different count, different symbols, or a different
    long/short mix, any gap between it and the detector would be explained by the
    mismatch rather than by entry timing.
    """
    bars_by_symbol = {"AAA": _bars([100 + math.sin(i / 7) * 5 for i in range(400)])}
    ref = be.BacktestResult(strategy="t", symbols=["AAA"])
    ref.trades = [
        be.Trade(symbol="AAA", setup="FLOOR BOUNCE", direction="LONG",
                 entry_time="x", entry_price=100.0)
        for _ in range(9)
    ] + [
        be.Trade(symbol="AAA", setup="CEILING REJECTION", direction="SHORT",
                 entry_time="x", entry_price=100.0)
        for _ in range(4)
    ]
    ref.stats = be.summarize(ref.trades)

    captured = []
    real_simulate = be._simulate

    def spy(bars, atr, i, symbol, setup, direction, **kw):
        captured.append((symbol, direction))
        return real_simulate(bars, atr, i, symbol, setup, direction, **kw)

    be._simulate = spy
    try:
        be.run_control(ref, bars_by_symbol, repeats=3, max_hold_bars=10)
    finally:
        be._simulate = real_simulate

    assert len(captured) == 13 * 3
    assert captured.count(("AAA", "LONG")) == 9 * 3
    assert captured.count(("AAA", "SHORT")) == 4 * 3


def test_control_entries_respect_detector_warmup():
    """Random entries must be drawn only where the detector could also have fired.

    Letting the control enter inside the 100-bar warmup would give it bars the
    detector is structurally barred from using, quietly biasing the comparison.
    """
    n = 400
    bars_by_symbol = {"AAA": _bars([100 + (i % 11) for i in range(n)])}
    ref = be.BacktestResult(strategy="t", symbols=["AAA"])
    ref.trades = [be.Trade(symbol="AAA", setup="S", direction="LONG",
                           entry_time="x", entry_price=100.0) for _ in range(50)]
    ref.stats = be.summarize(ref.trades)

    seen = []
    real_simulate = be._simulate

    def spy(bars, atr, i, *a, **kw):
        seen.append(i)
        return real_simulate(bars, atr, i, *a, **kw)

    be._simulate = spy
    try:
        be.run_control(ref, bars_by_symbol, repeats=5, max_hold_bars=24)
    finally:
        be._simulate = real_simulate

    assert seen, "control drew no entries"
    assert min(seen) >= 120
    assert max(seen) <= n - 24 - 2


def test_control_is_deterministic_for_a_given_seed():
    bars_by_symbol = {"AAA": _bars([100 + (i % 13) for i in range(400)])}
    ref = be.BacktestResult(strategy="t", symbols=["AAA"])
    ref.trades = [be.Trade(symbol="AAA", setup="S", direction="LONG",
                           entry_time="x", entry_price=100.0) for _ in range(20)]
    ref.stats = be.summarize(ref.trades)
    a = be.run_control(ref, bars_by_symbol, seed=42, repeats=4)
    b = be.run_control(ref, bars_by_symbol, seed=42, repeats=4)
    assert a["control"] == b["control"]


def test_walk_forward_warns_instead_of_silently_returning_nothing():
    """Too little history must produce a warning, not an empty report.

    An empty report reads exactly like "the strategy found no trades", which is a
    very different conclusion from "there was not enough data to look".
    """
    bars_by_symbol = {"AAA": _bars([100.0 + i * 0.1 for i in range(200)])}
    report = be.walk_forward(bars_by_symbol, folds=3, holdout_frac=0.25)
    assert report["warnings"], "short history must warn"
    assert any("insufficient history" in w for w in report["warnings"])


def test_walk_forward_holdout_does_not_overlap_folds():
    bars_by_symbol = {"AAA": _bars([100.0 + math.sin(i / 9) for i in range(2000)])}
    report = be.walk_forward(bars_by_symbol, folds=3, holdout_frac=0.25)
    assert report["folds"], "expected folds on 2000 bars"
    # Folds must all sit inside the first 75%; the holdout owns the rest.
    assert max(f["range"][1] for f in report["folds"]) <= 0.75 + 1e-9


def test_summarize_reports_median_alongside_mean():
    """A stop/target asymmetry makes mean and median diverge, and reporting only
    the mean hides that most trades lose."""
    trades = [be.Trade(symbol="A", setup="S", direction="LONG", entry_time="x",
                       entry_price=1.0, return_pct=r)
              for r in [-1.0] * 9 + [20.0]]
    s = be.summarize(trades)
    assert s["avg_return_pct"] > 0
    assert s["median_return_pct"] < 0
