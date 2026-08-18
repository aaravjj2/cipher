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
from scripts.experiment_obsidian_eod import (  # noqa: E402
    _complete_session_rows,
    _fixed_time_control,
    _protocol_fingerprint,
    _run_family,
    _write_checkpoint,
    resample_5m,
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
        # This synthetic state stands for a bar where signalling is permitted; under EOD Focus
        # that is the same thing as being inside the arming window.
        signal_gate=True,
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


def test_incomplete_sessions_are_excluded_from_experiment_data():
    rows = _day_bars()
    incomplete = [dict(row, timestamp=row["timestamp"].replace("2026-01-02", "2026-01-03"), time=row["time"].replace("2026-01-02", "2026-01-03")) for row in rows[:-1]]
    filtered, summary = _complete_session_rows(rows + incomplete, 390)
    assert filtered == rows
    assert summary["sessions_seen"] == 2
    assert summary["complete_sessions"] == 1
    assert summary["excluded_incomplete_sessions"] == 1


def test_5m_resample_is_session_aligned():
    bars = _day_bars()
    result = resample_5m(bars)
    assert len(result) == 78
    assert result[0]["time"].endswith("09:30:00-05:00")
    assert result[-1]["time"].endswith("15:55:00-05:00")
    assert len(resample_5m(bars[:-1])) == 0


def test_fixed_control_summary_covers_all_control_bars():
    first = _day_bars()
    second = [dict(row, timestamp=row["timestamp"].replace("2026-01-02", "2026-01-05"), time=row["time"].replace("2026-01-02", "2026-01-05")) for row in first]
    result = _fixed_time_control({"TEST": first + second}, start=__import__("datetime").date(2026, 1, 1), end=__import__("datetime").date(2026, 1, 31))
    assert result["trades"] == 2
    assert result["first_bar"].startswith("2026-01-02")
    assert result["last_bar"].startswith("2026-01-05")


def test_checkpoint_is_atomic_and_protocol_fingerprint_is_stable(tmp_path):
    target = tmp_path / "checkpoint.json"
    fingerprint = _protocol_fingerprint({"grid": [1, 2], "holdout": "locked"})
    _write_checkpoint(target, {"schema_version": 2, "protocol_fingerprint": fingerprint})
    assert target.exists()
    assert not list(tmp_path.glob("*.tmp"))
    assert _protocol_fingerprint({"holdout": "locked", "grid": [1, 2]}) == fingerprint


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


def test_short_return_uses_entry_not_exit_as_denominator():
    bars = _day_bars()
    # signal at 14:30, delayed entry at 14:31 = 100, EOD cover = 90.
    bars[301]["close"] = 100.0
    bars[-1]["close"] = 90.0
    trade = _trade_for_signal(
        "TEST", bars, 300, "SHORT", "CLPS DOWN", entry_delay=1, tick_size=0.0
    )
    assert trade is not None
    assert trade.gross_return_pct == 10.0
    assert trade.net_return_pct == 10.0
