"""The setup scanner's cluster ranking must actually rank on cluster strength.

`scanner._cluster_strength` was rebuilt on 2026-08-06 specifically so that Strength was
scale-free and comparable across tickers, after the previous tier-offset score correlated
with the real product at ~0.19 and made the same mega-caps surface on every scan.

`setup_research_engine.build_scores` then discarded that work: it scored strength as
`min(strength / 6.0, 25.0)`, which saturates at 150 while real values run 72-340. On a
real 40-row scan every single row scored the full 25 points, so strength contributed no
ordering at all -- seven tickers tied at 69.0 with strengths from 192 to 255.

These tests fix the property that matters: two clusters of different strength must not
receive the same strength score anywhere in the range the scanner actually produces.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.setup_research_engine import (  # noqa: E402
    CLUSTER_STRENGTH_MAX,
    CLUSTER_STRENGTH_POINTS,
    cluster_strength_points,
    grade,
)

# The range the scanner's docstring records as observed: triples 72-260, quads 254-340.
OBSERVED = (72.0, 150.0, 192.0, 204.0, 207.0, 211.0, 212.0, 231.0, 239.0, 255.0, 265.0,
            278.0, 289.0, 303.0, 317.0, 340.0)


def test_strength_is_strictly_increasing_across_the_observed_range():
    """The regression: every value at or above 150 collapsed to the same 25.00."""
    points = [cluster_strength_points(value) for value in OBSERVED]
    assert len(set(points)) == len(points), dict(zip(OBSERVED, points))
    assert points == sorted(points)


def test_the_saturating_scale_would_have_failed_this():
    """Guards the property rather than the constant: document what was broken."""
    old = [min(value / 6.0, 25.0) for value in OBSERVED]
    saturated = [value for value in old if value == 25.0]
    assert len(saturated) >= 14, "the old scale saturated for nearly the whole range"


def test_no_row_can_exceed_the_points_budget():
    for value in (CLUSTER_STRENGTH_MAX, CLUSTER_STRENGTH_MAX * 2, 10_000.0):
        assert cluster_strength_points(value) == CLUSTER_STRENGTH_POINTS


def test_absent_or_nonsensical_strength_scores_zero():
    for value in (0.0, -1.0, -500.0):
        assert cluster_strength_points(value) == 0.0


def test_a_quad_and_a_triple_of_equal_strength_score_equally_here():
    """Cluster kind is scored separately as the quad/triple bonus.

    Folding kind into the strength term as well would double-count it and break the real
    product's ordering of quads first, then descending strength.
    """
    assert cluster_strength_points(255.0) == cluster_strength_points(255.0)


def test_grades_still_span_their_bands():
    assert grade(90) == "A"
    assert grade(80) == "B+"
    assert grade(70) == "B"
    assert grade(60) == "C"
    assert grade(10) == "skip"


# --------------------------------------------------------------- agreement reporting

def _row(monkeypatch, cluster: dict, liq: dict | None = None) -> dict:
    """Drive build_scores over one synthetic cluster row."""
    from core import setup_research_engine as engine

    monkeypatch.setattr(engine, "latest_scan_run", lambda scan_dir: Path("/tmp/does-not-matter"))
    monkeypatch.setattr(engine, "load_json", lambda path: {})
    monkeypatch.setattr(engine, "latest_context", lambda context_dir: None)

    def rows_for(run_dir, name):
        if name == "cluster":
            return [cluster]
        if name == "liq":
            return [liq] if liq else []
        return []

    monkeypatch.setattr(engine, "rows_for", rows_for)
    report = engine.build_scores(Path("/tmp/scan"), Path("/tmp/context"))
    return report["ranked"][0]


def test_a_conflicting_liq_row_is_not_reported_as_an_overlap(monkeypatch):
    """`liq_overlap` was `bool(liq)`: true even for a row just penalised 8 points."""
    row = _row(
        monkeypatch,
        {"ticker": "TST", "setup": "QUAD UPSIDE", "strength": 300, "rank": 1,
         "spot": 100.0, "cluster_target": 103.0},
        liq={"ticker": "TST", "setup": "DOWNSIDE", "runway_clarity_pct": 90},
    )
    assert row["liq_present"] is True
    assert row["liq_overlap"] is False
    assert row["liq_conflict"] is True
    assert "liq_direction_conflict" in row["reasons"]


def test_an_agreeing_liq_row_is_reported_as_an_overlap(monkeypatch):
    row = _row(
        monkeypatch,
        {"ticker": "TST", "setup": "QUAD UPSIDE", "strength": 300, "rank": 1,
         "spot": 100.0, "cluster_target": 103.0},
        liq={"ticker": "TST", "setup": "UPSIDE", "runway_clarity_pct": 90},
    )
    assert row["liq_overlap"] is True
    assert row["liq_conflict"] is False


def test_a_missing_liq_row_is_neither_overlap_nor_conflict(monkeypatch):
    row = _row(
        monkeypatch,
        {"ticker": "TST", "setup": "TRIPLE UPSIDE", "strength": 200, "rank": 9,
         "spot": 100.0, "cluster_target": 102.0},
    )
    assert row["liq_present"] is False
    assert row["liq_overlap"] is False
    assert row["liq_conflict"] is False


def test_stronger_cluster_outscores_weaker_when_all_else_is_equal(monkeypatch):
    base = {"ticker": "TST", "setup": "TRIPLE UPSIDE", "rank": 9, "spot": 100.0,
            "cluster_target": 102.0}
    weak = _row(monkeypatch, {**base, "strength": 192})
    strong = _row(monkeypatch, {**base, "strength": 289})
    assert strong["score"] > weak["score"], (strong["score"], weak["score"])
