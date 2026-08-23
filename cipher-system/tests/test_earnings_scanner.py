from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from earnings_model import scanner


class _Ticker:
    def get_calendar(self):
        return {
            "Earnings Date": [date.today() + timedelta(days=2)],
            "Earnings Average": 2.0,
            "Earnings Low": 1.8,
            "Earnings High": 2.2,
        }

    def history(self, **_kwargs):
        return pd.DataFrame({"Close": [100.0 + index for index in range(30)]})


def test_current_price_drift_uses_latest_session_closes():
    drift = scanner.current_price_drift(_Ticker())
    assert drift["pre_5d_return_pct"] == (129.0 / 124.0 - 1.0) * 100.0
    assert drift["pre_20d_return_pct"] == (129.0 / 109.0 - 1.0) * 100.0


def test_upcoming_cards_pass_current_drift_into_model(monkeypatch):
    seen = {}
    monkeypatch.setattr(scanner.yf, "Ticker", lambda _symbol: _Ticker())
    monkeypatch.setattr(scanner, "is_etf", lambda _symbol: False)
    monkeypatch.setattr(scanner, "get_earnings_for_symbol", lambda _conn, _symbol: [])

    def predict(_symbol, conn=None, feature_overrides=None):
        seen.update(feature_overrides or {})
        return {
            "direction": "NEUTRAL / MIXED", "confidence": 0.5,
            "expected_gap_pct": 2.0, "prob_reversal": 0.25,
            "primary_strategy": "Research only", "rationale": "unvalidated",
            "inputs_snapshot": {
                "pre_5d_drift_pct": feature_overrides["pre_5d_return_pct"],
                "pre_20d_drift_pct": feature_overrides["pre_20d_return_pct"],
                "market_drift_source": "current",
                "pre_news_sentiment": 0.0,
            },
        }

    monkeypatch.setattr(scanner, "predict_for_symbol", predict)
    cards = scanner.find_upcoming_earnings(symbols=["NVDA"], conn=object())
    assert len(cards) == 1
    assert seen["pre_5d_return_pct"] > 0
    assert cards[0]["market_drift_source"] == "current"
    assert cards[0]["pre_drift_20d"] == seen["pre_20d_return_pct"]
    assert cards[0]["earnings_date_sources"] == ["yahoo_finance"]
    assert cards[0]["earnings_date_confirmation"] == "single_source_unconfirmed"
