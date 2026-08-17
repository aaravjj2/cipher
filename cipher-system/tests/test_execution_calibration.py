"""Calibration of assumed execution cost against measured spreads, and its refusals."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from core.execution_calibration import (
    ASSUMED_MODELS,
    BUCKET_MAP,
    DEFAULT_PROFILE,
    Verdict,
    calibrate,
    load_profile,
    report,
)

PROFILE = {
    "capture_window": {"first_event": "2026-07-22T00:00:00+00:00",
                       "last_event": "2026-08-10T00:00:00+00:00", "distinct_days": 12},
    "caveat": "short window",
    "option_half_spread_pct_of_premium": {
        "SPY|0dte": {"median": 0.625, "p75": 1.125, "p95": 3.125, "samples": 3119220, "sufficient": True},
        "IWM|0dte": {"median": 2.125, "p75": 4.125, "p95": 14.375, "samples": 2678614, "sufficient": True},
        "AAPL|1-7": {"median": 1.375, "p75": 2.125, "p95": 2.875, "samples": 925105, "sufficient": True},
    },
}


def _row(rows, symbol, model, bucket="0dte"):
    return next(r for r in rows if r.symbol == symbol and r.model == model and r.lab_bucket == bucket)


def test_the_copied_model_numbers_have_not_drifted_from_the_lab() -> None:
    """This module mirrors the lab's tuple so it can run without importing a 1,300-line
    module. A silent drift would calibrate against numbers nothing uses.

    Parsed from the source rather than imported: `core/eod_option_pattern_lab.py` imports its
    siblings bare (`from eod_pattern_lab import ...`), which needs `core/` itself on sys.path,
    and a drift check should not depend on the import layout of the thing it checks.
    """
    lab = Path(__file__).resolve().parent.parent / "core" / "eod_option_pattern_lab.py"
    source = lab.read_text(encoding="utf-8")
    block = re.search(r"EXECUTION_MODELS[^=]*=\s*\((.*?)\)\s*\n", source, re.S)
    assert block, "EXECUTION_MODELS tuple not found; this test needs updating"
    found = dict(
        (name, float(fraction))
        for name, fraction in re.findall(
            r'ExecutionModel\(\s*"([a-z]+)"\s*,\s*([0-9.]+)', block.group(1)
        )
    )
    assert found == dict(ASSUMED_MODELS), (
        f"lab defines {found} but this module assumes {dict(ASSUMED_MODELS)}"
    )


def test_fraction_is_converted_to_percent_of_premium() -> None:
    """The lab stores 0.03; the profile stores 3.0. Comparing them raw would understate the
    assumption by 100x and make every model look wildly optimistic."""
    rows = calibrate(["SPY"], ["0dte"], profile=PROFILE)
    assert _row(rows, "SPY", "base").assumed_pct_of_premium == 3.0
    assert _row(rows, "SPY", "severe").assumed_pct_of_premium == 10.0


def test_spy_stress_cases_sit_beyond_the_measured_p95() -> None:
    rows = calibrate(["SPY"], ["0dte"], profile=PROFILE)
    assert _row(rows, "SPY", "base").verdict == Verdict.BETWEEN_P75_AND_P95
    assert _row(rows, "SPY", "worse").verdict == Verdict.HARSHER_THAN_P95
    assert _row(rows, "SPY", "severe").verdict == Verdict.HARSHER_THAN_P95
    assert _row(rows, "SPY", "severe").ratio_to_median == 16.0


def test_iwm_models_stay_inside_the_measured_distribution() -> None:
    """The same three models mean different things per symbol, which is the finding."""
    rows = calibrate(["IWM"], ["0dte"], profile=PROFILE)
    assert _row(rows, "IWM", "base").verdict == Verdict.BETWEEN_MEDIAN_AND_P75
    assert _row(rows, "IWM", "severe").verdict == Verdict.BETWEEN_P75_AND_P95
    assert _row(rows, "IWM", "severe").ratio_to_median == pytest.approx(4.706, abs=0.001)


def test_an_unmeasured_bucket_says_so_rather_than_borrowing_a_neighbour() -> None:
    """`swing` maps to nothing: a 30-day contract's spread is not a week's."""
    rows = calibrate(["SPY"], ["swing"], profile=PROFILE)
    assert BUCKET_MAP["swing"] is None
    for row in rows:
        assert row.verdict == Verdict.NO_MEASUREMENT
        assert row.measured_median is None
        assert row.ratio_to_median is None


def test_front_and_weekly_both_map_to_the_measured_one_to_seven_bucket() -> None:
    rows = calibrate(["AAPL"], ["front", "weekly"], profile=PROFILE)
    assert {r.measured_bucket for r in rows} == {"1-7"}
    assert all(r.verdict != Verdict.NO_MEASUREMENT for r in rows)


def test_a_missing_symbol_is_unmeasured_not_zero() -> None:
    rows = calibrate(["NVDL"], ["0dte"], profile=PROFILE)
    assert all(r.verdict == Verdict.NO_MEASUREMENT for r in rows)


def test_no_profile_means_no_comparison_rather_than_a_silent_one() -> None:
    """core.execution_cost refuses to auto-load for the same reason: an answer that depends
    on whether a file happens to exist is not something a verdict can rest on."""
    rows = calibrate(["SPY"], ["0dte"], profile=None)
    assert rows and all(r.verdict == Verdict.NO_MEASUREMENT for r in rows)


def test_load_profile_returns_none_rather_than_raising(tmp_path: Path) -> None:
    assert load_profile(tmp_path / "absent.json") is None
    broken = tmp_path / "broken.json"
    broken.write_text("{truncated", encoding="utf-8")
    assert load_profile(broken) is None
    listy = tmp_path / "listy.json"
    listy.write_text("[1,2]", encoding="utf-8")
    assert load_profile(listy) is None


def test_the_report_carries_the_window_and_refuses_to_be_a_repricing() -> None:
    payload = report(["SPY", "IWM"], ["0dte"], profile=PROFILE)
    assert payload["measured_window"]["distinct_days"] == 12
    assert "cannot cost a study" in payload["not_a_repricing"]
    assert payload["profile_caveat"] == "short window"
    assert payload["measured_cells"] == 6
    assert payload["assumptions_harsher_than_p95"] == 2


def test_the_report_counts_unmeasured_cells_so_coverage_is_visible() -> None:
    payload = report(["SPY", "NVDL"], ["0dte", "swing"], profile=PROFILE)
    # SPY 0dte measured (3 models); SPY swing, NVDL 0dte, NVDL swing unmeasured (9).
    assert payload["measured_cells"] == 3
    assert payload["unmeasured_cells"] == 9


@pytest.mark.skipif(not DEFAULT_PROFILE.is_file(), reason="spread profile not on this machine")
def test_against_the_real_profile_qqq_base_already_exceeds_the_measured_p95() -> None:
    """The walkforward trades SPY/QQQ/IWM almost entirely in 0dte, and is rejected because no
    policy clears profit factor 1 under `severe`. For QQQ even `base` is harsher than the
    measured p95, so that rejection is weak evidence about the strategy."""
    profile = load_profile()
    rows = calibrate(["QQQ"], ["0dte"], profile=profile)
    base = _row(rows, "QQQ", "base")
    assert base.measured_median is not None and base.sufficient is True
    assert base.verdict == Verdict.HARSHER_THAN_P95
    assert _row(rows, "QQQ", "severe").ratio_to_median is not None
    assert _row(rows, "QQQ", "severe").ratio_to_median > 10


@pytest.mark.skipif(not DEFAULT_PROFILE.is_file(), reason="spread profile not on this machine")
def test_the_real_profile_json_round_trips_through_the_report() -> None:
    payload = report(["SPY", "QQQ", "IWM"], ["0dte", "front", "swing"], profile=load_profile())
    json.loads(json.dumps(payload))
    assert payload["cells"] == 27
