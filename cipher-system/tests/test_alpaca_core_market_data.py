from __future__ import annotations

from core.paper_executor.alpaca_core_market_data import AlpacaCoreMarketData, _timestamp
from core.paper_executor.config import MarketDataConfig


def _adapter() -> AlpacaCoreMarketData:
    return AlpacaCoreMarketData(MarketDataConfig(provider="alpaca_core"))


def test_nanosecond_timestamp_is_utc_and_truncated_safely():
    stamp = _timestamp("2026-08-17T15:05:03.418573088Z")
    assert stamp.isoformat() == "2026-08-17T15:05:03.418573+00:00"


def test_alpaca_core_adapter_normalizes_chain_and_quotes(monkeypatch):
    adapter = _adapter()
    chain_payload = {
        "feed": "opra",
        "expirations": [{
            "expiration": "2026-08-21",
            "rows": [{
                "strike": 225,
                "call": {
                    "symbol": "NVDA260821C00225000", "expiry": "2026-08-21",
                    "strike": 225, "type": "call", "bid": 4.0, "ask": 4.1,
                    "last": 4.05, "volume": 900, "open_interest": 1200,
                    "quote_time": "2026-08-17T15:05:03.418573088Z",
                },
                "put": None,
            }],
        }],
    }
    quote_payload = {
        "ticker": "NVDA", "bid": 226.4, "ask": 226.42, "last": 226.41,
        "as_of": "2026-08-17T15:05:04.123456789Z",
    }

    def fake_request(path, params):
        return chain_payload if path == "/api/options-chain" else quote_payload

    monkeypatch.setattr(adapter, "_request", fake_request)
    assert adapter.expirations("NVDA") == ["2026-08-21"]
    rows = adapter.chain("NVDA", "2026-08-21")
    assert rows[0]["expiration"] == "2026-08-21"
    quotes = adapter.quotes(["NVDA", "NVDA260821C00225000"])
    assert quotes["NVDA"].midpoint == 226.41
    assert quotes["NVDA260821C00225000"].open_interest == 1200


def test_alpaca_core_adapter_rejects_non_opra_chain(monkeypatch):
    adapter = _adapter()
    monkeypatch.setattr(adapter, "_request", lambda *_args, **_kwargs: {"feed": "indicative", "expirations": []})
    try:
        adapter.expirations("NVDA")
    except RuntimeError as exc:
        assert "OPRA" in str(exc)
    else:
        raise AssertionError("indicative fallback must not authorize paper entry")
