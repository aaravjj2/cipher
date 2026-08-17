from __future__ import annotations

import time

from core import morning_brief


def _status(*, exceptions: list[dict] | None = None) -> dict:
    return {
        "generated_at": "2026-08-17T14:00:00+00:00",
        "session": {"phase": "regular", "market_date": "2026-08-17"},
        "exceptions": exceptions or [],
        "items": [],
    }


def _quote(ticker: str) -> dict:
    return {
        "price_context": 100.0,
        "day_change_pct": 1.0,
        "as_of": "2026-08-17T14:00:00+00:00",
        "feed": "sip",
    }


def _flow(*_args, **_kwargs) -> dict:
    return {"prints": [], "source": "alpaca", "session_date": "2026-08-17"}


def _patch_dependencies(monkeypatch, prospective: dict) -> None:
    monkeypatch.setattr(morning_brief, "_gex_change", lambda _ticker: {"available": False})
    monkeypatch.setattr(morning_brief, "_alert_states", lambda: {"rules": []})
    monkeypatch.setattr(morning_brief.scan_history, "list_scans", lambda **_kwargs: [])
    monkeypatch.setattr(
        morning_brief.paper_portfolio_api,
        "snapshot",
        lambda: {"portfolios": [], "combined_realized_pnl": 0.0},
    )
    monkeypatch.setattr(
        morning_brief.holdings,
        "holdings_status",
        lambda **_kwargs: {"positions": [], "unresolved": []},
    )
    monkeypatch.setattr(
        morning_brief.prospective_fronttest_api,
        "snapshot",
        lambda **_kwargs: prospective,
    )


def test_brief_prioritizes_prospective_coverage_and_open_signals(monkeypatch) -> None:
    prospective = {
        "as_of": "2026-08-17T14:00:00+00:00",
        "latest_coverage": {
            "run_id": 9, "observed": 2, "fresh": 2, "partial": 0,
            "stale": 0, "missing": 0, "signals_opened": 1,
        },
        "programs": [{
            "program_id": "p1", "name": "Program", "kind": "weekly_radar",
            "effective_status": "COLLECTING", "minimum_sample": 20,
            "eligible_signals": 1, "open_signals": 1, "closed_signals": 0,
            "void_signals": 0, "wins": 0, "sample_progress": 0.0,
            "closed_option_pnl": 0.0,
        }],
        "signals": [{
            "signal_id": "s1", "program_id": "p1", "ticker": "TSLA",
            "setup_id": "rejection", "direction": "long", "status": "OPEN",
            "signal_bar_at": "2026-08-17T13:55:00+00:00", "underlying_entry": 341.5,
            "target": 360.0, "deadline_at": None, "option_selection_status": "SELECTED",
        }],
        "observations": [{
            "run_id": 9, "program_id": "p1", "ticker": "TSLA",
            "observed_at": "2026-08-17T14:00:00+00:00", "latest_bar_at": None,
            "coverage_status": "FRESH", "decision": "SIGNAL_OPENED", "reason": "QUALIFIED",
        }],
    }
    _patch_dependencies(monkeypatch, prospective)

    result = morning_brief.build(
        ticker="TSLA", quote_fn=_quote, flow_fn=_flow, status_payload=_status()
    )

    assert result["prospective_fronttests"]["latest_coverage"]["fresh"] == 2
    assert result["prospective_fronttests"]["open_signals"][0]["ticker"] == "TSLA"
    assert result["prospective_fronttests"]["execution_capability"] is False
    assert result["attention"] == []


def test_brief_surfaces_nonfresh_coverage_voids_and_data_exceptions(monkeypatch) -> None:
    prospective = {
        "as_of": None,
        "latest_coverage": {
            "run_id": 10, "observed": 2, "fresh": 0, "partial": 0,
            "stale": 1, "missing": 1, "signals_opened": 0,
        },
        "programs": [],
        "signals": [{"status": "VOID"}],
        "observations": [],
    }
    _patch_dependencies(monkeypatch, prospective)
    result = morning_brief.build(
        ticker="SPY",
        quote_fn=_quote,
        flow_fn=_flow,
        status_payload=_status(exceptions=[{"name": "flow", "state": "unavailable"}]),
    )

    kinds = [item["kind"] for item in result["attention"]]
    assert kinds == ["data_exception", "prospective_coverage", "integrity_exclusion"]
    assert result["attention"][0]["severity"] == "error"
    assert "unavailable" in result["attention"][0]["detail"]
    assert "1 stale, 1 missing" in result["attention"][1]["detail"]


def test_brief_preserves_pending_flow_as_unknown_not_zero(monkeypatch) -> None:
    prospective = {
        "as_of": None,
        "latest_coverage": {
            "run_id": None, "observed": 0, "fresh": 0, "partial": 0,
            "stale": 0, "missing": 0, "signals_opened": 0,
        },
        "programs": [], "signals": [], "observations": [],
    }
    _patch_dependencies(monkeypatch, prospective)

    result = morning_brief.build(
        ticker="AAPL",
        quote_fn=_quote,
        flow_fn=lambda *_args, **_kwargs: {
            "prints": [],
            "source": "unavailable",
            "freshness": {"status": "unknown", "age_seconds": None},
            "availability": {"status": "refreshing", "reason": "refresh_pending"},
            "coverage": {"status": "unknown"},
            "caveat": "Missing flow remains unknown and is not represented as zero.",
        },
        status_payload=_status(),
    )

    assert result["significant_flow"]["availability"]["status"] == "refreshing"
    assert result["significant_flow"]["coverage"]["status"] == "unknown"
    assert "not represented as zero" in result["significant_flow"]["caveat"]


def test_brief_market_context_has_one_shared_hard_budget(monkeypatch) -> None:
    prospective = {
        "as_of": None,
        "latest_coverage": {
            "run_id": None, "observed": 0, "fresh": 0, "partial": 0,
            "stale": 0, "missing": 0, "signals_opened": 0,
        },
        "programs": [], "signals": [], "observations": [],
    }
    _patch_dependencies(monkeypatch, prospective)
    monkeypatch.setattr(morning_brief, "BRIEF_MARKET_BUDGET_SECONDS", 0.01)

    def slow_quote(_ticker: str) -> dict:
        time.sleep(0.15)
        return _quote(_ticker)

    started = time.monotonic()
    result = morning_brief.build(
        ticker="AAPL", quote_fn=slow_quote, flow_fn=_flow,
        status_payload=_status(),
    )

    assert time.monotonic() - started < 0.1
    assert [row["ticker"] for row in result["market"]] == ["SPY", "QQQ", "IWM"]
    assert all(row["price"] is None for row in result["market"])
    assert all(row["availability"]["status"] == "refreshing" for row in result["market"])
