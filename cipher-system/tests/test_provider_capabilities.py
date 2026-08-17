from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "core") not in sys.path:
    sys.path.insert(0, str(ROOT / "core"))

import app  # noqa: E402


def test_full_alpaca_mode_reports_capabilities_without_secret_values():
    payload = app.provider_capabilities({
        "ALPACA_API_KEY": "public-looking-key",
        "ALPACA_API_SECRET": "super-secret-value",
        "ALPACA_DATA_FEED": "opra",
        "ALPACA_STOCK_FEED": "sip",
        "TRADIER_ACCESS_TOKEN": "tradier-secret",
    })

    assert payload["active_provider"] == "alpaca"
    assert payload["mode"] == "alpaca_opra_sip"
    assert payload["alpaca"]["options_feed"] == "opra"
    assert payload["alpaca"]["stock_feed"] == "sip"
    assert payload["alpaca"]["options_chain"] == "full"
    assert payload["alpaca"]["stock_quotes_bars"] == "full"
    assert payload["tradier"]["status"] == "capture_supplement_only"
    assert payload["webull"]["status"] == "unsupported"
    rendered = str(payload)
    assert "super-secret-value" not in rendered
    assert "tradier-secret" not in rendered


def test_standard_account_mode_is_explicitly_degraded():
    payload = app.provider_capabilities({
        "ALPACA_API_KEY": "key",
        "ALPACA_API_SECRET": "secret",
        "ALPACA_DATA_FEED": "indicative",
        "ALPACA_STOCK_FEED": "iex",
    })

    assert payload["mode"] == "alpaca_indicative_iex"
    assert payload["alpaca"]["options_chain"] == "degraded"
    assert payload["alpaca"]["stock_quotes_bars"] == "degraded"
    assert payload["alpaca"]["caveat"]


def test_missing_alpaca_credentials_are_unconfigured_not_unavailable_as_zero():
    payload = app.provider_capabilities({
        "ALPACA_DATA_FEED": "opra",
        "ALPACA_STOCK_FEED": "sip",
    })

    assert payload["mode"] == "unconfigured"
    assert payload["alpaca"]["credentials_configured"] is False
    assert payload["alpaca"]["options_chain"] is None
    assert payload["alpaca"]["stock_quotes_bars"] is None
    assert payload["read_only"] is True
    assert payload["live_execution_present"] is False
