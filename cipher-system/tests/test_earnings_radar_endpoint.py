"""Tests for the earnings-radar artifact endpoint and the ops health monitor."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core import app
from scripts import alert_health_monitor


def test_earnings_radar_reports_unavailable_when_no_artifact(monkeypatch):
    monkeypatch.setattr(app, "EARNINGS_RADAR_PATH", Path("/nonexistent/earnings_radar.json"))
    result = app.earnings_radar()
    assert result["status"] == "unavailable"
    assert result["cards"] == []
    assert "as_of" in result


def test_earnings_radar_serves_artifact_without_fabrication(monkeypatch, tmp_path):
    artifact = tmp_path / "earnings_radar.json"
    # Use a timestamp relative to "now" so the schedule-aware freshness check
    # sees the artifact as satisfying the latest due weekday run.
    fresh_as_of = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    artifact.write_text(json.dumps({
        "as_of": fresh_as_of,
        "days_ahead": 14,
        "count": 1,
        "paper_scorecard": {"total": 2, "open": 1, "settled": 1, "wins": 0},
        "cards": [{
            "symbol": "TGT", "scheduled_date": "2026-08-19", "days_until": 1,
            "eps_estimate_avg": 2.5, "direction_bias": "NEUTRAL",
            "confidence": 0.6, "expected_gap_pct": 3.0,
            "recommended_strategy": "Iron Condor", "rationale": "test card",
        }],
    }))
    monkeypatch.setattr(app, "EARNINGS_RADAR_PATH", artifact)
    result = app.earnings_radar()
    assert result["status"] == "current"
    assert result["count"] == 1
    assert result["cards"][0]["symbol"] == "TGT"
    assert result["paper_scorecard"]["settled"] == 1
    assert "caveat" in result


def test_earnings_radar_marks_unreadable_artifact(monkeypatch, tmp_path):
    artifact = tmp_path / "earnings_radar.json"
    artifact.write_text("{not json")
    monkeypatch.setattr(app, "EARNINGS_RADAR_PATH", artifact)
    result = app.earnings_radar()
    assert result["status"] == "unavailable"


def test_partial_scan_does_not_look_like_success(monkeypatch, tmp_path):
    artifact = tmp_path / 'radar.json'
    artifact.write_text(json.dumps({'as_of': datetime.now(timezone.utc).isoformat(), 'cards': [], 'data_status': 'partial', 'scan_diagnostics': {'errors': [{'stage': 'scan'}]}}))
    monkeypatch.setattr(app, 'EARNINGS_RADAR_PATH', artifact)
    result = app.earnings_radar()
    assert result['status'] == 'partial' and result['scan_diagnostics']['errors']


def test_radar_rejects_valid_json_wrong_shape(monkeypatch, tmp_path):
    artifact = tmp_path / 'radar.json'
    artifact.write_text('[]')
    monkeypatch.setattr(app, 'EARNINGS_RADAR_PATH', artifact)
    assert app.earnings_radar()['status'] == 'unavailable'


def test_earnings_radar_freshness_respects_weekend_schedule():
    produced = datetime(2026, 8, 21, 12, 16, tzinfo=timezone.utc)  # Friday 08:16 ET
    saturday = datetime(2026, 8, 22, 22, 0, tzinfo=timezone.utc)
    monday_before_grace = datetime(2026, 8, 24, 13, 0, tzinfo=timezone.utc)
    monday_after_grace = datetime(2026, 8, 24, 15, 0, tzinfo=timezone.utc)
    assert app._earnings_radar_is_stale(produced, saturday) is False
    assert app._earnings_radar_is_stale(produced, monday_before_grace) is False
    assert app._earnings_radar_is_stale(produced, monday_after_grace) is True


def test_health_monitor_classifies_failed_unit(monkeypatch):
    def fake_systemctl(*_args, **_kwargs):
        class Completed:
            stdout = (
                "  cipher-a.service loaded inactive dead x\n"
                "● cipher-b.service loaded failed   failed y\n"
            )
        return Completed()
    monkeypatch.setattr(alert_health_monitor, "_systemctl", fake_systemctl)
    monkeypatch.setattr(
        alert_health_monitor, "service_last_run",
        lambda unit: {
            "cipher-a.service": {"active": "inactive", "result": "success",
                                 "exec_ts": "Tue 2026-08-18 10:00:00 UTC"},
            "cipher-b.service": {"active": "failed", "result": "exit-code",
                                 "exec_ts": "Mon 2026-08-17 23:00:00 UTC"},
        }[unit],
    )
    findings = alert_health_monitor.collect(max_hours_default=30.0)
    units = {u["unit"]: u for u in findings["units"]}
    assert "cipher-b.service" in units
    assert units["cipher-b.service"]["status"] == "failed"
    assert units["cipher-a.service"]["status"] == "ok"
    message = alert_health_monitor.render(findings)
    assert "cipher-b.service" in message
    assert "cipher-a.service" not in message
