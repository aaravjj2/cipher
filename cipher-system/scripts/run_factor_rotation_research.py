#!/usr/bin/env python3
"""Evaluate the frozen factor/macro ETF rotation grid.

The complete 16-rule grid was hashed before the provider download.  Signals use
close information and positions are shifted to the next session open.  Results
remain exploratory because the date range overlaps prior research periods.
"""
from __future__ import annotations

import json
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

from core.research_platform.factor_rotation import (  # noqa: E402
    FactorRotationSpec,
    adaptive_factor_rotation_specs,
    build_desired_weights,
    default_factor_rotation_specs,
    selection_frequency,
    simulate_rotation,
)
from core.research_platform.hashing import stable_id  # noqa: E402
from core.research_platform.regime_allocator import (  # noqa: E402
    annualized_return,
    best_month_exclusion_positive,
    block_sign_flip_p_value,
    fold_metrics,
    holm_adjust,
    maximum_drawdown,
    profit_factor,
    sharpe,
    total_return,
)
from core.research_platform.strategy_research_loop import load_canonical_daily_panel  # noqa: E402

REGISTRY = ROOT / "data" / "governance" / "research_registry.sqlite"
DATASET_ID = "ds_796df562a29d2b01d2e1ca24"
CACHE = ROOT / "data" / "cache" / "factor_rotation"
REGISTRATION = ROOT / "data" / "historical_equities" / "factor_etf_panel_v1" / "registration.json"
OUTPUT = ROOT / "data" / "governance" / "factor_rotation_research.json"
EVALUATION_START = pd.Timestamp("2018-01-02")
EVALUATION_END = pd.Timestamp("2026-08-04")
FOLDS = {
    "2018_2019": ("2018-01-02", "2019-12-31"),
    "2020_2022": ("2020-01-02", "2022-12-30"),
    "2023_2025": ("2023-01-03", "2025-12-31"),
    "2026_ytd": ("2026-01-02", "2026-08-04"),
}
DROP_CATEGORIES = ("sectors", "international", "bonds", "real_assets")


def frozen_spec_payload(spec: FactorRotationSpec) -> dict[str, Any]:
    """Exact initial-grid schema persisted before the provider download."""
    return {
        "strategy_id": spec.strategy_id,
        "name": spec.name,
        "mode": spec.mode,
        "lookback": spec.lookback,
        "skip": spec.skip,
        "top_k": spec.top_k,
        "rebalance": spec.rebalance,
        "score_type": spec.score_type,
        "absolute_momentum": spec.absolute_momentum,
        "trend_filter": spec.trend_filter,
        "defensive_symbol": spec.defensive_symbol,
        "core_weight": spec.core_weight,
        "target_volatility": spec.target_volatility,
    }


def spec_payload(spec: FactorRotationSpec) -> dict[str, Any]:
    return {
        "strategy_id": spec.strategy_id,
        "name": spec.name,
        "mode": spec.mode,
        "lookback": spec.lookback,
        "skip": spec.skip,
        "top_k": spec.top_k,
        "rebalance": spec.rebalance,
        "score_type": spec.score_type,
        "absolute_momentum": spec.absolute_momentum,
        "trend_filter": spec.trend_filter,
        "defensive_symbol": spec.defensive_symbol,
        "core_weight": spec.core_weight,
        "target_volatility": spec.target_volatility,
        "relative_overlay_lookback": spec.relative_overlay_lookback,
        "market_trend_filter": spec.market_trend_filter,
        "passive_symbol": spec.passive_symbol,
        "risk_off_symbol": spec.risk_off_symbol,
    }


def load_registered_freeze() -> dict[str, Any]:
    with sqlite3.connect(REGISTRY) as db:
        row = db.execute(
            """
            select r.payload_json
            from dataset_raw_objects l
            join raw_objects r on r.raw_object_id=l.raw_object_id
            where l.dataset_id=?
            order by r.raw_object_id
            limit 1
            """,
            (DATASET_ID,),
        ).fetchone()
    if not row:
        raise RuntimeError("factor rotation dataset raw lineage is missing")
    payload = json.loads(row[0])
    metadata = payload.get("request_metadata") or {}
    grid = metadata.get("strategy_grid_pre_download")
    if not isinstance(grid, list) or not grid:
        raise RuntimeError("factor rotation pre-download strategy grid is missing from raw lineage")
    return {
        "freeze_hash": metadata.get("strategy_grid_freeze_hash"),
        "symbols": metadata.get("symbols_requested"),
        "categories": metadata.get("categories"),
        "grid": grid,
    }


def monthly_metrics(values: pd.Series) -> dict[str, Any]:
    monthly = values.groupby(values.index.to_period("M")).apply(lambda rows: float((1.0 + rows).prod() - 1.0))
    return {
        "months": int(len(monthly)),
        "positive_months": int((monthly > 0).sum()),
        "positive_month_fraction": float((monthly > 0).mean()) if len(monthly) else None,
        "worst_month_pct": float(monthly.min() * 100.0) if len(monthly) else None,
        "best_month_pct": float(monthly.max() * 100.0) if len(monthly) else None,
    }


def evaluate(
    opens: pd.DataFrame,
    closes: pd.DataFrame,
    benchmark: pd.Series,
    spec: FactorRotationSpec,
    categories: dict[str, list[str]],
    *,
    slippage_bps: float,
) -> dict[str, Any]:
    desired = build_desired_weights(closes, spec, categories)
    simulation = simulate_rotation(opens, desired, slippage_bps_per_side=slippage_bps)
    strategy = simulation["returns"].loc[(simulation["returns"].index >= EVALUATION_START) & (simulation["returns"].index <= EVALUATION_END)]
    benchmark_eval = benchmark.reindex(strategy.index).fillna(0.0)
    folds = fold_metrics(strategy, benchmark_eval, FOLDS)
    metrics = {
        "total_return_pct": total_return(strategy),
        "annualized_return_pct": annualized_return(strategy),
        "maximum_drawdown_pct": maximum_drawdown(strategy),
        "sharpe_ratio": sharpe(strategy),
        "daily_profit_factor": profit_factor(strategy),
        "benchmark_total_return_pct": total_return(benchmark_eval),
        "strategy_excess_return_pct": total_return(strategy) - total_return(benchmark_eval),
        "sessions": int(len(strategy)),
        "total_turnover": simulation["total_turnover"],
        "average_gross_exposure": simulation["average_gross_exposure"],
        "cash_fraction": simulation["cash_fraction"],
        **monthly_metrics(strategy),
    }
    return {
        "strategy_id": spec.strategy_id,
        "name": spec.name,
        "spec": spec_payload(spec),
        "metrics": metrics,
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
            seed=161803 + int(spec.strategy_id[-6:], 16) % 100_000,
            block_size=21,
        ),
        "selection_frequency": selection_frequency(simulation["weights"].loc[str(EVALUATION_START.date()) : str(EVALUATION_END.date())]),
        "return_path_hash": stable_id(
            "factor_rotation_return_path",
            [(timestamp.isoformat(), round(float(value), 12)) for timestamp, value in strategy.items()],
            length=64,
        ),
    }


def main() -> int:
    now = datetime.now(timezone.utc)
    registration = json.loads(REGISTRATION.read_text(encoding="utf-8"))
    categories = {key: list(value) for key, value in registration["categories"].items()}
    initial_specs = default_factor_rotation_specs()
    adaptive_specs = adaptive_factor_rotation_specs()
    specs = initial_specs + adaptive_specs
    registered_freeze = load_registered_freeze()
    expected_grid = [frozen_spec_payload(spec) for spec in initial_specs]
    if expected_grid != registered_freeze["grid"]:
        raise RuntimeError("factor rotation initial grid no longer matches the pre-download raw lineage")
    recomputed_freeze_hash = stable_id(
        "factor_rotation_grid_pre_download",
        {
            "symbols": tuple(registered_freeze["symbols"] or ()),
            "categories": {
                key: tuple(value)
                for key, value in (registered_freeze["categories"] or {}).items()
            },
            "strategies": expected_grid,
        },
        length=64,
    )
    if recomputed_freeze_hash != registered_freeze["freeze_hash"]:
        raise RuntimeError("factor rotation pre-download freeze hash failed recomputation")
    if recomputed_freeze_hash != registration.get("strategy_grid_freeze_hash"):
        raise RuntimeError("factor rotation registration report disagrees with canonical raw lineage")
    if len(initial_specs) != int(registration["strategy_count"]):
        raise RuntimeError("factor rotation strategy count changed after download")

    loaded = load_canonical_daily_panel(REGISTRY, DATASET_ID, cache_root=CACHE)
    frame = loaded.frame.copy()
    frame["date"] = pd.to_datetime(frame["date"]).dt.tz_localize(None)
    opens = frame.pivot(index="date", columns="ticker", values="open").sort_index().astype(float)
    closes = frame.pivot(index="date", columns="ticker", values="close").sort_index().astype(float)
    common = opens.index.intersection(closes.index)
    opens = opens.reindex(common)
    closes = closes.reindex(common)
    benchmark = (opens["SPY"].shift(-1) / opens["SPY"] - 1.0).fillna(0.0)

    primary = [evaluate(opens, closes, benchmark, spec, categories, slippage_bps=10.0) for spec in specs]
    stress_50 = {
        spec.strategy_id: evaluate(opens, closes, benchmark, spec, categories, slippage_bps=50.0)
        for spec in specs
    }
    category_stress: dict[str, dict[str, dict[str, Any]]] = {}
    for category in DROP_CATEGORIES:
        dropped = set(categories.get(category, []))
        keep = [symbol for symbol in opens.columns if symbol not in dropped]
        category_stress[category] = {}
        for spec in specs:
            result = evaluate(
                opens[keep],
                closes[keep],
                benchmark,
                spec,
                categories,
                slippage_bps=10.0,
            )
            category_stress[category][spec.strategy_id] = {
                "total_return_pct": result["metrics"]["total_return_pct"],
                "maximum_drawdown_pct": result["metrics"]["maximum_drawdown_pct"],
                "strategy_excess_return_pct": result["metrics"]["strategy_excess_return_pct"],
                "positive_excess_folds": result["positive_excess_folds"],
            }

    path_groups: dict[str, list[dict[str, Any]]] = {}
    for row in primary:
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
    records: list[dict[str, Any]] = []
    for row in primary:
        strategy_id = row["strategy_id"]
        metrics = row["metrics"]
        stress = stress_50[strategy_id]
        category_rows = {category: category_stress[category][strategy_id] for category in DROP_CATEGORIES}
        failures: list[str] = []
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
        if stress["metrics"]["total_return_pct"] <= 0:
            failures.append("50bps_turnover_stress_failed")
        if any(value["total_return_pct"] <= 0 for value in category_rows.values()):
            failures.append("leave_one_category_positive_return_failed")
        if any(value["maximum_drawdown_pct"] > 35.0 for value in category_rows.values()):
            failures.append("leave_one_category_drawdown_failed")
        if sum(value["strategy_excess_return_pct"] > 0 for value in category_rows.values()) < 2:
            failures.append("leave_one_category_excess_failed")
        records.append(
            {
                **row,
                "search_generation": "initial_pre_download" if strategy_id in {spec.strategy_id for spec in initial_specs} else "bounded_adaptive_generation_1",
                "holm_adjusted_p_value": adjusted_p_value,
                "stress_50bps": {
                    "total_return_pct": stress["metrics"]["total_return_pct"],
                    "maximum_drawdown_pct": stress["metrics"]["maximum_drawdown_pct"],
                    "positive_excess_folds": stress["positive_excess_folds"],
                },
                "leave_one_category_out": category_rows,
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
        "dataset": {
            "dataset_id": loaded.dataset_id,
            "dataset_name": loaded.dataset_name,
            "lineage_hash": loaded.lineage_hash,
            "sessions": int(frame["date"].nunique()),
            "symbols": int(frame["ticker"].nunique()),
            "start": str(frame["date"].min().date()),
            "end": str(frame["date"].max().date()),
        },
        "research_role": "factor_macro_etf_rotation_development_only_not_independent_holdout",
        "strategy_grid_frozen_before_download": True,
        "registration_freeze_hash": registration["strategy_grid_freeze_hash"],
        "canonical_raw_lineage_freeze_verified": True,
        "recomputed_freeze_hash": recomputed_freeze_hash,
        "initial_strategy_count": len(initial_specs),
        "adaptive_strategy_count": len(adaptive_specs),
        "strategy_family_size": len(specs),
        "effective_hypothesis_count": len(path_groups),
        "return_path_aliases": path_aliases,
        "multiple_testing": "Holm across unique return paths in the complete initial and bounded adaptive rotation family",
        "statistical_test": "21-session block sign flip on log strategy excess return",
        "execution": "close signal shifted to next session open",
        "primary_slippage_bps_per_side": 10.0,
        "stress_slippage_bps_per_side": 50.0,
        "evaluation_window": {"start": str(EVALUATION_START.date()), "end": str(EVALUATION_END.date())},
        "folds": FOLDS,
        "results": records,
        "ranking": ranking,
        "summary": {
            "strategies": len(records),
            "screening_passes": sum(row["verdict"] == "SCREENING_PASS" for row in records),
            "failures": sum(row["verdict"] == "FAIL" for row in records),
            "leader_strategy_id": ranking[0]["strategy_id"] if ranking else None,
            "leader_name": ranking[0]["name"] if ranking else None,
            "leader_verdict": ranking[0]["verdict"] if ranking else None,
        },
        "automatic_promotion": False,
        "paper_or_live_execution": False,
        "execution_authority": False,
        "source_code_auto_edit": False,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": payload["status"], **payload["summary"], "output": str(OUTPUT), "automatic_promotion": False, "execution_authority": False}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
