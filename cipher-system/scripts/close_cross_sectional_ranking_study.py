#!/usr/bin/env python3
"""Clustered-bootstrap close-out for the price-only ranking study.

Resampling is by same-origin/horizon cohort, never by individual ticker.  This
preserves the dependence that makes a cross-sectional ranking meaningful.
Outputs are research evidence only and cannot promote a model or strategy.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.research_platform.forecast_ranking import spearman  # noqa: E402
QUALITY = ROOT / "data" / "market_quality"
DOCS = ROOT / "docs"


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[round((len(ordered) - 1) * p)]


def cohort_metric(payload: dict, score: str, metric: str = "spearman_ic") -> list[float]:
    return [cohort["metrics"][score][metric] for cohort in payload["results"]["cohorts"] if cohort["metrics"].get(score) and cohort["metrics"][score][metric] is not None]


def paired_bootstrap(left: list[float], right: list[float], *, seed: int = 42, draws: int = 5000) -> dict:
    if len(left) != len(right) or not left:
        raise ValueError("paired cohort samples are required")
    differences = [a - b for a, b in zip(left, right)]
    rng = random.Random(seed)
    resamples = [mean(differences[rng.randrange(len(differences))] for _ in differences) for _ in range(draws)]
    return {"observed_mean_difference": mean(differences), "ci_95": [percentile(resamples, .025), percentile(resamples, .975)], "cohorts": len(differences), "draws": draws, "seed": seed}


def leave_one_out(rows: list[dict], score: str, dimension: str) -> dict[str, float | None]:
    """Mean cohort IC after omitting each origin or ticker, for sensitivity only."""
    values = sorted({str(row[dimension]) for row in rows})
    result = {}
    for value in values:
        retained = [row for row in rows if str(row[dimension]) != value]
        grouped: dict[tuple[str, int], list[dict]] = {}
        for row in retained:
            grouped.setdefault((row["origin"], int(row["horizon_sessions"])), []).append(row)
        ics = []
        for cohort in grouped.values():
            scores = [row["scores"][score] for row in cohort]
            realized = [row["realized_return"] for row in cohort]
            ic = spearman(scores, realized)
            if ic is not None:
                ics.append(ic)
        result[value] = mean(ics) if ics else None
    return result


def latest(pattern: str) -> Path:
    paths = sorted(QUALITY.glob(pattern))
    if not paths:
        raise FileNotFoundError(pattern)
    return paths[-1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Close out price-only cross-sectional ranking research.")
    parser.add_argument("--development", type=Path, default=latest("cross_sectional_ranking_development_*.json"))
    parser.add_argument("--holdout", type=Path, default=latest("cross_sectional_ranking_holdout_b_results_*.json"))
    args = parser.parse_args(argv)
    development = json.loads(args.development.read_text(encoding="utf-8"))
    holdout = json.loads(args.holdout.read_text(encoding="utf-8"))
    comparisons = {}
    for model in ("kronos", "timesfm", "equal_weight_rank_ensemble"):
        comparisons[model] = paired_bootstrap(cohort_metric(holdout, model), cohort_metric(holdout, "momentum_20"))
    verdict = {
        "kronos": "not_supported_for_ranking" if comparisons["kronos"]["observed_mean_difference"] <= 0 else "inconclusive",
        "timesfm": "not_supported_for_ranking" if comparisons["timesfm"]["observed_mean_difference"] <= 0 else "inconclusive",
        "equal_weight_rank_ensemble": "not_supported_for_ranking" if comparisons["equal_weight_rank_ensemble"]["observed_mean_difference"] <= 0 else "inconclusive",
        "promotion": "blocked_research_only",
        "reason": "Both frozen models underperform the pre-registered momentum_20 comparator on the sealed Holdout B mean Spearman IC; four dependent cohorts are insufficient for a positive capability claim.",
    }
    payload = {
        "schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "clustered-bootstrap close-out of price-only cross-sectional ranking study",
        "development_source": str(args.development), "holdout_b_source": str(args.holdout),
        "resampling_unit": "same-origin/horizon cross-sectional cohort", "metric": "spearman_ic",
        "comparisons_against_registered_momentum_20": comparisons, "verdict": verdict,
        "sensitivity": {model: {"leave_one_origin_mean_ic": leave_one_out(holdout["results"]["cases"], model, "origin"), "leave_one_ticker_mean_ic": leave_one_out(holdout["results"]["cases"], model, "ticker")} for model in ("kronos", "timesfm", "equal_weight_rank_ensemble")},
        "safety": {"volume_used": False, "full_volume_gate_changed": False, "holdout_a_accessed": False, "live_execution": False, "promotion_eligible": False},
    }
    QUALITY.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_out = QUALITY / f"cross_sectional_ranking_closeout_{stamp}.json"
    json_out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    DOCS.mkdir(parents=True, exist_ok=True)
    md_out = DOCS / "cross_sectional_forecast_ranking_study.md"
    md_out.write_text(
        "# Cross-Sectional Forecast Ranking Study\n\n"
        "## Scope\n\n"
        "This is price-only forecast research. It uses no volume, creates no portfolio weights or orders, and does not modify the full volume-reconciled market-data gate. Holdout A (2015) was not accessed.\n\n"
        "## Frozen Data\n\n"
        f"Development: `{args.development.name}` (six same-origin/horizon cohorts). Holdout B: `{args.holdout.name}` (four cohorts, 35 model cases). The Holdout B case roster was written before model loading.\n\n"
        "## Results\n\n"
        f"Development mean Spearman IC: Kronos {development['results']['aggregate']['kronos']['mean_spearman_ic']:+.3f}, TimesFM {development['results']['aggregate']['timesfm']['mean_spearman_ic']:+.3f}, momentum_20 {development['results']['aggregate']['momentum_20']['mean_spearman_ic']:+.3f}. "
        f"Holdout B mean Spearman IC: Kronos {holdout['results']['aggregate']['kronos']['mean_spearman_ic']:+.3f}, TimesFM {holdout['results']['aggregate']['timesfm']['mean_spearman_ic']:+.3f}, ensemble {holdout['results']['aggregate']['equal_weight_rank_ensemble']['mean_spearman_ic']:+.3f}, momentum_20 {holdout['results']['aggregate']['momentum_20']['mean_spearman_ic']:+.3f}.\n\n"
        "## Clustered Bootstrap\n\n"
        + "\n".join(f"- {name} minus momentum_20: observed {item['observed_mean_difference']:.3f}; 95% cohort-bootstrap CI [{item['ci_95'][0]:.3f}, {item['ci_95'][1]:.3f}] (n={item['cohorts']})." for name, item in comparisons.items())
        + "\n\n## Verdict\n\n"
        "Neither model is supported for cross-sectional ranking. This is a negative research result, not a model or strategy promotion. More independent market periods would be required before reassessment; the next study must reserve a new untouched period and keep this one sealed.\n",
        encoding="utf-8",
    )
    print(json.dumps({"json": str(json_out), "report": str(md_out), "verdict": verdict}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
