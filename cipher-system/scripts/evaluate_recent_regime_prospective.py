#!/usr/bin/env python3
"""Score immutable recent-regime snapshots with future session opens only."""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.research_platform.recent_regime_prospective_evaluation import (  # noqa: E402
    evaluate_prospective_snapshots,
)

REGISTRY = ROOT / "data" / "governance" / "research_registry.sqlite"
PROSPECTIVE_ROOT = ROOT / "data" / "governance" / "recent_regime_prospective"
PREFIX = "alpaca_broad_daily_recent_2024_"
FALLBACK_NAME = "alpaca_broad_daily_2024_2026_ytd_holdout_v1"


def latest_dataset() -> dict[str, Any]:
    with sqlite3.connect(f"file:{REGISTRY.as_posix()}?mode=ro", uri=True, timeout=30) as db:
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
        raise RuntimeError("no canonical recent-regime dataset is registered")
    uri = str(row[3])
    if not uri.startswith("file://"):
        raise RuntimeError("recent-regime dataset is not a local canonical file")
    path = Path(uri.removeprefix("file://"))
    if not path.is_file():
        raise RuntimeError(f"recent-regime dataset path is unavailable: {path}")
    payload = json.loads(row[2]) if row[2] else {}
    quality = payload.get("quality_checks") or {}
    return {
        "dataset_id": str(row[0]),
        "dataset_name": str(row[1]),
        "path": str(path),
        "checksum": str(row[4]),
        "latest_session": quality.get("observed_end"),
    }


def main() -> int:
    dataset = latest_dataset()
    frame = pd.read_parquet(dataset["path"], columns=["timestamp", "ticker", "open"])
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True).dt.tz_convert(None).dt.normalize()
    opens = frame.pivot(index="timestamp", columns="ticker", values="open").sort_index()
    snapshot_paths = sorted((PROSPECTIVE_ROOT / "snapshots").glob("*.json"))
    summary = evaluate_prospective_snapshots(
        opens=opens,
        snapshot_paths=snapshot_paths,
        root=PROSPECTIVE_ROOT,
        dataset=dataset,
        evaluated_at=datetime.now(timezone.utc),
    )
    print(
        json.dumps(
            {
                "status": summary["status"],
                "snapshots": summary["snapshots"],
                "matured_observations": summary["matured_observations"],
                "pending_observations": summary["pending_observations"],
                "leader_selector_one_session": summary["leader_selector_one_session"],
                "leader_gate_one_session": summary["leader_gate_one_session"],
                "output": str(PROSPECTIVE_ROOT / "latest_evaluation_summary.json"),
                "automatic_promotion": False,
                "execution_authority": False,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
