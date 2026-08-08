"""Auto-save completed Setup Scanner scans to disk.

Every run_scan() completion (both sync and the async job path, which calls
run_scan() internally) gets written here — one JSON file per scan plus a
lightweight index for fast listing. Capped at MAX_SAVED_SCANS, oldest pruned
first.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from threading import Lock
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
HISTORY_DIR = ROOT / "data" / "scan_history"
INDEX_PATH = HISTORY_DIR / "index.json"
MAX_SAVED_SCANS = 200

_LOCK = Lock()


def _load_index() -> list[dict[str, Any]]:
    if not INDEX_PATH.is_file():
        return []
    try:
        return json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _write_index(entries: list[dict[str, Any]]) -> None:
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(json.dumps(entries, indent=2, default=str), encoding="utf-8")


def save_scan(result: dict[str, Any]) -> str | None:
    """Persist a completed scan result. Returns the scan id, or None on failure."""
    try:
        HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        scan_id = uuid.uuid4().hex[:12]
        top = result.get("top") or []
        entry = {
            "id": scan_id,
            "as_of": result.get("as_of"),
            "mode": result.get("mode"),
            "strategy": result.get("strategy"),
            "universe_size": result.get("universe_size"),
            "qualified": result.get("qualified"),
            "top_ticker": top[0]["ticker"] if top else None,
            "elapsed_ms": result.get("elapsed_ms"),
        }
        with _LOCK:
            (HISTORY_DIR / f"{scan_id}.json").write_text(
                json.dumps(result, default=str), encoding="utf-8"
            )
            entries = _load_index()
            entries.insert(0, entry)
            if len(entries) > MAX_SAVED_SCANS:
                for stale in entries[MAX_SAVED_SCANS:]:
                    stale_path = HISTORY_DIR / f"{stale['id']}.json"
                    stale_path.unlink(missing_ok=True)
                entries = entries[:MAX_SAVED_SCANS]
            _write_index(entries)
        return scan_id
    except OSError:
        return None


def list_scans(strategy: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    entries = _load_index()
    if strategy:
        entries = [e for e in entries if e.get("strategy") == strategy]
    return entries[: max(1, min(limit, MAX_SAVED_SCANS))]


def load_scan(scan_id: str) -> dict[str, Any] | None:
    safe_id = "".join(ch for ch in scan_id if ch.isalnum())
    path = HISTORY_DIR / f"{safe_id}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
