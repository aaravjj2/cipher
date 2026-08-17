from datetime import datetime, timedelta, timezone
from pathlib import Path

from core import provider_telemetry


def test_provider_summary_reports_latency_and_errors(tmp_path):
    db = tmp_path / "metrics.sqlite"
    provider_telemetry.record_provider("alpaca", "/v2/quotes", 10, "ok", path=db)
    provider_telemetry.record_provider("alpaca", "/v2/quotes", 30, "error", "HTTP_429", path=db)
    result = provider_telemetry.summary(path=db)
    row = result["providers"][0]
    assert row["requests"] == 2
    assert row["avg_latency_ms"] == 20
    assert row["p95_latency_ms"] == 30
    assert row["error_count"] == 1
    assert row["error_rate_pct"] == 50
    assert row["last_error"] == "HTTP_429"


def test_storage_runway_requires_real_elapsed_history(tmp_path):
    db, data = tmp_path / "metrics.sqlite", tmp_path / "data"
    archive = data / "gex_snapshots"
    archive.mkdir(parents=True)
    (archive / "first.json").write_bytes(b"x" * 100)
    start = datetime.now(timezone.utc) - timedelta(days=2)
    provider_telemetry.capture_storage(data, db, start.isoformat())
    assert provider_telemetry.storage_runway(10_000, db)["status"] == "INSUFFICIENT_HISTORY"
    (archive / "second.json").write_bytes(b"x" * 100)
    provider_telemetry.capture_storage(data, db, datetime.now(timezone.utc).isoformat())
    result = provider_telemetry.storage_runway(10_000, db)
    assert result["status"] == "ESTIMATED"
    assert result["growth_bytes_per_day"] == 50
    assert result["days"] == 200


def test_retention_policy_is_dry_run_only(tmp_path):
    old = tmp_path / "backtest_runs" / "old.json"
    old.parent.mkdir(parents=True)
    old.write_text("evidence", encoding="utf-8")
    timestamp = (datetime.now(timezone.utc) - timedelta(days=100)).timestamp()
    Path(old).touch()
    import os
    os.utime(old, (timestamp, timestamp))
    result = provider_telemetry.retention_dry_run(tmp_path)
    assert result["candidate_count"] == 1
    assert result["mode"] == "DRY_RUN_ONLY"
    assert result["destructive_action_enabled"] is False
    assert old.exists()
