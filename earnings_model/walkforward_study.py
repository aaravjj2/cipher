"""Nested walk-forward validation for the earnings |gap| magnitude model.

The v2 audit (docs/audits/simple_morning_brief_and_earnings_v2_2026-08-23.md)
found exactly one earnings-model edge that survived an untouched holdout:
absolute-gap magnitude improved MAE ~12% over a naive baseline, while every
directional output was rejected. A single 60/20/20 split cannot show that a
selection *process* using only past data keeps finding this edge, so this module
asks the harder question the way `core/eod_option_walkforward.py` asks it of EOD
option patterns: could a walk-forward procedure have selected a magnitude model
that beat naive baselines on each next unseen month?

Methodology
-----------
* Uses the point-in-time dataset from ``earnings_model.model``
  (features are expanding per-symbol priors shifted one event back, so no event
  ever sees itself).
* Folds are chronological holdout months. For each month, only events reported
  strictly before that month minus an embargo window are training data.
* Candidate regressors (ridge, Huber gradient boosting — the production pair)
  are chosen by MAE on the most recent slice of the training window only; the
  holdout month judges, never selects. The selected candidate is then refit on
  the full training window, mirroring the production refit step.
* Naive baselines are computed from training-window information only:
  the training median |gap|, each symbol's own prior mean |gap|
  (a point-in-time feature), and the magnitude of the recent pre-earnings move
  as a prior-day-move proxy. No implied-move proxy exists: historical option
  IV was never captured locally, and that absence is carried as a blocker.
* The pass rule is the repo's own gap threshold
  (``model.MIN_GAP_MAE_IMPROVEMENT``, 5%): a candidate passes only if its
  pooled out-of-sample MAE beats the STRONGEST baseline's pooled MAE by at
  least that margin. Judging against the strongest baseline rather than the
  weakest is the same harshest-case discipline the EOD walk-forward applies to
  execution models.
* The verdict comes from ``core.research_envelope.verdict_from_blockers`` —
  the shared rule, not a local copy — so blockers plus a passing result read
  INCONCLUSIVE and never SELECTABLE.

Research only. Outcomes are underlying-price magnitudes: no historical NBBO,
option P&L, or IV series exists locally, so nothing here may be read as an
options-economics result.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(REPO_ROOT), str(REPO_ROOT / "cipher-system")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from core.research_envelope import Verdict, verdict_from_blockers  # noqa: E402

from .config import DB_PATH  # noqa: E402
from .model import FEATURE_COLS, MIN_GAP_MAE_IMPROVEMENT, build_feature_dataset  # noqa: E402

STUDY_ID = "earnings_gap_magnitude_walkforward"
DEFAULT_OUT = Path("/home/aarav/Aarav/cipher/runtime/data") / STUDY_ID

#: Calendar-day embargo between the last training report and the holdout month.
#: Covers the day-5 target horizon plus reporting lag; the gap target itself is
#: resolved at the next open, so this is deliberately conservative.
DEFAULT_EMBARGO_DAYS = 10

CANDIDATE_NAMES = ("ridge", "gradient_boosting")

BASELINE_NAMES = (
    "train_median_abs_gap",
    "symbol_prior_mean_abs_gap",
    "recent_move_proxy",
)

BLOCKERS: tuple[str, ...] = (
    "No historical NBBO, option premiums, or IV series exist locally, so outcomes "
    "are underlying-price magnitudes: this validates a size estimate, not an "
    "options P&L result, and fills remain underlying-proxy.",
    "No implied-move proxy is evaluable on history: option chains were never "
    "captured before 2026, so the strongest magnitude benchmark a real trader "
    "could quote cannot be scored here.",
    "Historical earnings dates were single-sourced (Yahoo) until the 2026-08 "
    "Nasdaq cross-check existed, so some historical events may be dated to the "
    "wrong session.",
)


def _regressor_candidates() -> Dict[str, Any]:
    """The production candidate pair, imported lazily to avoid sklearn cycles."""
    from sklearn.linear_model import Ridge
    from sklearn.ensemble import GradientBoostingRegressor
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    return {
        "ridge": Pipeline([("scaler", StandardScaler()), ("model", Ridge(alpha=10.0))]),
        "gradient_boosting": GradientBoostingRegressor(
            n_estimators=50, max_depth=2, loss="huber", random_state=42,
        ),
    }


def _mae(actual: np.ndarray, predicted: np.ndarray) -> Optional[float]:
    if len(actual) == 0:
        return None
    return float(np.mean(np.abs(actual - predicted)))


def _month_of(event_date: str) -> str:
    return str(event_date)[:7]


def prepare_dataset(
    df: Optional[pd.DataFrame] = None,
    *,
    db_path: Optional[str] = None,
) -> pd.DataFrame:
    """Point-in-time events with complete features/targets, oldest first."""
    if df is None:
        from .db import init_db

        conn = init_db(db_path)
        try:
            df = build_feature_dataset(conn)
        finally:
            conn.close()
    if df is None or df.empty:
        return pd.DataFrame()
    needed = FEATURE_COLS + ["symbol", "earnings_date", "target_abs_gap",
                             "prior_avg_abs_gap", "pre_5d_return_pct"]
    valid = df.dropna(subset=[c for c in needed if c in df.columns]).copy()
    valid["event_day"] = valid["earnings_date"].astype(str).str.slice(0, 10)
    return valid.sort_values("event_day").reset_index(drop=True)


def plan_folds(
    events: pd.DataFrame,
    *,
    embargo_days: int,
    min_train_events: int,
    min_holdout_events: int,
) -> List[Dict[str, Any]]:
    """Chronological monthly folds an expanding window can actually support."""
    folds: List[Dict[str, Any]] = []
    if events.empty:
        return folds
    months = sorted({_month_of(day) for day in events["event_day"]})
    for month in months:
        month_start = f"{month}-01"
        cutoff = (pd.Timestamp(month_start) - pd.Timedelta(days=embargo_days)).strftime("%Y-%m-%d")
        train = events[events["event_day"] < cutoff]
        month_end = str(pd.Period(month).end_time.date())
        holdout = events[
            (events["event_day"] >= month_start) & (events["event_day"] <= month_end)
        ]
        if len(train) < min_train_events or len(holdout) < min_holdout_events:
            continue
        folds.append({
            "holdout_month": month,
            "embargo_cutoff_day": cutoff,
            "train_events": int(len(train)),
            "holdout_events": int(len(holdout)),
        })
    return folds


def _baseline_predictions(
    train: pd.DataFrame,
    holdout: pd.DataFrame,
) -> Dict[str, tuple[np.ndarray, np.ndarray]]:
    """Naive |gap| forecasts built from training-window information only."""
    actual = holdout["target_abs_gap"].to_numpy(dtype=float)
    baselines: Dict[str, tuple[np.ndarray, np.ndarray]] = {}

    train_median = float(train["target_abs_gap"].median())
    baselines["train_median_abs_gap"] = (actual, np.full(len(holdout), train_median))

    prior_mean = pd.to_numeric(holdout["prior_avg_abs_gap"], errors="coerce").to_numpy(dtype=float)
    baselines["symbol_prior_mean_abs_gap"] = (actual, np.where(np.isnan(prior_mean), train_median, prior_mean))

    recent = pd.to_numeric(holdout["pre_5d_return_pct"], errors="coerce").to_numpy(dtype=float)
    recent = np.abs(recent)
    coverage = float(np.mean(~np.isnan(recent))) if len(recent) else 0.0
    filled = np.where(np.isnan(recent), train_median, recent)
    baselines["recent_move_proxy"] = (actual, filled)
    baselines["_recent_move_coverage"] = (np.array([coverage]), np.array([coverage]))
    return baselines


def run_study(
    df: Optional[pd.DataFrame] = None,
    *,
    db_path: Optional[str] = None,
    output_dir: Path = DEFAULT_OUT,
    embargo_days: int = DEFAULT_EMBARGO_DAYS,
    min_train_events: int = 400,
    min_holdout_events: int = 30,
    train_window_events: int = 0,
    write_artifacts: bool = True,
) -> Dict[str, Any]:
    """Run the walk-forward and return the study-of-record dict.

    ``train_window_events=0`` keeps the expanding window; a positive value makes
    it rolling, mirroring the EOD walk-forward's rolling variants. When local
    data cannot support even one honest fold the runner returns INSUFFICIENT_DATA
    with the specific capture gaps named — fabricating folds is not an outcome.
    """
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    events = prepare_dataset(df, db_path=db_path)

    insufficient_blockers: List[str] = []
    if events.empty:
        insufficient_blockers.append(
            f"no local historical earnings+price dataset available at {DB_PATH}; "
            "capture requires the earnings collector to run against the priced universe"
        )
    elif len(events) < max(min_train_events + min_holdout_events, 100):
        insufficient_blockers.append(
            f"only {len(events)} point-in-time events with complete features; a "
            f"walk-forward needs at least {min_train_events} training events plus "
            f"{min_holdout_events} holdout events per month"
        )

    folds = [] if insufficient_blockers else plan_folds(
        events,
        embargo_days=embargo_days,
        min_train_events=min_train_events,
        min_holdout_events=min_holdout_events,
    )
    if not insufficient_blockers and not folds:
        insufficient_blockers.append(
            f"no month satisfies the fold gates (train >= {min_train_events} events, "
            f"holdout >= {min_holdout_events}, embargo {embargo_days}d); capture more "
            "history or relax the gates explicitly"
        )

    if insufficient_blockers:
        result: Dict[str, Any] = {
            "study_id": STUDY_ID,
            "status": "INSUFFICIENT_DATA",
            "generated_at": generated_at,
            "observations": 0,
            "folds": [],
            # Kept present-but-empty so the corpus router can still adapt this
            # report: an unreadable gap is exactly what should be visible.
            "gap_fold_results": [],
            "verdict": verdict_from_blockers(insufficient_blockers, 0, passes=False).value,
            "evidence_tier": _evidence_tier(
                verdict_from_blockers(insufficient_blockers, 0, passes=False), True
            ),
            "blockers": list(insufficient_blockers),
            "notes": [
                "no walk-forward was run; fabricating folds from thin data is not an "
                "acceptable substitute"
            ],
            "cost_basis": "not-applicable:magnitude-claim-no-execution",
        }
        if write_artifacts:
            _write_artifacts(result, [], [], output_dir=output_dir)
        return result

    prediction_rows: List[Dict[str, Any]] = []
    fold_rows: List[Dict[str, Any]] = []

    for fold in folds:
        month = fold["holdout_month"]
        cutoff = fold["embargo_cutoff_day"]
        # Fresh estimator instances per fold: no state can leak between folds
        # through a reused model object.
        candidates = _regressor_candidates()
        train_all = events[events["event_day"] < cutoff]
        if train_window_events and train_window_events > 0:
            train_all = train_all.iloc[-int(train_window_events):]
        month_end = str(pd.Period(month).end_time.date())
        holdout = events[
            (events["event_day"] >= f"{month}-01") & (events["event_day"] <= month_end)
        ]

        # Selection slice: the most recent quarter of the training window, exactly
        # the role validation plays in the production split. The holdout month
        # never participates in selection.
        split = int(len(train_all) * 0.75)
        fit_slice = train_all.iloc[:split]
        select_slice = train_all.iloc[split:]

        selection_mae: Dict[str, float] = {}
        for name in CANDIDATE_NAMES:
            model = candidates[name]
            model.fit(fit_slice[FEATURE_COLS], fit_slice["target_abs_gap"])
            prediction = np.maximum(0.0, model.predict(select_slice[FEATURE_COLS]))
            selection_mae[name] = float(
                np.mean(np.abs(select_slice["target_abs_gap"].to_numpy(dtype=float) - prediction))
            )
        selected_name = min(selection_mae, key=lambda n: (selection_mae[n], n))

        # Refit on the full training window after selection, as production does.
        selected = candidates[selected_name]
        selected.fit(train_all[FEATURE_COLS], train_all["target_abs_gap"])
        model_prediction = np.maximum(0.0, selected.predict(holdout[FEATURE_COLS]))

        actual = holdout["target_abs_gap"].to_numpy(dtype=float)
        baselines = _baseline_predictions(train_all, holdout)
        recent_coverage = float(baselines["_recent_move_coverage"][0][0])

        row: Dict[str, Any] = {
            "holdout_month": month,
            "embargo_cutoff_day": cutoff,
            "selected_candidate": selected_name,
            "selection_mae_by_candidate": {
                n: round(v, 4) for n, v in sorted(selection_mae.items())
            },
            "train_events": int(len(train_all)),
            "holdout_events": int(len(holdout)),
            "model_mae_pct": round(_mae(actual, model_prediction), 4),
        }
        for baseline_name in BASELINE_NAMES:
            b_actual, b_pred = baselines[baseline_name]
            row[f"{baseline_name}_mae_pct"] = round(_mae(b_actual, b_pred), 4)
        row["recent_move_proxy_coverage"] = round(recent_coverage, 4)

        fold_rows.append(row)
        frame = pd.DataFrame({
            "holdout_month": month,
            "event_day": holdout["event_day"].to_numpy(),
            "symbol": holdout["symbol"].to_numpy(),
            "actual_abs_gap_pct": actual,
            "predicted_abs_gap_pct": model_prediction,
            "selected_candidate": selected_name,
        })
        for baseline_name in BASELINE_NAMES:
            frame[baseline_name] = baselines[baseline_name][1]
        prediction_rows.extend(frame.to_dict("records"))

    aggregate_rows: List[Dict[str, Any]] = []
    predictions_df = pd.DataFrame(prediction_rows)
    for name in CANDIDATE_NAMES:
        picked = predictions_df[predictions_df["selected_candidate"] == name]
        if picked.empty:
            continue
        actual = picked["actual_abs_gap_pct"].to_numpy(dtype=float)
        predicted = picked["predicted_abs_gap_pct"].to_numpy(dtype=float)
        row: Dict[str, Any] = {
            "candidate": name,
            "months_selected": int(picked["holdout_month"].nunique()),
            "evaluated_events": int(len(picked)),
            "oos_mae_pct": round(_mae(actual, predicted), 4),
        }
        baseline_maes = {}
        for baseline_name in BASELINE_NAMES:
            b_mae = _mae(actual, picked[baseline_name].to_numpy(dtype=float))
            baseline_maes[baseline_name] = round(b_mae, 4) if b_mae is not None else None
        row["baseline_oos_mae_pct"] = baseline_maes
        strongest = min((v for v in baseline_maes.values() if v is not None), default=None)
        if strongest:
            row["strongest_baseline"] = min(
                (k for k, v in baseline_maes.items() if v == strongest), default=None
            )
            row["improvement_vs_strongest_baseline_pct"] = round(
                (strongest - row["oos_mae_pct"]) / strongest * 100.0, 2
            )
        else:
            row["strongest_baseline"] = None
            row["improvement_vs_strongest_baseline_pct"] = None
        aggregate_rows.append(row)

    observations = max(
        (int(row.get("evaluated_events") or 0) for row in aggregate_rows), default=0
    )
    survives = any(
        (row.get("improvement_vs_strongest_baseline_pct") or 0.0)
        >= MIN_GAP_MAE_IMPROVEMENT * 100.0
        for row in aggregate_rows
    )

    blockers = list(BLOCKERS)
    low_coverage_months = [
        row["holdout_month"] for row in fold_rows
        if row.get("recent_move_proxy_coverage", 0.0) < 0.95
    ]
    if low_coverage_months:
        blockers.append(
            "the recent-move baseline lacks pre-5d returns for some events in "
            f"{len(low_coverage_months)} holdout month(s); those rows fall back to the "
            "training-median baseline there"
        )

    verdict = verdict_from_blockers(blockers, observations, passes=survives)

    best_improvement = max(
        (
            (row.get("improvement_vs_strongest_baseline_pct") or float("-inf"))
            for row in aggregate_rows
        ),
        default=float("-inf"),
    )
    notes = [
        f"folds: {len(fold_rows)} monthly holdouts, embargo {embargo_days} days, "
        + ("rolling window" if train_window_events else "expanding window"),
        f"pass rule: pooled MAE must beat the strongest naive baseline by at least "
        f"{MIN_GAP_MAE_IMPROVEMENT * 100:.0f}% (model.MIN_GAP_MAE_IMPROVEMENT)",
        (
            f"best pooled improvement vs strongest baseline: {best_improvement:.2f}%"
            if best_improvement != float("-inf")
            else "no candidate produced pooled out-of-sample predictions"
        ),
        "judged against the strongest baseline, not the weakest, mirroring the EOD "
        "walk-forward's harshest-execution rule",
    ]
    if survives:
        notes.append(
            "a candidate cleared the improvement threshold; blockers still prevent a "
            "SELECTABLE reading because outcomes are underlying-proxy magnitudes"
        )

    result = {
        "study_id": STUDY_ID,
        "status": "COMPLETE",
        "generated_at": generated_at,
        "analysis_start": str(events["event_day"].iloc[0]),
        "analysis_end": str(events["event_day"].iloc[-1]),
        "observations": observations,
        "folds": [
            {
                "holdout_month": fold["holdout_month"],
                "train_events": fold["train_events"],
                "holdout_events": fold["holdout_events"],
                "embargo_cutoff_day": fold["embargo_cutoff_day"],
            }
            for fold in fold_rows
        ],
        "embargo_days": embargo_days,
        "train_window_events": train_window_events,
        "candidates": list(CANDIDATE_NAMES),
        "baselines": list(BASELINE_NAMES),
        "gap_fold_results": fold_rows,
        "aggregate_results": aggregate_rows,
        "verdict": verdict.value,
        "evidence_tier": _evidence_tier(verdict, bool(blockers)),
        "blockers": blockers,
        "notes": notes,
        "cost_basis": "not-applicable:magnitude-claim-no-execution",
    }
    if write_artifacts:
        _write_artifacts(result, fold_rows, prediction_rows, output_dir=output_dir)
    return result


def _evidence_tier(verdict: Verdict, has_blockers: bool) -> int:
    """Mirror of research_envelope.ResearchResult.evidence_tier.

    The envelope adapter recomputes the authoritative tier from this report;
    carrying the same rule here keeps the standalone JSON readable without a
    second interpretation of the ladder.
    """
    if has_blockers and verdict is not Verdict.REJECTED:
        return 5
    if verdict is Verdict.INCONCLUSIVE:
        return 3
    if verdict is Verdict.REJECTED:
        return 4
    return 2  # selectable on assumed costs; unreachable while blockers exist


def _write_artifacts(
    summary: Mapping[str, Any],
    fold_rows: Sequence[Mapping[str, Any]],
    prediction_rows: Sequence[Mapping[str, Any]],
    *,
    output_dir: Path,
) -> Dict[str, str]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "report_json": str(output_dir / "report.json"),
        "fold_results_csv": str(output_dir / "gap_fold_results.csv"),
        "oos_predictions_csv": str(output_dir / "oos_predictions.csv"),
        "report_markdown": str(output_dir / "report.md"),
    }
    payload = dict(summary)
    payload["files"] = paths
    (output_dir / "report.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8"
    )
    _write_csv(output_dir / "gap_fold_results.csv", fold_rows)
    _write_csv(output_dir / "oos_predictions.csv", prediction_rows)
    (output_dir / "report.md").write_text(
        _build_markdown(payload), encoding="utf-8"
    )
    return paths


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    flattened: List[Dict[str, Any]] = []
    for row in rows:
        flat = {}
        for key, value in row.items():
            flat[key] = json.dumps(value, sort_keys=True) if isinstance(value, dict) else value
        flattened.append(flat)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(flattened[0].keys()))
        writer.writeheader()
        writer.writerows(flattened)


def _build_markdown(summary: Mapping[str, Any]) -> str:
    lines = [
        f"# {summary.get('study_id', STUDY_ID)} — study of record",
        "",
        f"Generated: `{summary.get('generated_at')}` | Status: `{summary.get('status')}`",
        f"Verdict: **{str(summary.get('verdict')).upper()}** "
        f"(tier {summary.get('evidence_tier', 'n/a')}) | Observations: {summary.get('observations')}",
        "",
        "## Blockers",
        "",
    ]
    lines.extend(f"- {blocker}" for blocker in summary.get("blockers", ()))
    lines.extend(["", "## Aggregate out-of-sample results", ""])
    lines.append("| candidate | months | events | OOS MAE % | strongest baseline | improvement % |")
    lines.append("|---|---|---|---|---|---|")
    for row in summary.get("aggregate_results", ()):
        lines.append(
            f"| {row.get('candidate')} | {row.get('months_selected')} | "
            f"{row.get('evaluated_events')} | {row.get('oos_mae_pct')} | "
            f"{row.get('strongest_baseline')} ({(row.get('baseline_oos_mae_pct') or {}).get(str(row.get('strongest_baseline')))}) | "
            f"{row.get('improvement_vs_strongest_baseline_pct')} |"
        )
    lines.extend(["", "## Notes", ""])
    lines.extend(f"- {note}" for note in summary.get("notes", ()))
    lines.append("")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=DB_PATH, help="Path to the local earnings sqlite book")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUT))
    parser.add_argument("--embargo-days", type=int, default=DEFAULT_EMBARGO_DAYS)
    parser.add_argument("--min-train-events", type=int, default=400)
    parser.add_argument("--min-holdout-events", type=int, default=30)
    parser.add_argument(
        "--train-window-events", type=int, default=0,
        help="Rolling window size in events; 0 keeps the expanding window",
    )
    parser.add_argument("--no-artifacts", action="store_true", help="Print the result without writing files")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    result = run_study(
        db_path=None if str(args.db) == str(DB_PATH) else str(args.db),
        output_dir=Path(args.output_dir),
        embargo_days=args.embargo_days,
        min_train_events=args.min_train_events,
        min_holdout_events=args.min_holdout_events,
        train_window_events=args.train_window_events,
        write_artifacts=not args.no_artifacts,
    )
    print(json.dumps({
        "study_id": result.get("study_id"),
        "status": result.get("status"),
        "folds": len(result.get("folds", [])),
        "observations": result.get("observations"),
        "verdict": result.get("verdict"),
        "blockers": result.get("blockers"),
    }, indent=2, default=str))
    return 0 if result.get("status") == "COMPLETE" else 3


if __name__ == "__main__":
    raise SystemExit(main())
