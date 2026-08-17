from __future__ import annotations

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
