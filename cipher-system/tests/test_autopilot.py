"""The unattended loop: what it notices, what it stays quiet about, and what it may never do."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from core.autopilot import (
    DEFAULT_STATE_PATH,
    STALE_AFTER_SECONDS,
    last_run_summary,
    diff_fingerprints,
    headline,
    load_state,
    run_once,
    save_state,
)


def _write(root: Path, study: str, **summary) -> None:
    directory = root / study
    directory.mkdir(parents=True, exist_ok=True)
    payload = {"stock_positions": [], "summary": {"research_grade": True, **summary}}
    (directory / "report.json").write_text(json.dumps(payload), encoding="utf-8")


def _kinds(report: dict) -> list[str]:
    return [c["kind"] for c in report["changes"]]


# ------------------------------------------------------------------------ the first pass

def test_the_first_pass_is_a_baseline_not_sixty_one_new_studies(tmp_path: Path) -> None:
    """"Everything is new" is noise that would hide the one line that matters on pass two."""
    root, state = tmp_path / "corpus", tmp_path / "state.json"
    _write(root, "a", total_return_pct=1.0, closed_option_events=5)
    _write(root, "b", total_return_pct=2.0, closed_option_events=6)
    report = run_once(root=root, state_path=state)
    assert report["baseline"] is True
    assert report["changes"] == []
    assert "baseline recorded over 2 studies" in headline(report)


def test_an_unchanged_pass_is_a_noop_and_says_so(tmp_path: Path) -> None:
    root, state = tmp_path / "corpus", tmp_path / "state.json"
    _write(root, "a", total_return_pct=1.0, closed_option_events=5)
    run_once(root=root, state_path=state)
    second = run_once(root=root, state_path=state)
    assert second["noop"] is True and second["changes"] == []
    assert headline(second) == "Cipher autopilot: no change in the evidence."


# --------------------------------------------------------------------- change detection

def test_a_growing_sample_is_noticed(tmp_path: Path) -> None:
    """observations is stored in the fingerprint, so it must be diffed. It was stored and not
    compared at first, which made a ten-trade increase invisible while looking covered."""
    root, state = tmp_path / "corpus", tmp_path / "state.json"
    _write(root, "a", total_return_pct=4.0, closed_option_events=40)
    run_once(root=root, state_path=state)
    _write(root, "a", total_return_pct=4.0, closed_option_events=50)
    report = run_once(root=root, state_path=state)
    assert "sample_changed" in _kinds(report)
    assert "40 -> 50" in report["changes"][0]["detail"]


def test_a_cost_basis_change_is_noticed(tmp_path: Path) -> None:
    root, state = tmp_path / "corpus", tmp_path / "state.json"
    _write(root, "a", total_return_pct=1.0, closed_option_events=5)
    run_once(root=root, state_path=state)
    fingerprint = {"studies": {"a": {"tier": 5, "verdict": "inconclusive", "observations": 5,
                                     "cost_basis": "measured:median", "blockers": []}},
                   "tier_counts": {}}
    previous = {"studies": {"a": {"tier": 5, "verdict": "inconclusive", "observations": 5,
                                  "cost_basis": "assumed:no-profile", "blockers": []}}}
    changes = diff_fingerprints(previous, fingerprint)
    assert [c["kind"] for c in changes] == ["cost_basis_changed"]


def test_a_verdict_change_is_reported_once_not_twice(tmp_path: Path) -> None:
    """A study going REJECTED -> INCONCLUSIVE moves tier 4 -> 5. Emitting a separate
    tier_regressed line double-counts one event and reads as "got worse" when the study may
    simply have stopped being clearly dead."""
    previous = {"studies": {"a": {"tier": 4, "verdict": "rejected", "observations": 5,
                                  "cost_basis": "x", "blockers": []}}}
    current = {"studies": {"a": {"tier": 5, "verdict": "inconclusive", "observations": 5,
                                 "cost_basis": "x", "blockers": []}}, "tier_counts": {}}
    changes = diff_fingerprints(previous, current)
    assert [c["kind"] for c in changes] == ["verdict_changed"]
    assert "tier 4 -> 5" in changes[0]["detail"]


def test_a_tier_move_under_an_unchanged_verdict_is_still_reported() -> None:
    """Same conclusion, different evidence quality: a genuine independent change."""
    previous = {"studies": {"a": {"tier": 2, "verdict": "selectable", "observations": 5,
                                  "cost_basis": "assumed:no-profile", "blockers": []}}}
    current = {"studies": {"a": {"tier": 1, "verdict": "selectable", "observations": 5,
                                 "cost_basis": "measured:median", "blockers": []}}, "tier_counts": {}}
    kinds = [c["kind"] for c in diff_fingerprints(previous, current)]
    assert "tier_improved" in kinds and "cost_basis_changed" in kinds


def test_blockers_appearing_and_clearing_are_both_reported() -> None:
    previous = {"studies": {"a": {"tier": 5, "verdict": "inconclusive", "observations": 5,
                                  "cost_basis": "x", "blockers": ["old"]}}}
    current = {"studies": {"a": {"tier": 5, "verdict": "inconclusive", "observations": 5,
                                 "cost_basis": "x", "blockers": ["new"]}}, "tier_counts": {}}
    kinds = [c["kind"] for c in diff_fingerprints(previous, current)]
    assert sorted(kinds) == ["blocker_appeared", "blocker_cleared"]


def test_a_study_that_vanishes_is_reported(tmp_path: Path) -> None:
    root, state = tmp_path / "corpus", tmp_path / "state.json"
    _write(root, "a", total_return_pct=1.0, closed_option_events=5)
    _write(root, "b", total_return_pct=1.0, closed_option_events=5)
    run_once(root=root, state_path=state)
    (root / "b" / "report.json").unlink()
    report = run_once(root=root, state_path=state)
    assert "study_disappeared" in _kinds(report)


def test_an_improvement_sorts_above_the_noise() -> None:
    previous = {"studies": {
        "improving": {"tier": 3, "verdict": "inconclusive", "observations": 5, "cost_basis": "x", "blockers": []},
        "noisy": {"tier": 5, "verdict": "inconclusive", "observations": 5, "cost_basis": "x", "blockers": []},
    }}
    current = {"studies": {
        "improving": {"tier": 1, "verdict": "inconclusive", "observations": 5, "cost_basis": "x", "blockers": []},
        "noisy": {"tier": 5, "verdict": "inconclusive", "observations": 9, "cost_basis": "x", "blockers": ["b"]},
    }, "tier_counts": {}}
    assert diff_fingerprints(previous, current)[0]["kind"] == "tier_improved"


# -------------------------------------------------------------------------- state safety

def test_dry_run_leaves_the_baseline_untouched(tmp_path: Path) -> None:
    """Otherwise a dry run silently consumes the comparison the next real run needed."""
    root, state = tmp_path / "corpus", tmp_path / "state.json"
    _write(root, "a", total_return_pct=1.0, closed_option_events=5)
    run_once(root=root, state_path=state, dry_run=True)
    assert not state.exists()


def test_a_corrupt_state_file_degrades_to_a_baseline_rather_than_failing(tmp_path: Path) -> None:
    root, state = tmp_path / "corpus", tmp_path / "state.json"
    _write(root, "a", total_return_pct=1.0, closed_option_events=5)
    state.write_text("{truncated", encoding="utf-8")
    assert load_state(state) is None
    report = run_once(root=root, state_path=state)
    assert report["baseline"] is True


def test_state_is_written_atomically_leaving_no_temp_files(tmp_path: Path) -> None:
    """A truncated state file reads as "no previous run" and silently resets the diff."""
    state = tmp_path / "nested" / "state.json"
    save_state(state, {"fingerprint": {"studies": {}}})
    assert json.loads(state.read_text(encoding="utf-8"))["fingerprint"] == {"studies": {}}
    assert list(state.parent.glob("*.tmp")) == []


# ---------------------------------------------------------------------------- the ceiling

def test_every_report_asserts_it_holds_no_order_authority(tmp_path: Path) -> None:
    root, state = tmp_path / "corpus", tmp_path / "state.json"
    _write(root, "a", total_return_pct=1.0, closed_option_events=5)
    report = run_once(root=root, state_path=state)
    assert report["live_order_authority"] is False
    assert report["highest_possible_output"] == "a proposal a human reads"


def test_the_loop_reports_coverage_so_it_is_never_read_as_the_whole_corpus(tmp_path: Path) -> None:
    root, state = tmp_path / "corpus", tmp_path / "state.json"
    _write(root, "known", total_return_pct=1.0, closed_option_events=5)
    (root / "unknown").mkdir(parents=True, exist_ok=True)
    (root / "unknown" / "report.json").write_text(json.dumps({"mystery": 1}), encoding="utf-8")
    report = run_once(root=root, state_path=state)
    assert report["coverage"]["adapted"] == 1
    assert report["coverage"]["unadapted"] == 1


def test_an_empty_corpus_is_handled_without_pretending(tmp_path: Path) -> None:
    root, state = tmp_path / "corpus", tmp_path / "state.json"
    root.mkdir()
    report = run_once(root=root, state_path=state)
    assert report["coverage"]["adapted"] == 0
    assert report["recommended_actions"] == []
    assert "nothing to reason about" in report["nothing_to_run_because"]


def test_a_missing_report_says_so_instead_of_inventing_a_belief(tmp_path: Path) -> None:
    summary = last_run_summary(tmp_path / "absent.json")
    assert summary["available"] is False
    assert "no autopilot pass has been recorded" in summary["reason"]
    # Even the failure path asserts the ceiling, because a caller may render only this.
    assert summary["live_order_authority"] is False


def test_a_stale_report_is_flagged_rather_than_served_as_current(tmp_path: Path) -> None:
    report = tmp_path / "last.json"
    report.write_text(json.dumps({
        "generated_at": "2026-08-01T00:00:00+00:00", "tier_counts": {}, "coverage": {},
    }), encoding="utf-8")
    summary = last_run_summary(report, now=datetime(2026, 8, 12, tzinfo=timezone.utc))
    assert summary["available"] is True
    assert summary["stale"] is True
    assert summary["age_seconds"] > STALE_AFTER_SECONDS


def test_a_fresh_report_is_not_flagged_stale(tmp_path: Path) -> None:
    report = tmp_path / "last.json"
    report.write_text(json.dumps({
        "generated_at": "2026-08-12T00:00:00+00:00", "tier_counts": {}, "coverage": {},
        "noop": True,
    }), encoding="utf-8")
    summary = last_run_summary(report, now=datetime(2026, 8, 12, 6, tzinfo=timezone.utc))
    assert summary["stale"] is False
    assert summary["age_seconds"] == 6 * 3600


def test_an_unparseable_timestamp_degrades_to_stale_not_to_current(tmp_path: Path) -> None:
    """Reporting unknown age as fresh is the one failure that would make the UI lie."""
    report = tmp_path / "last.json"
    report.write_text(json.dumps({"generated_at": "not a date", "coverage": {}}), encoding="utf-8")
    summary = last_run_summary(report)
    assert summary["age_seconds"] is None
    assert summary["stale"] is True


def test_a_corrupt_report_is_unavailable_rather_than_half_rendered(tmp_path: Path) -> None:
    report = tmp_path / "last.json"
    report.write_text("[not an object]", encoding="utf-8")
    assert last_run_summary(report)["available"] is False


def test_the_default_state_path_lives_outside_the_repo() -> None:
    """Governance state is runtime data, not source. Writing it into the tree would make
    every pass show up as a dirty worktree and taint the commit every run binds itself to."""
    assert "runtime/governance" in DEFAULT_STATE_PATH.as_posix()
    assert "cipher-github" not in DEFAULT_STATE_PATH.as_posix()


# ------------------------------------------- scope changes are decisions, not data loss

def _study_fp(study_id: str) -> dict:
    return {
        "studies": {study_id: {"tier": 4, "verdict": "rejected", "observations": 5,
                               "cost_basis": "assumed:no-profile", "blockers": []}},
        "tier_counts": {},
        "capture": {},
    }


def test_a_deprioritised_study_leaving_the_corpus_is_not_reported_as_lost() -> None:
    """The wheel was stopped as a line of work, so 55 studies left the focused corpus in one
    run. Reporting those as "absent now" would send the next reader hunting for files that
    are exactly where they were left."""
    changes = diff_fingerprints(_study_fp("leveraged_etf_wheel/run_a"),
                                {"studies": {}, "tier_counts": {}, "capture": {}})
    assert [c["kind"] for c in changes] == ["study_deprioritised"]
    detail = changes[0]["detail"]
    assert "not lost" in detail
    assert "scope='all'" in detail, "must say how to read the retained results"


def test_a_study_vanishing_for_any_other_reason_is_still_a_fault() -> None:
    """The exemption must not swallow real losses."""
    changes = diff_fingerprints(_study_fp("eod_pattern_lab/run_a"),
                                {"studies": {}, "tier_counts": {}, "capture": {}})
    assert [c["kind"] for c in changes] == ["study_disappeared"]
    assert "absent now" in changes[0]["detail"]


def test_a_deprioritisation_sorts_below_every_substantive_change() -> None:
    previous = {
        "studies": {
            "leveraged_etf_wheel/run_a": {"tier": 4, "verdict": "rejected", "observations": 5,
                                          "cost_basis": "a", "blockers": []},
            "eod_pattern_lab/keeps": {"tier": 5, "verdict": "inconclusive", "observations": 5,
                                      "cost_basis": "a", "blockers": []},
        },
        "tier_counts": {}, "capture": {},
    }
    current = {
        "studies": {
            "eod_pattern_lab/keeps": {"tier": 1, "verdict": "selectable", "observations": 5,
                                      "cost_basis": "measured:median", "blockers": []},
        },
        "tier_counts": {}, "capture": {},
    }
    kinds = [c["kind"] for c in diff_fingerprints(previous, current)]
    assert kinds[0] == "verdict_changed", "a study becoming believable still leads"
    assert kinds[-1] == "study_deprioritised"
