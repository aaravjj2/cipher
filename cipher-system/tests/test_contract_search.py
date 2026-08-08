"""Tick-rule classification guards for Spyglass Contract Search.

The buy/sell split is inferred, not reported, so the inference itself is what needs
pinning: if the rule silently changes, the panel keeps rendering confident numbers
that mean something different.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))

import app as core_app  # noqa: E402


def _chain(strike=100.0, kind="call", expiry="2026-08-14"):
    return [{
        "symbol": f"TEST260814C{int(strike * 1000):08d}",
        "type": kind, "strike": strike, "expiry": expiry,
        "open_interest": 500, "bid": 1.0, "ask": 1.2, "last": 1.1,
    }]


def _patch(monkeypatch, trades):
    monkeypatch.setattr(core_app, "option_chain", lambda *a, **k: _chain())
    monkeypatch.setattr(core_app, "resolve_options_feed", lambda f: "indicative")
    monkeypatch.setattr(core_app, "alpaca",
                        lambda path, query=None, base=None: {
                            "trades": {query["symbols"]: trades}, "next_page_token": None,
                        })


def test_upticks_count_as_bought_and_downticks_as_sold(monkeypatch):
    _patch(monkeypatch, [
        {"p": 1.00, "s": 10, "t": "2026-08-07T14:00:00Z", "x": "X"},  # no prior -> unclassified
        {"p": 1.10, "s": 20, "t": "2026-08-07T14:01:00Z", "x": "X"},  # uptick -> buy
        {"p": 1.05, "s": 30, "t": "2026-08-07T14:02:00Z", "x": "X"},  # downtick -> sell
    ])
    res = core_app.contract_search("TEST", "indicative", 100.0, "call")
    assert res["found"] is True
    assert res["buy_volume"] == 20
    assert res["sell_volume"] == 30
    assert res["unclassified_volume"] == 10
    assert res["volume"] == 60


def test_flat_trade_inherits_the_previous_direction(monkeypatch):
    """The zero-tick rule is the whole reason this is Lee-Ready rather than a naive
    comparison — a run of equal prices must not silently become unclassified."""
    _patch(monkeypatch, [
        {"p": 1.00, "s": 5, "t": "2026-08-07T14:00:00Z", "x": "X"},
        {"p": 0.90, "s": 5, "t": "2026-08-07T14:01:00Z", "x": "X"},   # downtick -> sell
        {"p": 0.90, "s": 40, "t": "2026-08-07T14:02:00Z", "x": "X"},  # flat -> still sell
    ])
    res = core_app.contract_search("TEST", "indicative", 100.0, "call")
    assert res["sell_volume"] == 45
    assert res["buy_volume"] == 0


def test_leading_trades_before_any_tick_are_not_guessed(monkeypatch):
    """With no prior print there is no direction to infer. Assigning one would
    invent a side, so those contracts are reported as unclassified."""
    _patch(monkeypatch, [
        {"p": 2.00, "s": 7, "t": "2026-08-07T14:00:00Z", "x": "X"},
        {"p": 2.00, "s": 3, "t": "2026-08-07T14:00:01Z", "x": "X"},
    ])
    res = core_app.contract_search("TEST", "indicative", 100.0, "call")
    assert res["unclassified_volume"] == 10
    assert res["buy_volume"] == res["sell_volume"] == 0
    assert res["buy_pct"] == 0.0


def test_missing_strike_reports_nearest_instead_of_empty(monkeypatch):
    monkeypatch.setattr(core_app, "option_chain", lambda *a, **k: [
        {"symbol": "T", "type": "call", "strike": s, "expiry": "2026-08-14"}
        for s in (90.0, 95.0, 105.0)
    ])
    monkeypatch.setattr(core_app, "resolve_options_feed", lambda f: "indicative")
    res = core_app.contract_search("TEST", "indicative", 100.0, "call")
    assert res["found"] is False
    assert 95.0 in res["nearest_strikes"] and 105.0 in res["nearest_strikes"]


def test_response_states_the_inference(monkeypatch):
    """The split must never be presented as exchange-reported fact."""
    _patch(monkeypatch, [{"p": 1.0, "s": 1, "t": "2026-08-07T14:00:00Z", "x": "X"}])
    res = core_app.contract_search("TEST", "indicative", 100.0, "call")
    assert "tick rule" in res["method"]
    assert "inferred" in res["caveat"]


def test_premium_uses_the_100_share_multiplier(monkeypatch):
    _patch(monkeypatch, [
        {"p": 1.00, "s": 10, "t": "2026-08-07T14:00:00Z", "x": "X"},
        {"p": 2.00, "s": 10, "t": "2026-08-07T14:01:00Z", "x": "X"},
    ])
    res = core_app.contract_search("TEST", "indicative", 100.0, "call")
    # 1.00*10*100 + 2.00*10*100
    assert res["premium"] == pytest.approx(3000.0)
    assert res["buy_premium"] == pytest.approx(2000.0)
