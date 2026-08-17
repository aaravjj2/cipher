"""Capture continuity: the one asset the measurement programme cannot rebuild."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from core.autopilot import diff_fingerprints
from core.capture_continuity import (
    DEFAULT_PROFILE,
    PROFILE_STALE_AFTER_DAYS,
    read,
)

NOW = datetime(2026, 8, 12, tzinfo=timezone.utc)


def _profile(tmp_path: Path, **window) -> Path:
    payload = {
        "created_at": "2026-08-10T22:26:25+00:00",
        "capture_window": {
            "first_event": "2026-07-22T19:33:44+00:00",
            "last_event": "2026-08-10T19:59:59+00:00",
            "distinct_days": 12,
            **window,
        },
    }
    path = tmp_path / "spread_profile.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_missing_weekdays_and_sparse_days_both_count_against_continuity(tmp_path: Path) -> None:
    """A sparse day is a partial observation, not a clean one, so it cannot be counted as
    coverage. Twelve captured days with four imperfect ones is eight clean days."""
    path = _profile(
        tmp_path,
        missing_weekdays=["2026-07-24", "2026-07-28"],
        sparse_days={"2026-07-22": 1452, "2026-07-23": 49644},
    )
    health = read(path, now=NOW)
    assert health.distinct_days == 12
    assert health.missing_weekdays == ("2026-07-24", "2026-07-28")
    assert health.sparse_days == ("2026-07-22", "2026-07-23")
    assert health.gap_count == 4
    assert "4 imperfect day(s)" in health.verdict


def test_sparse_days_are_read_from_a_mapping_of_day_to_count(tmp_path: Path) -> None:
    """The artifact stores counts; only the keys matter here, and sorting them keeps the
    fingerprint stable across rebuilds."""
    path = _profile(tmp_path, sparse_days={"2026-07-23": 49644, "2026-07-22": 1452})
    assert read(path, now=NOW).sparse_days == ("2026-07-22", "2026-07-23")


def test_a_list_of_sparse_days_is_also_accepted(tmp_path: Path) -> None:
    path = _profile(tmp_path, sparse_days=["2026-07-23", "2026-07-22"])
    assert read(path, now=NOW).sparse_days == ("2026-07-22", "2026-07-23")


def test_a_clean_window_says_so(tmp_path: Path) -> None:
    path = _profile(tmp_path, missing_weekdays=[], sparse_days={})
    health = read(path, now=NOW)
    assert health.gap_count == 0
    assert health.verdict == "continuous over the captured window"


def test_a_stale_profile_does_not_claim_to_describe_today(tmp_path: Path) -> None:
    """Capture continues after a rebuild, so an old profile understates coverage. Saying so
    beats letting a decision rest on a count that is weeks behind."""
    path = _profile(tmp_path)
    late = datetime(2026, 9, 30, tzinfo=timezone.utc)
    health = read(path, now=late)
    assert health.profile_stale is True
    assert health.profile_age_days is not None and health.profile_age_days > PROFILE_STALE_AFTER_DAYS
    assert "stale" in health.verdict


def test_a_fresh_profile_is_not_stale(tmp_path: Path) -> None:
    assert read(_profile(tmp_path), now=NOW).profile_stale is False


def test_an_unparseable_build_date_is_treated_as_stale(tmp_path: Path) -> None:
    """Reporting unknown age as current is the one error that makes a decision wrong rather
    than merely uninformed."""
    path = tmp_path / "p.json"
    path.write_text(json.dumps({"created_at": "whenever", "capture_window": {}}), encoding="utf-8")
    health = read(path, now=NOW)
    assert health.profile_age_days is None
    assert health.profile_stale is True


def test_a_missing_profile_reports_unknown_rather_than_continuous(tmp_path: Path) -> None:
    health = read(tmp_path / "absent.json", now=NOW)
    assert health.available is False
    assert health.gap_count == 0
    assert "unknown" in health.verdict


def test_a_corrupt_profile_is_unavailable(tmp_path: Path) -> None:
    path = tmp_path / "p.json"
    path.write_text("[not an object]", encoding="utf-8")
    assert read(path, now=NOW).available is False


def test_health_serialises_for_the_report(tmp_path: Path) -> None:
    payload = read(_profile(tmp_path, missing_weekdays=["2026-07-24"]), now=NOW).to_dict()
    json.loads(json.dumps(payload))
    assert payload["gap_count"] == 1
    assert payload["missing_weekdays"] == ["2026-07-24"]


# ---------------------------------------------------- the diff the autopilot now reports

def _fp(days: int, gaps: list[str]) -> dict:
    return {
        "capture": {"distinct_days": days, "gap_count": len(gaps), "missing_weekdays": gaps,
                    "last_event": "x"},
        "studies": {},
        "tier_counts": {},
    }


def test_a_new_capture_gap_is_reported_as_unrecoverable() -> None:
    """Quotes are observations. A day not captured is a day gone, and no later run fills it."""
    changes = diff_fingerprints(_fp(12, ["2026-07-24"]), _fp(13, ["2026-07-24", "2026-08-11"]))
    kinds = [c["kind"] for c in changes]
    assert "capture_gap_appeared" in kinds
    gap = next(c for c in changes if c["kind"] == "capture_gap_appeared")
    assert "2026-08-11" in gap["detail"]
    assert "cannot be recovered" in gap["detail"]


def test_a_new_gap_outranks_routine_coverage_growth() -> None:
    changes = diff_fingerprints(_fp(12, []), _fp(13, ["2026-08-11"]))
    assert changes[0]["kind"] == "capture_gap_appeared"


def test_coverage_growing_is_reported_but_quietly() -> None:
    changes = diff_fingerprints(_fp(12, []), _fp(13, []))
    assert [c["kind"] for c in changes] == ["capture_days_changed"]
    assert "12 -> 13" in changes[0]["detail"]


def test_an_unchanged_capture_window_produces_no_capture_change() -> None:
    assert diff_fingerprints(_fp(12, ["2026-07-24"]), _fp(12, ["2026-07-24"])) == []


def test_a_state_file_predating_this_field_does_not_alarm_on_old_gaps() -> None:
    """The first pass after adding capture tracking reported two long-standing gaps as newly
    appeared, because the previous fingerprint had no capture block to compare against. An
    absent block is a baseline, not an empty set."""
    previous = {"studies": {}, "tier_counts": {}}  # no "capture" key at all
    changes = diff_fingerprints(previous, _fp(12, ["2026-07-24", "2026-07-28"]))
    assert [c["kind"] for c in changes if c["kind"].startswith("capture")] == []


def test_a_gap_appearing_after_capture_is_tracked_is_still_reported() -> None:
    """The baseline exemption must not swallow real gaps once a baseline exists."""
    changes = diff_fingerprints(_fp(12, []), _fp(13, ["2026-08-11"]))
    assert [c["kind"] for c in changes if c["kind"] == "capture_gap_appeared"]


def test_the_real_profile_is_readable_on_this_machine() -> None:
    if not DEFAULT_PROFILE.is_file():
        return
    health = read()
    assert health.available is True
    assert health.distinct_days is not None and health.distinct_days > 0
