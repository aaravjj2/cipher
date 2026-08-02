from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any

from .bootstrap import ResearchPlatform
from .config import ResearchPlatformConfig

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG = ROOT / "cipher-system" / "config" / "research-platform.json"


def register_ingestion_file(
    path: str | Path,
    *,
    source: str,
    dataset: str,
    ingestion_run_id: str | None = None,
    received_at: datetime | None = None,
    available_at: datetime | None = None,
    event_time_start: datetime | None = None,
    event_time_end: datetime | None = None,
    metadata: dict[str, Any] | None = None,
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    """Register an append-closed raw file without disrupting its collector.

    The hook is active only after the governance registry has been initialized.
    It never performs a cloud write and never raises into market-data collectors.
    """

    try:
        config = ResearchPlatformConfig.load(config_path or DEFAULT_CONFIG, ROOT)
        if not config.registry_path.exists():
            return {
                "status": "skipped",
                "reason": "governance_registry_not_initialized",
                "path": str(path),
            }
        platform = ResearchPlatform(config)
        lake_object = platform.raw_lake.register_existing_immutable_file(
            path,
            source=source,
            dataset=dataset,
            received_at=received_at,
            available_at=available_at,
            event_time_start=event_time_start,
            event_time_end=event_time_end,
            request_metadata={
                **dict(metadata or {}),
                "collector_hook": True,
                "cloud_write_attempted": False,
            },
            ingestion_run_id=ingestion_run_id,
        )
        return {
            "status": "registered",
            "raw_object_id": lake_object.manifest.raw_object_id,
            "manifest_artifact_id": lake_object.artifact.artifact_id if lake_object.artifact else None,
            "path": str(path),
        }
    except Exception as exc:  # collectors must continue even if governance is degraded
        return {
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
            "path": str(path),
        }


def hooks_enabled() -> bool:
    return os.environ.get("CIPHER_GOVERNANCE_HOOKS", "1").lower() not in {"0", "false", "no"}
