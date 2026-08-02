from __future__ import annotations

from core.earnings_defined_risk_lab import (
    Contract,
    EarningsEvent,
    ExecutionModel,
    TradeRow,
    condor_debit_to_close,
    opening_range_signal,
    spread_intrinsic,
    summarize,
)


class _UnderlyingArchive:
    def __init__(self, bars):
        self._bars = bars

    def underlying_bars(self, symbol: str, day: str):
        return self._bars


class _OptionArchive:
    def __init__(self, bars):
        self._bars = bars

    def option_bars(self, contract: str, day: str):
        return self._bars[contract]


def _bar(price: float):
    return (price, price, price, price, 1.0)


def test_opening_range_requires_two_consecutive_five_minute_closes():
    bars = {minute: (100.0, 101.0, 99.0, 100.0, 1.0) for minute in range(570, 630)}
    bars[634] = (101.0, 102.0, 100.5, 101.5, 1.0)
    bars[639] = (101.5, 102.5, 101.0, 102.0, 1.0)
    event = EarningsEvent("TEST", "2026-01-01", "2026-01-02")
    assert opening_range_signal(_UnderlyingArchive(bars), event) == ("bullish", 640)


def test_morning_condor_exit_uses_first_print_after_target():
    contracts = tuple(
        Contract(f"C{index}", "TEST", "2026-01-02", strike, option_type)
        for index, (strike, option_type) in enumerate(
            ((90.0, "put"), (95.0, "put"), (105.0, "call"), (110.0, "call"))
        )
    )
    bars = {
        contract.symbol: {629: _bar(1.0), 631: _bar(2.0)}
        for contract in contracts
    }
    execution = ExecutionModel("test", 0.0, 0.0, 0.0)
    forward = condor_debit_to_close(
        _OptionArchive(bars), contracts, "2026-01-02", 630, execution, backward=False
    )
    backward = condor_debit_to_close(
        _OptionArchive(bars), contracts, "2026-01-02", 630, execution, backward=True
    )
    assert forward is not None and forward[1] == 631
    assert backward is not None and backward[1] == 629


def test_vertical_intrinsic_is_bounded_by_width():
    assert spread_intrinsic("bullish", 100.0, 110.0, 130.0) == 10.0
    assert spread_intrinsic("bearish", 100.0, 90.0, 70.0) == 10.0
    assert spread_intrinsic("bullish", 100.0, 110.0, 95.0) == 0.0


def test_ticker_robustness_requires_positive_worse_fill_result():
    events = [EarningsEvent("AAPL", f"2025-0{i + 1}-01", f"2025-0{i + 1}-02") for i in range(6)]
    rows = []
    for model, returns in (("base", [10, 12, 8, 9, 11, -1]), ("worse", [5, 6, 4, 3, 5, -2])):
        for event, value in zip(events, returns):
            rows.append(
                TradeRow(
                    strategy="candidate",
                    symbol="AAPL",
                    report_date=event.report_date,
                    post_day=event.next_trading_day,
                    execution_model=model,
                    direction="neutral",
                    status="ok",
                    max_risk_dollars=100.0,
                    pnl_dollars=value,
                    return_on_risk_pct=value,
                )
            )
    results = summarize(rows, events)
    aapl_base = next(
        row for row in results
        if row.strategy == "candidate" and row.scope == "AAPL" and row.execution_model == "base"
    )
    assert aapl_base.robust_positive is True
