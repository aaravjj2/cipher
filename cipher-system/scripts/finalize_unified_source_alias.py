#!/usr/bin/env python3
"""Replace the legacy Cipher source tree with a compatibility alias.

The original source directory is moved into the pre-unification backup, then the
legacy path becomes a symlink to the canonical Git checkout. Existing systemd
units may continue using the legacy path while executing canonical source.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path


CANONICAL_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LEGACY_ROOT = Path("/home/aarav/Aarav/cipher/cipher-system")
DEFAULT_RUNTIME_ROOT = Path("/home/aarav/Aarav/cipher/runtime")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--legacy-root", type=Path, default=DEFAULT_LEGACY_ROOT)
    parser.add_argument("--canonical-root", type=Path, default=CANONICAL_ROOT)
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME_ROOT)
    parser.add_argument("--backup-stamp", default="20260803T231700Z")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    legacy = args.legacy_root.resolve() if args.legacy_root.is_symlink() else args.legacy_root.absolute()
    canonical = args.canonical_root.resolve()
    backup = args.runtime_root.resolve() / "backups" / f"pre_unification_{args.backup_stamp}" / "legacy_source_original"
    plan = {
        "legacy_path": str(args.legacy_root.absolute()),
        "canonical_target": str(canonical),
        "legacy_source_backup": str(backup),
        "execution_authority": False,
    }
    if not args.execute:
        print(json.dumps({"status": "planned", **plan}, indent=2, sort_keys=True))
        return 0

    legacy_path = args.legacy_root.absolute()
    if legacy_path.is_symlink():
        if legacy_path.resolve() == canonical:
            print(json.dumps({"status": "already_finalized", **plan}, indent=2, sort_keys=True))
            return 0
        raise RuntimeError(f"legacy path is already an unexpected symlink: {legacy_path}")
    if not legacy_path.is_dir():
        raise FileNotFoundError(legacy_path)
    if backup.exists():
        raise FileExistsError(backup)
    backup.parent.mkdir(parents=True, exist_ok=True)
    os.replace(legacy_path, backup)
    legacy_path.symlink_to(canonical, target_is_directory=True)
    report = {
        "status": "completed",
        **plan,
        "legacy_path_resolves_to": str(legacy_path.resolve()),
        "backup_exists": backup.is_dir(),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    report_path = args.runtime_root.resolve() / "governance" / "source_alias_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = report_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, report_path)
    report["report_path"] = str(report_path)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
