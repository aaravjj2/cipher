from __future__ import annotations

import json

from core import autopilot_status


def test_status_exposes_sanitized_paper_readiness_and_orders(monkeypatch):
    payload = {
        "mode": "paper",
        "reconciliation_passed": True,
        "quote_manager": {"degraded": False},
        "market_data_readiness": {"market_data_ready": True},
        "execution": {"backend": "alpaca_paper"},
        "paper_broker": {
            "backend": "alpaca_paper", "ready": True, "paper_only": True,
            "account": {"status": "ACTIVE", "currency": "USD", "equity": "100000"},
            "recent_orders": [{"id": "paper-1", "symbol": "MU260828C00100000", "status": "filled"}],
        },
        "observability": {"counts": {}, "open_shadow_positions": 0, "open_paper_positions": 0},
    }

    class Response:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def read(self): return json.dumps(payload).encode()

    monkeypatch.setattr(autopilot_status, "urlopen", lambda *_args, **_kwargs: Response())
    result = autopilot_status._executor("http://executor/status")
    assert result["execution_backend"] == "alpaca_paper"
    assert result["paper_broker"]["ready"] is True
    assert result["paper_broker"]["recent_orders"][0]["status"] == "filled"
    assert "secret" not in json.dumps(result).lower()


def test_status_has_no_browser_submission_capability():
    assert autopilot_status.snapshot()["live_execution_capability"] is False
