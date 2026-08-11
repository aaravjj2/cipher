"""Build point-in-time Flash labels paired with historical local GEX features.

The browser capture repeatedly observes the same commercial card.  This module
keeps only the first admissible scored observation for each (session date,
ticker), paired to the newest local GEX snapshot that existed *at or before*
that card.  It never substitutes a later surface.  Pairs also fail closed when
the snapshot is more than 20 minutes old or the two observed spots differ by
0.5% or more.

GEX features use the project's fixed public-OI heuristic.  They are not verified
dealer positioning and are suitable only for this private research calibration.
"""
from __future__ import annotations

import csv
import json
import math
import os
import sqlite3
import tempfile
from bisect import bisect_right
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BROWSER_DIR = ROOT / "data" / "browser_ingest"
DEFAULT_GEX_DB = ROOT / "data" / "gex_history.sqlite"
DEFAULT_OUT = ROOT / "data" / "weight_lab" / "paired" / "paired_flash_gex_history_v1.jsonl"
PAIR_VERSION = 1


def _timestamp(value: object) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _number(value: object) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes"}


def _load_valid_observations(browser_dir: Path) -> tuple[list[dict], dict]:
    """Load valid scored observations before independent-group selection.

    Browser rows carry the card time explicitly.  UTC and New York share the
    same calendar date throughout regular US market hours, where eligible pairs
    can exist; using the normalized event date also avoids trusting filenames.
    """
    candidates: list[dict] = []
    rejected = Counter()
    files = sorted(browser_dir.glob("flash-observations-v2-*.csv"))
    for path in files:
        with path.open(newline="", encoding="utf-8") as handle:
            for row_number, row in enumerate(csv.DictReader(handle), start=2):
                ticker = str(row.get("ticker") or "").upper().lstrip("$").strip()
                observed_at = (
                    _timestamp(row.get("card_timestamp"))
                    or _timestamp(row.get("client_timestamp"))
                    or _timestamp(row.get("received_at"))
                )
                score = _number(row.get("score"))
                spot = _number(row.get("spot"))
                if not ticker or ticker == "TEST":
                    rejected["invalid_ticker"] += 1
                    continue
                if observed_at is None:
                    rejected["invalid_timestamp"] += 1
                    continue
                if score is None or not 0.0 <= score <= 100.0:
                    rejected["invalid_score"] += 1
                    continue
                if spot is None or spot <= 0:
                    rejected["invalid_spot"] += 1
                    continue
                if row.get("geometry_valid") not in (None, "") and not _truthy(row.get("geometry_valid")):
                    rejected["invalid_geometry"] += 1
                    continue
                candidates.append(
                    {
                        "session_date": observed_at.date().isoformat(),
                        "ticker": ticker,
                        "observed_at": observed_at,
                        "score": score,
                        "rank": _number(row.get("rank")),
                        "commercial_spot": spot,
                        "direction": row.get("direction"),
                        "setup_type": row.get("setup_type"),
                        "source_file": str(path),
                        "source_row": row_number,
                        "request_id": row.get("request_id"),
                    }
                )

    candidates.sort(key=lambda item: (item["observed_at"], item["source_file"], item["source_row"]))
    groups = {(row["session_date"], row["ticker"]) for row in candidates}
    return candidates, {
        "files": len(files),
        "valid_rows": len(candidates),
        "canonical_groups": len(groups),
        "duplicate_observations": len(candidates) - len(groups),
        "rejected": dict(rejected),
    }


def load_canonical_observations(browser_dir: Path = DEFAULT_BROWSER_DIR) -> tuple[list[dict], dict]:
    """Return the first valid scored row for each UTC session-date/ticker."""
    candidates, report = _load_valid_observations(browser_dir)
    canonical: dict[tuple[str, str], dict] = {}
    for row in sorted(candidates, key=lambda item: (item["observed_at"], item["source_file"], item["source_row"])):
        canonical.setdefault((row["session_date"], row["ticker"]), row)
    return list(canonical.values()), report


def _snapshot_index(db_path: Path) -> dict[str, list[dict]]:
    by_ticker: dict[str, list[dict]] = {}
    with sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=10) as db:
        rows = db.execute(
            "select id, ticker, captured_at, spot, raw_json_path, feed from gex_snapshots"
        )
        for snapshot_id, ticker, captured_at, spot, raw_path, feed in rows:
            captured = _timestamp(captured_at)
            if captured is None:
                continue
            by_ticker.setdefault(str(ticker).upper(), []).append(
                {
                    "snapshot_id": int(snapshot_id),
                    "captured_at": captured,
                    "spot": _number(spot),
                    "raw_json_path": str(raw_path),
                    "feed": feed,
                }
            )
    for values in by_ticker.values():
        values.sort(key=lambda item: item["captured_at"])
    return by_ticker


def _asof_snapshot(index: dict[str, list[dict]], ticker: str, observed_at: datetime) -> dict | None:
    rows = index.get(ticker) or []
    position = bisect_right([row["captured_at"] for row in rows], observed_at) - 1
    return rows[position] if position >= 0 else None


def _snapshot_features(snapshot: dict, *, as_of: date) -> tuple[dict | None, dict]:
    # Imports stay local so the corpus inspector can report join counts even when
    # optional numeric/scientific dependencies are unavailable.
    import scanner
    import weight_lab

    raw_path = Path(snapshot["raw_json_path"])
    if not raw_path.is_absolute():
        raw_path = ROOT / raw_path
    payload = json.loads(raw_path.read_text(encoding="utf-8"))
    spot = _number((payload.get("quote") or {}).get("price_context")) or snapshot["spot"]
    expirations = payload.get("expirations") or []
    profile = scanner._strike_profile(
        payload.get("rows"), cluster_exp="nearest", expirations=expirations
    )
    peaks = scanner._local_peaks(profile)
    model = scanner.cipher_model_from_profile(
        str(payload.get("ticker") or "").upper(),
        profile,
        peaks,
        payload.get("summary") or {},
        spot,
    )
    if not model:
        return None, {"reason": "no_model"}

    dte = None
    if expirations:
        first = expirations[0]
        label = first if isinstance(first, str) else first.get("expiration") or first.get("date")
        try:
            dte = max(0, (date.fromisoformat(str(label)[:10]) - as_of).days)
        except (TypeError, ValueError):
            dte = None
    features = weight_lab.features_from_model(model, profile, spot, dte=dte, as_of=as_of)
    return features, {
        "snapshot_spot": spot,
        "dte": dte,
        "expiration": str(expirations[0])[:10] if expirations else None,
        "profile_points": len(profile),
        "model_direction": model.get("direction"),
    }


def build_pairs(
    *,
    browser_dir: Path = DEFAULT_BROWSER_DIR,
    db_path: Path = DEFAULT_GEX_DB,
    max_age_minutes: float = 20.0,
    max_spot_drift_pct: float = 0.5,
) -> tuple[list[dict], dict]:
    """Build strict as-of pairs in memory and return records plus audit report."""
    import weight_lab

    observations, observation_report = _load_valid_observations(browser_dir)
    snapshots = _snapshot_index(db_path)
    records: list[dict] = []
    attempt_rejections = Counter()
    groups: dict[tuple[str, str], list[dict]] = {}
    for observation in observations:
        groups.setdefault((observation["session_date"], observation["ticker"]), []).append(observation)
    feature_cache: dict[tuple[int, str], tuple[dict | None, dict]] = {}

    for group_key in sorted(groups):
        paired = False
        for observation in groups[group_key]:
            snapshot = _asof_snapshot(snapshots, observation["ticker"], observation["observed_at"])
            if snapshot is None:
                attempt_rejections["no_prior_snapshot"] += 1
                continue
            age_minutes = (observation["observed_at"] - snapshot["captured_at"]).total_seconds() / 60.0
            if age_minutes < 0:  # Defensive invariant; _asof_snapshot should make this impossible.
                attempt_rejections["future_snapshot"] += 1
                continue
            if age_minutes > max_age_minutes:
                attempt_rejections["snapshot_too_old"] += 1
                continue
            snapshot_spot = snapshot["spot"]
            if not snapshot_spot or snapshot_spot <= 0:
                attempt_rejections["missing_snapshot_spot"] += 1
                continue
            drift = abs(observation["commercial_spot"] - snapshot_spot) / observation["commercial_spot"] * 100.0
            if drift >= max_spot_drift_pct:
                attempt_rejections["spot_drift"] += 1
                continue
            cache_key = (snapshot["snapshot_id"], observation["session_date"])
            try:
                if cache_key not in feature_cache:
                    feature_cache[cache_key] = _snapshot_features(
                        snapshot, as_of=date.fromisoformat(observation["session_date"])
                    )
                features, feature_meta = feature_cache[cache_key]
            except (OSError, ValueError, json.JSONDecodeError):
                attempt_rejections["snapshot_unreadable"] += 1
                continue
            if not features:
                attempt_rejections[feature_meta.get("reason", "feature_reconstruction_failed")] += 1
                continue
            if any(name not in features or not math.isfinite(float(features[name])) for name in weight_lab.FLASH_FEATURE_NAMES):
                attempt_rejections["missing_or_nonfinite_feature"] += 1
                continue

            records.append(
                {
                    "pair_version": PAIR_VERSION,
                    "session_date": observation["session_date"],
                    "ticker": observation["ticker"],
                    "score": observation["score"],
                    "rank": observation["rank"],
                    "direction": observation["direction"],
                    "setup_type": observation["setup_type"],
                    "observed_at": observation["observed_at"].isoformat(),
                    "commercial_spot": observation["commercial_spot"],
                    "features": {
                        name: float(features[name]) for name in weight_lab.FLASH_FEATURE_NAMES
                    },
                    "provenance": {
                        "label_source_file": observation["source_file"],
                        "label_source_row": observation["source_row"],
                        "request_id": observation["request_id"],
                        "snapshot_id": snapshot["snapshot_id"],
                        "snapshot_captured_at": snapshot["captured_at"].isoformat(),
                        "snapshot_raw_json_path": snapshot["raw_json_path"],
                        "snapshot_feed": snapshot["feed"],
                        "snapshot_age_minutes": round(age_minutes, 6),
                        "spot_drift_pct": round(drift, 6),
                        "join_rule": "newest snapshot at or before label",
                        "gex_caveat": "Public-OI heuristic, not verified dealer positioning.",
                        **feature_meta,
                    },
                }
            )
            paired = True
            break
        if not paired:
            attempt_rejections["groups_without_admissible_pair"] += 1

    report = {
        "pair_version": PAIR_VERSION,
        "pairs": len(records),
        "tickers": len({row["ticker"] for row in records}),
        "days": len({row["session_date"] for row in records}),
        "max_age_minutes": max_age_minutes,
        "max_spot_drift_pct_exclusive": max_spot_drift_pct,
        "observation_corpus": observation_report,
        "groups_without_pairs": observation_report["canonical_groups"] - len(records),
        "attempt_rejections": dict(attempt_rejections),
    }
    return records, report


def write_pairs(records: list[dict], out_path: Path = DEFAULT_OUT) -> Path:
    """Atomically replace the derived JSONL corpus; source captures are untouched."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{out_path.name}.", dir=out_path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
        os.replace(temporary, out_path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return out_path
