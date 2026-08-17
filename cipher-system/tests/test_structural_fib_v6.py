from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from core import structural_fib_v6 as v6
from core.structural_fib_bars import Bar

NY = ZoneInfo("America/New_York")


def bar(hhmm: str, o: float, h: float, l: float, c: float, day: str = "2026-03-02") -> Bar:
    hh, mm = map(int, hhmm.split(":"))
    y, mo, d = map(int, day.split("-"))
    return Bar(datetime(y, mo, d, hh, mm, tzinfo=NY), o, h, l, c, 1000)


def test_fixed_first_anchor_and_next_bar_open_fill():
    reg = [
        bar("09:30", 100, 100.2, 100.0, 100.1),
        bar("09:35", 100.1, 100.3, 99.0, 100.2),  # later low must not trail anchor
        bar("09:40", 100.2, 100.75, 100.2, 100.7),
        bar("09:45", 100.9, 101.1, 100.8, 101.0),
    ]
    signals, trades = v6.evaluate_day("T", date(2026, 3, 2), reg, 101, 100, 0, 1.0)
    assert signals[0].setup_id == "C05"
    assert signals[0].stop == pytest.approx(100.0)
    assert trades[0].entry_price == pytest.approx(100.9)
    assert trades[0].entry_time == "09:45"


def test_oversized_first_candle_uses_only_second_wick():
    reg = [
        bar("09:30", 100, 101, 99, 100),  # 2% > default 0.75%
        bar("09:35", 100, 100.2, 99.8, 100),
        bar("09:40", 100, 100.27, 100.0, 100.24),
        bar("09:45", 100.5, 100.8, 100.4, 100.7),
    ]
    signals, _ = v6.evaluate_day("T", date(2026, 3, 2), reg, 100.6, 100.0, 0, 0.6)
    assert signals
    assert signals[0].anchor_source == "2nd 5m wick"
    assert signals[0].stop == pytest.approx(99.8)


def test_continuation_requires_real_half_cross_and_expires_after_five_bars():
    reg = [bar("09:30", 100, 100.1, 99.9, 100)]
    reg.append(bar("09:35", 100, 100.7, 100, 100.6))  # actual 0.5 cross
    base = datetime(2026, 3, 2, 9, 40, tzinfo=NY)
    for i in range(6):
        reg.append(Bar(base + timedelta(minutes=5 * i), 100.6, 100.8, 100.55, 100.65, 1000))
    reg.append(bar("10:10", 100.7, 101.3, 100.7, 101.2))
    signals, _ = v6.evaluate_day("T", date(2026, 3, 2), reg, 101, 100, 0, 1.0)
    assert "C05" in [x.setup_id for x in signals]
    assert "C1" not in [x.setup_id for x in signals]


def test_weak_body_cross_is_not_an_entry():
    reg = [
        bar("09:30", 100, 100.1, 99.9, 100),
        bar("09:35", 100.49, 101.2, 99.9, 100.55),  # tiny body despite close over 0.5
        bar("09:40", 100.5, 100.6, 100.4, 100.5),
    ]
    signals, trades = v6.evaluate_day("T", date(2026, 3, 2), reg, 101, 100, 0, 1.0)
    assert signals == [] and trades == []


def test_confirmed_close_stop_fills_at_next_open_not_at_stop_level():
    reg = [
        bar("09:30", 100, 100.2, 100.0, 100.1),
        bar("09:35", 100.1, 100.8, 100.1, 100.7),
        bar("09:40", 100.8, 100.9, 99.8, 99.9),  # entry then closes through c0
        bar("09:45", 99.5, 99.7, 99.4, 99.6),
    ]
    _, trades = v6.evaluate_day("T", date(2026, 3, 2), reg, 101, 100, 0, 1.0)
    assert trades[0].exit_reason == "fib_invalidation"
    assert trades[0].exit_price == pytest.approx(99.5)


def test_pine_else_if_priority_allows_only_one_signal_per_bar():
    # Directly pins the output invariant even when levels are unusually compressed.
    reg = [
        bar("09:30", 100, 100.01, 99.99, 100),
        bar("09:35", 100, 100.3, 100, 100.25),
        bar("09:40", 100.3, 100.4, 100.2, 100.35),
    ]
    signals, _ = v6.evaluate_day("T", date(2026, 3, 2), reg, 100.2, 100, 0, 0.2)
    assert len([s for s in signals if s.signal_time == "09:35"]) <= 1
