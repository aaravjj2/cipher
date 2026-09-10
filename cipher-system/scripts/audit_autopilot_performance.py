#!/usr/bin/env python3
"""Report grounded performance diagnostics from a Cipher local-paper ledger."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.paper_executor.cohort_evaluation import evaluate_cohort, replay_ledger


def audit(path: Path, baseline_path: Path | None = None) -> dict[str, Any]:
    return evaluate_cohort(path, baseline_path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--baseline-db", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--replay", action="store_true", help="Reproduce ledger decisions from their recorded option quotes")
    args = parser.parse_args()
    report = audit(args.db, args.baseline_db)
    if args.replay:
        report["replay"] = replay_ledger(args.db)
    print(json.dumps(report, indent=2 if args.json else None, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
