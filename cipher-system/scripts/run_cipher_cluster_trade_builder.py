#!/usr/bin/env python3
"""Research delayed Cluster entries and build a read-only next-session trade board.

The existing Tier A Cluster evidence enters at the first traded option minute at
or after the signal. A trade prepared after the close is a different rule, so
this script separately measures next-session-open entries before creating a
current watch board.

No orders are created or submitted. Quote-derived debits are closing/reference
values only and must be refreshed before any manual decision.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, time as dt_time, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "core") not in sys.path:
    sys.path.insert(0, str(ROOT / "core"))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from app import option_chain, quote  # noqa: E402
from core.research_platform.hashing import stable_id  # noqa: E402
from run_cipher_cluster_individual_analysis import (  # noqa: E402
    first_number,
    prepare_daily_bars,
)
from run_cipher_complete_observations import (  # noqa: E402
    NY,
    OPTION_BARS_URL,
    OPTION_DAILY_CACHE,
    STOCK_BARS_URL,
    STOCK_DAILY_CACHE,
    atomic_json,
    latest_completed_market_session,
    latest_dataset,
    load_or_fetch_daily_bars,
    provider_headers,
)

UTC = timezone.utc
GOV = ROOT / "data" / "governance" / "cipher_signal_only"
SOURCE = GOV / "latest_cluster_individual_analysis.json"
OUTPUT = GOV / "latest_cluster_trade_candidates.json"


def finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def nested(row: Mapping[str, Any], *keys: str) -> Any:
    value: Any = row
    for key in keys:
        value = value.get(key) if isinstance(value, Mapping) else None
    return value


def tier(row: Mapping[str, Any]) -> str:
    return str(nested(row, "standalone_assessment", "research_tier") or "")


def ordered_records(records: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        (dict(row) for row in records),
        key=lambda row: (
            str(row.get("market_session") or ""),
            str(row.get("ticker") or ""),
            str(row.get("first_seen_at") or ""),
            str(row.get("signal_id") or ""),
        ),
    )


def first_tier_a_per_ticker_session(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in ordered_records(records):
        if tier(row) != "tier_a_cluster_only":
            continue
        key = (str(row.get("market_session") or ""), str(row.get("ticker") or ""))
        if not all(key) or key in seen:
            continue
        seen.add(key)
        selected.append(row)
    return selected


def latest_record_per_ticker_session(records: Sequence[Mapping[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for row in ordered_records(records):
        key = (str(row.get("market_session") or ""), str(row.get("ticker") or ""))
        if all(key):
            latest[key] = row
    return latest


def session_bar(rows: Sequence[Mapping[str, Any]], session: str) -> Mapping[str, Any] | None:
    return next((row for row in rows if str(row.get("session") or "") == session), None)


def next_session_bar(rows: Sequence[Mapping[str, Any]], session: str, expiry: str) -> Mapping[str, Any] | None:
    return next(
        (
            row
            for row in rows
            if session < str(row.get("session") or "") <= expiry
        ),
        None,
    )


def last_bar_through(rows: Sequence[Mapping[str, Any]], *, start_session: str, cutoff: str) -> Mapping[str, Any] | None:
    eligible = [
        row
        for row in rows
        if start_session <= str(row.get("session") or "") <= cutoff
    ]
    return eligible[-1] if eligible else None


def delayed_entry_record(
    row: Mapping[str, Any],
    *,
    option_daily: Mapping[str, Sequence[Mapping[str, Any]]],
    stock_daily: Mapping[str, Sequence[Mapping[str, Any]]],
    latest_market_session: str,
    latest_state: Mapping[str, Any] | None,
) -> dict[str, Any]:
    session = str(row.get("market_session") or "")
    expiry = str(row.get("cluster_expiration") or "")
    ticker = str(row.get("ticker") or "")
    long_symbol = str(nested(row, "atm_contract", "symbol") or "")
    short_symbol = str(nested(row, "target_contract", "symbol") or "")
    base = {
        "market_session": session,
        "ticker": ticker,
        "signal_id": row.get("signal_id"),
        "first_seen_at": row.get("first_seen_at"),
        "rank": row.get("rank"),
        "strength": row.get("strength"),
        "signal_spot": row.get("spot"),
        "signal_target": row.get("target"),
        "target_distance_pct": row.get("target_distance_pct"),
        "cluster_expiration": expiry,
        "long_atm_symbol": long_symbol or None,
        "short_target_symbol": short_symbol or None,
        "latest_same_day_tier": tier(latest_state or {}),
        "latest_same_day_direction": (latest_state or {}).get("direction"),
        "latest_same_day_rank": (latest_state or {}).get("rank"),
        "latest_same_day_strength": (latest_state or {}).get("strength"),
        "persisted_tier_a_to_last_capture": tier(latest_state or {}) == "tier_a_cluster_only",
    }
    if not all((session, expiry, ticker, long_symbol, short_symbol)):
        return {**base, "status": "unscorable_missing_identity_or_contract"}

    stock_entry_bar = next_session_bar(stock_daily.get(ticker, []), session, expiry)
    if not stock_entry_bar:
        return {**base, "status": "pending_or_missing_next_stock_session"}
    entry_session = str(stock_entry_bar.get("session") or "")
    long_entry_bar = session_bar(option_daily.get(long_symbol, []), entry_session)
    short_entry_bar = session_bar(option_daily.get(short_symbol, []), entry_session)
    if not long_entry_bar or not short_entry_bar:
        return {
            **base,
            "status": "next_session_option_entry_unavailable",
            "entry_session": entry_session,
            "long_entry_available": bool(long_entry_bar),
            "short_entry_available": bool(short_entry_bar),
        }

    long_entry = first_number(long_entry_bar, "o", "vw", "c")
    short_entry = first_number(short_entry_bar, "o", "vw", "c")
    stock_entry = first_number(stock_entry_bar, "o", "vw", "c")
    if long_entry is None or short_entry is None or stock_entry is None:
        return {**base, "status": "next_session_open_price_unavailable", "entry_session": entry_session}
    entry_debit = long_entry - short_entry
    target = finite(row.get("target"))
    target_reached_before_entry = target is not None and stock_entry >= target
    target_remaining_at_entry_pct = (
        (target / stock_entry - 1.0) * 100.0 if target is not None and stock_entry not in {None, 0} else None
    )
    target_remaining_bucket = remaining_target_bucket(
        target_remaining_at_entry_pct,
        reached_before_entry=target_reached_before_entry,
    )
    long_strike = finite(nested(row, "atm_contract", "strike_price"))
    short_strike = finite(nested(row, "target_contract", "strike_price"))
    width = short_strike - long_strike if long_strike is not None and short_strike is not None else None
    if entry_debit <= 0 or (width is not None and entry_debit >= width):
        return {
            **base,
            "status": "invalid_next_session_spread_debit",
            "entry_session": entry_session,
            "long_entry_price": long_entry,
            "short_entry_price": short_entry,
            "entry_debit": entry_debit,
            "spread_width": width,
            "target_reached_before_entry": target_reached_before_entry,
            "target_remaining_at_entry_pct": target_remaining_at_entry_pct,
            "target_remaining_bucket": target_remaining_bucket,
        }

    cutoff = min(expiry, latest_market_session)
    long_final_bar = last_bar_through(option_daily.get(long_symbol, []), start_session=entry_session, cutoff=cutoff)
    short_final_bar = last_bar_through(option_daily.get(short_symbol, []), start_session=entry_session, cutoff=cutoff)
    stock_final_bar = last_bar_through(stock_daily.get(ticker, []), start_session=entry_session, cutoff=cutoff)
    if not long_final_bar or not short_final_bar or not stock_final_bar:
        return {
            **base,
            "status": "entry_available_mark_unavailable",
            "entry_session": entry_session,
            "entry_debit": entry_debit,
        }

    long_mark = first_number(long_final_bar, "c", "vw")
    short_mark = first_number(short_final_bar, "c", "vw")
    stock_mark = first_number(stock_final_bar, "c", "vw")
    if long_mark is None or short_mark is None or stock_mark is None:
        return {
            **base,
            "status": "entry_available_mark_price_unavailable",
            "entry_session": entry_session,
            "entry_debit": entry_debit,
        }
    spread_mark = long_mark - short_mark
    stock_highs = [
        first_number(bar, "h", "c")
        for bar in stock_daily.get(ticker, [])
        if entry_session <= str(bar.get("session") or "") <= cutoff
    ]
    stock_highs = [value for value in stock_highs if value is not None]
    status = "matured_at_expiry" if expiry <= latest_market_session else "pending_marked_to_latest"
    return {
        **base,
        "status": status,
        "entry_session": entry_session,
        "entry_basis": "each option leg daily open on the next underlying market session",
        "stock_entry_price": stock_entry,
        "long_entry_price": long_entry,
        "short_entry_price": short_entry,
        "entry_debit": entry_debit,
        "spread_width": width,
        "target_reached_before_entry": target_reached_before_entry,
        "target_remaining_at_entry_pct": target_remaining_at_entry_pct,
        "target_remaining_bucket": target_remaining_bucket,
        "mark_session": str(stock_final_bar.get("session") or "") or None,
        "stock_mark_price": stock_mark,
        "underlying_return_pct": (stock_mark / stock_entry - 1.0) * 100.0,
        "target_hit_after_delayed_entry": target is not None and bool(stock_highs) and max(stock_highs) >= target,
        "long_mark_price": long_mark,
        "short_mark_price": short_mark,
        "spread_mark_value": spread_mark,
        "spread_return_pct": (spread_mark / entry_debit - 1.0) * 100.0,
        "profitable_spread": spread_mark > entry_debit,
    }


def remaining_target_bucket(value: Any, *, reached_before_entry: bool = False) -> str:
    if reached_before_entry:
        return "target_reached_before_entry"
    number = finite(value)
    if number is None:
        return "unavailable"
    if number < 1.0:
        return "under_1_pct"
    if number < 2.0:
        return "1_to_2_pct"
    if number < 5.0:
        return "2_to_5_pct"
    if number <= 10.0:
        return "5_to_10_pct"
    return "over_10_pct"


def metric(values: Iterable[Any]) -> dict[str, Any]:
    numbers = [value for raw in values if (value := finite(raw)) is not None]
    return {
        "available": len(numbers),
        "average": mean(numbers) if numbers else None,
        "median": median(numbers) if numbers else None,
        "positive_fraction": sum(value > 0 for value in numbers) / len(numbers) if numbers else None,
    }


def summarize_delayed(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    scored = [row for row in rows if row.get("status") in {"matured_at_expiry", "pending_marked_to_latest"}]
    matured = [row for row in scored if row.get("status") == "matured_at_expiry"]
    return {
        "observations": len(rows),
        "status_counts": dict(Counter(str(row.get("status")) for row in rows)),
        "scored": len(scored),
        "matured_at_expiry": len(matured),
        "spread_return_pct": metric(row.get("spread_return_pct") for row in scored),
        "underlying_return_pct": metric(row.get("underlying_return_pct") for row in scored),
        "target_hit_fraction": (
            sum(bool(row.get("target_hit_after_delayed_entry")) for row in scored) / len(scored)
            if scored
            else None
        ),
    }


def summarize_grouped(rows: Sequence[Mapping[str, Any]], key: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get(key) or "unavailable")].append(row)
    return [
        {key: group_name, **summarize_delayed(group_rows)}
        for group_name, group_rows in sorted(grouped.items())
    ]


def relative_quote_width(contract: Mapping[str, Any]) -> float | None:
    bid = finite(contract.get("bid"))
    ask = finite(contract.get("ask"))
    mid = finite(contract.get("mid"))
    if bid is None or ask is None or mid in {None, 0}:
        return None
    return max(ask - bid, 0.0) / mid


def quote_trade_geometry(
    *,
    long_contract: Mapping[str, Any] | None,
    short_contract: Mapping[str, Any] | None,
    long_strike: float | None,
    short_strike: float | None,
    underlying_spot: float | None,
) -> dict[str, Any]:
    long_contract = long_contract or {}
    short_contract = short_contract or {}
    long_bid = finite(long_contract.get("bid"))
    long_ask = finite(long_contract.get("ask"))
    long_mid = finite(long_contract.get("mid"))
    short_bid = finite(short_contract.get("bid"))
    short_ask = finite(short_contract.get("ask"))
    short_mid = finite(short_contract.get("mid"))
    width = short_strike - long_strike if long_strike is not None and short_strike is not None else None
    midpoint_debit = long_mid - short_mid if long_mid is not None and short_mid is not None else None
    natural_debit = long_ask - short_bid if long_ask is not None and short_bid is not None else None
    marketable_credit_side = long_bid - short_ask if long_bid is not None and short_ask is not None else None
    marketable_debit_floor = max(marketable_credit_side, 0.0) if marketable_credit_side is not None else None
    valid = bool(
        width is not None
        and width > 0
        and midpoint_debit is not None
        and natural_debit is not None
        and 0 < midpoint_debit < width
        and 0 < natural_debit < width
    )
    reference_limit = round(midpoint_debit, 2) if valid else None
    max_profit_mid = width - midpoint_debit if valid else None
    return {
        "quote_available": all(
            value is not None
            for value in (long_bid, long_ask, long_mid, short_bid, short_ask, short_mid)
        ),
        "valid_debit_geometry": valid,
        "spread_width": width,
        "long_bid": long_bid,
        "long_ask": long_ask,
        "long_mid": long_mid,
        "short_bid": short_bid,
        "short_ask": short_ask,
        "short_mid": short_mid,
        "midpoint_debit": midpoint_debit,
        "natural_debit": natural_debit,
        "marketable_debit_floor": marketable_debit_floor,
        "reference_limit_debit_at_snapshot": reference_limit,
        "reference_max_loss_per_spread": reference_limit * 100.0 if reference_limit is not None else None,
        "reference_max_profit_per_spread": max_profit_mid * 100.0 if max_profit_mid is not None else None,
        "reference_max_return_on_risk_pct": (
            max_profit_mid / midpoint_debit * 100.0 if valid and midpoint_debit else None
        ),
        "reference_breakeven": long_strike + midpoint_debit if valid and long_strike is not None else None,
        "reference_breakeven_distance_pct": (
            ((long_strike + midpoint_debit) / underlying_spot - 1.0) * 100.0
            if valid and long_strike is not None and underlying_spot not in {None, 0}
            else None
        ),
        "long_relative_quote_width": relative_quote_width(long_contract),
        "short_relative_quote_width": relative_quote_width(short_contract),
        "long_volume": finite(long_contract.get("volume")),
        "short_volume": finite(short_contract.get("volume")),
        "long_open_interest": finite(long_contract.get("open_interest")),
        "short_open_interest": finite(short_contract.get("open_interest")),
        "long_delta": finite(long_contract.get("delta")),
        "short_delta": finite(short_contract.get("delta")),
        "long_iv": finite(long_contract.get("iv")),
        "short_iv": finite(short_contract.get("iv")),
        "quote_time": max(
            str(long_contract.get("quote_time") or ""),
            str(short_contract.get("quote_time") or ""),
        )
        or None,
    }


def liquidity_label(geometry: Mapping[str, Any]) -> str:
    if not geometry.get("valid_debit_geometry"):
        return "unusable_reference_quote"
    widths = [
        finite(geometry.get("long_relative_quote_width")),
        finite(geometry.get("short_relative_quote_width")),
    ]
    widths = [value for value in widths if value is not None]
    minimum_volume = min(
        finite(geometry.get("long_volume")) or 0.0,
        finite(geometry.get("short_volume")) or 0.0,
    )
    minimum_oi = min(
        finite(geometry.get("long_open_interest")) or 0.0,
        finite(geometry.get("short_open_interest")) or 0.0,
    )
    widest = max(widths) if widths else math.inf
    if minimum_volume >= 50 and minimum_oi >= 100 and widest <= 0.15:
        return "strong_reference_liquidity"
    if minimum_volume >= 10 and minimum_oi >= 25 and widest <= 0.30:
        return "acceptable_reference_liquidity"
    return "weak_or_wide_reference_liquidity"


def current_candidate(
    first_row: Mapping[str, Any],
    latest_row: Mapping[str, Any],
    *,
    refresh_quotes: bool,
) -> dict[str, Any]:
    ticker = str(first_row.get("ticker") or "")
    expiry = str(latest_row.get("cluster_expiration") or "")
    long_symbol = str(nested(latest_row, "atm_contract", "symbol") or "")
    short_symbol = str(nested(latest_row, "target_contract", "symbol") or "")
    long_strike = finite(nested(latest_row, "atm_contract", "strike_price"))
    short_strike = finite(nested(latest_row, "target_contract", "strike_price"))
    persisted = tier(latest_row) == "tier_a_cluster_only" and str(latest_row.get("direction")) == "BULLISH"
    base = {
        "ticker": ticker,
        "signal_session": first_row.get("market_session"),
        "first_qualifying_at": first_row.get("first_seen_at"),
        "first_qualifying_rank": first_row.get("rank"),
        "first_qualifying_strength": first_row.get("strength"),
        "first_qualifying_target": first_row.get("target"),
        "latest_capture_at": latest_row.get("first_seen_at"),
        "latest_direction": latest_row.get("direction"),
        "latest_tier": tier(latest_row),
        "latest_rank": latest_row.get("rank"),
        "latest_strength": latest_row.get("strength"),
        "latest_spot_at_capture": latest_row.get("spot"),
        "latest_target": latest_row.get("target"),
        "latest_target_distance_pct": latest_row.get("target_distance_pct"),
        "persisted_tier_a_to_last_capture": persisted,
        "cluster_expiration": expiry or None,
        "long_atm_contract": long_symbol or None,
        "long_strike": long_strike,
        "short_target_contract": short_symbol or None,
        "short_strike": short_strike,
        "research_structure": "long ATM call / short target call debit spread",
        "execution_authority": False,
    }
    if not persisted:
        return {**base, "plan_status": "watch_only_latest_cluster_state_not_tier_a"}
    if not all((ticker, expiry, long_symbol, short_symbol)):
        return {**base, "plan_status": "watch_only_missing_contract_identity"}
    try:
        stock_quote = quote(ticker)
        chain = option_chain(
            ticker,
            "opra",
            force=refresh_quotes,
            max_pages=8,
            expiration_gte=expiry,
            expiration_lte=expiry,
        )
    except Exception as exc:  # network/data failures are normal and remain explicit
        return {**base, "plan_status": "watch_only_provider_error", "provider_error": str(exc)}
    by_symbol = {str(row.get("symbol") or ""): row for row in chain}
    long_contract = by_symbol.get(long_symbol)
    short_contract = by_symbol.get(short_symbol)
    spot = finite(stock_quote.get("price_context")) or finite(stock_quote.get("last"))
    target = finite(latest_row.get("target"))
    remaining = ((target / spot - 1.0) * 100.0) if target is not None and spot not in {None, 0} else None
    target_already_reached = target is not None and spot is not None and spot >= target
    target_remaining_bucket = remaining_target_bucket(remaining, reached_before_entry=target_already_reached)
    try:
        calendar_days_to_expiry = (date.fromisoformat(expiry) - date.fromisoformat(str(first_row.get("market_session")))).days
    except (TypeError, ValueError):
        calendar_days_to_expiry = None
    geometry = quote_trade_geometry(
        long_contract=long_contract,
        short_contract=short_contract,
        long_strike=long_strike,
        short_strike=short_strike,
        underlying_spot=spot,
    )
    liquidity = liquidity_label(geometry)
    if target_already_reached:
        status = "skip_target_already_reached"
    elif remaining is not None and remaining < 1.0:
        status = "conditional_refresh_required_target_nearly_reached"
    elif calendar_days_to_expiry is not None and calendar_days_to_expiry <= 4:
        status = "conditional_refresh_required_very_short_expiry"
    elif not geometry.get("valid_debit_geometry"):
        status = "watch_only_no_valid_reference_debit"
    elif liquidity == "weak_or_wide_reference_liquidity":
        status = "conditional_refresh_required_weak_reference_liquidity"
    else:
        status = "next_session_watch_refresh_required"
    return {
        **base,
        "plan_status": status,
        "underlying_quote": stock_quote,
        "underlying_reference_spot": spot,
        "target_remaining_distance_pct": remaining,
        "target_remaining_bucket": target_remaining_bucket,
        "calendar_days_from_signal_to_expiry": calendar_days_to_expiry,
        "liquidity_label": liquidity,
        "quote_geometry": geometry,
        "entry_instruction": (
            "At the next regular-session open, refresh both option quotes; use a limit debit near the live spread midpoint, "
            "do not exceed the live natural debit, and skip if the latest Cluster state is no longer Tier A or the target is reached."
        ),
        "exit_research_rule": "close by Cluster expiration; target-hit and profit-protection exits remain under research",
    }


def candidate_sort_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    status_order = {
        "next_session_watch_refresh_required": 0,
        "conditional_refresh_required_target_nearly_reached": 1,
        "conditional_refresh_required_very_short_expiry": 2,
        "conditional_refresh_required_weak_reference_liquidity": 3,
        "watch_only_no_valid_reference_debit": 4,
        "watch_only_provider_error": 5,
        "watch_only_missing_contract_identity": 6,
        "watch_only_latest_cluster_state_not_tier_a": 7,
        "skip_target_already_reached": 8,
    }
    liquidity_order = {
        "strong_reference_liquidity": 0,
        "acceptable_reference_liquidity": 1,
        "weak_or_wide_reference_liquidity": 2,
        "unusable_reference_quote": 3,
    }
    return (
        status_order.get(str(row.get("plan_status")), 99),
        liquidity_order.get(str(row.get("liquidity_label")), 99),
        finite(row.get("latest_rank")) or 999,
        str(row.get("ticker") or ""),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--force-provider-refresh", action="store_true")
    parser.add_argument("--skip-current-quotes", action="store_true")
    args = parser.parse_args()

    payload = json.loads(SOURCE.read_text(encoding="utf-8"))
    records = payload.get("records") if isinstance(payload.get("records"), list) else []
    selected = first_tier_a_per_ticker_session(records)
    latest_map = latest_record_per_ticker_session(records)
    if not selected:
        raise RuntimeError("no first-qualifying Tier A Cluster records")

    created_at = datetime.now(UTC)
    headers = provider_headers()
    option_symbols = sorted(
        {
            str(symbol)
            for row in selected
            for symbol in (nested(row, "atm_contract", "symbol"), nested(row, "target_contract", "symbol"))
            if symbol
        }
    )
    stock_symbols = sorted({str(row.get("ticker")) for row in selected if row.get("ticker")})
    first_session = min(str(row.get("market_session")) for row in selected)
    start = datetime.combine(datetime.fromisoformat(first_session).date(), dt_time(0, 0), tzinfo=UTC)
    option_raw = load_or_fetch_daily_bars(
        option_symbols,
        root=OPTION_DAILY_CACHE,
        url=OPTION_BARS_URL,
        headers=headers,
        stock=False,
        start=start,
        end=created_at,
        workers=max(1, min(args.workers, 4)),
        force=args.force_provider_refresh,
    )
    stock_raw = load_or_fetch_daily_bars(
        stock_symbols,
        root=STOCK_DAILY_CACHE,
        url=STOCK_BARS_URL,
        headers=headers,
        stock=True,
        start=start,
        end=created_at,
        workers=max(1, min(args.workers, 4)),
        force=args.force_provider_refresh,
    )
    option_daily = prepare_daily_bars(option_raw)
    stock_daily = prepare_daily_bars(stock_raw)
    fallback = str(latest_dataset().get("latest_session") or payload.get("summary", {}).get("latest_completed_market_session"))
    latest_session = latest_completed_market_session(stock_raw, now=created_at, fallback=fallback)

    delayed = [
        delayed_entry_record(
            row,
            option_daily=option_daily,
            stock_daily=stock_daily,
            latest_market_session=latest_session,
            latest_state=latest_map.get((str(row.get("market_session")), str(row.get("ticker")))),
        )
        for row in selected
    ]
    persisted_delayed = [row for row in delayed if row.get("persisted_tier_a_to_last_capture")]
    degraded_delayed = [row for row in delayed if not row.get("persisted_tier_a_to_last_capture")]

    latest_signal_session = max(str(row.get("market_session")) for row in selected)
    latest_first = [row for row in selected if str(row.get("market_session")) == latest_signal_session]
    candidates = []
    for row in latest_first:
        latest_row = latest_map.get((latest_signal_session, str(row.get("ticker")))) or row
        candidates.append(
            current_candidate(
                row,
                latest_row,
                refresh_quotes=args.force_provider_refresh and not args.skip_current_quotes,
            )
            if not args.skip_current_quotes
            else {
                "ticker": row.get("ticker"),
                "signal_session": latest_signal_session,
                "plan_status": "quotes_skipped",
                "persisted_tier_a_to_last_capture": tier(latest_row) == "tier_a_cluster_only",
                "latest_tier": tier(latest_row),
                "cluster_expiration": latest_row.get("cluster_expiration"),
                "long_atm_contract": nested(latest_row, "atm_contract", "symbol"),
                "short_target_contract": nested(latest_row, "target_contract", "symbol"),
                "execution_authority": False,
            }
        )
    candidates.sort(key=candidate_sort_key)
    for index, row in enumerate(candidates, start=1):
        row["research_rank"] = index

    output = {
        "schema_version": 1,
        "created_at": created_at.isoformat(),
        "status": "completed",
        "mode": "cluster_tier_a_delayed_entry_research_and_read_only_trade_board",
        "source_report": str(SOURCE),
        "source_report_id": payload.get("report_id"),
        "latest_completed_market_session": latest_session,
        "latest_signal_session": latest_signal_session,
        "historical_rule_boundary": {
            "original_rule": "first traded option minute at or after the first qualifying Tier A Cluster episode",
            "newly_tested_rule": "each spread leg daily open on the next underlying market session",
            "reason": "after-close trade preparation must not silently reuse same-session evidence",
        },
        "delayed_entry_research": {
            "all_first_qualifying_tier_a": summarize_delayed(delayed),
            "persisted_tier_a_to_last_same_day_capture": summarize_delayed(persisted_delayed),
            "degraded_before_last_same_day_capture": summarize_delayed(degraded_delayed),
            "by_target_remaining_at_next_open": summarize_grouped(delayed, "target_remaining_bucket"),
            "records": delayed,
        },
        "current_trade_board": {
            "selection": "first qualifying Tier A per ticker on the latest signal session, then require the latest same-day Cluster state to remain Tier A",
            "quote_basis": "latest Alpaca OPRA option snapshot and SIP underlying quote; after-hours values are references only",
            "candidates": candidates,
            "status_counts": dict(Counter(str(row.get("plan_status")) for row in candidates)),
        },
        "research_limits": {
            "next_session_option_entry_uses_each_leg_daily_open": True,
            "historical_bid_ask_unavailable": True,
            "daily_leg_opens_are_not_proof_of_simultaneous_fill": True,
            "current_option_quotes_must_be_refreshed_at_entry": True,
            "post_hoc_tier_a_definition": True,
            "pending_expiry_results_are_not_final": True,
            "current_trade_board_is_not_a_broker_order": True,
        },
        "automatic_promotion": False,
        "paper_or_live_execution": False,
        "execution_authority": False,
    }
    output["report_id"] = stable_id(
        "cluster_tier_a_delayed_entry_and_trade_board",
        {
            "source_report_id": output.get("source_report_id"),
            "latest_completed_market_session": latest_session,
            "latest_signal_session": latest_signal_session,
            "candidate_contracts": [
                {
                    "ticker": row.get("ticker"),
                    "long": row.get("long_atm_contract"),
                    "short": row.get("short_target_contract"),
                    "status": row.get("plan_status"),
                }
                for row in candidates
            ],
        },
        length=64,
    )
    atomic_json(OUTPUT, output)
    print(
        json.dumps(
            {
                "status": "completed",
                "output": str(OUTPUT),
                "latest_signal_session": latest_signal_session,
                "delayed_entry": output["delayed_entry_research"]["all_first_qualifying_tier_a"],
                "candidate_status_counts": output["current_trade_board"]["status_counts"],
                "top_candidates": [
                    {
                        "rank": row.get("research_rank"),
                        "ticker": row.get("ticker"),
                        "status": row.get("plan_status"),
                        "liquidity": row.get("liquidity_label"),
                        "reference_limit": nested(row, "quote_geometry", "reference_limit_debit_at_snapshot"),
                        "long": row.get("long_atm_contract"),
                        "short": row.get("short_target_contract"),
                    }
                    for row in candidates[:8]
                ],
                "execution_authority": False,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
