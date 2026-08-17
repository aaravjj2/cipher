#!/usr/bin/env python3
"""Test Structural Fib V6 signals against locally captured option data."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.structural_fib_v6_options import historical_trade_bar_test, tradier_nbbo_test  # noqa: E402

WORKSPACE = ROOT.parents[1]
DEFAULT_SIGNALS = WORKSPACE / "runtime/data/structural_fib_v6/backtest_nvda_aapl.json"
DEFAULT_STREAM = WORKSPACE / "runtime/data/tradier_stream.sqlite"
DEFAULT_OUT = WORKSPACE / "runtime/data/structural_fib_v6/options_captured_test.json"
DEFAULT_ARCHIVES = {
    ("NVDA", "call"): WORKSPACE / "runtime/data/historical_options/alpaca_nvda_weekly_14d_call/historical_options.sqlite",
    ("NVDA", "put"): WORKSPACE / "runtime/data/historical_options/alpaca_nvda_weekly_14d_put/historical_options.sqlite",
    ("AAPL", "call"): WORKSPACE / "runtime/data/historical_options/earnings_defined_risk_aapl_condor/historical_options.sqlite",
    ("AAPL", "put"): WORKSPACE / "runtime/data/historical_options/earnings_defined_risk_aapl_condor/historical_options.sqlite",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--signals", type=Path, default=DEFAULT_SIGNALS)
    parser.add_argument("--stream-db", type=Path, default=DEFAULT_STREAM)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args(argv)
    if not args.signals.is_file():
        parser.error(f"signal artifact not found: {args.signals}")
    payload = json.loads(args.signals.read_text(encoding="utf-8"))
    trades = payload.get("trade_records", [])
    historical = historical_trade_bar_test(trades, DEFAULT_ARCHIVES)
    nbbo = tradier_nbbo_test(trades, args.stream_db) if args.stream_db.is_file() else {
        "study": "structural_fib_v6_captured_tradier_nbbo", "error": "stream database missing"
    }
    report = {
        "study": "structural_fib_v6_captured_options",
        "source_underlying_artifact": str(args.signals),
        "source_underlying_trades": len(trades),
        "historical_trade_bars": historical,
        "tradier_nbbo": nbbo,
        "interpretation_rule": "Do not pool the historical trade-bar proxy with the executable NBBO overlap.",
    }
    print(json.dumps({
        "historical_trade_bars": {
            "eligible": historical["eligible_underlying_trades"],
            "mapped": historical["mapped_trades"],
            "coverage_rate": historical["coverage_rate"],
            "protocols": historical["protocols"],
            "skips": historical["skips"],
        },
        "tradier_nbbo": {
            "mapped": nbbo.get("mapped_trades"), "overall": nbbo.get("overall"),
            "by_symbol": nbbo.get("by_symbol"), "skips": nbbo.get("skips"),
        },
    }, indent=2))
    if not args.no_write:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
