#!/usr/bin/env python3
"""Download and register a frozen 2026 YTD broad-panel holdout.

The dataset includes 2024-2025 warmup bars so long-lookback indicators can be
formed before the first 2026 session.  The registered evaluation contract makes
only 2026-01-02 through 2026-08-04 scoreable.  Candidate identities are frozen
before the provider request, and the holdout cannot generate adaptive children.

Market-data only: no account, broker, or order endpoints are imported.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
OUTPUT_ROOT = ROOT / "data" / "historical_equities" / "broad_2026_ytd_holdout_v1"
DATASET_NAME = "alpaca_broad_daily_2024_2026_ytd_holdout_v1"
DOWNLOAD_START = "2024-01-01"
DOWNLOAD_END = "2026-08-04"
EVALUATION_START = "2026-01-02"
EVALUATION_END = "2026-08-04"
RESEARCH_ROLE = "locked_short_temporal_holdout_fixed_pre_download_candidates_only"


def freeze_candidate_identities(registry: ResearchRegistry) -> dict[str, Any]:
    with registry.connect() as db:
        rows = db.execute(
            "select strategy_id,payload_json from strategies where name like 'autonomous_price_only_%' order by strategy_id"
        ).fetchall()
    candidates: dict[str, dict[str, Any]] = {}
    for _strategy_id, payload_json in rows:
        payload = json.loads(payload_json)
        signal = payload.get("signal_rule") or {}
        candidate_id = str(signal.get("candidate_id") or "")
        if not candidate_id:
            continue
        candidate = {
            "candidate_id": candidate_id,
            "family": signal.get("family"),
            "parameters": signal.get("parameters"),
            "parent_candidate_id": signal.get("parent_candidate_id"),
        }
        existing = candidates.get(candidate_id)
        if existing is not None and existing != candidate:
            raise RuntimeError(f"candidate identity collision for {candidate_id}")
        candidates[candidate_id] = candidate
    ordered = [candidates[key] for key in sorted(candidates)]
    return {
        "candidate_count": len(ordered),
        "strategy_spec_count": len(rows),
        "candidate_ids": [item["candidate_id"] for item in ordered],
        "hash": stable_id("candidate_id_set_pre_2026_ytd_download", ordered, length=64),
    }


def register_dataset(
    registry: ResearchRegistry,
    frame: pd.DataFrame,
    raw_pages: list[dict[str, Any]],
    freeze: dict[str, Any],
    created_at: datetime,
) -> dict[str, Any]:
    quality = validate_slice(frame, DOWNLOAD_START, DOWNLOAD_END)
    if not quality["passed"]:
        raise RuntimeError(f"2026 YTD holdout quality failed: {quality['failures']}")
    scoreable = frame[
        (frame["timestamp"] >= pd.Timestamp(EVALUATION_START, tz="UTC"))
        & (frame["timestamp"] <= pd.Timestamp(EVALUATION_END + "T23:59:59", tz="UTC"))
    ]
    scoreable_sessions = int(scoreable["timestamp"].dt.date.nunique())
    if scoreable_sessions < 100:
        raise RuntimeError(f"2026 YTD holdout has only {scoreable_sessions} scoreable sessions")

    normalized = OUTPUT_ROOT / "normalized"
    normalized.mkdir(parents=True, exist_ok=True)
    parquet_path = normalized / f"{DATASET_NAME}.parquet"
    frame.to_parquet(parquet_path, index=False)
    checksum = sha256_file(parquet_path)
    raw = RawObjectManifest(
        source="Alpaca SIP adjusted daily bars",
        dataset=DATASET_NAME,
        uri=parquet_path.resolve().as_uri(),
        checksum=checksum,
        checksum_method="sha256",
        size_bytes=parquet_path.stat().st_size,
        received_at=created_at,
        available_at=created_at,
        ingestion_run_id=stable_id("broad_2026_ytd_run", {"created_at": created_at.isoformat(), "freeze": freeze["hash"]}),
        content_type="application/x-parquet",
        event_time_start=pd.Timestamp(DOWNLOAD_START, tz="UTC").to_pydatetime(),
        event_time_end=pd.Timestamp(DOWNLOAD_END + "T23:59:59", tz="UTC").to_pydatetime(),
        request_metadata={
            "provider_raw_pages": raw_pages,
            "normalizer_sha256": sha256_file(Path(__file__)),
            "symbols_requested": list(SYMBOLS),
            "adjustment": "all",
            "feed": "sip",
            "warmup_start": DOWNLOAD_START,
            "evaluation_start": EVALUATION_START,
            "evaluation_end": EVALUATION_END,
            "candidate_identity_freeze": freeze,
            "research_role": RESEARCH_ROLE,
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

    checks = {
        **quality,
        "passed": True,
        "research_role": RESEARCH_ROLE,
        "warmup_start": DOWNLOAD_START,
        "evaluation_start": EVALUATION_START,
        "evaluation_end": EVALUATION_END,
        "scoreable_rows": int(len(scoreable)),
        "scoreable_sessions": scoreable_sessions,
        "candidate_identity_freeze_hash": freeze["hash"],
        "candidate_identity_count": freeze["candidate_count"],
        "adaptive_search_allowed": False,
        "final_holdout_claim": False,
        "automatic_promotion": False,
        "execution_authority": False,
    }
    dataset = DatasetManifest(
        name=DATASET_NAME,
        created_at=created_at,
        availability_cutoff=created_at,
        sources=("Alpaca SIP adjusted daily bars",),
        raw_object_ids=(raw_object_id,),
        symbol_universe_id="broad_cross_asset_equity_etf_panel_v1",
        corporate_action_version="alpaca_adjustment_all_2026_08_04",
        normalizer_version=f"download_register_2026_ytd_holdout.py:sha256:{sha256_file(Path(__file__))}",
        schema_name="daily_adjusted_ohlcv_with_evaluation_window_v1",
        row_counts={
            "rows": len(frame),
            "sessions": frame["timestamp"].dt.date.nunique(),
            "symbols": frame["ticker"].nunique(),
            "scoreable_rows": len(scoreable),
            "scoreable_sessions": scoreable_sessions,
            "raw_pages": len(raw_pages),
        },
        quality_checks=checks,
        frozen=True,
    )
    registry.register_dataset(dataset)
    return {
        "dataset_id": dataset.dataset_id,
        "dataset_name": dataset.name,
        "path": str(parquet_path),
        "sha256": checksum,
        "rows": int(len(frame)),
        "sessions": int(frame["timestamp"].dt.date.nunique()),
        "symbols": int(frame["ticker"].nunique()),
        "scoreable_rows": int(len(scoreable)),
        "scoreable_sessions": scoreable_sessions,
        "evaluation_start": EVALUATION_START,
        "evaluation_end": EVALUATION_END,
        "research_role": RESEARCH_ROLE,
    }


def main() -> int:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    registry = ResearchRegistry(REGISTRY_PATH)
    freeze = freeze_candidate_identities(registry)
    created_at = datetime.now(UTC)
    frame, raw_pages = download(OUTPUT_ROOT, DOWNLOAD_START, DOWNLOAD_END)
    dataset = register_dataset(registry, frame, raw_pages, freeze, created_at)
    payload = {
        "schema_version": 1,
        "created_at": created_at.isoformat(),
        "status": "completed",
        "download": {
            "start": DOWNLOAD_START,
            "end": DOWNLOAD_END,
            "symbols_requested": list(SYMBOLS),
            "rows": int(len(frame)),
            "pages": len(raw_pages),
            "raw_pages": raw_pages,
        },
        "candidate_identity_freeze": freeze,
        "dataset": dataset,
        "adaptive_feedback_allowed": False,
        "automatic_promotion": False,
        "paper_or_live_execution": False,
        "execution_authority": False,
    }
    report = OUTPUT_ROOT / "holdout_registration.json"
    report.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
