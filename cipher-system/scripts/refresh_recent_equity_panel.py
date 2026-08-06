#!/usr/bin/env python3
"""Refresh Cipher's rolling 2024-present broad daily panel after market close.

Each advanced snapshot is immutable and registered separately. The rolling
panel is exploratory recent-regime data and cannot be relabelled as an
independent holdout or authorize execution.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.research_platform.hashing import sha256_file, stable_id  # noqa: E402
from core.research_platform.models import DataDisposition, DatasetManifest, RawObjectManifest  # noqa: E402
from core.research_platform.registry import ResearchRegistry  # noqa: E402
from download_register_broad_equity_panel import SYMBOLS, download, validate_slice  # noqa: E402

UTC = timezone.utc
NY = ZoneInfo("America/New_York")
REGISTRY_PATH = ROOT / "data" / "governance" / "research_registry.sqlite"
OUTPUT_BASE = ROOT / "data" / "historical_equities" / "recent_regime_snapshots"
START = "2024-01-01"
PREFIX = "alpaca_broad_daily_recent_2024_"
FALLBACK_NAME = "alpaca_broad_daily_2024_2026_ytd_holdout_v1"
ROLE = "recent_2025_2026_rolling_development_only_not_independent_holdout"


def latest_registered_panel() -> dict[str, Any] | None:
    if not REGISTRY_PATH.is_file():
        return None
    with sqlite3.connect(f"file:{REGISTRY_PATH.as_posix()}?mode=ro", uri=True, timeout=30) as db:
        row = db.execute(
            """
            select d.dataset_id, d.name, d.payload_json, r.uri, r.checksum
            from datasets d
            join dataset_raw_objects l on l.dataset_id=d.dataset_id
            join raw_objects r on r.raw_object_id=l.raw_object_id
            where d.frozen=1 and d.quality_passed=1
              and (d.name like ? or d.name = ?)
            order by case when d.name like ? then 0 else 1 end, d.created_at desc
            limit 1
            """,
            (f"{PREFIX}%", FALLBACK_NAME, f"{PREFIX}%"),
        ).fetchone()
    if not row:
        return None
    payload = json.loads(row[2]) if row[2] else {}
    uri = str(row[3])
    path = Path(uri.removeprefix("file://")) if uri.startswith("file://") else None
    observed_end = ((payload.get("quality_checks") or {}).get("observed_end"))
    if not observed_end and path and path.is_file():
        frame = pd.read_parquet(path, columns=["timestamp"])
        observed_end = str(pd.to_datetime(frame["timestamp"], utc=True).max().date())
    return {
        "dataset_id": str(row[0]),
        "name": str(row[1]),
        "path": str(path) if path else None,
        "checksum": str(row[4]),
        "observed_end": observed_end,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--end-date", default=datetime.now(NY).date().isoformat())
    args = parser.parse_args()
    requested_end = pd.Timestamp(args.end_date).date().isoformat()
    snapshot_root = OUTPUT_BASE / requested_end.replace("-", "")
    frame, raw_pages = download(snapshot_root, START, requested_end, symbols=SYMBOLS)
    quality = validate_slice(frame, START, requested_end)
    observed_end = str(quality.get("observed_end"))
    if not quality["passed"]:
        raise RuntimeError(f"recent rolling panel quality failed: {quality['failures']}")

    previous = latest_registered_panel()
    if previous and previous.get("observed_end") and observed_end <= str(previous["observed_end"]):
        payload = {
            "schema_version": 1,
            "created_at": datetime.now(UTC).isoformat(),
            "status": "not_advanced",
            "requested_end": requested_end,
            "observed_end": observed_end,
            "previous_dataset_id": previous["dataset_id"],
            "previous_observed_end": previous["observed_end"],
            "raw_pages": raw_pages,
            "automatic_promotion": False,
            "paper_or_live_execution": False,
            "execution_authority": False,
        }
        report = snapshot_root / "refresh_status.json"
        report.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    dataset_name = f"{PREFIX}{observed_end.replace('-', '')}_v1"
    normalized = snapshot_root / "normalized"
    normalized.mkdir(parents=True, exist_ok=True)
    parquet_path = normalized / f"{dataset_name}.parquet"
    frame.to_parquet(parquet_path, index=False)
    created_at = datetime.now(UTC)
    checksum = sha256_file(parquet_path)
    registry = ResearchRegistry(REGISTRY_PATH)
    raw = RawObjectManifest(
        source="Alpaca SIP adjusted daily bars",
        dataset=dataset_name,
        uri=parquet_path.resolve().as_uri(),
        checksum=checksum,
        checksum_method="sha256",
        size_bytes=parquet_path.stat().st_size,
        received_at=created_at,
        available_at=created_at,
        ingestion_run_id=stable_id("recent_regime_panel_run", {"created_at": created_at.isoformat(), "observed_end": observed_end}),
        content_type="application/x-parquet",
        event_time_start=pd.Timestamp(START, tz="UTC").to_pydatetime(),
        event_time_end=pd.Timestamp(observed_end + "T23:59:59", tz="UTC").to_pydatetime(),
        request_metadata={
            "provider_raw_pages": raw_pages,
            "symbols_requested": list(SYMBOLS),
            "adjustment": "all",
            "feed": "sip",
            "research_role": ROLE,
            "outcome_informed_recent_research": True,
            "automatic_promotion": False,
            "execution_authority": False,
        },
        disposition=DataDisposition.FROZEN_SNAPSHOT,
    )
    with registry.connect() as db:
        existing = db.execute(
            "select raw_object_id from raw_objects where checksum=? and uri=?",
            (raw.checksum, raw.uri),
        ).fetchone()
    if existing:
        raw_object_id = str(existing[0])
    else:
        registry.register_raw_object(raw)
        raw_object_id = raw.raw_object_id

    dataset = DatasetManifest(
        name=dataset_name,
        created_at=created_at,
        availability_cutoff=created_at,
        sources=("Alpaca SIP adjusted daily bars",),
        raw_object_ids=(raw_object_id,),
        symbol_universe_id="broad_cross_asset_equity_etf_panel_v1",
        corporate_action_version=f"alpaca_adjustment_all_{observed_end}",
        normalizer_version=f"refresh_recent_equity_panel.py:sha256:{sha256_file(Path(__file__))}",
        schema_name="daily_adjusted_ohlcv_v1",
        row_counts={
            "rows": len(frame),
            "sessions": int(frame["timestamp"].dt.date.nunique()),
            "symbols": int(frame["ticker"].nunique()),
            "raw_pages": len(raw_pages),
        },
        quality_checks={
            **quality,
            "research_role": ROLE,
            "outcome_informed_recent_research": True,
            "independent_holdout": False,
            "adaptive_search_allowed": True,
            "automatic_promotion": False,
            "execution_authority": False,
        },
        frozen=True,
    )
    registry.register_dataset(dataset)
    payload = {
        "schema_version": 1,
        "created_at": created_at.isoformat(),
        "status": "completed",
        "dataset_id": dataset.dataset_id,
        "dataset_name": dataset.name,
        "path": str(parquet_path),
        "sha256": checksum,
        "requested_end": requested_end,
        "observed_end": observed_end,
        "rows": int(len(frame)),
        "sessions": int(frame["timestamp"].dt.date.nunique()),
        "symbols": int(frame["ticker"].nunique()),
        "raw_pages": len(raw_pages),
        "research_role": ROLE,
        "automatic_promotion": False,
        "paper_or_live_execution": False,
        "execution_authority": False,
    }
    report = snapshot_root / "refresh_status.json"
    report.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
