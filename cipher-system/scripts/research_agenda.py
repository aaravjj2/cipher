#!/usr/bin/env python3
"""What to research next, derived from what Cipher has already established.

Phase 2's CLI. It reads every adaptable report, censuses the reasons those results cannot be
believed, and proposes only the actions that would actually clear one. Blockers that cannot
be cleared, and blockers that need a purchase rather than a compute job, are reported
separately so neither disappears and neither becomes a task.

It is allowed to recommend nothing, and says why when it does.

    python3 scripts/research_agenda.py --summary
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

from core.research_agenda import propose  # noqa: E402
from core.research_corpus import DEFAULT_ROOT, collect  # noqa: E402
from core.research_envelope import current_commit  # noqa: E402


def print_summary(agenda: dict) -> None:
    print(f"{agenda['studies_considered']} studies considered, "
          f"{agenda['selectable_today']} selectable today")

    if agenda["nothing_to_run_because"]:
        print(f"\nNo action recommended: {agenda['nothing_to_run_because']}")
    else:
        print("\nRECOMMENDED, most-unblocking first")
        for index, action in enumerate(agenda["recommended_actions"], start=1):
            print(f"\n  {index}. {action['action']}")
            print(f"     unblocks {action['unblocks_studies']} studies")
            if action["detail"]:
                print(f"     how: {action['detail']}")
            print(f"     latency: {action['latency']}")
            if action["limitation"]:
                print(f"     limit: {action['limitation']}")

    if agenda["blockers_requiring_acquisition"]:
        print("\nNEEDS DATA YOU DO NOT HAVE — a spending decision, not a task")
        for row in agenda["blockers_requiring_acquisition"]:
            print(f"  {row['study_count']:>3}x  {row['blocker']}")

    if agenda["blockers_that_cannot_be_cleared"]:
        print("\nCANNOT BE CLEARED — properties of the method, not gaps")
        for row in agenda["blockers_that_cannot_be_cleared"]:
            print(f"  {row['study_count']:>3}x  {row['blocker']}")

    if agenda["unclassified_blockers"]:
        print(f"\nUNCLASSIFIED — {agenda['unclassified_warning']}")
        for row in agenda["unclassified_blockers"]:
            print(f"  {row['study_count']:>3}x  {row['blocker']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--out", type=Path, help="write JSON here instead of stdout")
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args(argv)

    if not args.root.is_dir():
        parser.error(f"--root is not a directory: {args.root}")

    results, unadapted = collect(args.root, commit=current_commit())
    agenda = propose(results)
    agenda["unadapted_reports"] = len(unadapted)

    if args.out:
        args.out.write_text(json.dumps(agenda, indent=2, sort_keys=True), encoding="utf-8")
        print(f"wrote {args.out}")
    elif args.summary:
        print_summary(agenda)
    else:
        print(json.dumps(agenda, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
