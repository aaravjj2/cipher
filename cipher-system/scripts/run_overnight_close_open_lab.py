#!/usr/bin/env python3
"""Run or validate the close-to-next-open research artifacts."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

SYSTEM = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SYSTEM))

from core.overnight_close_open_lab import DEFAULT_OUTPUT, run  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check-existing", action="store_true")
    args = parser.parse_args()
    if args.check_existing:
        report_path = args.output / "report.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        required = ("report.md", "stock_trades.csv", "stock_rankings.csv", "stock_period_rankings.csv", "stock_cost_sensitivity.csv", "option_trades.csv", "option_rankings.csv", "option_friction_sensitivity.csv")
        if not report["stock_rankings"] or not report["option_dataset_inventory"] or any(not (args.output / name).exists() for name in required):
            raise SystemExit("overnight close/open artifacts incomplete")
        print("overnight-close-open-ok")
        return 0
    report = run(args.output)
    print(json.dumps({
        "output": str(args.output),
        "stock_tickers": len(report["stock_rankings"]),
        "option_groups": len(report["option_rankings"]),
        "option_datasets": len(report["option_dataset_inventory"]),
        "option_underlyings": report["availability"]["option_underlyings_tested"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
