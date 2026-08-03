#!/usr/bin/env python3
"""Compare one LSE reference-volume pilot with matching Alpaca minute bars."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
NY = ZoneInfo("America/New_York")
THRESHOLD = 0.05


def regular_alpaca(path: Path, date_text: str) -> pd.DataFrame:
    frame = pd.read_parquet(path, columns=["timestamp", "ticker", "volume"])
    stamp = pd.to_datetime(frame["timestamp"], utc=True).dt.tz_convert(NY)
    mask = (stamp.dt.date.astype(str) == date_text) & (stamp.dt.time >= time(9, 30)) & (stamp.dt.time < time(16))
    return frame.loc[mask].copy()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="2023-06-01")
    args = parser.parse_args()
    manifest_path = ROOT / "data" / "reference_volume" / f"lse_reference_volume_pilot_{args.date}.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    alpaca_path = ROOT / "data" / "normalized" / "alpaca_sip_holdout_c_1m" / f"year={args.date[:4]}" / f"month={args.date[5:7]}" / f"{args.date}.parquet"
    alpaca = regular_alpaca(alpaca_path, args.date)
    results = []
    for reference in manifest["records"]:
        symbol = reference["symbol"]
        observed = alpaca.loc[alpaca["ticker"] == symbol]
        observed_volume = float(observed["volume"].sum())
        reference_volume = float(reference["regular_volume"])
        difference = abs(observed_volume - reference_volume) / reference_volume if reference_volume > 0 else None
        results.append(
            {
                "symbol": symbol,
                "alpaca_bars": int(len(observed)),
                "lse_bars": int(reference["regular_rows"]),
                "alpaca_volume": observed_volume,
                "lse_volume": reference_volume,
                "relative_difference": difference,
                "passes_5_percent": difference is not None and difference <= THRESHOLD,
            }
        )
    output = ROOT / "data" / "market_quality" / f"lse_alpaca_volume_pilot_{args.date}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "date": args.date,
        "session_comparison": "09:30 <= America/New_York timestamp < 16:00; 390 minute intervals",
        "reference_source": "London Strategic Edge minute candles",
        "observed_source": "Alpaca SIP minute bars",
        "threshold": THRESHOLD,
        "results": results,
        "pass_count": sum(item["passes_5_percent"] for item in results),
        "total_count": len(results),
        "status": "rejected_pilot_material_mismatch" if any(not item["passes_5_percent"] for item in results) else "pilot_passes_pending_broader_validation",
        "full_gate_changed": False,
        "trading_or_signal_evaluation": False,
    }
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
