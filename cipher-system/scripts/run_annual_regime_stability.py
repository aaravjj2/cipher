#!/usr/bin/env python3
"""Calendar-year stability analysis across the continuous 2016-2026 panel.

The candidate set is the union of strategies that passed at least one governed
research period. Each candidate is re-run independently for every calendar year
using earlier bars only as indicator warmup. Annual metrics use 25 bps per side;
full-period cost stress also runs at 25 and 50 bps.

This is diagnostic robustness evidence, not a new holdout and not promotion.
"""
from __future__ import annotations

import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.research_platform.hashing import stable_id  # noqa: E402
from core.research_platform.strategy_research_loop import (  # noqa: E402
    CanonicalPanel,
    StrategyCandidate,
    StrategyResearchPolicy,
    load_canonical_daily_panel,
    run_candidate_backtest,
)

REGISTRY = ROOT / "data" / "governance" / "research_registry.sqlite"
CACHE = ROOT / "data" / "cache" / "annual_regime_stability"
MATRIX = ROOT / "data" / "governance" / "cross_period_strategy_matrix.json"
OUTPUT = ROOT / "data" / "governance" / "annual_regime_stability.json"
DATASETS = (
    "ds_fb1e8d9aeb51f12407b08123",
    "ds_532bf7c42462c24a7c1a0a1f",
    "ds_3e9b83d533c645ea23e1abf8",
    "ds_f20f2e15e7d1041ce6a1858d",
)


def policy(slippage: float) -> StrategyResearchPolicy:
    return StrategyResearchPolicy(
        batch_size=1,
        maximum_total_candidates=1,
        maximum_generation=0,
        maximum_adaptive_children_per_cycle=0,
        slippage_bps_per_side=slippage,
        minimum_sessions=100,
        minimum_trades=5,
        minimum_profit_factor=1.10,
        maximum_drawdown_pct=25.0,
        maximum_holm_adjusted_p_value=0.10,
        walk_forward_folds=3,
        random_seed=141421,
    )


def compact(result: Any) -> dict[str, Any]:
    metrics = dict(result.output.metrics)
    benchmark = dict(result.output.benchmark_metrics)
    quality = dict(result.output.quality_checks)
    trade_count = int(metrics.get("trade_count") or 0)
    diagnostic_gate = bool(
        float(metrics.get("total_return_pct") or 0.0) > 0.0
        and float(metrics.get("profit_factor") or 0.0) >= 1.10
        and trade_count >= 5
        and float(metrics.get("maximum_drawdown_pct") or 999.0) <= 25.0
        and quality.get("best_trade_exclusion_passed") is True
        and quality.get("walk_forward_passed") is True
    )
    return {
        "total_return_pct": metrics.get("total_return_pct"),
        "annualized_return_pct": metrics.get("annualized_return_pct"),
        "maximum_drawdown_pct": metrics.get("maximum_drawdown_pct"),
        "profit_factor": metrics.get("profit_factor"),
        "trade_count": trade_count,
        "win_rate": metrics.get("win_rate"),
        "sharpe_ratio": metrics.get("sharpe_ratio"),
        "benchmark_return_pct": benchmark.get("total_return_pct"),
        "strategy_excess_return_pct": benchmark.get("strategy_excess_return_pct"),
        "best_trade_exclusion_passed": quality.get("best_trade_exclusion_passed"),
        "walk_forward_passed": quality.get("walk_forward_passed"),
        "benchmark_outperformance_passed": quality.get("benchmark_outperformance_passed"),
        "fold_returns_pct": list(result.fold_returns_pct),
        "annual_diagnostic_gate": diagnostic_gate,
    }


def load_continuous_panel() -> CanonicalPanel:
    loaded = [load_canonical_daily_panel(REGISTRY, dataset_id, cache_root=CACHE / dataset_id) for dataset_id in DATASETS]
    frame = pd.concat([item.frame for item in loaded], ignore_index=True)
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame.sort_values(["date", "ticker"]).drop_duplicates(["date", "ticker"], keep="last")
    return CanonicalPanel(
        dataset_id="derived_continuous_broad_2016_2026",
        dataset_name="derived_continuous_broad_2016_2026",
        frame=frame.reset_index(drop=True),
        raw_object_count=sum(item.raw_object_count for item in loaded),
        source_paths=tuple(path for item in loaded for path in item.source_paths),
        lineage_hash=stable_id("continuous_panel_lineage", [item.lineage_hash for item in loaded], length=64),
        research_role="diagnostic_annual_regime_stability_not_independent_holdout",
    )


def main() -> int:
    matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
    priority_periods = {"locked_2016_2019", "original_2023_2025", "locked_2026_ytd"}
    candidate_rows = [
        row
        for row in matrix["matrix"]
        if row["pass_count"] >= 2 or priority_periods.intersection(row["passed_periods"])
    ]
    panel = load_continuous_panel()
    years = list(range(2016, 2027))
    records = []
    for row in candidate_rows:
        candidate = StrategyCandidate(
            family=str(row["family"]),
            parameters=dict(row["parameters"]),
            hypothesis="Calendar-year stability diagnostic for a prior period-screening winner.",
            candidate_id=str(row["candidate_id"]),
            parent_candidate_id=row.get("parent_candidate_id"),
        )
        annual: dict[str, Any] = {}
        for year in years:
            end = "2026-08-04" if year == 2026 else f"{year}-12-31"
            annual_panel = CanonicalPanel(
                dataset_id=panel.dataset_id,
                dataset_name=panel.dataset_name,
                frame=panel.frame,
                raw_object_count=panel.raw_object_count,
                source_paths=panel.source_paths,
                lineage_hash=panel.lineage_hash,
                research_role=panel.research_role,
                evaluation_start=f"{year}-01-01",
                evaluation_end=end,
            )
            annual[str(year)] = compact(run_candidate_backtest(annual_panel, candidate, policy(25.0)))

        full_stress = {}
        for slippage in (25.0, 50.0):
            full_panel = CanonicalPanel(
                dataset_id=panel.dataset_id,
                dataset_name=panel.dataset_name,
                frame=panel.frame,
                raw_object_count=panel.raw_object_count,
                source_paths=panel.source_paths,
                lineage_hash=panel.lineage_hash,
                research_role=panel.research_role,
                evaluation_start="2016-01-01",
                evaluation_end="2026-08-04",
            )
            full_stress[str(int(slippage))] = compact(run_candidate_backtest(full_panel, candidate, policy(slippage)))

        returns = [float(value["total_return_pct"] or 0.0) for value in annual.values()]
        positive_years = sum(value > 0.0 for value in returns)
        outperform_years = sum(bool(value["benchmark_outperformance_passed"]) for value in annual.values())
        gate_years = sum(bool(value["annual_diagnostic_gate"]) for value in annual.values())
        active_years = sum(int(value["trade_count"] or 0) >= 5 for value in annual.values())
        stability = bool(
            positive_years >= 8
            and outperform_years >= 6
            and gate_years >= 6
            and min(returns) > -15.0
            and float(full_stress["50"]["total_return_pct"] or 0.0) > 0.0
            and full_stress["50"]["best_trade_exclusion_passed"] is True
        )
        records.append(
            {
                "candidate": candidate.to_dict(),
                "previous_passed_periods": row["passed_periods"],
                "annual_25bps": annual,
                "full_period_cost_stress": full_stress,
                "summary": {
                    "years": len(years),
                    "active_years": active_years,
                    "positive_years": positive_years,
                    "benchmark_outperformance_years": outperform_years,
                    "annual_diagnostic_gate_years": gate_years,
                    "median_annual_return_pct": statistics.median(returns),
                    "worst_annual_return_pct": min(returns),
                    "best_annual_return_pct": max(returns),
                    "stable_across_calendar_years": stability,
                },
                "automatic_promotion": False,
                "execution_authority": False,
            }
        )

    records.sort(
        key=lambda item: (
            item["summary"]["stable_across_calendar_years"],
            item["summary"]["annual_diagnostic_gate_years"],
            item["summary"]["positive_years"],
            item["summary"]["median_annual_return_pct"],
        ),
        reverse=True,
    )
    family_summary: dict[str, dict[str, Any]] = {}
    for row in records:
        family = row["candidate"]["family"]
        bucket = family_summary.setdefault(family, {"candidates": 0, "stable": 0, "gate_years": [], "positive_years": []})
        bucket["candidates"] += 1
        bucket["stable"] += int(row["summary"]["stable_across_calendar_years"])
        bucket["gate_years"].append(row["summary"]["annual_diagnostic_gate_years"])
        bucket["positive_years"].append(row["summary"]["positive_years"])
    for bucket in family_summary.values():
        bucket["median_gate_years"] = statistics.median(bucket.pop("gate_years"))
        bucket["median_positive_years"] = statistics.median(bucket.pop("positive_years"))

    payload = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "completed",
        "period": "2016-01-01 through 2026-08-04",
        "calendar_years": years,
        "candidate_selection": "union of locked-2016-2019, original-2023-2025, and locked-2026-YTD winners plus every multi-period winner",
        "candidate_count": len(records),
        "stable_candidate_count": sum(row["summary"]["stable_across_calendar_years"] for row in records),
        "family_summary": family_summary,
        "ranking": records,
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
                "stable_candidate_count": payload["stable_candidate_count"],
                "top": [
                    {
                        "candidate_id": row["candidate"]["candidate_id"],
                        "family": row["candidate"]["family"],
                        **row["summary"],
                        "full_return_50bps": row["full_period_cost_stress"]["50"]["total_return_pct"],
                    }
                    for row in records[:10]
                ],
                "family_summary": payload["family_summary"],
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
