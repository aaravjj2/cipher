#!/usr/bin/env python3
"""Construct and freeze Holdout C eligibility before any ranking outcomes."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.research_platform.market_quality import require_holdout_c_cohort

QUALITY = ROOT / "data" / "market_quality"
GOV = ROOT / "data" / "governance"
MIN_TICKERS, CONTEXT, HORIZON, MIN_ORIGINS = 8, 32, 20, 12


def construct_candidate_blocks(
    all_days: list[str],
    eligible: dict[str, list[str]],
    *,
    minimum_tickers: int = MIN_TICKERS,
    context_sessions: int = CONTEXT,
    outcome_sessions: int = HORIZON,
) -> list[dict]:
    """Build deterministic non-overlapping windows with one ticker set throughout.

    A day is part of a candidate block only when it is individually eligible.
    Each accepted origin then requires at least ``minimum_tickers`` in the
    intersection across its complete context and outcome window.
    """

    blocks: list[list[str]] = []
    current: list[str] = []
    for day in all_days:
        if day in eligible:
            current.append(day)
        else:
            if current:
                blocks.append(current)
            current = []
    if current:
        blocks.append(current)

    findings: list[dict] = []
    window_size = context_sessions + outcome_sessions
    for block in blocks:
        origin_windows = []
        offset = 0
        while offset + window_size <= len(block):
            window = block[offset : offset + window_size]
            common_tickers = sorted(set.intersection(*(set(eligible[day]) for day in window)))
            if len(common_tickers) >= minimum_tickers:
                origin_windows.append(
                    {
                        "context_start": window[0],
                        "origin": window[context_sessions - 1],
                        "outcome_start": window[context_sessions],
                        "outcome_end": window[-1],
                        "tickers": common_tickers,
                    }
                )
                offset += window_size
            else:
                offset += 1
        if origin_windows:
            findings.append(
                {
                    "start": block[0],
                    "end": block[-1],
                    "sessions": len(block),
                    "minimum_common_tickers": min(len(item["tickers"]) for item in origin_windows),
                    "strict_independent_origins": len(origin_windows),
                    "origin_windows": origin_windows,
                }
            )
    return findings


def build_cohort_payload(
    scope: dict,
    *,
    scope_artifact: str,
    period: str,
    created_at: datetime | None = None,
) -> dict:
    """Run the original 52-session construction against an explicit scope."""

    all_days = sorted({row["date"] for row in scope["daily_results"]})
    eligible = {
        row["date"]: sorted(row["tickers"])
        for row in scope["common_eligible_by_day"]
        if row["count"] >= MIN_TICKERS
    }
    # A strict independent case consumes a 32-session context and a disjoint
    # 20-session outcome. The same ticker set must be eligible throughout all
    # 52 sessions; origin-day membership alone is not sufficient.
    findings = construct_candidate_blocks(all_days, eligible)
    best = max(
        findings,
        key=lambda item: (
            item["strict_independent_origins"],
            item["minimum_common_tickers"],
            item["sessions"],
        ),
        default=None,
    )
    cohort_gate = require_holdout_c_cohort(
        source_count=1,
        common_tickers=(best["minimum_common_tickers"] if best else 0),
        strict_independent_origins=(best["strict_independent_origins"] if best else 0),
    )
    passed = cohort_gate["eligible"]
    observed_at = (created_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return {
        "schema_version": 1,
        "created_at": observed_at.isoformat(),
        "scope_artifact": scope_artifact,
        "holdout_period": period,
        "requirements": {
            "minimum_common_tickers": MIN_TICKERS,
            "context_sessions": CONTEXT,
            "outcome_horizon_sessions": HORIZON,
            "minimum_strict_independent_origins": MIN_ORIGINS,
        },
        "candidate_blocks": findings,
        "selected_block": best,
        "cohort_gate": cohort_gate,
        "pass": passed,
        "cohort_frozen_before_ranking_outcomes": passed,
        "ranking_outcomes_evaluated": False,
        "full_volume_reconciled_gate_changed": False,
        "volume_features_or_evaluation": False,
        "live_execution": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scope", help="Explicit price-only scope artifact.")
    parser.add_argument("--period", default="2017-01 through 2019-12")
    args = parser.parse_args()
    scope_path = (
        Path(args.scope)
        if args.scope
        else sorted(QUALITY.glob("alpaca_holdout_c_price_only_scope_*.json"))[-1]
    )
    scope = json.loads(scope_path.read_text(encoding="utf-8"))
    payload = build_cohort_payload(
        scope,
        scope_artifact=str(scope_path),
        period=args.period,
    )
    GOV.mkdir(parents=True, exist_ok=True)
    output = GOV / f"holdout_c_alpaca_cohort_construction_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "path": str(output),
                "pass": payload["pass"],
                "selected_block": payload["selected_block"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
