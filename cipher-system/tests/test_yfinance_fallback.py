from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from core import app
from core import yfinance_provider


class Frame:
    def __init__(self, rows, index):
        self._rows = rows
        self.index = index

    def iterrows(self):
        return iter(zip(self.index, self._rows))


class FakeTicker:
    def __init__(self):
        self.fast_info = {"last_price": 101.25, "previous_close": 100.0}
        self.options = ("2026-09-18", "2026-10-16")
        self.calls = Frame(
            [
                {
                    "contractSymbol": "SPY260918C00100000",
                    "strike": 100.0,
                    "lastPrice": 2.5,
                    "bid": 2.4,
                    "ask": 2.6,
                    "volume": 120,
                    "openInterest": 500,
                    "impliedVolatility": 0.22,
                    "lastTradeDate": datetime(2026, 8, 18, 14, 30, tzinfo=timezone.utc),
                }
            ],
            [datetime(2026, 8, 18, 14, 30, tzinfo=timezone.utc)],
        )
        self.puts = Frame(
            [
                {
                    "contractSymbol": "SPY260918P00100000",
                    "strike": 100.0,
                    "lastPrice": 1.8,
                    "bid": 1.7,
                    "ask": 1.9,
                    "volume": 80,
                    "openInterest": 400,
                    "impliedVolatility": 0.24,
                    "lastTradeDate": datetime(2026, 8, 18, 14, 30, tzinfo=timezone.utc),
                }
            ],
            [datetime(2026, 8, 18, 14, 30, tzinfo=timezone.utc)],
        )

    def history(self, **_kwargs):
        return Frame(
            [
                {"Open": 99.0, "High": 101.0, "Low": 98.5, "Close": 100.0, "Volume": 1_000},
                {"Open": 100.0, "High": 102.0, "Low": 99.5, "Close": 101.25, "Volume": 1_200},
            ],
            [
                datetime(2026, 8, 17, tzinfo=timezone.utc),
                datetime(2026, 8, 18, tzinfo=timezone.utc),
            ],
        )

    def option_chain(self, expiration):
        assert expiration == "2026-09-18"
        return SimpleNamespace(calls=self.calls, puts=self.puts)


def test_yfinance_quote_is_explicitly_delayed_and_secret_free():
    result = yfinance_provider.quote("SPY", ticker_factory=lambda _symbol: FakeTicker())

    assert result["ticker"] == "SPY"
    assert result["price_context"] == 101.25
    assert result["prior_close"] == 100.0
    assert result["feed"] == "yahoo"
    assert result["provider"] == "yfinance"
    assert result["availability"]["status"] == "available"
    assert "delayed" in result["caveat"].lower()


def test_yfinance_bars_normalize_ohlcv_without_inventing_fields():
    result = yfinance_provider.bars(
        "SPY", "1d", limit=2, ticker_factory=lambda _symbol: FakeTicker()
    )

    assert result["ticker"] == "SPY"
    assert result["feed"] == "yahoo"
    assert result["provider"] == "yfinance"
    assert len(result["bars"]) == 2
    assert result["bars"][-1]["close"] == 101.25
    assert result["bars"][-1]["volume"] == 1_200


def test_yfinance_option_chain_is_limited_and_marks_missing_greeks():
    result = yfinance_provider.option_chain(
        "SPY", expiration_count=1, ticker_factory=lambda _symbol: FakeTicker()
    )

    assert len(result) == 2
    assert {row["type"] for row in result} == {"call", "put"}
    assert all(row["feed"] == "yahoo" for row in result)
    assert all(row["provider"] == "yfinance" for row in result)
    assert all(row["gamma"] is None and row["delta"] is None for row in result)
    assert result[0]["open_interest"] in {400, 500}


def test_matrix_uses_yfinance_chain_when_no_alpaca_session(monkeypatch):
    monkeypatch.setattr(app, "local_settings", lambda: (_ for _ in ()).throw(ValueError("no Alpaca session")))
    monkeypatch.setattr(
        app.yfinance_provider,
        "quote",
        lambda _ticker: {
            "ticker": "SPY", "bid": None, "ask": None, "mid": None,
            "last": 101.25, "price_context": 101.25,
            "price_context_kind": "delayed_close", "as_of": "2026-08-18T14:30:00+00:00",
            "feed": "yahoo", "provider": "yfinance", "day_change_pct": 1.25,
            "prior_close": 100.0, "availability": {"status": "available"},
            "caveat": "Delayed Yahoo Finance data.",
        },
    )
    monkeypatch.setattr(
        app.yfinance_provider,
        "option_chain",
        lambda *_args, **_kwargs: [
            {
                "symbol": "SPY260918C00100000", "type": "call", "strike": 100.0,
                "expiry": "2026-09-18", "bid": 2.4, "ask": 2.6, "mid": 2.5,
                "last": 2.5, "size": 120, "volume": 120, "open_interest": 500,
                "open_interest_date": None, "iv": 0.22, "gamma": None,
                "delta": None, "theta": None, "vega": None, "rho": None,
                "quote_time": "2026-08-18T14:30:00+00:00", "trade_time": None,
                "exchange": None, "feed": "yahoo", "provider": "yfinance",
            },
            {
                "symbol": "SPY260918P00100000", "type": "put", "strike": 100.0,
                "expiry": "2026-09-18", "bid": 1.7, "ask": 1.9, "mid": 1.8,
                "last": 1.8, "size": 80, "volume": 80, "open_interest": 400,
                "open_interest_date": None, "iv": 0.24, "gamma": None,
                "delta": None, "theta": None, "vega": None, "rho": None,
                "quote_time": "2026-08-18T14:30:00+00:00", "trade_time": None,
                "exchange": None, "feed": "yahoo", "provider": "yfinance",
            },
        ],
    )
    app.MATRIX_CACHE.clear()

    result = app.matrix("SPY", "opra", "0.06", 1, force=True)

    assert result["feed"] == "yahoo"
    assert result["rows"]
    assert result["coverage"]["contracts"] == 2
    assert "OPRA" in result["caveat"]
    assert result["coverage"]["open_interest_source"] != "Alpaca option-contract metadata"


def test_options_view_labels_yfinance_as_limited_without_opra(monkeypatch):
    view = app.options_terminal.chain_view(
        "SPY",
        {"price_context": 101.25, "as_of": "2026-08-18T14:30:00+00:00"},
        [{
            "symbol": "SPY260918C00100000", "type": "call", "strike": 100.0,
            "expiry": "2026-09-18", "bid": 2.4, "ask": 2.6, "mid": 2.5,
            "last": 2.5, "volume": 120, "open_interest": 500,
            "iv": 0.22, "gamma": None, "delta": None, "theta": None,
            "vega": None, "rho": None, "quote_time": None, "feed": "yahoo",
        }],
    )

    assert "OPRA" in view["caveat"].upper()
    assert "GREEKS" in view["caveat"].upper()


def test_flow_reports_opra_unavailable_instead_of_using_yfinance_as_a_tape(monkeypatch):
    monkeypatch.setattr(app, "local_settings", lambda: (_ for _ in ()).throw(ValueError("no Alpaca session")))
    monkeypatch.setattr(app.tradier_flow, "flow", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        app,
        "quote",
        lambda _ticker: {"price_context": 101.25, "feed": "yahoo", "provider": "yfinance"},
    )

    result = app._flow_unbounded("SPY", "opra", min_premium=100_000)

    assert result["count"] == 0
    assert result["source"] == "unavailable"
    assert "OPRA" in (result["caveat"] + " " + str(result["availability"].get("detail"))).upper()
    assert result["prints"] == []
