"""RUNWAY clause parsing for captured Flash Agentic cards.

The agentic card labels this segment "RUNWAY" and prints it as a phrase, not a
bare percentage. Only the label "RUNWAY CLARITY" was mapped, so runway_clarity_pct
came back null on all 432 captured rows — while runway_clarity_norm carries the
largest coefficient in the fitted flash head. The label side of the most important
feature was empty, which is worth a test rather than a comment.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from capture_accessobsidian_scans import parse_runway  # noqa: E402
from reparse_flash_agentic import fields_from, first_card  # noqa: E402


def test_clean_runway_yields_full_clarity():
    row = {}
    assert parse_runway("clean (100% clear)", row) == 100.0
    assert row.get("runway_wall_strike") is None


def test_wall_runway_yields_clarity_plus_the_wall_detail():
    """The wall strike and level count explain WHY the runway is not clean, so
    they are recorded rather than discarded."""
    row = {}
    assert parse_runway("wall 980 (64% clear, 4 levels)", row) == 64.0
    assert row["runway_wall_strike"] == 980.0
    assert row["runway_levels"] == 4.0


def test_bare_percentage_still_parses():
    """The old "RUNWAY CLARITY" label emitted a plain value; that path must keep
    working so a differently-shaped card is not silently dropped."""
    assert parse_runway("64%", {}) == 64.0
    assert parse_runway("87", {}) == 87.0


def test_unparseable_runway_returns_none_rather_than_zero():
    """Zero is a real clarity value. An unreadable clause must not masquerade as
    a fully blocked runway."""
    assert parse_runway("", {}) is None
    assert parse_runway("clean", {}) is None


def test_reparse_recovers_the_same_fields_from_raw_card():
    raw = (
        "Active | $MU | BEARISH | 80/100 | REJECTION REVERSAL#7 | Edge 100 | Mixed | "
        "0% to target | SPOT | 991.705 | PIVOT | 1000 (ceiling, nearing) | TARGET | 983.4 | "
        "STRETCH | 970 | RUNWAY | wall 980 (64% clear, 4 levels) | INVALIDATION | 1020"
    )
    fields = fields_from(first_card(raw))
    assert fields["runway_clarity_pct"] == 64.0
    assert fields["runway_wall_strike"] == 980.0
    assert fields["runway_levels"] == 4.0
    assert fields["pivot_kind"] == "ceiling"
    assert fields["trigger_proximity"] == "nearing"
    assert fields["edge"] == 100.0
    assert fields["spot"] == pytest.approx(991.705)


def test_reparse_handles_a_single_card_row():
    """Backfill must reach clean rows too — the missing clarity was a label-mapping
    bug, not a card-boundary bug, so it affects well-split rows equally."""
    raw = "Active | $AMD | BULLISH | 57/100 | MOMENTUM PUSH#4 | Edge 89 | Trend | RUNWAY | clean (76% clear)"
    tokens = first_card(raw)
    assert tokens is not None
    assert fields_from(tokens)["runway_clarity_pct"] == 76.0


def test_first_card_still_splits_merged_cards():
    raw = "Active | $MU | BEARISH | RUNWAY | clean (65% clear) | DONE · 282s | $TSLA | BULLISH | RUNWAY | clean (99% clear)"
    tokens = first_card(raw)
    assert "$TSLA" not in tokens
    assert fields_from(tokens)["runway_clarity_pct"] == 65.0
