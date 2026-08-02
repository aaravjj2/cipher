#!/usr/bin/env python3
"""Archive closed live option-chain JSONL files to verified GCS cold storage."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "core"
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

from live_chain_archive import (  # noqa: E402
    ArchiveLedger,
    GCSArchiveStore,
    archive_cold_files,
    select_archive_candidates,
)
from research_platform.bootstrap import ResearchPlatform  # noqa: E402
from research_platform.config import ResearchPlatformConfig  # noqa: E402
from research_platform.models import AuditEvent, utc_now  # noqa: E402

DEFAULT_CONFIG = ROOT / "config" / "research-platform.json"
DEFAULT_SOURCE = ROOT / "data" / "live_option_chains"
DEFAULT_LEDGER = ROOT / "data" / "live_option_chains_archive.sqlite"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compress, checksum, upload, verify, and prune closed live-chain JSONL files."
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--bucket")
    parser.add_argument("--prefix", default="cold/live-option-chains")
    parser.add_argument("--keep-dates", type=int, default=2)
    parser.add_argument("--max-files", type=int)
    parser.add_argument("--compression-level", type=int, default=3)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = ResearchPlatformConfig.load(args.config, ROOT.parent)
    bucket = args.bucket or config.gcs_bucket
    if not bucket:
        raise SystemExit("No GCS bucket is configured")

    candidates = select_archive_candidates(
        args.source,
        keep_dates=max(0, args.keep_dates),
        max_files=args.max_files,
    )
    if args.dry_run:
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "source": str(args.source),
                    "bucket": bucket,
                    "keep_dates": args.keep_dates,
                    "selected": [
                        {
                            "path": str(candidate.path),
                            "trading_day": candidate.trading_day.isoformat(),
                            "ticker": candidate.ticker,
                            "size_bytes": candidate.path.stat().st_size,
                        }
                        for candidate in candidates
                    ],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    result = archive_cold_files(
        args.source,
        store=GCSArchiveStore(bucket),
        ledger_path=args.ledger,
        keep_dates=max(0, args.keep_dates),
        max_files=args.max_files,
        object_prefix=args.prefix,
        compression_level=args.compression_level,
    )

    try:
        platform = ResearchPlatform(config)
        artifact = platform.artifacts.put_json(
            result,
            metadata={
                "kind": "live_option_chain_archive_run",
                "bucket": bucket,
                "prefix": args.prefix,
            },
        )
        platform.registry.register_artifact(artifact.to_dict())
        platform.registry.audit(
            AuditEvent(
                event_type="LIVE_OPTION_CHAINS_ARCHIVED",
                entity_type="dataset",
                entity_id="live_option_chains",
                occurred_at=utc_now(),
                payload={
                    "artifact_id": artifact.artifact_id,
                    "archived": result["archived"],
                    "errors": result["errors"],
                    "local_bytes_freed": result["local_bytes_freed"],
                    "compressed_bytes_uploaded": result["compressed_bytes_uploaded"],
                    "bucket": bucket,
                    "prefix": args.prefix,
                },
                actor="live_chain_archive",
            )
        )
        result["governance_artifact_id"] = artifact.artifact_id
    except Exception as exc:
        result["governance_warning"] = f"{type(exc).__name__}: {exc}"

    print(json.dumps(result, indent=2, sort_keys=True))
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
