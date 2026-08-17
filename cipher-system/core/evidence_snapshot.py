"""Shared, read-only evidence identity for Scanner and Night Vision.

The snapshot describes what was observed, not what a strategy concluded.  A
Scanner card and a Night Vision view built from the same matrix payload therefore
receive the same ``snapshot_id`` even when their derived overlays differ.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any
from zoneinfo import ZoneInfo

SCHEMA_VERSION = 1
ET = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parents[1]
STORE_DIR = ROOT / "data" / "evidence_snapshots"
MAX_STORED = 500
_STORE_LOCK = Lock()


def _matrix_digest(payload: dict[str, Any]) -> str:
    """Hash the complete normalized matrix, including every strike cell."""
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _stamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _session(event: datetime | None) -> dict[str, Any]:
    if event is None:
        return {"timezone": "America/New_York", "market_date": None, "phase": "unknown"}
    local = event.astimezone(ET)
    minutes = local.hour * 60 + local.minute
    weekday = local.weekday() < 5
    if not weekday:
        phase = "closed"
    elif 240 <= minutes < 570:
        phase = "premarket"
    elif 570 <= minutes < 960:
        phase = "regular"
    elif 960 <= minutes < 1200:
        phase = "postmarket"
    else:
        phase = "closed"
    return {
        "timezone": "America/New_York",
        "market_date": local.date().isoformat(),
        "phase": phase,
    }


def _coverage(payload: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    raw = payload.get("coverage") or {}
    calculated = raw.get("calculated_cells")
    listed = raw.get("listed_cells")
    contracts = raw.get("contracts")
    missing: list[str] = []
    if calculated is None or listed is None or contracts is None:
        status = "unknown"
        missing.append("options_coverage_unknown")
    elif float(calculated) < 8 or float(contracts) < 20:
        status = "limited"
        missing.append("options_coverage_thin")
    else:
        status = "sufficient"
    if listed and calculated is not None and float(calculated) < float(listed):
        missing.append("some_listed_cells_uncalculated")
    if raw.get("contracts_missing_gamma"):
        missing.append("some_contract_gamma_unavailable")
    return ({
        "status": status,
        "calculated_cells": calculated,
        "listed_cells": listed,
        "contracts": contracts,
        "open_interest_as_of": raw.get("open_interest_as_of"),
        "open_interest_source": raw.get("open_interest_source"),
    }, missing)


def _exposure_levels(payload: dict[str, Any]) -> list[dict[str, Any]]:
    summary = payload.get("summary") or {}
    fields = (
        ("global_max", "Global", "global_max_strike"),
        ("call_wall", "Call wall", "call_wall_strike"),
        ("put_wall", "Put wall", "put_wall_strike"),
        ("gamma_flip", "Gamma flip", "gamma_flip_level"),
    )
    levels = []
    for kind, label, key in fields:
        price = _finite(summary.get(key))
        if price is not None:
            levels.append({
                "kind": kind, "label": label, "price": price,
                "origin": "public_oi_exposure_summary",
            })
    return levels


def build(payload: dict[str, Any], *, view: str, session_levels: dict | None = None) -> dict[str, Any]:
    """Build a deterministic evidence envelope from a matrix-compatible payload."""
    quote = payload.get("quote") or {}
    ticker = str(payload.get("ticker") or quote.get("ticker") or "").upper()
    event_at = quote.get("as_of") or payload.get("as_of")
    captured_at = payload.get("as_of")
    event = _stamp(event_at)
    captured = _stamp(captured_at) or datetime.now(timezone.utc)
    age_seconds = max(0.0, (captured - event).total_seconds()) if event else None
    freshness = (
        "unknown" if event is None else "current" if age_seconds is not None and age_seconds <= 120 else "stale"
    )
    coverage, missing = _coverage(payload)
    feed = str(payload.get("feed") or quote.get("feed") or "unknown").lower()
    spot = _finite(quote.get("price_context"))
    if spot is None:
        missing.append("spot_unknown")
    if event is None:
        missing.append("event_time_unknown")
    if feed != "opra":
        missing.append("options_feed_not_opra")

    exposure = _exposure_levels(payload)
    identity = {
        "schema_version": SCHEMA_VERSION,
        "ticker": ticker,
        "event_at": event.isoformat() if event else None,
        "captured_at": captured.isoformat(),
        "feed": feed,
        "spot": spot,
        "expirations": list(payload.get("expirations") or []),
        "coverage": coverage,
        "exposure_levels": exposure,
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    session_rows = []
    for level in (session_levels or {}).get("levels") or []:
        price = _finite(level.get("price"))
        if price is not None:
            session_rows.append({
                "kind": level.get("kind"), "label": level.get("label"), "price": price,
                "origin": "exchange_session_bars",
            })
    return {
        "schema_version": SCHEMA_VERSION,
        "snapshot_id": digest,
        "ticker": ticker,
        "view": view,
        "event_at": identity["event_at"],
        "captured_at": identity["captured_at"],
        "provider": "alpaca",
        "feed": feed,
        "spot": spot,
        "session": _session(event),
        "freshness": {"status": freshness, "age_seconds": age_seconds},
        "coverage": coverage,
        "levels": {"exposure": exposure, "session": session_rows},
        "missing_reasons": sorted(set(missing)),
        "caveats": [
            "GEX is a public-OI heuristic, not verified dealer positioning.",
            "Missing gamma, open interest, quotes, or cells remain unknown rather than zero.",
        ],
        "read_only": True,
        "execution_capability": False,
    }


def persist_matrix(payload: dict[str, Any], snapshot: dict[str, Any]) -> bool:
    """Persist a bounded frozen matrix artifact for explicit replay.

    The artifact contains only already-normalized market data returned by the local
    matrix engine. It never contains Alpaca credentials and is not an execution log.
    """
    snapshot_id = str(snapshot.get("snapshot_id") or "").lower()
    if len(snapshot_id) != 64 or any(ch not in "0123456789abcdef" for ch in snapshot_id):
        return False
    artifact = {
        "schema_version": SCHEMA_VERSION,
        "snapshot_id": snapshot_id,
        "ticker": snapshot.get("ticker"),
        "event_at": snapshot.get("event_at"),
        "captured_at": snapshot.get("captured_at"),
        "evidence_snapshot": snapshot,
        "matrix": payload,
        "matrix_sha256": _matrix_digest(payload),
        "read_only": True,
        "execution_capability": False,
    }
    try:
        with _STORE_LOCK:
            STORE_DIR.mkdir(parents=True, exist_ok=True)
            target = STORE_DIR / f"{snapshot_id}.json"
            temporary = STORE_DIR / f".{snapshot_id}.{os.getpid()}.tmp"
            temporary.write_text(
                json.dumps(artifact, separators=(",", ":"), allow_nan=False, default=str),
                encoding="utf-8",
            )
            os.replace(temporary, target)
            stored = sorted(STORE_DIR.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
            for stale in stored[MAX_STORED:]:
                stale.unlink(missing_ok=True)
        return True
    except (OSError, TypeError, ValueError):
        return False


def load_matrix(snapshot_id: str) -> dict[str, Any] | None:
    safe = str(snapshot_id or "").lower()
    if len(safe) != 64 or any(ch not in "0123456789abcdef" for ch in safe):
        return None
    path = STORE_DIR / f"{safe}.json"
    if not path.is_file():
        return None
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    matrix = artifact.get("matrix")
    frozen = artifact.get("evidence_snapshot")
    if artifact.get("snapshot_id") != safe or not isinstance(matrix, dict):
        return None
    if not isinstance(frozen, dict) or frozen.get("snapshot_id") != safe:
        return None
    # The evidence identity covers the provider event, feed, coverage, spot and
    # declared levels. Recomputing it protects even older artifacts that predate
    # the complete-matrix checksum.
    if build(matrix, view="integrity_check").get("snapshot_id") != safe:
        return None
    recorded_digest = artifact.get("matrix_sha256")
    calculated_digest = _matrix_digest(matrix)
    if recorded_digest is not None and recorded_digest != calculated_digest:
        return None
    artifact["integrity"] = {
        "snapshot_identity": "verified",
        "matrix_checksum": "verified" if recorded_digest is not None else "legacy_unavailable",
        "matrix_sha256": calculated_digest,
    }
    return artifact
