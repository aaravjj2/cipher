"""Leakage-safe dataset export for future Cipher ranking models.

Only closed shadow positions are eligible.  Features come from the last scanner
observation at or before the simulated fill; marks and exits are labels only.
The exporter does not train or promote a model.  It records why training is
blocked until the prospective sample is large and spans enough market dates.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


SCHEMA_VERSION = 1
DEFAULT_DB = Path(__file__).resolve().parents[2] / "data" / "paper_runtime" / "data" / "paper_trades" / "autopilot_shadow.sqlite"
DEFAULT_OUTPUT = Path(__file__).resolve().parents[2] / "data" / "paper_runtime" / "autopilot" / "training"


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _json(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _sample(db: sqlite3.Connection, position: sqlite3.Row) -> dict[str, Any] | None:
    update = db.execute(
        """select * from episode_updates
           where episode_id = ? and seen_at <= ?
           order by seen_at desc limit 1""",
        (position["episode_id"], position["opened_at"]),
    ).fetchone()
    if not update:
        return None
    raw = _json(update["payload_json"])
    evidence = raw.get("evidence_snapshot") or {}
    autopilot = raw.get("autopilot") or {}
    sentiment = autopilot.get("sentiment") or {}
    marks = db.execute(
        "select min(pnl_pct), max(pnl_pct), count(*) from paper_marks where position_id = ?",
        (position["id"],),
    ).fetchone()
    entry = float(position["entry_price"])
    exit_price = float(position["exit_price"])
    pnl_pct = (exit_price - entry) / entry * 100.0 if entry > 0 else None
    opened = datetime.fromisoformat(position["opened_at"])
    closed = datetime.fromisoformat(position["closed_at"])
    local = opened.astimezone(ZoneInfo("America/New_York"))
    features = {
        "ticker": position["ticker"],
        "direction": position["direction"],
        "setup": raw.get("setup_type") or raw.get("setup"),
        "score": _number(raw.get("score")),
        "reward_risk": _number(raw.get("reward_risk")),
        "spot": _number(raw.get("spot")),
        "target_distance_pct": (
            abs(float(update["target"]) - float(update["spot"])) / float(update["spot"]) * 100
            if update["target"] is not None and update["spot"] else None
        ),
        "invalidation_distance_pct": (
            abs(float(update["invalidation"]) - float(update["spot"])) / float(update["spot"]) * 100
            if update["invalidation"] is not None and update["spot"] else None
        ),
        "coverage_status": (evidence.get("coverage") or {}).get("status"),
        "options_feed": evidence.get("feed"),
        "evidence_snapshot_id": evidence.get("snapshot_id"),
        "premarket_evidence_snapshot_id": autopilot.get("premarket_evidence_snapshot_id"),
        "finbert_score": _number(sentiment.get("score")),
        "finbert_status": sentiment.get("status") or "unavailable",
        "finbert_events": int(sentiment.get("events") or 0),
        "entry_hour": local.hour,
        "entry_minute": local.minute,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "sample_id": position["id"],
        "market_date": opened.date().isoformat(),
        "feature_cutoff_at": update["seen_at"],
        "opened_at": position["opened_at"],
        "closed_at": position["closed_at"],
        "features": features,
        "labels": {
            "profitable": bool(pnl_pct is not None and pnl_pct > 0),
            "pnl_pct": round(pnl_pct, 6) if pnl_pct is not None else None,
            "mfe_pct": _number(marks[1]),
            "mae_pct": _number(marks[0]),
            "mark_count": int(marks[2]),
            "exit_reason": position["exit_reason"],
            "hold_seconds": round((closed - opened).total_seconds(), 3),
        },
        "provenance": {
            "position_id": position["id"],
            "episode_id": position["episode_id"],
            "feature_source": "last episode update at_or_before opened_at",
            "label_source": "later shadow marks and simulated liquidation bid",
            "actual_fill_claim": False,
            "live_order_authority": False,
        },
    }


def build_dataset(
    database: Path = DEFAULT_DB,
    output: Path = DEFAULT_OUTPUT,
    *,
    minimum_samples: int = 100,
    minimum_market_dates: int = 20,
) -> dict[str, Any]:
    samples: list[dict[str, Any]] = []
    excluded = Counter()
    if database.is_file():
        with sqlite3.connect(database) as db:
            db.row_factory = sqlite3.Row
            positions = db.execute(
                "select * from paper_positions where status = 'CLOSED' and closed_at is not null and exit_price is not null order by opened_at"
            ).fetchall()
            for position in positions:
                sample = _sample(db, position)
                if sample is None:
                    excluded["missing_point_in_time_entry_features"] += 1
                elif sample["labels"]["mark_count"] < 1:
                    excluded["missing_mark_path"] += 1
                else:
                    samples.append(sample)
    dates = sorted({row["market_date"] for row in samples})
    # Date groups, not random rows, prevent the same market session appearing in
    # both training and evaluation.  One date on each side of the split is
    # embargoed when enough history exists.
    train_dates: list[str] = []
    test_dates: list[str] = []
    embargo_dates: list[str] = []
    if len(dates) >= 5:
        split = max(1, int(len(dates) * 0.7))
        embargo_dates = dates[split:split + 1]
        train_dates = dates[:split]
        test_dates = dates[split + 1:]
    by_split = {
        "train": [row for row in samples if row["market_date"] in train_dates],
        "test": [row for row in samples if row["market_date"] in test_dates],
    }
    for name, rows in by_split.items():
        _write(output / f"{name}.jsonl", "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))
    _write(output / "prospective.jsonl", "".join(json.dumps(row, sort_keys=True) + "\n" for row in samples))
    digest = hashlib.sha256("".join(row["sample_id"] for row in samples).encode()).hexdigest()
    ready = len(samples) >= minimum_samples and len(dates) >= minimum_market_dates and bool(test_dates)
    blockers = []
    if len(samples) < minimum_samples:
        blockers.append(f"need_{minimum_samples - len(samples)}_more_closed_replayable_samples")
    if len(dates) < minimum_market_dates:
        blockers.append(f"need_{minimum_market_dates - len(dates)}_more_market_dates")
    if not test_dates:
        blockers.append("chronological_holdout_not_available")
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "database": str(database),
        "dataset_id": f"autopilot_dataset_{digest[:20]}",
        "samples": len(samples),
        "market_dates": len(dates),
        "train_samples": len(by_split["train"]),
        "test_samples": len(by_split["test"]),
        "prospective_samples": len(samples),
        "embargo_dates": embargo_dates,
        "excluded": dict(excluded),
        "training_status": "READY_FOR_OFFLINE_EXPERIMENT" if ready else "INSUFFICIENT_PROSPECTIVE_DATA",
        "blockers": blockers,
        "policies": {
            "point_in_time_features_only": True,
            "chronological_date_split": True,
            "embargo_between_train_and_test": True,
            "ticker_holdout_required_for_promotion": True,
            "finbert_advisory_feature_only": True,
            "fingpt_enabled": False,
            "model_may_authorize_live_orders": False,
        },
    }
    _write(output / "manifest.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    print(json.dumps(build_dataset(args.database, args.output), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
