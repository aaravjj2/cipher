#!/usr/bin/env python3
"""Run the faithful Structural Fib study and print the result.

    python3 scripts/run_structural_fib_lab.py
    python3 scripts/run_structural_fib_lab.py --symbols NVDA,AAPL,MU --advance 0.0

Research-only. Reports what it measures, including negative results.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from core import structural_fib_lab as lab  # noqa: E402

DEFAULT_DB = Path(
    "/home/aarav/Aarav/cipher/EOD strategy/data/historical_equities/"
    "obsidian_pine_ytd_2026/equity_bars.sqlite"
)
DEFAULT_OUT = Path("/home/aarav/Aarav/cipher/runtime/data/structural_fib_faithful")


def pct(x) -> str:
    return "--" if x is None else f"{100.0 * x:.1f}%"


def print_table(title: str, groups: dict) -> None:
    rows = [(k, v) for k, v in groups.items() if v.get("n")]
    if not rows:
        return
    print(f"\n{title}")
    print(f"  {'setup / leg':<22}{'n':>5}{'touch':>8}{'ci95':>15}"
          f"{'claimed':>9}{'verdict':>10}{'race':>8}{'avg%':>8}")
    for key, s in rows:
        lo, hi = s.get("touch_ci95", (0, 0))
        if s.get("claim_excluded") is None:
            verdict = "--"
        elif s["claim_excluded"]:
            verdict = "REFUTED"
        else:
            verdict = "consistent"
        flag = " *" if s.get("underpowered") else ""
        print(f"  {key:<22}{s['n']:>5}{pct(s['touch_rate']):>8}"
              f"{f'[{pct(lo)}, {pct(hi)}]':>15}{pct(s.get('claimed')):>9}"
              f"{verdict:>10}{pct(s['race_win_rate']):>8}"
              f"{s['avg_return_pct']:>7.3f}%{flag}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--symbols", default="NVDA,AAPL")
    ap.add_argument("--advance", type=float, default=lab.REVERSAL_MIN_ADVANCE_R,
                    help="minimum advance in R before a reversal counts (0 = unconditional)")
    ap.add_argument("--entry", choices=("confirmed", "level"), default="confirmed",
                    help="confirmed = body close past the level (the method as taught); "
                         "level = resting limit at the level itself")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args()

    db = Path(args.db)
    if not db.is_file():
        print(f"no bar database at {db}", file=sys.stderr)
        return 2
    symbols = tuple(s.strip().upper() for s in args.symbols.split(",") if s.strip())

    report = lab.run(db, symbols, min_advance_r=args.advance, entry_mode=args.entry)

    print("=" * 78)
    print("STRUCTURAL FIB — trailed anchor, reversal separated from continuation")
    print("=" * 78)
    print(f"symbols: {', '.join(symbols)}   reversal min advance: {args.advance}R"
          f"   entry: {args.entry}")

    print("\nCOVERAGE")
    for sym, cov in report["coverage"].items():
        if not cov.get("days"):
            print(f"  {sym:<6} {cov.get('note', 'no data')}")
            continue
        print(f"  {sym:<6} {cov['days']:>4} sessions  {cov['start']} -> {cov['end']}"
              f"   trending {cov['trending_days']:>3} / choppy {cov['choppy_days']:>3}"
              f"   with signals {cov['days_with_signals']:>3}")

    print("\nTHE FILTER'S OWN CLAIM — does pre-market range <= 1.5% select trending days?")
    fc = report["premarket_filter_check"]
    if fc:
        for regime, s in fc.items():
            lo, hi = s["ci95"]
            print(f"  {regime:<10} {s['trended']:>4}/{s['n']:<4} trended = "
                  f"{pct(s['rate'])}   ci95 [{pct(lo)}, {pct(hi)}]")
        t, c = fc.get("trending"), fc.get("choppy")
        if t and c:
            delta = t["rate"] - c["rate"]
            print(f"  -> the filter is worth {delta * 100:+.1f} percentage points "
                  f"(claimed: it identifies trending days ~90% of the time)")
    else:
        print("  no days classified")

    print_table("ALL SIGNALS", report["overall"])
    print_table("SHORT ONLY (the side the method calls cleanest)",
                report["by_direction"]["short"])
    print_table("LONG ONLY (the method's own weaker side)", report["by_direction"]["long"])
    for sym in symbols:
        print_table(f"{sym}", report["by_symbol"].get(sym, {}))
    print_table("TRENDING DAYS (own pre-market leg)", report["by_regime"]["trending"])
    print_table("CHOPPY DAYS (fallback leg)", report["by_regime"]["choppy"])

    ctrl = report.get("matched_random_entry_control") or {}
    if ctrl:
        print("\nMATCHED RANDOM-ENTRY CONTROL — same geometry, random entry time")
        print(f"  {'setup / leg':<22}{'strategy':>10}{'control':>10}{'edge':>8}"
              f"{'str.ret':>10}{'ctl.ret':>10}")
        for key, c in ctrl.items():
            s = report["overall"].get(key)
            if not s or not s.get("n"):
                continue
            edge = s["touch_rate"] - c["touch_rate"]
            print(f"  {key:<22}{pct(s['touch_rate']):>10}{pct(c['touch_rate']):>10}"
                  f"{edge * 100:>+7.1f}p{s['avg_return_pct']:>9.3f}%"
                  f"{c['avg_return_pct']:>9.3f}%")
        print("  edge = strategy touch rate minus control. If it is ~0 the Fibonacci")
        print("  level carried no information and the hit rate is bar geometry alone.")

    print("\n  * = fewer than "
          f"{lab.MIN_REPORT_N} observations; treat as underpowered, not as a result.")
    print("  touch = reached target before the close, no stop (the claimed quantity).")
    print("  race  = target before stop, ambiguous bars scored as the stop.")
    print("  avg%  = underlying price return of the raced position. Option cost not modelled.")

    print("\nLIMITATIONS")
    for item in report["limitations"]:
        print(f"  - {item}")

    if not args.no_write:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nwrote {out / 'report.json'} ({len(report['signals'])} signals)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
