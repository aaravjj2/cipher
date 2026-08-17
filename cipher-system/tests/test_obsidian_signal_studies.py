from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from core.obsidian_signal_studies import mu_premarket_study, qqq_wave_study
from core.structural_fib_bars import Bar

NY = ZoneInfo("America/New_York")


def _bar(day: str, hhmm: str, o: float, h: float, l: float, c: float) -> Bar:
    return Bar(datetime.fromisoformat(f"{day}T{hhmm}:00").replace(tzinfo=NY), o, h, l, c, 1000)


def test_mu_break_is_confirmed_close_and_enters_next_five_minute_open():
    day = "2026-08-03"
    bars = [
        _bar(day, "09:25", 100, 101, 99, 100),
        _bar(day, "09:30", 100, 100.5, 99.8, 100),
        _bar(day, "09:35", 100, 101.4, 100, 101.2),
        _bar(day, "09:40", 101.3, 101.5, 101.1, 101.4),
        _bar(day, "09:55", 101.4, 101.8, 101.3, 101.7),
        _bar(day, "10:10", 101.7, 102.0, 101.6, 101.9),
        _bar(day, "10:40", 101.9, 102.2, 101.8, 102.1),
    ]
    report = mu_premarket_study(bars)
    bull = next(x for x in report["signal_records"] if x["setup_id"] == "bull_break")
    assert bull["signal_at"].endswith("09:35:00-04:00")
    assert bull["entry_at"].endswith("09:40:00-04:00")
    assert bull["entry_price"] == 101.3


def test_qqq_default_early_pivot_can_hit_half_range_on_confirmation_bar():
    day = "2026-08-03"
    bars = []
    start = datetime(2026, 8, 3, 9, 0, tzinfo=NY)
    for i in range(30):
        bars.append(Bar(start + timedelta(minutes=i), 100, 100.1, 99.9, 100, 1000))
    bars.extend([
        _bar(day, "09:30", 100.00, 100.08, 99.98, 100.02),
        _bar(day, "09:31", 100.02, 100.07, 99.97, 100.01),
        _bar(day, "09:32", 100.01, 100.06, 99.96, 99.99),
        _bar(day, "09:33", 99.99, 100.02, 99.90, 99.92),
        _bar(day, "09:34", 99.92, 100.05, 99.92, 100.02),
        _bar(day, "09:35", 100.02, 100.08, 100.00, 100.04),
        _bar(day, "09:36", 100.04, 100.10, 100.01, 100.06),
        _bar(day, "09:37", 100.06, 100.12, 100.03, 100.08),
    ])
    report = qqq_wave_study(bars)
    assert report["early"]["signals"] >= 1
    assert report["early"]["bull"] >= 1
    assert report["early"]["hit05"] >= 1
    assert 0 <= report["early"]["hit05_rate"] <= 1
