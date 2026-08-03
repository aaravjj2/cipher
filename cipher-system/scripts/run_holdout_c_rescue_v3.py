#!/usr/bin/env python3
"""Preregister and audit a fixed same-provider rescue basket for Holdout C.

All five candidates are frozen before retrieval and are evaluated together.
Selection uses only timestamp completeness, OHLC integrity, split-like price
continuity, and window-wide cohort availability. The script never calculates
forward returns, factor/model scores, rankings, or strategy outcomes.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import time
from datetime import date, datetime, time as clock_time, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from core.env import load_local_env
from core.research_platform.market_quality import require_holdout_c_cohort

QUALITY = ROOT / "data" / "market_quality"
GOV = ROOT / "data" / "governance"
RAW = ROOT / "data" / "raw" / "alpaca_sip_holdout_c_rescue_v3_1m"
NORMALIZED = ROOT / "data" / "normalized" / "alpaca_sip_holdout_c_rescue_v3_1m"
CANDIDATES = ("AMD", "AMZN", "GOOGL", "META", "TSLA")
MIN_TICKERS = 8
MIN_ORIGINS = 12
CONTEXT = 32
HORIZON = 20
NY = ZoneInfo("America/New_York")


def latest(pattern: str) -> Path:
    rows = sorted(ROOT.glob(pattern))
    if not rows:
        raise FileNotFoundError(pattern)
    return rows[-1]


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def load_credentials(env_file: Path | None) -> dict[str, str]:
    load_local_env()
    if env_file:
        load_local_env(env_file.expanduser().resolve())
    key = next(
        (
            os.environ.get(name)
            for name in ("ALPACA_ALGO_PLUS_KEY", "ALPACA_ALGO_KEY", "ALPACA_API_KEY")
            if os.environ.get(name)
        ),
        None,
    )
    secret = next(
        (
            os.environ.get(name)
            for name in (
                "ALPACA_ALGO_PLUS_SECRET",
                "ALPACA_ALGO_SECRET",
                "ALPACA_API_SECRET",
                "ALPACA_SECRET_KEY",
            )
            if os.environ.get(name)
        ),
        None,
    )
    if not key or not secret:
        raise RuntimeError("read-only Alpaca credentials are unavailable")
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}


def ny_utc(day: date, local: str) -> str:
    hour, minute = map(int, local.split(":"))
    value = datetime.combine(day, clock_time(hour, minute), NY)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def atomic_json(path: Path, payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise RuntimeError(f"immutable raw payload mismatch: {path}")
        return digest
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(encoded)
    os.replace(temporary, path)
    return digest


def write_preregistration(scope_path: Path, cohort_path: Path) -> Path:
    GOV.mkdir(parents=True, exist_ok=True)
    stable = GOV / "holdout_c_rescue_v3_preregistration.json"
    payload = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "candidate_universe": list(CANDIDATES),
        "candidate_order_has_no_stopping_rule": True,
        "all_candidates_must_be_evaluated": True,
        "provider": "Alpaca SIP",
        "feed": "sip",
        "selection_frozen_before_candidate_data_retrieval": True,
        "selection_basis": [
            "high_intraday_liquidity",
            "large_us_equity",
            "listed_before_2023_candidate_period",
            "not_in_frozen_nine_symbol_panel",
            "candidate_set_fixed_without_return_or_model_outcomes",
        ],
        "scope_artifact": str(scope_path),
        "corrected_cohort_artifact": str(cohort_path),
        "prior_dia_rescue_result": "failed_due_to_repeated_sub_391_bar_sessions",
        "unchanged_gate": {
            "exact_regular_session_bars": 391,
            "close_ratio_exclusive_lower": 0.5,
            "close_ratio_exclusive_upper": 2.0,
            "minimum_common_tickers": MIN_TICKERS,
            "context_sessions": CONTEXT,
            "outcome_sessions": HORIZON,
            "minimum_strict_independent_origins": MIN_ORIGINS,
            "single_provider": True,
        },
        "selection_rule_after_availability_audit": (
            "Any candidate may contribute to a 52-session origin only when it passes the unchanged "
            "price-only gate on every session in that origin. No return-based preference is allowed."
        ),
        "forbidden_analysis": [
            "forward_return_scoring",
            "factor_scoring",
            "model_scoring",
            "ranking_outcomes",
            "strategy_backtesting",
            "parameter_selection",
            "volume_features",
            "paper_or_live_trading",
        ],
        "vendor_mixing": False,
        "gate_relaxation": False,
        "execution_authority": False,
    }
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if stable.exists():
        prior = read_json(stable)
        protected = (
            "candidate_universe",
            "all_candidates_must_be_evaluated",
            "provider",
            "feed",
            "selection_basis",
            "unchanged_gate",
            "selection_rule_after_availability_audit",
            "forbidden_analysis",
        )
        if any(prior.get(key) != payload.get(key) for key in protected):
            raise RuntimeError("rescue v3 preregistration differs on protected fields")
        return stable
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    timestamped = GOV / f"holdout_c_rescue_v3_preregistration_{stamp}.json"
    stable.write_text(encoded, encoding="utf-8")
    timestamped.write_text(encoded, encoding="utf-8")
    return stable


def request_day(day: str, request_headers: dict[str, str]) -> dict[str, Any]:
    session_day = date.fromisoformat(day)
    start = ny_utc(session_day, "09:30")
    end = ny_utc(session_day, "16:00")
    response = requests.get(
        "https://data.alpaca.markets/v2/stocks/bars",
        headers=request_headers,
        params={
            "symbols": ",".join(CANDIDATES),
            "timeframe": "1Min",
            "start": start,
            "end": end,
            "feed": "sip",
            "limit": 10000,
        },
        timeout=120,
    )
    response.raise_for_status()
    body = response.json()
    if body.get("next_page_token"):
        raise RuntimeError(f"unexpected pagination for {day}; request contract must be revised before continuing")
    return {
        "schema_version": 1,
        "provider": "Alpaca SIP",
        "feed": "sip",
        "candidates": list(CANDIDATES),
        "session_date": day,
        "request": {
            "symbols": list(CANDIDATES),
            "timeframe": "1Min",
            "start": start,
            "end": end,
            "feed": "sip",
        },
        "received_at": datetime.now(timezone.utc).isoformat(),
        "response": body,
        "ranking_or_model_outcomes_evaluated": False,
    }


def normalize(raw: dict[str, Any], destination: Path) -> int:
    rows: list[dict[str, Any]] = []
    bars_by_symbol = raw.get("response", {}).get("bars", {})
    for symbol in CANDIDATES:
        for bar in bars_by_symbol.get(symbol) or []:
            rows.append(
                {
                    "timestamp": bar["t"],
                    "ticker": symbol,
                    "open": bar["o"],
                    "high": bar["h"],
                    "low": bar["l"],
                    "close": bar["c"],
                    "volume": bar["v"],
                    "trade_count": bar.get("n"),
                    "vwap": bar.get("vw"),
                    "provider": "alpaca_sip",
                    "source_day": raw["session_date"],
                }
            )
    frame = pd.DataFrame(rows)
    if not frame.empty:
        invalid = (
            (frame.low > frame.open)
            | (frame.open > frame.high)
            | (frame.low > frame.close)
            | (frame.close > frame.high)
            | (frame[["open", "high", "low", "close"]] <= 0).any(axis=1)
        )
        if bool(invalid.any()):
            raise RuntimeError(f"OHLC integrity failure for {raw['session_date']}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp")
    frame.to_parquet(temporary, index=False)
    os.replace(temporary, destination)
    return len(frame)


def load_cohort_module():
    path = ROOT / "scripts" / "construct_alpaca_holdout_c_cohort.py"
    spec = importlib.util.spec_from_file_location("holdout_c_rescue_v3_cohort", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load cohort constructor")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def quality_rows(days: list[str]) -> list[dict[str, Any]]:
    previous_close: dict[str, float | None] = {symbol: None for symbol in CANDIDATES}
    output: list[dict[str, Any]] = []
    for day in days:
        raw_path = RAW / f"year={day[:4]}" / f"month={day[5:7]}" / f"{day}.json"
        raw = read_json(raw_path)
        bars_by_symbol = raw.get("response", {}).get("bars", {})
        for symbol in CANDIDATES:
            bars = bars_by_symbol.get(symbol) or []
            closes = [float(bar["c"]) for bar in bars if bar.get("c") is not None]
            close = closes[-1] if closes else None
            prior = previous_close[symbol]
            ratio = (close / prior) if close is not None and prior not in (None, 0.0) else None
            exact_bars = len(bars) == 391
            ratio_ok = ratio is None or (0.5 < ratio < 2.0)
            eligible = exact_bars and close is not None and close > 0 and ratio_ok
            output.append(
                {
                    "date": day,
                    "ticker": symbol,
                    "bars": len(bars),
                    "close": close,
                    "close_ratio_to_previous_session": ratio,
                    "price_only_eligible": eligible,
                    "reason": None if eligible else "missing_or_split_like_price_discontinuity",
                    "raw_path": str(raw_path),
                }
            )
            if close is not None:
                previous_close[symbol] = close
    return output


def merge_scope(base_scope: dict[str, Any], candidate_rows: list[dict[str, Any]]) -> dict[str, Any]:
    eligible_candidates: dict[str, set[str]] = {}
    for row in candidate_rows:
        if row["price_only_eligible"]:
            eligible_candidates.setdefault(row["date"], set()).add(row["ticker"])
    merged: list[dict[str, Any]] = []
    for row in base_scope.get("common_eligible_by_day", []):
        tickers = set(str(value) for value in row.get("tickers", []))
        tickers.update(eligible_candidates.get(str(row["date"]), set()))
        merged.append({"date": row["date"], "count": len(tickers), "tickers": sorted(tickers)})
    return {
        "schema_version": 3,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "provider": "Alpaca SIP",
        "feed": "sip",
        "base_panel": sorted({str(row["ticker"]) for row in base_scope.get("daily_results", [])}),
        "rescue_candidates": list(CANDIDATES),
        "daily_results": [*base_scope.get("daily_results", []), *candidate_rows],
        "common_eligible_by_day": merged,
        "gate": base_scope.get("gate"),
        "volume_features_or_evaluation": False,
        "ranking_or_model_outcomes_evaluated": False,
        "gate_relaxed": False,
        "vendor_mixing": False,
        "live_execution": False,
    }


def cohort_result(scope_payload: dict[str, Any], scope_path: Path) -> dict[str, Any]:
    module = load_cohort_module()
    all_days = sorted({str(row["date"]) for row in scope_payload["daily_results"]})
    eligible = {
        str(row["date"]): sorted(str(ticker) for ticker in row.get("tickers", []))
        for row in scope_payload["common_eligible_by_day"]
        if int(row.get("count") or 0) >= MIN_TICKERS
    }
    findings = module.construct_candidate_blocks(all_days, eligible)
    best = max(
        findings,
        key=lambda item: (
            int(item["strict_independent_origins"]),
            int(item["minimum_common_tickers"]),
            int(item["sessions"]),
        ),
        default=None,
    )
    gate = require_holdout_c_cohort(
        source_count=1,
        common_tickers=int(best["minimum_common_tickers"]) if best else 0,
        strict_independent_origins=int(best["strict_independent_origins"]) if best else 0,
    )
    return {
        "schema_version": 3,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scope_artifact": str(scope_path),
        "rescue_candidates": list(CANDIDATES),
        "requirements": {
            "minimum_common_tickers": MIN_TICKERS,
            "context_sessions": CONTEXT,
            "outcome_horizon_sessions": HORIZON,
            "minimum_strict_independent_origins": MIN_ORIGINS,
        },
        "candidate_blocks": findings,
        "selected_block": best,
        "cohort_gate": gate,
        "pass": bool(gate["eligible"]),
        "cohort_frozen_before_candidate_ranking_outcomes": bool(gate["eligible"]),
        "candidate_ranking_or_model_outcomes_evaluated": False,
        "prior_nine_symbol_period_was_previously_used_for_exploratory_research": True,
        "allowed_claim": "structural_cohort_eligibility_only_not_restored_untouched_holdout",
        "gate_relaxed": False,
        "vendor_mixing": False,
        "volume_features_or_evaluation": False,
        "live_execution": False,
    }


def candidate_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for symbol in CANDIDATES:
        selected = [row for row in rows if row["ticker"] == symbol]
        bad = [row for row in selected if not row["price_only_eligible"]]
        output[symbol] = {
            "sessions": len(selected),
            "eligible_sessions": len(selected) - len(bad),
            "ineligible_sessions": len(bad),
            "sub_391_bar_sessions": sum(1 for row in bad if int(row["bars"]) != 391),
            "split_like_sessions": [
                {
                    "date": row["date"],
                    "bars": row["bars"],
                    "close_ratio_to_previous_session": row["close_ratio_to_previous_session"],
                }
                for row in bad
                if int(row["bars"]) == 391
            ],
        }
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", help="Optional inclusive date for resumable batch.")
    parser.add_argument("--end", help="Optional inclusive date for resumable batch.")
    parser.add_argument("--sleep-seconds", type=float, default=0.32)
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args()

    scope_path = latest("data/market_quality/alpaca_holdout_c_price_only_scope_*.json")
    cohort_path = latest("data/governance/holdout_c_alpaca_cohort_construction_*.json")
    base_scope = read_json(scope_path)
    prereg = write_preregistration(scope_path, cohort_path)
    request_headers = load_credentials(args.env_file)

    days = sorted({str(row["date"]) for row in base_scope.get("daily_results", [])})
    selected = [day for day in days if (not args.start or day >= args.start) and (not args.end or day <= args.end)]
    for index, day in enumerate(selected, start=1):
        raw_path = RAW / f"year={day[:4]}" / f"month={day[5:7]}" / f"{day}.json"
        normalized_path = NORMALIZED / f"year={day[:4]}" / f"month={day[5:7]}" / f"{day}.parquet"
        if raw_path.exists():
            raw = read_json(raw_path)
        else:
            raw = request_day(day, request_headers)
            atomic_json(raw_path, raw)
            time.sleep(max(0.0, args.sleep_seconds))
        normalize(raw, normalized_path)
        if index % 50 == 0:
            print(f"{index}/{len(selected)} rescue-v3 sessions processed", flush=True)

    missing = [
        day
        for day in days
        if not (RAW / f"year={day[:4]}" / f"month={day[5:7]}" / f"{day}.json").is_file()
    ]
    if missing:
        print(
            json.dumps(
                {
                    "preregistration": str(prereg),
                    "batch_complete": True,
                    "full_panel_complete": False,
                    "missing_dates": len(missing),
                },
                indent=2,
            )
        )
        return 0

    rows = quality_rows(days)
    rescue_scope = merge_scope(base_scope, rows)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    scope_v3_path = QUALITY / f"alpaca_holdout_c_price_only_scope_rescue_v3_{stamp}.json"
    scope_v3_path.write_text(json.dumps(rescue_scope, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    cohort = cohort_result(rescue_scope, scope_v3_path)
    cohort_v3_path = GOV / f"holdout_c_alpaca_cohort_rescue_v3_{stamp}.json"
    stable = GOV / "holdout_c_alpaca_cohort_rescue_v3.json"
    cohort_v3_path.write_text(json.dumps(cohort, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    stable.write_text(json.dumps(cohort, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary = candidate_summary(rows)
    summary_path = GOV / f"holdout_c_rescue_v3_candidate_quality_{stamp}.json"
    summary_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "candidates": summary,
                "ranking_or_model_outcomes_evaluated": False,
                "gate_relaxed": False,
                "vendor_mixing": False,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "preregistration": str(prereg),
                "scope": str(scope_v3_path),
                "cohort": str(cohort_v3_path),
                "candidate_quality": str(summary_path),
                "pass": cohort["pass"],
                "strict_independent_origins": (cohort.get("selected_block") or {}).get("strict_independent_origins"),
                "candidate_ineligible_sessions": {
                    key: value["ineligible_sessions"] for key, value in summary.items()
                },
                "allowed_claim": cohort["allowed_claim"],
                "ranking_or_model_outcomes_evaluated": False,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
