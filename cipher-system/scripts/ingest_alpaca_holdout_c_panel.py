#!/usr/bin/env python3
"""Read-only, resumable SIP recovery ingest for the frozen Holdout C panel.

This source is isolated from all prior vendor data.  It writes immutable raw
provider responses plus normalized partitions, uses no volume signal, and does
not inspect returns, rank securities, create orders, or alter the full gate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from datetime import date, datetime, time as clock_time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "alpaca_sip_holdout_c_1m"
NORMALIZED = ROOT / "data" / "normalized" / "alpaca_sip_holdout_c_1m"
QUALITY = ROOT / "data" / "market_quality"
PANEL = ("SPY", "QQQ", "IWM", "XLF", "XLE", "AAPL", "MSFT", "NVDA", "GE")
START, END = date(2017, 1, 1), date(2019, 12, 31)


def headers() -> dict[str, str]:
    values = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return {"APCA-API-KEY-ID": values["ALPACA_API_KEY"], "APCA-API-SECRET-KEY": values["ALPACA_SECRET_KEY"]}


def ny_utc(day: date, local: str) -> str:
    hour, minute = map(int, local.split(":"))
    value = datetime.combine(day, clock_time(hour, minute), ZoneInfo("America/New_York"))
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def sessions(start: date, end: date) -> list[dict]:
    response = requests.get("https://paper-api.alpaca.markets/v2/calendar", headers=headers(),
                            params={"start": start.isoformat(), "end": end.isoformat()}, timeout=60)
    response.raise_for_status()
    return [row for row in response.json() if row["open"] == "09:30" and row["close"] == "16:00"]


def atomic_json(path: Path, payload: dict) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = hashlib.sha256(path.read_bytes()).hexdigest()
        if existing != digest:
            raise RuntimeError(f"immutable raw payload mismatch: {path}")
        return digest
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(encoded)
    os.replace(temporary, path)
    return digest


def normalize(raw: dict, destination: Path) -> int:
    rows = []
    for symbol, bars in raw["response"].get("bars", {}).items():
        for bar in bars:
            rows.append({"timestamp": bar["t"], "ticker": symbol, "open": bar["o"], "high": bar["h"],
                         "low": bar["l"], "close": bar["c"], "volume": bar["v"], "trade_count": bar.get("n"),
                         "vwap": bar.get("vw"), "provider": "alpaca_sip", "source_day": raw["session_date"]})
    frame = pd.DataFrame(rows)
    if not frame.empty:
        invalid = (frame.low > frame.open) | (frame.open > frame.high) | (frame.low > frame.close) | (frame.close > frame.high) | (frame[["open", "high", "low", "close"]] <= 0).any(axis=1)
        if invalid.any():
            raise RuntimeError(f"OHLC integrity failure for {raw['session_date']}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp")
    frame.to_parquet(temporary, index=False)
    os.replace(temporary, destination)
    return len(frame)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default=START.isoformat())
    parser.add_argument("--end", default=END.isoformat())
    parser.add_argument("--limit-days", type=int, default=None, help="Bounded diagnostic/recovery batch; does not change panel.")
    args = parser.parse_args()
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    report = {"schema_version": 1, "provider": "Alpaca SIP", "feed": "sip", "panel": list(PANEL),
              "period": f"{start}..{end}", "regular_session_definition": "calendar 09:30-16:00 America/New_York; exact 391 bars assessed separately",
              "volume_used": False, "ranking_outcomes_evaluated": False, "live_execution": False, "days": []}
    selected = sessions(start, end)
    if args.limit_days is not None:
        selected = selected[:args.limit_days]
    for index, session in enumerate(selected, start=1):
        day = date.fromisoformat(session["date"])
        raw_path = RAW / f"year={day.year}" / f"month={day.month:02d}" / f"{day.isoformat()}.json"
        normalized_path = NORMALIZED / f"year={day.year}" / f"month={day.month:02d}" / f"{day.isoformat()}.parquet"
        if raw_path.exists():
            raw = json.loads(raw_path.read_text(encoding="utf-8"))
            digest = hashlib.sha256(raw_path.read_bytes()).hexdigest()
            action = "reused"
        else:
            start_utc, end_utc = ny_utc(day, session["open"]), ny_utc(day, session["close"])
            response = requests.get("https://data.alpaca.markets/v2/stocks/bars", headers=headers(), params={
                "symbols": ",".join(PANEL), "timeframe": "1Min", "start": start_utc, "end": end_utc, "feed": "sip", "limit": 10000}, timeout=90)
            response.raise_for_status()
            raw = {"schema_version": 1, "provider": "Alpaca SIP", "feed": "sip", "session_date": day.isoformat(),
                   "calendar": session, "request": {"symbols": list(PANEL), "timeframe": "1Min", "start": start_utc, "end": end_utc},
                   "received_at": datetime.now(timezone.utc).isoformat(), "response": response.json()}
            digest = atomic_json(raw_path, raw)
            action = "downloaded"
            time.sleep(0.32)
        count = normalize(raw, normalized_path)
        counts = {symbol: len(raw["response"].get("bars", {}).get(symbol, [])) for symbol in PANEL}
        report["days"].append({"date": day.isoformat(), "action": action, "raw_path": str(raw_path), "sha256": digest,
                               "normalized_path": str(normalized_path), "rows": count, "bar_counts": counts})
        if index % 25 == 0:
            print(f"{index}/{len(selected)} regular sessions processed", flush=True)
    report["created_at"] = datetime.now(timezone.utc).isoformat()
    report["regular_sessions_processed"] = len(report["days"])
    QUALITY.mkdir(parents=True, exist_ok=True)
    output = QUALITY / f"alpaca_holdout_c_ingest_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
