#!/usr/bin/env python3
"""Scope the isolated Alpaca recovery panel with Cipher's price-only rules.

The full volume-reconciled gate is not called, weakened, or replaced here.
This audit is exclusively for price-only forecast research and cannot make a
strategy eligible for paper trading or use volume as a feature/evaluation.
"""
from __future__ import annotations
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.research_platform.market_quality import require_price_only_market_day

NORMALIZED = ROOT / "data" / "normalized" / "alpaca_sip_holdout_c_1m"
QUALITY = ROOT / "data" / "market_quality"
PANEL = ("SPY", "QQQ", "IWM", "XLF", "XLE", "AAPL", "MSFT", "NVDA", "GE")
MIN_STRETCH = 52


def main() -> None:
    paths = sorted(NORMALIZED.glob("year=*/month=*/*.parquet"))
    if not paths:
        raise SystemExit("no normalized Alpaca recovery partitions")
    rows = []
    for path in paths:
        frame = pd.read_parquet(path, columns=["timestamp", "ticker", "close"])
        frame = frame[frame["ticker"].isin(PANEL)]
        if frame.empty:
            continue
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True).dt.tz_convert("America/New_York")
        for ticker, group in frame.groupby("ticker"):
            rows.append({"ticker": ticker, "date": group["timestamp"].iloc[0].date().isoformat(), "bars": len(group),
                         "close": float(group.sort_values("timestamp").iloc[-1]["close"]), "path": str(path)})
    daily = pd.DataFrame(rows).sort_values(["ticker", "date"])
    results, stretches = [], []
    for ticker, group in daily.groupby("ticker", sort=True):
        prior_date = prior_close = None
        stretch = []
        for row in group.itertuples(index=False):
            contiguous = prior_date is not None and (pd.Timestamp(row.date) - pd.Timestamp(prior_date)).days <= 4
            ratio = (row.close / prior_close) if contiguous and prior_close and prior_close > 0 else None
            # Match the existing scope treatment of a first available session:
            # completeness starts a stretch, continuity is required thereafter.
            decision = require_price_only_market_day(observed_bars=int(row.bars), close_ratio_to_previous_session=ratio)
            eligible = int(row.bars) == 391 and (prior_date is None or not contiguous or decision["eligible"])
            result = {"ticker": ticker, "date": row.date, "bars": int(row.bars), "close": row.close,
                      "close_ratio_to_previous_session": ratio, "price_only_eligible": eligible,
                      "reason": "first_or_calendar_restart" if eligible and ratio is None else decision["price_continuity"]["reason"]}
            results.append(result)
            if eligible:
                stretch.append(result)
            else:
                if len(stretch) >= MIN_STRETCH:
                    stretches.append({"ticker": ticker, "start": stretch[0]["date"], "end": stretch[-1]["date"], "sessions": len(stretch)})
                stretch = []
            prior_date, prior_close = row.date, row.close
        if len(stretch) >= MIN_STRETCH:
            stretches.append({"ticker": ticker, "start": stretch[0]["date"], "end": stretch[-1]["date"], "sessions": len(stretch)})
    common = {}
    for result in results:
        if result["price_only_eligible"]:
            common.setdefault(result["date"], []).append(result["ticker"])
    common_eligible = [{"date": day, "tickers": sorted(tickers), "count": len(tickers)} for day, tickers in sorted(common.items())]
    code = ROOT / "core" / "research_platform" / "market_quality.py"
    payload = {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(), "provider": "Alpaca SIP", "feed": "sip",
               "panel": list(PANEL), "normalized_partitions": len(paths), "normalized_partition_sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
               "gate": {"session_completeness": "exactly 391 NY regular-session minute bars", "price_continuity": "daily close ratio strictly between 0.5 and 2.0 when prior session is present", "volume_reconciliation": "not evaluated", "allowed_use": "price_forecast_research_only_no_volume_features"},
               "full_gate_changed": False, "full_gate_module_sha256": hashlib.sha256(code.read_bytes()).hexdigest(),
               "daily_results": results, "stretches_at_least_52_sessions": stretches, "common_eligible_by_day": common_eligible,
               "ranking_outcomes_evaluated": False, "live_execution": False}
    QUALITY.mkdir(parents=True, exist_ok=True)
    output = QUALITY / f"alpaca_holdout_c_price_only_scope_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"path": str(output), "partitions": len(paths), "stretches": len(stretches), "max_common": max((x["count"] for x in common_eligible), default=0)}, indent=2))


if __name__ == "__main__":
    main()
