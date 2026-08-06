#!/usr/bin/env python3
"""Correct the broad validation dataset label after provider coverage inspection."""
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
from core.research_platform.models import AuditEvent, DataDisposition, DatasetManifest, RawObjectManifest  # noqa: E402
from core.research_platform.registry import ResearchRegistry  # noqa: E402

REGISTRY_PATH = ROOT / "data" / "governance" / "research_registry.sqlite"
DATA_ROOT = ROOT / "data" / "historical_equities" / "broad_research_panel_v1"
SOURCE_PATH = DATA_ROOT / "normalized" / "alpaca_broad_daily_2010_2019_locked_validation_v1.parquet"
OLD_NAME = "alpaca_broad_daily_2010_2019_locked_validation_v1"
NEW_NAME = "alpaca_broad_daily_2016_2019_locked_validation_v1"


def main() -> int:
    frame = pd.read_parquet(SOURCE_PATH)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    observed_start = frame["timestamp"].min()
    observed_end = frame["timestamp"].max()
    if observed_start.date().isoformat() != "2016-01-04" or observed_end.date().isoformat() != "2019-12-31":
        raise RuntimeError(f"unexpected corrected coverage: {observed_start} through {observed_end}")
    if frame.duplicated(["timestamp", "ticker"]).any():
        raise RuntimeError("duplicate timestamp/ticker rows in corrected validation panel")
    now = datetime.now(timezone.utc)
    registry = ResearchRegistry(REGISTRY_PATH)
    report = json.loads((DATA_ROOT / "broad_panel_registration.json").read_text(encoding="utf-8"))
    candidate_snapshot = report["candidate_set_frozen_before_download"]
    raw = RawObjectManifest(
        source="Alpaca SIP adjusted daily bars",
        dataset=NEW_NAME,
        uri=SOURCE_PATH.resolve().as_uri(),
        checksum=sha256_file(SOURCE_PATH),
        checksum_method="sha256",
        size_bytes=SOURCE_PATH.stat().st_size,
        received_at=now,
        available_at=now,
        ingestion_run_id=stable_id("broad_panel_correction", {"time": now.isoformat(), "name": NEW_NAME}),
        content_type="application/x-parquet",
        event_time_start=observed_start.to_pydatetime(),
        event_time_end=observed_end.to_pydatetime(),
        request_metadata={
            "correction_of": OLD_NAME,
            "reason": "Alpaca accessible adjusted SIP history began 2016-01-04 despite a 2010 request",
            "candidate_set_hash_before_download": candidate_snapshot["hash"],
            "candidate_count_before_download": candidate_snapshot["strategy_count"],
            "research_role": "locked_temporal_validation_fixed_pre_download_candidates_only",
        },
        disposition=DataDisposition.FROZEN_SNAPSHOT,
    )
    with registry.connect() as db:
        existing_raw = db.execute(
            "select raw_object_id from raw_objects where checksum=? and uri=?",
            (raw.checksum, raw.uri),
        ).fetchone()
    if existing_raw:
        raw_object_id = str(existing_raw[0])
    else:
        registry.register_raw_object(raw)
        raw_object_id = raw.raw_object_id
    dataset = DatasetManifest(
        name=NEW_NAME,
        created_at=now,
        availability_cutoff=now,
        sources=("Alpaca SIP adjusted daily bars",),
        raw_object_ids=(raw_object_id,),
        symbol_universe_id="broad_cross_asset_equity_etf_panel_v1",
        corporate_action_version="alpaca_adjustment_all_2026_08_04",
        normalizer_version=f"register_broad_panel_correction.py:sha256:{sha256_file(Path(__file__))}",
        schema_name="daily_adjusted_ohlcv_v1",
        row_counts={
            "rows": len(frame),
            "sessions": frame["timestamp"].dt.date.nunique(),
            "symbols": frame["ticker"].nunique(),
        },
        quality_checks={
            "passed": True,
            "observed_start": observed_start.date().isoformat(),
            "observed_end": observed_end.date().isoformat(),
            "requested_start": "2010-01-01",
            "provider_coverage_truncated": True,
            "correction_of": OLD_NAME,
            "do_not_use_superseded_dataset_name": OLD_NAME,
            "research_role": "locked_temporal_validation_fixed_pre_download_candidates_only",
            "candidate_set_hash_before_download": candidate_snapshot["hash"],
            "adaptive_search_allowed": False,
            "automatic_promotion": False,
            "execution_authority": False,
        },
        frozen=True,
    )
    registry.register_dataset(dataset)
    with registry.connect() as db:
        old_rows = db.execute("select dataset_id from datasets where name=? order by created_at", (OLD_NAME,)).fetchall()
    for row in old_rows:
        registry.audit(
            AuditEvent(
                event_type="DATASET_LABEL_CORRECTION",
                entity_type="dataset",
                entity_id=str(row[0]),
                occurred_at=now,
                actor="broad_panel_correction",
                payload={
                    "superseded_for_future_experiments": True,
                    "corrected_dataset_id": dataset.dataset_id,
                    "corrected_name": NEW_NAME,
                    "reason": "observed coverage starts 2016-01-04, not 2010-01-01",
                },
            )
        )
    payload = {
        "schema_version": 1,
        "created_at": now.isoformat(),
        "status": "corrected",
        "old_name": OLD_NAME,
        "corrected_dataset_id": dataset.dataset_id,
        "corrected_name": NEW_NAME,
        "observed_start": observed_start.date().isoformat(),
        "observed_end": observed_end.date().isoformat(),
        "rows": len(frame),
        "sessions": int(frame["timestamp"].dt.date.nunique()),
        "symbols": int(frame["ticker"].nunique()),
        "candidate_set_frozen_before_download": candidate_snapshot,
        "adaptive_search_allowed": False,
        "execution_authority": False,
    }
    path = DATA_ROOT / "broad_panel_registration_correction.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
