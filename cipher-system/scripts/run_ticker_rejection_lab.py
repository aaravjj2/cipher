#!/usr/bin/env python3
"""Fetch read-only stock bars and run the MU/TSLA wall-rejection lab."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "core"))

import app  # noqa: E402
from core.ticker_rejection_lab import TARGET_TICKERS, run_lab  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", default=",".join(TARGET_TICKERS))
    parser.add_argument("--start", default="2026-07-22")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    tickers = tuple(dict.fromkeys(value.strip().upper() for value in args.tickers.split(",") if value.strip()))
    bars = {}
    sources = {}
    for ticker in (*tickers,):
        response = app.bars(ticker, "5m", limit=1000, start=args.start)
        bars[ticker] = response.get("bars") or []
        sources[ticker] = f"Alpaca {response.get('feed', 'unknown').upper()} 5-minute raw-adjustment bars"
    payload = run_lab(
        bars,
        output_directory=args.output or ROOT / "data" / "ticker_rejection_lab",
        bar_sources=sources,
    )
    print(json.dumps({
        "status": payload["status"],
        "as_of": payload["as_of"],
        "catalog_size": payload["protocol"]["catalog_size"],
        "unique_signal_paths": payload["protocol"]["unique_signal_paths"],
        "descriptive_leaders": len(payload["descriptive_leaders"]),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
