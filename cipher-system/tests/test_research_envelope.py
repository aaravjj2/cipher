"""The envelope's invariants, and proof it survives contact with two real lab reports."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.research_envelope import (
    UNKNOWN,
    Candidate,
    Metric,
    Provenance,
    ResearchResult,
    Sample,
    Verdict,
    current_commit,
    rank,
    verdict_from_blockers,
)
from core.research_envelope_adapters import (
    eod_option_walkforward_envelope,
    wheel_engine_envelope,
    WHEEL_UNCOSTED,
    eod_pattern_lab_envelope,
    wheel_parameter_lab_envelope,
)

RUNTIME = Path("/home/aarav/Aarav/cipher/runtime/data")
WHEEL_REPORT = RUNTIME / "leveraged_etf_wheel" / "parameter_lab_2026" / "report.json"
EOD_REPORT = RUNTIME / "eod_pattern_lab" / "report.json"


def _result(**overrides) -> ResearchResult:
    base = dict(
        study_id="s",
        engine="e",
        verdict=Verdict.SELECTABLE,
        sample=Sample(observations=10),
        provenance=Provenance(cost_basis="measured:median"),
    )
    base.update(overrides)
    return ResearchResult(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- invariants

def test_a_blocked_result_cannot_be_selectable() -> None:
    """The wheel study published beat_control_pct 100.0 beside research_grade False."""
    with pytest.raises(ValueError, match="cannot be SELECTABLE"):
        _result(blockers=("no measured cost",))


def test_a_result_with_no_observations_cannot_be_selectable() -> None:
    """The same artifact claimed a sweep of 40 replicates from an arm that never traded."""
    with pytest.raises(ValueError, match="zero observations"):
        _result(sample=Sample(observations=0))


def test_cost_basis_cannot_be_left_empty() -> None:
    with pytest.raises(ValueError, match="cost_basis is required"):
        Provenance(cost_basis="")


def test_unknown_cost_is_allowed_but_is_not_measured() -> None:
    assert Provenance(cost_basis=UNKNOWN).cost_is_measured is False
    assert Provenance(cost_basis="measured:median").cost_is_measured is True
    assert Provenance(cost_basis="assumed:no-profile").cost_is_measured is False


def test_blockers_do_not_rescue_a_losing_study() -> None:
    """Rejection is allowed to be caveated; selection is not. The asymmetry is deliberate."""
    assert verdict_from_blockers(("caveat",), 50, passes=False) is Verdict.REJECTED
    assert verdict_from_blockers(("caveat",), 50, passes=True) is Verdict.INCONCLUSIVE
    assert verdict_from_blockers((), 50, passes=True) is Verdict.SELECTABLE
    assert verdict_from_blockers((), 50, passes=False) is Verdict.REJECTED


def test_nothing_can_be_concluded_from_an_empty_sample() -> None:
    assert verdict_from_blockers((), 0, passes=True) is Verdict.INCONCLUSIVE
    assert verdict_from_blockers((), 0, passes=False) is Verdict.INCONCLUSIVE


def test_an_unnamed_metric_is_refused() -> None:
    with pytest.raises(ValueError, match="metric name is required"):
        Metric(name="", value=1.0)


# ------------------------------------------------------------------------ evidence tiers

def test_measured_cost_outranks_assumed_cost_at_equal_verdicts() -> None:
    measured = _result(provenance=Provenance(cost_basis="measured:median"))
    assumed = _result(provenance=Provenance(cost_basis="assumed:no-profile"))
    assert measured.evidence_tier == 1
    assert assumed.evidence_tier == 2


def test_tiers_order_inconclusive_above_rejected_and_blocked_last() -> None:
    inconclusive = _result(verdict=Verdict.INCONCLUSIVE)
    rejected = _result(verdict=Verdict.REJECTED)
    blocked = _result(verdict=Verdict.INCONCLUSIVE, blockers=("missing cost",))
    assert (inconclusive.evidence_tier, rejected.evidence_tier, blocked.evidence_tier) == (3, 4, 5)


def test_a_rejected_study_with_caveats_is_not_demoted_to_blocked() -> None:
    """Otherwise dead strategies stay in the queue forever, waiting on a caveat nobody
    can clear."""
    assert _result(verdict=Verdict.REJECTED, blockers=("caveat",)).evidence_tier == 4


# ------------------------------------------------------------------------------- best-of

def test_a_null_metric_is_skipped_not_treated_as_zero() -> None:
    result = _result(
        candidates=(
            Candidate("a", Metric("pf", None, "ratio")),
            Candidate("b", Metric("pf", -3.0, "ratio")),
        )
    )
    assert result.best is not None
    assert result.best.candidate_id == "b"


def test_lower_is_better_metrics_are_respected() -> None:
    result = _result(
        candidates=(
            Candidate("shallow", Metric("max_drawdown_pct", 5.0, "pct", higher_is_better=False)),
            Candidate("deep", Metric("max_drawdown_pct", 40.0, "pct", higher_is_better=False)),
        )
    )
    assert result.best is not None and result.best.candidate_id == "shallow"


def test_no_candidates_means_no_best_rather_than_an_error() -> None:
    assert _result().best is None


# -------------------------------------------------------------------------------- rank()

def test_rank_refuses_to_merge_incommensurable_metrics() -> None:
    pct = _result(
        study_id="pct-study",
        candidates=(Candidate("a", Metric("total_return_pct", 4.0, "pct")),),
    )
    ratio = _result(
        study_id="ratio-study",
        candidates=(Candidate("b", Metric("profit_factor", 5.3, "ratio")),),
    )
    report = rank([pct, ratio])
    assert report["comparable_across_groups"] is False
    assert sorted(g["metric"] for g in report["groups"]) == ["profit_factor", "total_return_pct"]
    assert all(len(g["results"]) == 1 for g in report["groups"])


def test_rank_puts_evidence_quality_before_the_number() -> None:
    """A big number on an assumed cost must not outrank a smaller measured one."""
    weak_but_big = _result(
        study_id="assumed-big",
        provenance=Provenance(cost_basis="assumed:no-profile"),
        candidates=(Candidate("a", Metric("total_return_pct", 90.0, "pct")),),
    )
    strong_but_small = _result(
        study_id="measured-small",
        provenance=Provenance(cost_basis="measured:median"),
        candidates=(Candidate("b", Metric("total_return_pct", 1.0, "pct")),),
    )
    group = rank([weak_but_big, strong_but_small])["groups"][0]
    assert [r["study_id"] for r in group["results"]] == ["measured-small", "assumed-big"]


def test_rank_counts_every_tier_even_when_empty() -> None:
    report = rank([_result()])
    assert report["tier_counts"] == {"1": 1, "2": 0, "3": 0, "4": 0, "5": 0}


def test_rank_of_nothing_is_empty_not_an_error() -> None:
    report = rank([])
    assert report["result_count"] == 0 and report["groups"] == []


# ------------------------------------------------------------------------------ adapters

def test_wheel_adapter_records_that_the_universe_is_uncosted() -> None:
    """None of NVDL/SOXL/TQQQ/TSLL is in the captured spread profile, so no wheel result
    can reach tier 1 however good the parameters look."""
    payload = {
        "start": "2024-02-01",
        "end": "2026-06-01",
        "variants": [
            {"variant_id": "v1", "total_return_pct": 3.9, "closed_option_events": 38,
             "max_drawdown_pct": 12.0, "win_rate_pct": 70.0, "stock_symbols": ["NVDL"]},
        ],
    }
    result = wheel_parameter_lab_envelope(payload, study_id="w")
    assert result.provenance.cost_basis == WHEEL_UNCOSTED
    assert result.provenance.cost_is_measured is False
    assert result.evidence_tier == 5
    assert result.verdict is Verdict.INCONCLUSIVE
    assert result.sample.observations == 38
    assert result.sample.symbols == ("NVDL",)


def test_wheel_adapter_does_not_sum_events_across_variants() -> None:
    """Variants re-run the same window, so summing counts one market many times and would
    inflate the sample in proportion to how many parameters were swept."""
    payload = {"variants": [
        {"variant_id": "a", "total_return_pct": 1.0, "closed_option_events": 30},
        {"variant_id": "b", "total_return_pct": 2.0, "closed_option_events": 38},
    ]}
    assert wheel_parameter_lab_envelope(payload, study_id="w").sample.observations == 38


def test_an_infinite_profit_factor_is_unknown_not_the_winner() -> None:
    """No losing trades yields inf, which is a real observation but not a rankable one."""
    payload = {"research_grade": True, "patterns_full": [
        {"pattern_id": "inf", "profit_factor": float("inf"), "n": 9, "fdr_q_value": 0.01},
        {"pattern_id": "real", "profit_factor": 1.8, "n": 40, "fdr_q_value": 0.02},
    ]}
    result = eod_pattern_lab_envelope(payload, study_id="e")
    assert result.best is not None and result.best.candidate_id == "real"


def test_eod_adapter_reads_the_lab_s_own_blocker_fields() -> None:
    payload = {
        "research_grade": False,
        "research_grade_reason": "no out-of-sample period",
        "caveats": ["five-minute bars are not quotes"],
        "patterns_full": [{"pattern_id": "p", "profit_factor": 2.0, "n": 30, "fdr_q_value": 0.01}],
    }
    result = eod_pattern_lab_envelope(payload, study_id="e")
    assert "no out-of-sample period" in result.blockers
    assert "five-minute bars are not quotes" in result.blockers
    assert result.verdict is Verdict.INCONCLUSIVE


def test_eod_adapter_requires_fdr_survival_to_pass() -> None:
    """"The best of 429 patterns looked good" is a description of chance, not a finding."""
    unsurvived = {"research_grade": True, "patterns_full": [
        {"pattern_id": "p", "profit_factor": 9.0, "n": 30, "fdr_q_value": 0.90},
    ]}
    assert eod_pattern_lab_envelope(unsurvived, study_id="e").verdict is Verdict.REJECTED
    survived = {"research_grade": True, "patterns_full": [
        {"pattern_id": "p", "profit_factor": 9.0, "n": 30, "fdr_q_value": 0.05},
    ]}
    assert eod_pattern_lab_envelope(survived, study_id="e").verdict is Verdict.SELECTABLE


def test_current_commit_never_invents_a_value(tmp_path: Path) -> None:
    assert current_commit(tmp_path) == UNKNOWN


def test_wheel_engine_adapter_reads_the_nested_summary() -> None:
    """These keys sit under `summary`; a top-level probe read them as None and the runs were
    recorded as malformed when they never were."""
    payload = {"summary": {
        "total_return_pct": -4.8, "closed_option_events": 5, "max_drawdown_pct": 6.1,
        "win_rate_pct": 20.0, "events": 12, "skips": 250, "start": "2025-01-02",
        "end": "2026-07-24", "research_grade": False,
        "research_grade_blockers": ["historical option NBBO is unavailable"],
        "stock_symbols": 0,
    }}
    result = wheel_engine_envelope(payload, study_id="w")
    assert result.best is not None and result.best.primary.value == pytest.approx(-4.8)
    assert result.sample.observations == 5
    assert result.verdict is Verdict.REJECTED
    assert "historical option NBBO is unavailable" in result.blockers


def test_wheel_engine_adapter_does_not_read_a_count_as_symbols() -> None:
    """`stock_symbols` is a list in the parameter lab and an int here -- same key, different
    type. Filling the symbol tuple from it would put a number where names belong."""
    result = wheel_engine_envelope({"summary": {"stock_symbols": 4, "closed_option_events": 2,
                                                "total_return_pct": 1.0, "research_grade": True}},
                                   study_id="w")
    assert result.sample.symbols == ()
    assert "stock_symbols count: 4" in result.notes


def test_a_zero_event_wheel_run_cannot_claim_anything() -> None:
    """This is the exact artifact that published beat_control_pct 100.0."""
    result = wheel_engine_envelope(
        {"summary": {"total_return_pct": 0.0, "closed_option_events": 0, "research_grade": True}},
        study_id="w",
    )
    assert result.verdict is Verdict.INCONCLUSIVE
    assert result.sample.observations == 0


def test_walkforward_is_judged_on_the_harshest_execution_model() -> None:
    """Reporting the base-model number would be choosing the most favourable cost assumption
    available and calling it a finding."""
    payload = {"research_grade": True, "fold_results": [], "aggregate_results": [
        {"policy": "robust", "execution_model": "base", "profit_factor": 1.16, "trades": 76},
        {"policy": "robust", "execution_model": "worse", "profit_factor": 0.97, "trades": 76},
        {"policy": "robust", "execution_model": "severe", "profit_factor": 0.61, "trades": 76},
    ]}
    result = eod_option_walkforward_envelope(payload, study_id="wf")
    assert result.verdict is Verdict.REJECTED
    # The best case is still visible -- suppressed numbers are their own dishonesty.
    assert result.best is not None and result.best.candidate_id == "robust@base"
    assert any("harshest execution model run (severe)" in n for n in result.notes)
    assert any("best case under any model: profit factor 1.160" in n for n in result.notes)


def test_walkforward_passes_only_when_the_severe_case_clears_one() -> None:
    payload = {"research_grade": True, "fold_results": [], "aggregate_results": [
        {"policy": "robust", "execution_model": "base", "profit_factor": 2.0, "trades": 50},
        {"policy": "robust", "execution_model": "severe", "profit_factor": 1.4, "trades": 50},
    ]}
    assert eod_option_walkforward_envelope(payload, study_id="wf").verdict is Verdict.SELECTABLE


def test_walkforward_candidate_ids_carry_their_execution_model() -> None:
    """Every number should say which assumption produced it."""
    payload = {"research_grade": True, "fold_results": [], "aggregate_results": [
        {"policy": "permissive", "execution_model": "severe", "profit_factor": 0.5, "trades": 78},
    ]}
    result = eod_option_walkforward_envelope(payload, study_id="wf")
    assert [c.candidate_id for c in result.candidates] == ["permissive@severe"]
    assert result.provenance.cost_basis == "assumed:execution-stressed(severe)"


def test_walkforward_does_not_sum_trades_across_execution_models() -> None:
    """The same holdout trades are re-scored under each model."""
    payload = {"research_grade": True, "fold_results": [], "aggregate_results": [
        {"policy": "p", "execution_model": "base", "profit_factor": 1.0, "trades": 78},
        {"policy": "p", "execution_model": "severe", "profit_factor": 0.5, "trades": 78},
    ]}
    assert eod_option_walkforward_envelope(payload, study_id="wf").sample.observations == 78


# ------------------------------------------------- the part fixtures cannot prove: real data

@pytest.mark.skipif(not WHEEL_REPORT.is_file(), reason="wheel report.json not on this machine")
def test_the_envelope_survives_the_real_wheel_report() -> None:
    payload = json.loads(WHEEL_REPORT.read_text(encoding="utf-8"))
    result = wheel_parameter_lab_envelope(payload, study_id="wheel/parameter_lab_2026")
    assert len(result.candidates) == len(payload["variants"]) > 0
    assert result.best is not None and result.best.primary.value is not None
    assert result.sample.observations > 0
    assert result.evidence_tier == 5
    json.loads(result.to_json())


@pytest.mark.skipif(not EOD_REPORT.is_file(), reason="eod report.json not on this machine")
def test_the_envelope_survives_the_real_eod_report() -> None:
    payload = json.loads(EOD_REPORT.read_text(encoding="utf-8"))
    result = eod_pattern_lab_envelope(payload, study_id="eod/pattern_lab")
    assert len(result.candidates) == len(payload["patterns_full"]) > 0
    # Read from data.coverage, not data.symbols -- the latter does not exist and returned an
    # empty tuple that looked like "no symbols" instead of "wrong key".
    assert result.sample.symbols == ("IWM", "QQQ", "SPY")
    assert result.sample.start == "2026-01-25" and result.sample.end == "2026-07-24"
    # The lab records a flat round-trip cost under data, so this is costed but not measured.
    assert result.provenance.cost_basis == "assumed:flat-round-trip-0.02pct"
    assert result.provenance.cost_is_measured is False
    json.loads(result.to_json())


@pytest.mark.skipif(
    not (WHEEL_REPORT.is_file() and EOD_REPORT.is_file()),
    reason="both real reports required",
)
def test_two_incommensurable_real_labs_sort_into_one_structure() -> None:
    """This is the whole point of Phase 1: the spine exists the moment this passes."""
    wheel = wheel_parameter_lab_envelope(
        json.loads(WHEEL_REPORT.read_text(encoding="utf-8")), study_id="wheel/parameter_lab_2026"
    )
    eod = eod_pattern_lab_envelope(
        json.loads(EOD_REPORT.read_text(encoding="utf-8")), study_id="eod/pattern_lab"
    )
    report = rank([wheel, eod])
    assert report["result_count"] == 2
    assert report["comparable_across_groups"] is False
    assert {g["metric"] for g in report["groups"]} == {"total_return_pct", "profit_factor"}
    # Both are blocked today, which is the honest state of Cipher's research: nothing is
    # believable enough to act on. If this ever changes, it should change loudly.
    assert report["tier_counts"]["5"] == 2
    assert report["tier_counts"]["1"] == 0
