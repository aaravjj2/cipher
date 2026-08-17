from __future__ import annotations

import time

from core import app


def test_flow_adds_explicit_freshness_to_captured_tape(monkeypatch) -> None:
    monkeypatch.setattr(app, "quote", lambda _ticker: {"price_context": 100.0})
    monkeypatch.setattr(
        app.tradier_flow,
        "flow",
        lambda *_args, **_kwargs: {"event_age_seconds": 240.0, "prints": [], "source": "tradier_stream"},
    )

    result = app.flow("AAPL", "opra")

    assert result["freshness"] == {"status": "stale", "age_seconds": 240.0}


def test_flow_marks_unknown_when_snapshot_has_no_event_clock(monkeypatch) -> None:
    monkeypatch.setattr(app, "quote", lambda _ticker: {"price_context": 100.0})
    monkeypatch.setattr(
        app.tradier_flow,
        "flow",
        lambda *_args, **_kwargs: {"event_age_seconds": None, "prints": [], "source": "tradier_stream"},
    )

    result = app.flow("AAPL", "opra")

    assert result["freshness"] == {"status": "unknown", "age_seconds": None}


def test_flow_returns_truthful_unknown_within_hard_budget(monkeypatch) -> None:
    def slow_flow(*_args, **_kwargs):
        time.sleep(0.15)
        return {"source": "alpaca_chain_snapshot", "prints": [{"contract": "late"}]}

    monkeypatch.setattr(app, "_flow_unbounded", slow_flow)
    monkeypatch.setattr(app, "FLOW_RESPONSE_BUDGET_SECONDS", 0.01)
    started = time.monotonic()

    result = app.flow("BUDGET", "opra")

    assert time.monotonic() - started < 0.1
    assert result["freshness"] == {"status": "unknown", "age_seconds": None}
    assert result["availability"]["status"] == "refreshing"
    assert result["availability"]["reason"] == "refresh_pending"
    assert result["prints"] == []
    assert "not represented as zero" in result["caveat"]


def test_flow_provider_failure_is_a_data_state_not_an_http_exception(monkeypatch) -> None:
    def failed_flow(*_args, **_kwargs):
        raise ValueError("provider unavailable")

    monkeypatch.setattr(app, "_flow_unbounded", failed_flow)

    result = app.flow("FAILFLOW", "opra")

    assert result["availability"]["status"] == "unavailable"
    assert result["availability"]["reason"] == "provider_error"
    assert result["coverage"]["status"] == "unknown"
    assert result["count"] == 0


def test_bounded_quote_returns_unknown_while_cold_quote_warms(monkeypatch) -> None:
    def slow_quote(_ticker):
        time.sleep(0.15)
        return {"ticker": "SLOWQ", "price_context": 123.0, "feed": "sip"}

    monkeypatch.setattr(app, "quote", slow_quote)
    monkeypatch.setattr(app, "QUOTE_RESPONSE_BUDGET_SECONDS", 0.01)
    started = time.monotonic()

    result = app.bounded_quote("SLOWQ")

    assert time.monotonic() - started < 0.1
    assert result["price_context"] is None
    assert result["availability"]["status"] == "refreshing"
    assert result["availability"]["reason"] == "refresh_pending"
