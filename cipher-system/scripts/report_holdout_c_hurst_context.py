#!/usr/bin/env python3
"""Describe, but never select on, Hurst context for the frozen Holdout C block."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from hurst import compute_Hc

ROOT = Path(__file__).resolve().parents[1]
QUALITY = ROOT / "data" / "market_quality"
GOV = ROOT / "data" / "governance"


def label(value: float) -> str:
    if value > 0.55:
        return "persistent_descriptive_only"
    if value < 0.45:
        return "mean_reverting_descriptive_only"
    return "near_random_walk_descriptive_only"


def main() -> None:
    scope_path = sorted(QUALITY.glob("alpaca_holdout_c_price_only_scope_*.json"))[-1]
    cohort_path = sorted(GOV.glob("holdout_c_alpaca_cohort_construction_*.json"))[-1]
    scope, cohort = json.loads(scope_path.read_text(encoding="utf-8")), json.loads(cohort_path.read_text(encoding="utf-8"))
    block = cohort.get("selected_block")
    if not block:
        raise SystemExit("no selected Holdout C block")
    output = []
    for ticker in sorted({row["ticker"] for row in scope["daily_results"]}):
        rows = [row for row in scope["daily_results"] if row["ticker"] == ticker and block["start"] <= row["date"] <= block["end"] and row["price_only_eligible"]]
        if len(rows) < 100:
            continue
        value, constant, _ = compute_Hc([float(row["close"]) for row in rows], kind="price", simplified=True)
        output.append({"ticker": ticker, "sessions": len(rows), "hurst": float(value), "constant": float(constant), "label": label(float(value))})
    payload = {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(), "tool": {"package": "hurst", "version": "0.0.5", "method": "compute_Hc(kind=price, simplified=true)"},
               "scope_artifact": str(scope_path), "cohort_artifact": str(cohort_path), "block": block,
               "results": output,
               "interpretation": "Hurst values are descriptive regime tags only. They were computed after the frozen block was already selected and cannot change source choice, ticker membership, origins, gates, outcomes, or promotion.",
               "ranking_outcomes_evaluated": False, "volume_used": False, "live_execution": False}
    output_path = QUALITY / f"holdout_c_hurst_context_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"path": str(output_path), "tickers": len(output)}, indent=2))


if __name__ == "__main__":
    main()
