from __future__ import annotations

from core.earnings_advanced_technique_lab import (
    cumulative_vwap,
    gap_vwap_continuation_signal,
    opening_range_15_signal,
    split_map,
)
from core.earnings_defined_risk_lab import EarningsEvent


class _Archive:
    def __init__(self, bars, close=100.0):
        self._bars = bars
        self._close = close

    def underlying_bars(self, symbol: str, day: str):
        return self._bars

    def daily_closes(self, symbol: str):
        return [("2026-01-01", self._close)]


def _bar(open_: float, high: float, low: float, close: float, volume: float = 100.0):
    return (open_, high, low, close, volume)


def test_split_map_uses_first_three_for_exploration():
    events = [
        EarningsEvent("AAPL", f"2025-0{index + 1}-01", f"2025-0{index + 1}-02")
        for index in range(6)
    ]
    mapping = split_map(events)
    assert [mapping[(event.symbol, event.report_date)] for event in events] == [
        "explore", "explore", "explore", "validate", "validate", "validate"
    ]


def test_cumulative_vwap_is_volume_weighted():
    bars = {
        570: _bar(99.0, 101.0, 99.0, 100.0, 100.0),
        571: _bar(109.0, 111.0, 109.0, 110.0, 300.0),
    }
    # Typical prices are 100 and 110, weighted 1:3.
    assert cumulative_vwap(bars, 571) == 107.5


def test_opening_range_15_requires_two_confirming_closes():
    bars = {minute: _bar(100.0, 101.0, 99.0, 100.0) for minute in range(570, 585)}
    bars[589] = _bar(101.0, 102.0, 100.5, 101.5)
    bars[594] = _bar(101.5, 102.5, 101.0, 102.0)
    event = EarningsEvent("TEST", "2026-01-01", "2026-01-02")
    assert opening_range_15_signal(_Archive(bars), event) == ("bullish", 595)


def test_gap_vwap_continuation_requires_gap_open_and_vwap_hold(monkeypatch):
    bars = {
        minute: _bar(102.0, 103.0, 101.5, 102.5, 100.0)
        for minute in range(570, 600)
    }
    event = EarningsEvent("TEST", "2026-01-01", "2026-01-02")
    archive = _Archive(bars, close=100.0)
    monkeypatch.setattr(
        "core.earnings_advanced_technique_lab.prior_close",
        lambda archive, symbol, day: 100.0,
    )
    assert gap_vwap_continuation_signal(archive, event, 600) == ("bullish", 600)
