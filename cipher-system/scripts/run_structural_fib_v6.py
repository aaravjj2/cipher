#!/usr/bin/env python3
"""Backtest the Pine V6 normal baseline on the local one-minute stock corpus."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from core import structural_fib_v6 as v6  # noqa: E402
from core.structural_fib_bars import load_minute_bars, resample_5m  # noqa: E402

DEFAULT_DB = Path(
    "/home/aarav/Aarav/cipher/EOD strategy/data/historical_equities/"
    "obsidian_pine_ytd_2026/equity_bars.sqlite"
)
DEFAULT_OUT = Path("/home/aarav/Aarav/cipher/runtime/data/structural_fib_v6/backtest.json")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--symbols", default="NVDA,AAPL")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args(argv)
    if not args.db.is_file():
        parser.error(f"bar database not found: {args.db}")
    symbols = [x.strip().upper() for x in args.symbols.split(",") if x.strip()]
    results = []
    for symbol in symbols:
        bars = resample_5m(load_minute_bars(args.db, symbol))
        result = v6.run_symbol(symbol, bars)
        results.append(result)
        print(f"{symbol}: {len(bars)} 5m bars, {len(result['signals'])} signals, {len(result['trades'])} trades")
    report = v6.report(results)
    stat = args.db.stat()
    report["data_source"] = {
        "path": str(args.db),
        "bytes": stat.st_size,
        "modified_ns": stat.st_mtime_ns,
        "bar_input": "1Min resampled to exchange-local 5Min",
    }
    print(json.dumps({
        "study": report["study"], "coverage": report["coverage"],
        "overall": report["overall"], "by_setup": report["by_setup"],
        "by_direction": report["by_direction"], "by_pm_source": report["by_pm_source"],
        "temporal_split": report["temporal_split"],
        "signals": report["signals"], "trades": report["trades"],
        "net_equity_return_pct_serialized": report["net_equity_return_pct_serialized"],
        "max_drawdown_pct_serialized": report["max_drawdown_pct_serialized"],
    }, indent=2))
    if not args.no_write:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
