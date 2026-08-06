#!/usr/bin/env python3
"""Stress-test the 2026 YTD screening winners and prior consensus leader.

Tests are deliberately stricter than the screening gate:
* 10/25/50 bps per-side slippage;
* leave-one-symbol-out concentration checks at 25 bps;
* monthly return consistency;
* deterministic five-session block bootstrap of total return.

This script creates governance evidence only. It cannot generate candidates,
promote strategies, or access broker/order endpoints.
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.research_platform.strategy_research_loop import (  # noqa: E402
    CanonicalPanel,
    StrategyCandidate,
    StrategyResearchPolicy,
    load_canonical_daily_panel,
    run_candidate_backtest,
)

REGISTRY = ROOT / "data" / "governance" / "research_registry.sqlite"
DATASET_ID = "ds_f20f2e15e7d1041ce6a1858d"
CACHE = ROOT / "data" / "cache" / "strategy_research_2026_ytd_robustness"
HOLDOUT_REPORT = ROOT / "data" / "governance" / "strategy_research_2026_ytd" / "latest_2026_ytd_locked_validation.json"
OUTPUT = ROOT / "data" / "governance" / "strategy_research_2026_ytd" / "latest_2026_ytd_robustness.json"
EVALUATION_START = "2026-01-02"
EVALUATION_END = "2026-08-04"
PRIOR_CONSENSUS_ID = "candidate_f77ecd3f538b4211298f59e1"


def candidate_from_dict(payload: dict[str, Any]) -> StrategyCandidate:
    return StrategyCandidate(
        family=str(payload["family"]),
        parameters=dict(payload["parameters"]),
        generation=int(payload.get("generation") or 0),
        parent_candidate_id=payload.get("parent_candidate_id"),
        hypothesis=str(payload.get("hypothesis") or "2026 YTD robustness candidate"),
        candidate_id=str(payload["candidate_id"]),
    )


def equity_daily_returns(output: Any) -> pd.Series:
    points = pd.Series(
        {pd.Timestamp(point.timestamp): float(point.equity) for point in output.equity_curve},
        dtype=float,
    ).sort_index()
    return points.pct_change().dropna()


def monthly_returns(daily_returns: pd.Series) -> dict[str, float]:
    if daily_returns.empty:
        return {}
    grouped = daily_returns.groupby(daily_returns.index.to_period("M"))
    return {
        str(period): float(((1.0 + values).prod() - 1.0) * 100.0)
        for period, values in grouped
    }


def block_bootstrap_total_return_ci(
    daily_returns: pd.Series,
    *,
    seed: int,
    block_size: int = 5,
    simulations: int = 2000,
) -> tuple[float | None, float | None]:
    values = daily_returns.to_numpy(dtype=float)
    values = values[np.isfinite(values)]
    if len(values) < max(20, block_size * 3):
        return None, None
    rng = np.random.default_rng(seed)
    starts = np.arange(max(1, len(values) - block_size + 1))
    totals = np.empty(simulations, dtype=float)
    blocks_needed = math.ceil(len(values) / block_size)
    for index in range(simulations):
        pieces = []
        for start in rng.choice(starts, size=blocks_needed, replace=True):
            pieces.append(values[int(start): int(start) + block_size])
        sampled = np.concatenate(pieces)[: len(values)]
        totals[index] = ((1.0 + sampled).prod() - 1.0) * 100.0
    return float(np.quantile(totals, 0.05)), float(np.quantile(totals, 0.95))


def backtest(panel: CanonicalPanel, candidate: StrategyCandidate, slippage: float) -> Any:
    return run_candidate_backtest(
        panel,
        candidate,
        StrategyResearchPolicy(
            batch_size=1,
            maximum_total_candidates=1,
            maximum_generation=0,
            maximum_adaptive_children_per_cycle=0,
            slippage_bps_per_side=slippage,
            minimum_sessions=100,
            minimum_trades=10,
            minimum_profit_factor=1.10,
            maximum_drawdown_pct=25.0,
            maximum_holm_adjusted_p_value=0.10,
            walk_forward_folds=3,
            random_seed=161803,
        ),
    )


def compact(result: Any) -> dict[str, Any]:
    metrics = dict(result.output.metrics)
    quality = dict(result.output.quality_checks)
    return {
        "total_return_pct": metrics.get("total_return_pct"),
        "annualized_return_pct": metrics.get("annualized_return_pct"),
        "maximum_drawdown_pct": metrics.get("maximum_drawdown_pct"),
        "profit_factor": metrics.get("profit_factor"),
        "trade_count": metrics.get("trade_count"),
        "win_rate": metrics.get("win_rate"),
        "sharpe_ratio": metrics.get("sharpe_ratio"),
        "best_trade_exclusion_passed": quality.get("best_trade_exclusion_passed"),
        "walk_forward_passed": quality.get("walk_forward_passed"),
        "benchmark_outperformance_passed": quality.get("benchmark_outperformance_passed"),
        "fold_returns_pct": list(result.fold_returns_pct),
    }


def main() -> int:
    holdout = json.loads(HOLDOUT_REPORT.read_text(encoding="utf-8"))
    candidate_rows = {
        str(row["candidate"]["candidate_id"]): row["candidate"]
        for row in holdout["results"]
        if row.get("candidate")
    }
    selected_ids = [
        str(row["candidate"]["candidate_id"])
        for row in holdout["results"]
        if row.get("verdict") == "PASS"
    ]
    if PRIOR_CONSENSUS_ID not in selected_ids:
        selected_ids.append(PRIOR_CONSENSUS_ID)
    candidates = [candidate_from_dict(candidate_rows[candidate_id]) for candidate_id in selected_ids]

    loaded = load_canonical_daily_panel(REGISTRY, DATASET_ID, cache_root=CACHE)
    panel = CanonicalPanel(
        dataset_id=loaded.dataset_id,
        dataset_name=loaded.dataset_name,
        frame=loaded.frame,
        raw_object_count=loaded.raw_object_count,
        source_paths=loaded.source_paths,
        lineage_hash=loaded.lineage_hash,
        research_role="2026_ytd_robustness_stress_only",
        evaluation_start=EVALUATION_START,
        evaluation_end=EVALUATION_END,
    )
    symbols = sorted(panel.frame["ticker"].unique().tolist())
    records = []
    for candidate in candidates:
        slippage_results = {}
        baseline_result = None
        for slippage in (10.0, 25.0, 50.0):
            result = backtest(panel, candidate, slippage)
            slippage_results[str(int(slippage))] = compact(result)
            if slippage == 10.0:
                baseline_result = result
        assert baseline_result is not None

        leave_one_out = []
        for symbol in symbols:
            reduced_frame = panel.frame[panel.frame["ticker"] != symbol].copy()
            reduced_panel = CanonicalPanel(
                dataset_id=panel.dataset_id,
                dataset_name=panel.dataset_name,
                frame=reduced_frame,
                raw_object_count=panel.raw_object_count,
                source_paths=panel.source_paths,
                lineage_hash=panel.lineage_hash,
                research_role=panel.research_role,
                evaluation_start=panel.evaluation_start,
                evaluation_end=panel.evaluation_end,
            )
            result = backtest(reduced_panel, candidate, 25.0)
            leave_one_out.append(
                {
                    "excluded_symbol": symbol,
                    "total_return_pct": result.output.metrics.get("total_return_pct"),
                    "maximum_drawdown_pct": result.output.metrics.get("maximum_drawdown_pct"),
                    "profit_factor": result.output.metrics.get("profit_factor"),
                    "trade_count": result.output.metrics.get("trade_count"),
                }
            )

        daily = equity_daily_returns(baseline_result.output)
        month_returns = monthly_returns(daily)
        bootstrap_low, bootstrap_high = block_bootstrap_total_return_ci(
            daily,
            seed=161803 + int(candidate.candidate_id[-6:], 16) % 100_000,
        )
        loo_returns = [float(row["total_return_pct"] or 0.0) for row in leave_one_out]
        positive_months = sum(value > 0.0 for value in month_returns.values())
        month_count = len(month_returns)
        cost_50 = slippage_results["50"]
        cost_stress_passed = bool(
            float(cost_50["total_return_pct"] or 0.0) > 0.0
            and float(cost_50["profit_factor"] or 0.0) >= 1.10
            and int(cost_50["trade_count"] or 0) >= 10
            and float(cost_50["maximum_drawdown_pct"] or 999.0) <= 25.0
            and cost_50["best_trade_exclusion_passed"] is True
        )
        symbol_concentration_passed = bool(
            loo_returns
            and min(loo_returns) > 0.0
            and sum(value > 0.0 for value in loo_returns) / len(loo_returns) >= 0.90
        )
        monthly_consistency_passed = bool(
            month_count >= 5
            and positive_months / month_count >= 0.60
            and min(month_returns.values(), default=-999.0) > -5.0
        )
        bootstrap_passed = bool(bootstrap_low is not None and bootstrap_low > 0.0)
        robust = bool(
            cost_stress_passed
            and symbol_concentration_passed
            and monthly_consistency_passed
            and bootstrap_passed
        )
        records.append(
            {
                "candidate": candidate.to_dict(),
                "screening_pass_2026_ytd": candidate.candidate_id != PRIOR_CONSENSUS_ID,
                "slippage_stress_bps_per_side": slippage_results,
                "leave_one_symbol_out_25bps": {
                    "tests": len(leave_one_out),
                    "positive_fraction": sum(value > 0.0 for value in loo_returns) / len(loo_returns),
                    "worst_total_return_pct": min(loo_returns),
                    "best_total_return_pct": max(loo_returns),
                    "results": leave_one_out,
                },
                "monthly_returns_pct": month_returns,
                "positive_month_fraction": positive_months / month_count if month_count else None,
                "block_bootstrap_total_return_ci_90_pct": [bootstrap_low, bootstrap_high],
                "gates": {
                    "cost_stress_50bps_passed": cost_stress_passed,
                    "leave_one_symbol_out_passed": symbol_concentration_passed,
                    "monthly_consistency_passed": monthly_consistency_passed,
                    "bootstrap_lower_bound_positive": bootstrap_passed,
                },
                "robust_2026_ytd": robust,
                "automatic_promotion": False,
                "execution_authority": False,
            }
        )

    records.sort(
        key=lambda row: (
            row["robust_2026_ytd"],
            sum(bool(value) for value in row["gates"].values()),
            float(row["slippage_stress_bps_per_side"]["50"]["total_return_pct"] or -999.0),
        ),
        reverse=True,
    )
    payload = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "completed",
        "dataset_id": DATASET_ID,
        "evaluation_start": EVALUATION_START,
        "evaluation_end": EVALUATION_END,
        "candidate_count": len(records),
        "robust_candidate_count": sum(row["robust_2026_ytd"] for row in records),
        "records": records,
        "automatic_promotion": False,
        "paper_or_live_execution": False,
        "execution_authority": False,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": payload["status"],
                "candidate_count": payload["candidate_count"],
                "robust_candidate_count": payload["robust_candidate_count"],
                "ranking": [
                    {
                        "candidate_id": row["candidate"]["candidate_id"],
                        "family": row["candidate"]["family"],
                        "gates_passed": sum(bool(value) for value in row["gates"].values()),
                        "robust": row["robust_2026_ytd"],
                        "return_50bps": row["slippage_stress_bps_per_side"]["50"]["total_return_pct"],
                        "worst_leave_one_out_return": row["leave_one_symbol_out_25bps"]["worst_total_return_pct"],
                        "bootstrap_ci": row["block_bootstrap_total_return_ci_90_pct"],
                    }
                    for row in records
                ],
                "output": str(OUTPUT),
                "automatic_promotion": False,
                "execution_authority": False,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
