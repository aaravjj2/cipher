"""Timeframe resolution for /api/bars.

`bars()` used to end an unrecognized timeframe with `allowed.get(normalized, "5Min")`.
That default is the worst possible failure for a research tool: asking for daily bars and
getting 5-minute bars *labelled* daily produces a chart that looks entirely plausible and
is wrong. Night Vision's EOD button hit exactly that path — "EOD" lowercases to "eod",
which was not a key, so the panel drew five-minute candles under an EOD heading.

So these tests pin two things:
  1. every spelling that actually reaches this function resolves to the right Alpaca
     timeframe — including the Alpaca-style long forms internal callers already pass;
  2. anything else raises, rather than being silently served as 5-minute data.

Alpaca itself is stubbed; the assertion is on the `timeframe` this function *asks* for.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))

import app as core_app  # noqa: E402


@pytest.fixture
def asked(monkeypatch):
    """Capture the timeframe handed to Alpaca, with the bars cache neutralized."""
    seen = []

    def fake_alpaca(path, query=None):
        seen.append((query or {}).get("timeframe"))
        return {"bars": [{"t": "2026-08-10T13:30:00Z", "o": 1, "h": 2, "l": 1, "c": 2, "v": 10}]}

    monkeypatch.setattr(core_app, "alpaca", fake_alpaca)
    # A shared 20s cache would let one case answer another's question.
    with core_app._CACHE_LOCK:
        core_app.BARS_CACHE.clear()
    return seen


@pytest.mark.parametrize(
    "requested,expected",
    [
        # Canonical short forms — the documented API surface.
        ("1m", "1Min"),
        ("5m", "5Min"),
        ("15m", "15Min"),
        ("1h", "1Hour"),
        ("4h", "4Hour"),
        ("1d", "1Day"),
        ("1w", "1Week"),
        # The Night Vision EOD button. This is the bug that prompted the fix.
        ("eod", "1Day"),
        ("EOD", "1Day"),
        # Alpaca-style long forms passed by internal callers, e.g.
        # core/intraday_backtest.py's bars_fn(ticker, "5Min", ...).
        ("5Min", "5Min"),
        ("1Min", "1Min"),
        ("15Min", "15Min"),
        ("1Hour", "1Hour"),
        ("4Hour", "4Hour"),
        ("1Day", "1Day"),
        ("1Week", "1Week"),
        # Word forms, since "daily"/"weekly" read naturally in a query string.
        ("daily", "1Day"),
        ("weekly", "1Week"),
        # Case is not significant anywhere.
        ("1D", "1Day"),
    ],
)
def test_recognized_timeframes_reach_alpaca_intact(asked, requested, expected):
    core_app.bars("spy", requested, limit=5)
    assert asked[0] == expected, f"{requested!r} asked Alpaca for {asked[0]!r}"


@pytest.mark.parametrize("requested", ["2m", "3d", "1y", "1month", "tick", "", "5 min", "hourly"])
def test_unrecognized_timeframes_raise_instead_of_defaulting(asked, requested):
    with pytest.raises(ValueError) as err:
        core_app.bars("SPY", requested, limit=5)
    assert "unsupported timeframe" in str(err.value)
    # The important half: it must not have quietly fetched 5-minute bars anyway.
    assert asked == []


def test_the_reported_timeframe_is_the_canonical_one(asked):
    """An alias must not echo back as itself, or the caller cannot tell what it got."""
    out = core_app.bars("SPY", "eod", limit=5)
    assert out["timeframe"] == "1d"
    assert out["bars"]


def test_aliases_and_their_canonical_form_share_a_cache_entry(asked):
    """`eod` and `1d` are the same request; caching them apart would double the fetches."""
    core_app.bars("SPY", "1d", limit=5)
    core_app.bars("SPY", "eod", limit=5)
    assert len(asked) == 1, "the alias re-fetched instead of reusing the 1d cache entry"


def test_every_timeframe_the_night_vision_strip_can_send_is_accepted(asked):
    """Mirrors the `Timeframe` union in web/src/components/panels/NightVision.tsx.

    The panel sends `timeframe.toLowerCase()`, so this is the exact set of strings the
    route can receive from a button press. Now that an unknown value raises, a new button
    added to that union without a backend key would 422 in the user's face — this test is
    what makes that show up here instead.
    """
    ui_union = ["1D", "5m", "1m", "15m", "1H", "4H", "1W", "EOD"]
    reported = [core_app.bars("SPY", label.lower(), limit=5)["timeframe"] for label in ui_union]

    # Not one of the eight raises, and each returns bars.
    assert len(reported) == 8
    # "1D" and "EOD" are the same request, so they collapse to one canonical key and the
    # second is a cache hit — seven distinct fetches for eight buttons is correct here.
    assert reported == ["1d", "5m", "1m", "15m", "1h", "4h", "1w", "1d"]
    assert sorted(asked) == sorted(["1Day", "5Min", "1Min", "15Min", "1Hour", "4Hour", "1Week"])


def test_daily_and_intraday_do_not_collide_in_the_cache(asked):
    """The guard on the test above: distinct timeframes must still be distinct keys."""
    core_app.bars("SPY", "1d", limit=5)
    core_app.bars("SPY", "5m", limit=5)
    assert asked == ["1Day", "5Min"]
