#!/usr/bin/env python3
"""Run the weekly, risk-capped bullish debit options study.

The protocol uses point-in-time 10-21 DTE weekly call archives for AMZN,
GOOGL, and NVDA.  It evaluates long calls, bull-call verticals, and call
butterflies with prior-price-only bullish setup filters.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

CORE = Path(__file__).resolve().parents[1] / "core"
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

from capital_efficient_multi_stock_option_lab import (  # noqa: E402
    DEBIT_EXPIRY,
    DEBIT_PT50_SL50,
    DEBIT_PT50_SL50_DTE3,
    ArchivePaths,
    CapitalStrategySpec,
    LegTarget,
    run_study,
)


ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_ROOT = ROOT / "data" / "historical_options"
OUTPUT_ROOT = ARCHIVE_ROOT / "weekly_bullish_debit_option_lab"
TICKERS = ("AMZN", "GOOGL", "NVDA")
BUDGETS = (250.0, 500.0, 1_000.0, 2_000.0)
MAXIMUM_TRADE_RISK_FRACTION = 0.25


def weekly_archive_paths() -> tuple[ArchivePaths, ...]:
    return tuple(
        ArchivePaths(
            ticker=ticker,
            put_database=(
                ARCHIVE_ROOT
                / f"alpaca_{ticker.lower()}_weekly_14d_put"
                / "historical_options.sqlite"
            ),
            call_database=(
                ARCHIVE_ROOT
                / f"alpaca_{ticker.lower()}_weekly_14d_call"
                / "historical_options.sqlite"
            ),
        )
        for ticker in TICKERS
    )


def weekly_bullish_specs() -> tuple[CapitalStrategySpec, ...]:
    signals = (
        "momentum20_and_trend200",
        "shallow_pullback_momentum",
        "calm_uptrend",
    )
    debit_rules = (
        DEBIT_EXPIRY,
        DEBIT_PT50_SL50,
        DEBIT_PT50_SL50_DTE3,
    )
    butterfly_rules = (
        DEBIT_EXPIRY,
        DEBIT_PT50_SL50_DTE3,
    )
    widths = (1.0, 2.5, 5.0)
    specs: list[CapitalStrategySpec] = []

    for long_target in (1.00, 1.02):
        target_slug = int(round(long_target * 100))
        for width in widths:
            width_slug = str(width).replace(".", "p")
            for signal in signals:
                for rule in debit_rules:
                    specs.append(
                        CapitalStrategySpec(
                            name=(
                                f"weekly14_bull_call_m{target_slug}_w{width_slug}_"
                                f"{signal}_{rule.name}"
                            ),
                            family="bull_call_spread",
                            direction="bullish",
                            option_type="call",
                            signal=signal,
                            legs=(
                                LegTarget(1, long_target),
                                LegTarget(-1, long_target),
                            ),
                            exit_rule=rule,
                            target_dte=14,
                            strike_offsets=(0.0, width),
                        )
                    )

    for width in widths:
        width_slug = str(width).replace(".", "p")
        for signal in signals:
            for rule in butterfly_rules:
                specs.append(
                    CapitalStrategySpec(
                        name=(
                            f"weekly14_call_bfly_m98_w{width_slug}_"
                            f"{signal}_{rule.name}"
                        ),
                        family="call_butterfly",
                        direction="bullish",
                        option_type="call",
                        signal=signal,
                        legs=(
                            LegTarget(1, 0.98),
                            LegTarget(-2, 0.98),
                            LegTarget(1, 0.98),
                        ),
                        exit_rule=rule,
                        target_dte=14,
                        strike_offsets=(0.0, width, 2.0 * width),
                    )
                )

    for long_target in (1.00, 1.02):
        target_slug = int(round(long_target * 100))
        for signal in signals:
            for rule in debit_rules:
                specs.append(
                    CapitalStrategySpec(
                        name=f"weekly14_long_call_m{target_slug}_{signal}_{rule.name}",
                        family="long_call",
                        direction="bullish",
                        option_type="call",
                        signal=signal,
                        legs=(LegTarget(1, long_target),),
                        exit_rule=rule,
                        target_dte=14,
                    )
                )

    names = [spec.name for spec in specs]
    if len(names) != 90 or len(names) != len(set(names)):
        raise AssertionError("weekly bullish protocol must contain 90 unique variants")
    return tuple(specs)


def main() -> int:
    specs = weekly_bullish_specs()
    report = run_study(
        weekly_archive_paths(),
        output_root=OUTPUT_ROOT,
        budgets=BUDGETS,
        specs=specs,
        maximum_trade_risk_fraction=MAXIMUM_TRADE_RISK_FRACTION,
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "promoted_count": len(report.get("promoted", [])),
                "strategy_count": report["protocol"]["strategy_count"],
                "maximum_trade_risk_fraction": report["protocol"][
                    "position_rules"
                ]["maximum_trade_risk_fraction"],
                "output_root": str(OUTPUT_ROOT),
                "top_by_budget": {
                    budget: rows[:10]
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
