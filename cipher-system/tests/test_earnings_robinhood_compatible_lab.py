from __future__ import annotations

from core.earnings_defined_risk_lab import EarningsEvent
from core.earnings_robinhood_compatible_lab import (
    ResultRow,
    closing_gap_reversal_signal,
    gap_fade_signal,
    summarize,
)


class _Archive:
    def __init__(self, bars, closes, sessions=None):
        self._bars = bars
        self._closes = closes
        self._sessions = sessions or []

    def underlying_bars(self, symbol: str, day: str):
        return self._bars.get(day, {})

    def daily_closes(self, symbol: str):
        return self._closes

    def session_days(self, symbol: str):
        return self._sessions


def _bar(open_price: float, close_price: float):
    high = max(open_price, close_price)
    low = min(open_price, close_price)
    return (open_price, high, low, close_price, 1.0)


def test_gap_fade_requires_large_gap_and_minimum_retracement():
    event = EarningsEvent("TEST", "2026-01-01", "2026-01-02")
    bars = {
        "2026-01-02": {
            570: _bar(110.0, 110.0),
            629: _bar(107.0, 107.0),
        }
    }
    archive = _Archive(bars, [("2026-01-01", 100.0)])
    assert gap_fade_signal(archive, event, 2.0, 0.25) == ("bearish", 630)
    assert gap_fade_signal(archive, event, 2.0, 0.50) is None


def test_gap_fade_handles_down_gap_symmetrically():
    event = EarningsEvent("TEST", "2026-01-01", "2026-01-02")
    bars = {
        "2026-01-02": {
            570: _bar(90.0, 90.0),
            629: _bar(93.0, 93.0),
        }
    }
    archive = _Archive(bars, [("2026-01-01", 100.0)])
    assert gap_fade_signal(archive, event, 2.0, 0.25) == ("bullish", 630)


def test_closing_gap_reversal_enters_next_session():
    event = EarningsEvent("TEST", "2026-01-01", "2026-01-02")
    bars = {
        "2026-01-02": {
            570: _bar(110.0, 110.0),
            955: _bar(104.0, 104.0),
        }
    }
    archive = _Archive(
        bars,
        [("2026-01-01", 100.0)],
        ["2026-01-02", "2026-01-05"],
    )
    assert closing_gap_reversal_signal(archive, event, 2.0) == ("bearish", 575)


def test_summary_excludes_best_trade_for_robustness():
    events = [
        EarningsEvent("AAPL", f"2025-0{index + 1}-01", f"2025-0{index + 1}-02")
        for index in range(6)
    ]
    rows = []
    for event, value in zip(events, [7.0, 8.0, 6.0, 9.0, 5.0, 10.0]):
        rows.append(
            ResultRow(
                strategy="candidate",
                structure="credit_vertical",
                symbol="AAPL",
                report_date=event.report_date,
                post_day=event.next_trading_day,
                execution_model="base",
                direction="bullish",
                status="ok",
                max_risk_dollars=100.0,
                pnl_dollars=value,
                return_on_risk_pct=value,
            )
        )
    result = next(
        row for row in summarize(rows, events)
        if row.strategy == "candidate"
        and row.structure == "credit_vertical"
        and row.scope == "AAPL"
        and row.execution_model == "base"
    )
    assert result.executed_n == 6
    assert result.exclude_best_1_mean_pct == 7.0
    assert result.robust_positive is True
