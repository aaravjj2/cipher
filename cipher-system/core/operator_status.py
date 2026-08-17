"""Bounded local operator/readiness status for the active research terminal."""
from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
BACKUPS = DATA / "backups" / "local_state"
ARCHIVE_LEDGER = DATA / "live_option_chains_archive.sqlite"
SMALL_STORES = (
    "alerts.sqlite", "watchlists.sqlite", "trader_journal.sqlite",
    "fronttest_portfolios/fronttest.sqlite", "gex_history.sqlite",
    "prospective_fronttests/prospective_fronttests.sqlite",
)
BACKUP_STORES = (
    "alerts.sqlite", "watchlists.sqlite", "trader_journal.sqlite",
    "fronttest_portfolios/fronttest.sqlite",
    "prospective_fronttests/prospective_fronttests.sqlite",
)
CAPTURES = {
    "tradier_stream": "tradier_stream.sqlite",
    "gex_snapshot": "gex_snapshots/**/*.json",
    "fronttest_portfolios": "fronttest_portfolios/fronttest.sqlite",
    "prospective_fronttests": "prospective_fronttests/prospective_fronttests.sqlite",
    "saved_scans": "scan_history/**/*.json",
}
CAPTURE_EXPECTATIONS = {
    "tradier_stream": {"current_seconds": 300, "market_bound": True},
    "gex_snapshot": {"current_seconds": 1800, "market_bound": True},
    "fronttest_portfolios": {"current_seconds": 3600, "market_bound": True},
    "prospective_fronttests": {"current_seconds": 3600, "market_bound": True},
    "saved_scans": {"current_seconds": 3600, "market_bound": False},
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _age(path: Path) -> dict:
    stat = path.stat()
    observed = datetime.fromtimestamp(stat.st_mtime, timezone.utc)
    return {"path": str(path.relative_to(DATA)), "bytes": stat.st_size,
            "observed_at": observed.isoformat(timespec="seconds"),
            "age_seconds": max(0, round((datetime.now(timezone.utc) - observed).total_seconds(), 1))}


def _newest(pattern: str) -> dict:
    matches = [path for path in DATA.glob(pattern) if path.is_file()]
    if not matches:
        return {"status": "UNAVAILABLE", "detail": "No matching local artifact."}
    return {"status": "AVAILABLE", **_age(max(matches, key=lambda path: path.stat().st_mtime))}


def _capture_state(name: str, item: dict, now: datetime) -> dict:
    if item.get("status") != "AVAILABLE":
        return item
    expectation = CAPTURE_EXPECTATIONS.get(name, {"current_seconds": 3600, "market_bound": False})
    age_seconds = float(item.get("age_seconds") or 0)
    local = now.astimezone(ZoneInfo("America/New_York"))
    regular = local.weekday() < 5 and (local.hour * 60 + local.minute) >= 570 and (local.hour * 60 + local.minute) < 960
    last_session_day: date = local.date()
    if regular:
        last_session_day = local.date()
    elif local.weekday() < 5 and (local.hour * 60 + local.minute) >= 960:
        last_session_day = local.date()
    else:
        last_session_day -= timedelta(days=1)
        while last_session_day.weekday() >= 5:
            last_session_day -= timedelta(days=1)
    observed_on_last_session = False
    try:
        observed = datetime.fromisoformat(str(item.get("observed_at"))).astimezone(local.tzinfo)
        observed_on_last_session = observed.date() == last_session_day
    except (TypeError, ValueError):
        # Compatibility for old callers/fixtures without an event timestamp.
        observed_on_last_session = age_seconds <= 36 * 3600
    if expectation["market_bound"] and not regular and (
        age_seconds <= expectation["current_seconds"] or observed_on_last_session
    ):
        state = "LAST_SESSION"
    elif age_seconds <= expectation["current_seconds"]:
        state = "CURRENT"
    else:
        state = "STALE"
    return {**item, "status": state, "market_bound": expectation["market_bound"]}


def database_status(relative: str, *, integrity_limit_bytes: int = 64 * 1024 * 1024) -> dict:
    path = DATA / relative
    if not path.is_file():
        return {"path": relative, "status": "UNAVAILABLE", "integrity": "NOT_RUN"}
    item = _age(path)
    uri = f"file:{path.as_posix()}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True, timeout=5) as db:
            tables = db.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
            if path.stat().st_size <= integrity_limit_bytes:
                integrity = db.execute("PRAGMA quick_check(1)").fetchone()[0]
                state = "OK" if integrity == "ok" else "ERROR"
            else:
                integrity, state = "NOT_RUN_LARGE_FILE", "AVAILABLE"
        return {**item, "status": state, "integrity": integrity, "table_count": tables}
    except sqlite3.Error as exc:
        return {**item, "status": "ERROR", "integrity": "ERROR", "error": str(exc)}


def _latest_backup() -> dict:
    manifests = sorted(BACKUPS.glob("*/manifest.json"), reverse=True) if BACKUPS.is_dir() else []
    if not manifests:
        return {"status": "UNAVAILABLE", "detail": "No verified local-state backup has been created."}
    try:
        manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"status": "ERROR", "error": str(exc)}
    return {"status": "VERIFIED" if manifest.get("restore_verified") else "UNVERIFIED",
            "created_at": manifest.get("created_at"), "store_count": len(manifest.get("stores") or []),
            "path": str(manifests[0].parent.relative_to(DATA))}


def _off_host_archive() -> dict:
    if not ARCHIVE_LEDGER.exists():
        return {"status": "UNAVAILABLE", "detail": "No verified off-host archive receipts."}
    try:
        with sqlite3.connect(f"file:{ARCHIVE_LEDGER.as_posix()}?mode=ro", uri=True) as db:
            count, deleted, last = db.execute("select count(*),sum(source_deleted),max(archived_at) from archive_receipts").fetchone()
        return {"status": "VERIFIED_RECEIPTS" if count else "EMPTY", "receipts": count,
                "verified_and_pruned": deleted or 0, "last_archived_at": last,
                "detail": "GCS objects are checksum-verified before any local source is pruned."}
    except sqlite3.Error as exc:
        return {"status": "ERROR", "error": str(exc)}


def status(*, caches: Iterable[dict] = ()) -> dict:
    from core import provider_telemetry
    usage = shutil.disk_usage(DATA.resolve())
    stores = [database_status(name) for name in SMALL_STORES]
    now = datetime.now(timezone.utc)
    captures = {name: _capture_state(name, _newest(pattern), now) for name, pattern in CAPTURES.items()}
    exceptions = []
    if usage.free / usage.total < 0.10:
        exceptions.append("Filesystem free space is below 10%.")
    exceptions.extend(f"Database {row['path']}: {row['status']}" for row in stores if row["status"] in {"ERROR", "UNAVAILABLE"})
    exceptions.extend(f"Capture {name}: {row['status']}" for name, row in captures.items() if row["status"] in {"STALE", "UNAVAILABLE"})
    telemetry_db = DATA / "operational_metrics.sqlite"
    runway = provider_telemetry.storage_runway(usage.free, path=telemetry_db)
    return {
        "generated_at": _now(), "read_only": True, "execution_capability": False,
        "disk": {"total_bytes": usage.total, "used_bytes": usage.used, "free_bytes": usage.free,
                 "free_percent": round(usage.free / usage.total * 100, 2),
                 "runway_status": runway["status"], "runway_days": runway.get("days"),
                 "detail": "Runway uses recorded dataset growth only after at least one full day of history."},
        "databases": stores, "captures": captures, "caches": list(caches),
        "provider_telemetry": provider_telemetry.summary(path=telemetry_db),
        "retention": provider_telemetry.retention_dry_run(data_root=DATA),
        "off_host_archive": _off_host_archive(),
        "backup": _latest_backup(), "exceptions": exceptions,
    }
