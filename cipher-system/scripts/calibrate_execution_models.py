#!/usr/bin/env python3
"""Where do the labs' assumed execution costs sit in the measured spread distribution?

    python3 scripts/calibrate_execution_models.py
    python3 scripts/calibrate_execution_models.py --symbols SPY QQQ IWM --buckets 0dte front

Answers one question and refuses the adjacent one. It reports whether an assumption is
optimistic or pessimistic against currently observable spreads. It does not reprice any
backtest, because the measured window is short and the studies are not.

Read the limitations block in the output before quoting any number from it.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

from core.execution_calibration import DEFAULT_PROFILE, load_profile, report  # noqa: E402

# Stated in every run because the numbers invite a conclusion they do not support. Each entry
# is a reason the comparison understates how conservative the labs already are.
LIMITATIONS = (
    "The labs buy at the observed bar HIGH and then add this fraction, so the total "
    "assumption is more conservative than the fraction alone.",
    "An absolute floor per fill ($0.03/$0.05/$0.10) and per-contract fees ($0.75/$1.00 per "
    "side) are also applied by the labs and are not compared here.",
    "Measured values are quoted half-spreads, not achieved fills. A real fill can be worse "
    "than the quote at the moment of trading.",
    "The measured window is recent and short; it does not overlap most of any study period. "
    "It describes today's liquidity, not the liquidity a backtest traded through.",
    "A verdict of 'harsher than measured p95' does not make a strategy profitable. It means "
    "the stress case sits outside the measured distribution, so a failure under it is weak "
    "evidence either way.",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--symbols", nargs="+", default=["SPY", "QQQ", "IWM"])
    parser.add_argument("--buckets", nargs="+", default=["0dte", "front", "swing"])
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    profile = load_profile(args.profile)
    if profile is None:
        print(f"no readable spread profile at {args.profile}", file=sys.stderr)
        print("Build one with scripts/build_execution_cost_profile.py", file=sys.stderr)
        return 1

    payload = report(args.symbols, args.buckets, profile=profile)
    payload["limitations"] = list(LIMITATIONS)

    if args.out:
        args.out.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        print(f"wrote {args.out}")
        return 0
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    window = payload["measured_window"]
    print(
        f"measured over {window['distinct_days']} capture days "
        f"({str(window['first_event'])[:10]} -> {str(window['last_event'])[:10]})"
    )
    print(
        f"{payload['measured_cells']} cells measured, {payload['unmeasured_cells']} without a "
        f"measured counterpart, {payload['assumptions_harsher_than_p95']} assumptions harsher "
        f"than the measured p95\n"
    )
    header = (
        f"{'symbol':7}{'bucket':8}{'model':8}{'assumed%':>9}{'median':>8}{'p75':>7}"
        f"{'p95':>8}{'xmedian':>9}  verdict"
    )
    print(header)
    for row in payload["rows"]:
        if row["measured_median"] is None:
            continue
        print(
            f"{row['symbol']:7}{row['lab_bucket']:8}{row['model']:8}"
            f"{row['assumed_pct_of_premium']:>9}{row['measured_median']:>8}"
            f"{row['measured_p75']:>7}{row['measured_p95']:>8}{row['ratio_to_median']:>9}  "
            f"{row['verdict']}"
        )

    unmeasured = sorted({
        (r["symbol"], r["lab_bucket"]) for r in payload["rows"] if r["measured_median"] is None
    })
    if unmeasured:
        print("\nno measured cell for: " + ", ".join(f"{s} {b}" for s, b in unmeasured))

    print("\nLIMITATIONS — read before quoting any number above")
    for line in LIMITATIONS:
        print(f"  - {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
