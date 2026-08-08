"""Build the execution-cost profile artifact from the captured quote corpus.

Writes `data/execution_costs/spread_profile.json`. See `core/execution_cost.py`
for why the equity and option profiles are kept separate and why the source
database is opened read-only.

Usage:
  python3 scripts/build_execution_cost_profile.py
  python3 scripts/build_execution_cost_profile.py --db /path/to/tradier_stream.sqlite
  python3 scripts/build_execution_cost_profile.py --all-hours
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (str(ROOT), str(ROOT / "core")):
    if path not in sys.path:
        sys.path.insert(0, path)

import execution_cost as ec  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ec.DEFAULT_DB))
    ap.add_argument("--out", default=str(ec.DEFAULT_OUT))
    ap.add_argument("--all-hours", action="store_true",
                    help="include pre/post-market quotes, which are much wider")
    args = ap.parse_args()

    db = Path(args.db)
    if not db.exists():
        print(f"no corpus at {db}", file=sys.stderr)
        return 1

    print(f"reading {db} (read-only)…", flush=True)
    profile = ec.build_profile(db, rth_only=not args.all_hours)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(profile, indent=2))

    window = profile["capture_window"]
    print(f"\ncapture window: {window['first_event'][:10]} .. {window['last_event'][:10]} "
          f"({window['distinct_days']} days)")

    equity = profile["equity_half_spread_bps"]
    usable = {k: v for k, v in equity.items() if v["sufficient"]}
    print(f"\nequity half-spread, bps of price ({len(usable)} symbols with "
          f">={ec.MIN_SAMPLES_FOR_USE} samples)")
    print(f"  {'symbol':8s}{'samples':>10s}{'p25':>8s}{'median':>8s}{'p75':>8s}{'p95':>8s}{'zero%':>8s}")
    for symbol, cell in sorted(usable.items(), key=lambda kv: -kv[1]["samples"])[:20]:
        print(f"  {symbol:8s}{cell['samples']:>10d}{cell['p25']:>8.3f}{cell['median']:>8.3f}"
              f"{cell['p75']:>8.3f}{cell['p95']:>8.3f}{cell['zero_spread_share']*100:>8.1f}")

    if usable:
        medians = sorted(c["median"] for c in usable.values())
        mid = medians[len(medians) // 2]
        print(f"\n  median across symbols: {mid:.3f} bps per side")
        print(f"  vs the assumed 2.000 bps: assumption is "
              f"{'CONSERVATIVE' if mid < 2.0 else 'OPTIMISTIC'}")

    option = profile["option_half_spread_pct_of_premium"]
    usable_opt = {k: v for k, v in option.items() if v["sufficient"]}
    print(f"\noption half-spread, % of premium ({len(usable_opt)} cells with "
          f">={ec.MIN_SAMPLES_FOR_USE} samples)")
    print(f"  {'underlying|dte':22s}{'samples':>10s}{'median':>9s}{'p75':>9s}{'p95':>9s}")
    for key, cell in sorted(usable_opt.items(), key=lambda kv: -kv[1]["samples"])[:12]:
        print(f"  {key:22s}{cell['samples']:>10d}{cell['median']:>9.2f}"
              f"{cell['p75']:>9.2f}{cell['p95']:>9.2f}")

    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
