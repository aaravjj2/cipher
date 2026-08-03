#!/usr/bin/env python3
"""Run only allowlisted operational repairs against existing governed artifacts."""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from core.research_platform.repair_actions import RepairExecutor
from core.research_platform.repair_boundary import RepairRequest


def event_summary(payload: bytes) -> bytes:
    source = json.loads(payload.decode("utf-8"))
    events = source.get("processed_events", [])
    rows = [item.get("record", {}) for item in events]
    summary = {
        "schema_version": 1,
        "source_artifact_created_at": source.get("created_at"),
        "event_count": len(rows),
        "high_magnitude_count": sum(bool(row.get("high_magnitude")) for row in rows),
        "sources": sorted({str(row.get("source")) for row in rows if row.get("source")}),
        "symbols": sorted({symbol for row in rows for symbol in row.get("symbols", [])}),
        "directional_signal_allowed": False,
        "live_execution": False,
    }
    return (json.dumps(summary, indent=2, sort_keys=True) + "\n").encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event-report", type=Path)
    args = parser.parse_args()
    candidates = sorted((ROOT / "data" / "events").glob("public_event_ingestion_*.json"))
    source = args.event_report or (candidates[-1] if candidates else None)
    output_dir = ROOT / "data" / "governance"
    output_dir.mkdir(parents=True, exist_ok=True)
    if source is None:
        payload = {
            "schema_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "skipped_data_insufficient",
            "reason": "no governed public-event artifact exists",
            "repairs_attempted": 0,
            "gate_relaxed": False,
            "execution_authority": False,
        }
        path = output_dir / f"bounded_repair_run_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(path)
        return 0

    executor = RepairExecutor(ROOT / "data" / "repair_incidents")
    checksum = executor.recompute_checksum(
        RepairRequest(
            action="recompute_checksum",
            target=str(source),
            changes={"checksum_algorithm": "sha256", "content_modified": False},
        ),
        target=source,
    )
    cache = executor.rebuild_derived_cache(
        RepairRequest(
            action="rebuild_derived_cache",
            target="public_event_summary_cache",
            changes={"derived_cache_only": True, "source_content_immutable": True},
        ),
        source=source,
        destination=ROOT / "data" / "cache" / "public_event_summary.json",
        transform=event_summary,
    )
    payload = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "complete",
        "source": str(source),
        "incidents": [checksum, cache],
        "repairs_attempted": 2,
        "repairs_completed": sum(item["status"] in {"verified", "repaired"} for item in (checksum, cache)),
        "protected_research_fields_changed": False,
        "gate_relaxed": False,
        "promotion_changed": False,
        "execution_authority": False,
    }
    path = output_dir / f"bounded_repair_run_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output_dir / "latest_bounded_repair_run.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({"path": str(path), "status": payload["status"], "repairs_completed": payload["repairs_completed"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
