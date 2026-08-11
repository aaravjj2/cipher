"""Offline invariants for the Pine-v6 Obsidian YTD runner."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.obsidian_eod import BarState  # noqa: E402
from scripts.run_obsidian_pine_ytd import (  # noqa: E402
    _recent_release,
    _rth,
    _trade_for_signal,
)


def _state(events: list[str]) -> BarState:
    return BarState(
        index=0,
        time=None,
        hist=0.0,
        run_sign=0.0,
        run_peak=0.0,
        bars_from_peak=0,
        collapse_pct=0.0,
        significant=False,
        coiling=False,
        in_window=True,
        hot=False,
        events=events,
    )


def _day_bars() -> list[dict]:
    rows = []
    for minute in range(390):
        total = 9 * 60 + 30 + minute
        hour, minute_of_hour = divmod(total, 60)
        timestamp = f"2026-01-02T{hour:02d}:{minute_of_hour:02d}:00-05:00"
        rows.append({
            "time": timestamp,
            "timestamp": timestamp,
            "open": 100.0,
            "high": 100.1,
            "low": 99.9,
            "close": 100.0,
            "volume": 1000,
        })
    return rows


def test_rls_relation_does_not_confuse_down_release_with_up_release():
    states = [_state(["Momentum push (down)"]), _state([]), _state([])]
    assert not _recent_release(states, 2, "LONG", 10)
    states = [_state(["Momentum push"]), _state([]), _state([])]
    assert _recent_release(states, 2, "LONG", 10)


def test_rth_session_has_390_timestamped_one_minute_bars():
    rows = _day_bars() + [{
        "timestamp": "2026-01-02T08:00:00-05:00",
        "open": 1.0,
        "high": 1.0,
        "low": 1.0,
        "close": 1.0,
        "volume": 1,
    }]
    assert len(_rth(rows)) == 390


def test_trade_uses_delayed_close_and_1559_close():
    bars = _day_bars()
    trade = _trade_for_signal(
        "TEST", bars, 300, "LONG", "CLPS UP", entry_delay=2, tick_size=0.01
    )
    assert trade is not None
    assert trade.entry_time.endswith("14:32:00-05:00")
    assert trade.exit_time.endswith("15:59:00-05:00")
    assert trade.entry_price == trade.entry_price_before_slippage + 0.01
    assert trade.exit_price == trade.exit_price_before_slippage - 0.01
