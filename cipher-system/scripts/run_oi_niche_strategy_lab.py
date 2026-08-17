#!/usr/bin/env python3
"""Run the local OI/GEX niche strategy lab (research only)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.oi_niche_strategy_lab import DEFAULT_DB, DEFAULT_OUTPUT, run_lab  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = run_lab(args.db, args.output)
    leader = report.get("leader")
    provisional = report.get("provisional_leader")
    print(json.dumps({
        "status": report["status"],
        "leader_status": report["leader_status"],
        "catalog_size": report["protocol"]["catalog_size"],
        "shortlist_count": report["protocol"]["shortlist_count"],
        "leader": leader["candidate"]["candidate_id"] if leader else None,
        "provisional_status": report["provisional_status"],
        "provisional_leader": provisional["candidate"]["candidate_id"] if provisional else None,
        "report": str((args.output / "latest_oi_niche_strategy_report.json").resolve()),
        "option_pnl": False,
        "execution_authority": False,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
