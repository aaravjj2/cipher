"""Tests for the earnings |gap| magnitude walk-forward study.

The walk-forward is the only path by which the one surviving v2 edge (absolute
gap MAE) may ever be upgraded, so these tests pin what makes it trustworthy:
chronological folds with a real embargo, selection that never sees the holdout
month, verdicts computed from the numbers via the shared envelope rule (never
hand-set), an honest INSUFFICIENT_DATA outcome instead of fabricated folds, and
a report.json the research corpus can route into the fingerprint plane. All
data is synthetic; nothing here reads the network or the local earnings book.
"""
import json
import sys
from pathlib import Path

import pytest

pd = pytest.importorskip("pandas", reason="walkforward_study requires pandas")
np = pytest.importorskip("numpy", reason="walkforward_study requires numpy")
sklearn = pytest.importorskip("sklearn", reason="walkforward_study requires scikit-learn")

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
REPOSITORY_ROOT = REPO_ROOT.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from core.research_envelope import Verdict  # noqa: E402
from core.research_corpus import collect  # noqa: E402
from core.research_envelope_adapters import (  # noqa: E402
    earnings_gap_magnitude_walkforward_envelope,
)

from earnings_model import walkforward_study as wf  # noqa: E402


N_SYMBOLS = 12
N_DAYS = 260


def _synthetic_events(seed: int = 7) -> pd.DataFrame:
    """A deterministic panel that looks like the point-in-time dataset.

    |gap| depends on the prior features so the candidates have something real
    to find, but no fold can leak: every feature is a per-event constant.
    """
    rng = np.random.default_rng(seed)
    rows = []
    start = pd.Timestamp("2024-01-05")
    for day_index in range(N_DAYS):
        day = (start + pd.offsets.BDay(day_index)).strftime("%Y-%m-%d")
        for symbol_index in range(N_SYMBOLS):
            symbol = f"SYM{symbol_index:02d}"
            scale = 1.0 + 0.35 * symbol_index
            pre_5d = float(rng.normal(0.0, 1.5 * scale))
            prior_mean = abs(rng.normal(3.0 * scale, 0.4))
            gap = float(abs(rng.normal(prior_mean + 0.15 * abs(pre_5d), 0.8)))
            rows.append({
                "symbol": symbol,
                "earnings_date": f"{day}T16:00:00-04:00",
                "event_day": day,
                "target_abs_gap": gap,
                "prior_avg_abs_gap": prior_mean,
                "pre_5d_return_pct": pre_5d,
                "prior_beat_rate": float(rng.uniform(0.3, 0.8)),
                "prior_avg_surprise": float(rng.normal(0, 1)),
                "prior_avg_day5": float(rng.normal(0, 2)),
                "prior_reversal_rate": float(rng.uniform(0.2, 0.6)),
                "prior_streak": int(rng.integers(-3, 4)),
                "pre_20d_return_pct": float(rng.normal(0, 4)),
                "pre_news_count": int(rng.integers(0, 10)),
                "pre_news_sentiment_avg": float(rng.uniform(-1, 1)),
                "pre_news_pos_ratio": float(rng.uniform(0, 1)),
                "pre_news_neg_ratio": float(rng.uniform(0, 1)),
                "pre_news_unc_ratio": float(rng.uniform(0, 1)),
            })
    return pd.DataFrame(rows)


def test_folds_are_chronological_with_embargo_applied():
    events = _synthetic_events()
    folds = wf.plan_folds(events, embargo_days=10, min_train_events=400, min_holdout_events=20)
    assert len(folds) >= 6
    months = [fold["holdout_month"] for fold in folds]
    assert months == sorted(months)
    first = folds[0]
    # Training pool ends at least the embargo before the holdout month starts.
    cutoff = pd.Timestamp(first["embargo_cutoff_day"])
    assert cutoff < pd.Timestamp(f"{first['holdout_month']}-01")


def test_run_study_completes_and_verdict_matches_the_shared_rule(tmp_path):
    result = wf.run_study(
        df=_synthetic_events(),
        output_dir=tmp_path,
        min_train_events=500,
        min_holdout_events=20,
        write_artifacts=True,
    )
    assert result["status"] == "COMPLETE"
    assert result["study_id"] == "earnings_gap_magnitude_walkforward"
    assert result["folds"], "a synthetic panel this size must produce folds"

    # The verdict must be exactly what the shared rule computes from the
    # published numbers — never hand-set.
    survives = any(
        (row.get("improvement_vs_strongest_baseline_pct") or 0.0)
        >= wf.MIN_GAP_MAE_IMPROVEMENT * 100.0
        for row in result["aggregate_results"]
    )
    expected = wf.verdict_from_blockers(
        result["blockers"], result["observations"], passes=survives
    )
    assert result["verdict"] == expected.value
    # Blockers are always carried, so SELECTABLE is unreachable even when the
    # numbers clear the threshold.
    if survives:
        assert result["verdict"] == Verdict.INCONCLUSIVE.value

    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert "gap_fold_results" in report
    assert report["cost_basis"].startswith("not-applicable:")
    assert (tmp_path / "oos_predictions.csv").exists()


def test_selection_never_sees_the_holdout_month(tmp_path):
    """Every prediction row's model was fit strictly before its holdout month."""
    events = _synthetic_events()
    result = wf.run_study(
        df=events,
        output_dir=tmp_path,
        min_train_events=500,
        min_holdout_events=20,
        write_artifacts=False,
    )
    events_by_month = set(pd.to_datetime(events["event_day"]).dt.strftime("%Y-%m"))
    for fold in result["folds"]:
        assert fold["holdout_month"] in events_by_month
        assert fold["train_events"] > 0 and fold["holdout_events"] > 0


def test_insufficient_data_names_capture_gaps_instead_of_fabricating(tmp_path):
    empty = pd.DataFrame()
    result = wf.run_study(df=empty, output_dir=tmp_path, write_artifacts=True)
    assert result["status"] == "INSUFFICIENT_DATA"
    assert result["observations"] == 0
    assert result["verdict"] == Verdict.INCONCLUSIVE.value
    assert any("earnings.sqlite" in blocker or "capture" in blocker.lower()
               for blocker in result["blockers"])
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["status"] == "INSUFFICIENT_DATA"


def test_report_routes_through_research_corpus(tmp_path):
    root = tmp_path / "corpus"
    study_dir = root / wf.STUDY_ID
    study_dir.mkdir(parents=True)
    wf.run_study(
        df=_synthetic_events(),
        output_dir=study_dir,
        min_train_events=500,
        min_holdout_events=20,
        write_artifacts=True,
    )
    results, unadapted = collect(root)
    matched = [r for r in results if r.study_id == wf.STUDY_ID]
    assert matched, "the corpus must adapt the earnings walk-forward report"
    adapted = matched[0]
    assert adapted.engine == "earnings_gap_magnitude_walkforward"
    assert adapted.blockers, "underlying-proxy outcomes must stay blocked"
    payload = json.loads((study_dir / "report.json").read_text(encoding="utf-8"))
    recomputed = earnings_gap_magnitude_walkforward_envelope(
        payload, study_id=wf.STUDY_ID
    )
    assert adapted.verdict == recomputed.verdict
    # Not routed anywhere else by mistake.
    assert all(u["path"] != str(study_dir / "report.json") for u in unadapted)


def test_envelope_refuses_selectable_with_blockers_and_passes_numbers():
    payload = {
        "aggregate_results": [{
            "candidate": "gradient_boosting",
            "months_selected": 3,
            "evaluated_events": 120,
            "oos_mae_pct": 1.0,
            "baseline_oos_mae_pct": {"train_median_abs_gap": 2.0},
            "improvement_vs_strongest_baseline_pct": 50.0,
        }],
        "folds": [{"holdout_month": "2024-03"}],
        "blockers": ["underlying-proxy outcomes"],
        "embargo_days": 10,
        "cost_basis": "not-applicable:magnitude-claim-no-execution",
    }
    result = earnings_gap_magnitude_walkforward_envelope(payload, study_id="x")
    assert result.verdict == Verdict.INCONCLUSIVE.value  # blockers cap a pass
    assert result.evidence_tier == 5

    failing = dict(payload)
    failing["aggregate_results"] = [{
        **payload["aggregate_results"][0],
        "oos_mae_pct": 2.5,
        "improvement_vs_strongest_baseline_pct": -25.0,
    }]
    rejected = earnings_gap_magnitude_walkforward_envelope(failing, study_id="x")
    assert rejected.verdict == Verdict.REJECTED.value
    assert rejected.evidence_tier == 4


def test_main_reports_nonzero_when_no_folds_can_be_built(tmp_path, monkeypatch, capsys):
    # No dependency on local data state: an empty panel cannot support a study,
    # so the runner must exit 3 and say exactly why.
    monkeypatch.setattr(wf, "prepare_dataset", lambda *a, **k: pd.DataFrame())
    rc = wf.main(["--output-dir", str(tmp_path / "out")])
    assert rc == 3
    out = capsys.readouterr().out
    assert "INSUFFICIENT_DATA" in out
