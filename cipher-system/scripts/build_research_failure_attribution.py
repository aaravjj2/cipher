#!/usr/bin/env python3
"""Build gate-level diagnostics across auxiliary strategy research branches."""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
GOV = ROOT / "data" / "governance"
REGIME = GOV / "regime_allocator_research.json"
FACTOR = GOV / "factor_rotation_research.json"
OUTPUT = GOV / "research_failure_attribution.json"


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def branch_diagnostics(name: str, payload: dict[str, Any]) -> dict[str, Any]:
    rows = [row for row in payload.get("results", []) if isinstance(row, dict)]
    failures = Counter()
    categories = Counter()
    for row in rows:
        for gate in row.get("gate_failures", []) or []:
            failures[str(gate)] += 1
            text = str(gate)
            if "significance" in text or "p_value" in text:
                categories["statistical_confidence"] += 1
            elif "drawdown" in text or "profit_factor" in text:
                categories["risk_quality"] += 1
            elif "benchmark" in text or "excess" in text:
                categories["benchmark_consistency"] += 1
            elif "category" in text or "subuniverse" in text:
                categories["universe_robustness"] += 1
            elif "fold" in text:
                categories["temporal_consistency"] += 1
            elif "stress" in text or "turnover" in text:
                categories["cost_robustness"] += 1
            else:
                categories["other"] += 1
    near_misses = sorted(
        [
            {
                "id": row.get("strategy_id") or row.get("allocator_id"),
                "name": row.get("name"),
                "verdict": row.get("verdict"),
                "gate_failures": list(row.get("gate_failures") or []),
                "total_return_pct": (row.get("metrics") or {}).get("total_return_pct"),
                "strategy_excess_return_pct": (row.get("metrics") or {}).get("strategy_excess_return_pct"),
                "maximum_drawdown_pct": (row.get("metrics") or {}).get("maximum_drawdown_pct"),
                "holm_adjusted_p_value": row.get("holm_adjusted_p_value"),
                "positive_excess_folds": row.get("positive_excess_folds"),
                "return_path_hash": row.get("return_path_hash"),
            }
            for row in rows
            if 0 < len(row.get("gate_failures") or []) <= 3
        ],
        key=lambda row: (
            len(row["gate_failures"]),
            float(row.get("strategy_excess_return_pct") or -1e9),
            -float(row.get("maximum_drawdown_pct") or 1e9),
        ),
    )[:10]
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    return {
        "branch": name,
        "status": payload.get("status"),
        "family_size": payload.get("strategy_family_size") or payload.get("allocator_family_size") or summary.get("strategies") or summary.get("allocator_specs"),
        "effective_hypothesis_count": payload.get("effective_hypothesis_count"),
        "return_path_alias_group_count": len(payload.get("return_path_aliases") or {}),
        "screening_passes": summary.get("screening_passes"),
        "failures": summary.get("failures"),
        "leader_name": summary.get("leader_name"),
        "leader_verdict": summary.get("leader_verdict"),
        "gate_failure_counts": dict(failures.most_common()),
        "failure_category_counts": dict(categories.most_common()),
        "near_misses": near_misses,
        "automatic_promotion": False,
        "execution_authority": False,
    }


def main() -> int:
    regime = read_json(REGIME)
    factor = read_json(FACTOR)
    branches = {
        "regime_allocator": branch_diagnostics("regime_allocator", regime),
        "factor_rotation": branch_diagnostics("factor_rotation", factor),
    }
    aggregate_gates = Counter()
    aggregate_categories = Counter()
    for branch in branches.values():
        aggregate_gates.update(branch["gate_failure_counts"])
        aggregate_categories.update(branch["failure_category_counts"])
    dominant = aggregate_categories.most_common(1)
    payload = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "completed" if regime and factor else "partial_missing_branch",
        "branches": branches,
        "aggregate_gate_failure_counts": dict(aggregate_gates.most_common()),
        "aggregate_failure_category_counts": dict(aggregate_categories.most_common()),
        "dominant_failure_category": dominant[0][0] if dominant else None,
        "conclusion": (
            "No auxiliary branch currently clears the complete benchmark, risk, statistical, and robustness contract. Future work should add new observations or genuinely distinct hypotheses rather than relax gates."
        ),
        "next_evidence_dependency": (
            "Additional out-of-sample sessions and independent universes are more valuable than further local parameter descendants."
        ),
        "automatic_promotion": False,
        "paper_or_live_execution": False,
        "execution_authority": False,
        "source_code_auto_edit": False,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "dominant_failure_category": payload["dominant_failure_category"],
        "regime_near_misses": len(branches["regime_allocator"]["near_misses"]),
        "factor_near_misses": len(branches["factor_rotation"]["near_misses"]),
        "output": str(OUTPUT),
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
