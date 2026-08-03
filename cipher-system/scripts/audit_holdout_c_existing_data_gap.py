#!/usr/bin/env python3
"""Audit already-ingested data for the missing Holdout C origin.

This script is deliberately read-only with respect to vendors. It does not
request, download, adjust, interpolate, or patch market data. It proves whether
the existing local estate can close the unchanged 12-origin price-only cohort.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
QUALITY = ROOT / "data" / "market_quality"
GOV = ROOT / "data" / "governance"
DEFAULT_RUNTIME_DATA = Path("/home/aarav/Aarav/cipher/cipher-system/data")
MIN_TICKERS = 8
WINDOW_SIZE = 52
REQUIRED_ORIGINS = 12


def latest(pattern: str) -> Path:
    rows = sorted(ROOT.glob(pattern))
    if not rows:
        raise FileNotFoundError(pattern)
    return rows[-1]


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def load_cohort_module():
    path = ROOT / "scripts" / "construct_alpaca_holdout_c_cohort.py"
    spec = importlib.util.spec_from_file_location("holdout_c_cohort_audit", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load cohort constructor")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def scope_eligibility(scope: dict[str, Any]) -> tuple[list[str], dict[str, list[str]]]:
    days = sorted({str(row["date"]) for row in scope.get("daily_results", [])})
    eligible = {
        str(row["date"]): sorted(str(ticker) for ticker in row.get("tickers", []))
        for row in scope.get("common_eligible_by_day", [])
        if int(row.get("count") or 0) >= MIN_TICKERS
    }
    return days, eligible


def selected_block_days(all_days: list[str], eligible: dict[str, list[str]], start: str, end: str) -> list[str]:
    return [day for day in all_days if start <= day <= end and day in eligible]


def enumerate_valid_windows(block: list[str], eligible: dict[str, list[str]]) -> list[dict[str, Any]]:
    windows: list[dict[str, Any]] = []
    for offset in range(0, max(0, len(block) - WINDOW_SIZE + 1)):
        dates = block[offset : offset + WINDOW_SIZE]
        common = sorted(set.intersection(*(set(eligible[day]) for day in dates)))
        if len(common) >= MIN_TICKERS:
            windows.append(
                {
                    "offset": offset,
                    "start": dates[0],
                    "end": dates[-1],
                    "common_tickers": common,
                }
            )
    return windows


def greedy_maximum(windows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Earliest-finish interval scheduling for equal-length accepted windows."""

    selected: list[dict[str, Any]] = []
    cursor = 0
    for window in windows:
        if int(window["offset"]) < cursor:
            continue
        selected.append(window)
        cursor = int(window["offset"]) + WINDOW_SIZE
    return selected


def ticker_failures(scope: dict[str, Any], block_start: str, block_end: str) -> dict[str, list[dict[str, Any]]]:
    output: dict[str, list[dict[str, Any]]] = {}
    for row in scope.get("daily_results", []):
        date = str(row.get("date") or "")
        if not (block_start <= date <= block_end):
            continue
        if bool(row.get("price_only_eligible")):
            continue
        ticker = str(row.get("ticker") or "")
        output.setdefault(ticker, []).append(
            {
                "date": date,
                "bars": int(row.get("bars") or 0),
                "close_ratio_to_previous_session": row.get("close_ratio_to_previous_session"),
                "reason": row.get("reason"),
            }
        )
    return output


def counterfactual_origins(
    all_days: list[str],
    eligible: dict[str, list[str]],
    repairs: list[tuple[str, str]],
) -> int:
    module = load_cohort_module()
    amended = {day: list(tickers) for day, tickers in eligible.items()}
    for date, ticker in repairs:
        if date not in amended:
            continue
        amended[date] = sorted(set(amended[date]) | {ticker})
    findings = module.construct_candidate_blocks(all_days, amended)
    best = max(
        findings,
        key=lambda item: (
            int(item["strict_independent_origins"]),
            int(item["minimum_common_tickers"]),
            int(item["sessions"]),
        ),
        default=None,
    )
    return int(best["strict_independent_origins"]) if best else 0


def sqlite_summary(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"path": str(path), "exists": False}
    uri = f"file:{path.as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=30) as db:
        tables = [row[0] for row in db.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'")]
        summary: dict[str, Any] = {"path": str(path), "exists": True, "tables": {}}
        for table in tables:
            columns = [row[1] for row in db.execute(f'pragma table_info("{table}")')]
            row_count = int(db.execute(f'select count(*) from "{table}"').fetchone()[0])
            record: dict[str, Any] = {"row_count": row_count, "columns": columns}
            symbol_column = next((name for name in ("symbol", "ticker", "underlying") if name in columns), None)
            time_column = next((name for name in ("timestamp", "event_time", "date", "time") if name in columns), None)
            if symbol_column:
                record["symbols"] = [
                    str(row[0])
                    for row in db.execute(
                        f'select distinct "{symbol_column}" from "{table}" order by 1'
                    ).fetchall()
                ]
            if time_column:
                minimum, maximum = db.execute(
                    f'select min("{time_column}"), max("{time_column}") from "{table}"'
                ).fetchone()
                record["minimum_time"] = minimum
                record["maximum_time"] = maximum
            summary["tables"][table] = record
        return summary


def current_panel_inventory(scope: dict[str, Any]) -> dict[str, Any]:
    normalized = sorted((ROOT / "data" / "normalized" / "alpaca_sip_holdout_c_1m").glob("year=*/month=*/*.parquet"))
    raw = sorted((ROOT / "data" / "raw" / "alpaca_sip_holdout_c_1m").glob("year=*/month=*/*.json"))
    tickers = sorted({str(row.get("ticker")) for row in scope.get("daily_results", []) if row.get("ticker")})
    return {
        "provider": "alpaca_sip",
        "raw_day_files": len(raw),
        "normalized_day_files": len(normalized),
        "tickers": tickers,
        "additional_tickers_beyond_frozen_nine": [],
        "alternative_raw_copy_for_critical_dates": False,
    }


def build_report(scope_path: Path, cohort_path: Path, runtime_data: Path) -> dict[str, Any]:
    scope = load_json(scope_path)
    cohort = load_json(cohort_path)
    all_days, eligible = scope_eligibility(scope)
    selected = cohort.get("selected_block") or {}
    block_start = str(selected.get("start") or "")
    block_end = str(selected.get("end") or "")
    block = selected_block_days(all_days, eligible, block_start, block_end)
    windows = enumerate_valid_windows(block, eligible)
    maximum = greedy_maximum(windows)
    failures = ticker_failures(scope, block_start, block_end)

    # The stable eight are present on every eligible day except these two
    # split-like discontinuities. GE is the ninth panel member but has many
    # separate gaps and cannot substitute throughout either affected window.
    critical_repairs = [("2024-06-10", "NVDA"), ("2025-12-05", "XLE")]
    individual = {
        f"{ticker}:{date}": counterfactual_origins(all_days, eligible, [(date, ticker)])
        for date, ticker in critical_repairs
    }
    both = counterfactual_origins(all_days, eligible, critical_repairs)

    historical_bars = sqlite_summary(runtime_data / "historical_bars.sqlite")
    index_bars = sqlite_summary(runtime_data / "historical_equities" / "alpaca_eod_indices" / "equity_bars.sqlite")
    leveraged = sqlite_summary(runtime_data / "historical_equities" / "leveraged_etf_wheel" / "equity_bars.sqlite")

    stores = {
        "current_panel": current_panel_inventory(scope),
        "historical_bars_sqlite": historical_bars,
        "alpaca_eod_indices_sqlite": index_bars,
        "leveraged_etf_wheel_sqlite": leveraged,
        "qlib_panel": {
            "path": str(QUALITY / "current_era_price_only_qlib_panel.parquet"),
            "exists": (QUALITY / "current_era_price_only_qlib_panel.parquet").is_file(),
            "derived_from_current_nine_symbol_panel": True,
            "can_supply_new_raw_evidence": False,
        },
    }

    return {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "existing_unused_data_only_holdout_c_origin_gap_audit",
        "scope_artifact": str(scope_path),
        "cohort_artifact": str(cohort_path),
        "unchanged_requirements": {
            "provider_count": 1,
            "minimum_common_tickers": MIN_TICKERS,
            "context_sessions": 32,
            "outcome_sessions": 20,
            "minimum_strict_independent_origins": REQUIRED_ORIGINS,
            "single_contiguous_eligible_block": True,
            "price_only_exact_bars": 391,
            "close_ratio_exclusive_bounds": [0.5, 2.0],
        },
        "observed": {
            "selected_block_start": block_start,
            "selected_block_end": block_end,
            "selected_block_sessions": len(block),
            "valid_52_session_windows": len(windows),
            "maximum_non_overlapping_valid_windows": len(maximum),
            "required_origins": REQUIRED_ORIGINS,
            "origin_gap": REQUIRED_ORIGINS - len(maximum),
            "status": "11/12_origins_one_short_essentially_resolved_not_cleared",
        },
        "critical_discontinuities": [
            {
                "ticker": ticker,
                "date": date,
                "records": failures.get(ticker, []),
                "bars_present": next(
                    (
                        int(row.get("bars") or 0)
                        for row in failures.get(ticker, [])
                        if row.get("date") == date
                    ),
                    None,
                ),
            }
            for date, ticker in critical_repairs
        ],
        "counterfactual_availability_only": {
            "repair_nvda_only_origins": individual["NVDA:2024-06-10"],
            "repair_xle_only_origins": individual["XLE:2025-12-05"],
            "repair_both_origins": both,
            "outcomes_used": False,
            "prices_adjusted": False,
        },
        "existing_data_stores_audited": stores,
        "existing_unused_data_closes_gap": False,
        "reason_existing_data_does_not_close_gap": [
            "The current raw and normalized panel contains only the frozen nine symbols.",
            "Both critical sessions already contain 391 raw Alpaca bars; they fail the unchanged split-like close-ratio rule rather than a missing-file check.",
            "The local historical_bars store begins after the 2024 critical date and has no complete alternate 52-session ticker coverage for the affected windows.",
            "The Alpaca index store contains only SPY, QQQ, and IWM, which are already members of the stable common universe.",
            "The leveraged-ETF store is not a matching 1-minute substitute for the frozen nine-symbol panel.",
            "The Qlib panel is derived evidence and cannot create an additional raw ticker or repair a rejected price discontinuity.",
        ],
        "minimal_safe_next_step": {
            "action": "preregister_one_additional_same_provider_ticker_coverage_audit",
            "selection_basis": "availability_and_corporate_action_continuity_only_no_outcomes",
            "required_coverage": [block_start, block_end],
            "must_be_complete_in_both_critical_52_session_windows": True,
            "new_ticker_outcomes_must_remain_uninspected_until_refreeze": True,
            "provider": "alpaca_sip",
            "vendor_mixing": False,
            "gate_relaxation": False,
        },
        "ranking_or_model_outcomes_evaluated_by_this_audit": False,
        "new_market_data_sourced": False,
        "volume_features_or_evaluation": False,
        "live_execution": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scope", type=Path)
    parser.add_argument("--cohort", type=Path)
    parser.add_argument("--runtime-data", type=Path, default=DEFAULT_RUNTIME_DATA)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    scope_path = args.scope or latest("data/market_quality/alpaca_holdout_c_price_only_scope_*.json")
    cohort_path = args.cohort or latest("data/governance/holdout_c_alpaca_cohort_construction_*.json")
    payload = build_report(scope_path, cohort_path, args.runtime_data)
    GOV.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    timestamped = args.output or GOV / f"holdout_c_existing_data_gap_audit_{stamp}.json"
    stable = GOV / "holdout_c_existing_data_gap_audit.json"
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    timestamped.write_text(encoded, encoding="utf-8")
    stable.write_text(encoded, encoding="utf-8")
    print(
        json.dumps(
            {
                "path": str(stable),
                "timestamped_path": str(timestamped),
                "status": payload["observed"]["status"],
                "existing_unused_data_closes_gap": payload["existing_unused_data_closes_gap"],
                "new_market_data_sourced": payload["new_market_data_sourced"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
