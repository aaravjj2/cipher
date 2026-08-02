"""Optional Kronos integration helpers for Cipher research.

Kronos forecasts OHLCV candlestick sequences.  In Cipher it should be treated
as a directional/regime filter for flow/GEX signals, not as an options data
source.  This module keeps the integration optional: importing it does not
require torch or the Hugging Face model stack.
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import random
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parents[0]
KRONOS_ROOT = WORKSPACE / "Stock data" / "external" / "Kronos"
STOCK_DATA_ROOT = WORKSPACE / "Stock data" / "data"
HISTORICAL_BARS_DB = ROOT / "data" / "historical_bars.sqlite"
OUT_DIR = ROOT / "data" / "kronos"
DEFAULT_TOKENIZER_ID = "NeoQuasar/Kronos-Tokenizer-base"
DEFAULT_MODEL_ID = "NeoQuasar/Kronos-small"


def module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def status() -> dict:
    deps = {
        "torch": module_available("torch"),
        "huggingface_hub": module_available("huggingface_hub"),
        "safetensors": module_available("safetensors"),
        "einops": module_available("einops"),
        "pandas": module_available("pandas"),
        "numpy": module_available("numpy"),
    }
    return {
        "kronos_root": str(KRONOS_ROOT),
        "repo_present": KRONOS_ROOT.is_dir(),
        "model_py": str(KRONOS_ROOT / "model" / "kronos.py"),
        "model_py_present": (KRONOS_ROOT / "model" / "kronos.py").is_file(),
        "deps": deps,
        "ready_for_inference": all(deps[k] for k in ("torch", "huggingface_hub", "safetensors", "einops", "pandas", "numpy")),
        "role": "Optional OHLCV forecast/regime filter for flow/Cipher signals; not an options-flow or GEX source.",
    }


def local_csv_path(ticker: str, timeframe: str) -> Path:
    return STOCK_DATA_ROOT / timeframe / f"{ticker.upper()}.csv"


def number(value):
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def parse_dt(raw: str):
    raw = str(raw or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        try:
            return datetime.fromisoformat(raw.replace(" ", "T"))
        except ValueError:
            return None


def _aggregate_ohlcv_rows(rows: list[dict], timeframe: str) -> list[dict]:
    minutes = {"1m": 1, "5m": 5, "15m": 15}.get(timeframe)
    if minutes is None or minutes == 1:
        return rows
    buckets: dict[datetime, dict] = {}
    for row in rows:
        timestamp = row["timestamp"]
        bucket = timestamp.replace(
            minute=(timestamp.minute // minutes) * minutes,
            second=0,
            microsecond=0,
        )
        current = buckets.get(bucket)
        if current is None:
            buckets[bucket] = {
                "timestamp": bucket,
                "open": row["open"],
                "high": row["high"],
                "low": row["low"],
                "close": row["close"],
                "volume": row["volume"],
            }
            continue
        current["high"] = max(current["high"], row["high"])
        current["low"] = min(current["low"], row["low"])
        current["close"] = row["close"]
        current["volume"] += row["volume"]
    out = []
    for row in sorted(buckets.values(), key=lambda item: item["timestamp"]):
        row["amount"] = row["volume"] * (
            (row["open"] + row["high"] + row["low"] + row["close"]) / 4.0
        )
        out.append(row)
    return out


def _load_sqlite_ohlcv_rows(ticker: str, timeframe: str) -> list[dict]:
    if not HISTORICAL_BARS_DB.is_file():
        return []
    rows = []
    with sqlite3.connect(f"file:{HISTORICAL_BARS_DB}?mode=ro", uri=True) as db:
        query = """
            select timestamp, open, high, low, close, volume
            from historical_bars
            where symbol = ?
            order by timestamp
        """
        for timestamp, op, hi, lo, cl, volume in db.execute(query, (ticker.upper(),)):
            dt = parse_dt(timestamp)
            values = [number(value) for value in (op, hi, lo, cl)]
            if not dt or any(value is None for value in values):
                continue
            op_num, hi_num, lo_num, cl_num = values
            rows.append(
                {
                    "timestamp": dt,
                    "open": op_num,
                    "high": hi_num,
                    "low": lo_num,
                    "close": cl_num,
                    "volume": number(volume) or 0.0,
                }
            )
    if timeframe in {"1m", "5m", "15m"} and len(rows) >= 3:
        intraday_intervals = sum(
            1
            for left, right in zip(rows, rows[1:])
            if 0 < (right["timestamp"] - left["timestamp"]).total_seconds() <= 3600
        )
        if intraday_intervals < (len(rows) - 1) / 2:
            return []
    return _aggregate_ohlcv_rows(rows, timeframe)


def load_local_ohlcv_rows(ticker: str, timeframe: str = "5m") -> list[dict]:
    source = local_csv_path(ticker, timeframe)
    if not source.is_file():
        return _load_sqlite_ohlcv_rows(ticker, timeframe)
    rows = []
    with source.open(newline="", encoding="utf-8", errors="ignore") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            dt = parse_dt(row.get("Datetime"))
            op = number(row.get("Open"))
            hi = number(row.get("High"))
            lo = number(row.get("Low"))
            cl = number(row.get("Close"))
            vol = number(row.get("Volume")) or 0.0
            if not dt or op is None or hi is None or lo is None or cl is None:
                continue
            rows.append(
                {
                    "timestamp": dt,
                    "open": op,
                    "high": hi,
                    "low": lo,
                    "close": cl,
                    "volume": vol,
                    "amount": vol * ((op + hi + lo + cl) / 4.0),
                }
            )
    rows.sort(key=lambda r: r["timestamp"])
    return rows


def export_local_ohlcv(
    ticker: str,
    *,
    timeframe: str = "5m",
    start: str | None = None,
    end: str | None = None,
    limit: int = 512,
) -> dict:
    """Export local stock bars to Kronos-compatible CSV columns.

    Kronos examples expect timestamp plus lower-case `open/high/low/close` and
    optional `volume/amount`.  We derive `amount` as volume times OHLC average.
    """
    source = local_csv_path(ticker, timeframe)
    if not source.is_file():
        return {"error": f"Missing local stock CSV: {source}", "ticker": ticker.upper()}

    rows = []
    with source.open(newline="", encoding="utf-8", errors="ignore") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            ts = row.get("Datetime")
            if not ts:
                continue
            day = ts[:10]
            if start and day < start[:10]:
                continue
            if end and day > end[:10]:
                continue
            try:
                op = float(row.get("Open"))
                hi = float(row.get("High"))
                lo = float(row.get("Low"))
                cl = float(row.get("Close"))
                vol = float(row.get("Volume") or 0.0)
            except (TypeError, ValueError):
                continue
            amount = vol * ((op + hi + lo + cl) / 4.0)
            rows.append(
                {
                    "timestamps": ts,
                    "open": op,
                    "high": hi,
                    "low": lo,
                    "close": cl,
                    "volume": vol,
                    "amount": amount,
                }
            )
    if limit and len(rows) > int(limit):
        rows = rows[-int(limit) :]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%S%fZ")
    out = OUT_DIR / f"{ticker.upper()}_{timeframe}_kronos_{stamp}.csv"
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["timestamps", "open", "high", "low", "close", "volume", "amount"])
        writer.writeheader()
        writer.writerows(rows)
    return {
        "ticker": ticker.upper(),
        "timeframe": timeframe,
        "source": str(source),
        "path": str(out),
        "rows": len(rows),
        "start": rows[0]["timestamps"] if rows else None,
        "end": rows[-1]["timestamps"] if rows else None,
        "kronos_ready": status()["ready_for_inference"],
        "caveat": "Export only. Install Kronos dependencies/models before inference.",
    }


def require_kronos_ready() -> None:
    current = status()
    missing = [name for name, present in current["deps"].items() if not present]
    if missing:
        raise RuntimeError(
            "Kronos inference dependencies are missing: "
            + ", ".join(missing)
            + ". Install the Kronos requirements before running forecast filters."
        )
    if not current["repo_present"]:
        raise RuntimeError(f"Kronos repo not found: {KRONOS_ROOT}")


def load_predictor(
    *,
    model_id: str = DEFAULT_MODEL_ID,
    tokenizer_id: str = DEFAULT_TOKENIZER_ID,
    device: str | None = None,
    max_context: int = 512,
):
    require_kronos_ready()
    if str(KRONOS_ROOT) not in sys.path:
        sys.path.insert(0, str(KRONOS_ROOT))
    from model import Kronos, KronosPredictor, KronosTokenizer

    tokenizer = KronosTokenizer.from_pretrained(tokenizer_id)
    model = Kronos.from_pretrained(model_id)
    return KronosPredictor(model, tokenizer, device=device, max_context=max_context)


def set_research_seed(seed: int | None) -> None:
    if seed is None:
        return
    random.seed(int(seed))
    try:
        import numpy as np

        np.random.seed(int(seed))
    except Exception:
        pass
    try:
        import torch

        torch.manual_seed(int(seed))
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(int(seed))
    except Exception:
        pass


def future_rows_for_horizon(rows: list[dict], as_of: str, horizon_days: int, max_pred_bars: int) -> list[dict]:
    as_day = as_of[:10]
    future = [r for r in rows if r["timestamp"].date().isoformat() > as_day]
    days = []
    seen = set()
    for row in future:
        day = row["timestamp"].date().isoformat()
        if day not in seen:
            seen.add(day)
            days.append(day)
        if len(days) >= int(horizon_days):
            break
    allowed = set(days)
    selected = [r for r in future if r["timestamp"].date().isoformat() in allowed]
    if max_pred_bars and len(selected) > int(max_pred_bars):
        selected = selected[: int(max_pred_bars)]
    return selected


def synthetic_future_timestamps(as_of: str, *, timeframe: str, horizon_days: int, max_pred_bars: int) -> list[dict]:
    """Build known regular-session future timestamps without using future prices."""
    step_minutes = {"1m": 1, "5m": 5, "15m": 15}.get(timeframe, 5)
    out = []
    day = datetime.fromisoformat(as_of[:10]) + timedelta(days=1)
    sessions = 0
    while sessions < int(horizon_days):
        if day.weekday() < 5:
            cur = day.replace(hour=9, minute=30, second=0, microsecond=0)
            stop = day.replace(hour=16, minute=0, second=0, microsecond=0)
            while cur < stop:
                out.append({"timestamp": cur})
                if max_pred_bars and len(out) >= int(max_pred_bars):
                    return out
                cur += timedelta(minutes=step_minutes)
            sessions += 1
        day += timedelta(days=1)
    return out


def kronos_forecast_signal(
    predictor,
    ticker: str,
    as_of: str,
    *,
    timeframe: str = "5m",
    horizon_days: int = 2,
    lookback: int = 400,
    max_pred_bars: int = 160,
    temperature: float = 1.0,
    top_p: float = 0.9,
    sample_count: int = 1,
) -> dict:
    """Forecast a local OHLCV path and return a compact directional signal."""
    import pandas as pd

    rows = load_local_ohlcv_rows(ticker, timeframe)
    if not rows:
        return {"ticker": ticker.upper(), "as_of": as_of[:10], "available": False, "reason": "missing_local_ohlcv"}
    as_day = as_of[:10]
    history = [r for r in rows if r["timestamp"].date().isoformat() <= as_day]
    future = future_rows_for_horizon(rows, as_of, horizon_days, max_pred_bars)
    if len(history) < int(lookback):
        return {
            "ticker": ticker.upper(),
            "as_of": as_of[:10],
            "available": False,
            "reason": "insufficient_lookback",
            "history_rows": len(history),
        }
    timestamp_source = "local_future_rows"
    if not future:
        future = synthetic_future_timestamps(
            as_of,
            timeframe=timeframe,
            horizon_days=horizon_days,
            max_pred_bars=max_pred_bars,
        )
        timestamp_source = "synthetic_regular_session"
    if not future:
        return {"ticker": ticker.upper(), "as_of": as_of[:10], "available": False, "reason": "missing_future_timestamps"}

    context = history[-int(lookback) :]
    x_df = pd.DataFrame([{k: r[k] for k in ("open", "high", "low", "close", "volume", "amount")} for r in context])
    x_ts = pd.Series([r["timestamp"] for r in context])
    y_ts = pd.Series([r["timestamp"] for r in future])
    pred_df = predictor.predict(
        df=x_df,
        x_timestamp=x_ts,
        y_timestamp=y_ts,
        pred_len=len(future),
        T=float(temperature),
        top_p=float(top_p),
        sample_count=int(sample_count),
        verbose=False,
    )
    last_close = float(context[-1]["close"])
    pred_close = float(pred_df["close"].iloc[-1])
    pred_high = float(pred_df["high"].max())
    pred_low = float(pred_df["low"].min())
    pred_return = (pred_close - last_close) / last_close * 100.0 if last_close else None
    return {
        "ticker": ticker.upper(),
        "as_of": as_of[:10],
        "available": True,
        "timeframe": timeframe,
        "horizon_days": int(horizon_days),
        "lookback": int(lookback),
        "pred_bars": len(future),
        "timestamp_source": timestamp_source,
        "last_context_close": round(last_close, 4),
        "pred_close": round(pred_close, 4),
        "pred_high": round(pred_high, 4),
        "pred_low": round(pred_low, 4),
        "pred_return_pct": round(pred_return, 4) if pred_return is not None else None,
        "direction": "long" if pred_return and pred_return > 0 else "short" if pred_return and pred_return < 0 else "flat",
    }


def profit_factor(values: list[float]) -> float | None:
    gains = sum(v for v in values if v > 0)
    losses = -sum(v for v in values if v < 0)
    if losses <= 0:
        return None if gains <= 0 else 999.0
    return gains / losses


def summarize_returns(rows: list[dict]) -> dict:
    values = [float(r["trade_return_pct"]) for r in rows if number(r.get("trade_return_pct")) is not None]
    if not values:
        return {"n": 0}
    wins = [v for v in values if v > 0]
    losses = [v for v in values if v < 0]
    pf = profit_factor(values)
    values_sorted = sorted(values)
    mid = len(values_sorted) // 2
    med = values_sorted[mid] if len(values_sorted) % 2 else (values_sorted[mid - 1] + values_sorted[mid]) / 2.0
    return {
        "n": len(values),
        "win_rate": round(len(wins) / len(values), 4),
        "avg_return_pct": round(sum(values) / len(values), 4),
        "median_return_pct": round(med, 4),
        "profit_factor": round(pf, 4) if pf is not None else None,
        "avg_win_pct": round(sum(wins) / len(wins), 4) if wins else None,
        "avg_loss_pct": round(sum(losses) / len(losses), 4) if losses else None,
    }


def kronos_threshold_sweep(rows: list[dict], thresholds: list[float] | None = None) -> list[dict]:
    thresholds = thresholds or [0.0, 0.025, 0.05, 0.1, 0.2, 0.3, 0.5, 0.75, 1.0]
    out = []
    for threshold in thresholds:
        agreed = []
        rejected = []
        for row in rows:
            pred_ret = number(row.get("kronos_pred_return_pct"))
            if pred_ret is None:
                continue
            direction = row.get("trade_direction")
            ok = abs(pred_ret) >= float(threshold) and (
                (direction == "long" and pred_ret > 0)
                or (direction == "short" and pred_ret < 0)
            )
            if ok:
                agreed.append(row)
            else:
                rejected.append(row)
        out.append(
            {
                "min_abs_pred_return_pct": float(threshold),
                "agreed": summarize_returns(agreed),
                "rejected": summarize_returns(rejected),
            }
        )
    return out


def run_candidate_filter(
    candidate_csv: Path,
    *,
    timeframe: str = "5m",
    lookback: int = 400,
    max_pred_bars: int = 160,
    model_id: str = DEFAULT_MODEL_ID,
    tokenizer_id: str = DEFAULT_TOKENIZER_ID,
    device: str | None = None,
    sample_count: int = 1,
    seed: int | None = 42,
    min_abs_pred_return_pct: float = 0.0,
    primary_only: bool = True,
    horizon: int = 2,
    limit: int = 0,
    predictor=None,
    tickers: list[str] | None = None,
) -> dict:
    """Use Kronos forecasts as a direction/regime filter over flow candidates."""
    if not candidate_csv.is_file():
        raise FileNotFoundError(candidate_csv)
    set_research_seed(seed)
    if predictor is None:
        predictor = load_predictor(model_id=model_id, tokenizer_id=tokenizer_id, device=device)
    candidates = []
    ticker_set = {t.upper().strip() for t in (tickers or []) if t.strip()}
    with candidate_csv.open(newline="", encoding="utf-8", errors="ignore") as fh:
        for row in csv.DictReader(fh):
            if ticker_set and (row.get("ticker") or "").upper() not in ticker_set:
                continue
            if primary_only and str(row.get("is_primary")) != "True":
                continue
            if int(row.get("horizon") or 0) != int(horizon):
                continue
            if not row.get("trade_return_pct") or not row.get("trade_direction"):
                continue
            candidates.append(row)
    if limit:
        candidates = candidates[: int(limit)]

    forecast_cache = {}
    enriched = []
    for row in candidates:
        key = (row["ticker"].upper(), row["as_of"][:10], int(row["horizon"]))
        if key not in forecast_cache:
            forecast_cache[key] = kronos_forecast_signal(
                predictor,
                key[0],
                key[1],
                timeframe=timeframe,
                horizon_days=key[2],
                lookback=lookback,
                max_pred_bars=max_pred_bars,
                sample_count=sample_count,
            )
        signal = forecast_cache[key]
        out = dict(row)
        out["kronos_available"] = signal.get("available")
        out["kronos_reason"] = signal.get("reason")
        out["kronos_direction"] = signal.get("direction")
        out["kronos_pred_return_pct"] = signal.get("pred_return_pct")
        out["kronos_pred_close"] = signal.get("pred_close")
        out["kronos_timestamp_source"] = signal.get("timestamp_source")
        pred_ret = number(signal.get("pred_return_pct"))
        agrees = (
            signal.get("available")
            and pred_ret is not None
            and abs(pred_ret) >= float(min_abs_pred_return_pct)
            and (
                (row.get("trade_direction") == "long" and pred_ret > 0)
                or (row.get("trade_direction") == "short" and pred_ret < 0)
            )
        )
        out["kronos_agrees"] = bool(agrees)
        enriched.append(out)

    agreed = [r for r in enriched if r.get("kronos_agrees")]
    rejected = [r for r in enriched if r.get("kronos_available") and not r.get("kronos_agrees")]
    unavailable = [r for r in enriched if not r.get("kronos_available")]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%S%fZ")
    out_csv = OUT_DIR / f"kronos_filter_{stamp}.csv"
    fields = list(enriched[0].keys()) if enriched else []
    if fields:
        with out_csv.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(enriched)
    report = {
        "as_of": datetime.utcnow().isoformat() + "Z",
        "mode": "kronos_flow_candidate_filter",
        "candidate_csv": str(candidate_csv),
        "output_csv": str(out_csv) if fields else None,
        "model_id": model_id,
        "tokenizer_id": tokenizer_id,
        "timeframe": timeframe,
        "lookback": int(lookback),
        "max_pred_bars": int(max_pred_bars),
        "sample_count": int(sample_count),
        "seed": seed,
        "primary_only": bool(primary_only),
        "horizon": int(horizon),
        "ticker_filter": sorted(ticker_set),
        "min_abs_pred_return_pct": float(min_abs_pred_return_pct),
        "input_summary": summarize_returns(enriched),
        "kronos_agreed_summary": summarize_returns(agreed),
        "kronos_rejected_summary": summarize_returns(rejected),
        "threshold_sweep": kronos_threshold_sweep(enriched),
        "unavailable_n": len(unavailable),
        "forecast_count": len(forecast_cache),
        "caveat": "Kronos is an OHLCV forecast/regime filter, not an options-flow or GEX source. This report must be validated out of sample.",
    }
    out_json = OUT_DIR / f"kronos_filter_{stamp}.json"
    out_json.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    report["path"] = str(out_json)
    return report


def parse_int_grid(raw: str) -> list[int]:
    return [int(part.strip()) for part in str(raw or "").split(",") if part.strip()]


def parse_tickers(raw_values: list[str] | None) -> list[str]:
    out = []
    seen = set()
    for raw in raw_values or []:
        for part in str(raw).replace(";", ",").split(","):
            ticker = part.upper().strip()
            if ticker and ticker not in seen:
                seen.add(ticker)
                out.append(ticker)
    return out


def run_candidate_sweep(
    candidate_csv: Path,
    *,
    timeframe: str = "5m",
    lookback_grid: list[int] | None = None,
    max_pred_bars_grid: list[int] | None = None,
    model_id: str = DEFAULT_MODEL_ID,
    tokenizer_id: str = DEFAULT_TOKENIZER_ID,
    device: str | None = None,
    sample_count: int = 1,
    seed: int | None = 42,
    primary_only: bool = True,
    horizon: int = 2,
    limit: int = 0,
    tickers: list[str] | None = None,
) -> dict:
    """Sweep Kronos lookback/prediction length while reusing one model load."""
    lookback_grid = lookback_grid or [128, 256]
    max_pred_bars_grid = max_pred_bars_grid or [32, 64]
    set_research_seed(seed)
    predictor = load_predictor(model_id=model_id, tokenizer_id=tokenizer_id, device=device)
    runs = []
    for lookback in lookback_grid:
        for max_pred_bars in max_pred_bars_grid:
            report = run_candidate_filter(
                candidate_csv,
                timeframe=timeframe,
                lookback=int(lookback),
                max_pred_bars=int(max_pred_bars),
                model_id=model_id,
                tokenizer_id=tokenizer_id,
                device=device,
                sample_count=sample_count,
                seed=seed,
                min_abs_pred_return_pct=0.0,
                primary_only=primary_only,
                horizon=horizon,
                limit=limit,
                predictor=predictor,
                tickers=tickers,
            )
            best_threshold = None
            qualified = [
                row
                for row in report.get("threshold_sweep") or []
                if (row.get("agreed") or {}).get("n", 0) >= 10
            ]
            if qualified:
                best_threshold = max(
                    qualified,
                    key=lambda row: (
                        (row.get("agreed") or {}).get("avg_return_pct", -999),
                        (row.get("agreed") or {}).get("profit_factor", 0) or 0,
                        (row.get("agreed") or {}).get("n", 0),
                    ),
                )
            runs.append(
                {
                    "lookback": int(lookback),
                    "max_pred_bars": int(max_pred_bars),
                    "report_path": report.get("path"),
                    "output_csv": report.get("output_csv"),
                    "input_summary": report.get("input_summary"),
                    "best_threshold_min_n_10": best_threshold,
                }
            )
    ranked = sorted(
        runs,
        key=lambda row: (
            ((row.get("best_threshold_min_n_10") or {}).get("agreed") or {}).get("avg_return_pct", -999),
            (((row.get("best_threshold_min_n_10") or {}).get("agreed") or {}).get("profit_factor", 0) or 0),
        ),
        reverse=True,
    )
    payload = {
        "as_of": datetime.utcnow().isoformat() + "Z",
        "mode": "kronos_flow_candidate_sweep",
        "candidate_csv": str(candidate_csv),
        "timeframe": timeframe,
        "lookback_grid": [int(x) for x in lookback_grid],
        "max_pred_bars_grid": [int(x) for x in max_pred_bars_grid],
        "model_id": model_id,
        "tokenizer_id": tokenizer_id,
        "sample_count": int(sample_count),
        "seed": seed,
        "primary_only": bool(primary_only),
        "horizon": int(horizon),
        "ticker_filter": sorted({t.upper().strip() for t in (tickers or []) if t.strip()}),
        "runs": runs,
        "top_runs": ranked,
        "caveat": "Kronos parameter sweep over flow candidates. Ranking is exploratory and sample-size constrained.",
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%S%fZ")
    out_json = OUT_DIR / f"kronos_sweep_{stamp}.json"
    out_json.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    payload["path"] = str(out_json)
    return payload


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Optional Kronos research bridge for Cipher.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="Show Kronos repo/dependency status.")
    exp = sub.add_parser("export", help="Export local OHLCV CSV in Kronos-compatible shape.")
    exp.add_argument("--ticker", required=True)
    exp.add_argument("--timeframe", choices=("1m", "5m", "15m"), default="5m")
    exp.add_argument("--start")
    exp.add_argument("--end")
    exp.add_argument("--limit", type=int, default=512)
    filt = sub.add_parser("filter-candidates", help="Filter flow-cluster candidate trades with real Kronos forecasts.")
    filt.add_argument("--candidate-csv", required=True)
    filt.add_argument("--timeframe", choices=("1m", "5m", "15m"), default="5m")
    filt.add_argument("--lookback", type=int, default=400)
    filt.add_argument("--max-pred-bars", type=int, default=160)
    filt.add_argument("--model-id", default=DEFAULT_MODEL_ID)
    filt.add_argument("--tokenizer-id", default=DEFAULT_TOKENIZER_ID)
    filt.add_argument("--device")
    filt.add_argument("--sample-count", type=int, default=1)
    filt.add_argument("--seed", type=int, default=42)
    filt.add_argument("--min-abs-pred-return-pct", type=float, default=0.0)
    filt.add_argument("--all-setups", action="store_true")
    filt.add_argument("--horizon", type=int, default=2)
    filt.add_argument("--limit", type=int, default=0)
    filt.add_argument("--ticker", action="append", default=[])
    sw = sub.add_parser("sweep-candidates", help="Sweep Kronos lookback/prediction settings over flow candidates.")
    sw.add_argument("--candidate-csv", required=True)
    sw.add_argument("--timeframe", choices=("1m", "5m", "15m"), default="5m")
    sw.add_argument("--lookback-grid", default="64,128,256")
    sw.add_argument("--max-pred-bars-grid", default="32,64")
    sw.add_argument("--model-id", default=DEFAULT_MODEL_ID)
    sw.add_argument("--tokenizer-id", default=DEFAULT_TOKENIZER_ID)
    sw.add_argument("--device")
    sw.add_argument("--sample-count", type=int, default=1)
    sw.add_argument("--seed", type=int, default=42)
    sw.add_argument("--all-setups", action="store_true")
    sw.add_argument("--horizon", type=int, default=2)
    sw.add_argument("--limit", type=int, default=0)
    sw.add_argument("--ticker", action="append", default=[])
    args = parser.parse_args(argv)
    try:
        if args.cmd == "status":
            payload = status()
        elif args.cmd == "export":
            payload = export_local_ohlcv(
                args.ticker,
                timeframe=args.timeframe,
                start=args.start,
                end=args.end,
                limit=args.limit,
            )
        elif args.cmd == "filter-candidates":
            payload = run_candidate_filter(
                Path(args.candidate_csv),
                timeframe=args.timeframe,
                lookback=args.lookback,
                max_pred_bars=args.max_pred_bars,
                model_id=args.model_id,
                tokenizer_id=args.tokenizer_id,
                device=args.device,
                sample_count=args.sample_count,
                seed=args.seed,
                min_abs_pred_return_pct=args.min_abs_pred_return_pct,
                primary_only=not args.all_setups,
                horizon=args.horizon,
                limit=args.limit,
                tickers=parse_tickers(args.ticker),
            )
        else:
            payload = run_candidate_sweep(
                Path(args.candidate_csv),
                timeframe=args.timeframe,
                lookback_grid=parse_int_grid(args.lookback_grid),
                max_pred_bars_grid=parse_int_grid(args.max_pred_bars_grid),
                model_id=args.model_id,
                tokenizer_id=args.tokenizer_id,
                device=args.device,
                sample_count=args.sample_count,
                seed=args.seed,
                primary_only=not args.all_setups,
                horizon=args.horizon,
                limit=args.limit,
                tickers=parse_tickers(args.ticker),
            )
    except Exception as exc:
        payload = {
            "error": str(exc),
            "cmd": args.cmd,
            "kronos_status": status(),
        }
        print(json.dumps(payload, indent=2, default=str))
        return 1
    print(json.dumps(payload, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
