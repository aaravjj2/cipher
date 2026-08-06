#!/usr/bin/env python3
"""Run fixed-component, trailing-only regime-allocation research.

The component list and allocator grid are explicit.  Dynamic weights use only
component returns strictly before each rebalance date.  Component returns
already include underlying next-open execution slippage; allocator switching
cost is charged separately.  Results are exploratory because the component set
was informed by earlier research periods.
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

from core.research_platform.hashing import stable_id  # noqa: E402
from core.research_platform.regime_allocator import (  # noqa: E402
    AllocatorSpec,
    annualized_return,
    apply_allocator,
    best_month_exclusion_positive,
    block_sign_flip_p_value,
    build_allocator_weights,
    component_selection_frequency,
    default_allocator_specs,
    equity_curve_to_returns,
    fold_metrics,
    holm_adjust,
    maximum_drawdown,
    profit_factor,
    sharpe,
    total_return,
)
from core.research_platform.strategy_research_loop import (  # noqa: E402
    CanonicalPanel,
    StrategyCandidate,
    StrategyResearchPolicy,
    load_canonical_daily_panel,
    run_candidate_backtest,
)

REGISTRY = ROOT / "data" / "governance" / "research_registry.sqlite"
CACHE = ROOT / "data" / "cache" / "regime_allocator"
OUTPUT = ROOT / "data" / "governance" / "regime_allocator_research.json"
MATRIX = ROOT / "data" / "governance" / "cross_period_strategy_matrix.json"
DATASET_IDS = (
    "ds_fb1e8d9aeb51f12407b08123",
    "ds_532bf7c42462c24a7c1a0a1f",
    "ds_3e9b83d533c645ea23e1abf8",
    "ds_f20f2e15e7d1041ce6a1858d",
)
PASSIVE_COMPONENT_ID = "passive_universe_baseline"
COMPONENT_IDS = (
    "candidate_f77ecd3f538b4211298f59e1",  # slow trend / breakout ensemble
    "candidate_a1dd25c6c1804b1ca67a40be",  # SMA 50/200
    "candidate_a19fdcaef2510d901d9f4335",  # RSI(2) mean reversion
    "candidate_7d8580584de158f76e339984",  # Bollinger mean reversion
    "candidate_b7c98046a81d6600c3d21c29",  # cross-sectional five-day reversal
    "candidate_fc4e54dbae61339b495f6aff",  # regime switch
    "candidate_8c26c571bd13a677e07962ba",  # Donchian breakout
    "candidate_c892a9a7a2f3831f2fc0da40",  # Keltner breakout
)
ETF_SYMBOLS = frozenset(
    {
        "SPY", "QQQ", "IWM", "DIA", "XLK", "XLF", "XLE", "XLV", "XLY", "XLP", "XLI", "XLU", "XLB", "XLRE",
        "SMH", "SOXX", "TLT", "IEF", "HYG", "LQD", "GLD", "SLV", "USO", "VNQ", "EEM", "EFA",
    }
)
FOLDS = {
    "2018_2019": ("2018-01-02", "2019-12-31"),
    "2020_2022": ("2020-01-02", "2022-12-30"),
    "2023_2025": ("2023-01-03", "2025-12-31"),
    "2026_ytd": ("2026-01-02", "2026-08-04"),
}
EVALUATION_START = pd.Timestamp("2018-01-02")
EVALUATION_END = pd.Timestamp("2026-08-04")


def load_component_candidates() -> tuple[StrategyCandidate, ...]:
    payload = json.loads(MATRIX.read_text(encoding="utf-8"))
    by_id = {row["candidate_id"]: row for row in payload["matrix"]}
    candidates: list[StrategyCandidate] = []
    for candidate_id in COMPONENT_IDS:
        row = by_id.get(candidate_id)
        if not row:
            raise RuntimeError(f"component candidate missing from cross-period matrix: {candidate_id}")
        candidates.append(
            StrategyCandidate(
                family=str(row["family"]),
                parameters=dict(row["parameters"]),
                parent_candidate_id=row.get("parent_candidate_id"),
                hypothesis="Fixed component for trailing-only regime allocator research.",
                candidate_id=candidate_id,
            )
        )
    return tuple(candidates)


def load_continuous_panel() -> CanonicalPanel:
    frames: list[pd.DataFrame] = []
    lineage: list[dict[str, Any]] = []
    for dataset_id in DATASET_IDS:
        loaded = load_canonical_daily_panel(REGISTRY, dataset_id, cache_root=CACHE / dataset_id)
        frames.append(loaded.frame.copy())
        lineage.append(
            {
                "dataset_id": loaded.dataset_id,
                "dataset_name": loaded.dataset_name,
                "lineage_hash": loaded.lineage_hash,
                "raw_object_count": loaded.raw_object_count,
            }
        )
    frame = pd.concat(frames, ignore_index=True)
    frame["date"] = pd.to_datetime(frame["date"]).dt.tz_localize(None)
    frame = frame.sort_values(["date", "ticker"]).drop_duplicates(["date", "ticker"], keep="last")
    return CanonicalPanel(
        dataset_id=stable_id("continuous_panel", lineage),
        dataset_name="broad_38_asset_continuous_2016_2026_ytd",
        frame=frame.reset_index(drop=True),
        raw_object_count=sum(int(row["raw_object_count"]) for row in lineage),
        source_paths=tuple(row["dataset_id"] for row in lineage),
        lineage_hash=stable_id("continuous_lineage", lineage, length=64),
        research_role="exploratory_continuous_regime_allocator_not_independent_holdout",
    )


def subset_panel(panel: CanonicalPanel, universe: str) -> CanonicalPanel:
    if universe == "full":
        return panel
    symbols = set(panel.frame["ticker"].unique())
    if universe == "etf_only":
        selected = symbols & set(ETF_SYMBOLS)
    elif universe == "equity_only":
        selected = symbols - set(ETF_SYMBOLS)
    else:
        raise KeyError(universe)
    frame = panel.frame[panel.frame["ticker"].isin(selected)].copy()
    return CanonicalPanel(
        dataset_id=f"{panel.dataset_id}_{universe}",
        dataset_name=f"{panel.dataset_name}_{universe}",
        frame=frame,
        raw_object_count=panel.raw_object_count,
        source_paths=panel.source_paths,
        lineage_hash=stable_id("subuniverse", {"lineage": panel.lineage_hash, "universe": universe, "symbols": sorted(selected)}, length=64),
        research_role=panel.research_role,
    )


def component_return_frame(panel: CanonicalPanel, candidates: tuple[StrategyCandidate, ...]) -> tuple[pd.DataFrame, dict[str, Any]]:
    streams: dict[str, pd.Series] = {}
    metadata: dict[str, Any] = {}
    policy = StrategyResearchPolicy(
        batch_size=1,
        maximum_total_candidates=1,
        maximum_generation=0,
        maximum_adaptive_children_per_cycle=0,
        slippage_bps_per_side=10.0,
        minimum_sessions=500,
        minimum_trades=1,
        maximum_drawdown_pct=100.0,
        maximum_holm_adjusted_p_value=1.0,
        walk_forward_folds=4,
        random_seed=314159,
    )
    for candidate in candidates:
        result = run_candidate_backtest(panel, candidate, policy)
        points = result.output.equity_curve
        stream = equity_curve_to_returns(
            [point.timestamp for point in points],
            [point.equity for point in points],
        )
        streams[candidate.candidate_id] = stream
        metadata[candidate.candidate_id] = {
            "family": candidate.family,
            "parameters": dict(candidate.parameters),
            "component_total_return_pct": total_return(stream),
            "component_maximum_drawdown_pct": maximum_drawdown(stream),
            "component_trade_count": result.output.metrics.get("trade_count"),
        }
    frame = pd.concat(streams, axis=1).sort_index().fillna(0.0)
    return frame, metadata


def passive_component_returns(panel: CanonicalPanel, index: pd.Index) -> pd.Series:
    opens = panel.frame.pivot(index="date", columns="ticker", values="open").sort_index().astype(float)
    if "SPY" in opens.columns:
        passive = (opens["SPY"].shift(-1) / opens["SPY"] - 1.0).fillna(0.0)
    else:
        passive = (opens.shift(-1) / opens - 1.0).mean(axis=1).fillna(0.0)
    passive.index = pd.to_datetime(passive.index).tz_localize(None)
    return passive.reindex(index).fillna(0.0)


def spy_benchmark(panel: CanonicalPanel, index: pd.Index) -> pd.Series:
    spy = panel.frame[panel.frame["ticker"] == "SPY"].sort_values("date").set_index("date")
    if spy.empty:
        equal = panel.frame.pivot(index="date", columns="ticker", values="open").sort_index()
        benchmark = (equal.shift(-1) / equal - 1.0).mean(axis=1).fillna(0.0)
    else:
        opens = spy["open"].astype(float)
        benchmark = (opens.shift(-1) / opens - 1.0).fillna(0.0)
    benchmark.index = pd.to_datetime(benchmark.index).tz_localize(None)
    return benchmark.reindex(index).fillna(0.0)


def monthly_metrics(values: pd.Series) -> dict[str, Any]:
    monthly = values.groupby(values.index.to_period("M")).apply(lambda rows: float((1.0 + rows).prod() - 1.0))
    return {
        "months": int(len(monthly)),
        "positive_months": int((monthly > 0).sum()),
        "positive_month_fraction": float((monthly > 0).mean()) if len(monthly) else None,
        "worst_month_pct": float(monthly.min() * 100.0) if len(monthly) else None,
        "best_month_pct": float(monthly.max() * 100.0) if len(monthly) else None,
    }


def evaluate_spec(
    spec: AllocatorSpec,
    component_returns: pd.DataFrame,
    benchmark: pd.Series,
    *,
    switching_cost_bps: float,
) -> dict[str, Any]:
    weights = build_allocator_weights(component_returns, spec)
    applied = apply_allocator(component_returns, weights, switching_cost_bps=switching_cost_bps)
    strategy = applied["returns"].loc[(applied["returns"].index >= EVALUATION_START) & (applied["returns"].index <= EVALUATION_END)]
    benchmark_eval = benchmark.reindex(strategy.index).fillna(0.0)
    folds = fold_metrics(strategy, benchmark_eval, FOLDS)
    monthly = monthly_metrics(strategy)
    return {
        "allocator_id": spec.allocator_id,
        "name": spec.name,
        "spec": {
            "mode": spec.mode,
            "lookback": spec.lookback,
            "rebalance": spec.rebalance,
            "top_k": spec.top_k,
            "objective": spec.objective,
            "minimum_training_return": spec.minimum_training_return,
            "target_volatility": spec.target_volatility,
            "component_subset": list(spec.component_subset),
            "passive_component": spec.passive_component,
            "core_weight": spec.core_weight,
        },
        "metrics": {
            "total_return_pct": total_return(strategy),
            "annualized_return_pct": annualized_return(strategy),
            "maximum_drawdown_pct": maximum_drawdown(strategy),
            "sharpe_ratio": sharpe(strategy),
            "daily_profit_factor": profit_factor(strategy),
            "benchmark_total_return_pct": total_return(benchmark_eval),
            "strategy_excess_return_pct": total_return(strategy) - total_return(benchmark_eval),
            "sessions": int(len(strategy)),
            "total_allocator_turnover": applied["total_turnover"],
            "average_gross_exposure": applied["average_gross_exposure"],
            "cash_fraction": applied["cash_fraction"],
            **monthly,
        },
        "folds": folds,
        "positive_strategy_folds": sum(row["strategy_return_pct"] > 0 for row in folds.values()),
        "positive_excess_folds": sum(row["excess_return_pct"] > 0 for row in folds.values()),
        "best_month_exclusion_positive": best_month_exclusion_positive(strategy),
        "raw_p_value": block_sign_flip_p_value(
            pd.Series(
                np.log1p(strategy.clip(lower=-0.999999))
                - np.log1p(benchmark_eval.clip(lower=-0.999999)),
                index=strategy.index,
            ),
            seed=271828 + int(spec.allocator_id[-6:], 16) % 100_000,
            block_size=21,
        ),
        "selection_frequency": component_selection_frequency(weights.loc[str(EVALUATION_START.date()) : str(EVALUATION_END.date())]),
        "return_path_hash": stable_id(
            "regime_allocator_return_path",
            [(timestamp.isoformat(), round(float(value), 12)) for timestamp, value in strategy.items()],
            length=64,
        ),
    }


def main() -> int:
    now = datetime.now(timezone.utc)
    panel = load_continuous_panel()
    candidates = load_component_candidates()
    specs = default_allocator_specs(COMPONENT_IDS, passive_component=PASSIVE_COMPONENT_ID)
    universe_results: dict[str, Any] = {}
    full_primary: list[dict[str, Any]] = []

    for universe in ("full", "etf_only", "equity_only"):
        current_panel = subset_panel(panel, universe)
        component_returns, component_metadata = component_return_frame(current_panel, candidates)
        passive = passive_component_returns(current_panel, component_returns.index)
        component_returns[PASSIVE_COMPONENT_ID] = passive
        component_metadata[PASSIVE_COMPONENT_ID] = {
            "family": "passive_baseline",
            "parameters": {"universe": universe},
            "component_total_return_pct": total_return(passive),
            "component_maximum_drawdown_pct": maximum_drawdown(passive),
            "component_trade_count": None,
        }
        benchmark = spy_benchmark(panel, component_returns.index)
        primary = [evaluate_spec(spec, component_returns, benchmark, switching_cost_bps=10.0) for spec in specs]
        stress_50 = {
            spec.allocator_id: evaluate_spec(spec, component_returns, benchmark, switching_cost_bps=50.0)
            for spec in specs
        }
        universe_results[universe] = {
            "symbols": sorted(current_panel.frame["ticker"].unique().tolist()),
            "symbol_count": int(current_panel.frame["ticker"].nunique()),
            "sessions": int(current_panel.frame["date"].nunique()),
            "components": component_metadata,
            "primary_10bps": primary,
            "stress_50bps": {
                key: {
                    "total_return_pct": value["metrics"]["total_return_pct"],
                    "maximum_drawdown_pct": value["metrics"]["maximum_drawdown_pct"],
                    "positive_excess_folds": value["positive_excess_folds"],
                }
                for key, value in stress_50.items()
            },
        }
        if universe == "full":
            full_primary = primary

    path_groups: dict[str, list[dict[str, Any]]] = {}
    for row in full_primary:
        path_groups.setdefault(str(row["return_path_hash"]), []).append(row)
    adjusted_paths = holm_adjust(
        {
            path_hash: min(float(row["raw_p_value"]) for row in rows)
            for path_hash, rows in path_groups.items()
        }
    )
    path_aliases = {
        path_hash: [row["name"] for row in rows]
        for path_hash, rows in path_groups.items()
        if len(rows) > 1
    }
    by_universe = {
        universe: {row["allocator_id"]: row for row in payload["primary_10bps"]}
        for universe, payload in universe_results.items()
    }
    records: list[dict[str, Any]] = []
    for row in full_primary:
        allocator_id = row["allocator_id"]
        stress = universe_results["full"]["stress_50bps"][allocator_id]
        etf = by_universe["etf_only"][allocator_id]
        equity = by_universe["equity_only"][allocator_id]
        failures: list[str] = []
        metrics = row["metrics"]
        if metrics["total_return_pct"] <= 0:
            failures.append("nonpositive_total_return")
        if metrics["strategy_excess_return_pct"] <= 0:
            failures.append("nonpositive_benchmark_excess")
        if metrics["maximum_drawdown_pct"] > 25.0:
            failures.append("maximum_drawdown_failed")
        if (metrics["daily_profit_factor"] or 0.0) < 1.05:
            failures.append("profit_factor_failed")
        if row["positive_strategy_folds"] < 3:
            failures.append("positive_fold_count_failed")
        if row["positive_excess_folds"] < 2:
            failures.append("benchmark_fold_count_failed")
        adjusted_p_value = adjusted_paths[str(row["return_path_hash"])]
        if adjusted_p_value > 0.10:
            failures.append("holm_adjusted_significance_failed")
        if not row["best_month_exclusion_positive"]:
            failures.append("best_month_exclusion_failed")
        if stress["total_return_pct"] <= 0:
            failures.append("allocator_50bps_stress_failed")
        if etf["metrics"]["total_return_pct"] <= 0 or equity["metrics"]["total_return_pct"] <= 0:
            failures.append("subuniverse_positive_return_failed")
        if etf["metrics"]["maximum_drawdown_pct"] > 35.0 or equity["metrics"]["maximum_drawdown_pct"] > 35.0:
            failures.append("subuniverse_drawdown_failed")
        records.append(
            {
                **row,
                "holm_adjusted_p_value": adjusted_p_value,
                "stress_50bps": stress,
                "subuniverse_robustness": {
                    "etf_only_total_return_pct": etf["metrics"]["total_return_pct"],
                    "etf_only_maximum_drawdown_pct": etf["metrics"]["maximum_drawdown_pct"],
                    "equity_only_total_return_pct": equity["metrics"]["total_return_pct"],
                    "equity_only_maximum_drawdown_pct": equity["metrics"]["maximum_drawdown_pct"],
                },
                "gate_failures": failures,
                "verdict": "SCREENING_PASS" if not failures else "FAIL",
            }
        )

    ranking = sorted(
        records,
        key=lambda row: (
            row["verdict"] == "SCREENING_PASS",
            row["positive_excess_folds"],
            row["metrics"]["strategy_excess_return_pct"],
            -row["metrics"]["maximum_drawdown_pct"],
        ),
        reverse=True,
    )
    payload = {
        "schema_version": 1,
        "created_at": now.isoformat(),
        "status": "completed",
        "research_role": "exploratory_nested_regime_allocator_not_independent_holdout",
        "component_selection_note": "Components were fixed from earlier governed winners and family representatives; this makes the allocator exploratory rather than independent validation.",
        "continuous_panel": {
            "dataset_id": panel.dataset_id,
            "dataset_name": panel.dataset_name,
            "source_dataset_ids": list(DATASET_IDS),
            "lineage_hash": panel.lineage_hash,
            "start": str(panel.frame["date"].min().date()),
            "end": str(panel.frame["date"].max().date()),
            "sessions": int(panel.frame["date"].nunique()),
            "symbols": int(panel.frame["ticker"].nunique()),
        },
        "evaluation_window": {"start": str(EVALUATION_START.date()), "end": str(EVALUATION_END.date())},
        "folds": FOLDS,
        "component_ids": list(COMPONENT_IDS),
        "passive_component_id": PASSIVE_COMPONENT_ID,
        "allocator_family_size": len(specs),
        "effective_hypothesis_count": len(path_groups),
        "return_path_aliases": path_aliases,
        "multiple_testing": "Holm across unique return paths in the complete fixed allocator grid",
        "statistical_test": "21-session block sign flip on log strategy excess return",
        "primary_switching_cost_bps": 10.0,
        "stress_switching_cost_bps": 50.0,
        "results": records,
        "ranking": ranking,
        "summary": {
            "allocator_specs": len(records),
            "screening_passes": sum(row["verdict"] == "SCREENING_PASS" for row in records),
            "failures": sum(row["verdict"] == "FAIL" for row in records),
            "leader_allocator_id": ranking[0]["allocator_id"] if ranking else None,
            "leader_name": ranking[0]["name"] if ranking else None,
            "leader_verdict": ranking[0]["verdict"] if ranking else None,
        },
        "universe_diagnostics": universe_results,
        "automatic_promotion": False,
        "paper_or_live_execution": False,
        "execution_authority": False,
        "source_code_auto_edit": False,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": payload["status"],
                **payload["summary"],
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
