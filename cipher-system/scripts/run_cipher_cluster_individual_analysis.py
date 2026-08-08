#!/usr/bin/env python3
"""Score every unique Cluster episode independently through its own expiry.

The unit of analysis is one globally deduplicated Cluster episode, not a daily
terminal state and not a combined source vote. No Flash, Agentic, agreement,
confirmation, lead-lag, majority-vote, or cross-source field is used.

For each eligible episode the scanner's second listed expiration is
reconstructed, then the underlying, ATM directional option, target-strike
option, and ATM-to-target debit spread are measured from the episode timestamp
through expiration or the latest available completed market session.

Read-only research only. No account, broker-order, or execution authority.
"""
from __future__ import annotations

import argparse
import json
import sys
from bisect import bisect_left
from collections import Counter, defaultdict
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from core.research_platform.cipher_signal_overlay import (  # noqa: E402
    eligible_episode,
    load_signal_episodes,
    signal_file_manifest,
)
from core.research_platform.hashing import stable_id  # noqa: E402
from run_cipher_complete_observations import (  # noqa: E402
    NY,
    OPTION_BARS_URL,
    OPTION_DAILY_CACHE,
    OPTION_MINUTE_CACHE,
    STOCK_BARS_URL,
    STOCK_DAILY_CACHE,
    STOCK_MINUTE_CACHE,
    atomic_json,
    cluster_population_metrics,
    directional_target_distance_pct,
    enrich_cluster_contracts,
    fetch_contract_universe,
    finite,
    latest_completed_market_session,
    latest_dataset,
    load_or_fetch_daily_bars,
    load_or_fetch_partitioned_minute_bars,
    option_path_diagnostics,
    provider_headers,
    rank_bucket,
    signal_time_bucket,
    spread_metrics,
    strength_bucket,
    summarize_numeric,
    target_distance_bucket,
    utc_timestamp,
)

UTC = timezone.utc
CAPTURE_ROOT = ROOT / "data" / "browser_ingest"
GOV = ROOT / "data" / "governance" / "cipher_signal_only"
OUTPUT = GOV / "latest_cluster_individual_analysis.json"

VALUE_FIELDS = (
    "underlying_directional_return_pct",
    "underlying_maximum_favorable_move_pct",
    "underlying_maximum_adverse_move_pct",
    "atm_option_end_return_pct",
    "atm_option_maximum_return_pct",
    "target_option_end_return_pct",
    "target_option_maximum_return_pct",
    "debit_spread_end_return_pct",
)


def exclusion_reason(row: Mapping[str, Any]) -> str | None:
    if not row.get("regular_hours"):
        return "outside_regular_session"
    if not row.get("ticker"):
        return "ticker_missing"
    if str(row.get("direction")) not in {"BULLISH", "BEARISH"}:
        return "direction_missing_or_invalid"
    if not bool(row.get("geometry_valid")):
        return "geometry_invalid"
    return None


def sequence_context(rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(str(row.get("market_session")), str(row.get("ticker")))].append(row)
    output: dict[str, dict[str, Any]] = {}
    for group in grouped.values():
        ordered = sorted(group, key=lambda row: (str(row.get("first_seen_at")), str(row.get("signal_id"))))
        previous: Mapping[str, Any] | None = None
        total = len(ordered)
        for index, row in enumerate(ordered, start=1):
            signal_id = str(row.get("signal_id"))
            rank_now = finite(row.get("rank"))
            rank_before = finite((previous or {}).get("rank"))
            strength_now = finite(row.get("strength"))
            strength_before = finite((previous or {}).get("strength"))
            target_now = finite(row.get("target"))
            target_before = finite((previous or {}).get("target"))
            output[signal_id] = {
                "episode_number_for_ticker_session": index,
                "episodes_for_ticker_session": total,
                "appearance_bucket": "first" if index == 1 else "second" if index == 2 else "third_plus",
                "previous_cluster_signal_id": previous.get("signal_id") if previous else None,
                "direction_changed_from_previous": (
                    str(previous.get("direction")) != str(row.get("direction")) if previous else None
                ),
                "rank_change_from_previous": (
                    rank_now - rank_before if rank_now is not None and rank_before is not None else None
                ),
                "strength_change_from_previous": (
                    strength_now - strength_before
                    if strength_now is not None and strength_before is not None
                    else None
                ),
                "target_change_pct_from_previous": (
                    (target_now / target_before - 1.0) * 100.0
                    if target_now is not None and target_before not in {None, 0}
                    else None
                ),
            }
            previous = row
    return output


def duration_minutes(row: Mapping[str, Any]) -> float | None:
    first = utc_timestamp(row.get("first_seen_at"))
    last = utc_timestamp(row.get("last_seen_at"))
    if first is None or last is None:
        return None
    return max((last - first).total_seconds() / 60.0, 0.0)


def compact_bar(row: Mapping[str, Any]) -> dict[str, Any] | None:
    timestamp = utc_timestamp(row.get("t") or row.get("timestamp"))
    if timestamp is None:
        return None
    return {
        "timestamp": timestamp,
        "session": timestamp.tz_convert(NY).date().isoformat(),
        "o": finite(row.get("o")),
        "h": finite(row.get("h")),
        "l": finite(row.get("l")),
        "c": finite(row.get("c")),
        "vw": finite(row.get("vw")),
    }


def first_number(row: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = finite(row.get(key))
        if value is not None:
            return value
    return None


def prepare_minute_bars(
    raw: Mapping[tuple[str, str], Sequence[Mapping[str, Any]]],
) -> dict[tuple[str, str], dict[str, Any]]:
    output: dict[tuple[str, str], dict[str, Any]] = {}
    for key, rows in raw.items():
        compact = [value for row in rows if (value := compact_bar(row)) is not None]
        compact.sort(key=lambda row: row["timestamp"])
        output[key] = {
            "times": [row["timestamp"] for row in compact],
            "rows": compact,
        }
    return output


def prepare_daily_bars(
    raw: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    output: dict[str, list[dict[str, Any]]] = {}
    for symbol, rows in raw.items():
        compact = [value for row in rows if (value := compact_bar(row)) is not None]
        compact.sort(key=lambda row: row["timestamp"])
        output[str(symbol)] = compact
    return output


def option_leg_metrics_fast(
    *,
    symbol: str | None,
    signal_at: pd.Timestamp,
    expiry: str,
    minute_bars: Mapping[tuple[str, str], Mapping[str, Any]],
    daily_bars: Mapping[str, Sequence[Mapping[str, Any]]],
    latest_market_session: str,
) -> dict[str, Any]:
    if not symbol or not expiry:
        return {"status": "contract_unavailable"}
    session = signal_at.tz_convert(NY).date().isoformat()
    series = minute_bars.get((session, symbol)) or {}
    times = list(series.get("times") or [])
    rows = list(series.get("rows") or [])
    index = bisect_left(times, signal_at)
    intraday = rows[index:]
    if not intraday:
        return {"status": "entry_bar_unavailable", "symbol": symbol}
    entry = first_number(intraday[0], "vw", "c", "o")
    if entry is None or entry <= 0:
        return {"status": "entry_price_unavailable", "symbol": symbol}
    cutoff = min(expiry, latest_market_session)
    later_daily = [
        row
        for row in daily_bars.get(symbol, []) or []
        if session < str(row.get("session") or "") <= cutoff
    ]
    final_row = later_daily[-1] if later_daily else intraday[-1]
    final_price = first_number(final_row, "c", "vw")
    final_timestamp = final_row.get("timestamp")
    highs = [first_number(row, "h", "c") for row in intraday]
    highs.extend(first_number(row, "h", "c") for row in later_daily)
    lows = [first_number(row, "l", "c") for row in intraday]
    lows.extend(first_number(row, "l", "c") for row in later_daily)
    highs = [value for value in highs if value is not None]
    lows = [value for value in lows if value is not None]
    status = "matured_at_expiry" if expiry <= latest_market_session else "pending_expiry_marked_to_latest"
    return {
        "status": status,
        "symbol": symbol,
        "entry_at": intraday[0]["timestamp"].isoformat(),
        "entry_price": entry,
        "mark_at": final_timestamp.isoformat() if final_timestamp is not None else None,
        "mark_session": str(final_row.get("session") or "") or None,
        "mark_basis": "daily_close" if later_daily else "same_session_intraday",
        "mark_price": final_price,
        "end_return_pct": (final_price / entry - 1.0) * 100.0 if final_price is not None else None,
        "maximum_return_pct": (max(highs) / entry - 1.0) * 100.0 if highs else None,
        "minimum_return_pct": (min(lows) / entry - 1.0) * 100.0 if lows else None,
        "profitable_at_mark": final_price > entry if final_price is not None else None,
        "minute_bars_after_signal": len(intraday),
        "later_daily_bars": len(later_daily),
    }


def underlying_metrics_fast(
    row: Mapping[str, Any],
    *,
    signal_at: pd.Timestamp,
    expiry: str,
    minute_bars: Mapping[tuple[str, str], Mapping[str, Any]],
    daily_bars: Mapping[str, Sequence[Mapping[str, Any]]],
    latest_market_session: str,
) -> dict[str, Any]:
    ticker = str(row.get("ticker") or "")
    session = str(row.get("market_session") or "")
    spot = finite(row.get("spot"))
    target = finite(row.get("target"))
    direction = str(row.get("direction") or "")
    if spot is None or spot <= 0:
        return {"status": "scan_spot_unavailable"}
    series = minute_bars.get((session, ticker)) or {}
    times = list(series.get("times") or [])
    rows = list(series.get("rows") or [])
    index = bisect_left(times, signal_at)
    intraday = rows[index:]
    cutoff = min(expiry, latest_market_session)
    later_daily = [
        bar
        for bar in daily_bars.get(ticker, []) or []
        if session < str(bar.get("session") or "") <= cutoff
    ]
    final_row = later_daily[-1] if later_daily else intraday[-1] if intraday else None
    final_price = first_number(final_row or {}, "c", "vw") if final_row else None
    final_timestamp = (final_row or {}).get("timestamp") if final_row else None
    highs = [first_number(bar, "h", "c") for bar in intraday]
    highs.extend(first_number(bar, "h", "c") for bar in later_daily)
    lows = [first_number(bar, "l", "c") for bar in intraday]
    lows.extend(first_number(bar, "l", "c") for bar in later_daily)
    highs = [value for value in highs if value is not None]
    lows = [value for value in lows if value is not None]
    favorable = adverse = target_hit = None
    if highs and lows:
        if direction == "BULLISH":
            favorable = (max(highs) / spot - 1.0) * 100.0
            adverse = (min(lows) / spot - 1.0) * 100.0
            target_hit = target is not None and max(highs) >= target
        elif direction == "BEARISH":
            favorable = (1.0 - min(lows) / spot) * 100.0
            adverse = (1.0 - max(highs) / spot) * 100.0
            target_hit = target is not None and min(lows) <= target
    raw_return = (final_price / spot - 1.0) * 100.0 if final_price is not None else None
    directional_return = raw_return if direction == "BULLISH" else -raw_return if raw_return is not None else None
    status = "matured_at_expiry" if expiry <= latest_market_session else "pending_expiry_marked_to_latest"
    return {
        "status": status,
        "scan_spot": spot,
        "target": target,
        "mark_at": final_timestamp.isoformat() if final_timestamp is not None else None,
        "mark_session": str((final_row or {}).get("session") or "") or None,
        "mark_basis": "daily_close" if later_daily else "same_session_intraday" if intraday else "unavailable",
        "mark_price": final_price,
        "raw_return_pct": raw_return,
        "directional_return_pct": directional_return,
        "direction_correct": directional_return > 0 if directional_return is not None else None,
        "maximum_favorable_move_pct": favorable,
        "maximum_adverse_move_pct": adverse,
        "target_hit_by_mark": target_hit,
        "minute_bars_after_signal": len(intraday),
        "later_daily_bars": len(later_daily),
    }


def standalone_assessment(row: Mapping[str, Any]) -> dict[str, Any]:
    """Assign a descriptive Cluster-only research tier.

    These tiers freeze findings discovered from the current sample. They are
    prospective research labels, not validated recommendations or execution
    instructions.
    """
    direction = str(row.get("direction"))
    rank = finite(row.get("rank"))
    strength = finite(row.get("strength"))
    distance = finite(row.get("target_distance_pct"))
    expiry = str(row.get("cluster_expiration") or "")
    atm = row.get("atm_contract") if isinstance(row.get("atm_contract"), dict) else None
    target = row.get("target_contract") if isinstance(row.get("target_contract"), dict) else None

    reasons: list[str] = []
    cautions: list[str] = []
    if direction != "BULLISH":
        tier = "observe_only_bearish"
        reasons.append("bearish Cluster episodes are retained for research but are not grouped with bullish candidates")
    elif rank is None:
        tier = "observe_only_unranked"
        cautions.append("rank unavailable")
    elif distance is None or distance <= 0:
        tier = "observe_only_invalid_target_geometry"
        cautions.append("target is not ahead in the signaled direction")
    elif rank <= 10 and 200 <= (strength if strength is not None else -1) < 300 and 2 <= distance <= 10:
        tier = "tier_a_cluster_only"
        reasons.extend(("bullish", "rank 1-10", "strength 200-299", "target distance 2-10%"))
    elif rank <= 10 and 2 <= distance <= 10:
        tier = "tier_b_cluster_only"
        reasons.extend(("bullish", "rank 1-10", "target distance 2-10%"))
        if strength is None or not 200 <= strength < 300:
            cautions.append("strength outside preferred 200-299 zone")
    elif rank <= 20 and 0 < distance <= 10:
        tier = "tier_c_cluster_only"
        reasons.extend(("bullish", "rank 1-20", "target distance at most 10%"))
    else:
        tier = "observe_only_cluster"
        if rank > 20:
            cautions.append("rank below top-20 priority")
        if distance > 10:
            cautions.append("target more than 10% away")

    if not expiry:
        cautions.append("expiration unavailable")
    if not atm:
        cautions.append("ATM contract unavailable")
    if not target:
        cautions.append("target-strike contract unavailable")

    same_strike = bool(
        atm
        and target
        and str(atm.get("symbol") or "")
        and str(atm.get("symbol")) == str(target.get("symbol"))
    )
    if tier.startswith("tier_") and atm and target and not same_strike:
        structure = "atm_to_target_debit_spread_research"
    elif tier.startswith("tier_") and atm:
        structure = "atm_directional_option_research"
    else:
        structure = "observation_only"

    return {
        "research_tier": tier,
        "tier_origin": "post_hoc_descriptive_frozen_after_2026_08_06",
        "qualification_reasons": reasons,
        "cautions": cautions,
        "preferred_research_structure": structure,
        "entry_measurement": "first traded one-minute option bar at or after this Cluster episode",
        "uses_other_signal_sources": False,
        "automatic_promotion": False,
        "execution_authority": False,
    }


def flatten_record(row: Mapping[str, Any]) -> dict[str, Any]:
    underlying = row.get("underlying") if isinstance(row.get("underlying"), dict) else {}
    atm = row.get("atm_option") if isinstance(row.get("atm_option"), dict) else {}
    target = row.get("target_option") if isinstance(row.get("target_option"), dict) else {}
    spread = row.get("debit_spread") if isinstance(row.get("debit_spread"), dict) else {}
    assessment = row.get("standalone_assessment") if isinstance(row.get("standalone_assessment"), dict) else {}
    return {
        **{
            key: value
            for key, value in row.items()
            if key not in {"underlying", "atm_option", "target_option", "debit_spread", "standalone_assessment"}
        },
        "research_tier": assessment.get("research_tier"),
        "preferred_research_structure": assessment.get("preferred_research_structure"),
        "underlying_directional_return_pct": underlying.get("directional_return_pct"),
        "underlying_maximum_favorable_move_pct": underlying.get("maximum_favorable_move_pct"),
        "underlying_maximum_adverse_move_pct": underlying.get("maximum_adverse_move_pct"),
        "target_hit_by_expiry": underlying.get("target_hit_by_mark"),
        "atm_option_status": atm.get("status"),
        "target_option_status": target.get("status"),
        "debit_spread_status": spread.get("status"),
        "atm_option_end_return_pct": atm.get("end_return_pct"),
        "atm_option_maximum_return_pct": atm.get("maximum_return_pct"),
        "target_option_end_return_pct": target.get("end_return_pct"),
        "target_option_maximum_return_pct": target.get("maximum_return_pct"),
        "debit_spread_end_return_pct": spread.get("end_return_pct"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--force-provider-refresh", action="store_true")
    args = parser.parse_args()

    created_at = datetime.now(UTC)
    all_episodes = load_signal_episodes(CAPTURE_ROOT)
    cluster_episodes = [dict(row) for row in all_episodes if str(row.get("scan_type")) == "cluster"]
    sequence = sequence_context(cluster_episodes)
    eligible = [row for row in cluster_episodes if eligible_episode(row)]
    excluded = [row for row in cluster_episodes if not eligible_episode(row)]
    if not eligible:
        raise RuntimeError("no eligible Cluster episodes found")
    print(
        json.dumps(
            {
                "stage": "episodes_loaded",
                "total_cluster_episodes": len(cluster_episodes),
                "eligible": len(eligible),
                "excluded": len(excluded),
            },
            sort_keys=True,
        ),
        flush=True,
    )

    first_session = min(str(row["market_session"]) for row in eligible)
    last_session = max(str(row["market_session"]) for row in eligible)
    contract_end = (date.fromisoformat(last_session) + timedelta(days=75)).isoformat()
    contracts_by_ticker, contract_diagnostics = fetch_contract_universe(
        eligible,
        start=first_session,
        end=contract_end,
        workers=max(1, args.workers),
        force=args.force_provider_refresh,
    )
    enriched = enrich_cluster_contracts(eligible, contracts_by_ticker)
    print(
        json.dumps(
            {
                "stage": "contracts_enriched",
                "episodes": len(enriched),
                "expirations": sum(bool(row.get("cluster_expiration")) for row in enriched),
            },
            sort_keys=True,
        ),
        flush=True,
    )

    option_symbols: set[str] = set()
    option_minute_requirements: dict[str, set[str]] = defaultdict(set)
    stock_minute_requirements: dict[str, set[str]] = defaultdict(set)
    for row in enriched:
        session = str(row["market_session"])
        stock_minute_requirements[session].add(str(row["ticker"]))
        for key in ("atm_contract", "target_contract"):
            contract = row.get(key) if isinstance(row.get(key), dict) else {}
            symbol = str(contract.get("symbol") or "")
            if symbol:
                option_symbols.add(symbol)
                option_minute_requirements[session].add(symbol)

    headers = provider_headers()
    option_minute = load_or_fetch_partitioned_minute_bars(
        option_minute_requirements,
        root=OPTION_MINUTE_CACHE,
        url=OPTION_BARS_URL,
        headers=headers,
        stock=False,
        workers=max(1, min(args.workers, 4)),
        force=args.force_provider_refresh,
    )
    stock_minute = load_or_fetch_partitioned_minute_bars(
        stock_minute_requirements,
        root=STOCK_MINUTE_CACHE,
        url=STOCK_BARS_URL,
        headers=headers,
        stock=True,
        workers=max(1, min(args.workers, 4)),
        force=args.force_provider_refresh,
    )
    bars_start = datetime.combine(date.fromisoformat(first_session), dt_time(0, 0), tzinfo=UTC)
    option_daily = load_or_fetch_daily_bars(
        sorted(option_symbols),
        root=OPTION_DAILY_CACHE,
        url=OPTION_BARS_URL,
        headers=headers,
        stock=False,
        start=bars_start,
        end=created_at,
        workers=max(1, min(args.workers, 4)),
        force=args.force_provider_refresh,
    )
    stock_symbols = sorted({str(row["ticker"]) for row in eligible})
    stock_daily = load_or_fetch_daily_bars(
        stock_symbols,
        root=STOCK_DAILY_CACHE,
        url=STOCK_BARS_URL,
        headers=headers,
        stock=True,
        start=bars_start,
        end=created_at,
        workers=max(1, min(args.workers, 4)),
        force=args.force_provider_refresh,
    )
    fallback_session = str(latest_dataset().get("latest_session") or last_session)
    latest_session = latest_completed_market_session(stock_daily, now=created_at, fallback=fallback_session)
    prepared_option_minute = prepare_minute_bars(option_minute)
    prepared_stock_minute = prepare_minute_bars(stock_minute)
    prepared_option_daily = prepare_daily_bars(option_daily)
    prepared_stock_daily = prepare_daily_bars(stock_daily)
    print(
        json.dumps(
            {
                "stage": "bars_indexed",
                "option_minute_series": len(prepared_option_minute),
                "stock_minute_series": len(prepared_stock_minute),
                "option_daily_symbols": len(prepared_option_daily),
                "stock_daily_symbols": len(prepared_stock_daily),
                "latest_completed_market_session": latest_session,
            },
            sort_keys=True,
        ),
        flush=True,
    )

    records: list[dict[str, Any]] = []
    for row in enriched:
        signal_id = str(row.get("signal_id"))
        signal_at = utc_timestamp(row.get("first_seen_at"))
        expiry = str(row.get("cluster_expiration") or "")
        base = {
            key: row.get(key)
            for key in (
                "signal_id",
                "signal_signature",
                "market_session",
                "first_seen_at",
                "last_seen_at",
                "seen_count",
                "ticker",
                "direction",
                "rank",
                "strength",
                "spot",
                "target",
                "geometry_valid",
                "cluster_expiration",
                "expiration_reconstruction_method",
                "option_type",
                "atm_contract",
                "target_contract",
                "source_file",
            )
        }
        base.update(sequence.get(signal_id) or {})
        base["episode_duration_minutes"] = duration_minutes(row)
        base["rank_bucket"] = rank_bucket(row.get("rank"))
        base["strength_bucket"] = strength_bucket(row.get("strength"))
        base["target_distance_pct"] = directional_target_distance_pct(row)
        base["target_distance_bucket"] = target_distance_bucket(base["target_distance_pct"])
        base["signal_time_bucket"] = signal_time_bucket(row.get("first_seen_at"))
        assessment_input = {**base}
        base["standalone_assessment"] = standalone_assessment(assessment_input)
        if signal_at is None or not expiry:
            records.append({**base, "status": "unscorable_missing_signal_time_or_expiration"})
            continue
        atm_contract = row.get("atm_contract") if isinstance(row.get("atm_contract"), dict) else {}
        target_contract = row.get("target_contract") if isinstance(row.get("target_contract"), dict) else {}
        atm = option_leg_metrics_fast(
            symbol=atm_contract.get("symbol"),
            signal_at=signal_at,
            expiry=expiry,
            minute_bars=prepared_option_minute,
            daily_bars=prepared_option_daily,
            latest_market_session=latest_session,
        )
        target_option = option_leg_metrics_fast(
            symbol=target_contract.get("symbol"),
            signal_at=signal_at,
            expiry=expiry,
            minute_bars=prepared_option_minute,
            daily_bars=prepared_option_daily,
            latest_market_session=latest_session,
        )
        underlying = underlying_metrics_fast(
            row,
            signal_at=signal_at,
            expiry=expiry,
            minute_bars=prepared_stock_minute,
            daily_bars=prepared_stock_daily,
            latest_market_session=latest_session,
        )
        spread = spread_metrics(atm, target_option)
        status = "matured_at_expiry" if expiry <= latest_session else "pending_expiry_marked_to_latest"
        records.append(
            {
                **base,
                "status": status,
                "underlying": underlying,
                "atm_option": atm,
                "target_option": target_option,
                "debit_spread": spread,
            }
        )

    print(json.dumps({"stage": "episodes_scored", "records": len(records)}, sort_keys=True), flush=True)

    excluded_records = []
    for row in excluded:
        signal_id = str(row.get("signal_id"))
        excluded_records.append(
            {
                **row,
                **(sequence.get(signal_id) or {}),
                "status": "excluded_from_outcome_scoring",
                "exclusion_reason": exclusion_reason(row) or "not_eligible",
                "uses_other_signal_sources": False,
            }
        )

    flat = [flatten_record(row) for row in records]
    matured = [row for row in flat if row.get("status") == "matured_at_expiry"]
    pending = [row for row in flat if row.get("status") == "pending_expiry_marked_to_latest"]
    unscorable = [row for row in flat if str(row.get("status", "")).startswith("unscorable")]
    completed_sessions = [row for row in flat if str(row.get("market_session")) <= latest_session]
    current_partial = [row for row in flat if str(row.get("market_session")) > latest_session]

    summary = {
        "analysis_unit": "one globally deduplicated Cluster episode",
        "latest_completed_market_session": latest_session,
        "total_cluster_episodes": len(cluster_episodes),
        "eligible_regular_session_episodes": len(eligible),
        "excluded_episodes": len(excluded_records),
        "scored_or_pending_records": len(records),
        "matured_at_expiry": len(matured),
        "pending_expiry": len(pending),
        "unscorable": len(unscorable),
        "unique_tickers": len({str(row.get("ticker")) for row in cluster_episodes if row.get("ticker")}),
        "market_sessions": len({str(row.get("market_session")) for row in cluster_episodes}),
        "all_final_and_pending": cluster_population_metrics(flat),
        "finalized_at_expiry": cluster_population_metrics(matured),
        "pending_mark_to_latest": cluster_population_metrics(pending),
        "completed_sessions_only": cluster_population_metrics(completed_sessions),
        "current_partial_sessions": cluster_population_metrics(current_partial),
        "by_direction": summarize_numeric(completed_sessions, ("direction",), VALUE_FIELDS),
        "by_rank_bucket": summarize_numeric(completed_sessions, ("rank_bucket",), VALUE_FIELDS),
        "by_strength_bucket": summarize_numeric(completed_sessions, ("strength_bucket",), VALUE_FIELDS),
        "by_target_distance_bucket": summarize_numeric(
            completed_sessions, ("direction", "target_distance_bucket"), VALUE_FIELDS
        ),
        "by_signal_time_bucket": summarize_numeric(
            completed_sessions, ("direction", "signal_time_bucket"), VALUE_FIELDS
        ),
        "by_research_tier": summarize_numeric(completed_sessions, ("research_tier",), VALUE_FIELDS),
        "by_appearance_bucket": summarize_numeric(
            completed_sessions, ("direction", "appearance_bucket"), VALUE_FIELDS
        ),
        "by_episode_number": summarize_numeric(
            completed_sessions, ("direction", "episode_number_for_ticker_session"), VALUE_FIELDS
        ),
        "by_expiration": summarize_numeric(completed_sessions, ("cluster_expiration",), VALUE_FIELDS),
        "by_market_session": summarize_numeric(completed_sessions, ("market_session",), VALUE_FIELDS),
        "by_ticker": summarize_numeric(completed_sessions, ("ticker",), VALUE_FIELDS),
        "option_path_diagnostics": {
            "atm_option": option_path_diagnostics(completed_sessions, "atm_option"),
            "target_option": option_path_diagnostics(completed_sessions, "target_option"),
        },
        "status_counts": dict(Counter(str(row.get("status")) for row in flat)),
        "exclusion_counts": dict(Counter(str(row.get("exclusion_reason")) for row in excluded_records)),
        "tier_counts": dict(Counter(str(row.get("research_tier")) for row in flat)),
    }

    payload = {
        "schema_version": 1,
        "created_at": created_at.isoformat(),
        "status": "completed",
        "mode": "cluster_individual_episode_analysis",
        "source_boundary": {
            "source": "cluster",
            "uses_other_signal_sources": False,
            "cross_source_confirmation": False,
            "cross_source_veto": False,
            "cross_source_ranking": False,
        },
        "primary_horizon": "scanner_second_listed_option_expiration",
        "entry_measurement": "first traded one-minute bar at or after each individual Cluster episode",
        "contract_selection": {
            "directional_type": "call for bullish and put for bearish",
            "atm_leg": "listed strike nearest that episode's captured spot",
            "target_leg": "listed strike nearest that episode's captured target",
            "debit_spread": "long ATM directional option and short target-strike directional option",
        },
        "capture_manifest": signal_file_manifest(CAPTURE_ROOT),
        "summary": summary,
        "contract_download_diagnostics": contract_diagnostics,
        "records": records,
        "excluded_records": excluded_records,
        "research_limits": {
            "historical_bid_ask_unavailable": True,
            "entry_uses_first_traded_minute_bar": True,
            "illiquid_contract_without_post_signal_trade_is_unscorable": True,
            "pending_expirations_are_marked_not_final": True,
            "research_tiers_are_post_hoc_and_prospective_only": True,
            "daily_terminal_state_collapse_used": False,
            "other_signal_sources_used": False,
        },
        "automatic_promotion": False,
        "paper_or_live_execution": False,
        "execution_authority": False,
    }
    payload["report_id"] = stable_id(
        "cipher_cluster_individual_episode_analysis",
        {
            "capture_manifest": payload["capture_manifest"],
            "latest_completed_market_session": latest_session,
            "eligible_records": len(records),
            "excluded_records": len(excluded_records),
        },
        length=64,
    )
    atomic_json(OUTPUT, payload)
    print(
        json.dumps(
            {
                "status": "completed",
                "mode": payload["mode"],
                "output": str(OUTPUT),
                "summary": {
                    key: summary.get(key)
                    for key in (
                        "total_cluster_episodes",
                        "eligible_regular_session_episodes",
                        "excluded_episodes",
                        "matured_at_expiry",
                        "pending_expiry",
                        "unscorable",
                        "unique_tickers",
                        "market_sessions",
                    )
                },
                "uses_other_signal_sources": False,
                "execution_authority": False,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
