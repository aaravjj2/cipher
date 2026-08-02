"""Advanced Robinhood-compatible earnings technique sweep.

This lab expands the four-name earnings study without butterflies, condors,
straddles, strangles, ratio spreads, naked options, or order routing. It tests:

* 15-minute opening-range acceptance;
* gap/VWAP continuation and VWAP reclaim reversals;
* opening-drive exhaustion fades;
* power-hour continuation and overnight follow-through;
* pre-earnings momentum and contrarian long options/debit verticals;
* pre-earnings one-sided credit verticals across wider implied-move buffers.

The first three chronological earnings per ticker are treated as exploration and
the last three as validation. Historical NBBO is unavailable; fills use
conservative one-minute OPRA trade-bar extremes. Research only.
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
        find_bar,
        minute_clock,
        next_session,
        parse_events,
        prior_close,
        same_expiry_pool,
        underlying_spot,
    )
except ImportError:
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
        find_bar,
        minute_clock,
        next_session,
        parse_events,
        prior_close,
        same_expiry_pool,
        underlying_spot,
    )


CORE = Path(__file__).resolve().parent
ROOT = CORE.parent
DEFAULT_OUTPUT = ROOT / "data" / "earnings_advanced_technique_lab"


@dataclass(slots=True)
class Trade:
    strategy: str
    family: str
    structure: str
    symbol: str
    report_date: str
    post_day: str
    split: str
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
class Summary:
    strategy: str
    family: str
    structure: str
    symbol: str
    split: str
    execution_model: str
    event_n: int
    executed_n: int
    coverage_pct: float
    mean_return_pct: float | None
    median_return_pct: float | None
    win_rate_pct: float | None
    exclude_best_1_mean_pct: float | None
    total_pnl_dollars: float | None
    profit_factor: float | None
    best_return_pct: float | None
    worst_return_pct: float | None
    passed_validation: bool


def split_map(events: Sequence[EarningsEvent]) -> dict[tuple[str, str], str]:
    output: dict[tuple[str, str], str] = {}
    for symbol in sorted({event.symbol for event in events}):
        ordered = sorted((event for event in events if event.symbol == symbol), key=lambda row: row.report_date)
        cutoff = len(ordered) // 2
        for index, event in enumerate(ordered):
            output[(event.symbol, event.report_date)] = "explore" if index < cutoff else "validate"
    return output


def empty_trade(
    strategy: str,
    family: str,
    structure: str,
    event: EarningsEvent,
    split: str,
    execution: ExecutionModel,
    direction: str,
    reason: str,
    detail: str,
) -> Trade:
    return Trade(
        strategy=strategy,
        family=family,
        structure=structure,
        symbol=event.symbol,
        report_date=event.report_date,
        post_day=event.next_trading_day,
        split=split,
        execution_model=execution.name,
        direction=direction,
        status="skipped",
        skip_reason=reason,
        signal_detail=detail,
    )


def daily_momentum(archive: Archive, symbol: str, report_day: str, lookback: int) -> float | None:
    values = [value for day, value in archive.daily_closes(symbol) if day < report_day]
    if len(values) < lookback or values[-lookback] <= 0:
        return None
    return (values[-1] / values[-lookback] - 1.0) * 100.0


def cumulative_vwap(
    bars: dict[int, tuple[float, float, float, float, float]],
    end_minute: int,
) -> float | None:
    numerator = 0.0
    denominator = 0.0
    for minute in range(570, end_minute + 1):
        bar = bars.get(minute)
        if bar is None:
            continue
        typical = (bar[1] + bar[2] + bar[3]) / 3.0
        volume = max(0.0, bar[4])
        numerator += typical * volume
        denominator += volume
    return numerator / denominator if denominator > 0 else None


def opening_range_15_signal(archive: Archive, event: EarningsEvent) -> tuple[str, int] | None:
    bars = archive.underlying_bars(event.symbol, event.next_trading_day)
    opening = [bars[m] for m in range(570, 585) if m in bars]
    if len(opening) < 12:
        return None
    high = max(row[1] for row in opening)
    low = min(row[2] for row in opening)
    prior: str | None = None
    for close_minute in range(589, 690, 5):
        bar = bars.get(close_minute)
        if bar is None:
            prior = None
            continue
        direction = "bullish" if bar[3] > high else "bearish" if bar[3] < low else None
        if direction is not None and direction == prior:
            return direction, close_minute + 1
        prior = direction
    return None


def gap_vwap_continuation_signal(
    archive: Archive,
    event: EarningsEvent,
    confirm_minute: int,
    minimum_gap: float = 1.0,
) -> tuple[str, int] | None:
    bars = archive.underlying_bars(event.symbol, event.next_trading_day)
    reference = prior_close(archive, event.symbol, event.report_date)
    open_bar = bars.get(570)
    confirm = bars.get(confirm_minute - 1)
    vwap = cumulative_vwap(bars, confirm_minute - 1)
    if reference is None or reference <= 0 or open_bar is None or confirm is None or vwap is None:
        return None
    session_open = open_bar[0]
    close = confirm[3]
    gap = (session_open / reference - 1.0) * 100.0
    if gap >= minimum_gap and close > session_open and close > vwap:
        return "bullish", confirm_minute
    if gap <= -minimum_gap and close < session_open and close < vwap:
        return "bearish", confirm_minute
    return None


def gap_vwap_reclaim_signal(
    archive: Archive,
    event: EarningsEvent,
    confirm_minute: int,
    minimum_gap: float = 2.0,
) -> tuple[str, int] | None:
    bars = archive.underlying_bars(event.symbol, event.next_trading_day)
    reference = prior_close(archive, event.symbol, event.report_date)
    open_bar = bars.get(570)
    confirm = bars.get(confirm_minute - 1)
    vwap = cumulative_vwap(bars, confirm_minute - 1)
    if reference is None or reference <= 0 or open_bar is None or confirm is None or vwap is None:
        return None
    session_open = open_bar[0]
    close = confirm[3]
    gap = (session_open / reference - 1.0) * 100.0
    if gap >= minimum_gap and close < session_open and close < vwap:
        return "bearish", confirm_minute
    if gap <= -minimum_gap and close > session_open and close > vwap:
        return "bullish", confirm_minute
    return None


def opening_drive_exhaustion_signal(
    archive: Archive,
    event: EarningsEvent,
    minimum_gap: float = 2.0,
    extension_pct: float = 0.75,
) -> tuple[str, int] | None:
    bars = archive.underlying_bars(event.symbol, event.next_trading_day)
    reference = prior_close(archive, event.symbol, event.report_date)
    open_bar = bars.get(570)
    confirm = bars.get(599)
    first15 = [bars[m] for m in range(570, 585) if m in bars]
    if reference is None or reference <= 0 or open_bar is None or confirm is None or len(first15) < 12:
        return None
    session_open = open_bar[0]
    gap = (session_open / reference - 1.0) * 100.0
    high15 = max(row[1] for row in first15)
    low15 = min(row[2] for row in first15)
    close = confirm[3]
    if gap >= minimum_gap:
        extension = (high15 / session_open - 1.0) * 100.0
        if extension >= extension_pct and close < low15:
            return "bearish", 600
    if gap <= -minimum_gap:
        extension = (session_open / low15 - 1.0) * 100.0
        if extension >= extension_pct and close > high15:
            return "bullish", 600
    return None


def power_hour_break_signal(archive: Archive, event: EarningsEvent) -> tuple[str, int] | None:
    bars = archive.underlying_bars(event.symbol, event.next_trading_day)
    earlier = [bars[m] for m in range(570, 900) if m in bars]
    if len(earlier) < 280:
        return None
    high = max(row[1] for row in earlier)
    low = min(row[2] for row in earlier)
    prior: str | None = None
    for close_minute in range(904, 946, 5):
        bar = bars.get(close_minute)
        if bar is None:
            prior = None
            continue
        direction = "bullish" if bar[3] > high else "bearish" if bar[3] < low else None
        if direction is not None and direction == prior:
            return direction, close_minute + 1
        prior = direction
    return None


def full_session_followthrough_signal(archive: Archive, event: EarningsEvent) -> tuple[str, int] | None:
    bars = archive.underlying_bars(event.symbol, event.next_trading_day)
    opening = [bars[m] for m in range(570, 630) if m in bars]
    close_bar, _ = find_bar(bars, 955, forward=False)
    open_bar = bars.get(570)
    if len(opening) < 50 or close_bar is None or open_bar is None:
        return None
    high = max(row[1] for row in opening)
    low = min(row[2] for row in opening)
    close = close_bar[3]
    if close > high and close > open_bar[0]:
        return "bullish", 945
    if close < low and close < open_bar[0]:
        return "bearish", 945
    return None


def select_long_contract(
    archive: Archive,
    event: EarningsEvent,
    direction: str,
    spot: float,
    otm_pct: float,
) -> Contract | None:
    option_type = "call" if direction == "bullish" else "put"
    contracts = archive.contracts(event.report_date, event.symbol, option_type)
    expiry = choose_expiration(contracts, event.report_date, 8)
    pool = [row for row in contracts if row.expiration_date == expiry]
    if not pool:
        return None
    target = spot * (1.0 + otm_pct if direction == "bullish" else 1.0 - otm_pct)
    return min(pool, key=lambda row: (abs(row.strike - target), row.strike))


def select_debit_pair_custom(
    archive: Archive,
    event: EarningsEvent,
    direction: str,
    spot: float,
    long_otm_pct: float,
    width_pct: float,
) -> tuple[Contract, Contract] | None:
    long_leg = select_long_contract(archive, event, direction, spot, long_otm_pct)
    if long_leg is None:
        return None
    option_type = "call" if direction == "bullish" else "put"
    contracts = [
        row for row in archive.contracts(event.report_date, event.symbol, option_type)
        if row.expiration_date == long_leg.expiration_date
    ]
    target = long_leg.strike + spot * width_pct if direction == "bullish" else long_leg.strike - spot * width_pct
    eligible = [
        row for row in contracts
        if (row.strike > long_leg.strike if direction == "bullish" else row.strike < long_leg.strike)
    ]
    if not eligible:
        return None
    short_leg = min(eligible, key=lambda row: (abs(row.strike - target), abs(row.strike - long_leg.strike)))
    return long_leg, short_leg


def option_buy(
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


def option_sell(
    archive: Archive,
    contract: Contract,
    day: str,
    minute: int,
    execution: ExecutionModel,
    backward: bool,
) -> tuple[float, int] | None:
    bar, actual = find_bar(archive.option_bars(contract.symbol, day), minute, forward=not backward)
    if bar is None or actual is None:
        return None
    return execution.sell(bar[2]), actual


def long_option_trade(
    *,
    strategy: str,
    family: str,
    event: EarningsEvent,
    split: str,
    archive: Archive,
    execution: ExecutionModel,
    direction: str,
    entry_day: str,
    entry_minute: int,
    exit_day: str,
    exit_minute: int,
    otm_pct: float,
    detail: str,
) -> Trade:
    row = Trade(
        strategy=strategy,
        family=family,
        structure="single_leg",
        symbol=event.symbol,
        report_date=event.report_date,
        post_day=event.next_trading_day,
        split=split,
        execution_model=execution.name,
        direction=direction,
        status="skipped",
        entry_day=entry_day,
        entry_time_et=minute_clock(entry_minute),
        exit_day=exit_day,
        exit_time_et=minute_clock(exit_minute),
        signal_detail=detail,
    )
    spot = underlying_spot(archive, event.symbol, entry_day, entry_minute)
    row.underlying_entry_spot = spot
    if spot is None:
        row.skip_reason = "missing underlying entry"
        return row
    contract = select_long_contract(archive, event, direction, spot, otm_pct)
    if contract is None:
        row.skip_reason = "no selected long contract"
        return row
    row.expiration_date = contract.expiration_date
    row.long_contract = contract.symbol
    row.long_strike = contract.strike
    entry = option_buy(archive, contract, entry_day, entry_minute, execution)
    exit_obs = option_sell(archive, contract, exit_day, exit_minute, execution, backward=exit_minute >= 900)
    if entry is None or exit_obs is None:
        row.skip_reason = "missing option entry or exit"
        return row
    premium, actual_entry = entry
    exit_value, actual_exit = exit_obs
    if premium <= 0:
        row.skip_reason = "non-economic long premium"
        return row
    fees = 2.0 * execution.fee_per_leg
    risk = premium * 100.0 + execution.fee_per_leg
    pnl = (exit_value - premium) * 100.0 - fees
    row.status = "ok"
    row.entry_time_et = minute_clock(actual_entry)
    row.exit_time_et = minute_clock(actual_exit)
    row.entry_value = premium
    row.exit_value = exit_value
    row.max_risk_dollars = risk
    row.fees_dollars = fees
    row.pnl_dollars = pnl
    row.return_on_risk_pct = pnl / risk * 100.0
    return row


def debit_vertical_trade(
    *,
    strategy: str,
    family: str,
    event: EarningsEvent,
    split: str,
    archive: Archive,
    execution: ExecutionModel,
    direction: str,
    entry_day: str,
    entry_minute: int,
    exit_day: str,
    exit_minute: int,
    long_otm_pct: float,
    width_pct: float,
    detail: str,
) -> Trade:
    row = Trade(
        strategy=strategy,
        family=family,
        structure="debit_vertical",
        symbol=event.symbol,
        report_date=event.report_date,
        post_day=event.next_trading_day,
        split=split,
        execution_model=execution.name,
        direction=direction,
        status="skipped",
        entry_day=entry_day,
        entry_time_et=minute_clock(entry_minute),
        exit_day=exit_day,
        exit_time_et=minute_clock(exit_minute),
        signal_detail=detail,
    )
    spot = underlying_spot(archive, event.symbol, entry_day, entry_minute)
    row.underlying_entry_spot = spot
    if spot is None:
        row.skip_reason = "missing underlying entry"
        return row
    pair = select_debit_pair_custom(archive, event, direction, spot, long_otm_pct, width_pct)
    if pair is None:
        row.skip_reason = "no custom debit pair"
        return row
    long_leg, short_leg = pair
    row.expiration_date = long_leg.expiration_date
    row.long_contract, row.long_strike = long_leg.symbol, long_leg.strike
    row.short_contract, row.short_strike = short_leg.symbol, short_leg.strike
    long_entry, le_min = find_bar(archive.option_bars(long_leg.symbol, entry_day), entry_minute, forward=True)
    short_entry, se_min = find_bar(archive.option_bars(short_leg.symbol, entry_day), entry_minute, forward=True)
    long_exit, lx_min = find_bar(archive.option_bars(long_leg.symbol, exit_day), exit_minute, forward=exit_minute < 900)
    short_exit, sx_min = find_bar(archive.option_bars(short_leg.symbol, exit_day), exit_minute, forward=exit_minute < 900)
    if None in (long_entry, short_entry, long_exit, short_exit, le_min, se_min, lx_min, sx_min):
        row.skip_reason = "missing vertical leg observation"
        return row
    actual_entry = max(int(le_min), int(se_min))
    actual_exit = max(int(lx_min), int(sx_min)) if exit_minute < 900 else min(int(lx_min), int(sx_min))
    long_entry = archive.option_bars(long_leg.symbol, entry_day).get(actual_entry, long_entry)
    short_entry = archive.option_bars(short_leg.symbol, entry_day).get(actual_entry, short_entry)
    long_exit = archive.option_bars(long_leg.symbol, exit_day).get(actual_exit, long_exit)
    short_exit = archive.option_bars(short_leg.symbol, exit_day).get(actual_exit, short_exit)
    debit = execution.buy(long_entry[1]) - execution.sell(short_entry[2])
    width = abs(short_leg.strike - long_leg.strike)
    exit_value = execution.sell(long_exit[2]) - execution.buy(short_exit[1])
    exit_value = max(0.0, min(width, exit_value))
    if debit <= 0 or debit >= width:
        row.skip_reason = "non-economic debit reconstruction"
        return row
    fees = 4.0 * execution.fee_per_leg
    risk = debit * 100.0 + 2.0 * execution.fee_per_leg
    pnl = (exit_value - debit) * 100.0 - fees
    row.status = "ok"
    row.entry_time_et = minute_clock(actual_entry)
    row.exit_time_et = minute_clock(actual_exit)
    row.entry_value = debit
    row.exit_value = exit_value
    row.max_risk_dollars = risk
    row.fees_dollars = fees
    row.pnl_dollars = pnl
    row.return_on_risk_pct = pnl / risk * 100.0
    return row


def credit_vertical_trade(
    *,
    strategy: str,
    family: str,
    event: EarningsEvent,
    split: str,
    archive: Archive,
    execution: ExecutionModel,
    direction: str,
    entry_minute: int,
    move_multiplier: float,
    wing_width: float,
    exit_minute: int,
    detail: str,
) -> Trade:
    row = Trade(
        strategy=strategy,
        family=family,
        structure="credit_vertical",
        symbol=event.symbol,
        report_date=event.report_date,
        post_day=event.next_trading_day,
        split=split,
        execution_model=execution.name,
        direction=direction,
        status="skipped",
        entry_day=event.report_date,
        entry_time_et=minute_clock(entry_minute),
        exit_day=event.next_trading_day,
        exit_time_et=minute_clock(exit_minute),
        signal_detail=detail,
    )
    spot = underlying_spot(archive, event.symbol, event.report_date, entry_minute)
    row.underlying_entry_spot = spot
    pools = same_expiry_pool(archive, event)
    if spot is None or pools is None:
        row.skip_reason = "missing spot or immediate expiry"
        return row
    expiry, calls, puts = pools
    row.expiration_date = expiry
    atm_call = min(calls, key=lambda contract: abs(contract.strike - spot))
    atm_put = min(puts, key=lambda contract: abs(contract.strike - spot))
    call_bar, _ = find_bar(archive.option_bars(atm_call.symbol, event.report_date), entry_minute, forward=True)
    put_bar, _ = find_bar(archive.option_bars(atm_put.symbol, event.report_date), entry_minute, forward=True)
    if call_bar is None or put_bar is None:
        row.skip_reason = "missing straddle proxy"
        return row
    implied_move = call_bar[3] + put_bar[3]
    if direction == "bullish":
        boundary = spot - move_multiplier * implied_move
        shorts = [contract for contract in puts if contract.strike <= boundary]
        if not shorts:
            row.skip_reason = "no put short beyond boundary"
            return row
        short_leg = max(shorts, key=lambda contract: contract.strike)
        longs = [contract for contract in puts if contract.strike <= short_leg.strike - wing_width]
        if not longs:
            row.skip_reason = "no protective put"
            return row
        long_leg = max(longs, key=lambda contract: contract.strike)
    else:
        boundary = spot + move_multiplier * implied_move
        shorts = [contract for contract in calls if contract.strike >= boundary]
        if not shorts:
            row.skip_reason = "no call short beyond boundary"
            return row
        short_leg = min(shorts, key=lambda contract: contract.strike)
        longs = [contract for contract in calls if contract.strike >= short_leg.strike + wing_width]
        if not longs:
            row.skip_reason = "no protective call"
            return row
        long_leg = min(longs, key=lambda contract: contract.strike)
    row.long_contract, row.long_strike = long_leg.symbol, long_leg.strike
    row.short_contract, row.short_strike = short_leg.symbol, short_leg.strike
    short_entry, se_min = find_bar(archive.option_bars(short_leg.symbol, event.report_date), entry_minute, forward=True)
    long_entry, le_min = find_bar(archive.option_bars(long_leg.symbol, event.report_date), entry_minute, forward=True)
    short_exit, sx_min = find_bar(archive.option_bars(short_leg.symbol, event.next_trading_day), exit_minute, forward=True)
    long_exit, lx_min = find_bar(archive.option_bars(long_leg.symbol, event.next_trading_day), exit_minute, forward=True)
    if None in (short_entry, long_entry, short_exit, long_exit, se_min, le_min, sx_min, lx_min):
        row.skip_reason = "missing credit leg observation"
        return row
    actual_entry = max(int(se_min), int(le_min))
    actual_exit = max(int(sx_min), int(lx_min))
    short_entry = archive.option_bars(short_leg.symbol, event.report_date).get(actual_entry, short_entry)
    long_entry = archive.option_bars(long_leg.symbol, event.report_date).get(actual_entry, long_entry)
    short_exit = archive.option_bars(short_leg.symbol, event.next_trading_day).get(actual_exit, short_exit)
    long_exit = archive.option_bars(long_leg.symbol, event.next_trading_day).get(actual_exit, long_exit)
    credit = execution.sell(short_entry[2]) - execution.buy(long_entry[1])
    width = abs(short_leg.strike - long_leg.strike)
    debit = execution.buy(short_exit[1]) - execution.sell(long_exit[2])
    debit = max(0.0, min(width, debit))
    if credit <= 0 or credit >= width:
        row.skip_reason = "non-economic credit reconstruction"
        return row
    fees = 4.0 * execution.fee_per_leg
    risk = (width - credit) * 100.0 + 2.0 * execution.fee_per_leg
    pnl = (credit - debit) * 100.0 - fees
    row.status = "ok"
    row.entry_time_et = minute_clock(actual_entry)
    row.exit_time_et = minute_clock(actual_exit)
    row.entry_value = credit
    row.exit_value = debit
    row.max_risk_dollars = risk
    row.fees_dollars = fees
    row.pnl_dollars = pnl
    row.return_on_risk_pct = pnl / risk * 100.0
    return row


def append_signal_family(
    rows: list[Trade],
    *,
    event: EarningsEvent,
    split: str,
    next_week: Archive,
    execution: ExecutionModel,
    family: str,
    signal: tuple[str, int] | None,
    entry_day: str,
    exit_specs: Sequence[tuple[str, str, int]],
    detail: str,
) -> None:
    for exit_label, exit_day, exit_minute in exit_specs:
        for otm in (0.0, 0.01):
            strategy = f"{family}_long_{int(otm*100)}otm_{exit_label}"
            if signal is None:
                rows.append(empty_trade(strategy, family, "single_leg", event, split, execution, "none", "signal absent", detail))
            else:
                direction, entry_minute = signal
                rows.append(long_option_trade(
                    strategy=strategy,
                    family=family,
                    event=event,
                    split=split,
                    archive=next_week,
                    execution=execution,
                    direction=direction,
                    entry_day=entry_day,
                    entry_minute=entry_minute,
                    exit_day=exit_day,
                    exit_minute=exit_minute,
                    otm_pct=otm,
                    detail=detail,
                ))
        for long_otm, width in ((0.0, 0.015), (0.01, 0.02)):
            strategy = f"{family}_debit_{int(long_otm*100)}otm_w{int(width*1000)}_{exit_label}"
            if signal is None:
                rows.append(empty_trade(strategy, family, "debit_vertical", event, split, execution, "none", "signal absent", detail))
            else:
                direction, entry_minute = signal
                rows.append(debit_vertical_trade(
                    strategy=strategy,
                    family=family,
                    event=event,
                    split=split,
                    archive=next_week,
                    execution=execution,
                    direction=direction,
                    entry_day=entry_day,
                    entry_minute=entry_minute,
                    exit_day=exit_day,
                    exit_minute=exit_minute,
                    long_otm_pct=long_otm,
                    width_pct=width,
                    detail=detail,
                ))


def simulate(events: Sequence[EarningsEvent], next_week: Archive, immediate: Archive) -> list[Trade]:
    rows: list[Trade] = []
    splits = split_map(events)
    for event in events:
        split = splits[(event.symbol, event.report_date)]
        next_post = next_session(next_week, event.symbol, event.next_trading_day)
        signals = {
            "or15": (opening_range_15_signal(next_week, event), event.next_trading_day, "15-minute opening range; two five-minute closes"),
            "gap_vwap_1000": (gap_vwap_continuation_signal(next_week, event, 600), event.next_trading_day, "1% gap continuation above/below cumulative VWAP at 10:00"),
            "gap_vwap_1030": (gap_vwap_continuation_signal(next_week, event, 630), event.next_trading_day, "1% gap continuation above/below cumulative VWAP at 10:30"),
            "vwap_reclaim_1000": (gap_vwap_reclaim_signal(next_week, event, 600), event.next_trading_day, "2% gap reclaimed/rejected through VWAP at 10:00"),
            "vwap_reclaim_1030": (gap_vwap_reclaim_signal(next_week, event, 630), event.next_trading_day, "2% gap reclaimed/rejected through VWAP at 10:30"),
            "opening_exhaustion": (opening_drive_exhaustion_signal(next_week, event), event.next_trading_day, "2% gap, 0.75% opening extension, then 30-minute reversal"),
            "power_hour": (power_hour_break_signal(next_week, event), event.next_trading_day, "two power-hour closes beyond pre-3PM range"),
        }
        follow = full_session_followthrough_signal(next_week, event)
        if next_post is not None:
            signals["overnight_follow"] = (follow, event.next_trading_day, "full-session close outside first-hour range; enter 15:45")

        for execution in EXECUTION_MODELS:
            for family, (signal, entry_day, detail) in signals.items():
                if family == "overnight_follow":
                    exits = (
                        ("next_open", next_post or event.next_trading_day, 575),
                        ("next_1030", next_post or event.next_trading_day, 630),
                        ("next_close", next_post or event.next_trading_day, 955),
                    )
                elif family == "power_hour":
                    exits = (
                        ("same_close", event.next_trading_day, 955),
                        ("next_open", next_post or event.next_trading_day, 575),
                        ("next_1030", next_post or event.next_trading_day, 630),
                    )
                else:
                    exits = (
                        ("same_close", event.next_trading_day, 955),
                        ("next_close", next_post or event.next_trading_day, 955),
                    )
                append_signal_family(
                    rows,
                    event=event,
                    split=split,
                    next_week=next_week,
                    execution=execution,
                    family=family,
                    signal=signal,
                    entry_day=entry_day,
                    exit_specs=exits,
                    detail=detail,
                )

            for lookback in (3, 5, 10):
                momentum = daily_momentum(next_week, event.symbol, event.report_date, lookback)
                for threshold in (0.5, 1.0, 2.0):
                    for mode in ("trend", "contrarian"):
                        family = f"pre_{mode}_m{lookback}_t{str(threshold).replace('.', '')}"
                        if momentum is None or abs(momentum) < threshold:
                            direction = "none"
                        else:
                            trend_direction = "bullish" if momentum > 0 else "bearish"
                            direction = trend_direction if mode == "trend" else ("bearish" if trend_direction == "bullish" else "bullish")
                        detail = f"{lookback}-session momentum {mode}, threshold {threshold:.1f}%"
                        for exit_label, exit_minute in (("0935", 575), ("1030", 630), ("close", 955)):
                            for otm in (0.0, 0.01):
                                strategy = f"{family}_long_{int(otm*100)}otm_{exit_label}"
                                if direction == "none":
                                    rows.append(empty_trade(strategy, family, "single_leg", event, split, execution, direction, "momentum threshold absent", detail))
                                else:
                                    rows.append(long_option_trade(
                                        strategy=strategy,
                                        family=family,
                                        event=event,
                                        split=split,
                                        archive=next_week,
                                        execution=execution,
                                        direction=direction,
                                        entry_day=event.report_date,
                                        entry_minute=945,
                                        exit_day=event.next_trading_day,
                                        exit_minute=exit_minute,
                                        otm_pct=otm,
                                        detail=detail,
                                    ))
                            for long_otm, width in ((0.0, 0.015), (0.01, 0.02)):
                                strategy = f"{family}_debit_{int(long_otm*100)}otm_w{int(width*1000)}_{exit_label}"
                                if direction == "none":
                                    rows.append(empty_trade(strategy, family, "debit_vertical", event, split, execution, direction, "momentum threshold absent", detail))
                                else:
                                    rows.append(debit_vertical_trade(
                                        strategy=strategy,
                                        family=family,
                                        event=event,
                                        split=split,
                                        archive=next_week,
                                        execution=execution,
                                        direction=direction,
                                        entry_day=event.report_date,
                                        entry_minute=945,
                                        exit_day=event.next_trading_day,
                                        exit_minute=exit_minute,
                                        long_otm_pct=long_otm,
                                        width_pct=width,
                                        detail=detail,
                                    ))

                        for move in (0.75, 1.0, 1.25, 1.5):
                            for wing in (5.0, 10.0):
                                for exit_label, exit_minute in (("0935", 575), ("1000", 600), ("1030", 630), ("1100", 660)):
                                    strategy = f"{family}_credit_{move:.2f}x_w{int(wing)}_{exit_label}"
                                    if direction == "none":
                                        rows.append(empty_trade(strategy, family, "credit_vertical", event, split, execution, direction, "momentum threshold absent", detail))
                                    else:
                                        rows.append(credit_vertical_trade(
                                            strategy=strategy,
                                            family=family,
                                            event=event,
                                            split=split,
                                            archive=immediate,
                                            execution=execution,
                                            direction=direction,
                                            entry_minute=945,
                                            move_multiplier=move,
                                            wing_width=wing,
                                            exit_minute=exit_minute,
                                            detail=detail,
                                        ))
    return rows


def profit_factor(values: Sequence[float]) -> float | None:
    gains = sum(value for value in values if value > 0)
    losses = -sum(value for value in values if value < 0)
    if losses > 0:
        return gains / losses
    return math.inf if gains > 0 else None


def summarize(rows: Sequence[Trade], events: Sequence[EarningsEvent]) -> list[Summary]:
    output: list[Summary] = []
    strategies = sorted({(row.strategy, row.family, row.structure) for row in rows})
    symbols = sorted({event.symbol for event in events})
    for strategy, family, structure in strategies:
        for symbol in symbols:
            for split in ("explore", "validate", "all"):
                event_n = sum(
                    event.symbol == symbol
                    and (split == "all" or split_map(events)[(event.symbol, event.report_date)] == split)
                    for event in events
                )
                for model in (execution.name for execution in EXECUTION_MODELS):
                    subset = [
                        row for row in rows
                        if row.strategy == strategy
                        and row.family == family
                        and row.structure == structure
                        and row.symbol == symbol
                        and row.execution_model == model
                        and (split == "all" or row.split == split)
                        and row.status == "ok"
                        and row.return_on_risk_pct is not None
                        and row.pnl_dollars is not None
                    ]
                    returns = [float(row.return_on_risk_pct) for row in subset]
                    pnls = [float(row.pnl_dollars) for row in subset]
                    ex_best = mean(sorted(returns)[:-1]) if len(returns) > 1 else None
                    output.append(Summary(
                        strategy=strategy,
                        family=family,
                        structure=structure,
                        symbol=symbol,
                        split=split,
                        execution_model=model,
                        event_n=event_n,
                        executed_n=len(returns),
                        coverage_pct=(len(returns) / event_n * 100.0) if event_n else 0.0,
                        mean_return_pct=mean(returns) if returns else None,
                        median_return_pct=median(returns) if returns else None,
                        win_rate_pct=(sum(value > 0 for value in returns) / len(returns) * 100.0) if returns else None,
                        exclude_best_1_mean_pct=ex_best,
                        total_pnl_dollars=sum(pnls) if pnls else None,
                        profit_factor=profit_factor(pnls),
                        best_return_pct=max(returns) if returns else None,
                        worst_return_pct=min(returns) if returns else None,
                        passed_validation=False,
                    ))
    lookup = {
        (row.strategy, row.structure, row.symbol, row.split, row.execution_model): row
        for row in output
    }
    for row in output:
        if row.split != "validate" or row.execution_model != "base":
            continue
        validation_worse = lookup.get(
            (row.strategy, row.structure, row.symbol, "validate", "worse")
        )
        validation_severe = lookup.get(
            (row.strategy, row.structure, row.symbol, "validate", "severe")
        )
        exploration_base = lookup.get(
            (row.strategy, row.structure, row.symbol, "explore", "base")
        )
        exploration_worse = lookup.get(
            (row.strategy, row.structure, row.symbol, "explore", "worse")
        )
        exploration_severe = lookup.get(
            (row.strategy, row.structure, row.symbol, "explore", "severe")
        )
        required = (
            validation_worse,
            validation_severe,
            exploration_base,
            exploration_worse,
            exploration_severe,
        )
        row.passed_validation = bool(
            row.executed_n == 3
            and row.mean_return_pct is not None and row.mean_return_pct > 0
            and row.median_return_pct is not None and row.median_return_pct > 0
            and row.worst_return_pct is not None and row.worst_return_pct > -20
            and all(candidate is not None for candidate in required)
            and all(candidate.executed_n == 3 for candidate in required if candidate is not None)
            and all(
                candidate.mean_return_pct is not None and candidate.mean_return_pct > 0
                for candidate in required if candidate is not None
            )
            and exploration_base is not None
            and exploration_base.worst_return_pct is not None
            and exploration_base.worst_return_pct > -20
        )
    return output


def fmt(value: float | None, suffix: str = "") -> str:
    if value is None:
        return "—"
    if math.isinf(value):
        return "∞"
    return f"{value:.2f}{suffix}"


def write_outputs(output: Path, trades: Sequence[Trade], summaries: Sequence[Summary]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    with (output / "trade_log.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(asdict(trades[0]).keys()))
        writer.writeheader()
        writer.writerows(asdict(row) for row in trades)
    with (output / "summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(asdict(summaries[0]).keys()))
        writer.writeheader()
        writer.writerows(asdict(row) for row in summaries)

    lookup = {
        (row.strategy, row.structure, row.symbol, row.split, row.execution_model): row
        for row in summaries
    }
    passed = [
        row for row in summaries
        if row.split == "validate" and row.execution_model == "base" and row.passed_validation
    ]
    passed.sort(key=lambda row: (row.symbol, -(row.mean_return_pct or -math.inf), row.strategy))
    lines = [
        "# Advanced Earnings Technique Sweep",
        "",
        "Chronological split: first three events per ticker explore; last three validate.",
        "No butterflies, condors, straddles, strangles, naked options, or order routing.",
        "",
        "## Strict validation survivors",
        "",
        "| Ticker | Strategy | Structure | Validation N | Validation mean | Median | Win rate | Worst | Worse-fill mean | Explore mean |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in passed:
        worse = lookup.get((row.strategy, row.structure, row.symbol, "validate", "worse"))
        explore = lookup.get((row.strategy, row.structure, row.symbol, "explore", "base"))
        lines.append(
            f"| {row.symbol} | {row.strategy} | {row.structure} | {row.executed_n} | "
            f"{fmt(row.mean_return_pct, '%')} | {fmt(row.median_return_pct, '%')} | "
            f"{fmt(row.win_rate_pct, '%')} | {fmt(row.worst_return_pct, '%')} | "
            f"{fmt(worse.mean_return_pct if worse else None, '%')} | "
            f"{fmt(explore.mean_return_pct if explore else None, '%')} |"
        )
    if not passed:
        lines.append("| — | No strategy passed | — | — | — | — | — | — | — | — |")

    lines.extend([
        "",
        "## Best validation result per ticker and structure",
        "",
        "| Ticker | Structure | Strategy | N | Mean | Median | Win rate | Worst | Worse-fill mean | Explore mean | Pass |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|:---:|",
    ])
    for symbol in ("MSFT", "META", "AAPL", "AMZN"):
        for structure in ("single_leg", "debit_vertical", "credit_vertical"):
            candidates = [
                row for row in summaries
                if row.symbol == symbol and row.structure == structure
                and row.split == "validate" and row.execution_model == "base"
                and row.executed_n >= 1 and row.mean_return_pct is not None
            ]
            if not candidates:
                continue
            best = max(candidates, key=lambda row: (row.mean_return_pct or -math.inf, row.executed_n))
            worse = lookup.get((best.strategy, best.structure, symbol, "validate", "worse"))
            explore = lookup.get((best.strategy, best.structure, symbol, "explore", "base"))
            lines.append(
                f"| {symbol} | {structure} | {best.strategy} | {best.executed_n} | "
                f"{fmt(best.mean_return_pct, '%')} | {fmt(best.median_return_pct, '%')} | "
                f"{fmt(best.win_rate_pct, '%')} | {fmt(best.worst_return_pct, '%')} | "
                f"{fmt(worse.mean_return_pct if worse else None, '%')} | "
                f"{fmt(explore.mean_return_pct if explore else None, '%')} | "
                f"{'YES' if best.passed_validation else 'NO'} |"
            )

    lines.extend([
        "",
        "## Method limits",
        "",
        "- Three validation events per ticker is still extremely small.",
        "- Hundreds of variants create multiple-testing risk; the strict pass requires all six events and positive base, worse, and severe execution in both chronological halves.",
        "- Historical NBBO is unavailable; trade-bar extremes are conservative execution proxies.",
        "- Missing signals are no-trades, not zero-return observations.",
        "- Survivors remain paper-research candidates, not deployment-ready systems.",
    ])
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (output / "report.json").write_text(json.dumps({
        "trade_rows": len(trades),
        "strategies": len({row.strategy for row in trades}),
        "validation_survivors": [asdict(row) for row in passed],
    }, indent=2, sort_keys=True), encoding="utf-8")


def run(args: argparse.Namespace) -> dict[str, object]:
    events = parse_events(Path(args.events))
    next_week = Archive([Path(args.next_week_db)])
    immediate = Archive([Path(args.aapl_immediate_db), Path(args.other_immediate_db)])
    try:
        trades = simulate(events, next_week, immediate)
        summaries = summarize(trades, events)
    finally:
        next_week.close()
        immediate.close()
    output = Path(args.output)
    write_outputs(output, trades, summaries)
    survivors = [
        row for row in summaries
        if row.split == "validate" and row.execution_model == "base" and row.passed_validation
    ]
    return {
        "events": len(events),
        "trade_rows": len(trades),
        "strategies": len({row.strategy for row in trades}),
        "validation_survivors": len(survivors),
        "output": str(output.resolve()),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Advanced earnings technique sweep")
    parser.add_argument("--events", default=str(DEFAULT_EVENTS))
    parser.add_argument("--next-week-db", default=str(DEFAULT_NEXT_WEEK_DB))
    parser.add_argument("--aapl-immediate-db", default=str(DEFAULT_AAPL_CONDOR_DB))
    parser.add_argument("--other-immediate-db", default=str(DEFAULT_OTHER_CONDOR_DB))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    return parser


def main() -> int:
    result = run(build_parser().parse_args())
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
