#!/usr/bin/env python3
"""Run the fixed-dollar-width low-capital options study."""
from __future__ import annotations

import json
import sys
from pathlib import Path

CORE = Path(__file__).resolve().parents[1] / "core"
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

from capital_efficient_multi_stock_option_lab import (  # noqa: E402
    DEFAULT_ARCHIVE_ROOT,
    DEFAULT_BUDGETS,
    DEFAULT_TICKERS,
    fixed_width_strategy_specs,
    resolve_archive_paths,
    run_study,
)


def main() -> int:
    output = (
        Path(__file__).resolve().parents[1]
        / "data"
        / "historical_options"
        / "fixed_width_multi_stock_lab"
    )
    bullish_signals = {"momentum20_positive", "mild_pullback_uptrend", "ema50_reclaim"}
    bearish_signals = {"momentum20_negative", "ema50_breakdown"}
    debit_exits = {"expiry", "pt50_sl50", "dte7"}
    credit_exits = {"expiry", "pt50"}
    specs = tuple(
        spec
        for spec in fixed_width_strategy_specs()
        if spec.strike_offsets is not None
        and abs(spec.strike_offsets[1]) in {1.0, 2.5, 5.0}
        and (
            (spec.direction == "bullish" and spec.signal in bullish_signals)
            or (spec.direction == "bearish" and spec.signal in bearish_signals)
        )
        and spec.exit_rule.name
        in (credit_exits if spec.is_credit else debit_exits)
    )
    report = run_study(
        resolve_archive_paths(DEFAULT_ARCHIVE_ROOT, DEFAULT_TICKERS),
        output_root=output,
        budgets=DEFAULT_BUDGETS,
        specs=specs,
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "promoted_count": len(report.get("promoted", [])),
                "strategy_count": report["protocol"]["strategy_count"],
                "output_root": str(output),
                "top_by_budget": {
                    budget: rows[:5]
                    for budget, rows in report.get("top_by_budget", {}).items()
                },
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
