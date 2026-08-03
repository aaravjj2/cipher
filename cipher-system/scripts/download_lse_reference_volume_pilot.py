#!/usr/bin/env python3
"""Download a bounded LSE minute-volume reference pilot.

This stores raw LSE responses separately from Alpaca market data. It does not
patch the price catalog, reconcile the gate, or run a trading evaluation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, date, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "core") not in sys.path:
    sys.path.insert(0, str(ROOT / "core"))

from flow_cluster_backtest import LSE_BASE, lse_get  # noqa: E402

SYMBOLS = ("SPY", "QQQ", "IWM", "XLF", "XLE", "AAPL", "MSFT", "NVDA", "GE")
NY = ZoneInfo("America/New_York")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def parse_lse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=timezone.utc).astimezone(NY)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="2023-06-01")
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "reference_volume" / "raw" / "lse_pilot")
    args = parser.parse_args()
    pilot_date = date.fromisoformat(args.date)
    end_date = (pilot_date + timedelta(days=1)).isoformat()

    args.output.mkdir(parents=True, exist_ok=True)
    records = []
    for symbol in SYMBOLS:
        params = {
            "symbol": symbol,
            "timeframe": "1m",
            "start": args.date,
            "end": end_date,
            "order": "asc",
            "limit": 5000,
        }
        raw = []
        dataset_used = None
        for dataset in (None, "stocks", "etf", "index"):
            query = dict(params)
            if dataset:
                query["dataset"] = dataset
            candidate = lse_get("/candles", query)
            if candidate:
                raw = candidate
                dataset_used = dataset
                break
        raw_bytes = json.dumps(raw, sort_keys=True, separators=(",", ":")).encode("utf-8")
        raw_path = args.output / f"{args.date}_{symbol}_1m.json"
        raw_path.write_bytes(raw_bytes)

        regular = []
        for row in raw or []:
            timestamp = row.get("ts")
            if not timestamp:
                continue
            local = parse_lse_time(timestamp)
            if time(9, 30) <= local.time() < time(16, 0):
                regular.append(row)
        records.append(
            {
                "symbol": symbol,
                "dataset": dataset_used,
                "raw_path": str(raw_path.relative_to(ROOT)),
                "raw_sha256": sha256_bytes(raw_bytes),
                "raw_rows": len(raw or []),
                "regular_rows": len(regular),
                "regular_first": regular[0].get("ts") if regular else None,
                "regular_last": regular[-1].get("ts") if regular else None,
                "regular_volume": sum(float(row.get("volume") or 0) for row in regular),
            }
        )

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "provider": "london_strategic_edge",
        "endpoint": f"{LSE_BASE}/candles",
        "date": args.date,
        "symbols": list(SYMBOLS),
        "request": {"timeframe": "1m", "order": "asc", "limit": 5000},
        "session_filter": "09:30 <= America/New_York timestamp < 16:00",
        "records": records,
        "allowed_use": "independent_volume_reference_feasibility_only",
        "gate_status": "not_reconciled",
        "prohibited": ["patching_alpaca_prices", "volume_scaling", "daily_bar_substitution", "trading", "signal_evaluation"],
    }
    manifest_path = ROOT / "data" / "reference_volume" / f"lse_reference_volume_pilot_{args.date}.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(manifest_path)


if __name__ == "__main__":
    main()
