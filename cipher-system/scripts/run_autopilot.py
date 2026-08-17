#!/usr/bin/env python3
"""Run one pass of Cipher's evidence autopilot.

Observes every adaptable research report, ranks it by evidence quality, asks what is worth
running next, and reports what changed since the last pass. Its highest output is a proposal
a human reads: it runs no engine, promotes no finding, and places no order.

    python3 scripts/run_autopilot.py --summary
    python3 scripts/run_autopilot.py --dry-run --summary   # do not update the state file

Exit codes are meaningful for a timer: 0 quiet or informational, 3 when something changed and
a human should look. A timer that always exits 0 gives an operator nothing to filter on.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

from core.autopilot import DEFAULT_STATE_PATH, headline, run_once  # noqa: E402
from core.research_corpus import DEFAULT_ROOT  # noqa: E402

CHANGES_FOUND = 3


#: Above this many changes of one kind, print a count instead of a line each.
BULK_CHANGE_THRESHOLD = 5


def print_summary(report: dict) -> None:
    print(headline(report))
    print(f"  commit {report['commit']}  live_order_authority={report['live_order_authority']}")
    coverage = report["coverage"]
    print(f"  {coverage['adapted']} studies adapted, {coverage['unadapted']} without an adapter")
    tiers = report["tier_counts"]
    print("  tiers: " + "  ".join(f"{t}:{tiers.get(t, 0)}" for t in sorted(tiers)))

    health = report.get("capture_health") or {}
    if health.get("available"):
        # Printed every pass, not only when it changes: measured-spread coverage is the asset
        # the research programme rests on, and its size is the headline fact about progress.
        print(
            f"  measured spreads: {health.get('distinct_days')} capture days, "
            f"{health.get('gap_count')} imperfect — {health.get('verdict')}"
        )
    elif health:
        print(f"  measured spreads: {health.get('verdict')}")

    if report["changes"]:
        print("\nCHANGED SINCE LAST PASS")
        # A bulk change is one event, not fifty-five. Printing a line per affected study
        # buries the substantive changes above it and trains the reader to skip the section,
        # which is the exact failure this loop exists to avoid. The per-study detail is still
        # in the JSON artifact for anything that wants to diff two passes.
        counts: dict[str, int] = {}
        for change in report["changes"]:
            counts[change["kind"]] = counts.get(change["kind"], 0) + 1
        collapsed: set[str] = set()
        for change in report["changes"]:
            kind = change["kind"]
            total = counts[kind]
            if total < BULK_CHANGE_THRESHOLD:
                study = change.get("study_id", "")
                print(f"  [{kind}] {study} — {change['detail']}")
                continue
            if kind in collapsed:
                continue
            collapsed.add(kind)
            example = change.get("study_id", "")
            print(f"  [{kind}] x{total} studies — {change['detail']}")
            print(f"      e.g. {example}; the rest are in the JSON artifact")

    if report["recommended_actions"]:
        print("\nWORTH RUNNING NEXT")
        for index, action in enumerate(report["recommended_actions"], start=1):
            print(f"  {index}. {action['action']}  (unblocks {action['unblocks_studies']})")
            print(f"     latency: {action['latency']}")
            if action["limitation"]:
                print(f"     limit: {action['limitation']}")
    else:
        print(f"\nNothing worth running: {report['nothing_to_run_because']}")

    if report["unclassified_blockers"]:
        print("\nUNCLASSIFIED BLOCKERS — the agent has no rule for these")
        for row in report["unclassified_blockers"]:
            print(f"  {row['study_count']:>3}x  {row['blocker']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH)
    parser.add_argument("--out", type=Path, help="write the full JSON report here")
    parser.add_argument("--summary", action="store_true")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="report without updating the state file, so the next real run still sees the "
             "same baseline",
    )
    args = parser.parse_args(argv)

    if not args.root.is_dir():
        parser.error(f"--root is not a directory: {args.root}")

    report = run_once(root=args.root, state_path=args.state, dry_run=args.dry_run)

    if args.out:
        args.out.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        print(f"wrote {args.out}")
    if args.summary or args.out:
        print_summary(report)
    if not args.summary and not args.out:
        print(json.dumps(report, indent=2, sort_keys=True))

    return CHANGES_FOUND if report["changes"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
