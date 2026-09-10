from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

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


def _refused_transport(*_args, **_kwargs):
    raise OSError("network off in tests")


@pytest.fixture(autouse=True)
def _offline_nasdaq(monkeypatch):
    """Default test posture: Nasdaq transport refused and cache empty.

    Refusing at the socket layer keeps the real fetch/cache/cross-check code
    paths exercised while guaranteeing no test touches the network; individual
    tests may stub higher-level seams (_fetch_nasdaq_day_map or
    _nasdaq_earnings_for_day) on top of this.
    """
    monkeypatch.setattr(scanner, "_nasdaq_day_cache", {})
    monkeypatch.setattr(scanner.urllib.request, "urlopen", _refused_transport)


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
    # Second-source values are recorded even when Nasdaq is unreachable.
    assert cards[0]["yahoo_earnings_date"] == cards[0]["scheduled_date"]
    assert cards[0]["nasdaq_earnings_date"] is None
    assert cards[0]["earnings_date_crosscheck"] == "SECOND_SOURCE_UNAVAILABLE"


def _prediction_patch(monkeypatch):
    monkeypatch.setattr(scanner.yf, "Ticker", lambda _symbol: _Ticker())
    monkeypatch.setattr(scanner, "is_etf", lambda _symbol: False)
    monkeypatch.setattr(scanner, "get_earnings_for_symbol", lambda _conn, _symbol: [])
    monkeypatch.setattr(scanner, "predict_for_symbol", lambda *_a, **_k: {
        "direction": "BULLISH", "confidence": 0.6,
        "expected_gap_pct": 3.0, "prob_reversal": 0.25,
        "primary_strategy": "Debit Bull Call Spread", "rationale": "test",
        "inputs_snapshot": {},
    })


def _nasdaq_lists_everywhere(monkeypatch, symbol="NVDA"):
    monkeypatch.setattr(
        scanner, "_nasdaq_earnings_for_day", lambda day: {symbol: "Before Open"}
    )


def test_radar_confirms_date_when_nasdaq_agrees(monkeypatch):
    _prediction_patch(monkeypatch)
    _nasdaq_lists_everywhere(monkeypatch)
    cards = scanner.find_upcoming_earnings(symbols=["NVDA"], conn=object())
    assert cards[0]["earnings_date_confirmation"].startswith("CONFIRMED_YAHOO_NASDAQ")
    assert cards[0]["earnings_date_sources"] == ["yahoo_finance", "nasdaq"]
    assert cards[0]["earnings_date_crosscheck"] == "CONFIRMED_EXACT"
    assert cards[0]["nasdaq_earnings_date"] == cards[0]["scheduled_date"]


def test_parse_nasdaq_calendar_payload_extracts_symbols():
    payload = {"data": {"rows": [
        {"symbol": "nvda", "time": "Before Market Open"},
        {"symbol": "", "time": "junk"},
        "not-a-row",
        {"time": "no symbol"},
        {"symbol": "AAPL"},
    ], "asOf": "2026-08-24"}}
    assert scanner._parse_nasdaq_calendar_payload(payload) == {
        "NVDA": "Before Market Open", "AAPL": "",
    }
    assert scanner._parse_nasdaq_calendar_payload(None) == {}
    assert scanner._parse_nasdaq_calendar_payload({"data": None}) == {}


class _FixedDayNasdaq:
    """Fake fetcher answering per ISO day; records every call."""

    def __init__(self, mapping_by_iso):
        self.mapping = mapping_by_iso
        self.calls = []

    def __call__(self, day):
        self.calls.append(day.isoformat())
        return self.mapping.get(day.isoformat())


MONDAY = date(2026, 8, 24)  # a known trading day


def test_cross_check_confirms_exact_match(monkeypatch):
    monkeypatch.setattr(scanner, "_nasdaq_day_cache", {})
    fake = _FixedDayNasdaq({MONDAY.isoformat(): {"NVDA": ""}})
    monkeypatch.setattr(scanner, "_fetch_nasdaq_day_map", fake)

    result = scanner.cross_check_earnings_date("nvda", MONDAY)
    assert result == {
        "confirmation_status": "CONFIRMED_EXACT", "confirmed": True,
        "nasdaq_date": "2026-08-24",
    }


def test_cross_check_confirms_adjacent_trading_day(monkeypatch):
    monkeypatch.setattr(scanner, "_nasdaq_day_cache", {})
    # Monday report that Nasdaq lists on the prior Friday.
    fake = _FixedDayNasdaq({"2026-08-21": {"NVDA": ""}, "2026-08-25": {"MSFT": ""}})
    monkeypatch.setattr(scanner, "_fetch_nasdaq_day_map", fake)

    result = scanner.cross_check_earnings_date("NVDA", MONDAY)
    assert result["confirmed"] is True
    assert result["confirmation_status"] == "CONFIRMED_ADJACENT_PRIOR_TRADING_DAY"
    assert result["nasdaq_date"] == "2026-08-21"


def test_cross_check_keeps_unconfirmed_flag_on_disagreement(monkeypatch):
    monkeypatch.setattr(scanner, "_nasdaq_day_cache", {})
    # Source reachable but the symbol is absent across the whole ±1 window.
    fake = _FixedDayNasdaq({
        "2026-08-21": {"AAPL": ""}, "2026-08-24": {}, "2026-08-25": {},
    })
    monkeypatch.setattr(scanner, "_fetch_nasdaq_day_map", fake)

    result = scanner.cross_check_earnings_date("NVDA", MONDAY)
    assert result == {
        "confirmation_status": "DISAGREED_WITHIN_ONE_TRADING_DAY",
        "confirmed": False, "nasdaq_date": None,
    }


def test_cross_check_reports_unavailable_second_source(monkeypatch):
    monkeypatch.setattr(scanner, "_nasdaq_day_cache", {})

    def refused(*_args, **_kwargs):
        raise OSError("blocked UA / offline")

    # Patch the transport so the real fetch wrapper's catch-all is exercised.
    monkeypatch.setattr(scanner.urllib.request, "urlopen", refused)

    result = scanner.cross_check_earnings_date("NVDA", MONDAY)
    assert result == {
        "confirmation_status": "SECOND_SOURCE_UNAVAILABLE",
        "confirmed": False, "nasdaq_date": None,
    }


def test_nasdaq_fetch_failures_never_raise(monkeypatch):
    def boom(_url, **_kwargs):
        raise OSError("blocked UA")

    monkeypatch.setattr(scanner.urllib.request, "urlopen", boom)
    assert scanner._fetch_nasdaq_day_map(MONDAY) is None


def test_nasdaq_day_maps_are_ttl_cached(monkeypatch):
    monkeypatch.setattr(scanner, "_nasdaq_day_cache", {})
    fake = _FixedDayNasdaq({
        iso: {"NVDA": ""}
        for iso in ("2026-08-21", "2026-08-24", "2026-08-25")
    })
    monkeypatch.setattr(scanner, "_fetch_nasdaq_day_map", fake)

    first = scanner.cross_check_earnings_date("NVDA", MONDAY)
    second = scanner.cross_check_earnings_date("NVDA", MONDAY)
    assert first["confirmed"] and second["confirmed"]
    assert len(fake.calls) == 3  # three candidate days fetched exactly once each


def test_model_error_preserves_calendar_without_fabrication(monkeypatch):
    _prediction_patch(monkeypatch)
    monkeypatch.setattr(scanner, 'predict_for_symbol', lambda *a, **k: {'error': 'no historical data'})
    diagnostics = {}
    cards = scanner.find_upcoming_earnings(symbols=['NVDA'], conn=object(), diagnostics=diagnostics)
    assert cards[0]['forecast_status'] == 'UNAVAILABLE'
    assert cards[0]['expected_gap_pct'] is None and cards[0]['hist_beat_rate'] is None
    assert cards[0]['recommended_strategy'].startswith('NO TRADE')
    assert diagnostics['status'] == 'partial'
    assert 'unknown' in scanner.render_radar_table(cards)


def test_history_failure_preserves_event_and_blocks_entry(monkeypatch):
    _prediction_patch(monkeypatch)
    monkeypatch.setattr(scanner, 'current_price_drift', lambda ticker: (_ for _ in ()).throw(OSError('offline')))
    cards = scanner.find_upcoming_earnings(symbols=['NVDA'], conn=object())
    assert cards[0]['forecast_status'] == 'DEGRADED_INPUTS'
    assert cards[0]['pre_drift_20d'] is None
    assert cards[0]['strategy_eligible'] is False


def test_total_calendar_outage_is_not_successful_empty_scan(monkeypatch):
    _prediction_patch(monkeypatch)
    monkeypatch.setattr(scanner.yf, 'Ticker', lambda s: (_ for _ in ()).throw(OSError('offline')))
    diagnostics = {}
    assert scanner.find_upcoming_earnings(symbols=['NVDA'], conn=object(), diagnostics=diagnostics) == []
    assert diagnostics['status'] == 'unavailable' and len(diagnostics['errors']) == 1
