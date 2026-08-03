#!/usr/bin/env python3
"""Apply Cipher's unchanged full gate to the current-era Alpaca panel."""
from __future__ import annotations
import json
import sys
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.research_platform.market_quality import require_eligible_market_day  # noqa: E402

PANEL = ("SPY", "QQQ", "IWM", "XLF", "XLE", "AAPL", "MSFT", "NVDA", "GE")

def headers():
    values = dict(line.split("=", 1) for line in (ROOT / ".env").read_text().splitlines() if "=" in line)
    return {"APCA-API-KEY-ID": values["ALPACA_API_KEY"], "APCA-API-SECRET-KEY": values["ALPACA_SECRET_KEY"]}

def main() -> None:
    frames = [pd.read_parquet(p, columns=["timestamp", "ticker", "volume"]) for p in sorted((ROOT / "data" / "normalized" / "alpaca_sip_holdout_c_1m").glob("year=202[345]/month=*/*.parquet"))]
    minute = pd.concat(frames)
    minute["date"] = pd.to_datetime(minute["timestamp"], utc=True).dt.tz_convert("America/New_York").dt.date.astype(str)
    local = minute.groupby(["ticker", "date"]).agg(bars=("volume", "size"), volume=("volume", "sum")).reset_index()
    response = requests.get("https://data.alpaca.markets/v2/stocks/bars", headers=headers(), params={"symbols": ",".join(PANEL), "timeframe": "1Day", "start": "2023-01-01T00:00:00Z", "end": "2025-12-31T23:59:59Z", "feed": "sip", "limit": 10000}, timeout=90)
    response.raise_for_status()
    daily = {(symbol, row["t"][:10]): row["v"] for symbol, rows in response.json().get("bars", {}).items() for row in rows}
    results = []
    for row in local.itertuples(index=False):
        decision = require_eligible_market_day(observed_bars=int(row.bars), observed_volume=float(row.volume), reference_volume=daily.get((row.ticker, row.date)))
        results.append({"ticker": row.ticker, "date": row.date, "observed_bars": int(row.bars), "observed_volume": float(row.volume), "reference_volume": daily.get((row.ticker, row.date)), **decision})
    passed = sum(bool(item["eligible"]) for item in results)
    output = ROOT / "data" / "market_quality" / "alpaca_current_era_full_gate.json"
    output.write_text(json.dumps({"provider": "Alpaca SIP", "reference": "Alpaca SIP daily aggregates", "reference_scope": "daily aggregate; not verified regular-session-only", "results": results, "pass_count": passed, "total_count": len(results), "status": "blocked_reference_scope_mismatch" if passed < len(results) else "passed", "repair_action": "blocked; obtain a like-for-like regular-session reference rather than adjust volumes or thresholds", "full_gate_changed": False, "live_execution": False}, indent=2) + "\n")
    print(output)

if __name__ == "__main__": main()
