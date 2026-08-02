from __future__ import annotations

import math
import sqlite3
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

CORE = Path(__file__).resolve().parents[1] / "core"
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

from capital_efficient_multi_stock_option_lab import (
    CREDIT_EXPIRY,
    ArchivePaths,
    CandidateRun,
    CandidateTrade,
    CapitalStrategySpec,
    LegTarget,
    SelectedLeg,
    StudySignalFeatures,
    _liquidation_cash,
    apply_promotion_rules,
    audit_archive,
    detect_split_events,
    fixed_strategy_specs,
    fixed_width_strategy_specs,
    replay_portfolio,
    select_legs,
    signal_passes,
    split_adjusted_daily_closes,
)
from historical_option_strategy_lab import (
    ContractObservation,
    DecisionSnapshot,
    ExecutionAssumption,
    HistoricalOptionResearchDataset,
    SignalFeatures,
)
from recent_option_strategy_expansion import PathBar
from historical_options_download import HistoricalOptionsStore


NY = ZoneInfo("America/New_York")
UTC = timezone.utc


def _business_days(start: date, count: int) -> list[date]:
    result: list[date] = []
    current = start
    while len(result) < count:
        if current.weekday() < 5:
            result.append(current)
        current += timedelta(days=1)
    return result


def _iso_market(day: date, clock: time) -> str:
    return (
        datetime.combine(day, clock, tzinfo=NY)
        .astimezone(UTC)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _build_archive(
    root: Path,
    *,
    ticker: str = "AMZN",
    option_type: str = "put",
    running_window: bool = False,
) -> tuple[Path, date, date, float]:
    store = HistoricalOptionsStore(root)
    days = _business_days(date(2023, 1, 3), 330)
    decision_day = days[270]
    expiration_day = days[295]
    ticker_closes = {day: 90.0 + index * 0.1 for index, day in enumerate(days)}
    spy_closes = {day: 400.0 + index * 0.5 for index, day in enumerate(days)}
    option_symbol = f"{ticker}240101{'P' if option_type == 'put' else 'C'}00100000"
    prior_spot = ticker_closes[days[269]]
    strike = prior_spot * (0.96 if option_type == "put" else 1.02)

    with store.connect() as db:
        for symbol, closes in ((ticker, ticker_closes), ("SPY", spy_closes)):
            db.executemany(
                """insert into underlying_bars(
                       symbol,timestamp,timeframe,open,high,low,close,volume,vwap,trades,source
                   ) values(?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        symbol,
                        f"{day.isoformat()}T21:00:00Z",
                        "1Day",
                        close,
                        close,
                        close,
                        close,
                        1_000_000.0,
                        close,
                        1000,
                        "test",
                    )
                    for day, close in closes.items()
                ],
            )
        db.execute(
            """insert into contracts(
                   symbol,underlying,expiration_date,strike,option_type,status,style,
                   multiplier,size,tradable,close_price,close_price_date,open_interest,
                   open_interest_date,metadata_observed_at,raw_json
               ) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                option_symbol,
                ticker,
                expiration_day.isoformat(),
                strike,
                option_type,
                "active",
                "american",
                100,
                100,
                1,
                2.0,
                decision_day.isoformat(),
                1000.0,
                decision_day.isoformat(),
                f"{decision_day.isoformat()}T12:00:00Z",
                "{}",
            ),
        )
        db.execute(
            """insert into decision_selections(
                   decision_date,symbol,expiration_date,strike,option_type,spot,dte,
                   moneyness,rank,selected_at
               ) values(?,?,?,?,?,?,?,?,?,?)""",
            (
                decision_day.isoformat(),
                option_symbol,
                expiration_day.isoformat(),
                strike,
                option_type,
                prior_spot,
                (expiration_day - decision_day).days,
                strike / prior_spot,
                1,
                f"{decision_day.isoformat()}T20:00:00Z",
            ),
        )
        db.execute(
            """insert into selection_observation_audit(
                   decision_date,symbol,first_bar_at,first_trade_at,bars_on_decision,
                   trades_on_decision,observed_on_decision,audited_at
               ) values(?,?,?,?,?,?,?,?)""",
            (
                decision_day.isoformat(),
                option_symbol,
                _iso_market(decision_day, time(14, 0)),
                None,
                2,
                0,
                1,
                f"{decision_day.isoformat()}T22:00:00Z",
            ),
        )
        db.executemany(
            """insert into option_bars(
                   symbol,timestamp,timeframe,open,high,low,close,volume,vwap,trades,source
               ) values(?,?,?,?,?,?,?,?,?,?,?)""",
            [
                (
                    option_symbol,
                    _iso_market(decision_day, time(14, 0)),
                    "1Min",
                    2.0,
                    2.1,
                    1.9,
                    2.0,
                    20.0,
                    2.0,
                    5,
                    "test",
                ),
                (
                    option_symbol,
                    _iso_market(decision_day, time(15, 50)),
                    "1Min",
                    2.0,
                    2.2,
                    1.8,
                    2.0,
                    15.0,
                    2.0,
                    4,
                    "test",
                ),
            ],
        )
        db.execute(
            """insert into download_runs(
                   started_at,completed_at,status,underlying,start_date,end_date,
                   config_json,summary_json,error
               ) values(?,?,?,?,?,?,?,?,?)""",
            (
                f"{decision_day.isoformat()}T00:00:00Z",
                f"{decision_day.isoformat()}T23:00:00Z",
                "complete",
                ticker,
                decision_day.isoformat(),
                expiration_day.isoformat(),
                "{}",
                "{}",
                None,
            ),
        )
        if running_window:
            db.execute(
                """insert into download_windows(
                       window_key,run_id,kind,symbols_hash,symbols_json,start_at,end_at,
                       timeframe,status,page_count,row_count,completed_at,error
                   ) values(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    "running-test",
                    None,
                    "bars",
                    "hash",
                    "[]",
                    f"{decision_day.isoformat()}T00:00:00Z",
                    f"{expiration_day.isoformat()}T23:59:00Z",
                    "1Min",
                    "running",
                    0,
                    0,
                    None,
                    None,
                ),
            )
    return store.db_path, decision_day, expiration_day, prior_spot


def _spec(name: str = "test_bull_put") -> CapitalStrategySpec:
    return CapitalStrategySpec(
        name=name,
        family="bull_put_spread",
        direction="bullish",
        option_type="put",
        signal="always",
        legs=(LegTarget(-1, 0.96), LegTarget(1, 0.92)),
        exit_rule=CREDIT_EXPIRY,
    )


def _candidate(
    ticker: str,
    decision_day: date,
    exit_day: date,
    *,
    pnl: float,
    risk: float,
    adverse: float | None = None,
    strength: float = 0.0,
    volume: float = 10.0,
) -> CandidateTrade:
    return CandidateTrade(
        strategy="test_bull_put",
        family="bull_put_spread",
        direction="bullish",
        signal="always",
        exit_rule="expiry",
        ticker=ticker,
        decision_date=decision_day,
        expiration_date=exit_day,
        exit_date=exit_day,
        exit_reason="expiration",
        days_held=(exit_day - decision_day).days,
        leg_symbols=("SHORT", "LONG"),
        leg_quantities=(-1, 1),
        leg_strikes=(100.0, 95.0),
        entry_cash_per_share=1.0,
        exit_cash_per_share=0.0,
        expiration_payoff_per_share=0.0,
        fees=5.0,
        pnl=pnl,
        maximum_loss=risk,
        return_on_maximum_loss=pnl / risk,
        minimum_entry_volume=volume,
        signal_strength=strength,
        feature_return_5d=0.01,
        feature_return_20d=0.05,
        feature_above_sma_200=True,
        feature_ema50_reclaim=False,
        feature_ema50_breakdown=False,
        max_adverse_pnl=adverse if adverse is not None else min(0.0, pnl),
        max_favorable_pnl=max(0.0, pnl),
        exit_timestamp=None,
    )


def test_historical_dataset_uses_requested_underlying(tmp_path: Path) -> None:
    database, decision_day, expiration_day, prior_spot = _build_archive(tmp_path / "archive")
    dataset = HistoricalOptionResearchDataset(database, underlying_symbol="AMZN")
    snapshot = dataset.snapshots[0]
    assert snapshot.decision_date == decision_day
    assert snapshot.features.prior_close == pytest.approx(prior_spot)
    settlement_day, settlement_spot = dataset.settlement(expiration_day)
    assert settlement_day == expiration_day
    assert settlement_spot < 200.0
    assert dataset.underlying_symbol == "AMZN"


def test_archive_audit_blocks_running_window(tmp_path: Path) -> None:
    database, *_ = _build_archive(tmp_path / "archive", running_window=True)
    audit = audit_archive(database, "AMZN", "put")
    assert not audit.passed
    assert "download_window_still_running" in audit.blockers
    assert audit.selected_contracts == audit.observed_contracts == 1


def test_frozen_protocol_is_unique_and_complete() -> None:
    specs = fixed_strategy_specs()
    assert len(specs) == 194
    assert len({row.name for row in specs}) == len(specs)
    assert {row.family for row in specs} == {
        "long_call",
        "bull_call_spread",
        "call_butterfly",
        "bull_put_spread",
        "long_put",
        "bear_put_spread",
    }


def test_fixed_width_protocol_and_exact_strike_selection() -> None:
    specs = fixed_width_strategy_specs()
    assert len(specs) == 396
    assert len({row.name for row in specs}) == len(specs)
    assert {row.family for row in specs} == {
        "bull_call_spread",
        "bull_put_spread",
        "call_butterfly",
        "bear_put_spread",
        "bear_call_spread",
        "put_butterfly",
    }

    expiration = date(2026, 2, 20)
    contracts = tuple(
        ContractObservation(
            symbol=f"TESTC{int(strike * 1000):08d}",
            expiration_date=expiration,
            strike=strike,
            moneyness=strike / 100.0,
            dte=35,
            rank=index,
            pre_entry_bar_count=10,
            pre_entry_volume=20.0,
            entry_bar_count=2,
            entry_volume=10.0,
            entry_low=1.0,
            entry_high=1.2,
            entry_first_timestamp=None,
            entry_last_timestamp=None,
        )
        for index, strike in enumerate((100.0, 102.5, 105.0), start=1)
    )
    snapshot = DecisionSnapshot(
        decision_date=date(2026, 1, 2),
        features=SignalFeatures(
            decision_date=date(2026, 1, 2),
            prior_close=100.0,
            return_5d=0.01,
            return_20d=0.05,
            realized_volatility_20d=0.20,
            realized_volatility_percentile=0.50,
            sma_200=95.0,
            above_sma_200=True,
            drawdown_20d=-0.01,
            history_rows=300,
        ),
        contracts=contracts,
    )
    exact = next(row for row in specs if row.name == "fixed_bull_call_w2p5_always_expiry")
    selected = select_legs(snapshot, exact)
    assert selected is not None
    assert [row.strike for _, row in selected] == [100.0, 102.5]

    unavailable = next(row for row in specs if row.name == "fixed_bull_call_w1p0_always_expiry")
    assert select_legs(snapshot, unavailable) is None

    fallback_contracts = tuple(
        ContractObservation(
            symbol=f"FALLBACKC{int(strike * 1000):08d}",
            expiration_date=expiration,
            strike=strike,
            moneyness=strike / 100.0,
            dte=35,
            rank=index,
            pre_entry_bar_count=10,
            pre_entry_volume=20.0,
            entry_bar_count=2,
            entry_volume=10.0,
            entry_low=1.0,
            entry_high=1.2,
            entry_first_timestamp=None,
            entry_last_timestamp=None,
        )
        for index, strike in enumerate((100.0, 101.0, 103.5), start=1)
    )
    fallback_snapshot = DecisionSnapshot(
        decision_date=snapshot.decision_date,
        features=snapshot.features,
        contracts=fallback_contracts,
    )
    fallback_selected = select_legs(fallback_snapshot, exact)
    assert fallback_selected is not None
    assert [row.strike for _, row in fallback_selected] == [101.0, 103.5]


def test_signal_filters_use_only_supplied_features() -> None:
    features = StudySignalFeatures(
        ticker="AMZN",
        decision_date=date(2026, 1, 2),
        prior_close=110.0,
        prior_return_5d=-0.02,
        prior_return_20d=0.08,
        ema_50=108.0,
        previous_ema_50=109.0,
        sma_200=100.0,
        above_sma_200=True,
        ema50_reclaim=True,
        ema50_breakdown=False,
        history_rows=300,
    )
    assert signal_passes("momentum20_positive", features)[0]
    assert signal_passes("momentum20_and_trend200", features)[0]
    assert signal_passes("mild_pullback_uptrend", features)[0]
    assert signal_passes("ema50_reclaim", features)[0]
    assert not signal_passes("momentum20_negative", features)[0]


def test_portfolio_enforces_capital_and_one_open_position() -> None:
    first_day = date(2025, 1, 2)
    run = CandidateRun(
        _spec(),
        (
            _candidate("AMZN", first_day, date(2025, 1, 20), pnl=50.0, risk=200.0),
            _candidate("NVDA", first_day, date(2025, 1, 17), pnl=100.0, risk=300.0),
            _candidate("META", date(2025, 1, 10), date(2025, 2, 1), pnl=30.0, risk=100.0),
            _candidate("GOOGL", date(2025, 2, 3), date(2025, 2, 21), pnl=-25.0, risk=250.0, adverse=-80.0),
        ),
        (),
    )
    result = replay_portfolio(run, 250.0)
    assert result.trade_count == 2
    assert [row.ticker for row in result.trades] == ["AMZN", "GOOGL"]
    assert result.skipped_for_capital == 1
    assert result.skipped_while_position_open == 1
    assert result.ending_equity == pytest.approx(275.0)
    assert result.observed_mtm_max_drawdown_pct > 0
    assert result.maximum_single_trade_risk_pct == pytest.approx(250.0 / 300.0)


def test_portfolio_enforces_fractional_risk_cap() -> None:
    first_day = date(2025, 1, 2)
    run = CandidateRun(
        _spec(),
        (
            _candidate("AMZN", first_day, date(2025, 1, 10), pnl=20.0, risk=150.0),
            _candidate("GOOGL", first_day, date(2025, 1, 10), pnl=10.0, risk=100.0),
        ),
        (),
    )
    result = replay_portfolio(
        run,
        500.0,
        maximum_trade_risk_fraction=0.25,
    )
    assert result.trade_count == 1
    assert result.trades[0].ticker == "GOOGL"
    assert result.skipped_for_risk_cap == 1
    assert result.maximum_trade_risk_fraction == pytest.approx(0.25)
    assert result.maximum_single_trade_risk_pct == pytest.approx(0.20)


def test_split_detection_backward_adjusts_raw_history() -> None:
    rows = (
        (date(2024, 6, 6), 1200.0),
        (date(2024, 6, 7), 1210.0),
        (date(2024, 6, 10), 121.5),
        (date(2024, 6, 11), 123.0),
    )
    events = detect_split_events(rows)
    assert len(events) == 1
    assert events[0].effective_date == date(2024, 6, 10)
    assert events[0].backward_adjustment_factor == pytest.approx(0.1)
    adjusted = split_adjusted_daily_closes(rows, events)
    assert adjusted[1][1] == pytest.approx(121.0)
    assert adjusted[2][1] == pytest.approx(121.5)


def test_defined_risk_liquidation_is_clamped_to_package_bounds() -> None:
    expiration = date(2026, 2, 20)
    short_contract = ContractObservation(
        symbol="TESTP100",
        expiration_date=expiration,
        strike=100.0,
        moneyness=1.0,
        dte=35,
        rank=1,
        pre_entry_bar_count=10,
        pre_entry_volume=20.0,
        entry_bar_count=2,
        entry_volume=10.0,
        entry_low=2.0,
        entry_high=2.2,
        entry_first_timestamp=None,
        entry_last_timestamp=None,
    )
    long_contract = ContractObservation(
        symbol="TESTP95",
        expiration_date=expiration,
        strike=95.0,
        moneyness=0.95,
        dte=35,
        rank=2,
        pre_entry_bar_count=10,
        pre_entry_volume=20.0,
        entry_bar_count=2,
        entry_volume=10.0,
        entry_low=1.0,
        entry_high=1.2,
        entry_first_timestamp=None,
        entry_last_timestamp=None,
    )
    spec = _spec()
    legs = (
        SelectedLeg(-1, short_contract, 2.0),
        SelectedLeg(1, long_contract, 1.0),
    )
    bars = (
        PathBar(datetime(2026, 1, 5, 15, 0, tzinfo=UTC), 10.0, 20.0, 10.0),
        PathBar(datetime(2026, 1, 5, 15, 0, tzinfo=UTC), 1.0, 1.2, 10.0),
    )
    execution = ExecutionAssumption("test", 0.20, 0.15, 1.25)
    close_cash = _liquidation_cash(spec, legs, bars, execution)
    assert close_cash == pytest.approx(-5.0)


def test_holm_and_promotion_rules_do_not_promote_sparse_results() -> None:
    first_day = date(2025, 1, 2)
    run = CandidateRun(
        _spec(),
        (
            _candidate("AMZN", first_day, date(2025, 1, 10), pnl=10.0, risk=100.0),
            _candidate("GOOGL", date(2026, 1, 2), date(2026, 1, 10), pnl=10.0, risk=100.0),
        ),
        (),
    )
    base = replay_portfolio(run, 500.0)
    liquid = replay_portfolio(run, 500.0, minimum_entry_volume=5.0)
    completed = apply_promotion_rules(
        [base],
        {(base.strategy, base.starting_equity): liquid},
    )[0]
    assert completed.adjusted_p_value_holm is not None
    assert not completed.promoted
    assert not completed.promotion_checks["minimum_12_trades"]
