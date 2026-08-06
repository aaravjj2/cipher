#!/usr/bin/env python3
"""Download and register the missing 2023 broad adjusted daily panel.

This closes the calendar gap between the 2020-2022 development panel and the
2024-2026 warmup/holdout panel.  It is exploratory cross-universe data, not an
untouched holdout, and cannot authorize promotion or trading.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.research_platform.hashing import sha256_file, stable_id  # noqa: E402
from core.research_platform.models import DataDisposition, DatasetManifest, RawObjectManifest  # noqa: E402
from core.research_platform.registry import ResearchRegistry  # noqa: E402
from download_register_broad_equity_panel import SYMBOLS, download, validate_slice  # noqa: E402

UTC = timezone.utc
REGISTRY_PATH = ROOT / "data" / "governance" / "research_registry.sqlite"
OUTPUT_ROOT = ROOT / "data" / "historical_equities" / "broad_2023_panel_v1"
DATASET_NAME = "alpaca_broad_daily_2023_cross_universe_development_v1"
START = "2023-01-01"
END = "2023-12-31"
ROLE = "cross_universe_2023_development_only_not_independent_holdout"


def main() -> int:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    created_at = datetime.now(UTC)
    frame, raw_pages = download(OUTPUT_ROOT, START, END)
    quality = validate_slice(frame, START, END)
    if not quality["passed"]:
        raise RuntimeError(f"2023 broad panel quality failed: {quality['failures']}")
    normalized = OUTPUT_ROOT / "normalized"
    normalized.mkdir(parents=True, exist_ok=True)
    parquet_path = normalized / f"{DATASET_NAME}.parquet"
    frame.to_parquet(parquet_path, index=False)
    checksum = sha256_file(parquet_path)
    registry = ResearchRegistry(REGISTRY_PATH)
    raw = RawObjectManifest(
        source="Alpaca SIP adjusted daily bars",
        dataset=DATASET_NAME,
        uri=parquet_path.resolve().as_uri(),
        checksum=checksum,
        checksum_method="sha256",
        size_bytes=parquet_path.stat().st_size,
        received_at=created_at,
        available_at=created_at,
        ingestion_run_id=stable_id("broad_2023_run", {"created_at": created_at.isoformat()}),
        content_type="application/x-parquet",
        event_time_start=pd.Timestamp(START, tz="UTC").to_pydatetime(),
        event_time_end=pd.Timestamp(END + "T23:59:59", tz="UTC").to_pydatetime(),
        request_metadata={
            "provider_raw_pages": raw_pages,
            "symbols_requested": list(SYMBOLS),
            "normalizer_sha256": sha256_file(Path(__file__)),
            "adjustment": "all",
            "feed": "sip",
            "research_role": ROLE,
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
        name=DATASET_NAME,
        created_at=created_at,
        availability_cutoff=created_at,
        sources=("Alpaca SIP adjusted daily bars",),
        raw_object_ids=(raw_object_id,),
        symbol_universe_id="broad_cross_asset_equity_etf_panel_v1",
        corporate_action_version="alpaca_adjustment_all_2026_08_04",
        normalizer_version=f"download_register_2023_broad_panel.py:sha256:{sha256_file(Path(__file__))}",
        schema_name="daily_adjusted_ohlcv_v1",
        row_counts={
            "rows": len(frame),
            "sessions": frame["timestamp"].dt.date.nunique(),
            "symbols": frame["ticker"].nunique(),
            "raw_pages": len(raw_pages),
        },
        quality_checks={
            **quality,
            "research_role": ROLE,
            "adaptive_search_allowed": True,
            "independent_holdout": False,
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
        "rows": int(len(frame)),
        "sessions": int(frame["timestamp"].dt.date.nunique()),
        "symbols": int(frame["ticker"].nunique()),
        "raw_pages": len(raw_pages),
        "research_role": ROLE,
        "automatic_promotion": False,
        "paper_or_live_execution": False,
        "execution_authority": False,
    }
    report = OUTPUT_ROOT / "registration.json"
    report.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
