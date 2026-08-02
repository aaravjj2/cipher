#!/usr/bin/env python3
"""Run the pre-registered, gated AMD Kronos historical verification.

This is a one-off research evaluator, not scheduled forecast infrastructure.
It reads the immutable local Hugging Face minute archive through DuckDB,
restricts input and realized bars to regular NYSE sessions, and writes a
data-backed JSON/Markdown result suitable for the Kronos checklist.
"""
from __future__ import annotations

import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.kronos_research import (  # noqa: E402
    DEFAULT_MODEL_ID,
    DEFAULT_TOKENIZER_ID,
    load_predictor,
    set_research_seed,
)

CATALOG = ROOT / "data" / "market_catalog.duckdb"
OUT_DIR = ROOT / "data" / "kronos"
TICKER = "AMD"
ORIGINS = ("2020-08-25", "2020-08-26", "2020-08-27")
HORIZONS = (5, 20)
CONTEXT_DAYS = 20
SEEDS = tuple(range(42, 62))


def load_regular_session_daily_bars(
    *, ticker: str = TICKER, start: str = "2020-07-29", end: str = "2020-09-25"
) -> list[dict]:
    """Aggregate raw minute bars without letting extended-hours leak in."""
    import duckdb

    if not CATALOG.is_file():
        raise FileNotFoundError(f"missing local market catalog: {CATALOG}")
    query = """
        with regular as (
          select
            date(timezone('America/New_York', timestamp)) as trading_day,
            timezone('America/New_York', timestamp) as local_timestamp,
            open, high, low, close, volume
          from cipher_market.ohlcv_1m
          where ticker = ?
            and date(timezone('America/New_York', timestamp)) between ? and ?
            and cast(timezone('America/New_York', timestamp) as time) between time '09:30:00' and time '16:00:00'
        ), numbered as (
          select *,
            row_number() over (partition by trading_day order by local_timestamp) as opening_row,
            row_number() over (partition by trading_day order by local_timestamp desc) as closing_row
          from regular
        )
        select trading_day, count(*) as bars,
          max(open) filter (where opening_row = 1) as open,
          max(high) as high, min(low) as low,
          max(close) filter (where closing_row = 1) as close,
          sum(volume) as volume
        from numbered
        group by trading_day
        order by trading_day
    """
    with duckdb.connect(str(CATALOG), read_only=True) as db:
        rows = db.execute(query, [ticker.upper(), start, end]).fetchall()
    bars = []
    for day, count, op, hi, lo, cl, volume in rows:
        if int(count) != 391:
            continue
        bars.append({
            "date": day.isoformat(), "open": float(op), "high": float(hi),
            "low": float(lo), "close": float(cl), "volume": float(volume),
        })
    return bars


def _future_session_dates(realized: list[dict]) -> list[datetime]:
    """Known exchange-session dates only; actual prices are never passed in."""
    import pandas as pd

    return list(pd.to_datetime([row["date"] for row in realized]).to_pydatetime())


def _forecast_inputs(context: list[dict], realized: list[dict], *, price_only: bool = False):
    import pandas as pd

    x_df = pd.DataFrame([
        ({key: row[key] for key in ("open", "high", "low", "close")}
         if price_only else
         {**{key: row[key] for key in ("open", "high", "low", "close", "volume")},
          "amount": row["volume"] * ((row["open"] + row["high"] + row["low"] + row["close"]) / 4.0)})
        for row in context
    ])
    x_ts = pd.Series(pd.to_datetime([row["date"] for row in context]))
    y_ts = pd.Series(_future_session_dates(realized))
    return x_df, x_ts, y_ts


def forecast_terminal_samples(
    predictor, context: list[dict], *, realized: list[dict], origin: str, horizon: int,
    temperature: float = 1.0, top_p: float = 0.9, seeds: tuple[int, ...] = SEEDS,
    price_only: bool = False,
) -> tuple[list[float], float]:
    """Return independent paths for empirical calibration, not native quantiles."""
    x_df, x_ts, y_ts = _forecast_inputs(context, realized, price_only=price_only)
    samples = []
    started = time.monotonic()
    for seed in seeds:
        set_research_seed(seed)
        pred = predictor.predict(
            df=x_df, x_timestamp=x_ts, y_timestamp=y_ts, pred_len=horizon,
            T=float(temperature), top_p=float(top_p), sample_count=1, verbose=False,
        )
        close = float(pred["close"].iloc[-1])
        if not math.isfinite(close):
            raise RuntimeError(f"non-finite terminal close for {origin} horizon {horizon}, seed {seed}")
        samples.append(close)
    return samples, time.monotonic() - started


def forecast_terminal_ensemble(
    predictor, context: list[dict], *, realized: list[dict], horizon: int,
    temperature: float, top_p: float, sample_count: int, seed: int,
    price_only: bool = False,
) -> tuple[float, float]:
    """Return Kronos's internally averaged N-path terminal point forecast."""
    x_df, x_ts, y_ts = _forecast_inputs(context, realized, price_only=price_only)
    set_research_seed(seed)
    started = time.monotonic()
    pred = predictor.predict(
        df=x_df, x_timestamp=x_ts, y_timestamp=y_ts, pred_len=horizon,
        T=float(temperature), top_p=float(top_p), sample_count=int(sample_count), verbose=False,
    )
    close = float(pred["close"].iloc[-1])
    if not math.isfinite(close):
        raise RuntimeError("non-finite ensemble terminal close")
    return close, time.monotonic() - started


def percentile(values: list[float], q: float) -> float:
    import numpy as np
    return float(np.percentile(values, q))


def run() -> dict:
    bars = load_regular_session_daily_bars()
    by_date = {row["date"]: index for index, row in enumerate(bars)}
    required = {"2020-07-29", "2020-09-25"}
    if not required.issubset(by_date):
        raise RuntimeError("the pre-registered AMD gate-clean run is absent from the local catalog")
    predictor = load_predictor(model_id=DEFAULT_MODEL_ID, tokenizer_id=DEFAULT_TOKENIZER_ID, device="cpu", max_context=CONTEXT_DAYS)
    rows = []
    for origin in ORIGINS:
        origin_index = by_date[origin]
        context = bars[origin_index - CONTEXT_DAYS + 1:origin_index + 1]
        if len(context) != CONTEXT_DAYS:
            raise RuntimeError(f"insufficient pre-origin context for {origin}")
        for horizon in HORIZONS:
            realized = bars[origin_index + 1:origin_index + 1 + horizon]
            if len(realized) != horizon:
                raise RuntimeError(f"insufficient realized daily bars for {origin} horizon {horizon}")
            samples, seconds = forecast_terminal_samples(predictor, context, realized=realized, origin=origin, horizon=horizon)
            origin_close = context[-1]["close"]
            actual_close = realized[-1]["close"]
            p10, p50, p90 = (percentile(samples, q) for q in (10, 50, 90))
            rows.append({
                "ticker": TICKER, "origin": origin, "horizon_sessions": horizon,
                "input_dates": [context[0]["date"], context[-1]["date"]],
                "realization_dates": [realized[0]["date"], realized[-1]["date"]],
                "origin_close": origin_close, "actual_terminal_close": actual_close,
                "kronos_terminal_close_p10": p10, "kronos_terminal_close_p50": p50,
                "kronos_terminal_close_p90": p90,
                "actual_inside_p10_p90": p10 <= actual_close <= p90,
                "kronos_terminal_absolute_error": abs(p50 - actual_close),
                "naive_terminal_close": origin_close,
                "naive_terminal_absolute_error": abs(origin_close - actual_close),
                "kronos_beats_naive": abs(p50 - actual_close) < abs(origin_close - actual_close),
                "sample_count": len(samples), "seeds": list(SEEDS), "inference_seconds": seconds,
            })
    return {
        "schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "pre-registered historical Kronos verification; research-only",
        "data_source": str(CATALOG), "ticker": TICKER,
        "configuration": {"model": DEFAULT_MODEL_ID, "tokenizer": DEFAULT_TOKENIZER_ID, "device": "cpu", "context_days": CONTEXT_DAYS, "temperature": 1.0, "top_p": 0.9, "samples": len(SEEDS)},
        "timesfm_comparison": {"available": False, "reason": "base adapter requires 32 observations; pre-registered clean context is 20"},
        "results": rows, "live_execution": False, "promotion_eligible": False,
    }


def main() -> int:
    payload = run()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"kronos_real_verification_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"path": str(out), "result_count": len(payload["results"]), "live_execution": False}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
