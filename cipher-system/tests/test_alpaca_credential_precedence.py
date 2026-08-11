"""Every module must resolve Alpaca credentials plus-tier-first.

`live_option_chain_capture.get_credentials` returns the OPRA options feed, which requires
the higher-tier subscription, but resolved the base credential first while every other
module resolved plus first. Both Secret Manager entries currently hold the same value, so
the inconsistency was invisible; the day a genuinely higher-tier plus key is configured it
would silently request OPRA with the lower-tier credential.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "core") not in sys.path:
    sys.path.insert(0, str(ROOT / "core"))

import live_option_chain_capture as capture  # noqa: E402


def test_plus_tier_credentials_win_when_both_are_present():
    key, secret, feed = capture.get_credentials({
        "ALPACA_ALGO_KEY": "base-key",
        "ALPACA_ALGO_SECRET": "base-secret",
        "ALPACA_ALGO_PLUS_KEY": "plus-key",
        "ALPACA_ALGO_PLUS_SECRET": "plus-secret",
    })
    assert (key, secret) == ("plus-key", "plus-secret")
    # The feed is why the precedence matters.
    assert feed == "opra"


def test_base_credentials_are_still_used_when_plus_is_absent():
    key, secret, _ = capture.get_credentials({
        "ALPACA_ALGO_KEY": "base-key", "ALPACA_ALGO_SECRET": "base-secret",
    })
    assert (key, secret) == ("base-key", "base-secret")


def test_generic_api_credentials_are_the_last_resort():
    key, secret, _ = capture.get_credentials({
        "ALPACA_API_KEY": "generic", "ALPACA_API_SECRET": "generic-secret",
    })
    assert (key, secret) == ("generic", "generic-secret")


def test_missing_credentials_raise_rather_than_returning_empty():
    with pytest.raises(ValueError, match="not configured"):
        capture.get_credentials({})
    # A key without its secret is also unusable and must not half-succeed.
    with pytest.raises(ValueError, match="not configured"):
        capture.get_credentials({"ALPACA_ALGO_PLUS_KEY": "plus-key"})


def test_an_unrecognized_options_feed_falls_back_to_opra():
    _, _, feed = capture.get_credentials({
        "ALPACA_ALGO_KEY": "k", "ALPACA_ALGO_SECRET": "s", "ALPACA_DATA_FEED": "nonsense",
    })
    assert feed == "opra"
    _, _, indicative = capture.get_credentials({
        "ALPACA_ALGO_KEY": "k", "ALPACA_ALGO_SECRET": "s", "ALPACA_DATA_FEED": "INDICATIVE",
    })
    assert indicative == "indicative"


def test_the_other_modules_agree_on_precedence():
    """Pins the convention this file was the exception to."""
    sources = {
        "core/data_fetcher.py": 'creds.get("ALPACA_ALGO_PLUS_KEY") or creds.get("ALPACA_ALGO_KEY")',
        "core/research_platform/market_data_providers.py":
            'os.environ.get("ALPACA_ALGO_PLUS_KEY") or os.environ.get("ALPACA_ALGO_KEY")',
    }
    for relative, expected in sources.items():
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert expected in text, f"{relative} no longer resolves plus-tier first"
