#!/usr/bin/env python3
"""Point-in-time replay for the expiry-aware Cluster debit-spread strategy.

The replay uses persisted Cluster captures and Alpaca historical trade bars. It
never submits orders. Historical NBBO is unavailable, so option fills are trade-
print proxies and every result is reported under explicit execution stresses.
The strategy rules were designed after seeing this short sample; all outputs are
in-sample exploratory evidence, not an out-of-sample validation.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from statistics import mean, median, pstdev
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from core.research_platform.hashing import sha256_file, stable_id  # noqa: E402
from run_cipher_complete_observations import (  # noqa: E402
    OPTION_BARS_URL,
    OPTION_MINUTE_CACHE,
    STOCK_BARS_URL,
    STOCK_MINUTE_CACHE,
    load_or_fetch_partitioned_minute_bars,
    provider_headers,
)

UTC = timezone.utc
NY = ZoneInfo("America/New_York")
SOURCE = ROOT / "data" / "governance" / "cipher_signal_only" / "latest_cluster_individual_analysis.json"
OUT_ROOT = ROOT / "data" / "governance" / "cipher_strategy"
LATEST_JSON = OUT_ROOT / "latest_cluster_strategy_backtest.json"
LATEST_TRADES = OUT_ROOT / "latest_cluster_strategy_backtest_trades.csv"
LATEST_SCENARIOS = OUT_ROOT / "latest_cluster_strategy_backtest_scenarios.csv"
ARCHIVE_ROOT = OUT_ROOT / "cluster_strategy_backtests"


@dataclass(frozen=True)
class Scenario:
    name: str
    confirmation: str
    target_low: float
    target_high: float
    latest_entry_minute_et: int = 14 * 60 + 15
    minimum_full_sessions: int = 4
    entry_model: str = "within_window_trade_prints"
    fill_window_minutes: int = 5
    maximum_entry_lag_minutes: int | None = None
    stale_minutes: int = 15
    exit_fill_window_minutes: int = 120
    adverse_execution_fraction: float = 0.05
    fee_per_spread_round_trip: float = 2.60
    enforce_economics: bool = True
    exit_policy: str = "managed"
    starting_equity: float = 100_000.0
    risk_fraction: float = 0.0025
    maximum_positions: int = 2
    one_trade_per_scan: bool = True


@dataclass
class EntryProxy:
    valid: bool
    reason: str
    observed_at: datetime | None = None
    long_price: float | None = None
    short_price: float | None = None
    raw_debit: float | None = None
    stressed_debit: float | None = None
    width: float | None = None
    debit_width_fraction: float | None = None
    reward_risk: float | None = None
    breakeven: float | None = None
    long_volume: float | None = None
    short_volume: float | None = None
    print_gap_minutes: float | None = None
    simultaneous_minute: bool | None = None


@dataclass
class ExitProxy:
    valid: bool
    reason: str
    observed_at: datetime | None = None
    raw_value: float | None = None
    stressed_value: float | None = None
    trigger_at: datetime | None = None
    execution_lag_minutes: float | None = None
    proxy_quality: str | None = None
    finalized: bool = False


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    temporary.replace(path)


def finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def nested(row: Mapping[str, Any], *keys: str) -> Any:
    value: Any = row
    for key in keys:
        value = value.get(key) if isinstance(value, Mapping) else None
    return value


def utc(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def et_minute(value: datetime) -> int:
    local = value.astimezone(NY)
    return local.hour * 60 + local.minute


def tier_a(row: Mapping[str, Any] | None) -> bool:
    if not isinstance(row, Mapping):
        return False
    return (
        str(row.get("direction") or "").upper() == "BULLISH"
        and nested(row, "standalone_assessment", "research_tier") == "tier_a_cluster_only"
        and 1 <= int(row.get("rank") or 999) <= 10
        and 200.0 <= float(row.get("strength") or -1.0) < 300.0
        and bool(row.get("geometry_valid", True))
    )


def qualifies(row: Mapping[str, Any] | None, low: float, high: float) -> bool:
    room = finite(row.get("target_distance_pct")) if isinstance(row, Mapping) else None
    return tier_a(row) and room is not None and low <= room <= high


def business_sessions_after(start: str, expiry: str) -> int:
    first = pd.Timestamp(start) + pd.Timedelta(days=1)
    return len(pd.bdate_range(first, pd.Timestamp(expiry)))


def previous_business_day(day: str) -> str:
    return (pd.Timestamp(day) - pd.offsets.BDay(1)).date().isoformat()


def session_range(start: str, end: str) -> list[str]:
    return [value.date().isoformat() for value in pd.bdate_range(start, end)]


def load_source() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    payload = json.loads(SOURCE.read_text(encoding="utf-8"))
    records = [row for row in payload.get("records", []) if isinstance(row, dict)]
    return payload, records


def scan_maps(records: Sequence[Mapping[str, Any]]) -> tuple[
    dict[str, list[datetime]],
    dict[tuple[str, datetime], dict[str, Mapping[str, Any]]],
]:
    scans: dict[str, set[datetime]] = defaultdict(set)
    lookup: dict[tuple[str, datetime], dict[str, Mapping[str, Any]]] = defaultdict(dict)
    for row in records:
        session = str(row.get("market_session") or "")
        timestamp = utc(row.get("first_seen_at"))
        ticker = str(row.get("ticker") or "").upper()
        if not session or timestamp is None or not ticker:
            continue
        scans[session].add(timestamp)
        lookup[(session, timestamp)][ticker] = row
    return {key: sorted(values) for key, values in scans.items()}, dict(lookup)


def candidate_records(
    scenario: Scenario,
    scans: Mapping[str, Sequence[datetime]],
    lookup: Mapping[tuple[str, datetime], Mapping[str, Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    sessions = sorted(scans)
    previous_session: str | None = None
    for session in sessions:
        times = list(scans.get(session) or [])
        prior_final_rows: Mapping[str, Mapping[str, Any]] = {}
        if previous_session and scans.get(previous_session):
            prior_time = scans[previous_session][-1]
            prior_final_rows = lookup.get((previous_session, prior_time), {})
        for index, timestamp in enumerate(times):
            if et_minute(timestamp) > scenario.latest_entry_minute_et:
                continue
            current = lookup.get((session, timestamp), {})
            prior_same = lookup.get((session, times[index - 1]), {}) if index else {}
            for ticker, row in current.items():
                if not qualifies(row, scenario.target_low, scenario.target_high):
                    continue
                if scenario.confirmation == "strict_consecutive":
                    if not qualifies(prior_same.get(ticker), scenario.target_low, scenario.target_high):
                        continue
                elif scenario.confirmation == "previous_tier_a":
                    if not tier_a(prior_same.get(ticker)):
                        continue
                elif scenario.confirmation == "same_or_prior_day":
                    prior = prior_same.get(ticker) if index else prior_final_rows.get(ticker)
                    if not qualifies(prior, scenario.target_low, scenario.target_high):
                        continue
                elif scenario.confirmation == "none":
                    pass
                else:
                    raise ValueError(f"unknown confirmation mode: {scenario.confirmation}")
                expiry = str(row.get("cluster_expiration") or "")
                if not expiry or business_sessions_after(session, expiry) < scenario.minimum_full_sessions:
                    continue
                long_symbol = str(nested(row, "atm_contract", "symbol") or "")
                short_symbol = str(nested(row, "target_contract", "symbol") or "")
                long_strike = finite(nested(row, "atm_contract", "strike_price"))
                short_strike = finite(nested(row, "target_contract", "strike_price"))
                if not long_symbol or not short_symbol or long_strike is None or short_strike is None:
                    continue
                if short_strike <= long_strike:
                    continue
                output.append(
                    {
                        "session": session,
                        "scan_at": timestamp,
                        "ticker": ticker,
                        "row": row,
                        "long_symbol": long_symbol,
                        "short_symbol": short_symbol,
                        "long_strike": long_strike,
                        "short_strike": short_strike,
                        "expiry": expiry,
                    }
                )
        previous_session = session
    output.sort(key=lambda row: (row["scan_at"], row["ticker"]))
    return output


def all_data_requirements(
    candidates: Iterable[Mapping[str, Any]],
    latest_session: str,
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    option: dict[str, set[str]] = defaultdict(set)
    stock: dict[str, set[str]] = defaultdict(set)
    for row in candidates:
        end = min(str(row["expiry"]), latest_session)
        for session in session_range(str(row["session"]), end):
            option[session].update((str(row["long_symbol"]), str(row["short_symbol"])))
            stock[session].add(str(row["ticker"]))
    return dict(option), dict(stock)


def compact_bar(row: Mapping[str, Any]) -> dict[str, Any] | None:
    timestamp = utc(row.get("t") or row.get("timestamp"))
    if timestamp is None:
        return None
    return {
        "timestamp": timestamp,
        "o": finite(row.get("o")),
        "h": finite(row.get("h")),
        "l": finite(row.get("l")),
        "c": finite(row.get("c")),
        "vw": finite(row.get("vw")),
        "v": finite(row.get("v")),
    }


def prepare_bars(raw: Mapping[tuple[str, str], Sequence[Mapping[str, Any]]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    output: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for key, values in raw.items():
        rows = [bar for value in values if (bar := compact_bar(value)) is not None]
        rows.sort(key=lambda row: row["timestamp"])
        output[key] = rows
    return output


def bar_price(row: Mapping[str, Any]) -> float | None:
    for key in ("vw", "c", "o"):
        value = finite(row.get(key))
        if value is not None and value >= 0:
            return value
    return None


def first_bar(
    bars: Sequence[Mapping[str, Any]],
    start: datetime,
    window_minutes: int,
) -> Mapping[str, Any] | None:
    end = start + timedelta(minutes=window_minutes)
    return next((row for row in bars if start <= row["timestamp"] <= end and bar_price(row) is not None), None)


def entry_proxy(
    candidate: Mapping[str, Any],
    scenario: Scenario,
    option_bars: Mapping[tuple[str, str], Sequence[Mapping[str, Any]]],
) -> EntryProxy:
    session = str(candidate["session"])
    start = candidate["scan_at"]
    row = candidate["row"]
    long_bar: Mapping[str, Any] | None = None
    short_bar: Mapping[str, Any] | None = None
    if scenario.entry_model == "record_first_prints":
        long_price = finite(nested(row, "atm_option", "entry_price"))
        short_price = finite(nested(row, "target_option", "entry_price"))
        long_at = utc(nested(row, "atm_option", "entry_at"))
        short_at = utc(nested(row, "target_option", "entry_at"))
        if long_price is None or short_price is None or long_at is None or short_at is None:
            return EntryProxy(False, "reconstructed_first_print_entry_unavailable")
        if long_at < start or short_at < start:
            return EntryProxy(False, "reconstructed_entry_precedes_scan")
        if long_at.astimezone(NY).date().isoformat() != session or short_at.astimezone(NY).date().isoformat() != session:
            return EntryProxy(False, "reconstructed_entry_not_same_session")
        observed_at = max(long_at, short_at)
        lag_minutes = (observed_at - start).total_seconds() / 60.0
        if scenario.maximum_entry_lag_minutes is not None and lag_minutes > scenario.maximum_entry_lag_minutes:
            return EntryProxy(False, "reconstructed_entry_exceeded_maximum_lag")
        long_bar = next(
            (value for value in option_bars.get((session, str(candidate["long_symbol"])), []) if value["timestamp"] == long_at),
            None,
        )
        short_bar = next(
            (value for value in option_bars.get((session, str(candidate["short_symbol"])), []) if value["timestamp"] == short_at),
            None,
        )
        entry_reason = "reconstructed_separate_first_prints"
    elif scenario.entry_model == "within_window_trade_prints":
        long_bar = first_bar(option_bars.get((session, str(candidate["long_symbol"])), []), start, scenario.fill_window_minutes)
        short_bar = first_bar(option_bars.get((session, str(candidate["short_symbol"])), []), start, scenario.fill_window_minutes)
        if long_bar is None or short_bar is None:
            return EntryProxy(False, "both_legs_did_not_print_within_fill_window")
        long_price = bar_price(long_bar)
        short_price = bar_price(short_bar)
        if long_price is None or short_price is None:
            return EntryProxy(False, "entry_trade_price_unavailable")
        long_at = long_bar["timestamp"]
        short_at = short_bar["timestamp"]
        observed_at = max(long_at, short_at)
        entry_reason = "filled_trade_print_proxy"
    else:
        raise ValueError(f"unknown entry model: {scenario.entry_model}")

    raw_debit = long_price - short_price
    width = float(candidate["short_strike"]) - float(candidate["long_strike"])
    if raw_debit <= 0 or width <= 0 or raw_debit >= width:
        return EntryProxy(False, "invalid_trade_print_debit_geometry", raw_debit=raw_debit, width=width)
    stressed = min(width, raw_debit * (1.0 + scenario.adverse_execution_fraction))
    ratio = stressed / width
    reward_risk = (width - stressed) / stressed if stressed > 0 else None
    breakeven = float(candidate["long_strike"]) + stressed
    target = finite(nested(candidate, "row", "target"))
    economics = (
        ratio <= 0.45
        and reward_risk is not None
        and reward_risk >= 1.25
        and target is not None
        and breakeven < target
    )
    payload = {
        "observed_at": observed_at,
        "long_price": long_price,
        "short_price": short_price,
        "raw_debit": raw_debit,
        "stressed_debit": stressed,
        "width": width,
        "debit_width_fraction": ratio,
        "reward_risk": reward_risk,
        "breakeven": breakeven,
        "long_volume": finite(long_bar.get("v")) if long_bar else None,
        "short_volume": finite(short_bar.get("v")) if short_bar else None,
        "print_gap_minutes": abs((long_at - short_at).total_seconds()) / 60.0,
        "simultaneous_minute": long_at.replace(second=0, microsecond=0)
        == short_at.replace(second=0, microsecond=0),
    }
    if scenario.enforce_economics and not economics:
        return EntryProxy(False, "spread_economics_failed", **payload)
    return EntryProxy(True, entry_reason, **payload)


def spread_observations(
    candidate: Mapping[str, Any],
    entry: EntryProxy,
    scenario: Scenario,
    option_bars: Mapping[tuple[str, str], Sequence[Mapping[str, Any]]],
    latest_session: str,
) -> list[dict[str, Any]]:
    if entry.observed_at is None or entry.width is None:
        return []
    end_session = min(str(candidate["expiry"]), latest_session)
    long_rows: list[Mapping[str, Any]] = []
    short_rows: list[Mapping[str, Any]] = []
    for session in session_range(str(candidate["session"]), end_session):
        long_rows.extend(option_bars.get((session, str(candidate["long_symbol"])), []))
        short_rows.extend(option_bars.get((session, str(candidate["short_symbol"])), []))
    long_by_time = {row["timestamp"]: row for row in long_rows if row["timestamp"] >= entry.observed_at}
    short_by_time = {row["timestamp"]: row for row in short_rows if row["timestamp"] >= entry.observed_at}
    times = sorted(set(long_by_time) | set(short_by_time))
    output: list[dict[str, Any]] = []
    last_long: tuple[datetime, float] | None = None
    last_short: tuple[datetime, float] | None = None
    for timestamp in times:
        if timestamp in long_by_time:
            value = bar_price(long_by_time[timestamp])
            if value is not None:
                last_long = (timestamp, value)
        if timestamp in short_by_time:
            value = bar_price(short_by_time[timestamp])
            if value is not None:
                last_short = (timestamp, value)
        if last_long is None or last_short is None:
            continue
        long_age = (timestamp - last_long[0]).total_seconds() / 60.0
        short_age = (timestamp - last_short[0]).total_seconds() / 60.0
        if max(long_age, short_age) > scenario.stale_minutes:
            continue
        raw = min(entry.width, max(0.0, last_long[1] - last_short[1]))
        stressed = max(0.0, raw * (1.0 - scenario.adverse_execution_fraction))
        output.append(
            {
                "timestamp": timestamp,
                "raw_value": raw,
                "stressed_value": stressed,
                "long_age_minutes": long_age,
                "short_age_minutes": short_age,
            }
        )
    return output


def first_observation_at_or_after(
    observations: Sequence[Mapping[str, Any]],
    trigger: datetime,
) -> Mapping[str, Any] | None:
    return next((row for row in observations if row["timestamp"] >= trigger), None)


def latest_observation_at_or_before(
    observations: Sequence[Mapping[str, Any]],
    trigger: datetime,
) -> Mapping[str, Any] | None:
    rows = [row for row in observations if row["timestamp"] <= trigger]
    return rows[-1] if rows else None


def independent_spread_after(
    candidate: Mapping[str, Any],
    entry: EntryProxy,
    scenario: Scenario,
    option_bars: Mapping[tuple[str, str], Sequence[Mapping[str, Any]]],
    trigger: datetime,
) -> dict[str, Any] | None:
    if entry.width is None:
        return None
    session = trigger.astimezone(NY).date().isoformat()
    end = trigger + timedelta(minutes=scenario.exit_fill_window_minutes)
    long_bar = next(
        (
            row
            for row in option_bars.get((session, str(candidate["long_symbol"])), [])
            if trigger <= row["timestamp"] <= end and bar_price(row) is not None
        ),
        None,
    )
    short_bar = next(
        (
            row
            for row in option_bars.get((session, str(candidate["short_symbol"])), [])
            if trigger <= row["timestamp"] <= end and bar_price(row) is not None
        ),
        None,
    )
    if long_bar is None or short_bar is None:
        return None
    long_price = bar_price(long_bar)
    short_price = bar_price(short_bar)
    if long_price is None or short_price is None:
        return None
    raw = min(entry.width, max(0.0, long_price - short_price))
    return {
        "timestamp": max(long_bar["timestamp"], short_bar["timestamp"]),
        "raw_value": raw,
        "stressed_value": max(0.0, raw * (1.0 - scenario.adverse_execution_fraction)),
        "long_age_minutes": abs((long_bar["timestamp"] - trigger).total_seconds()) / 60.0,
        "short_age_minutes": abs((short_bar["timestamp"] - trigger).total_seconds()) / 60.0,
        "proxy_quality": "independent_first_prints_after_trigger",
    }


def latest_independent_spread(
    candidate: Mapping[str, Any],
    entry: EntryProxy,
    scenario: Scenario,
    option_bars: Mapping[tuple[str, str], Sequence[Mapping[str, Any]]],
    latest_session: str,
) -> dict[str, Any] | None:
    if entry.width is None or entry.observed_at is None:
        return None
    end_session = min(str(candidate["expiry"]), latest_session)
    for session in reversed(session_range(str(candidate["session"]), end_session)):
        cutoff = datetime.combine(date.fromisoformat(session), time(16, 0), tzinfo=NY).astimezone(UTC)
        long_rows = [
            row
            for row in option_bars.get((session, str(candidate["long_symbol"])), [])
            if entry.observed_at <= row["timestamp"] <= cutoff and bar_price(row) is not None
        ]
        short_rows = [
            row
            for row in option_bars.get((session, str(candidate["short_symbol"])), [])
            if entry.observed_at <= row["timestamp"] <= cutoff and bar_price(row) is not None
        ]
        if not long_rows or not short_rows:
            continue
        long_bar = long_rows[-1]
        short_bar = short_rows[-1]
        long_price = bar_price(long_bar)
        short_price = bar_price(short_bar)
        if long_price is None or short_price is None:
            continue
        raw = min(entry.width, max(0.0, long_price - short_price))
        return {
            "timestamp": max(long_bar["timestamp"], short_bar["timestamp"]),
            "raw_value": raw,
            "stressed_value": max(0.0, raw * (1.0 - scenario.adverse_execution_fraction)),
            "long_age_minutes": abs((long_bar["timestamp"] - short_bar["timestamp"]).total_seconds()) / 60.0,
            "short_age_minutes": abs((long_bar["timestamp"] - short_bar["timestamp"]).total_seconds()) / 60.0,
            "proxy_quality": "independent_last_prints_same_session",
        }
    return None


def stock_target_trigger(
    candidate: Mapping[str, Any],
    entry_at: datetime,
    stock_bars: Mapping[tuple[str, str], Sequence[Mapping[str, Any]]],
    latest_session: str,
) -> datetime | None:
    target = finite(nested(candidate, "row", "target"))
    if target is None:
        return None
    end_session = min(str(candidate["expiry"]), latest_session)
    for session in session_range(str(candidate["session"]), end_session):
        for row in stock_bars.get((session, str(candidate["ticker"])), []):
            if row["timestamp"] < entry_at:
                continue
            high = finite(row.get("h"))
            if high is not None and high >= target:
                return row["timestamp"]
    return None


def invalidation_triggers(
    candidate: Mapping[str, Any],
    entry_at: datetime,
    scans: Mapping[str, Sequence[datetime]],
    lookup: Mapping[tuple[str, datetime], Mapping[str, Mapping[str, Any]]],
    latest_session: str,
) -> list[tuple[datetime, str]]:
    ticker = str(candidate["ticker"])
    output: list[tuple[datetime, str]] = []
    for session in session_range(str(candidate["session"]), latest_session):
        for scan_at in scans.get(session, []):
            if scan_at <= entry_at:
                continue
            row = lookup.get((session, scan_at), {}).get(ticker)
            if not tier_a(row):
                output.append((scan_at, "cluster_invalidation"))
                return output
            spot = finite(row.get("spot")) if row else None
            target = finite(row.get("target")) if row else None
            if spot is not None and target is not None and target <= spot:
                output.append((scan_at, "cluster_target_reached_or_reduced"))
                return output
    return output


def time_exit_trigger(
    candidate: Mapping[str, Any],
    entry_at: datetime,
    scans: Mapping[str, Sequence[datetime]],
    lookup: Mapping[tuple[str, datetime], Mapping[str, Mapping[str, Any]]],
    latest_session: str,
) -> tuple[datetime, str] | None:
    expiry = str(candidate["expiry"])
    mandatory_session = previous_business_day(expiry)
    ticker = str(candidate["ticker"])
    for session in session_range(str(candidate["session"]), min(expiry, latest_session)):
        close_time = datetime.combine(date.fromisoformat(session), time(15, 45), tzinfo=NY).astimezone(UTC)
        if close_time <= entry_at:
            continue
        if session == mandatory_session:
            return close_time, "day_before_expiration"
        times = [value for value in scans.get(session, []) if value <= close_time]
        latest_row = lookup.get((session, times[-1]), {}).get(ticker) if times else None
        remaining = business_sessions_after(session, expiry)
        room = finite(latest_row.get("target_distance_pct")) if latest_row else None
        carry = tier_a(latest_row) and room is not None and room > 1.0 and remaining >= 2
        if not carry:
            return close_time, "failed_1545_carry_gate"
    return None


def simulate_exit(
    candidate: Mapping[str, Any],
    entry: EntryProxy,
    scenario: Scenario,
    option_bars: Mapping[tuple[str, str], Sequence[Mapping[str, Any]]],
    stock_bars: Mapping[tuple[str, str], Sequence[Mapping[str, Any]]],
    scans: Mapping[str, Sequence[datetime]],
    lookup: Mapping[tuple[str, datetime], Mapping[str, Mapping[str, Any]]],
    latest_session: str,
) -> ExitProxy:
    observations = spread_observations(candidate, entry, scenario, option_bars, latest_session)
    if entry.observed_at is None or entry.stressed_debit is None or entry.width is None:
        return ExitProxy(False, "entry_geometry_unavailable_for_exit")
    final_cutoff = datetime.combine(date.fromisoformat(latest_session), time(16, 0), tzinfo=NY).astimezone(UTC)
    last = latest_observation_at_or_before(observations, final_cutoff) if observations else None
    last = last or latest_independent_spread(candidate, entry, scenario, option_bars, latest_session)
    if last is None:
        return ExitProxy(False, "spread_exit_observation_unavailable")
    if scenario.exit_policy == "hold":
        finalized = last["timestamp"].date() >= date.fromisoformat(previous_business_day(str(candidate["expiry"])))
        return ExitProxy(
            True,
            "hold_to_available_horizon",
            last["timestamp"],
            last["raw_value"],
            last["stressed_value"],
            proxy_quality=str(last.get("proxy_quality") or "paired_forward_fill"),
            finalized=finalized,
        )

    triggers: list[tuple[datetime, int, str, Mapping[str, Any] | None]] = []
    profit_value = entry.stressed_debit + 0.70 * (entry.width - entry.stressed_debit)
    stop_value = 0.50 * entry.stressed_debit
    if scenario.exit_policy in {"managed", "spread_only"}:
        for observation in observations:
            if observation["timestamp"] <= entry.observed_at:
                continue
            if observation["stressed_value"] <= stop_value:
                triggers.append((observation["timestamp"], 0, "spread_50pct_debit_stop", observation))
                break
        for observation in observations:
            if observation["timestamp"] <= entry.observed_at:
                continue
            if observation["stressed_value"] >= profit_value:
                triggers.append((observation["timestamp"], 1, "spread_70pct_max_profit", observation))
                break
    if scenario.exit_policy in {"managed", "target_only"}:
        target_at = stock_target_trigger(candidate, entry.observed_at, stock_bars, latest_session)
        if target_at is not None:
            triggers.append((target_at, 2, "underlying_target_hit", None))
    if scenario.exit_policy in {"managed", "signal_only"}:
        for trigger_at, reason in invalidation_triggers(candidate, entry.observed_at, scans, lookup, latest_session):
            triggers.append((trigger_at, 3, reason, None))
    if scenario.exit_policy in {"managed", "target_only", "signal_only", "spread_only"}:
        time_trigger = time_exit_trigger(candidate, entry.observed_at, scans, lookup, latest_session)
        if time_trigger is not None:
            triggers.append((time_trigger[0], 4, time_trigger[1], None))

    if not triggers:
        return ExitProxy(
            True,
            "marked_to_latest_available",
            last["timestamp"],
            last["raw_value"],
            last["stressed_value"],
            proxy_quality=str(last.get("proxy_quality") or "paired_forward_fill"),
            finalized=False,
        )
    trigger_at, _, reason, direct = min(triggers, key=lambda row: (row[0], row[1]))
    observation = direct or first_observation_at_or_after(observations, trigger_at)
    if observation is None:
        observation = independent_spread_after(candidate, entry, scenario, option_bars, trigger_at)
    if observation is None:
        observation = last
        return ExitProxy(
            True,
            f"{reason}_no_post_trigger_print_marked_to_latest",
            observation["timestamp"],
            observation["raw_value"],
            observation["stressed_value"],
            trigger_at,
            (observation["timestamp"] - trigger_at).total_seconds() / 60.0,
            proxy_quality=str(observation.get("proxy_quality") or "paired_forward_fill"),
            finalized=False,
        )
    lag = (observation["timestamp"] - trigger_at).total_seconds() / 60.0
    return ExitProxy(
        True,
        reason,
        observation["timestamp"],
        observation["raw_value"],
        observation["stressed_value"],
        trigger_at,
        lag,
        proxy_quality=str(observation.get("proxy_quality") or "paired_forward_fill"),
        finalized=reason != "marked_to_latest_available",
    )


def candidate_rank(candidate: Mapping[str, Any], entry: EntryProxy) -> tuple[Any, ...]:
    room = finite(nested(candidate, "row", "target_distance_pct")) or 999.0
    rank = int(nested(candidate, "row", "rank") or 999)
    ratio = entry.debit_width_fraction if entry.debit_width_fraction is not None else 999.0
    volume = (entry.long_volume or 0.0) + (entry.short_volume or 0.0)
    return (abs(room - 3.5), rank, ratio, -volume, str(candidate["ticker"]))


def stock_bar_at_or_after(
    bars: Sequence[Mapping[str, Any]],
    trigger: datetime,
    window_minutes: int = 10,
) -> Mapping[str, Any] | None:
    end = trigger + timedelta(minutes=window_minutes)
    return next(
        (row for row in bars if trigger <= row["timestamp"] <= end and bar_price(row) is not None),
        None,
    )


def underlying_trade(
    candidate: Mapping[str, Any],
    *,
    exit_policy: str,
    stock_bars: Mapping[tuple[str, str], Sequence[Mapping[str, Any]]],
    scans: Mapping[str, Sequence[datetime]],
    lookup: Mapping[tuple[str, datetime], Mapping[str, Mapping[str, Any]]],
    latest_session: str,
) -> dict[str, Any] | None:
    session = str(candidate["session"])
    ticker = str(candidate["ticker"])
    entry_bar = stock_bar_at_or_after(
        stock_bars.get((session, ticker), []),
        candidate["scan_at"],
        5,
    )
    if entry_bar is None:
        return None
    entry_at = entry_bar["timestamp"]
    entry_price = bar_price(entry_bar)
    if entry_price is None or entry_price <= 0:
        return None
    end_session = min(str(candidate["expiry"]), latest_session)
    all_rows: list[Mapping[str, Any]] = []
    for value in session_range(session, end_session):
        all_rows.extend(
            row
            for row in stock_bars.get((value, ticker), [])
            if row["timestamp"] >= entry_at
        )
    all_rows.sort(key=lambda row: row["timestamp"])
    if not all_rows:
        return None
    last = all_rows[-1]
    if exit_policy == "hold":
        exit_at = last["timestamp"]
        exit_price = bar_price(last)
        reason = "underlying_hold_to_horizon"
        finalized = exit_at.astimezone(NY).date().isoformat() >= previous_business_day(str(candidate["expiry"]))
    else:
        triggers: list[tuple[datetime, int, str]] = []
        target_at = stock_target_trigger(candidate, entry_at, stock_bars, latest_session)
        if target_at is not None:
            triggers.append((target_at, 0, "underlying_target_hit"))
        for trigger_at, reason in invalidation_triggers(candidate, entry_at, scans, lookup, latest_session):
            triggers.append((trigger_at, 1, reason))
        time_trigger = time_exit_trigger(candidate, entry_at, scans, lookup, latest_session)
        if time_trigger is not None:
            triggers.append((time_trigger[0], 2, time_trigger[1]))
        if not triggers:
            exit_at = last["timestamp"]
            exit_price = bar_price(last)
            reason = "underlying_marked_to_horizon"
            finalized = False
        else:
            trigger_at, _, reason = min(triggers, key=lambda row: (row[0], row[1]))
            if reason == "underlying_target_hit":
                exit_at = trigger_at
                exit_price = finite(nested(candidate, "row", "target"))
                finalized = True
            else:
                trigger_session = trigger_at.astimezone(NY).date().isoformat()
                exit_bar = stock_bar_at_or_after(
                    stock_bars.get((trigger_session, ticker), []),
                    trigger_at,
                    30,
                )
                if exit_bar is None:
                    exit_bar = next((row for row in all_rows if row["timestamp"] >= trigger_at), None)
                if exit_bar is None:
                    exit_bar = last
                    finalized = False
                    reason = f"{reason}_no_post_trigger_bar_marked_to_horizon"
                else:
                    finalized = True
                exit_at = exit_bar["timestamp"]
                exit_price = bar_price(exit_bar)
    if exit_price is None or exit_price <= 0:
        return None
    return {
        "session": session,
        "ticker": ticker,
        "scan_at": candidate["scan_at"].isoformat(),
        "entry_at": entry_at,
        "entry_price": entry_price,
        "exit_at": exit_at,
        "exit_price": exit_price,
        "exit_reason": reason,
        "finalized": finalized,
        "return_pct": (exit_price / entry_price - 1.0) * 100.0,
        "target_room_pct": nested(candidate, "row", "target_distance_pct"),
        "rank": nested(candidate, "row", "rank"),
        "strength": nested(candidate, "row", "strength"),
    }


def underlying_portfolio_backtest(
    name: str,
    candidates: Sequence[Mapping[str, Any]],
    *,
    exit_policy: str,
    stock_bars: Mapping[tuple[str, str], Sequence[Mapping[str, Any]]],
    scans: Mapping[str, Sequence[datetime]],
    lookup: Mapping[tuple[str, datetime], Mapping[str, Mapping[str, Any]]],
    latest_session: str,
) -> dict[str, Any]:
    grouped: dict[datetime, list[Mapping[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        grouped[candidate["scan_at"]].append(candidate)
    open_positions: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    entered: set[tuple[str, str]] = set()
    rejects = Counter()
    for scan_at in sorted(grouped):
        closing = [row for row in open_positions if row["exit_at"] <= scan_at]
        for row in sorted(closing, key=lambda value: value["exit_at"]):
            trades.append(row)
            open_positions.remove(row)
        if len(open_positions) >= 2:
            rejects["maximum_open_positions"] += len(grouped[scan_at])
            continue
        evaluated: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        for candidate in grouped[scan_at]:
            key = (str(candidate["session"]), str(candidate["ticker"]))
            if key in entered:
                rejects["ticker_day_already_entered"] += 1
                continue
            trade = underlying_trade(
                candidate,
                exit_policy=exit_policy,
                stock_bars=stock_bars,
                scans=scans,
                lookup=lookup,
                latest_session=latest_session,
            )
            if trade is None:
                rejects["underlying_entry_or_exit_unavailable"] += 1
                continue
            room = finite(trade.get("target_room_pct")) or 999.0
            evaluated.append(
                (
                    (
                        abs(room - 3.5),
                        int(trade.get("rank") or 999),
                        -float(trade.get("strength") or 0.0),
                        str(trade["ticker"]),
                    ),
                    trade,
                )
            )
        if not evaluated:
            continue
        evaluated.sort(key=lambda row: row[0])
        selected = evaluated[0][1]
        selected["diagnostic"] = name
        open_positions.append(selected)
        entered.add((str(selected["session"]), str(selected["ticker"])))
        rejects["one_trade_per_scan"] += max(0, len(evaluated) - 1)
    trades.extend(sorted(open_positions, key=lambda row: row["exit_at"]))
    returns = [float(row["return_pct"]) for row in trades]
    cumulative = 0.0
    peak = 0.0
    drawdown = 0.0
    for value in returns:
        cumulative += value
        peak = max(peak, cumulative)
        drawdown = min(drawdown, cumulative - peak)
    return {
        "name": name,
        "exit_policy": exit_policy,
        "trades": [
            {
                **row,
                "entry_at": row["entry_at"].isoformat(),
                "exit_at": row["exit_at"].isoformat(),
            }
            for row in trades
        ],
        "summary": {
            "trades": len(trades),
            "finalized": sum(bool(row.get("finalized")) for row in trades),
            "win_rate": sum(value > 0 for value in returns) / len(returns) if returns else None,
            "mean_return_pct": mean(returns) if returns else None,
            "median_return_pct": median(returns) if returns else None,
            "sum_return_pct": sum(returns),
            "maximum_drawdown_sum_return_pct": drawdown,
            "mean_return_bootstrap_95pct": bootstrap_interval(returns, "mean"),
            "win_rate_bootstrap_95pct": bootstrap_interval(returns, "win_rate"),
            "exit_reasons": dict(Counter(str(row.get("exit_reason")) for row in trades)),
            "rejects": dict(rejects),
        },
    }


def theoretical_trade(
    candidate: Mapping[str, Any],
    scenario: Scenario,
    option_bars: Mapping[tuple[str, str], Sequence[Mapping[str, Any]]],
    stock_bars: Mapping[tuple[str, str], Sequence[Mapping[str, Any]]],
    scans: Mapping[str, Sequence[datetime]],
    lookup: Mapping[tuple[str, datetime], Mapping[str, Mapping[str, Any]]],
    latest_session: str,
) -> dict[str, Any]:
    entry = entry_proxy(candidate, scenario, option_bars)
    exit_proxy = (
        simulate_exit(candidate, entry, scenario, option_bars, stock_bars, scans, lookup, latest_session)
        if entry.valid
        else ExitProxy(False, "entry_not_filled")
    )
    debit = entry.stressed_debit
    exit_value = exit_proxy.stressed_value
    gross_return = ((exit_value / debit) - 1.0) * 100.0 if debit and exit_value is not None else None
    net_one_contract = (
        (exit_value - debit) * 100.0 - scenario.fee_per_spread_round_trip
        if debit is not None and exit_value is not None
        else None
    )
    net_return = (
        net_one_contract / (debit * 100.0) * 100.0
        if net_one_contract is not None and debit and debit > 0
        else None
    )
    return {
        "candidate": candidate,
        "entry": entry,
        "exit": exit_proxy,
        "gross_return_pct": gross_return,
        "net_one_contract": net_one_contract,
        "net_return_pct": net_return,
    }


def portfolio_backtest(
    scenario: Scenario,
    candidates: Sequence[Mapping[str, Any]],
    option_bars: Mapping[tuple[str, str], Sequence[Mapping[str, Any]]],
    stock_bars: Mapping[tuple[str, str], Sequence[Mapping[str, Any]]],
    scans: Mapping[str, Sequence[datetime]],
    lookup: Mapping[tuple[str, datetime], Mapping[str, Mapping[str, Any]]],
    latest_session: str,
) -> dict[str, Any]:
    grouped: dict[datetime, list[Mapping[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        grouped[candidate["scan_at"]].append(candidate)
    equity = scenario.starting_equity
    realized_equity = scenario.starting_equity
    peak = equity
    max_drawdown = 0.0
    open_positions: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    rejects = Counter()
    entered_ticker_days: set[tuple[str, str]] = set()

    def close_due(until: datetime) -> None:
        nonlocal equity, realized_equity, peak, max_drawdown, open_positions
        due = sorted(
            [
                row
                for row in open_positions
                if (utc(row.get("exit_at")) or datetime.max.replace(tzinfo=UTC)) <= until
            ],
            key=lambda row: utc(row.get("exit_at")) or datetime.max.replace(tzinfo=UTC),
        )
        for trade in due:
            pnl = trade["pnl_dollars"]
            realized_equity += pnl
            equity += pnl
            peak = max(peak, equity)
            max_drawdown = min(max_drawdown, equity - peak)
            trade["equity_after_exit"] = equity
            trade["portfolio_status"] = "closed"
            trades.append(trade)
            open_positions.remove(trade)

    for scan_at in sorted(grouped):
        close_due(scan_at)
        if len(open_positions) >= scenario.maximum_positions:
            rejects["maximum_open_positions"] += len(grouped[scan_at])
            continue
        evaluated = [
            theoretical_trade(candidate, scenario, option_bars, stock_bars, scans, lookup, latest_session)
            for candidate in grouped[scan_at]
        ]
        eligible = [row for row in evaluated if row["entry"].valid and row["exit"].valid]
        for row in evaluated:
            if not row["entry"].valid:
                rejects[row["entry"].reason] += 1
            elif not row["exit"].valid:
                rejects[row["exit"].reason] += 1
        eligible.sort(key=lambda row: candidate_rank(row["candidate"], row["entry"]))
        selected_count = 0
        for model in eligible:
            candidate = model["candidate"]
            key = (str(candidate["session"]), str(candidate["ticker"]))
            if key in entered_ticker_days:
                rejects["ticker_day_already_entered"] += 1
                continue
            if selected_count >= 1 and scenario.one_trade_per_scan:
                rejects["one_trade_per_scan"] += 1
                continue
            if len(open_positions) >= scenario.maximum_positions:
                rejects["maximum_open_positions"] += 1
                continue
            entry = model["entry"]
            exit_proxy = model["exit"]
            assert entry.stressed_debit is not None and exit_proxy.stressed_value is not None
            risk_budget = equity * scenario.risk_fraction
            contracts = int(risk_budget // (entry.stressed_debit * 100.0))
            if contracts < 1:
                rejects["position_size_below_one_contract"] += 1
                continue
            pnl = ((exit_proxy.stressed_value - entry.stressed_debit) * 100.0 - scenario.fee_per_spread_round_trip) * contracts
            trade = {
                "scenario": scenario.name,
                "session": candidate["session"],
                "ticker": candidate["ticker"],
                "scan_at": candidate["scan_at"].isoformat(),
                "entry_at": entry.observed_at.isoformat() if entry.observed_at else None,
                "exit_at": exit_proxy.observed_at.isoformat() if exit_proxy.observed_at else None,
                "expiry": candidate["expiry"],
                "rank": nested(candidate, "row", "rank"),
                "strength": nested(candidate, "row", "strength"),
                "signal_spot": nested(candidate, "row", "spot"),
                "target": nested(candidate, "row", "target"),
                "target_room_pct": nested(candidate, "row", "target_distance_pct"),
                "long_symbol": candidate["long_symbol"],
                "short_symbol": candidate["short_symbol"],
                "long_strike": candidate["long_strike"],
                "short_strike": candidate["short_strike"],
                "spread_width": entry.width,
                "raw_entry_debit": entry.raw_debit,
                "entry_debit": entry.stressed_debit,
                "debit_width_fraction": entry.debit_width_fraction,
                "reward_risk": entry.reward_risk,
                "entry_model": scenario.entry_model,
                "entry_scan_to_fill_minutes": (
                    (entry.observed_at - candidate["scan_at"]).total_seconds() / 60.0
                    if entry.observed_at
                    else None
                ),
                "entry_print_gap_minutes": entry.print_gap_minutes,
                "simultaneous_entry_minute": entry.simultaneous_minute,
                "entry_long_volume": entry.long_volume,
                "entry_short_volume": entry.short_volume,
                "raw_exit_value": exit_proxy.raw_value,
                "exit_value": exit_proxy.stressed_value,
                "exit_reason": exit_proxy.reason,
                "exit_trigger_at": exit_proxy.trigger_at.isoformat() if exit_proxy.trigger_at else None,
                "exit_execution_lag_minutes": exit_proxy.execution_lag_minutes,
                "exit_proxy_quality": exit_proxy.proxy_quality,
                "finalized": exit_proxy.finalized,
                "contracts": contracts,
                "risk_budget": risk_budget,
                "capital_at_risk": entry.stressed_debit * 100.0 * contracts,
                "gross_return_pct": model["gross_return_pct"],
                "net_return_pct": model["net_return_pct"],
                "pnl_dollars": pnl,
                "equity_at_entry": equity,
                "portfolio_status": "open",
            }
            open_positions.append(trade)
            entered_ticker_days.add(key)
            selected_count += 1

    final_time = datetime.combine(date.fromisoformat(latest_session), time(23, 59), tzinfo=UTC)
    close_due(final_time)
    for trade in sorted(
        open_positions,
        key=lambda row: utc(row.get("exit_at")) or datetime.max.replace(tzinfo=UTC),
    ):
        pnl = trade["pnl_dollars"]
        equity += pnl
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity - peak)
        trade["equity_after_exit"] = equity
        trade["portfolio_status"] = "marked_open_at_horizon"
        trades.append(trade)
    trades.sort(key=lambda row: (row["entry_at"] or "", row["ticker"]))
    return {
        "scenario": asdict(scenario),
        "trades": trades,
        "rejects": dict(rejects),
        "ending_equity": equity,
        "realized_equity": realized_equity,
        "maximum_drawdown_dollars": max_drawdown,
        "maximum_drawdown_pct_starting_equity": max_drawdown / scenario.starting_equity * 100.0,
    }


def distribution(values: Iterable[Any]) -> dict[str, Any]:
    clean = [float(value) for value in values if finite(value) is not None]
    if not clean:
        return {"count": 0, "mean": None, "median": None, "minimum": None, "maximum": None}
    return {
        "count": len(clean),
        "mean": mean(clean),
        "median": median(clean),
        "minimum": min(clean),
        "maximum": max(clean),
        "standard_deviation": pstdev(clean) if len(clean) > 1 else 0.0,
    }


def bootstrap_interval(values: Sequence[float], statistic: str, *, seed: int = 20260807, draws: int = 5000) -> dict[str, Any]:
    if not values:
        return {"lower": None, "upper": None, "draws": 0}
    rng = random.Random(seed)
    sampled: list[float] = []
    for _ in range(draws):
        sample = [values[rng.randrange(len(values))] for _ in values]
        if statistic == "mean":
            sampled.append(mean(sample))
        elif statistic == "win_rate":
            sampled.append(sum(value > 0 for value in sample) / len(sample))
        else:
            raise ValueError(statistic)
    sampled.sort()
    return {
        "lower": sampled[int(0.025 * (draws - 1))],
        "upper": sampled[int(0.975 * (draws - 1))],
        "draws": draws,
    }


def summarize_result(result: Mapping[str, Any]) -> dict[str, Any]:
    trades = list(result.get("trades") or [])
    returns = [float(row["net_return_pct"]) for row in trades if finite(row.get("net_return_pct")) is not None]
    pnls = [float(row["pnl_dollars"]) for row in trades if finite(row.get("pnl_dollars")) is not None]
    wins = [value for value in pnls if value > 0]
    losses = [value for value in pnls if value < 0]
    profit_factor = sum(wins) / abs(sum(losses)) if losses else (math.inf if wins else None)
    by_session: dict[str, dict[str, Any]] = {}
    for session in sorted({str(row["session"]) for row in trades}):
        subset = [row for row in trades if row["session"] == session]
        by_session[session] = {
            "trades": len(subset),
            "pnl_dollars": sum(float(row["pnl_dollars"]) for row in subset),
            "win_rate": sum(float(row["pnl_dollars"]) > 0 for row in subset) / len(subset),
            "median_return_pct": median(float(row["net_return_pct"]) for row in subset),
        }
    return {
        "name": nested(result, "scenario", "name"),
        "trades": len(trades),
        "closed_finalized": sum(bool(row.get("finalized")) for row in trades),
        "marked_open_at_horizon": sum(row.get("portfolio_status") == "marked_open_at_horizon" for row in trades),
        "winning_trades": sum(value > 0 for value in pnls),
        "losing_trades": sum(value < 0 for value in pnls),
        "win_rate": sum(value > 0 for value in pnls) / len(pnls) if pnls else None,
        "net_return_pct": distribution(returns),
        "pnl_dollars": distribution(pnls),
        "total_pnl_dollars": sum(pnls),
        "ending_equity": result.get("ending_equity"),
        "total_return_pct": (float(result.get("ending_equity") or 0.0) / float(nested(result, "scenario", "starting_equity")) - 1.0) * 100.0,
        "profit_factor": profit_factor,
        "maximum_drawdown_dollars": result.get("maximum_drawdown_dollars"),
        "maximum_drawdown_pct_starting_equity": result.get("maximum_drawdown_pct_starting_equity"),
        "mean_return_bootstrap_95pct": bootstrap_interval(returns, "mean"),
        "win_rate_bootstrap_95pct": bootstrap_interval(returns, "win_rate"),
        "exit_reasons": dict(Counter(str(row.get("exit_reason")) for row in trades)),
        "sessions": by_session,
        "rejects": result.get("rejects"),
    }


def scenarios() -> list[Scenario]:
    print_base = dict(
        confirmation="strict_consecutive",
        target_low=2.0,
        target_high=5.0,
        entry_model="within_window_trade_prints",
        fill_window_minutes=5,
        stale_minutes=15,
        exit_fill_window_minutes=120,
        adverse_execution_fraction=0.05,
        fee_per_spread_round_trip=2.60,
        enforce_economics=True,
        exit_policy="managed",
    )
    reconstructed = {
        **print_base,
        "entry_model": "record_first_prints",
        "maximum_entry_lag_minutes": 120,
        "stale_minutes": 120,
        "exit_fill_window_minutes": 180,
    }
    reconstructed_any = {**reconstructed, "maximum_entry_lag_minutes": None}
    return [
        Scenario("strict_print5_managed", **print_base),
        Scenario("strict_print15_managed", **{**print_base, "fill_window_minutes": 15}),
        Scenario("strict_reconstructed_60m", **{**reconstructed, "maximum_entry_lag_minutes": 60}),
        Scenario("strict_reconstructed_120m", **reconstructed),
        Scenario("strict_reconstructed_any", **reconstructed_any),
        Scenario("strict_reconstructed_raw", **{**reconstructed_any, "adverse_execution_fraction": 0.0, "fee_per_spread_round_trip": 0.0}),
        Scenario("strict_reconstructed_stress10", **{**reconstructed_any, "adverse_execution_fraction": 0.10}),
        Scenario("strict_reconstructed_stale15", **{**reconstructed_any, "stale_minutes": 15}),
        Scenario("strict_reconstructed_hold", **{**reconstructed_any, "exit_policy": "hold"}),
        Scenario("strict_reconstructed_target_only", **{**reconstructed_any, "exit_policy": "target_only"}),
        Scenario("strict_reconstructed_signal_only", **{**reconstructed_any, "exit_policy": "signal_only"}),
        Scenario("strict_reconstructed_spread_only", **{**reconstructed_any, "exit_policy": "spread_only"}),
        Scenario("previous_tier_a_2_5_reconstructed", **{**reconstructed_any, "confirmation": "previous_tier_a"}),
        Scenario("same_or_prior_day_2_5_reconstructed", **{**reconstructed_any, "confirmation": "same_or_prior_day"}),
        Scenario("first_scan_2_5_reconstructed", **{**reconstructed_any, "confirmation": "none"}),
        Scenario("strict_1_2_reconstructed", **{**reconstructed_any, "target_low": 1.0, "target_high": 2.0}),
        Scenario("strict_5_10_reconstructed", **{**reconstructed_any, "target_low": 5.0, "target_high": 10.0}),
        Scenario("strict_2_10_reconstructed", **{**reconstructed_any, "target_low": 2.0, "target_high": 10.0}),
        Scenario("strict_no_economics_reconstructed", **{**reconstructed_any, "enforce_economics": False}),
    ]


def csv_write(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--force-data", action="store_true")
    args = parser.parse_args()

    source_payload, records = load_source()
    scans, lookup = scan_maps(records)
    latest_session = str(nested(source_payload, "summary", "latest_completed_market_session") or max(scans))
    specs = scenarios()
    candidate_sets = {spec.name: candidate_records(spec, scans, lookup) for spec in specs}
    union: dict[tuple[str, str, str], Mapping[str, Any]] = {}
    for values in candidate_sets.values():
        for row in values:
            union[(str(row["session"]), str(row["ticker"]), row["scan_at"].isoformat())] = row
    option_requirements, stock_requirements = all_data_requirements(union.values(), latest_session)
    headers = provider_headers()
    raw_option = load_or_fetch_partitioned_minute_bars(
        option_requirements,
        root=OPTION_MINUTE_CACHE,
        url=OPTION_BARS_URL,
        headers=headers,
        stock=False,
        workers=max(1, args.workers),
        force=args.force_data,
    )
    raw_stock = load_or_fetch_partitioned_minute_bars(
        stock_requirements,
        root=STOCK_MINUTE_CACHE,
        url=STOCK_BARS_URL,
        headers=headers,
        stock=True,
        workers=max(1, args.workers),
        force=args.force_data,
    )
    option_bars = prepare_bars(raw_option)
    stock_bars = prepare_bars(raw_stock)

    results: dict[str, Any] = {}
    summaries: list[dict[str, Any]] = []
    all_trade_rows: list[dict[str, Any]] = []
    for spec in specs:
        result = portfolio_backtest(
            spec,
            candidate_sets[spec.name],
            option_bars,
            stock_bars,
            scans,
            lookup,
            latest_session,
        )
        summary = summarize_result(result)
        results[spec.name] = result
        summaries.append(summary)
        all_trade_rows.extend(result["trades"])

    underlying_specs = [
        ("underlying_strict_2_5_managed", "strict_reconstructed_any", "managed"),
        ("underlying_strict_2_5_hold", "strict_reconstructed_any", "hold"),
        ("underlying_previous_tier_a_2_5", "previous_tier_a_2_5_reconstructed", "managed"),
        ("underlying_same_or_prior_day_2_5", "same_or_prior_day_2_5_reconstructed", "managed"),
        ("underlying_first_scan_2_5", "first_scan_2_5_reconstructed", "managed"),
        ("underlying_strict_5_10", "strict_5_10_reconstructed", "managed"),
        ("underlying_strict_2_10", "strict_2_10_reconstructed", "managed"),
    ]
    underlying_diagnostics = {
        name: underlying_portfolio_backtest(
            name,
            candidate_sets[source_name],
            exit_policy=exit_policy,
            stock_bars=stock_bars,
            scans=scans,
            lookup=lookup,
            latest_session=latest_session,
        )
        for name, source_name, exit_policy in underlying_specs
    }

    fillability_specs = [
        "strict_print5_managed",
        "strict_print15_managed",
        "strict_reconstructed_60m",
        "strict_reconstructed_120m",
        "strict_reconstructed_any",
    ]
    strict_candidates = candidate_sets["strict_reconstructed_any"]
    entry_fillability: dict[str, Any] = {}
    spec_by_name = {value.name: value for value in specs}
    for name in fillability_specs:
        spec = spec_by_name[name]
        entries = [entry_proxy(candidate, spec, option_bars) for candidate in strict_candidates]
        entry_fillability[name] = {
            "candidate_records": len(entries),
            "unique_ticker_days": len({(str(row["session"]), str(row["ticker"])) for row in strict_candidates}),
            "passing_entry_and_economics": sum(value.valid for value in entries),
            "reasons": dict(Counter(value.reason for value in entries)),
            "median_scan_to_observed_fill_minutes": median(
                [
                    (value.observed_at - candidate["scan_at"]).total_seconds() / 60.0
                    for candidate, value in zip(strict_candidates, entries)
                    if value.valid and value.observed_at is not None
                ]
            )
            if any(value.valid and value.observed_at is not None for value in entries)
            else None,
            "median_leg_print_gap_minutes": median(
                [value.print_gap_minutes for value in entries if value.valid and value.print_gap_minutes is not None]
            )
            if any(value.valid and value.print_gap_minutes is not None for value in entries)
            else None,
        }

    payload: dict[str, Any] = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "mode": "point_in_time_cluster_debit_spread_strategy_backtest",
        "source": str(SOURCE),
        "source_sha256": sha256_file(SOURCE),
        "latest_completed_market_session": latest_session,
        "sample_sessions": sorted(scans),
        "candidate_counts_before_fill_and_portfolio": {key: len(value) for key, value in candidate_sets.items()},
        "data_coverage": {
            "option_symbol_sessions_requested": sum(len(value) for value in option_requirements.values()),
            "option_symbol_sessions_with_bars": sum(bool(option_bars.get(key)) for key in option_bars),
            "stock_symbol_sessions_requested": sum(len(value) for value in stock_requirements.values()),
            "stock_symbol_sessions_with_bars": sum(bool(stock_bars.get(key)) for key in stock_bars),
        },
        "methodology": {
            "entry": "actual saved Cluster scan; exact scenarios require both historical option prints inside the configured window, while reconstructed scenarios use separately observed first prints and retain their lag",
            "confirmation": "scenario-specific; strict requires both current and immediately previous scan to pass all Tier A and target-room gates",
            "execution": "debit increased and exit credit reduced by scenario adverse-execution fraction; no historical NBBO is available",
            "portfolio": "$100,000 normalized account; 0.25% equity risk per trade; maximum two concurrent positions; one new trade per scan",
            "managed_exit_order": [
                "50% debit stop or 70% maximum-profit capture on observable spread marks",
                "underlying target hit",
                "first subsequent Cluster scan invalidation",
                "3:45 PM carry gate or day-before-expiration exit",
            ],
            "lookahead_boundary": "rules were developed after observing this short sample; results are in-sample exploratory and require prospective validation",
            "option_data_limit": "trade bars are sparse and do not prove executable two-leg NBBO fills",
        },
        "scenario_summaries": summaries,
        "entry_fillability": entry_fillability,
        "underlying_diagnostics": underlying_diagnostics,
        "results": results,
        "automatic_promotion": False,
        "paper_or_live_execution": False,
        "execution_authority": False,
    }
    payload["report_id"] = stable_id(
        "cipher_cluster_strategy_backtest",
        {
            "created_at": payload["created_at"],
            "source_sha256": payload["source_sha256"],
            "scenarios": [asdict(value) for value in specs],
            "latest_session": latest_session,
        },
        length=64,
    )
    archive = ARCHIVE_ROOT / f"{payload['report_id']}.json"
    atomic_json(archive, payload)
    atomic_json(LATEST_JSON, payload)
    csv_write(LATEST_TRADES, all_trade_rows)
    scenario_rows: list[dict[str, Any]] = []
    for summary in summaries:
        scenario_rows.append(
            {
                "name": summary["name"],
                "trades": summary["trades"],
                "closed_finalized": summary["closed_finalized"],
                "marked_open_at_horizon": summary["marked_open_at_horizon"],
                "win_rate": summary["win_rate"],
                "mean_net_return_pct": nested(summary, "net_return_pct", "mean"),
                "median_net_return_pct": nested(summary, "net_return_pct", "median"),
                "total_pnl_dollars": summary["total_pnl_dollars"],
                "total_return_pct": summary["total_return_pct"],
                "profit_factor": summary["profit_factor"],
                "maximum_drawdown_pct": summary["maximum_drawdown_pct_starting_equity"],
                "mean_return_ci_lower": nested(summary, "mean_return_bootstrap_95pct", "lower"),
                "mean_return_ci_upper": nested(summary, "mean_return_bootstrap_95pct", "upper"),
                "win_rate_ci_lower": nested(summary, "win_rate_bootstrap_95pct", "lower"),
                "win_rate_ci_upper": nested(summary, "win_rate_bootstrap_95pct", "upper"),
            }
        )
    csv_write(LATEST_SCENARIOS, scenario_rows)
    print(
        json.dumps(
            {
                "status": "completed",
                "report_id": payload["report_id"],
                "json": str(LATEST_JSON),
                "trade_csv": str(LATEST_TRADES),
                "scenario_csv": str(LATEST_SCENARIOS),
                "latest_completed_market_session": latest_session,
                "candidate_counts": payload["candidate_counts_before_fill_and_portfolio"],
                "scenario_summaries": scenario_rows,
                "entry_fillability": entry_fillability,
                "underlying_diagnostics": {
                    name: value["summary"] for name, value in underlying_diagnostics.items()
                },
                "execution_authority": False,
            },
            indent=2,
            sort_keys=True,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
