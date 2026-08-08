"""Overfitting guards on the weight lab.

These exist because the guards were absent and a degenerate fit went live: the
flash head was driving every card while carrying n=22 rows from one session,
R² 0.982 with its own "likely overfit" warning, six coefficients pinned at exactly
zero, and two features that were the same column fitted twice. None of that was
reported by the fitter — it had to be found by hand.
"""
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))

import weight_lab as wl  # noqa: E402


# ── independent-sample accounting ────────────────────────────────────────────

def test_group_key_collapses_intraday_recaptures_of_one_card():
    """432 intraday rows of the same card are not 432 samples."""
    rows = [
        {"ticker": "MU", "session_date": "2026-07-23", "captured_at": f"2026-07-23T13:{m:02d}:00Z"}
        for m in range(0, 50, 5)
    ]
    assert len({wl._group_key(r) for r in rows}) == 1


def test_group_key_separates_days_and_tickers():
    rows = [
        {"ticker": "MU", "session_date": "2026-07-23"},
        {"ticker": "MU", "session_date": "2026-07-24"},
        {"ticker": "NVDA", "session_date": "2026-07-23"},
    ]
    assert len({wl._group_key(r) for r in rows}) == 3


def test_group_key_reads_the_date_out_of_a_commercial_filename():
    """Commercial CSV rows carry their session only in the source filename."""
    row = {"ticker": "AMD", "source": "obsidian_2026-07-19_flash_runway.csv"}
    assert wl._group_key(row) == "2026-07-19:AMD"


# ── degeneracy detection ─────────────────────────────────────────────────────

def test_constant_features_are_reported():
    """A constant column fits to exactly zero and contributes nothing; silently
    shipping it as a 'feature' overstates how much the model actually uses."""
    X = np.array([[1.0, 5.0], [2.0, 5.0], [3.0, 5.0]])
    constant, _ = wl._degenerate_features(X, ["varies", "frozen"])
    assert constant == ["frozen"]


def test_duplicate_columns_are_reported_as_collinear():
    X = np.array([[1.0, 2.0, 1.0], [2.0, 4.0, 5.0], [3.0, 6.0, 2.0]])
    _, collinear = wl._degenerate_features(X, ["a", "double_a", "c"])
    assert ("a", "double_a") in collinear


def test_independent_columns_are_not_flagged():
    rng = np.random.default_rng(0)
    X = rng.normal(size=(40, 3))
    constant, collinear = wl._degenerate_features(X, ["a", "b", "c"])
    assert constant == []
    assert collinear == []


# ── grouped cross-validation ─────────────────────────────────────────────────

def test_grouped_cv_returns_none_when_there_are_too_few_groups():
    X = np.array([[1.0], [2.0], [3.0]])
    y = np.array([1.0, 2.0, 3.0])
    assert wl._grouped_cv_r2(X, y, ["g1", "g1", "g2"], 1.0) is None


def test_grouped_cv_rewards_a_genuine_relationship():
    rng = np.random.default_rng(1)
    x = rng.normal(size=60)
    X = x.reshape(-1, 1)
    y = 3.0 * x + rng.normal(scale=0.1, size=60)
    groups = [f"g{i % 10}" for i in range(60)]
    assert wl._grouped_cv_r2(X, y, groups, 1.0) > 0.9


def test_grouped_cv_exposes_a_memorised_fit():
    """Pure noise has no out-of-sample signal; CV R² must not be positive."""
    rng = np.random.default_rng(2)
    X = rng.normal(size=(40, 8))
    y = rng.normal(size=40)
    groups = [f"g{i}" for i in range(40)]
    assert wl._grouped_cv_r2(X, y, groups, 1.0) < 0.2


# ── the activation gate ──────────────────────────────────────────────────────

def _passing_fit():
    return {
        "n_rows": 400, "n_groups": 45, "n_tickers": 15, "n_days": 12,
        "r_squared": 0.62, "r2_cv": 0.55,
        "degenerate_features": [], "collinear_pairs": [],
    }


def test_a_healthy_fit_clears_the_gate():
    assert wl.activation_blockers(_passing_fit()) == []


@pytest.mark.parametrize("field,value,expected", [
    ("n_groups", 12, "independent groups"),
    ("n_tickers", 4, "tickers"),
    ("n_days", 2, "sessions"),
])
def test_thin_samples_are_blocked(field, value, expected):
    fit = _passing_fit()
    fit[field] = value
    assert any(expected in b for b in wl.activation_blockers(fit))


def test_no_out_of_sample_skill_is_blocked():
    fit = _passing_fit()
    fit["r2_cv"] = -0.1
    assert any("out-of-sample" in b for b in wl.activation_blockers(fit))


def test_in_sample_out_of_sample_collapse_is_blocked():
    """The exact signature of the shipped fit: R² 0.98 in-sample, nothing out."""
    fit = _passing_fit()
    fit["r_squared"] = 0.98
    fit["r2_cv"] = 0.10
    assert any("overfit" in b for b in wl.activation_blockers(fit))


def test_degenerate_and_collinear_features_block_activation():
    fit = _passing_fit()
    fit["degenerate_features"] = ["dow_sin", "dow_cos"]
    fit["collinear_pairs"] = [["pull_from_support_pct", "stretch_from_support_pct"]]
    blockers = wl.activation_blockers(fit)
    assert any("constant features" in b for b in blockers)
    assert any("collinear" in b for b in blockers)


def test_a_fit_predating_the_guards_cannot_be_activated():
    """Absence of the diagnostics is disqualifying — it means nothing checked it."""
    assert any("predates" in b for b in wl.activation_blockers({"r_squared": 0.99, "n": 22}))


def test_missing_weights_is_blocked_rather_than_crashing():
    assert wl.activation_blockers(None) == ["no fitted weights on disk"]


# ── the gate is an invariant, not just an admission check ────────────────────

def test_a_failing_head_that_is_already_live_gets_switched_off(tmp_path, monkeypatch):
    """The original bug was not that a bad fit was activated — it was that it
    STAYED activated. Refusing new activations while leaving the old one running
    would not have caught it."""
    active = tmp_path / "active.json"
    active.write_text('{"active": true, "flash_active": true}')
    monkeypatch.setattr(wl, "ACTIVE_PATH", active)
    monkeypatch.setattr(wl, "ensure_dirs", lambda: None)
    monkeypatch.setattr(wl, "_clear_scanner_cache", lambda: None)
    monkeypatch.setattr(wl, "load_flash_weights", lambda: {
        "n_rows": 22, "n_groups": 22, "n_tickers": 22, "n_days": 1,
        "r_squared": 0.98, "r2_cv": 0.91,
        "degenerate_features": ["dow_sin"], "collinear_pairs": [],
    })

    result = wl.set_flash_active(True)
    assert result["activated"] is False
    assert result["deactivated_by_gate"] is True
    assert result["flash_active"] is False
    import json as _json
    assert _json.loads(active.read_text())["flash_active"] is False


def test_deactivation_is_never_blocked(tmp_path, monkeypatch):
    """Turning a head OFF must always work, whatever the fit looks like."""
    active = tmp_path / "active.json"
    active.write_text('{"active": true, "flash_active": true}')
    monkeypatch.setattr(wl, "ACTIVE_PATH", active)
    monkeypatch.setattr(wl, "ensure_dirs", lambda: None)
    monkeypatch.setattr(wl, "_clear_scanner_cache", lambda: None)
    monkeypatch.setattr(wl, "load_flash_weights", lambda: None)

    assert wl.set_flash_active(False)["flash_active"] is False
