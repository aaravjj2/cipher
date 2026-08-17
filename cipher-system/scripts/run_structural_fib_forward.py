#!/usr/bin/env python3
"""One pass of the Structural Fib forward test: detect, record, score, report.

    python3 scripts/run_structural_fib_forward.py            # a live pass
    python3 scripts/run_structural_fib_forward.py --report   # read the record, fetch nothing

Designed to run every five minutes during market hours. Research-only: it records signals and
scores them after the fact. It places no orders and holds no position.

Exit 0 always on a normal pass -- a session with no signal is the expected case, not a fault.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, time as dtime
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))
if str(HERE.parent.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent.parent))

from core import structural_fib_forward as fwd  # noqa: E402
from core.structural_fib_lab import NY  # noqa: E402


def pct(x) -> str:
    return "  --  " if x is None else f"{100.0 * x:5.1f}%"


def print_report(summary: dict) -> None:
    print("=" * 84)
    print("STRUCTURAL FIB — FORWARD TEST (pre-registered)")
    print("=" * 84)
    print(f"params {summary['params_hash']}   sessions {summary['sessions_recorded']}"
          f"   signals {summary['signals_recorded']}"
          f"   scored {summary['signals_scored']}"
          f"   awaiting {summary['signals_awaiting_outcome']}")
    if summary["first_session"]:
        print(f"window {summary['first_session']} -> {summary['last_session']}")
    if summary["params_drift"]:
        print("  !! PARAMETER DRIFT: signals in this record were produced under more than one")
        print(f"     parameter set {summary['params_hashes_in_data']} — do not pool them.")
    costs = summary.get("cost_basis_by_symbol") or {}
    if costs:
        print("cost basis: " + "  ".join(f"{k}={v}" for k, v in sorted(costs.items())))

    legs = summary.get("legs") or {}
    if not legs:
        print("\nNo scored signals yet. Nothing to conclude — this is the expected state on")
        print("day one, and it is what the record looks like before evidence exists.")
        return

    print(f"\n  {'setup / leg':<22}{'n':>4}{'fwd touch':>11}{'ci95':>16}"
          f"{'backtest':>10}{'claimed':>9}{'race':>8}{'avg%':>9}")
    for key, s in legs.items():
        lo, hi = s["touch_ci95"]
        flag = " *" if s["underpowered"] else ""
        print(f"  {key:<22}{s['n']:>4}{pct(s['touch_rate']):>11}"
              f"{f'[{pct(lo).strip()},{pct(hi).strip()}]':>16}"
              f"{pct(s['backtest_touch_rate']):>10}{pct(s['claimed']):>9}"
              f"{pct(s['race_win_rate']):>8}{s['avg_return_pct']:>8.3f}%{flag}")

    print("\n  VERDICT PER LEG (does the interval exclude each reference rate?)")
    for key, s in legs.items():
        bits = []
        if s["claim_excluded"] is not None:
            bits.append(f"claim {s['claimed'] * 100:.1f}% "
                        + ("EXCLUDED" if s["claim_excluded"] else "still consistent"))
        if s["backtest_excluded"] is not None:
            bits.append(f"backtest {s['backtest_touch_rate'] * 100:.1f}% "
                        + ("excluded" if s["backtest_excluded"] else "consistent"))
        print(f"    {key:<22} " + "; ".join(bits))

    print(f"\n  * = fewer than 12 observations; underpowered, not a result.")
    print("  A leg consistent with BOTH the claim and the backtest simply has too few")
    print("  observations yet to separate them. That is a sample-size fact, not a finding.")

    print("\nLIMITATIONS")
    for item in summary["limitations"]:
        print(f"  - {item}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", type=Path, default=fwd.DEFAULT_DIR)
    ap.add_argument("--symbols", default=",".join(fwd.PARAMS["symbols"]))
    ap.add_argument("--report", action="store_true",
                    help="print the standing record without fetching or recording")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="detect outside the regular session (for testing)")
    args = ap.parse_args(argv)

    if not args.report:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        now_et = datetime.now(NY)
        print(f"pass at {now_et:%Y-%m-%d %H:%M} ET")
        # Detection only inside the session it can detect anything in. The timer fires all
        # day so that the post-close scoring pass is guaranteed to run, but fetching bars
        # every five minutes overnight would spend the provider budget to re-read a session
        # that has stopped changing. Scoring below is unaffected and self-limiting: it
        # fetches nothing unless a closed session has unscored signals.
        weekday = now_et.weekday() < 5
        in_window = dtime(9, 30) <= now_et.time() <= dtime(16, 5)
        if not (weekday or args.force) or not (in_window or args.force):
            print("  outside the regular session — detection skipped, scoring still runs")
            symbols = []
        for symbol in symbols:
            try:
                bars = fwd.fetch_recent(symbol)
            except Exception as exc:  # a vendor hiccup must not lose the record
                print(f"  {symbol}: fetch failed ({exc.__class__.__name__}: {exc})")
                continue
            if not bars:
                print(f"  {symbol}: no bars returned")
                continue
            pending = fwd.detect(symbol, bars)
            outcome = fwd.record(pending, directory=args.dir)
            newest = bars[-1].t.strftime("%H:%M")
            print(f"  {symbol}: bars to {newest} ET  ->  {outcome['detected']} signal(s) live, "
                  f"{outcome['newly_recorded']} newly recorded")
            for p in pending:
                print(f"      [{p.setup} {p.leg} {p.direction}] {p.signal_time_et} ET  "
                      f"entry {p.entry_price:.2f} -> target {p.target:.2f} / stop {p.stop:.2f}  "
                      f"({p.reward_pct:.2f}% vs {p.risk_pct:.2f}%)")
        scored = fwd.score(directory=args.dir)
        print(f"  scoring: {scored['scored_now']} resolved, "
              f"{scored['waiting_on_open_sessions']} waiting on an open session")

    summary = fwd.report(directory=args.dir)
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print()
        print_report(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
