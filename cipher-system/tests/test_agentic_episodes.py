"""Target-extension behaviour for Flash Agentic episodes.

Modelled on the product's own Micron walkthrough: surfaced at $81 calling $83,
reached $83, extended repeatedly to $88, then shut off when structure flipped.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))

import agentic_episodes as ep  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(ep, "STORE", tmp_path / "episodes.json")


WALLS = [83.0, 85.0, 86.5, 88.0, 90.0]


def _step(spot, **kw):
    return ep.update(
        "MU", direction="BULLISH", setup="FLOOR BOUNCE", spot=spot,
        first_target=83.0, levels=WALLS, **kw
    )


def test_target_holds_until_price_actually_trades_through_it():
    """The bug this replaces: targets recomputed from live spot every scan, so
    they retreated as price advanced and could never be reached."""
    opened = _step(81.0)
    assert opened["target"] == 83.0
    # Price advances but has not reached the target — target must not move.
    still = _step(82.4)
    assert still["target"] == 83.0
    assert still["extension_count"] == 0
    assert still["entry_price"] == 81.0


def test_reaching_the_target_extends_to_the_next_structural_level():
    _step(81.0)
    hit = _step(83.05)
    assert hit["extension_count"] == 1
    assert hit["target"] == 85.0
    assert hit["extensions"][0]["from"] == 83.0
    assert hit["extensions"][0]["to"] == 85.0
    assert hit["state"] == "active"


def test_a_large_jump_promotes_through_every_cleared_level_at_once():
    """Scans are minutes apart; a move can clear several walls between them."""
    _step(81.0)
    jumped = _step(86.6)
    assert jumped["extension_count"] == 3       # 83 -> 85 -> 86.5 -> 88
    assert jumped["target"] == 88.0


def test_structure_flip_closes_the_episode():
    _step(81.0)
    _step(83.05)
    done = _step(87.9, structure_flipped=True)
    assert done["state"] == "completed"
    assert done["close_reason"] == "structure flipped"
    assert not ep.active()
    assert ep.closed()[0]["ticker"] == "MU"


def test_invalidation_breach_closes_as_invalidated_not_completed():
    """A stopped-out episode and a finished one must not read the same."""
    _step(81.0)
    dead = _step(79.0, invalidation=79.5)
    assert dead["state"] == "invalidated"
    assert dead["close_reason"] == "invalidation breached"


def test_running_out_of_structure_completes_rather_than_extending_forever():
    ep.update("MU", direction="BULLISH", setup="FLOOR BOUNCE", spot=81.0,
              first_target=83.0, levels=[83.0])
    done = ep.update("MU", direction="BULLISH", setup="FLOOR BOUNCE", spot=83.1,
                     first_target=83.0, levels=[83.0])
    assert done["state"] == "completed"
    assert done["close_reason"] == "target reached, no further structure"


def test_extension_is_capped():
    levels = [81.0 + 0.5 * i for i in range(1, 40)]
    ep.update("MU", direction="BULLISH", setup="FLOOR BOUNCE", spot=81.0,
              first_target=81.5, levels=levels)
    done = ep.update("MU", direction="BULLISH", setup="FLOOR BOUNCE", spot=200.0,
                     first_target=81.5, levels=levels)
    assert done["extension_count"] == ep.MAX_EXTENSIONS
    assert done["state"] == "completed"


def test_bearish_episodes_extend_downward():
    walls = [77.0, 75.0, 72.5]
    ep.update("MU", direction="BEARISH", setup="CEILING REJECTION", spot=80.0,
              first_target=77.0, levels=walls)
    hit = ep.update("MU", direction="BEARISH", setup="CEILING REJECTION", spot=76.9,
                    first_target=77.0, levels=walls)
    assert hit["target"] == 75.0
    assert hit["extension_count"] == 1


def test_a_different_setup_on_the_same_ticker_starts_a_new_episode():
    first = _step(81.0)
    assert first["setup"] == "FLOOR BOUNCE"
    other = ep.update("MU", direction="BULLISH", setup="BREAKOUT CONTINUATION",
                      spot=84.0, first_target=86.0, levels=WALLS)
    assert other["entry_price"] == 84.0
    assert other["extension_count"] == 0


def test_progress_is_measured_against_the_anchored_entry():
    _step(81.0)
    mid = _step(82.0)
    # Halfway from 81 to 83.
    assert mid["progress_pct"] == pytest.approx(50.0, abs=0.1)
    assert mid["move_pct"] == pytest.approx(1.23, abs=0.02)
