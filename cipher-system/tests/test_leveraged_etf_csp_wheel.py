from __future__ import annotations

import math
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Sequence
from zoneinfo import ZoneInfo

try:
    import pytest
except ModuleNotFoundError:  # Minimal fallback for bare VM smoke runs.
    class _Approx:
        def __init__(self, expected: float, rel: float = 1e-12, abs: float = 1e-12) -> None:
            self.expected = expected
            self.rel = rel
            self.abs = abs

        def __eq__(self, actual: object) -> bool:
            try:
                value = float(actual)
            except (TypeError, ValueError):
                return False
            tolerance = max(self.abs, abs(self.expected) * self.rel)
            return abs(value - self.expected) <= tolerance

    class _PytestFallback:
        @staticmethod
        def approx(expected: float, rel: float = 1e-12, abs: float = 1e-12) -> _Approx:
            return _Approx(expected, rel=rel, abs=abs)

    pytest = _PytestFallback()

CORE = Path(__file__).resolve().parents[1] / "core"
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

from leveraged_etf_csp_wheel import (
    DEFAULT_MODES,
    BuybackOpportunity,
    DailyBar,
    LeveragedEtfWheelBacktester,
    OptionCandidate,
    PutMode,
    UniverseAsset,
    WheelConfig,
    allocation_from_weekly_rsi,
    completed_weekly_closes,
    evaluate_put_candidates,
    implied_volatility,
    weekly_trend_state,
)


NY = ZoneInfo("America/New_York")


class InMemoryData:
    def __init__(self) -> None:
        self.bars: dict[str, dict[date, DailyBar]] = {}
        self.chains: dict[tuple[str, date, str], tuple[OptionCandidate, ...]] = {}
        self.buybacks: dict[tuple[str, date], float] = {}
        self.close_costs: dict[tuple[str, date], float] = {}
        self.marks: dict[tuple[str, date], float] = {}

    def add_bar(self, symbol: str, bar: DailyBar) -> None:
        self.bars.setdefault(symbol, {})[bar.day] = bar

    def add_chain(
        self,
        symbol: str,
        day: date,
        option_type: str,
        candidates: Sequence[OptionCandidate],
    ) -> None:
        self.chains[(symbol, day, option_type)] = tuple(candidates)

    def trading_days(self, start: date, end: date):
        days = {
            day
            for rows in self.bars.values()
            for day in rows
            if start <= day <= end
        }
        return tuple(sorted(days))

    def daily_bar(self, symbol: str, day: date):
        return self.bars.get(symbol, {}).get(day)

    def daily_history(self, symbol: str, end: date):
        return tuple(
            self.bars[symbol][day]
            for day in sorted(self.bars.get(symbol, {}))
            if day <= end
        )

    def option_chain(self, symbol: str, day: date, option_type: str):
        return self.chains.get((symbol, day, option_type), ())

    def buyback_opportunity(
        self,
        contract_symbol: str,
        day: date,
        target_price: float,
        *,
        exit_slippage_fraction: float,
        exit_slippage_floor: float,
    ):
        price = self.buybacks.get((contract_symbol, day))
        if price is None or price > target_price:
            return None
        return BuybackOpportunity(
            datetime.combine(day, time(10, 0), tzinfo=NY),
            price,
            price,
            "memory",
        )

    def close_buyback_price(
        self,
        contract_symbol: str,
        day: date,
        *,
        exit_slippage_fraction: float,
        exit_slippage_floor: float,
    ):
        return self.close_costs.get((contract_symbol, day))

    def option_liability_mark(self, contract_symbol: str, day: date):
        return self.marks.get((contract_symbol, day))


def quality_asset() -> UniverseAsset:
    return UniverseAsset(
        symbol="NVDL",
        reference="NVDA",
        quality_kind="single_company",
        quality_approved=True,
        quality_as_of="2024-01-01",
        leverage_multiple=2.0,
        parent_market_cap_billion=500.0,
        revenue_growth_positive=True,
        gross_margin_pct=60.0,
        all_time_high_history=True,
    )


def add_rising_weekly_history(
    data: InMemoryData,
    symbol: str,
    *,
    first_friday: date = date(2022, 1, 7),
    weeks: int = 110,
    start_price: float = 20.0,
) -> date:
    current = first_friday
    for index in range(weeks):
        close = start_price + index * 0.5
        data.add_bar(symbol, DailyBar(current, close - 0.1, close + 0.2, close - 0.2, close))
        current += timedelta(days=7)
    return current


def put_candidate(
    day: date,
    *,
    contract: str = "NVDL_TEST_P",
    strike: float = 70.0,
    credit_proxy: float = 4.0,
    dte: int = 30,
    iv: float = 0.50,
) -> OptionCandidate:
    return OptionCandidate(
        contract_symbol=contract,
        underlying="NVDL",
        option_type="put",
        expiration=day + timedelta(days=dte),
        strike=strike,
        pre_entry_price=credit_proxy,
        entry_price_proxy=credit_proxy,
        pre_entry_volume=100.0,
        source_archive="memory",
        implied_volatility=iv,
        entry_timestamp=datetime.combine(day, time(15, 45), tzinfo=NY),
    )


def call_candidate(
    day: date,
    *,
    contract: str = "NVDL_TEST_C",
    strike: float = 75.0,
    credit_proxy: float = 3.0,
    dte: int = 10,
) -> OptionCandidate:
    return OptionCandidate(
        contract_symbol=contract,
        underlying="NVDL",
        option_type="call",
        expiration=day + timedelta(days=dte),
        strike=strike,
        pre_entry_price=credit_proxy,
        entry_price_proxy=credit_proxy,
        pre_entry_volume=100.0,
        source_archive="memory",
        implied_volatility=0.50,
        entry_timestamp=datetime.combine(day, time(15, 45), tzinfo=NY),
    )


def test_weekly_clouds_exclude_current_week() -> None:
    bars = [
        DailyBar(date(2024, 1, 5), 10, 11, 9, 10),
        DailyBar(date(2024, 1, 12), 11, 12, 10, 11),
        DailyBar(date(2024, 1, 15), 100, 101, 99, 100),
    ]
    completed = completed_weekly_closes(bars, date(2024, 1, 16))
    assert completed == [
        (date(2024, 1, 5), 10),
        (date(2024, 1, 12), 11),
    ]


def test_weekly_cloud_and_rsi_state_is_bullish() -> None:
    data = InMemoryData()
    signal_day = add_rising_weekly_history(data, "NVDL")
    data.add_bar("NVDL", DailyBar(signal_day, 75, 76, 70, 71))
    state = weekly_trend_state(data.daily_history("NVDL", signal_day), signal_day, WheelConfig())
    assert state.bullish_clouds == 3
    assert state.weekly_rsi is not None
    assert state.weekly_rsi > 50


def test_iv_solver_recovers_input() -> None:
    from leveraged_etf_csp_wheel import black_scholes_price

    price = black_scholes_price(
        option_type="put",
        spot=100,
        strike=95,
        time_years=30 / 365,
        rate=0.04,
        dividend_yield=0.0,
        volatility=0.55,
    )
    solved = implied_volatility(
        option_type="put",
        price=price,
        spot=100,
        strike=95,
        time_years=30 / 365,
        rate=0.04,
        dividend_yield=0.0,
    )
    assert solved == pytest.approx(0.55, rel=1e-5)


def test_standard_mode_enforces_modeled_pop_floor() -> None:
    day = date(2026, 1, 2)
    candidate = OptionCandidate(
        contract_symbol="LOW_POP",
        underlying="NVDL",
        option_type="put",
        expiration=day + timedelta(days=30),
        strike=99.0,
        pre_entry_price=5.30,
        entry_price_proxy=5.30,
        pre_entry_volume=100.0,
        source_archive="memory",
        implied_volatility=0.60,
    )
    strict = evaluate_put_candidates(
        [candidate],
        day=day,
        spot=100.0,
        asset=quality_asset(),
        mode=DEFAULT_MODES["standard"],
        config=WheelConfig(),
    )
    relaxed = evaluate_put_candidates(
        [candidate],
        day=day,
        spot=100.0,
        asset=quality_asset(),
        mode=DEFAULT_MODES["standard"],
        config=WheelConfig(enforce_target_pop=False),
    )
    assert not strict
    assert relaxed
    assert relaxed[0].pop is not None and relaxed[0].pop < 0.70


def test_rsi_position_sizing_is_capped_normally() -> None:
    config = WheelConfig()
    assert allocation_from_weekly_rsi(70, config) == pytest.approx(0.10)
    assert allocation_from_weekly_rsi(32, config) == pytest.approx(0.25)
    assert allocation_from_weekly_rsi(15, config) == pytest.approx(0.30)

    aggressive = WheelConfig(
        enable_aggressive_scaling=True,
        max_trade_allocation=0.30,
        max_symbol_allocation=0.30,
    )
    assert allocation_from_weekly_rsi(24, aggressive) == pytest.approx(0.60)
    assert allocation_from_weekly_rsi(19, aggressive) == pytest.approx(0.80)


def test_fifty_percent_profit_take_recycles_collateral() -> None:
    data = InMemoryData()
    signal_day = add_rising_weekly_history(data, "NVDL")
    previous_close = 75.0
    data.add_bar("NVDL", DailyBar(signal_day, 74, 74.5, 69, 70))
    next_day = signal_day + timedelta(days=1)
    data.add_bar("NVDL", DailyBar(next_day, 70, 72, 69, 71))
    candidate = put_candidate(signal_day, strike=65, credit_proxy=3.5, dte=30)
    data.add_chain("NVDL", signal_day, "put", [candidate])
    # Entry credit is 3.325 after 5% slippage, so 1.6625 is the 50% target.
    data.buybacks[(candidate.contract_symbol, next_day)] = 1.50
    data.marks[(candidate.contract_symbol, signal_day)] = 3.0
    data.marks[(candidate.contract_symbol, next_day)] = 1.5

    config = WheelConfig(
        mode=PutMode(
            name="test",
            min_dte=25,
            max_dte=35,
            target_dte=30,
            target_collateral_return=0.05,
            minimum_collateral_return=0.01,
            maximum_collateral_return=0.10,
            target_pop=None,
            selection_style="target_return",
        )
    )
    backtester = LeveragedEtfWheelBacktester(
        data,
        [quality_asset()],
        config,
        initial_cash=100_000,
    )
    result = backtester.run(signal_day, next_day)
    assert not result.open_options
    assert any(event.event == "buy_to_close_50pct" for event in result.events)
    assert result.daily_equity[-1].reserved_collateral == pytest.approx(0.0)
    assert result.summary["ending_equity"] > 100_000


def test_itm_put_rolls_down_and_out_for_net_credit() -> None:
    data = InMemoryData()
    signal_day = add_rising_weekly_history(data, "NVDL")
    data.add_bar("NVDL", DailyBar(signal_day, 75, 75, 69, 70))
    original = put_candidate(signal_day, contract="OLD_PUT", strike=68, credit_proxy=4.0, dte=30)
    data.add_chain("NVDL", signal_day, "put", [original])
    data.marks[(original.contract_symbol, signal_day)] = 3.8

    roll_day = original.expiration - timedelta(days=4)
    current = signal_day + timedelta(days=1)
    while current <= roll_day:
        data.add_bar("NVDL", DailyBar(current, 65, 66, 60, 62))
        current += timedelta(days=1)
    data.close_costs[(original.contract_symbol, roll_day)] = 5.0
    rolled = OptionCandidate(
        contract_symbol="NEW_PUT",
        underlying="NVDL",
        option_type="put",
        expiration=original.expiration + timedelta(days=35),
        strike=60.0,
        pre_entry_price=7.0,
        entry_price_proxy=7.0,
        pre_entry_volume=100,
        source_archive="memory",
        implied_volatility=0.50,
        entry_timestamp=datetime.combine(roll_day, time(15, 45), tzinfo=NY),
    )
    data.add_chain("NVDL", roll_day, "put", [rolled])
    data.marks[(rolled.contract_symbol, roll_day)] = 6.8

    config = WheelConfig(
        mode=PutMode(
            name="test",
            min_dte=25,
            max_dte=35,
            target_dte=30,
            target_collateral_return=0.05,
            minimum_collateral_return=0.01,
            maximum_collateral_return=0.10,
            target_pop=None,
            selection_style="target_return",
        ),
        roll_trigger_dte=5,
    )
    backtester = LeveragedEtfWheelBacktester(
        data,
        [quality_asset()],
        config,
        initial_cash=100_000,
    )
    result = backtester.run(signal_day, roll_day)
    open_rows = list(result.open_options)
    assert len(open_rows) == 1
    assert open_rows[0]["contract_symbol"] == "NEW_PUT"
    assert open_rows[0]["strike"] < original.strike
    assert open_rows[0]["rolled_from"] is not None
    assert any(event.event == "roll_completed" for event in result.events)


def test_assignment_transitions_to_covered_call_and_called_shares() -> None:
    data = InMemoryData()
    signal_day = add_rising_weekly_history(data, "NVDL")
    data.add_bar("NVDL", DailyBar(signal_day, 75, 75, 68, 70))
    put = put_candidate(signal_day, contract="ASSIGN_PUT", strike=70, credit_proxy=6.0, dte=7)
    data.add_chain("NVDL", signal_day, "put", [put])
    data.marks[(put.contract_symbol, signal_day)] = 5.8

    expiration = put.expiration
    current = signal_day + timedelta(days=1)
    while current < expiration:
        data.add_bar("NVDL", DailyBar(current, 68, 69, 65, 67))
        current += timedelta(days=1)
    # A green expiration day permits the covered-call transition immediately.
    data.add_bar("NVDL", DailyBar(expiration, 65, 67, 64, 66))
    call = call_candidate(expiration, contract="COVERED_CALL", strike=66, credit_proxy=3.0, dte=10)
    data.add_chain("NVDL", expiration, "call", [call])
    data.marks[(call.contract_symbol, expiration)] = 2.8

    call_expiration = call.expiration
    current = expiration + timedelta(days=1)
    while current < call_expiration:
        data.add_bar("NVDL", DailyBar(current, 66, 68, 65, 67))
        current += timedelta(days=1)
    data.add_bar("NVDL", DailyBar(call_expiration, 70, 72, 69, 71))

    advanced = PutMode(
        name="assignment_test",
        min_dte=7,
        max_dte=14,
        target_dte=7,
        target_collateral_return=0.08,
        minimum_collateral_return=0.04,
        maximum_collateral_return=0.20,
        target_pop=None,
        selection_style="atm_or_below",
        seeking_assignment=True,
    )
    config = WheelConfig(
        mode=advanced,
        assignment_unwanted=False,
        covered_calls_enabled=True,
        profit_take_fraction=0.10,  # Prevent the synthetic call from closing early.
    )
    backtester = LeveragedEtfWheelBacktester(
        data,
        [quality_asset()],
        config,
        initial_cash=100_000,
    )
    result = backtester.run(signal_day, call_expiration)
    names = [event.event for event in result.events]
    assert "put_assigned" in names
    assert "sell_to_open_call" in names
    assert "call_assigned_shares_called" in names
    assert not result.stock_positions
    assert not result.open_options


def test_default_quality_asset_requires_dated_approval() -> None:
    from leveraged_etf_csp_wheel import default_universe

    for asset in default_universe():
        passed, reasons = asset.quality_check()
        assert not passed
        assert reasons


if __name__ == "__main__":
    tests = [
        value
        for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)} tests passed")
