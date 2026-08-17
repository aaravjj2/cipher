#!/usr/bin/env python3
"""Run the supplied MU and QQQ indicators on captured stock and option data."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parents[1]
sys.path.insert(0, str(ROOT))

from core.obsidian_signal_studies import (  # noqa: E402
    mu_premarket_study, qqq_wave_study, tradier_horizon_option_test,
)
from core.structural_fib_bars import load_minute_bars  # noqa: E402

DEFAULT_STOCK_DB = WORKSPACE / "EOD strategy/data/historical_equities/obsidian_pine_ytd_2026/equity_bars.sqlite"
DEFAULT_STREAM_DB = WORKSPACE / "runtime/data/tradier_stream.sqlite"
DEFAULT_OUT = WORKSPACE / "runtime/data/obsidian_indicator_studies/mu_qqq_captured_test.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stock-db", type=Path, default=DEFAULT_STOCK_DB)
    parser.add_argument("--stream-db", type=Path, default=DEFAULT_STREAM_DB)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args(argv)
    for path in (args.stock_db, args.stream_db):
        if not path.is_file(): parser.error(f"captured database missing: {path}")
    mu = mu_premarket_study(load_minute_bars(args.stock_db, "MU"))
    qqq = qqq_wave_study(load_minute_bars(args.stock_db, "QQQ"))
    mu_options = tradier_horizon_option_test(mu["signal_records"], args.stream_db)
    qqq_options = tradier_horizon_option_test(qqq["signal_records"], args.stream_db)
    report = {
        "study": "supplied_obsidian_indicators_captured_data",
        "stock_source": str(args.stock_db), "option_source": str(args.stream_db),
        "mu": {**mu, "captured_options": mu_options},
        "qqq": {**qqq, "captured_options": qqq_options},
        "execution_boundary": "research-only; no broker orders",
    }
    concise = {
        "mu": {k: mu[k] for k in ("signals", "by_setup", "underlying_followthrough")},
        "mu_options": mu_options["by_horizon"],
        "qqq": {k: qqq[k] for k in ("early", "validated", "underlying_early_followthrough")},
        "qqq_options": qqq_options["by_horizon"],
        "option_skips": {"MU": mu_options["skips"], "QQQ": qqq_options["skips"]},
    }
    print(json.dumps(concise, indent=2))
    if not args.no_write:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
