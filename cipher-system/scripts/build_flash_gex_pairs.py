#!/usr/bin/env python3
"""Build the strict historical Flash/GEX paired-label corpus."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))

import flash_gex_pairs  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser-dir", type=Path, default=flash_gex_pairs.DEFAULT_BROWSER_DIR)
    parser.add_argument("--gex-db", type=Path, default=flash_gex_pairs.DEFAULT_GEX_DB)
    parser.add_argument("--output", type=Path, default=flash_gex_pairs.DEFAULT_OUT)
    parser.add_argument("--max-age-minutes", type=float, default=20.0)
    parser.add_argument("--max-spot-drift-pct", type=float, default=0.5)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    records, report = flash_gex_pairs.build_pairs(
        browser_dir=args.browser_dir,
        db_path=args.gex_db,
        max_age_minutes=args.max_age_minutes,
        max_spot_drift_pct=args.max_spot_drift_pct,
    )
    if not args.dry_run:
        report["output"] = str(flash_gex_pairs.write_pairs(records, args.output))
    else:
        report["output"] = None
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if records else 2


if __name__ == "__main__":
    raise SystemExit(main())
