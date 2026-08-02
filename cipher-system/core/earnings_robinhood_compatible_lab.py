"""Robinhood-compatible earnings options research for four mega-cap names.

Evaluates structures that do not require butterflies or iron condors:

* next-week ATM single-leg calls/puts after mechanical earnings confirmation;
* next-week debit verticals after the same confirmation;
* immediate-expiry one-sided put/call credit verticals before earnings.

The frozen event set and immutable Alpaca OPRA archives come from
``earnings_defined_risk_lab``. Historical NBBO is unavailable, so every fill is
reconstructed conservatively from one-minute trade-bar extremes. Research only;
there are no brokerage or order-routing calls.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import asdict, dataclass
from datetime import date, time
from pathlib import Path
from statistics import mean, median
from typing import Sequence

try:
    from .earnings_defined_risk_lab import (
        Archive,
        Contract,
        EarningsEvent,
        ExecutionModel,
        EXECUTION_MODELS,
        DEFAULT_AAPL_CONDOR_DB,
        DEFAULT_EVENTS,
        DEFAULT_NEXT_WEEK_DB,
        DEFAULT_OTHER_CONDOR_DB,
        choose_expiration,
        clock_minute,
        debit_trade,
        find_bar,
        gap_hold_signal,
        minute_clock,
        momentum_5d,
        next_session,
        opening_range_signal,
        parse_events,
        prior_close,
        same_expiry_pool,
        underlying_spot,
    )
except ImportError:  # Direct script execution from cipher-system/core.
    from earnings_defined_risk_lab import (
        Archive,
        Contract,
        EarningsEvent,
        ExecutionModel,
        EXECUTION_MODELS,
        DEFAULT_AAPL_CONDOR_DB,
        DEFAULT_EVENTS,
        DEFAULT_NEXT_WEEK_DB,
        DEFAULT_OTHER_CONDOR_DB,
        choose_expiration,
        clock_minute,
        debit_trade,
        find_bar,
        gap_hold_signal,
        minute_clock,
        momentum_5d,
        next_session,
        opening_range_signal,
        parse_events,
        prior_close,
        same_expiry_pool,
        underlying_spot,
    )

CORE = Path(__file__).resolve().parent
ROOT = CORE.parent
DEFAULT_OUTPUT = ROOT / "data" / "earnings_robinhood_compatible_lab"


@dataclass(slots=True)
class ResultRow:
    strategy: str
    structure: str
    symbol: str
    report_date: str
    post_day: str
    execution_model: str
    direction: str
    status: str
    skip_reason: str | None = None
    entry_day: str | None = None
    entry_time_et: str | None = None
    exit_day: str | None = None
    exit_time_et: str | None = None
    expiration_date: str | None = None
    long_contract: str | None = None
    long_strike: float | None = None
    short_contract: str | None = None
    short_strike: float | None = None
    underlying_entry_spot: float | None = None
    signal_detail: str | None = None
    entry_value: float | None = None
    exit_value: float | None = None
    max_risk_dollars: float | None = None
    fees_dollars: float | None = None
    pnl_dollars: float | None = None
    return_on_risk_pct: float | None = None


@dataclass(slots=True)
class SummaryRow:
    strategy: str
    structure: str
    scope: str
    execution_model: str
    event_n: int
    executed_n: int
    coverage_pct: float
    mean_return_pct: float | None
    median_return_pct: float | None
    win_rate_pct: float | None
    total_pnl_dollars: float | None
    profit_factor: float | None
    exclude_best_1_mean_pct: float | None
    best_return_pct: float | None
    worst_return_pct: float | None
    robust_positive: bool


def empty_row(
    strategy: str,
    structure: str,
    event: EarningsEvent,
    execution: ExecutionModel,
    direction: str,
    reason: str,
) -> ResultRow:
    return ResultRow(
        strategy=strategy,
        structure=structure,
        symbol=event.symbol,
        report_date=event.report_date,
        post_day=event.next_trading_day,
        execution_model=execution.name,
        direction=direction,
        status="skipped",
        skip_reason=reason,
    )


def option_entry_buy(
    archive: Archive,
    contract: Contract,
    day: str,
    minute: int,
    execution: ExecutionModel,
) -> tuple[float, int] | None:
    bar, actual = find_bar(archive.option_bars(contract.symbol, day), minute, forward=True)
    if bar is None or actual is None:
        return None
    return execution.buy(bar[1]), actual


def option_exit_sell(
    archive: Archive,
    contract: Contract,
    day: str,
    minute: int,
    execution: ExecutionModel,
    *,
    backward: bool,
) -> tuple[float, int] | None:
    bar, actual = find_bar(
        archive.option_bars(contract.symbol, day), minute, forward=not backward
    )
    if bar is None or actual is None:
        return None
    return execution.sell(bar[2]), actual


def select_atm_contract(
    archive: Archive,
    event: EarningsEvent,
    direction: str,
    spot: float,
) -> Contract | None:
    option_type = "call" if direction == "bullish" else "put"
    contracts = archive.contracts(event.report_date, event.symbol, option_type)
    expiry = choose_expiration(contracts, event.report_date, 8)
    pool = [row for row in contracts if row.expiration_date == expiry]
    if not pool:
        return None
    return min(pool, key=lambda row: (abs(row.strike - spot), row.strike))


def single_leg_trade(
    *,
    strategy: str,
    event: EarningsEvent,
    archive: Archive,
    execution: ExecutionModel,
    direction: str,
    entry_day: str,
    entry_minute: int,
    exit_policy: str,
    signal_detail: str,
) -> ResultRow:
    row = ResultRow(
        strategy=strategy,
        structure="single_leg",
        symbol=event.symbol,
        report_date=event.report_date,
        post_day=event.next_trading_day,
        execution_model=execution.name,
        direction=direction,
        status="skipped",
        entry_day=entry_day,
        entry_time_et=minute_clock(entry_minute),
        signal_detail=signal_detail,
    )
    spot = underlying_spot(archive, event.symbol, entry_day, entry_minute)
    row.underlying_entry_spot = spot
    if spot is None:
        row.skip_reason = "missing underlying entry bar"
        return row
    contract = select_atm_contract(archive, event, direction, spot)
    if contract is None:
        row.skip_reason = "no valid next-week ATM contract"
        return row
    row.expiration_date = contract.expiration_date
    row.long_contract = contract.symbol
    row.long_strike = contract.strike
    entry = option_entry_buy(archive, contract, entry_day, entry_minute, execution)
    if entry is None:
        row.skip_reason = "missing option entry bar"
        return row
    premium, actual_entry = entry
    if premium <= 0:
        row.skip_reason = "non-economic option entry"
        return row
    row.entry_time_et = minute_clock(actual_entry)
    row.entry_value = premium
    risk = premium * 100.0 + execution.fee_per_leg

    exit_day: str | None = None
    exit_minute: int | None = None
    exit_price: float | None = None
    if exit_policy == "same_day_close":
        exit_day = entry_day
        observation = option_exit_sell(
            archive, contract, exit_day, 955, execution, backward=True
        )
        if observation:
            exit_price, exit_minute = observation
    elif exit_policy == "next_session_close":
        exit_day = next_session(archive, event.symbol, entry_day)
        if exit_day:
            observation = option_exit_sell(
                archive, contract, exit_day, 955, execution, backward=True
            )
            if observation:
                exit_price, exit_minute = observation
    else:
        target = premium * 1.50
        stop = premium * 0.60
        days = [
            day for day in archive.session_days(event.symbol)
            if entry_day <= day <= contract.expiration_date
        ]
        for day in days:
            start = actual_entry + 1 if day == entry_day else 570
            for minute in range(start, 956):
                observation = option_exit_sell(
                    archive, contract, day, minute, execution, backward=False
                )
                if observation is None:
                    continue
                value, actual = observation
                if value <= stop or value >= target:
                    exit_day, exit_minute, exit_price = day, actual, value
                    break
            if exit_price is not None:
                break
        if exit_price is None:
            exit_day = contract.expiration_date
            observation = option_exit_sell(
                archive, contract, exit_day, 955, execution, backward=True
            )
            if observation:
                exit_price, exit_minute = observation

    if exit_day is None or exit_minute is None or exit_price is None:
        row.skip_reason = "missing option exit observation"
        return row
    total_fees = 2.0 * execution.fee_per_leg
    pnl = (exit_price - premium) * 100.0 - total_fees
    row.status = "ok"
    row.exit_day = exit_day
    row.exit_time_et = minute_clock(exit_minute)
    row.exit_value = exit_price
    row.max_risk_dollars = risk
    row.fees_dollars = total_fees
    row.pnl_dollars = pnl
    row.return_on_risk_pct = pnl / risk * 100.0
    return row


def delayed_close_signal(archive: Archive, event: EarningsEvent) -> tuple[str, int] | None:
    bars = archive.underlying_bars(event.symbol, event.next_trading_day)
    opening = [bars[minute] for minute in range(570, 630) if minute in bars]
    close_bar, _ = find_bar(bars, 955, forward=False)
    open_bar = bars.get(570)
    if len(opening) < 50 or close_bar is None or open_bar is None:
        return None
    opening_high = max(row[1] for row in opening)
    opening_low = min(row[2] for row in opening)
    close = close_bar[3]
    session_open = open_bar[0]
    next_day = next_session(archive, event.symbol, event.next_trading_day)
    if next_day is None:
        return None
    if close > opening_high and close > session_open:
        return "bullish", 575
    if close < opening_low and close < session_open:
        return "bearish", 575
    return None


def gap_fade_signal(
    archive: Archive,
    event: EarningsEvent,
    minimum_gap_pct: float,
    minimum_retrace: float,
) -> tuple[str, int] | None:
    """Fade a large opening gap only after a measured first-hour retracement."""
    bars = archive.underlying_bars(event.symbol, event.next_trading_day)
    reference = prior_close(archive, event.symbol, event.report_date)
    open_bar = bars.get(570)
    confirm_bar = bars.get(629)
    if reference is None or open_bar is None or confirm_bar is None or reference <= 0:
        return None
    session_open = open_bar[0]
    confirmation = confirm_bar[3]
    gap_pct = (session_open / reference - 1.0) * 100.0
    gap_distance = abs(session_open - reference)
    if gap_distance <= 0 or abs(gap_pct) < minimum_gap_pct:
        return None
    if gap_pct > 0:
        retrace = (session_open - confirmation) / gap_distance
        if confirmation < session_open and retrace >= minimum_retrace:
            return "bearish", 630
    else:
        retrace = (confirmation - session_open) / gap_distance
        if confirmation > session_open and retrace >= minimum_retrace:
            return "bullish", 630
    return None


def closing_gap_reversal_signal(
    archive: Archive,
    event: EarningsEvent,
    minimum_gap_pct: float,
) -> tuple[str, int] | None:
    """Enter next morning after the first post-report session rejects a large gap."""
    bars = archive.underlying_bars(event.symbol, event.next_trading_day)
    reference = prior_close(archive, event.symbol, event.report_date)
    open_bar = bars.get(570)
    close_bar, _ = find_bar(bars, 955, forward=False)
    next_day = next_session(archive, event.symbol, event.next_trading_day)
    if reference is None or open_bar is None or close_bar is None or next_day is None:
        return None
    session_open = open_bar[0]
    close = close_bar[3]
    gap_pct = (session_open / reference - 1.0) * 100.0
    if gap_pct >= minimum_gap_pct and close < session_open:
        return "bearish", 575
    if gap_pct <= -minimum_gap_pct and close > session_open:
        return "bullish", 575
    return None


def gap_retest_signal(archive: Archive, event: EarningsEvent) -> tuple[str, int] | None:
    bars = archive.underlying_bars(event.symbol, event.next_trading_day)
    reference = prior_close(archive, event.symbol, event.report_date)
    if reference is None or 570 not in bars:
        return None
    session_open = bars[570][0]
    gap_pct = (session_open / reference - 1.0) * 100.0
    if abs(gap_pct) < 1.0:
        return None
    opening = [bars[minute] for minute in range(570, 630) if minute in bars]
    if len(opening) < 50:
        return None
    level = max(row[1] for row in opening) if gap_pct > 0 else min(row[2] for row in opening)
    direction = "bullish" if gap_pct > 0 else "bearish"
    broke = False
    tolerance = 0.0035
    for minute in range(630, 930):
        bar = bars.get(minute)
        if bar is None:
            continue
        if direction == "bullish":
            if bar[3] > level:
                broke = True
            if broke and bar[2] <= level * (1.0 + tolerance) and bar[3] > level:
                return direction, minute + 1
        else:
            if bar[3] < level:
                broke = True
            if broke and bar[1] >= level * (1.0 - tolerance) and bar[3] < level:
                return direction, minute + 1
    return None


def credit_spread_trade(
    *,
    strategy: str,
    event: EarningsEvent,
    archive: Archive,
    execution: ExecutionModel,
    direction: str,
    move_multiplier: float,
    exit_minute: int,
) -> ResultRow:
    row = ResultRow(
        strategy=strategy,
        structure="credit_vertical",
        symbol=event.symbol,
        report_date=event.report_date,
        post_day=event.next_trading_day,
        execution_model=execution.name,
        direction=direction,
        status="skipped",
        entry_day=event.report_date,
        entry_time_et="15:45",
        exit_day=event.next_trading_day,
        exit_time_et=minute_clock(exit_minute),
        signal_detail=f"{move_multiplier:.2f}x implied move",
    )
    spot = underlying_spot(archive, event.symbol, event.report_date, 945)
    row.underlying_entry_spot = spot
    if spot is None:
        row.skip_reason = "missing underlying 15:45 bar"
        return row
    pools = same_expiry_pool(archive, event)
    if pools is None:
        row.skip_reason = "no common immediate expiration"
        return row
    expiry, calls, puts = pools
    row.expiration_date = expiry
    atm_call = min(calls, key=lambda contract: abs(contract.strike - spot))
    atm_put = min(puts, key=lambda contract: abs(contract.strike - spot))
    call_bar, _ = find_bar(archive.option_bars(atm_call.symbol, event.report_date), 945, forward=True)
    put_bar, _ = find_bar(archive.option_bars(atm_put.symbol, event.report_date), 945, forward=True)
    if call_bar is None or put_bar is None:
        row.skip_reason = "missing ATM straddle entry proxy"
        return row
    implied_move = call_bar[3] + put_bar[3]
    wing_target = max(5.0, round((spot * 0.02) / 5.0) * 5.0)
    if direction == "bullish":
        boundary = spot - move_multiplier * implied_move
        shorts = [contract for contract in puts if contract.strike <= boundary]
        if not shorts:
            row.skip_reason = "no put short strike beyond implied move"
            return row
        short_leg = max(shorts, key=lambda contract: contract.strike)
        longs = [
            contract for contract in puts
            if contract.expiration_date == short_leg.expiration_date
            and contract.strike <= short_leg.strike - wing_target
        ]
        if not longs:
            row.skip_reason = "no lower protective put"
            return row
        long_leg = max(longs, key=lambda contract: contract.strike)
    else:
        boundary = spot + move_multiplier * implied_move
        shorts = [contract for contract in calls if contract.strike >= boundary]
        if not shorts:
            row.skip_reason = "no call short strike beyond implied move"
            return row
        short_leg = min(shorts, key=lambda contract: contract.strike)
        longs = [
            contract for contract in calls
            if contract.expiration_date == short_leg.expiration_date
            and contract.strike >= short_leg.strike + wing_target
        ]
        if not longs:
            row.skip_reason = "no upper protective call"
            return row
        long_leg = min(longs, key=lambda contract: contract.strike)

    row.long_contract, row.long_strike = long_leg.symbol, long_leg.strike
    row.short_contract, row.short_strike = short_leg.symbol, short_leg.strike
    short_bar, short_actual = find_bar(
        archive.option_bars(short_leg.symbol, event.report_date), 945, forward=True
    )
    long_bar, long_actual = find_bar(
        archive.option_bars(long_leg.symbol, event.report_date), 945, forward=True
    )
    if short_bar is None or long_bar is None or short_actual is None or long_actual is None:
        row.skip_reason = "missing credit-spread entry bar"
        return row
    actual_entry = max(short_actual, long_actual)
    short_bar = archive.option_bars(short_leg.symbol, event.report_date).get(actual_entry, short_bar)
    long_bar = archive.option_bars(long_leg.symbol, event.report_date).get(actual_entry, long_bar)
    credit = execution.sell(short_bar[2]) - execution.buy(long_bar[1])
    width = abs(short_leg.strike - long_leg.strike)
    if credit <= 0 or credit >= width:
        row.skip_reason = "non-economic reconstructed credit"
        return row

    short_exit, short_exit_minute = find_bar(
        archive.option_bars(short_leg.symbol, event.next_trading_day), exit_minute, forward=True
    )
    long_exit, long_exit_minute = find_bar(
        archive.option_bars(long_leg.symbol, event.next_trading_day), exit_minute, forward=True
    )
    if (
        short_exit is None or long_exit is None
        or short_exit_minute is None or long_exit_minute is None
    ):
        row.skip_reason = "missing credit-spread exit bar"
        return row
    actual_exit = max(short_exit_minute, long_exit_minute)
    short_exit = archive.option_bars(short_leg.symbol, event.next_trading_day).get(actual_exit, short_exit)
    long_exit = archive.option_bars(long_leg.symbol, event.next_trading_day).get(actual_exit, long_exit)
    debit = execution.buy(short_exit[1]) - execution.sell(long_exit[2])
    debit = max(0.0, min(width, debit))
    total_fees = 4.0 * execution.fee_per_leg
    risk = (width - credit) * 100.0 + 2.0 * execution.fee_per_leg
    pnl = (credit - debit) * 100.0 - total_fees
    row.status = "ok"
    row.entry_time_et = minute_clock(actual_entry)
    row.exit_time_et = minute_clock(actual_exit)
    row.entry_value = credit
    row.exit_value = debit
    row.max_risk_dollars = risk
    row.fees_dollars = total_fees
    row.pnl_dollars = pnl
    row.return_on_risk_pct = pnl / risk * 100.0
    return row


def append_directional_variants(
    rows: list[ResultRow],
    event: EarningsEvent,
    next_week: Archive,
    execution: ExecutionModel,
    strategy_prefix: str,
    signal: tuple[str, int] | None,
    entry_day: str,
    detail: str,
) -> None:
    for exit_policy in ("same_day_close", "next_session_close", "managed"):
        strategy = f"{strategy_prefix}_{exit_policy}"
        if signal is None:
            rows.append(empty_row(strategy, "single_leg", event, execution, "none", f"no {detail}"))
            rows.append(empty_row(strategy, "debit_vertical", event, execution, "none", f"no {detail}"))
            continue
        direction, minute = signal
        rows.append(
            single_leg_trade(
                strategy=strategy,
                event=event,
                archive=next_week,
                execution=execution,
                direction=direction,
                entry_day=entry_day,
                entry_minute=minute,
                exit_policy=exit_policy,
                signal_detail=detail,
            )
        )
        spread = debit_trade(
            strategy=strategy,
            event=event,
            archive=next_week,
            execution=execution,
            direction=direction,
            signal_time=max(570, minute - 1),
            entry_day=entry_day,
            entry_minute=minute,
            exit_policy=exit_policy,
        )
        rows.append(
            ResultRow(
                strategy=strategy,
                structure="debit_vertical",
                symbol=spread.symbol,
                report_date=spread.report_date,
                post_day=spread.post_day,
                execution_model=spread.execution_model,
                direction=spread.direction,
                status=spread.status,
                skip_reason=spread.skip_reason,
                entry_day=spread.entry_day,
                entry_time_et=spread.entry_time_et,
                exit_day=spread.exit_day,
                exit_time_et=spread.exit_time_et,
                expiration_date=spread.expiration_date,
                long_contract=spread.long_contract,
                long_strike=spread.long_strike,
                short_contract=spread.short_contract,
                short_strike=spread.short_strike,
                underlying_entry_spot=spread.underlying_entry_spot,
                signal_detail=detail,
                entry_value=spread.entry_value,
                exit_value=spread.exit_value,
                max_risk_dollars=spread.max_risk_dollars,
                fees_dollars=spread.fees_dollars,
                pnl_dollars=spread.pnl_dollars,
                return_on_risk_pct=spread.return_on_risk_pct,
            )
        )


def simulate(
    events: Sequence[EarningsEvent],
    next_week: Archive,
    immediate: Archive,
) -> list[ResultRow]:
    rows: list[ResultRow] = []
    for event in events:
        or_signal = opening_range_signal(next_week, event)
        gap_signal_raw = gap_hold_signal(next_week, event)
        gap_signal = (gap_signal_raw[0], gap_signal_raw[1]) if gap_signal_raw else None
        delayed_signal = delayed_close_signal(next_week, event)
        fade_25_signal = gap_fade_signal(next_week, event, 2.0, 0.25)
        fade_50_signal = gap_fade_signal(next_week, event, 2.0, 0.50)
        closing_reversal_signal = closing_gap_reversal_signal(next_week, event, 2.0)
        retest_signal = gap_retest_signal(next_week, event)
        momentum = momentum_5d(next_week, event.symbol, event.report_date)
        for execution in EXECUTION_MODELS:
            append_directional_variants(
                rows, event, next_week, execution,
                "opening_range", or_signal, event.next_trading_day,
                "two-close first-hour acceptance",
            )
            append_directional_variants(
                rows, event, next_week, execution,
                "gap_hold", gap_signal, event.next_trading_day,
                "1% gap hold",
            )
            delayed_entry_day = next_session(next_week, event.symbol, event.next_trading_day)
            append_directional_variants(
                rows, event, next_week, execution,
                "delayed_close", delayed_signal,
                delayed_entry_day or event.next_trading_day,
                "full-session continuation",
            )
            append_directional_variants(
                rows, event, next_week, execution,
                "gap_fade25", fade_25_signal, event.next_trading_day,
                "2% gap with 25% first-hour retracement",
            )
            append_directional_variants(
                rows, event, next_week, execution,
                "gap_fade50", fade_50_signal, event.next_trading_day,
                "2% gap with 50% first-hour retracement",
            )
            reversal_entry_day = next_session(next_week, event.symbol, event.next_trading_day)
            append_directional_variants(
                rows, event, next_week, execution,
                "closing_gap_reversal", closing_reversal_signal,
                reversal_entry_day or event.next_trading_day,
                "full-session rejection of 2% earnings gap",
            )
            append_directional_variants(
                rows, event, next_week, execution,
                "gap_retest", retest_signal, event.next_trading_day,
                "gap breakout and retest",
            )

            credit_directions = {
                "put_credit": "bullish",
                "call_credit": "bearish",
            }
            if momentum is not None and abs(momentum) >= 1.0:
                credit_directions["momentum_credit"] = "bullish" if momentum > 0 else "bearish"
            for label, direction in credit_directions.items():
                for multiplier in (1.0, 1.25):
                    for exit_label, exit_minute in (("0935", 575), ("1030", 630)):
                        rows.append(
                            credit_spread_trade(
                                strategy=f"{label}_{multiplier:.2f}x_exit_{exit_label}",
                                event=event,
                                archive=immediate,
                                execution=execution,
                                direction=direction,
                                move_multiplier=multiplier,
                                exit_minute=exit_minute,
                            )
                        )
            if momentum is None or abs(momentum) < 1.0:
                for multiplier in (1.0, 1.25):
                    for exit_label in ("0935", "1030"):
                        rows.append(
                            empty_row(
                                f"momentum_credit_{multiplier:.2f}x_exit_{exit_label}",
                                "credit_vertical", event, execution, "none",
                                "five-session momentum below 1%",
                            )
                        )
    return rows


def profit_factor(values: Sequence[float]) -> float | None:
    gains = sum(value for value in values if value > 0)
    losses = -sum(value for value in values if value < 0)
    if losses > 0:
        return gains / losses
    return math.inf if gains > 0 else None


def summarize(rows: Sequence[ResultRow], events: Sequence[EarningsEvent]) -> list[SummaryRow]:
    keys = sorted({(row.strategy, row.structure) for row in rows})
    scopes = ["ALL", "MSFT", "META", "AAPL", "AMZN"]
    results: list[SummaryRow] = []
    for strategy, structure in keys:
        for model in (execution.name for execution in EXECUTION_MODELS):
            for scope in scopes:
                eligible_events = len(events) if scope == "ALL" else sum(
                    event.symbol == scope for event in events
                )
                subset = [
                    row for row in rows
                    if row.strategy == strategy
                    and row.structure == structure
                    and row.execution_model == model
                    and (scope == "ALL" or row.symbol == scope)
                    and row.status == "ok"
                    and row.return_on_risk_pct is not None
                    and row.pnl_dollars is not None
                ]
                returns = [float(row.return_on_risk_pct) for row in subset]
                pnls = [float(row.pnl_dollars) for row in subset]
                ex_best = mean(sorted(returns)[:-1]) if len(returns) > 1 else None
                robust = bool(
                    len(returns) >= (12 if scope == "ALL" else 4)
                    and mean(returns) > 0
                    and median(returns) > 0
                    and ex_best is not None and ex_best > 0
                )
                results.append(
                    SummaryRow(
                        strategy=strategy,
                        structure=structure,
                        scope=scope,
                        execution_model=model,
                        event_n=eligible_events,
                        executed_n=len(returns),
                        coverage_pct=(len(returns) / eligible_events * 100.0) if eligible_events else 0.0,
                        mean_return_pct=mean(returns) if returns else None,
                        median_return_pct=median(returns) if returns else None,
                        win_rate_pct=(sum(value > 0 for value in returns) / len(returns) * 100.0) if returns else None,
                        total_pnl_dollars=sum(pnls) if pnls else None,
                        profit_factor=profit_factor(pnls),
                        exclude_best_1_mean_pct=ex_best,
                        best_return_pct=max(returns) if returns else None,
                        worst_return_pct=min(returns) if returns else None,
                        robust_positive=robust,
                    )
                )
    return results


def fmt(value: float | None, suffix: str = "") -> str:
    if value is None:
        return "—"
    if math.isinf(value):
        return "∞"
    return f"{value:.2f}{suffix}"


def write_report(output: Path, rows: Sequence[ResultRow], summaries: Sequence[SummaryRow]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    with (output / "trade_log.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(asdict(rows[0]).keys()))
        writer.writeheader()
        writer.writerows(asdict(row) for row in rows)
    with (output / "variant_results.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(asdict(summaries[0]).keys()))
        writer.writeheader()
        writer.writerows(asdict(row) for row in summaries)

    base = [row for row in summaries if row.execution_model == "base" and row.scope == "ALL"]
    base.sort(key=lambda row: row.mean_return_pct if row.mean_return_pct is not None else -math.inf, reverse=True)
    lines = [
        "# Robinhood-Compatible Earnings Options Lab",
        "",
        "No butterflies or iron condors. Historical one-minute OPRA trade bars; conservative fills.",
        "",
        "## Aggregate base ranking",
        "",
        "| Rank | Strategy | Structure | N | Coverage | Mean ROR | Median | Win rate | Ex-best-1 | Worst |",
        "|---:|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for index, row in enumerate(base, 1):
        lines.append(
            f"| {index} | {row.strategy} | {row.structure} | {row.executed_n} | "
            f"{row.coverage_pct:.1f}% | {fmt(row.mean_return_pct, '%')} | "
            f"{fmt(row.median_return_pct, '%')} | {fmt(row.win_rate_pct, '%')} | "
            f"{fmt(row.exclude_best_1_mean_pct, '%')} | {fmt(row.worst_return_pct, '%')} |"
        )

    lines.extend(["", "## Best base result by ticker and structure", ""])
    lines.append("| Ticker | Structure | Strategy | N | Mean | Median | Win rate | Ex-best-1 | Worse-fill mean |")
    lines.append("|---|---|---|---:|---:|---:|---:|---:|---:|")
    lookup = {(row.strategy, row.structure, row.scope, row.execution_model): row for row in summaries}
    for symbol in ("MSFT", "META", "AAPL", "AMZN"):
        for structure in ("single_leg", "debit_vertical", "credit_vertical"):
            choices = [
                row for row in summaries
                if row.scope == symbol and row.structure == structure
                and row.execution_model == "base" and row.executed_n > 0
            ]
            if not choices:
                continue
            best = max(
                choices,
                key=lambda row: (
                    row.robust_positive,
                    row.exclude_best_1_mean_pct if row.exclude_best_1_mean_pct is not None else -math.inf,
                    row.mean_return_pct if row.mean_return_pct is not None else -math.inf,
                    row.executed_n,
                ),
            )
            worse = lookup.get((best.strategy, best.structure, symbol, "worse"))
            lines.append(
                f"| {symbol} | {structure} | {best.strategy} | {best.executed_n} | "
                f"{fmt(best.mean_return_pct, '%')} | {fmt(best.median_return_pct, '%')} | "
                f"{fmt(best.win_rate_pct, '%')} | {fmt(best.exclude_best_1_mean_pct, '%')} | "
                f"{fmt(worse.mean_return_pct if worse else None, '%')} |"
            )

    lines.extend([
        "",
        "## Interpretation limits",
        "",
        "- Six earnings per ticker is a small descriptive sample.",
        "- Historical NBBO is unavailable; minute-bar extremes are conservative trade-price proxies.",
        "- Credit verticals require spread approval. Single-leg rows require only long-option approval.",
        "- Missing signals are deliberate no-trades, not zero-return observations.",
        "- Nothing in this lab submits or simulates a brokerage order.",
    ])
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    payload = {
        "rows": len(rows),
        "executed_rows": sum(row.status == "ok" for row in rows),
        "summaries": [asdict(row) for row in summaries],
    }
    (output / "report.json").write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def run(args: argparse.Namespace) -> dict[str, object]:
    events = parse_events(Path(args.events))
    next_week = Archive([Path(args.next_week_db)])
    immediate = Archive([Path(args.aapl_immediate_db), Path(args.other_immediate_db)])
    try:
        rows = simulate(events, next_week, immediate)
        summaries = summarize(rows, events)
        output = Path(args.output)
        write_report(output, rows, summaries)
        return {
            "events": len(events),
            "rows": len(rows),
            "executed_rows": sum(row.status == "ok" for row in rows),
            "strategies": len({(row.strategy, row.structure) for row in rows}),
            "output": str(output),
        }
    finally:
        next_week.close()
        immediate.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Backtest Robinhood-compatible earnings structures")
    parser.add_argument("--events", default=str(DEFAULT_EVENTS))
    parser.add_argument("--next-week-db", default=str(DEFAULT_NEXT_WEEK_DB))
    parser.add_argument("--aapl-immediate-db", default=str(DEFAULT_AAPL_CONDOR_DB))
    parser.add_argument("--other-immediate-db", default=str(DEFAULT_OTHER_CONDOR_DB))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    return parser


def main() -> int:
    summary = run(build_parser().parse_args())
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
