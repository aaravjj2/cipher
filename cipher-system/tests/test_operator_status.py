import json
import sqlite3

from core import operator_status, provider_telemetry
from scripts import backup_local_state


def _database(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE sample(id INTEGER PRIMARY KEY, value TEXT)")
        db.execute("INSERT INTO sample(value) VALUES('kept')")


def test_database_probe_is_read_only_and_labels_skipped_integrity(tmp_path, monkeypatch):
    monkeypatch.setattr(operator_status, "DATA", tmp_path)
    _database(tmp_path / "state.sqlite")
    checked = operator_status.database_status("state.sqlite")
    sampled = operator_status.database_status("state.sqlite", integrity_limit_bytes=1)
    assert checked["status"] == "OK" and checked["integrity"] == "ok"
    assert sampled["status"] == "AVAILABLE" and sampled["integrity"] == "NOT_RUN_LARGE_FILE"


def test_backup_is_hash_checked_and_restore_verified(tmp_path, monkeypatch):
    data, backups = tmp_path / "data", tmp_path / "backups"
    _database(data / "journal.sqlite")
    monkeypatch.setattr(operator_status, "DATA", data)
    monkeypatch.setattr(operator_status, "BACKUP_STORES", ("journal.sqlite", "missing.sqlite"))
    result = backup_local_state.backup(backups)
    manifest = json.loads((backups / result["path"].split("/")[-1] / "manifest.json").read_text())
    assert result["restore_verified"] is True
    assert len(manifest["stores"]) == 1
    assert len(manifest["stores"][0]["sha256"]) == 64


def test_operator_status_never_estimates_unproven_disk_runway(tmp_path, monkeypatch):
    monkeypatch.setattr(operator_status, "DATA", tmp_path)
    monkeypatch.setattr(operator_status, "SMALL_STORES", ("missing.sqlite",))
    monkeypatch.setattr(operator_status, "CAPTURES", {"capture": "missing/*.json"})
    monkeypatch.setattr(operator_status, "BACKUPS", tmp_path / "backups")
    monkeypatch.setattr(provider_telemetry, "DEFAULT_DB", tmp_path / "operational_metrics.sqlite")
    result = operator_status.status(caches=[{"name": "quote", "entries": 2}])
    assert result["execution_capability"] is False
    assert result["disk"]["runway_status"] == "INSUFFICIENT_HISTORY"
    assert result["disk"]["runway_days"] is None
    assert result["captures"]["capture"]["status"] == "UNAVAILABLE"
    assert result["exceptions"] == ["Database missing.sqlite: UNAVAILABLE", "Capture capture: UNAVAILABLE"]


def test_market_bound_capture_distinguishes_last_session_from_stale():
    from datetime import datetime, timezone
    recent = {"status": "AVAILABLE", "age_seconds": 8 * 3600}
    after_hours = datetime(2026, 8, 14, 4, 0, tzinfo=timezone.utc)  # midnight ET
    regular = datetime(2026, 8, 14, 15, 0, tzinfo=timezone.utc)  # 11:00 ET
    assert operator_status._capture_state("gex_snapshot", recent, after_hours)["status"] == "LAST_SESSION"
    assert operator_status._capture_state("gex_snapshot", recent, regular)["status"] == "STALE"


def test_market_bound_capture_treats_friday_as_last_session_through_weekend():
    from datetime import datetime, timezone
    friday_capture = {
        "status": "AVAILABLE", "age_seconds": 52 * 3600,
        "observed_at": "2026-08-14T20:15:00+00:00",
    }
    sunday_night = datetime(2026, 8, 17, 2, 0, tzinfo=timezone.utc)
    result = operator_status._capture_state("gex_snapshot", friday_capture, sunday_night)
    assert result["status"] == "LAST_SESSION"
