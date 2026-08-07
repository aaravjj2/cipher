#!/usr/bin/env python3
"""Walk-forward backtest of the Obsidian EOD detector over deep Alpaca history.

The earlier smoke test ran on ~288 daily bars, which was not an Alpaca limit but
the window the ad-hoc driver happened to request. Alpaca actually serves ~2500
daily bars (back to 2016) and ~13 months of 15-minute bars, which is enough for a
real walk-forward plus a locked holdout.

Usage:
  python scripts/run_obsidian_backtest.py                     # daily, 10y, 3 folds
  python scripts/run_obsidian_backtest.py --timeframe 15Min --years 1
  python scripts/run_obsidian_backtest.py --setups "FLOOR BOUNCE,CEILING REJECTION"
  python scripts/run_obsidian_backtest.py --refresh            # ignore the bar cache

Bars are cached under data/bar_cache/ so repeated parameter runs do not re-hit the
API — and so a walk-forward is reproducible against a fixed dataset rather than a
silently shifting one.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from core import backtest_engine  # noqa: E402
from core.data_fetcher import fetch_alpaca_bars, load_env  # noqa: E402

CACHE_DIR = ROOT / "data" / "bar_cache"

# Liquid, optionable names with long continuous history. Kept deliberately broad
# across sectors — a universe of only mega-cap tech would make any 2016-2026
# backtest a proxy for one sector's trend.
UNIVERSE = [
    "SPY", "QQQ", "IWM", "DIA",
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA",
    "JPM", "BAC", "GS", "XOM", "CVX",
    "JNJ", "UNH", "PFE", "WMT", "HD", "COST",
    "AMD", "INTC", "MU", "CRM", "ORCL",
    "CAT", "BA", "NKE", "DIS", "SBUX", "T", "VZ",
]


def load_bars(symbols, timeframe, years, refresh=False):
    """Fetch (or read cached) bars, normalised to the detector's schema."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    creds = load_env()
    end = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    start = end - dt.timedelta(days=int(years * 365.25))
    out = {}
    for sym in symbols:
        cache = CACHE_DIR / f"{sym}_{timeframe}_{years}y.json"
        if cache.exists() and not refresh:
            raw = json.loads(cache.read_text())
        else:
            raw = fetch_alpaca_bars(sym, start, end, timeframe=timeframe, creds=creds)
            if raw:
                cache.write_text(json.dumps(raw))
        if not raw:
            print(f"  {sym}: no bars", file=sys.stderr)
            continue
        out[sym] = [
            {
                "time": b["t"], "open": b["o"], "high": b["h"],
                "low": b["l"], "close": b["c"], "volume": b["v"],
            }
            for b in raw
        ]
        print(f"  {sym}: {len(out[sym])} bars {out[sym][0]['time'][:10]}..{out[sym][-1]['time'][:10]}")
    return out


def fmt_stats(s):
    if not s or not s.get("trades"):
        return "no trades"
    return (
        f"n={s['trades']} win={s['win_rate']}% avg={s['avg_return_pct']:.4f}% "
        f"med={s['median_return_pct']:.4f}% PF={s['profit_factor']} "
        f"maxDD={s['max_drawdown_pct']}%"
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeframe", default="1Day")
    ap.add_argument("--years", type=float, default=10.0)
    ap.add_argument("--folds", type=int, default=3)
    ap.add_argument("--holdout-frac", type=float, default=0.25)
    ap.add_argument("--symbols", default="")
    ap.add_argument("--setups", default="")
    ap.add_argument("--stop-atr", type=float, default=backtest_engine.DEFAULT_STOP_ATR)
    ap.add_argument("--target-atr", type=float, default=backtest_engine.DEFAULT_TARGET_ATR)
    ap.add_argument("--max-hold-bars", type=int, default=backtest_engine.DEFAULT_MAX_HOLD_BARS)
    ap.add_argument("--cost-bps", type=float, default=backtest_engine.DEFAULT_COST_BPS)
    # The indicator's own default is "EOD Focus", which only arms in the last
    # `arm_minutes` before the close. On daily bars that gate is meaningless, so
    # "Full Session" is the honest choice there — but on intraday bars EOD Focus is
    # the configuration the detector was actually written for.
    ap.add_argument("--mode", default="EOD Focus", choices=("EOD Focus", "Full Session"))
    ap.add_argument("--control-repeats", type=int, default=20)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--json-out", default="")
    args = ap.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] or UNIVERSE
    setups = {s.strip().upper() for s in args.setups.split(",") if s.strip()} or None

    print(f"loading {len(symbols)} symbols, {args.timeframe}, {args.years}y")
    bars = load_bars(symbols, args.timeframe, args.years, refresh=args.refresh)
    if not bars:
        print("no data", file=sys.stderr)
        return 1

    kw = dict(
        setups=setups, stop_atr=args.stop_atr, target_atr=args.target_atr,
        max_hold_bars=args.max_hold_bars, cost_bps=args.cost_bps,
        detector_params={"mode": args.mode},
    )
    print(f"detector mode: {args.mode}")

    print("\n=== FULL SAMPLE (in-sample, for reference only) ===")
    full = backtest_engine.run_backtest(bars, **kw)
    print(fmt_stats(full.stats))
    for k, v in (full.stats.get("by_setup") or {}).items():
        print(f"  {k:<24} n={v['n']:<5} avg={v['avg_pct']:>8.4f}%  win={v['win_rate']}%")

    # Diagnostic: is the setup-to-direction mapping simply backwards? Reported
    # alongside the normal run rather than in place of it — picking whichever
    # direction looks better after the fact is exactly the overfit this engine
    # exists to prevent.
    inv = backtest_engine.run_backtest(bars, invert=True, **kw)
    print(f"inverted (diagnostic)  {fmt_stats(inv.stats)}")
    inv_wf = backtest_engine.walk_forward(
        bars, folds=args.folds, holdout_frac=args.holdout_frac, invert=True, **kw
    )
    inv_hold = inv_wf.get("holdout")
    if inv_hold:
        print(f"inverted HOLDOUT       {fmt_stats(inv_hold)}")

    print(f"\n=== RANDOM-ENTRY CONTROL ({args.control_repeats} draws, matched by symbol+direction) ===")
    ctrl = backtest_engine.run_control(full, bars, repeats=args.control_repeats, **kw)
    c = ctrl.get("control")
    if c:
        print(f"control    win={c['win_rate']}% avg={c['avg_return_pct']:.4f}% "
              f"med={c['median_return_pct']:.4f}% PF={c['profit_factor']} "
              f"(avg over draws ranged {c['avg_return_pct_range'][0]:.4f}%..{c['avg_return_pct_range'][1]:.4f}%)")
        d = ctrl["detector_minus_control"]
        print(f"detector-control  win={d['win_rate']:+}pp avg={d['avg_return_pct']:+.4f}pp "
              f"PF={d['profit_factor']:+.3f}")
        print("VERDICT: " + ("detector beats every random draw"
                             if ctrl["detector_beats_control_range"]
                             else "detector does NOT clear the random-entry range — "
                                  "no entry-timing edge demonstrated"))

    print(f"\n=== WALK-FORWARD ({args.folds} folds + {args.holdout_frac:.0%} locked holdout) ===")
    report = backtest_engine.walk_forward(
        bars, folds=args.folds, holdout_frac=args.holdout_frac, **kw
    )
    for w in report["warnings"]:
        print(f"WARN: {w}")
    for f in report["folds"]:
        print(f"fold {f['fold']} {f['range']} syms={f['symbols']}: {fmt_stats(f['stats'])}")
    print(f"HOLDOUT: {fmt_stats(report['holdout']) if report['holdout'] else 'none'}")

    # The only line that matters: does the holdout agree with the folds?
    fold_pf = [f["stats"].get("profit_factor") for f in report["folds"]
               if f["stats"].get("profit_factor")]
    hold_pf = (report["holdout"] or {}).get("profit_factor")
    if fold_pf and hold_pf:
        ctrl_pf = ((ctrl.get("control") or {}).get("profit_factor")) or 1.0
        print(f"\nfold PF mean={sum(fold_pf)/len(fold_pf):.3f}  holdout PF={hold_pf:.3f}  "
              f"control PF={ctrl_pf:.3f}")
        # Two independent bars, and BOTH must clear. Profitable-but-no-better-than-
        # random is a market-drift result, and better-than-random-while-losing is
        # still a losing strategy — neither is tradable.
        if hold_pf < 1.0:
            print("VERDICT: holdout loses money (PF < 1.0). Beating the control while "
                  "unprofitable is not an edge — it only means the control lost harder.")
        elif hold_pf > ctrl_pf * 1.05:
            print("VERDICT: holdout is profitable AND beats the random-entry control")
        else:
            print("VERDICT: holdout is profitable but within noise of random entry — "
                  "consistent with market drift, not entry-timing edge")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(
            {"full": full.stats, "inverted": inv.stats, "control": ctrl,
             "walk_forward": report, "walk_forward_inverted": inv_wf,
             "params": full.params}, indent=2
        ))
        print(f"\nwrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
