#!/usr/bin/env python3
"""Pilot the free Hugging Face/Finnhub archive as an independent volume check."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
SYMBOLS = ("SPY", "QQQ", "IWM", "XLF", "XLE", "AAPL", "MSFT", "NVDA", "GE")
THRESHOLD = 0.05


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--month", default="2023-06")
    parser.add_argument("--date", default="2023-06-01")
    args = parser.parse_args()
    source = ROOT / "data" / "market_raw" / "huggingface_ohlcv_1m" / "data" / f"ohlcv_{args.month}.parquet"
    placeholders = ", ".join("?" for _ in SYMBOLS)
    with duckdb.connect(":memory:") as db:
        rows = db.execute(
            f"""
            with hf as (
                select ticker, count(*) as bars, sum(volume) as volume
                from read_parquet(?)
                where ticker in ({placeholders})
                  and cast(timezone('America/New_York', cast(timestamp as timestamp with time zone)) as time) >= time '09:30:00'
                  and cast(timezone('America/New_York', cast(timestamp as timestamp with time zone)) as time) < time '16:00:00'
                  and cast(timezone('America/New_York', cast(timestamp as timestamp with time zone)) as date) = cast(? as date)
                group by ticker
            ), alpaca as (
                select ticker, count(*) as bars, sum(volume) as volume
                from read_parquet(?)
                where ticker in ({placeholders})
                  and cast(timezone('America/New_York', cast(timestamp as timestamp with time zone)) as time) >= time '09:30:00'
                  and cast(timezone('America/New_York', cast(timestamp as timestamp with time zone)) as time) < time '16:00:00'
                  and source_day = ?
                group by ticker
            )
            select coalesce(a.ticker, h.ticker) as ticker,
                   coalesce(a.bars, 0) as alpaca_bars, coalesce(h.bars, 0) as hf_bars,
                   coalesce(a.volume, 0) as alpaca_volume, coalesce(h.volume, 0) as hf_volume
            from alpaca a full outer join hf h on a.ticker = h.ticker
            order by ticker
            """,
            [str(source), *SYMBOLS, args.date,
             str(ROOT / "data" / "normalized" / "alpaca_sip_holdout_c_1m" / f"year={args.date[:4]}" / f"month={args.date[5:7]}" / f"{args.date}.parquet"), *SYMBOLS, args.date],
        ).fetchall()
    results = []
    for ticker, alpaca_bars, hf_bars, alpaca_volume, hf_volume in rows:
        difference = abs(float(alpaca_volume) - float(hf_volume)) / float(hf_volume) if hf_volume else None
        results.append({
            "symbol": ticker,
            "alpaca_bars": int(alpaca_bars),
            "hf_bars": int(hf_bars),
            "alpaca_volume": float(alpaca_volume),
            "hf_volume": float(hf_volume),
            "relative_difference": difference,
            "passes_5_percent": difference is not None and difference <= THRESHOLD,
        })
    output = ROOT / "data" / "market_quality" / f"huggingface_alpaca_volume_pilot_{args.date}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": "mito0o852/OHLCV-1m (Finnhub-derived public archive)",
        "month": args.month,
        "date_tested": args.date,
        "session_filter": "09:30 <= America/New_York timestamp < 16:00",
        "threshold": THRESHOLD,
        "results": results,
        "pass_count": sum(r["passes_5_percent"] for r in results),
        "total_count": len(results),
        "status": "rejected_pilot_inconsistent_volume" if any(not r["passes_5_percent"] for r in results) else "pilot_passes_pending_broader_validation",
        "full_gate_changed": False,
        "trading_or_signal_evaluation": False,
    }, indent=2, sort_keys=True) + "\n")
    print(output)


if __name__ == "__main__":
    main()
