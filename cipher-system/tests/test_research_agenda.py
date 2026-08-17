"""The agent's judgement, especially its ability to recommend nothing."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.research_agenda import RULES, BlockerClass, census, classify, propose
from core.research_corpus import (
    DEFAULT_ROOT,
    ROUTES,
    collect,
    is_deprioritised,
    route,
    study_id_for,
)
from core.research_envelope import (
    Candidate,
    Metric,
    Provenance,
    ResearchResult,
    Sample,
    Verdict,
)


def _blocked(study_id: str, *blockers: str, tier_source: Verdict = Verdict.INCONCLUSIVE) -> ResearchResult:
    return ResearchResult(
        study_id=study_id,
        engine="engine",
        verdict=tier_source,
        sample=Sample(observations=10),
        provenance=Provenance(cost_basis="assumed:no-profile"),
        blockers=blockers,
        candidates=(Candidate("c", Metric("total_return_pct", 1.0, "pct")),),
    )


# ------------------------------------------------------------------------ classification

def test_an_inherent_limitation_is_never_proposed_as_work() -> None:
    """No dataset turns a risk-neutral POP into an observed frequency. An agent that
    proposes fixing it would generate work forever."""
    rule = classify("risk-neutral POP is a model estimate, not an observed probability")
    assert rule is not None and rule.classification is BlockerClass.INHERENT
    agenda = propose([_blocked("s", "risk-neutral POP is a model estimate, not an observed probability")])
    assert agenda["recommended_actions"] == []
    assert "inherent" in agenda["nothing_to_run_because"]


def test_an_acquirable_blocker_is_reported_but_not_scheduled() -> None:
    """Buying historical NBBO is the owner's spending decision, not a compute job."""
    agenda = propose([_blocked("s", "historical option NBBO is unavailable; trade-bar proxies are used")])
    assert agenda["recommended_actions"] == []
    assert len(agenda["blockers_requiring_acquisition"]) == 1


def test_an_actionable_blocker_becomes_a_proposal_with_its_limits() -> None:
    agenda = propose([_blocked("s", "execution cost is unmeasured for this universe: x")])
    assert len(agenda["recommended_actions"]) == 1
    action = agenda["recommended_actions"][0]
    assert "TRADIER_OPTION_UNDERLYINGS" in action["detail"]
    assert "12 trading days" in action["latency"]
    # The action must carry what it will NOT achieve, or it reads as a fix.
    assert "does NOT retroactively cost" in action["limitation"]


def test_an_unrecognised_blocker_surfaces_rather_than_vanishing() -> None:
    """A blocker nobody anticipated must make the agent loudly incomplete, not quietly
    wrong."""
    agenda = propose([_blocked("s", "the moon was in the wrong phase")])
    assert classify("the moon was in the wrong phase") is None
    assert [r["blocker"] for r in agenda["unclassified_blockers"]] == ["the moon was in the wrong phase"]
    assert agenda["unclassified_warning"]
    assert agenda["recommended_actions"] == []


def test_every_rule_states_what_it_implies_and_nothing_it_does_not() -> None:
    """ACQUIRABLE rules carry action text describing what acquiring the data means -- they
    are simply never scheduled. Only INHERENT rules must stay silent, because there is no
    action to describe."""
    for rule in RULES:
        if rule.classification is BlockerClass.ACTIONABLE:
            assert rule.action, f"actionable rule has no action text: {rule.match}"
            assert rule.latency != "unknown", f"actionable rule has no latency: {rule.match}"
        elif rule.classification is BlockerClass.INHERENT:
            assert not rule.action, f"inherent rule proposes work: {rule.match}"
            assert rule.detail, f"inherent rule must explain why it cannot be cleared: {rule.match}"


def test_only_actionable_rules_are_ever_scheduled() -> None:
    for rule in RULES:
        if rule.classification is BlockerClass.ACTIONABLE:
            continue
        agenda = propose([_blocked("s", f"prefix {rule.match} suffix")])
        assert agenda["recommended_actions"] == [], f"{rule.match} was scheduled"


# -------------------------------------------------------------------------------- census

def test_census_orders_by_how_many_studies_a_blocker_holds_down() -> None:
    results = [
        _blocked("a", "widespread", "rare"),
        _blocked("b", "widespread"),
        _blocked("c", "widespread"),
    ]
    rows = census(results)
    assert [r.text for r in rows] == ["widespread", "rare"]
    assert rows[0].study_count == 3 and rows[1].study_count == 1


def test_census_records_the_strongest_tier_a_blocker_holds_down() -> None:
    """Clearing a blocker on a nearly-believable study is worth more than clearing one on a
    study that would still be rejected afterwards."""
    rows = census([
        _blocked("weak", "shared", tier_source=Verdict.REJECTED),
        _blocked("strong", "shared", tier_source=Verdict.INCONCLUSIVE),
    ])
    assert rows[0].best_tier_blocked == 4


# --------------------------------------------------------------- recommending nothing

def test_no_studies_means_nothing_to_reason_about() -> None:
    agenda = propose([])
    assert agenda["recommended_actions"] == []
    assert "nothing to reason about" in agenda["nothing_to_run_because"]


def test_studies_without_blockers_ask_for_a_new_question_not_a_repair() -> None:
    clean = ResearchResult(
        study_id="clean",
        engine="e",
        verdict=Verdict.SELECTABLE,
        sample=Sample(observations=50),
        provenance=Provenance(cost_basis="measured:median"),
    )
    agenda = propose([clean])
    assert agenda["selectable_today"] == 1
    assert "a new question rather than a repair" in agenda["nothing_to_run_because"]


def test_the_agenda_is_empty_when_every_blocker_is_unfixable() -> None:
    agenda = propose([
        _blocked("a", "risk-neutral POP is a model estimate, not an observed probability"),
        _blocked("b", "historical option NBBO is unavailable"),
    ])
    assert agenda["recommended_actions"] == []
    assert agenda["nothing_to_run_because"]


# ------------------------------------------------------------------------------ routing

def test_routing_uses_a_key_the_engine_writes_not_the_filename() -> None:
    assert route({"variants": []})[0] == "leveraged_etf_wheel_parameter_lab"
    assert route({"patterns_full": []})[0] == "eod_pattern_lab"
    assert route({"fold_results": []})[0] == "eod_option_walkforward"
    assert route({"stock_positions": []})[0] == "leveraged_etf_csp_wheel"
    assert route({"something_else": 1}) is None


def test_the_parameter_lab_wins_over_the_engine_when_both_keys_appear() -> None:
    """Both live under leveraged_etf_wheel/; only the lab writes `variants`."""
    assert route({"variants": [], "stock_positions": []})[0] == "leveraged_etf_wheel_parameter_lab"


def test_study_id_is_the_directory_so_two_runs_stay_distinct() -> None:
    root = Path("/root")
    assert study_id_for(root / "wheel" / "run_a" / "report.json", root) == "wheel/run_a"
    assert study_id_for(root / "wheel" / "run_b" / "report.json", root) == "wheel/run_b"


def test_superseded_artifacts_never_re_enter_a_ranking(tmp_path: Path) -> None:
    """Two of them assert a verdict the current code forbids."""
    good = tmp_path / "live"
    bad = tmp_path / "superseded"
    for directory in (good, bad):
        directory.mkdir()
        (directory / "report.json").write_text(
            json.dumps({"stock_positions": [], "summary": {
                "total_return_pct": 1.0, "closed_option_events": 5, "research_grade": True}}),
            encoding="utf-8",
        )
    results, unadapted = collect(tmp_path, commit="test")
    assert [r.study_id for r in results] == ["live"]
    assert unadapted == []


def test_an_unadaptable_report_is_named_not_skipped(tmp_path: Path) -> None:
    (tmp_path / "odd").mkdir()
    (tmp_path / "odd" / "report.json").write_text(json.dumps({"mystery": 1}), encoding="utf-8")
    results, unadapted = collect(tmp_path, commit="test")
    assert results == []
    assert unadapted[0]["reason"] == "no adapter for this shape"
    assert unadapted[0]["top_level_keys"] == ["mystery"]


def test_unreadable_and_non_object_reports_are_reported(tmp_path: Path) -> None:
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "report.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "listy").mkdir()
    (tmp_path / "listy" / "report.json").write_text("[1,2,3]", encoding="utf-8")
    _results, unadapted = collect(tmp_path, commit="test")
    reasons = sorted(u["reason"] for u in unadapted)
    assert reasons == ["not a JSON object", "unreadable: JSONDecodeError"]


def test_every_route_targets_a_distinct_engine() -> None:
    engines = [engine for _key, engine, _adapter in ROUTES]
    assert len(engines) == len(set(engines))


# ------------------------------------------------------- against the real corpus on disk

@pytest.mark.skipif(not DEFAULT_ROOT.is_dir(), reason="runtime data not on this machine")
def test_the_agent_classifies_every_blocker_in_the_real_corpus() -> None:
    """If this fails, a new blocker string has appeared and the agent needs a rule for it --
    which is exactly what the unclassified list is for."""
    results, _unadapted = collect(DEFAULT_ROOT, commit="test")
    agenda = propose(results)
    assert agenda["studies_considered"] > 0
    assert agenda["unclassified_blockers"] == [], (
        "unclassified blockers found: "
        + "; ".join(r["blocker"] for r in agenda["unclassified_blockers"])
    )


@pytest.mark.skipif(not DEFAULT_ROOT.is_dir(), reason="runtime data not on this machine")
def test_the_wheel_is_out_of_the_agenda_but_not_off_the_disk() -> None:
    """The wheel was stopped as a line of work on 2026-08-13, and 55 wheel studies sharing
    one blocker had been outvoting every other recommendation the agent could make.

    Deprioritising is not retraction: the results stay readable under `scope="all"`, and this
    test fails if they are ever actually lost.
    """
    focused, _ = collect(DEFAULT_ROOT, commit="test", scope="focus")
    everything, _ = collect(DEFAULT_ROOT, commit="test", scope="all")

    assert not [r for r in focused if is_deprioritised(r.study_id)]
    dropped = [r for r in everything if is_deprioritised(r.study_id)]
    assert len(dropped) >= 50, "the wheel corpus should still be on disk and readable"
    assert len(everything) > len(focused)

    # Whatever the agent now recommends, it must be about work that is actually in scope.
    agenda = propose(focused)
    assert agenda["studies_considered"] == len(focused)
    for action in agenda["recommended_actions"]:
        assert action["unblocks_studies"] <= len(focused)


@pytest.mark.skipif(not DEFAULT_ROOT.is_dir(), reason="runtime data not on this machine")
def test_nothing_in_the_focused_corpus_is_selectable_yet() -> None:
    """Recorded as a fact rather than an aspiration: if this ever flips, a real result has
    arrived and the change is worth noticing rather than discovering later."""
    results, _ = collect(DEFAULT_ROOT, commit="test", scope="focus")
    assert propose(results)["selectable_today"] == 0


def test_scope_must_be_one_of_the_two_supported_values() -> None:
    with pytest.raises(ValueError, match="scope"):
        collect(DEFAULT_ROOT, commit="test", scope="everything")
