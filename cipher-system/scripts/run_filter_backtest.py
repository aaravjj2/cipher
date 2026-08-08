#!/usr/bin/env python3
"""Evaluate the Obsidian detector as a FILTER rather than an entry trigger.

`run_obsidian_backtest.py` asks the hardest question a signal can face: alone, can
it beat random entry on timing, direction and selection simultaneously? The answer
was no — 0 of 36 exit configurations profitable in every training fold.

That result cannot distinguish two very different situations:

  * the signal carries no information at all, or
  * it carries information that simply is not an entry trigger.

This driver asks the weaker, likelier question. It generates trades from a base
strategy with no view on price (fixed-cadence entries), partitions them by the
detector's state in the bars just before entry, and compares the partitions —
each against its own matched random control, because splitting a trade set enough
ways will always surface a flattering slice.

Usage:
  python3 scripts/run_filter_backtest.py
  python3 scripts/run_filter_backtest.py --timeframe 15Min --mode "EOD Focus" --lookback 6
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
sys.path.insert(0, str(ROOT / "core"))

import backtest_engine as be  # noqa: E402
from scripts.run_obsidian_backtest import UNIVERSE, load_bars  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeframe", default="15Min")
    ap.add_argument("--years", type=float, default=1.0)
    ap.add_argument("--mode", default="EOD Focus", choices=("EOD Focus", "Full Session"))
    ap.add_argument("--symbols", default="")
    ap.add_argument("--lookback", type=int, default=6,
                    help="bars before entry in which a signal counts as active")
    ap.add_argument("--entry-every", type=int, default=12,
                    help="base-strategy cadence in bars")
    ap.add_argument("--control-repeats", type=int, default=20)
    ap.add_argument("--json-out", default="")
    args = ap.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] or UNIVERSE
    print(f"loading {len(symbols)} symbols, {args.timeframe}, {args.years}y")
    bars = load_bars(symbols, args.timeframe, args.years)
    if not bars:
        print("no data", file=sys.stderr)
        return 1

    print(f"\ndetector mode: {args.mode}   lookback: {args.lookback} bars   "
          f"base cadence: every {args.entry_every} bars")
    report = be.run_filter(
        bars,
        detector_params={"mode": args.mode},
        lookback_bars=args.lookback,
        entry_every=args.entry_every,
        control_repeats=args.control_repeats,
    )
    if "error" in report:
        print(report["error"], file=sys.stderr)
        return 1

    base = report["base"]
    print(f"\nBASE (no filter)  n={base['trades']} win={base['win_rate']}% "
          f"avg={base['avg_return_pct']:.4f}% med={base['median_return_pct']:.4f}% "
          f"PF={base['profit_factor']}")

    print(f"\n{'partition':<10}{'n':>7}{'share':>8}{'win%':>8}{'avg%':>10}{'lift(pp)':>10}  verdict")
    for key, part in report["partitions"].items():
        s = part["stats"]
        lift = part.get("lift_vs_base_pp")
        if "note" in part:
            verdict = part["note"]
        elif part.get("beats_control_range"):
            verdict = "BEATS its own random control"
        else:
            verdict = "within noise of random entry"
        print(f"{key:<10}{s['trades']:>7}{part['share_of_base']:>7}%{s['win_rate']:>8}"
              f"{s['avg_return_pct']:>10.4f}{(lift if lift is not None else 0):>10.4f}  {verdict}")

    fired = {k: v for k, v in report["partitions"].items() if k != "none"}
    any_beats = any(v.get("beats_control_range") for v in fired.values())
    print("\n" + ("A filtered partition clears its own control — worth pursuing."
                  if any_beats else
                  "No filtered partition clears its own random control. The detector "
                  "does not usefully separate trades it did not trigger."))

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, indent=2, default=str))
        print(f"\nwrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
