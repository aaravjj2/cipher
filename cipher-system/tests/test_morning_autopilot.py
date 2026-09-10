from __future__ import annotations

from core import morning_autopilot


def test_liquidity_filter_uses_local_capture_when_live_data_is_absent(monkeypatch):
    def capture(ticker):
        if ticker == "NVDA":
            return {"contracts": [
                {"open_interest": 100}, {"open_interest": 50}, {"open_interest": None},
            ]}
        return None

    monkeypatch.setattr(morning_autopilot.tools, "latest_capture", capture)
    assert morning_autopilot.filter_liquid_options(["SPY", "NVDA"]) == ["NVDA"]


def test_liquidity_filter_uses_quotes_when_provider_omits_open_interest(monkeypatch):
    monkeypatch.setattr(morning_autopilot.tools, "latest_capture", lambda _ticker: {
        "contracts": [
            {"open_interest": None, "bid": 1.0, "ask": 1.1, "volume": 20},
            {"open_interest": None, "bid": 0.5, "ask": 0.6, "volume": 5},
            {"open_interest": None, "bid": 0, "ask": 0.1, "volume": 0},
        ],
    })
    assert morning_autopilot.filter_liquid_options(["NVDA"]) == ["NVDA"]


def test_missing_spot_is_a_skip_instead_of_an_exception(monkeypatch):
    monkeypatch.setattr(morning_autopilot, "rate_ticker", lambda _ticker: {
        "rating": 8, "confidence": 4, "option_strategy": "debit spread",
    })
    monkeypatch.setattr(morning_autopilot.tools, "dispatch", lambda *_args, **_kwargs: {
        "price_context": None, "mid": None, "last": None,
    })
    assert morning_autopilot.score_trade_opportunity("NVDA") is None
