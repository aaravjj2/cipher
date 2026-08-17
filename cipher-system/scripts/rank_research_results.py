#!/usr/bin/env python3
"""Rank every research report that fits the envelope, and name the ones that do not.

This is the read that makes Phase 1 real: labs whose results were previously incomparable
now sort into one structure, ordered by evidence quality rather than by whose number is
biggest. Discovery and adaptation live in core.research_corpus; this file is only its CLI.

    python3 scripts/rank_research_results.py --summary
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

from core.research_corpus import DEFAULT_ROOT, build_ranking  # noqa: E402

TIER_LABELS = {
    "1": "selectable, cost measured",
    "2": "selectable, cost assumed",
    "3": "inconclusive",
    "4": "rejected",
    "5": "blocked",
}


def print_summary(report: dict) -> None:
    coverage = report["coverage"]
    print(f"commit {report['commit']}")
    print(f"adapted {coverage['adapted']} studies, {coverage['unadapted']} without an adapter")
    for tier in sorted(TIER_LABELS):
        print(f"  tier {tier}  {report['tier_counts'].get(tier, 0):>3}  {TIER_LABELS[tier]}")
    for group in report["groups"]:
        print(f"\n{group['metric']} ({group['unit']}) — not comparable to other groups")
        # Labelled best-case on purpose. A walkforward's verdict is governed by its harshest
        # execution model, so a rejected study can still show a number above 1.0 here; an
        # unlabelled column would read as the governing result.
        print("  tier   best-case  sample   study  [cost basis]")
        for row in group["results"]:
            value = row["best_value"]
            shown = "n/a" if value is None else f"{value:+.3f}"
            print(
                f"  tier {row['evidence_tier']}  {shown:>10}  n={row['observations']:<6} "
                f"{row['study_id']}  [{row['cost_basis']}]"
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT,
                        help="directory to search for report.json files")
    parser.add_argument("--out", type=Path, help="write JSON here instead of stdout")
    parser.add_argument("--summary", action="store_true",
                        help="print a short human-readable summary instead of JSON")
    args = parser.parse_args(argv)

    if not args.root.is_dir():
        parser.error(f"--root is not a directory: {args.root}")

    report = build_ranking(args.root)

    if args.out:
        args.out.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        print(f"wrote {args.out}")
    elif args.summary:
        print_summary(report)
    else:
        print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
