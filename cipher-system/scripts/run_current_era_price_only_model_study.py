#!/usr/bin/env python3
"""Pre-register and run the current-era price-only TimesFM/Kronos study."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

import duckdb

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.kronos_research import load_predictor, set_research_seed  # noqa: E402

SYMBOLS = ("SPY", "QQQ", "IWM", "XLF", "XLE", "AAPL", "MSFT", "NVDA", "GE")
HORIZONS = (5, 20)
CONTEXT_SESSIONS = 32
ORIGIN_POSITIONS = (83, 239, 395, 551)
SEEDS = tuple(range(42, 52))
DATA_GLOB = str(ROOT / "data" / "normalized" / "alpaca_sip_holdout_c_1m" / "year=202[345]" / "month=*" / "*.parquet")
OUT_DIR = ROOT / "data" / "market_quality"


def hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_daily() -> dict[str, list[dict]]:
    query = """
        with regular as (
          select ticker,
                 date(timezone('America/New_York', cast(timestamp as timestamp with time zone))) as trading_day,
                 timezone('America/New_York', cast(timestamp as timestamp with time zone)) as local_timestamp,
                 open, high, low, close
          from read_parquet(?)
          where ticker in ('SPY','QQQ','IWM','XLF','XLE','AAPL','MSFT','NVDA','GE')
            and cast(timezone('America/New_York', cast(timestamp as timestamp with time zone)) as time) between time '09:30:00' and time '16:00:00'
        ), numbered as (
          select *, row_number() over (partition by ticker, trading_day order by local_timestamp desc) as closing_row
          from regular
        )
        select ticker, trading_day, count(*) as bars,
               max(open) filter (where local_timestamp = min(local_timestamp) over (partition by ticker, trading_day)) as open,
               max(high) as high, min(low) as low,
               max(close) filter (where closing_row = 1) as close
        from numbered
        group by ticker, trading_day
        order by ticker, trading_day
    """
    # DuckDB does not allow a window function inside an aggregate filter in all
    # versions, so use a small second CTE for the opening row.
    query = """
        with regular as (
          select ticker,
                 date(timezone('America/New_York', cast(timestamp as timestamp with time zone))) as trading_day,
                 timezone('America/New_York', cast(timestamp as timestamp with time zone)) as local_timestamp,
                 open, high, low, close
          from read_parquet(?)
          where ticker in ('SPY','QQQ','IWM','XLF','XLE','AAPL','MSFT','NVDA','GE')
            and cast(timezone('America/New_York', cast(timestamp as timestamp with time zone)) as time) between time '09:30:00' and time '16:00:00'
        ), numbered as (
          select *,
                 row_number() over (partition by ticker, trading_day order by local_timestamp) as opening_row,
                 row_number() over (partition by ticker, trading_day order by local_timestamp desc) as closing_row
          from regular
        )
        select ticker, trading_day, count(*) as bars,
               max(open) filter (where opening_row = 1) as open,
               max(high) as high, min(low) as low,
               max(close) filter (where closing_row = 1) as close
        from numbered
        group by ticker, trading_day
        order by ticker, trading_day
    """
    with duckdb.connect(":memory:") as db:
        rows = db.execute(query, [DATA_GLOB]).fetchall()
    out: dict[str, list[dict]] = {symbol: [] for symbol in SYMBOLS}
    for ticker, day, bars, op, hi, lo, close in rows:
        if int(bars) != 391:
            continue
        out[ticker].append({"date": day.isoformat(), "open": float(op), "high": float(hi), "low": float(lo), "close": float(close)})
    return out


def common_daily(daily: dict[str, list[dict]]) -> list[str]:
    common = set.intersection(*(set(row["date"] for row in rows) for rows in daily.values()))
    ordered = sorted(common)
    valid = []
    for day in ordered:
        valid.append(day)
    return valid


def preregister() -> dict:
    daily = load_daily()
    common = common_daily(daily)
    if len(common) <= ORIGIN_POSITIONS[-1] + max(HORIZONS):
        raise RuntimeError(f"insufficient common price-only sessions: {len(common)}")
    origins = [common[position] for position in ORIGIN_POSITIONS]
    cases = []
    by_date = {day: index for index, day in enumerate(common)}
    for origin in origins:
        origin_index = by_date[origin]
        for symbol in SYMBOLS:
            rows = daily[symbol]
            row_by_date = {row["date"]: row for row in rows}
            for horizon in HORIZONS:
                outcome_dates = common[origin_index + 1: origin_index + 1 + horizon]
                if len(outcome_dates) != horizon:
                    raise RuntimeError(f"missing outcome dates for {symbol} {origin} h{horizon}")
                context_dates = common[origin_index - CONTEXT_SESSIONS + 1: origin_index + 1]
                sequence = [row_by_date[day]["close"] for day in context_dates + outcome_dates]
                if any(sequence[index] / sequence[index - 1] <= 0.5 or sequence[index] / sequence[index - 1] >= 2.0 for index in range(1, len(sequence))):
                    raise RuntimeError(f"price continuity failed for {symbol} {origin} h{horizon}")
                cases.append({
                    "case_id": f"{symbol}-{origin}-h{horizon}", "symbol": symbol, "origin": origin,
                    "context_dates": [context_dates[0], context_dates[-1]],
                    "outcome_dates": [outcome_dates[0], outcome_dates[-1]],
                    "horizon_sessions": horizon, "context_sessions": CONTEXT_SESSIONS,
                })
    return {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "pre-registered current-era price-only TimesFM/Kronos study",
        "source": {"path_glob": DATA_GLOB, "symbols": list(SYMBOLS), "period": "2023-01-01..2025-12-31", "volume_used": False},
        "selection": {"common_price_only_sessions": len(common), "origin_positions": list(ORIGIN_POSITIONS), "origin_dates": origins, "independence_spacing_sessions": 156, "case_count": len(cases)},
        "models": {"timesfm": {"model_id": "google/timesfm-2.5-200m-pytorch", "input": "close_only", "native_interval": "p10-p90"}, "kronos": {"model_id": "NeoQuasar/Kronos-mini", "tokenizer": "NeoQuasar/Kronos-Tokenizer-base", "input": "OHLC_only", "interval": "10 independently seeded terminal samples"}},
        "baseline": "naive persistence: terminal forecast equals origin close",
        "cases": cases,
        "gate": {"allowed_use": "price_forecast_research_only_no_volume_features", "full_volume_gate_changed": False, "promotion_eligible": False, "trading": False},
    }


def run_model_case(timesfm_model, kronos_model, daily, case: dict) -> dict:
    import numpy as np
    symbol = case["symbol"]
    rows = daily[symbol]
    by_date = {row["date"]: row for row in rows}
    common_context = case["context_dates"]
    origin = case["origin"]
    context_dates = [row["date"] for row in rows if row["date"] <= origin][-CONTEXT_SESSIONS:]
    outcome_dates = [row["date"] for row in rows if row["date"] > origin][:case["horizon_sessions"]]
    context = [by_date[day] for day in context_dates]
    realized = [by_date[day] for day in outcome_dates]
    horizon = case["horizon_sessions"]
    close_context = np.asarray([row["close"] for row in context], dtype=np.float32)
    t0 = time.monotonic()
    point, quantiles = timesfm_model.forecast(horizon=horizon, inputs=[close_context])
    timesfm_point = float(point[0][-1])
    timesfm_lower = float(quantiles[0, -1, 1])
    timesfm_upper = float(quantiles[0, -1, 9])
    timesfm_seconds = time.monotonic() - t0
    from scripts.run_kronos_real_verification import forecast_terminal_samples
    t0 = time.monotonic()
    kronos_samples, _ = forecast_terminal_samples(kronos_model, context, realized=realized, origin=origin, horizon=horizon, seeds=SEEDS, price_only=True)
    kronos_seconds = time.monotonic() - t0
    kronos_point = float(mean(kronos_samples))
    kronos_lower = float(np.percentile(kronos_samples, 10))
    kronos_upper = float(np.percentile(kronos_samples, 90))
    actual = float(realized[-1]["close"])
    naive = float(context[-1]["close"])
    return {**case, "origin_close": naive, "actual_terminal_close": actual,
            "timesfm_point": timesfm_point, "timesfm_p10": timesfm_lower, "timesfm_p90": timesfm_upper,
            "timesfm_wins_naive": abs(timesfm_point - actual) < abs(naive - actual), "timesfm_interval_hit": timesfm_lower <= actual <= timesfm_upper,
            "kronos_point": kronos_point, "kronos_p10": kronos_lower, "kronos_p90": kronos_upper,
            "kronos_wins_naive": abs(kronos_point - actual) < abs(naive - actual), "kronos_interval_hit": kronos_lower <= actual <= kronos_upper,
            "timesfm_absolute_error": abs(timesfm_point - actual), "kronos_absolute_error": abs(kronos_point - actual), "naive_absolute_error": abs(naive - actual),
            "timesfm_seconds": timesfm_seconds, "kronos_seconds": kronos_seconds, "seed_count": len(SEEDS)}


def summarize(rows: list[dict]) -> dict:
    def summary(prefix: str, items: list[dict]) -> dict:
        if not items:
            return {"n": 0, "point_win_count": 0, "point_win_rate": None, "interval_hit_count": 0, "interval_coverage": None, "mean_absolute_error": None}
        return {"n": len(items), "point_win_count": sum(row[f"{prefix}_wins_naive"] for row in items), "point_win_rate": mean(row[f"{prefix}_wins_naive"] for row in items), "interval_hit_count": sum(row[f"{prefix}_interval_hit"] for row in items), "interval_coverage": mean(row[f"{prefix}_interval_hit"] for row in items), "mean_absolute_error": mean(row[f"{prefix}_absolute_error"] for row in items)}
    by_horizon = {}
    for horizon in HORIZONS:
        subset = [row for row in rows if row["horizon_sessions"] == horizon]
        by_horizon[str(horizon)] = {
            "timesfm": summary("timesfm", subset),
            "kronos": summary("kronos", subset),
        }
    return {"overall": {"timesfm": summary("timesfm", rows), "kronos": summary("kronos", rows)}, "by_horizon": by_horizon}


def run(preregistration: dict, daily: dict) -> dict:
    import numpy as np
    from timesfm import ForecastConfig, TimesFM_2p5_200M_torch
    timesfm_model = TimesFM_2p5_200M_torch.from_pretrained("google/timesfm-2.5-200m-pytorch")
    timesfm_model.compile(ForecastConfig(max_context=CONTEXT_SESSIONS, max_horizon=max(HORIZONS)))
    kronos_model = load_predictor(device="cpu", max_context=CONTEXT_SESSIONS)
    rows = [run_model_case(timesfm_model, kronos_model, daily, case) for case in preregistration["cases"]]
    return {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(), "preregistration": preregistration, "results": rows, "summary": summarize(rows), "promotion_eligible": False, "live_execution": False, "volume_used": False}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preregister", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--preregistration", type=Path)
    args = parser.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if args.preregister:
        payload = preregister()
        out = OUT_DIR / f"current_era_price_only_model_preregistration_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
        out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        print(json.dumps({"path": str(out), "cases": len(payload["cases"]), "origins": payload["selection"]["origin_dates"]}, indent=2))
        return 0
    if args.run:
        if not args.preregistration:
            raise SystemExit("--run requires --preregistration")
        prereg = json.loads(args.preregistration.read_text(encoding="utf-8"))
        payload = run(prereg, load_daily())
        out = OUT_DIR / f"current_era_price_only_model_results_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
        out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        print(json.dumps({"path": str(out), "cases": len(payload["results"]), "summary": payload["summary"]}, indent=2))
        return 0
    raise SystemExit("choose --preregister or --run")


if __name__ == "__main__":
    raise SystemExit(main())
