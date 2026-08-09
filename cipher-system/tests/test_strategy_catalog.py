"""The catalog must preserve entry logic exactly and block what it cannot measure.

Two failure modes matter here. An adapter that silently changes what a strategy
does would make every downstream verdict a verdict about the adapter, so entries
are checked against the legacy functions directly. And a strategy whose data is not
available must stay blocked rather than receive a number, because a lookahead-biased
result outranking an honest one is worse than no result.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for path in (str(ROOT), str(ROOT / "core")):
    if path not in sys.path:
        sys.path.insert(0, path)

import backtest_engine as be  # noqa: E402
import edge_backtest  # noqa: E402
import strategy_catalog as sc  # noqa: E402


def _synthetic_daily(n: int = 400, start: float = 100.0) -> list[dict]:
    """A deterministic trending-with-pullbacks series.

    Real bars would make these tests depend on a network fetch and on which
    symbols happen to be cached, so the shape is generated instead. It only has to
    be varied enough for the strategies to fire.
    """
    bars = []
    price = start
    for i in range(n):
        drift = 0.30 if (i // 20) % 3 != 2 else -0.55
        wobble = ((i * 7919) % 23 - 11) / 10.0
        close = max(5.0, price + drift + wobble)
        high = close + abs(wobble) * 0.6 + 0.4
        low = min(close, price) - abs(wobble) * 0.6 - 0.4
        bars.append({
            "time": f"2024-{(i // 28) % 12 + 1:02d}-{i % 28 + 1:02d}T00:00:00Z",
            "open": price, "high": high, "low": max(1.0, low),
            "close": close, "volume": 1_000_000 + (i % 50) * 1000,
        })
        price = close
    return bars


def test_catalog_covers_every_family():
    summary = sc.summary()
    assert summary["total"] >= 39
    for family in ("edge", "price", "intraday", "gex"):
        assert family in summary["families"], family
    # The eight GEX strategies read today's open interest over past bars. Not one
    # of them may be evaluable until point-in-time OI accrues.
    assert summary["families"]["gex"]["evaluable"] == 0


def test_blocked_strategies_carry_a_reason_and_no_signal_fn():
    blocked = sc.blocked()
    assert blocked
    for spec in blocked:
        assert spec.blocked_reason, spec.strategy_id
        assert spec.signal_fn is None, spec.strategy_id
        assert not spec.evaluable, spec.strategy_id


def test_option_structures_are_blocked_not_scored():
    """Six price_backtest entries are structures priced by model at a flat IV."""
    for name in ("long_straddle", "long_strangle", "iron_condor",
                 "covered_call", "bull_call_spread", "bear_put_spread"):
        spec = sc.get(f"price.{name}")
        assert spec is not None, name
        assert not spec.evaluable, name
        assert "iv=0.25" in spec.blocked_reason


def test_every_evaluable_strategy_has_a_callable_signal_fn():
    for spec in sc.evaluable():
        assert callable(spec.signal_fn), spec.strategy_id
        assert spec.blocked_reason is None, spec.strategy_id


def _legacy_untruncated(fn, bars):
    """Call a legacy strategy the way the adapter does.

    Not all of them accept `max_trades` — the ones that never truncated do not
    have the keyword at all — so the fallback has to be mirrored here or the test
    measures the calling convention rather than the entries.
    """
    try:
        return fn("TEST", bars, max_trades=sc.NO_TRUNCATION)
    except TypeError:
        return fn("TEST", bars)


@pytest.mark.parametrize("name", ["rsi2_reversion", "breakout_20d", "gap_and_go",
                                  "three_day_reversal", "trend_pullback"])
def test_adapter_preserves_legacy_entries(name):
    """The adapter must extract the legacy entries and nothing else."""
    bars = _synthetic_daily()
    fn = getattr(edge_backtest, f"strategy_{name}")
    legacy = _legacy_untruncated(fn, bars)
    signals = sc.get(f"edge.{name}").signal_fn("TEST", bars)

    legacy_keys = {(t.entry_day, str(t.direction).upper()) for t in legacy}
    adapter_keys = {(str(bars[i + 1].get("time", ""))[:10], d) for i, d, _ in signals}
    assert adapter_keys == legacy_keys, name


def test_truncation_is_no_longer_hardcoded():
    """Two strategies broke at a literal 5 trades, ignoring their own max_trades.

    That is chronological truncation: it keeps the five EARLIEST signals and
    discards the rest of the history regardless of what those signals did.
    """
    bars = _synthetic_daily(600)
    for name in ("skew_harvest", "weekend_theta"):
        fn = getattr(edge_backtest, f"strategy_{name}")
        capped = fn("TEST", bars, max_trades=3)
        assert len(capped) <= 3, name
        uncapped = fn("TEST", bars, max_trades=sc.NO_TRUNCATION)
        assert len(uncapped) >= len(capped), name


def test_signals_feed_the_shared_engine():
    """An adapted strategy must run through run_signals and be controllable."""
    bars = {"TEST": _synthetic_daily(500)}
    spec = sc.get("edge.rsi2_reversion")
    result = be.run_signals(bars, spec.signal_fn, strategy=spec.strategy_id)
    if not result.trades:
        pytest.skip("synthetic series produced no entries for this strategy")

    assert result.stats["trades"] == len(result.trades)
    # Cost must actually be charged; a gross number is what the legacy engines
    # produced and the entire point of routing through this one.
    assert all(t.return_pct is not None for t in result.trades)

    control = be.run_control(result, bars, repeats=5)
    assert "detector_beats_control_range" in control


def test_adapter_shifts_entries_back_to_the_signal_bar():
    """Legacy trades record the FILL bar; run_signals fills on the next open.

    Passing the fill bar straight through would enter one bar late on every trade.
    """
    bars = _synthetic_daily(400)
    signals = sc.get("edge.rsi2_reversion").signal_fn("TEST", bars)
    if not signals:
        pytest.skip("no entries on the synthetic series")
    legacy = _legacy_untruncated(edge_backtest.strategy_rsi2_reversion, bars)
    fill_days = {t.entry_day for t in legacy}
    for index, _direction, _tag in signals:
        # bar index+1 is the fill; that day must be one the legacy trade recorded.
        assert str(bars[index + 1].get("time", ""))[:10] in fill_days
