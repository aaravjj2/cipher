#!/usr/bin/env python3
"""Build a latest-result matrix across Cipher's four equity research periods.

Candidate ID, not StrategySpec ID, is the cross-period identity because each
period may use a distinct preregistered walk-forward policy and therefore a
distinct immutable StrategySpec.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "data" / "governance" / "research_registry.sqlite"
OUTPUT = ROOT / "data" / "governance" / "cross_period_strategy_matrix.json"
DATASETS = {
    "locked_2016_2019": "ds_fb1e8d9aeb51f12407b08123",
    "phase3_2020_2022": "ds_532bf7c42462c24a7c1a0a1f",
    "original_2023_2025": "ds_380c76da95f0c3787529c6b8",
    "locked_2026_ytd": "ds_f20f2e15e7d1041ce6a1858d",
}


def compact_result(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    result = json.loads(row["result_json"] or "{}")
    metrics = result.get("metrics") or {}
    tests = result.get("statistical_tests") or {}
    quality = result.get("quality_checks") or {}
    return {
        "strategy_id": row["strategy_id"],
        "experiment_id": row["experiment_id"],
        "verdict": row["verdict"],
        "completed_at": row["completed_at"],
        "total_return_pct": metrics.get("total_return_pct"),
        "annualized_return_pct": metrics.get("annualized_return_pct"),
        "profit_factor": metrics.get("profit_factor"),
        "trade_count": metrics.get("trade_count"),
        "maximum_drawdown_pct": metrics.get("maximum_drawdown_pct"),
        "holm_adjusted_p_value": tests.get("holm_adjusted_p_value"),
        "walk_forward_passed": quality.get("walk_forward_passed"),
        "best_trade_exclusion_passed": quality.get("best_trade_exclusion_passed"),
    }


def build_matrix(
    *,
    registry_path: str | Path = REGISTRY,
    output_path: str | Path = OUTPUT,
) -> dict[str, Any]:
    registry_path = Path(registry_path)
    output_path = Path(output_path)
    with sqlite3.connect(registry_path) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute(
            """
            select e.dataset_id,e.strategy_id,e.experiment_id,e.verdict,e.completed_at,e.result_json,
                   s.payload_json
            from experiments e
            join strategies s on s.strategy_id=e.strategy_id
            where e.status='COMPLETED'
              and e.dataset_id in ({placeholders})
              and s.name like 'autonomous_price_only_%'
            order by e.completed_at
            """.format(placeholders=",".join("?" for _ in DATASETS)),
            tuple(DATASETS.values()),
        ).fetchall()
    dataset_names = {value: key for key, value in DATASETS.items()}
    candidates: dict[str, dict[str, Any]] = {}
    for row in rows:
        spec = json.loads(row["payload_json"])
        signal = spec.get("signal_rule") or {}
        candidate_id = str(signal.get("candidate_id") or "")
        if not candidate_id:
            continue
        item = candidates.setdefault(
            candidate_id,
            {
                "candidate_id": candidate_id,
                "family": signal.get("family"),
                "parameters": signal.get("parameters"),
                "parent_candidate_id": signal.get("parent_candidate_id"),
                "periods": {name: None for name in DATASETS},
            },
        )
        period = dataset_names[str(row["dataset_id"])]
        existing = item["periods"].get(period)
        if existing is None or str(row["completed_at"]) >= str(existing["completed_at"]):
            item["periods"][period] = compact_result(row)

    matrix = []
    for item in candidates.values():
        periods = item["periods"]
        passed = [name for name, value in periods.items() if value and value["verdict"] == "PASS"]
        tested = [name for name, value in periods.items() if value]
        matrix.append(
            {
                **item,
                "tested_periods": tested,
                "passed_periods": passed,
                "pass_count": len(passed),
                "tested_count": len(tested),
                "passes_all_tested_periods": bool(tested and len(passed) == len(tested)),
            }
        )
    matrix.sort(key=lambda row: (row["pass_count"], row["tested_count"], row["candidate_id"]), reverse=True)
    payload = {
        "schema_version": 2,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "completed",
        "identity_key": "candidate_id",
        "datasets": DATASETS,
        "summary": {
            "candidates": len(matrix),
            "period_count": len(DATASETS),
            "tested_all_three": sum(row["tested_count"] >= 3 for row in matrix),
            "passed_all_three": sum(row["pass_count"] >= 3 for row in matrix),
            "tested_all_four": sum(row["tested_count"] == 4 for row in matrix),
            "passed_all_four": sum(row["pass_count"] == 4 for row in matrix),
            "passed_at_least_two": sum(row["pass_count"] >= 2 for row in matrix),
            "passed_locked_validation": sum("locked_2016_2019" in row["passed_periods"] for row in matrix),
            "passed_phase3": sum("phase3_2020_2022" in row["passed_periods"] for row in matrix),
            "passed_original": sum("original_2023_2025" in row["passed_periods"] for row in matrix),
            "passed_2026_ytd": sum("locked_2026_ytd" in row["passed_periods"] for row in matrix),
        },
        "multi_period_leaders": [row for row in matrix if row["pass_count"] >= 2],
        "locked_validation_passes": [row for row in matrix if "locked_2016_2019" in row["passed_periods"]],
        "matrix": matrix,
        "automatic_promotion": False,
        "paper_or_live_execution": False,
        "execution_authority": False,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    payload = build_matrix()
    print(json.dumps({
        "status": payload["status"],
        **payload["summary"],
        "multi_period_leaders": [
            {
                "candidate_id": row["candidate_id"],
                "family": row["family"],
                "parameters": row["parameters"],
                "tested_periods": row["tested_periods"],
                "passed_periods": row["passed_periods"],
            }
            for row in payload["multi_period_leaders"]
        ],
        "output": str(OUTPUT),
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
