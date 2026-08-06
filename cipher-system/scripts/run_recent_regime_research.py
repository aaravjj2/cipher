#!/usr/bin/env python3
"""Run focused rolling 2025-2026 recent-regime research.

The frozen candidate pool is outcome-informed by prior governed research and is
not an untouched holdout. Monthly selectors use only prior component returns;
market gates use only prior-session features. Results remain exploratory and
cannot promote or execute.
"""
from __future__ import annotations

import json
import math
import sqlite3
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
from core.research_platform.recent_regime import (  # noqa: E402
    RECENT_CANDIDATE_IDS,
    apply_monthly_selector,
    build_monthly_gate_weights,
    best_month_exclusion_positive,
    block_sign_flip_p_value,
    build_monthly_selector_weights,
    current_selection,
    default_recent_gate_specs,
    default_recent_selector_specs,
    maximum_drawdown_pct,
    monthly_return_map,
    sharpe_ratio,
    total_return_pct,
)
from core.research_platform.recent_regime_prospective import write_immutable_prospective_snapshot  # noqa: E402
from core.research_platform.regime_allocator import equity_curve_to_returns  # noqa: E402
from core.research_platform.strategy_research_loop import (  # noqa: E402
    CanonicalPanel,
    StrategyCandidate,
    StrategyResearchPolicy,
    holm_adjust,
    load_canonical_daily_panel,
    run_candidate_backtest,
)

REGISTRY = ROOT / "data" / "governance" / "research_registry.sqlite"
MATRIX = ROOT / "data" / "governance" / "cross_period_strategy_matrix.json"
OUTPUT = ROOT / "data" / "governance" / "recent_regime_research.json"
PROSPECTIVE_ROOT = ROOT / "data" / "governance" / "recent_regime_prospective"
CACHE = ROOT / "data" / "cache" / "recent_regime"
FALLBACK_DATASET_NAME = "alpaca_broad_daily_2024_2026_ytd_holdout_v1"
RECENT_PREFIX = "alpaca_broad_daily_recent_2024_"
COMPONENT_START = "2024-01-02"
ROLLING_START = "2025-01-02"
RECENT_YEAR_START = "2026-01-02"


def resolve_recent_dataset_id() -> str:
    with sqlite3.connect(f"file:{REGISTRY.as_posix()}?mode=ro", uri=True, timeout=30) as db:
        row = db.execute(
            """
            select dataset_id from datasets
            where frozen=1 and quality_passed=1
              and (name like ? or name = ?)
            order by case when name like ? then 0 else 1 end, created_at desc
            limit 1
            """,
            (f"{RECENT_PREFIX}%", FALLBACK_DATASET_NAME, f"{RECENT_PREFIX}%"),
        ).fetchone()
    if not row:
        raise RuntimeError("no canonical 2024-2026 recent-regime panel is registered")
    return str(row[0])


def candidate_pool() -> tuple[list[dict[str, Any]], str]:
    payload = json.loads(MATRIX.read_text(encoding="utf-8"))
    by_id = {str(row["candidate_id"]): row for row in payload.get("matrix", [])}
    missing = [candidate_id for candidate_id in RECENT_CANDIDATE_IDS if candidate_id not in by_id]
    if missing:
        raise RuntimeError(f"frozen recent-regime candidates are missing from the matrix: {missing}")
    selected: list[dict[str, Any]] = []
    for candidate_id in RECENT_CANDIDATE_IDS:
        row = by_id[candidate_id]
        selected.append(
            {
                "candidate_id": candidate_id,
                "family": str(row["family"]),
                "parameters": dict(row["parameters"]),
                "parent_candidate_id": row.get("parent_candidate_id"),
                "passed_periods": sorted(row.get("passed_periods") or []),
            }
        )
    return selected, stable_id("recent_regime_candidate_pool", selected, length=64)


def period_metrics(values: pd.Series, start: str, end: str) -> dict[str, Any]:
    selected = values.loc[(values.index >= pd.Timestamp(start)) & (values.index <= pd.Timestamp(end))]
    return {
        "sessions": int(len(selected)),
        "total_return_pct": total_return_pct(selected),
        "maximum_drawdown_pct": maximum_drawdown_pct(selected),
        "sharpe_ratio": sharpe_ratio(selected),
        "monthly_returns_pct": monthly_return_map(selected),
    }


def selector_period_metrics(
    strategy: pd.Series,
    benchmark: pd.Series,
    start: str,
    end: str,
) -> dict[str, Any]:
    selected = strategy.loc[(strategy.index >= pd.Timestamp(start)) & (strategy.index <= pd.Timestamp(end))]
    reference = benchmark.reindex(selected.index).fillna(0.0)
    monthly_strategy = monthly_return_map(selected)
    monthly_benchmark = monthly_return_map(reference)
    return {
        "sessions": int(len(selected)),
        "total_return_pct": total_return_pct(selected),
        "benchmark_return_pct": total_return_pct(reference),
        "strategy_excess_return_pct": total_return_pct(selected) - total_return_pct(reference),
        "maximum_drawdown_pct": maximum_drawdown_pct(selected),
        "sharpe_ratio": sharpe_ratio(selected),
        "positive_months": sum(value > 0.0 for value in monthly_strategy.values()),
        "benchmark_beating_months": sum(
            monthly_strategy.get(month, 0.0) > monthly_benchmark.get(month, 0.0)
            for month in monthly_strategy
        ),
        "months": len(monthly_strategy),
        "monthly_returns_pct": monthly_strategy,
        "benchmark_monthly_returns_pct": monthly_benchmark,
        "best_month_exclusion_positive": best_month_exclusion_positive(selected),
    }


def trade_period_summary(trades: tuple[Any, ...], start: str, end: str) -> dict[str, Any]:
    lower = pd.Timestamp(start)
    upper = pd.Timestamp(end)
    selected = [trade for trade in trades if lower <= pd.Timestamp(trade.entry_time) <= upper]
    returns = [float(trade.return_pct) for trade in selected if trade.return_pct is not None]
    holds = [
        int((pd.Timestamp(trade.exit_time) - pd.Timestamp(trade.entry_time)).days)
        for trade in selected
        if trade.exit_time and trade.entry_time
    ]
    return {
        "trade_count": len(selected),
        "win_rate": float(np.mean(np.asarray(returns) > 0.0)) if returns else None,
        "median_trade_return_pct": float(np.median(returns)) if returns else None,
        "median_hold_days": float(np.median(holds)) if holds else None,
    }


def market_regime_features(daily: pd.DataFrame) -> pd.DataFrame:
    frame = daily.copy()
    frame["date"] = pd.to_datetime(frame["date"]).dt.tz_localize(None)
    closes = frame.pivot(index="date", columns="ticker", values="close").sort_index()
    if "SPY" not in closes.columns:
        raise RuntimeError("recent-regime panel requires SPY closes")
    spy = closes["SPY"]
    daily_returns = spy.pct_change()
    realized_vol_21 = daily_returns.rolling(21, min_periods=21).std(ddof=0) * math.sqrt(252.0)
    dispersion_21 = closes.pct_change(21).std(axis=1, ddof=0)
    return pd.DataFrame(
        {
            "spy_return_21": spy.pct_change(21),
            "spy_return_63": spy.pct_change(63),
            "spy_sma50_distance": spy / spy.rolling(50, min_periods=50).mean() - 1.0,
            "spy_drawdown_63": spy / spy.rolling(63, min_periods=63).max() - 1.0,
            "realized_vol_21": realized_vol_21,
            "realized_vol_median_252": realized_vol_21.rolling(252, min_periods=126).median(),
            "dispersion_21": dispersion_21,
            "dispersion_median_252": dispersion_21.rolling(252, min_periods=126).median(),
        }
    )


def active_symbols_at_end(trades: tuple[Any, ...], evaluation_end: str) -> list[dict[str, Any]]:
    end = pd.Timestamp(evaluation_end)
    output: list[dict[str, Any]] = []
    for trade in trades:
        metadata = dict(trade.metadata or {})
        if pd.Timestamp(trade.exit_time) == end and metadata.get("forced_final_close"):
            output.append(
                {
                    "symbol": trade.symbol,
                    "entry_time": pd.Timestamp(trade.entry_time).date().isoformat(),
                    "entry_price": float(trade.entry_price),
                    "mark_to_end_return_pct": float(trade.return_pct) if trade.return_pct is not None else None,
                }
            )
    return sorted(output, key=lambda row: row["symbol"])


def main() -> int:
    created_at = datetime.now(timezone.utc)
    rows, pool_hash = candidate_pool()
    dataset_id = resolve_recent_dataset_id()
    loaded = load_canonical_daily_panel(REGISTRY, dataset_id, cache_root=CACHE / dataset_id)
    latest_session = pd.to_datetime(loaded.frame["date"]).max().tz_localize(None)
    evaluation_end = min(latest_session, pd.Timestamp("2026-12-31")).date().isoformat()
    panel = CanonicalPanel(
        dataset_id=loaded.dataset_id,
        dataset_name=loaded.dataset_name,
        frame=loaded.frame.copy(),
        raw_object_count=loaded.raw_object_count,
        source_paths=loaded.source_paths,
        lineage_hash=loaded.lineage_hash,
        research_role="recent_2024_warmup_2025_2026_rolling_development_only_not_independent_holdout",
        evaluation_start=COMPONENT_START,
        evaluation_end=evaluation_end,
    )
    policy = StrategyResearchPolicy(
        batch_size=1,
        maximum_total_candidates=14,
        maximum_generation=0,
        maximum_adaptive_children_per_cycle=1,
        slippage_bps_per_side=10.0,
        minimum_sessions=300,
        minimum_trades=1,
        minimum_profit_factor=0.0,
        maximum_drawdown_pct=100.0,
        maximum_holm_adjusted_p_value=1.0,
        walk_forward_folds=4,
        random_seed=20252026,
    )

    component_returns: dict[str, pd.Series] = {}
    component_families: dict[str, str] = {}
    component_records: list[dict[str, Any]] = []
    component_outputs: dict[str, Any] = {}
    for row in rows:
        candidate = StrategyCandidate(
            family=row["family"],
            parameters=row["parameters"],
            parent_candidate_id=row.get("parent_candidate_id"),
            hypothesis="Recent-regime component selected from prior governed screening evidence.",
            candidate_id=row["candidate_id"],
        )
        result = run_candidate_backtest(panel, candidate, policy)
        timestamps = [point.timestamp for point in result.output.equity_curve]
        values = [point.equity for point in result.output.equity_curve]
        daily_returns = equity_curve_to_returns(timestamps, values)
        component_returns[candidate.candidate_id] = daily_returns
        component_families[candidate.candidate_id] = candidate.family
        component_outputs[candidate.candidate_id] = result.output
        metrics_2025 = period_metrics(daily_returns, ROLLING_START, "2025-12-31")
        metrics_2026 = period_metrics(daily_returns, RECENT_YEAR_START, evaluation_end)
        trades_2025 = trade_period_summary(result.output.trades, ROLLING_START, "2025-12-31")
        trades_2026 = trade_period_summary(result.output.trades, RECENT_YEAR_START, evaluation_end)
        recent_score = (
            float(metrics_2026["total_return_pct"])
            + 0.35 * float(metrics_2025["total_return_pct"])
            - 0.60 * float(metrics_2026["maximum_drawdown_pct"])
            - 0.20 * float(metrics_2025["maximum_drawdown_pct"])
        )
        component_records.append(
            {
                "candidate": row,
                "recent_score": recent_score,
                "metrics_2025": metrics_2025,
                "metrics_2026_ytd": metrics_2026,
                "trades_2025": trades_2025,
                "trades_2026_ytd": trades_2026,
                "active_symbols_at_end": active_symbols_at_end(result.output.trades, evaluation_end),
                "next_session_open_execution": True,
                "slippage_bps_per_side": 10.0,
            }
        )

    returns_frame = pd.concat(component_returns, axis=1).sort_index().fillna(0.0)
    daily = panel.frame.copy()
    daily["date"] = pd.to_datetime(daily["date"]).dt.tz_localize(None)
    opens = daily.pivot(index="date", columns="ticker", values="open").sort_index()
    if "SPY" not in opens.columns:
        raise RuntimeError("recent-regime panel requires SPY")
    spy_returns = (opens["SPY"].shift(-1) / opens["SPY"] - 1.0).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    spy_returns = spy_returns.reindex(returns_frame.index).fillna(0.0)
    returns_frame["passive_spy"] = spy_returns
    component_families["passive_spy"] = "passive_benchmark"
    regime_features = market_regime_features(daily)

    selector_intermediate: list[dict[str, Any]] = []
    selector_weights: dict[str, pd.DataFrame] = {}
    raw_p_values: dict[str, float] = {}
    for spec in default_recent_selector_specs():
        weights, decisions = build_monthly_selector_weights(
            returns_frame,
            component_families,
            spec,
            evaluation_start=ROLLING_START,
            evaluation_end=evaluation_end,
        )
        selector_weights[spec.name] = weights.copy()
        base = apply_monthly_selector(returns_frame, weights, switching_cost_bps=10.0)
        stress25 = apply_monthly_selector(returns_frame, weights, switching_cost_bps=25.0)
        stress50 = apply_monthly_selector(returns_frame, weights, switching_cost_bps=50.0)
        strategy = base["returns"]
        benchmark = returns_frame["passive_spy"].reindex(strategy.index).fillna(0.0)
        metrics_2025 = selector_period_metrics(strategy, benchmark, ROLLING_START, "2025-12-31")
        metrics_2026 = selector_period_metrics(strategy, benchmark, RECENT_YEAR_START, evaluation_end)
        metrics_combined = selector_period_metrics(strategy, benchmark, ROLLING_START, evaluation_end)
        metrics_combined["total_turnover"] = base["total_turnover"]
        metrics_2026["total_turnover"] = float(
            base["turnover"].loc[
                (base["turnover"].index >= pd.Timestamp(RECENT_YEAR_START))
                & (base["turnover"].index <= pd.Timestamp(evaluation_end))
            ].sum()
        )
        stress25_combined = period_metrics(stress25["returns"], ROLLING_START, evaluation_end)
        stress50_combined = period_metrics(stress50["returns"], ROLLING_START, evaluation_end)
        excess = np.log1p(strategy.clip(lower=-0.999999)) - np.log1p(benchmark.clip(lower=-0.999999))
        raw_p = block_sign_flip_p_value(
            excess,
            seed=20250000 + int(spec.selector_id[-6:], 16) % 100_000,
            block_size=10,
        )
        raw_p_values[spec.selector_id] = raw_p
        selector_intermediate.append(
            {
                "selector_id": spec.selector_id,
                "spec": {
                    "name": spec.name,
                    "lookback_sessions": spec.lookback_sessions,
                    "top_k": spec.top_k,
                    "objective": spec.objective,
                    "mode": spec.mode,
                    "core_weight": spec.core_weight,
                    "minimum_training_return": spec.minimum_training_return,
                },
                "decisions": decisions,
                "current_selection": current_selection(decisions),
                "metrics": metrics_2026,
                "metrics_2025": metrics_2025,
                "metrics_2026_ytd": metrics_2026,
                "metrics_combined": metrics_combined,
                "stress_25bps": {
                    **stress25_combined,
                    "metrics_2025": period_metrics(stress25["returns"], ROLLING_START, "2025-12-31"),
                    "metrics_2026_ytd": period_metrics(stress25["returns"], RECENT_YEAR_START, evaluation_end),
                },
                "stress_50bps": {
                    **stress50_combined,
                    "metrics_2025": period_metrics(stress50["returns"], ROLLING_START, "2025-12-31"),
                    "metrics_2026_ytd": period_metrics(stress50["returns"], RECENT_YEAR_START, evaluation_end),
                },
                "raw_p_value": raw_p,
                "best_month_exclusion_positive": metrics_combined["best_month_exclusion_positive"],
            }
        )

    adjusted = holm_adjust(raw_p_values)
    selector_records: list[dict[str, Any]] = []
    for row in selector_intermediate:
        selector_id = row["selector_id"]
        metrics_2025 = row["metrics_2025"]
        metrics_2026 = row["metrics_2026_ytd"]
        metrics = row["metrics_combined"]
        months = max(1, int(metrics["months"]))
        failures: list[str] = []
        if float(metrics["total_return_pct"]) <= 0.0:
            failures.append("nonpositive_combined_return")
        if float(metrics["strategy_excess_return_pct"]) <= 0.0:
            failures.append("nonpositive_combined_spy_excess")
        if float(metrics_2025["strategy_excess_return_pct"]) <= 0.0:
            failures.append("nonpositive_2025_spy_excess")
        if float(metrics_2026["strategy_excess_return_pct"]) <= 0.0:
            failures.append("nonpositive_2026_spy_excess")
        if float(metrics["maximum_drawdown_pct"]) > 15.0:
            failures.append("maximum_drawdown_failed")
        if int(metrics["positive_months"]) < math.ceil(months * 0.57):
            failures.append("positive_month_consistency_failed")
        if int(metrics["benchmark_beating_months"]) < math.ceil(months / 2):
            failures.append("benchmark_month_consistency_failed")
        if float(row["stress_50bps"]["metrics_2025"]["total_return_pct"]) <= 0.0:
            failures.append("2025_switching_cost_stress_failed")
        if float(row["stress_50bps"]["metrics_2026_ytd"]["total_return_pct"]) <= 0.0:
            failures.append("2026_switching_cost_stress_failed")
        if not bool(row["best_month_exclusion_positive"]):
            failures.append("best_month_exclusion_failed")
        if float(adjusted[selector_id]) > 0.20:
            failures.append("holm_adjusted_significance_failed")
        score = (
            float(metrics_2025["strategy_excess_return_pct"])
            + 1.5 * float(metrics_2026["strategy_excess_return_pct"])
            - 0.75 * float(metrics["maximum_drawdown_pct"])
            + 2.0 * int(metrics["benchmark_beating_months"])
            + float(row["stress_50bps"]["total_return_pct"])
        )
        selector_records.append(
            {
                **row,
                "holm_adjusted_p_value": float(adjusted[selector_id]),
                "gate_failures": failures,
                "verdict": "PASS" if not failures else "FAIL",
                "composite_score": score,
                "automatic_promotion": False,
                "execution_authority": False,
            }
        )

    selector_by_name = {row["spec"]["name"]: row for row in selector_records}
    gate_intermediate: list[dict[str, Any]] = []
    gate_raw_p_values: dict[str, float] = {}
    for base_name in ("monthly_top1_63d_return", "monthly_family_balanced_63d_return"):
        base_weights = selector_weights[base_name]
        for gate_spec in default_recent_gate_specs():
            gated_weights, gate_decisions = build_monthly_gate_weights(
                base_weights,
                regime_features,
                gate_spec,
            )
            base = apply_monthly_selector(returns_frame, gated_weights, switching_cost_bps=10.0)
            stress50 = apply_monthly_selector(returns_frame, gated_weights, switching_cost_bps=50.0)
            strategy = base["returns"]
            benchmark = returns_frame["passive_spy"].reindex(strategy.index).fillna(0.0)
            metrics_2025 = selector_period_metrics(strategy, benchmark, ROLLING_START, "2025-12-31")
            metrics_2026 = selector_period_metrics(strategy, benchmark, RECENT_YEAR_START, evaluation_end)
            metrics_combined = selector_period_metrics(strategy, benchmark, ROLLING_START, evaluation_end)
            metrics_combined["total_turnover"] = base["total_turnover"]
            stress_2025 = period_metrics(stress50["returns"], ROLLING_START, "2025-12-31")
            stress_2026 = period_metrics(stress50["returns"], RECENT_YEAR_START, evaluation_end)
            stress_combined = period_metrics(stress50["returns"], ROLLING_START, evaluation_end)
            excess = np.log1p(strategy.clip(lower=-0.999999)) - np.log1p(benchmark.clip(lower=-0.999999))
            hypothesis_id = stable_id(
                "recent_gate_hypothesis",
                {"base_selector": base_name, "gate_id": gate_spec.gate_id},
            )
            raw_p = block_sign_flip_p_value(
                excess,
                seed=20251000 + int(hypothesis_id[-6:], 16) % 100_000,
                block_size=10,
            )
            gate_raw_p_values[hypothesis_id] = raw_p
            base_selector = selector_by_name[base_name]
            current_gate = dict(gate_decisions[-1]) if gate_decisions else None
            gate_intermediate.append(
                {
                    "hypothesis_id": hypothesis_id,
                    "base_selector_id": base_selector["selector_id"],
                    "base_selector_name": base_name,
                    "gate_id": gate_spec.gate_id,
                    "gate_name": gate_spec.name,
                    "gate_condition": gate_spec.condition,
                    "gate_decisions": gate_decisions,
                    "current_gate_decision": current_gate,
                    "current_base_selection": base_selector.get("current_selection"),
                    "current_effective_selection": (
                        base_selector.get("current_selection")
                        if current_gate and current_gate.get("active_selector")
                        else {"selected_components": [], "weights": {"passive_spy": 1.0}, "fallback_to_spy": True}
                    ),
                    "metrics_2025": metrics_2025,
                    "metrics_2026_ytd": metrics_2026,
                    "metrics_combined": metrics_combined,
                    "stress_50bps": {
                        **stress_combined,
                        "metrics_2025": stress_2025,
                        "metrics_2026_ytd": stress_2026,
                    },
                    "raw_p_value": raw_p,
                    "best_month_exclusion_positive": metrics_combined["best_month_exclusion_positive"],
                }
            )

    gate_adjusted = holm_adjust(gate_raw_p_values)
    gate_records: list[dict[str, Any]] = []
    for row in gate_intermediate:
        metrics_2025 = row["metrics_2025"]
        metrics_2026 = row["metrics_2026_ytd"]
        metrics = row["metrics_combined"]
        months = max(1, int(metrics["months"]))
        failures: list[str] = []
        if float(metrics_2025["strategy_excess_return_pct"]) <= 0.0:
            failures.append("nonpositive_2025_spy_excess")
        if float(metrics_2026["strategy_excess_return_pct"]) <= 0.0:
            failures.append("nonpositive_2026_spy_excess")
        if float(metrics["strategy_excess_return_pct"]) <= 0.0:
            failures.append("nonpositive_combined_spy_excess")
        if float(metrics["maximum_drawdown_pct"]) > 15.0:
            failures.append("maximum_drawdown_failed")
        if int(metrics["positive_months"]) < math.ceil(months * 0.57):
            failures.append("positive_month_consistency_failed")
        if int(metrics["benchmark_beating_months"]) < math.ceil(months / 2):
            failures.append("benchmark_month_consistency_failed")
        if float(row["stress_50bps"]["metrics_2025"]["total_return_pct"]) <= 0.0:
            failures.append("2025_switching_cost_stress_failed")
        if float(row["stress_50bps"]["metrics_2026_ytd"]["total_return_pct"]) <= 0.0:
            failures.append("2026_switching_cost_stress_failed")
        if not bool(row["best_month_exclusion_positive"]):
            failures.append("best_month_exclusion_failed")
        if float(gate_adjusted[row["hypothesis_id"]]) > 0.20:
            failures.append("holm_adjusted_significance_failed")
        score = (
            float(metrics_2025["strategy_excess_return_pct"])
            + 1.5 * float(metrics_2026["strategy_excess_return_pct"])
            - 0.75 * float(metrics["maximum_drawdown_pct"])
            + 2.0 * int(metrics["benchmark_beating_months"])
            + float(row["stress_50bps"]["total_return_pct"])
        )
        gate_records.append(
            {
                **row,
                "holm_adjusted_p_value": float(gate_adjusted[row["hypothesis_id"]]),
                "gate_failures": failures,
                "verdict": "PASS" if not failures else "FAIL",
                "composite_score": score,
                "automatic_promotion": False,
                "execution_authority": False,
            }
        )

    component_records.sort(key=lambda row: float(row["recent_score"]), reverse=True)
    selector_records.sort(key=lambda row: float(row["composite_score"]), reverse=True)
    gate_records.sort(key=lambda row: float(row["composite_score"]), reverse=True)
    passes = [row for row in selector_records if row["verdict"] == "PASS"]
    gate_passes = [row for row in gate_records if row["verdict"] == "PASS"]
    leader = selector_records[0] if selector_records else None
    gate_leader = gate_records[0] if gate_records else None
    current_components: list[dict[str, Any]] = []
    if leader and leader.get("current_selection"):
        for candidate_id in leader["current_selection"].get("selected_components", []):
            output = component_outputs.get(candidate_id)
            current_components.append(
                {
                    "candidate_id": candidate_id,
                    "family": component_families.get(candidate_id),
                    "active_symbols": active_symbols_at_end(output.trades, evaluation_end) if output else [],
                }
            )

    payload = {
        "schema_version": 1,
        "created_at": created_at.isoformat(),
        "status": "completed",
        "dataset": {
            "dataset_id": panel.dataset_id,
            "dataset_name": panel.dataset_name,
            "lineage_hash": panel.lineage_hash,
            "component_return_start": COMPONENT_START,
            "evaluation_start": ROLLING_START,
            "evaluation_end": evaluation_end,
            "rolling_2025_start": ROLLING_START,
            "rolling_2026_start": RECENT_YEAR_START,
            "latest_session": latest_session.date().isoformat(),
        },
        "candidate_pool": {
            "count": len(rows),
            "hash": pool_hash,
            "selection_rule": "frozen_2026_08_04_recent_evidence_pool",
            "outcome_informed": True,
            "independent_holdout": False,
            "candidates": rows,
        },
        "component_records": component_records,
        "selector_records": selector_records,
        "gate_records": gate_records,
        "summary": {
            "components": len(component_records),
            "selectors": len(selector_records),
            "selector_passes": len(passes),
            "gate_variants": len(gate_records),
            "gate_passes": len(gate_passes),
            "gate_leader_name": gate_leader.get("gate_name") if gate_leader else None,
            "gate_leader_base_selector": gate_leader.get("base_selector_name") if gate_leader else None,
            "gate_leader_verdict": gate_leader.get("verdict") if gate_leader else None,
            "gate_leader_2025_return_pct": (gate_leader.get("metrics_2025") or {}).get("total_return_pct") if gate_leader else None,
            "gate_leader_2026_return_pct": (gate_leader.get("metrics_2026_ytd") or {}).get("total_return_pct") if gate_leader else None,
            "gate_leader_combined_spy_excess_pct": (gate_leader.get("metrics_combined") or {}).get("strategy_excess_return_pct") if gate_leader else None,
            "gate_current_decision": gate_leader.get("current_gate_decision") if gate_leader else None,
            "gate_current_effective_selection": gate_leader.get("current_effective_selection") if gate_leader else None,
            "leader_selector_id": leader.get("selector_id") if leader else None,
            "leader_selector_name": (leader.get("spec") or {}).get("name") if leader else None,
            "leader_verdict": leader.get("verdict") if leader else None,
            "leader_2025_return_pct": (leader.get("metrics_2025") or {}).get("total_return_pct") if leader else None,
            "leader_2025_spy_excess_pct": (leader.get("metrics_2025") or {}).get("strategy_excess_return_pct") if leader else None,
            "leader_2026_return_pct": (leader.get("metrics_2026_ytd") or {}).get("total_return_pct") if leader else None,
            "leader_spy_excess_pct": (leader.get("metrics_2026_ytd") or {}).get("strategy_excess_return_pct") if leader else None,
            "leader_combined_return_pct": (leader.get("metrics_combined") or {}).get("total_return_pct") if leader else None,
            "leader_combined_spy_excess_pct": (leader.get("metrics_combined") or {}).get("strategy_excess_return_pct") if leader else None,
            "best_component_candidate_id": component_records[0]["candidate"]["candidate_id"] if component_records else None,
            "best_component_family": component_records[0]["candidate"]["family"] if component_records else None,
            "best_component_2026_return_pct": component_records[0]["metrics_2026_ytd"]["total_return_pct"] if component_records else None,
            "current_selection": leader.get("current_selection") if leader else None,
            "current_selected_components": current_components,
            "allowed_claim": "recent_regime_exploratory_rolling_results_only_no_independent_holdout",
            "promotion_eligible": False,
        },
        "research_role": "recent_2024_warmup_2025_2026_rolling_development_only_not_independent_holdout",
        "automatic_promotion": False,
        "paper_or_live_execution": False,
        "execution_authority": False,
    }
    prospective_snapshot = write_immutable_prospective_snapshot(
        payload,
        root=PROSPECTIVE_ROOT,
        created_at=created_at,
    )
    payload["prospective_snapshot"] = prospective_snapshot
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUTPUT.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    temporary.replace(OUTPUT)
    print(
        json.dumps(
            {
                "status": payload["status"],
                "dataset_id": panel.dataset_id,
                "evaluation_end": evaluation_end,
                "components": len(component_records),
                "selectors": len(selector_records),
                "selector_passes": len(passes),
                "gate_variants": len(gate_records),
                "gate_passes": len(gate_passes),
                "gate_leader_name": payload["summary"]["gate_leader_name"],
                "leader_selector_name": payload["summary"]["leader_selector_name"],
                "leader_verdict": payload["summary"]["leader_verdict"],
                "leader_2025_return_pct": payload["summary"]["leader_2025_return_pct"],
                "leader_2026_return_pct": payload["summary"]["leader_2026_return_pct"],
                "prospective_snapshot_status": prospective_snapshot["status"],
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
