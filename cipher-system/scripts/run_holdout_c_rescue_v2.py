#!/usr/bin/env python3
"""Preregister and audit one same-provider Holdout C rescue ticker.

The candidate is fixed to DIA before any download. Selection is based only on
structural criteria: a broad, liquid US index ETF, existing before the period,
not already in the frozen nine-symbol panel, and expected to avoid issuer-level
corporate-action discontinuities. The script retrieves Alpaca SIP minute bars,
applies the unchanged price-only gate, and recomputes cohort availability. It
never computes ranking/model outcomes, forward returns, or strategy results.
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
RAW = ROOT / "data" / "raw" / "alpaca_sip_holdout_c_rescue_v2_1m" / "ticker=DIA"
NORMALIZED = ROOT / "data" / "normalized" / "alpaca_sip_holdout_c_rescue_v2_1m" / "ticker=DIA"
CANDIDATE = "DIA"
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


def headers() -> dict[str, str]:
    load_local_env()
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
    stable = GOV / "holdout_c_rescue_v2_preregistration.json"
    payload = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "candidate": CANDIDATE,
        "provider": "Alpaca SIP",
        "feed": "sip",
        "selection_frozen_before_candidate_data_retrieval": True,
        "selection_basis": [
            "broad_us_index_etf",
            "high_liquidity_structural_candidate",
            "existed_before_2023_candidate_period",
            "not_in_frozen_nine_symbol_panel",
            "lower_issuer_specific_corporate_action_risk_than_single_company_candidate",
        ],
        "scope_artifact": str(scope_path),
        "corrected_cohort_artifact": str(cohort_path),
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
        "allowed_analysis": [
            "timestamp_and_bar_count_completeness",
            "ohlc_integrity",
            "close_continuity_gate",
            "window_wide_common_ticker_availability",
            "strict_non_overlapping_origin_count",
        ],
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
        protected = ("candidate", "provider", "feed", "selection_basis", "unchanged_gate", "forbidden_analysis")
        if any(prior.get(key) != payload.get(key) for key in protected):
            raise RuntimeError("rescue preregistration already exists with different protected fields")
        return stable
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    timestamped = GOV / f"holdout_c_rescue_v2_preregistration_{stamp}.json"
    stable.write_text(encoded, encoding="utf-8")
    timestamped.write_text(encoded, encoding="utf-8")
    return stable


def request_day(day: str, open_time: str, close_time: str) -> dict[str, Any]:
    session_day = date.fromisoformat(day)
    start = ny_utc(session_day, open_time)
    end = ny_utc(session_day, close_time)
    response = requests.get(
        "https://data.alpaca.markets/v2/stocks/bars",
        headers=headers(),
        params={
            "symbols": CANDIDATE,
            "timeframe": "1Min",
            "start": start,
            "end": end,
            "feed": "sip",
            "limit": 10000,
        },
        timeout=90,
    )
    response.raise_for_status()
    return {
        "schema_version": 1,
        "provider": "Alpaca SIP",
        "feed": "sip",
        "candidate": CANDIDATE,
        "session_date": day,
        "request": {
            "symbols": [CANDIDATE],
            "timeframe": "1Min",
            "start": start,
            "end": end,
            "feed": "sip",
        },
        "received_at": datetime.now(timezone.utc).isoformat(),
        "response": response.json(),
        "ranking_or_model_outcomes_evaluated": False,
    }


def normalize(raw: dict[str, Any], destination: Path) -> int:
    rows: list[dict[str, Any]] = []
    for bar in (raw.get("response", {}).get("bars", {}).get(CANDIDATE) or []):
        rows.append(
            {
                "timestamp": bar["t"],
                "ticker": CANDIDATE,
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
    spec = importlib.util.spec_from_file_location("holdout_c_rescue_cohort", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load cohort constructor")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def quality_rows(scope: dict[str, Any], session_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    previous_close: float | None = None
    for session in session_rows:
        day = str(session["date"])
        raw_path = RAW / f"year={day[:4]}" / f"month={day[5:7]}" / f"{day}.json"
        raw = read_json(raw_path)
        bars = raw.get("response", {}).get("bars", {}).get(CANDIDATE) or []
        closes = [float(bar["c"]) for bar in bars if bar.get("c") is not None]
        close = closes[-1] if closes else None
        ratio = (close / previous_close) if close is not None and previous_close not in (None, 0.0) else None
        exact_bars = len(bars) == 391
        ratio_ok = ratio is None or (0.5 < ratio < 2.0)
        eligible = exact_bars and close is not None and close > 0 and ratio_ok
        output.append(
            {
                "date": day,
                "ticker": CANDIDATE,
                "bars": len(bars),
                "close": close,
                "close_ratio_to_previous_session": ratio,
                "price_only_eligible": eligible,
                "reason": None if eligible else "missing_or_split_like_price_discontinuity",
                "raw_path": str(raw_path),
            }
        )
        if close is not None:
            previous_close = close
    return output


def build_rescue_scope(scope: dict[str, Any], candidate_rows: list[dict[str, Any]]) -> dict[str, Any]:
    candidate_by_date = {row["date"]: row for row in candidate_rows}
    merged: list[dict[str, Any]] = []
    for row in scope.get("common_eligible_by_day", []):
        tickers = set(str(value) for value in row.get("tickers", []))
        candidate = candidate_by_date.get(str(row["date"]))
        if candidate and candidate["price_only_eligible"]:
            tickers.add(CANDIDATE)
        merged.append({"date": row["date"], "count": len(tickers), "tickers": sorted(tickers)})
    return {
        "schema_version": 2,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "base_scope_artifact": scope.get("scope_artifact"),
        "provider": "Alpaca SIP",
        "feed": "sip",
        "base_panel": sorted({str(row["ticker"]) for row in scope.get("daily_results", [])}),
        "rescue_candidate": CANDIDATE,
        "daily_results": [*scope.get("daily_results", []), *candidate_rows],
        "common_eligible_by_day": merged,
        "gate": scope.get("gate"),
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
        "schema_version": 2,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scope_artifact": str(scope_path),
        "rescue_candidate": CANDIDATE,
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", help="Optional inclusive session date for resumable batch.")
    parser.add_argument("--end", help="Optional inclusive session date for resumable batch.")
    parser.add_argument("--sleep-seconds", type=float, default=0.32)
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Existing server-side environment file. Values are loaded into the process and never copied or printed.",
    )
    args = parser.parse_args()
    if args.env_file:
        load_local_env(args.env_file.expanduser().resolve())

    scope_path = latest("data/market_quality/alpaca_holdout_c_price_only_scope_*.json")
    cohort_path = latest("data/governance/holdout_c_alpaca_cohort_construction_*.json")
    scope = read_json(scope_path)
    prereg = write_preregistration(scope_path, cohort_path)

    calendar_by_date: dict[str, dict[str, Any]] = {}
    for row in scope.get("daily_results", []):
        calendar_by_date.setdefault(str(row["date"]), {"date": str(row["date"]), "open": "09:30", "close": "16:00"})
    selected = [calendar_by_date[key] for key in sorted(calendar_by_date)]
    if args.start:
        selected = [row for row in selected if row["date"] >= args.start]
    if args.end:
        selected = [row for row in selected if row["date"] <= args.end]

    for index, session in enumerate(selected, start=1):
        day = str(session["date"])
        raw_path = RAW / f"year={day[:4]}" / f"month={day[5:7]}" / f"{day}.json"
        normalized_path = NORMALIZED / f"year={day[:4]}" / f"month={day[5:7]}" / f"{day}.parquet"
        if raw_path.exists():
            raw = read_json(raw_path)
        else:
            raw = request_day(day, session["open"], session["close"])
            atomic_json(raw_path, raw)
            time.sleep(max(0.0, args.sleep_seconds))
        normalize(raw, normalized_path)
        if index % 50 == 0:
            print(f"{index}/{len(selected)} DIA sessions processed", flush=True)

    # Only finalize after all dates in the base scope have been retrieved.
    complete_dates = sorted(calendar_by_date)
    missing = [
        day
        for day in complete_dates
        if not (RAW / f"year={day[:4]}" / f"month={day[5:7]}" / f"{day}.json").is_file()
    ]
    if missing:
        print(json.dumps({"preregistration": str(prereg), "batch_complete": True, "full_panel_complete": False, "missing_dates": len(missing)}, indent=2))
        return 0

    candidate_rows = quality_rows(scope, [calendar_by_date[day] for day in complete_dates])
    rescue_scope = build_rescue_scope(scope, candidate_rows)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    rescue_scope_path = QUALITY / f"alpaca_holdout_c_price_only_scope_rescue_v2_{stamp}.json"
    rescue_scope["scope_artifact"] = str(rescue_scope_path)
    rescue_scope_path.write_text(json.dumps(rescue_scope, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    cohort = cohort_result(rescue_scope, rescue_scope_path)
    cohort_path_v2 = GOV / f"holdout_c_alpaca_cohort_rescue_v2_{stamp}.json"
    cohort_path_v2.write_text(json.dumps(cohort, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    stable = GOV / "holdout_c_alpaca_cohort_rescue_v2.json"
    stable.write_text(json.dumps(cohort, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "preregistration": str(prereg),
                "scope": str(rescue_scope_path),
                "cohort": str(cohort_path_v2),
                "pass": cohort["pass"],
                "strict_independent_origins": (cohort.get("selected_block") or {}).get("strict_independent_origins"),
                "allowed_claim": cohort["allowed_claim"],
                "ranking_or_model_outcomes_evaluated": False,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
