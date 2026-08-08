#!/usr/bin/env python3
"""Search exit rules on the TRAINING slice only, then score the winner once.

Context for why this script is shaped the way it is:

The Obsidian detector's signals were found to carry real directional information
in "EOD Focus" mode, but pointing the OPPOSITE way to their labels — over 1628
15-minute trades, trading the labels won 34.7% while fading them won 44.2%, and
the fade held at 44.2% on a locked holdout it had no influence on. Fading still
lost money (PF 0.882) under the arbitrary 1.0/1.5 ATR exit rule used to find it.

So the open question is whether ANY reasonable exit turns that directional
information into a profitable rule. Answering it means searching parameters, and
searching parameters over the whole sample is how backtests manufacture edge. The
protocol here:

  1. Carve the holdout off FIRST. It is never scored during the search.
  2. Sweep exits over the training folds only; rank by mean fold profit factor,
     requiring the config to be positive in EVERY fold (a config that is great in
     one fold and awful in two is a fluke, not a rule).
  3. Score exactly ONE chosen config on the holdout, once, and report it whether
     it works or not.
  4. Report the best-of-N inflation explicitly, so the training number is never
     mistaken for an expectation.

Usage:
  python scripts/sweep_obsidian_exits.py --timeframe 15Min --years 1 --invert
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from core import backtest_engine as be  # noqa: E402
from core import obsidian_eod  # noqa: E402
from scripts.run_obsidian_backtest import UNIVERSE, load_bars, fmt_stats  # noqa: E402

MIN_BARS = 120
# Deliberately coarse. A fine grid over the same data does not find more truth, it
# just raises the best-of-N inflation and makes the winner less likely to survive.
STOPS = (0.5, 1.0, 1.5)
TARGETS = (1.0, 1.5, 2.0, 3.0)
HOLDS = (6, 12, 24)


def slice_bars(bars_by_symbol, lo_frac, hi_frac):
    out = {}
    for sym, bars in bars_by_symbol.items():
        n = len(bars)
        lo, hi = int(n * lo_frac), int(n * hi_frac)
        if hi - lo >= MIN_BARS:
            out[sym] = bars[lo:hi]
    return out


def precompute(sliced, detector_params):
    """Detector states per symbol — identical across every exit rule."""
    states = {}
    for sym, bars in sliced.items():
        st, _ = obsidian_eod.compute(bars, detector_params)
        if st:
            states[sym] = st
    return states


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeframe", default="15Min")
    ap.add_argument("--years", type=float, default=1.0)
    ap.add_argument("--mode", default="EOD Focus", choices=("EOD Focus", "Full Session"))
    ap.add_argument("--folds", type=int, default=3)
    ap.add_argument("--holdout-frac", type=float, default=0.25)
    ap.add_argument("--invert", action="store_true",
                    help="fade the setups rather than following them")
    ap.add_argument("--cost-bps", type=float, default=be.DEFAULT_COST_BPS)
    ap.add_argument("--min-trades", type=int, default=100,
                    help="reject configs whose folds are too thin to mean anything")
    ap.add_argument("--symbols", default="")
    ap.add_argument("--json-out", default="")
    args = ap.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] or UNIVERSE
    detector_params = {"mode": args.mode}

    print(f"loading {len(symbols)} symbols, {args.timeframe}, {args.years}y")
    bars = load_bars(symbols, args.timeframe, args.years)
    if not bars:
        return 1

    train_end = 1.0 - args.holdout_frac
    print(f"\nmode={args.mode} invert={args.invert} cost={args.cost_bps}bps")
    print(f"training = first {train_end:.0%} in {args.folds} folds; "
          f"holdout = last {args.holdout_frac:.0%} (locked)")

    # Precompute detector states once per fold. This is the expensive part and it
    # does not vary with the exit rule.
    fold_slices, fold_states = [], []
    for f in range(args.folds):
        lo = train_end * f / args.folds
        hi = train_end * (f + 1) / args.folds
        sl = slice_bars(bars, lo, hi)
        if not sl:
            continue
        fold_slices.append(sl)
        fold_states.append(precompute(sl, detector_params))
    if not fold_slices:
        print("no usable training folds", file=sys.stderr)
        return 1
    print(f"precomputed detector states for {len(fold_slices)} folds")

    grid = list(itertools.product(STOPS, TARGETS, HOLDS))
    print(f"\nsweeping {len(grid)} exit configs on TRAINING ONLY")
    rows = []
    for stop, target, hold in grid:
        fold_stats = []
        for sl, st in zip(fold_slices, fold_states):
            r = be.run_backtest(
                sl, stop_atr=stop, target_atr=target, max_hold_bars=hold,
                cost_bps=args.cost_bps, detector_params=detector_params,
                invert=args.invert, states_by_symbol=st,
            )
            fold_stats.append(r.stats)
        pfs = [s.get("profit_factor") for s in fold_stats if s.get("profit_factor")]
        ns = [s.get("trades", 0) for s in fold_stats]
        if len(pfs) < len(fold_slices) or min(ns) < args.min_trades:
            continue
        rows.append({
            "stop_atr": stop, "target_atr": target, "max_hold_bars": hold,
            "fold_pf": [round(p, 3) for p in pfs],
            "mean_pf": round(sum(pfs) / len(pfs), 4),
            "min_pf": round(min(pfs), 4),
            "trades": sum(ns),
        })

    if not rows:
        print("no config met the minimum-trade floor in every fold", file=sys.stderr)
        return 1

    # Rank by the WORST fold, not the mean. A rule that only works in one regime
    # is not a rule, and ranking by mean rewards exactly that.
    rows.sort(key=lambda r: (r["min_pf"], r["mean_pf"]), reverse=True)
    print(f"\ntop training configs ({len(rows)} survived the trade floor):")
    for r in rows[:8]:
        print(f"  stop={r['stop_atr']} target={r['target_atr']} hold={r['max_hold_bars']:>3}  "
              f"folds={r['fold_pf']}  min={r['min_pf']:.3f} mean={r['mean_pf']:.3f}  "
              f"n={r['trades']}")

    best = rows[0]
    profitable = [r for r in rows if r["min_pf"] > 1.0]
    print(f"\n{len(profitable)}/{len(rows)} configs profitable in every training fold")

    if best["min_pf"] <= 1.0:
        print("\nNo config was profitable in every training fold. Nothing earns a "
              "holdout evaluation — scoring the holdout now would just be a second "
              "search over data reserved to keep the first one honest.")
        chosen = None
    else:
        chosen = best
        print(f"\nCHOSEN (training only): stop={chosen['stop_atr']} "
              f"target={chosen['target_atr']} hold={chosen['max_hold_bars']}")

    holdout_stats = None
    if chosen:
        hold_slice = slice_bars(bars, train_end, 1.0)
        print(f"\n=== HOLDOUT — evaluated once, on {len(hold_slice)} symbols ===")
        hr = be.run_backtest(
            hold_slice, stop_atr=chosen["stop_atr"], target_atr=chosen["target_atr"],
            max_hold_bars=chosen["max_hold_bars"], cost_bps=args.cost_bps,
            detector_params=detector_params, invert=args.invert,
        )
        holdout_stats = hr.stats
        print(fmt_stats(holdout_stats))

        ctrl = be.run_control(
            hr, hold_slice, repeats=20, stop_atr=chosen["stop_atr"],
            target_atr=chosen["target_atr"], max_hold_bars=chosen["max_hold_bars"],
            cost_bps=args.cost_bps,
        )
        c = ctrl.get("control") or {}
        if c:
            print(f"control on holdout: win={c['win_rate']}% avg={c['avg_return_pct']:.4f}% "
                  f"PF={c['profit_factor']}")
        hp = holdout_stats.get("profit_factor")
        cp = c.get("profit_factor")
        print(f"\nbest-of-{len(grid)} inflation: training min-fold PF was "
              f"{chosen['min_pf']:.3f} after searching {len(grid)} configs; the "
              f"holdout number below is the only unbiased one.")
        if hp and hp > 1.0 and cp and hp > cp * 1.05:
            print("VERDICT: survives — profitable on the holdout and ahead of random entry.")
        elif hp and hp > 1.0:
            print("VERDICT: profitable on the holdout but not clearly ahead of random "
                  "entry. Consistent with drift, not edge.")
        else:
            print("VERDICT: does NOT survive. The training result was best-of-N noise.")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps({
            "mode": args.mode, "invert": args.invert, "grid_size": len(grid),
            "training": rows, "chosen": chosen, "holdout": holdout_stats,
        }, indent=2))
        print(f"\nwrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
