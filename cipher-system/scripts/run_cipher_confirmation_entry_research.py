#!/usr/bin/env python3
"""Re-score confirmed Cluster signals from confirmation-time entries.

This cache-only research pass uses the existing complete-observations report and
its provider bar cache. It does not fetch market data, place orders, or mutate
execution state.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from run_cipher_complete_observations import (  # noqa: E402
    OPTION_DAILY_CACHE,
    OPTION_MINUTE_CACHE,
    OUTPUT as COMPLETE_OUTPUT,
    OUTPUT_ROOT,
    STOCK_DAILY_CACHE,
    STOCK_MINUTE_CACHE,
    atomic_json,
    bar_cache_path,
    cluster_population_metrics,
    daily_latest_states,
    finite,
    first_realtime_confirmation_context,
    flatten_entry_package,
    read_json,
    score_cluster_entry_package,
    summarize_numeric,
    terminal_confirmation_context,
    utc_timestamp,
)

UTC = timezone.utc
OUTPUT = OUTPUT_ROOT / "latest_confirmation_entry_research.json"


def cached_minute_bars(
    requirements: Sequence[tuple[str, str]],
    *,
    root: Path,
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    output: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for session, symbol in sorted(set(requirements)):
        payload = read_json(bar_cache_path(root, symbol, session))
        output[(session, symbol)] = list((payload or {}).get("bars") or []) if isinstance(payload, dict) else []
    return output


def cached_daily_bars(symbols: Sequence[str], *, root: Path) -> dict[str, list[dict[str, Any]]]:
    output: dict[str, list[dict[str, Any]]] = {}
    for symbol in sorted(set(symbols)):
        payload = read_json(bar_cache_path(root, symbol))
        output[symbol] = list((payload or {}).get("bars") or []) if isinstance(payload, dict) else []
    return output


def flatten_original_cluster_entry(row: Mapping[str, Any]) -> dict[str, Any]:
    underlying = row.get("underlying") or {}
    atm = row.get("atm_option") or {}
    target = row.get("target_option") or {}
    spread = row.get("debit_spread") or {}
    return {
        "signal_id": row.get("signal_id"),
        "market_session": row.get("market_session"),
        "ticker": row.get("ticker"),
        "direction": row.get("direction"),
        "rank": row.get("rank"),
        "strength": row.get("strength"),
        "target_distance_pct": row.get("target_distance_pct"),
        "cluster_expiration": row.get("cluster_expiration"),
        "agreement_status": row.get("agreement_status"),
        "status": row.get("status"),
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


def entry_record(
    row: Mapping[str, Any],
    *,
    context: Mapping[str, Any],
    package: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "signal_id": row.get("signal_id"),
        "ticker": row.get("ticker"),
        "market_session": row.get("market_session"),
        "cluster_signal_at": row.get("first_seen_at"),
        "cluster_expiration": row.get("cluster_expiration"),
        "rank": row.get("rank"),
        "strength": row.get("strength"),
        "spot": row.get("spot"),
        "target": row.get("target"),
        "target_distance_pct": row.get("target_distance_pct"),
        "confirmation": dict(context),
        "entry_result": dict(package),
    }


def main() -> int:
    report = read_json(COMPLETE_OUTPUT)
    if not isinstance(report, dict):
        raise RuntimeError(f"complete observations report unavailable: {COMPLETE_OUTPUT}")

    cluster_section = report.get("cluster_expiry_research") or {}
    records = list(cluster_section.get("records") or [])
    episodes = list(report.get("all_episode_observations") or [])
    states = list(report.get("all_daily_terminal_states") or [])
    if not states:
        states = daily_latest_states(episodes)
    terminal_contexts = terminal_confirmation_context(states)
    latest_completed_session = str((cluster_section.get("summary") or {}).get("latest_completed_market_session") or "")

    cohort = [
        row
        for row in records
        if str(row.get("direction")) == "BULLISH"
        and str(row.get("agreement_status")) == "all_agree_bullish"
        and str(row.get("market_session") or "") <= latest_completed_session
    ]
    cohort.sort(key=lambda row: (str(row.get("market_session")), str(row.get("ticker"))))

    option_minute_requirements: list[tuple[str, str]] = []
    stock_minute_requirements: list[tuple[str, str]] = []
    option_daily_symbols: list[str] = []
    stock_daily_symbols: list[str] = []
    contexts_by_signal: dict[str, dict[str, Mapping[str, Any]]] = {}

    for row in cohort:
        signal_id = str(row.get("signal_id"))
        key = (str(row.get("market_session")), str(row.get("ticker")))
        terminal = terminal_contexts.get(key)
        realtime = first_realtime_confirmation_context(row, episodes)
        contexts_by_signal[signal_id] = {
            "terminal": terminal or {},
            "realtime": realtime or {},
        }
        stock_daily_symbols.append(str(row.get("ticker")))
        for contract_key in ("atm_contract", "target_contract"):
            symbol = str(((row.get(contract_key) or {}).get("symbol")) or "")
            if symbol:
                option_daily_symbols.append(symbol)
        for context in (terminal, realtime):
            confirmed_at = utc_timestamp((context or {}).get("confirmed_at"))
            if confirmed_at is None:
                continue
            session = confirmed_at.tz_convert("America/New_York").date().isoformat()
            ticker = str(row.get("ticker"))
            stock_minute_requirements.append((session, ticker))
            for contract_key in ("atm_contract", "target_contract"):
                symbol = str(((row.get(contract_key) or {}).get("symbol")) or "")
                if symbol:
                    option_minute_requirements.append((session, symbol))

    option_minute = cached_minute_bars(option_minute_requirements, root=OPTION_MINUTE_CACHE)
    stock_minute = cached_minute_bars(stock_minute_requirements, root=STOCK_MINUTE_CACHE)
    option_daily = cached_daily_bars(option_daily_symbols, root=OPTION_DAILY_CACHE)
    stock_daily = cached_daily_bars(stock_daily_symbols, root=STOCK_DAILY_CACHE)

    original_flat: list[dict[str, Any]] = []
    terminal_flat: list[dict[str, Any]] = []
    realtime_flat: list[dict[str, Any]] = []
    terminal_records: list[dict[str, Any]] = []
    realtime_records: list[dict[str, Any]] = []

    for row in cohort:
        signal_id = str(row.get("signal_id"))
        expiry = str(row.get("cluster_expiration") or "")
        original_flat.append(flatten_original_cluster_entry(row))
        for name, package_key, flat_target, record_target in (
            ("terminal", "post_terminal_confirmation_entry", terminal_flat, terminal_records),
            ("realtime", "first_realtime_confirmation_entry", realtime_flat, realtime_records),
        ):
            context = contexts_by_signal[signal_id][name]
            confirmed_at = utc_timestamp(context.get("confirmed_at"))
            if confirmed_at is None or not expiry:
                continue
            package = score_cluster_entry_package(
                row,
                entry_at=confirmed_at,
                entry_context=context,
                expiry=expiry,
                option_minute=option_minute,
                option_daily=option_daily,
                stock_minute=stock_minute,
                stock_daily=stock_daily,
                latest_market_session=latest_completed_session,
            )
            enriched = {**row, package_key: package}
            flattened = flatten_entry_package(enriched, package_key)
            if flattened is not None:
                flat_target.append(flattened)
            record_target.append(entry_record(row, context=context, package=package))

    strict_signal_ids = {
        str(row.get("signal_id"))
        for row in cohort
        if (finite(row.get("rank")) or float("inf")) <= 10
        and 200 <= (finite(row.get("strength")) or float("-inf")) < 300
        and 2 <= (finite(row.get("target_distance_pct")) or float("-inf")) <= 10
    }
    value_fields = (
        "underlying_directional_return_pct",
        "underlying_maximum_favorable_move_pct",
        "underlying_maximum_adverse_move_pct",
        "atm_option_end_return_pct",
        "atm_option_maximum_return_pct",
        "target_option_end_return_pct",
        "target_option_maximum_return_pct",
        "debit_spread_end_return_pct",
    )
    payload = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "status": "completed",
        "mode": "cache_only_confirmation_entry_research",
        "source_complete_report": str(COMPLETE_OUTPUT),
        "source_report_id": report.get("report_id"),
        "latest_completed_market_session": latest_completed_session,
        "cohort_definition": "bullish Cluster records whose selected terminal source states all agree bullish",
        "cohort_observations": len(cohort),
        "strict_filter_observations": len(strict_signal_ids),
        "comparisons": {
            "original_cluster_entry": {
                "definition": "original first option bar at or after the selected Cluster timestamp",
                "metrics": cluster_population_metrics(original_flat),
                "by_ticker_session": summarize_numeric(original_flat, ("ticker", "market_session"), value_fields),
            },
            "post_terminal_confirmation_entry": {
                "definition": "entry after the last selected agreeing terminal source state appears",
                "selection_warning": "cohort membership uses end-of-session terminal-state selection",
                "metrics": cluster_population_metrics(terminal_flat),
                "strict_filter_metrics": cluster_population_metrics(
                    [row for row in terminal_flat if str(row.get("signal_id")) in strict_signal_ids]
                ),
                "by_ticker_session": summarize_numeric(terminal_flat, ("ticker", "market_session"), value_fields),
                "records": terminal_records,
            },
            "first_realtime_one_source_confirmation_entry": {
                "definition": (
                    "entry when at least one currently observed Flash/Agentic state first agrees with the selected Cluster state; "
                    "does not use knowledge of later source coverage"
                ),
                "metrics": cluster_population_metrics(realtime_flat),
                "strict_filter_metrics": cluster_population_metrics(
                    [row for row in realtime_flat if str(row.get("signal_id")) in strict_signal_ids]
                ),
                "by_ticker_session": summarize_numeric(realtime_flat, ("ticker", "market_session"), value_fields),
                "records": realtime_records,
            },
        },
        "research_limits": {
            "historical_option_bid_ask_unavailable": True,
            "entry_uses_first_traded_one_minute_bar": True,
            "terminal_cohort_has_selection_lookahead": True,
            "realtime_rule_is_one_source_confirmation": True,
            "descriptive_not_confirmatory": True,
        },
        "automatic_promotion": False,
        "paper_or_live_execution": False,
        "execution_authority": False,
    }
    atomic_json(OUTPUT, payload)
    print(
        json.dumps(
            {
                "status": payload["status"],
                "cohort_observations": payload["cohort_observations"],
                "strict_filter_observations": payload["strict_filter_observations"],
                "original": payload["comparisons"]["original_cluster_entry"]["metrics"],
                "post_terminal": payload["comparisons"]["post_terminal_confirmation_entry"]["metrics"],
                "first_realtime": payload["comparisons"]["first_realtime_one_source_confirmation_entry"]["metrics"],
                "output": str(OUTPUT),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
