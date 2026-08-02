#!/usr/bin/env python3
"""Run the weekly, risk-capped low-capital bull-put strategy study.

The study uses point-in-time 10-21 DTE weekly put archives for AMZN, GOOGL,
and NVDA.  Call archives are supplied only because the shared dataset/audit
container expects both option sides; no call strategy is simulated here.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

CORE = Path(__file__).resolve().parents[1] / "core"
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

from capital_efficient_multi_stock_option_lab import (  # noqa: E402
    CREDIT_EXPIRY,
    CREDIT_PT50_SL2X,
    CREDIT_PT50_SL2X_DTE3,
    ArchivePaths,
    CapitalStrategySpec,
    LegTarget,
    run_study,
)


ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_ROOT = ROOT / "data" / "historical_options"
OUTPUT_ROOT = ARCHIVE_ROOT / "weekly_low_capital_option_lab"
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
                / f"alpaca_{ticker.lower()}_call_monthly_backfill"
                / "historical_options.sqlite"
            ),
        )
        for ticker in TICKERS
    )


def weekly_specs() -> tuple[CapitalStrategySpec, ...]:
    """Frozen family developed from the monthly bull-put lead.

    The always/expiry controls are intentionally sparse.  Managed variants use
    only prior daily prices, exact listed strike widths, and one 14-DTE target.
    """
    signals = (
        "momentum20_and_trend200",
        "shallow_pullback_momentum",
        "calm_uptrend",
    )
    short_targets = (0.94, 0.96)
    widths = (1.0, 2.5, 5.0)
    managed_rules = (
        CREDIT_EXPIRY,
        CREDIT_PT50_SL2X,
        CREDIT_PT50_SL2X_DTE3,
    )
    specs: list[CapitalStrategySpec] = []

    for short_target in short_targets:
        target_slug = int(round(short_target * 100))
        for width in widths:
            width_slug = str(width).replace(".", "p")
            specs.append(
                CapitalStrategySpec(
                    name=f"weekly14_bull_put_m{target_slug}_w{width_slug}_always_expiry",
                    family="bull_put_spread",
                    direction="bullish",
                    option_type="put",
                    signal="always",
                    legs=(
                        LegTarget(-1, short_target),
                        LegTarget(1, short_target),
                    ),
                    exit_rule=CREDIT_EXPIRY,
                    target_dte=14,
                    strike_offsets=(0.0, -width),
                )
            )
            for signal in signals:
                for rule in managed_rules:
                    specs.append(
                        CapitalStrategySpec(
                            name=(
                                f"weekly14_bull_put_m{target_slug}_w{width_slug}_"
                                f"{signal}_{rule.name}"
                            ),
                            family="bull_put_spread",
                            direction="bullish",
                            option_type="put",
                            signal=signal,
                            legs=(
                                LegTarget(-1, short_target),
                                LegTarget(1, short_target),
                            ),
                            exit_rule=rule,
                            target_dte=14,
                            strike_offsets=(0.0, -width),
                        )
                    )

    names = [spec.name for spec in specs]
    if len(names) != 60 or len(names) != len(set(names)):
        raise AssertionError("weekly protocol must contain 60 unique variants")
    return tuple(specs)


def main() -> int:
    specs = weekly_specs()
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
