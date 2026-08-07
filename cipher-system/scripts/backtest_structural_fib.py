#!/usr/bin/env python3
"""Test the Structural Fib strategy's published hit rates on real 5-minute bars.

The strategy, as specified:

  1. Measure the pre-market range, (PMH - PML) / PML. At or under 1.5% the day is
     expected to trend; wider is expected to chop, and you fall back to the most
     recent session that WAS at or under 1.5% and use its pre-market levels.
  2. Take the pre-market range as the fib unit R.
  3. Anchor the '0' level to the extreme wick of the opening 5-minute candle — the
     low for longs, the high for shorts. If that first candle covers more than 50%
     of the pre-market range ("massive overextension"), skip it and anchor to the
     second candle instead.
  4. Levels are 0, 0.5R, 1R and 2R from the anchor.
  5. Entry needs a "clean break": a 5-minute candle whose BODY closes past the
     level, not merely a wick through it.

The published likelihoods are 98% of reaching '1' after a 0.5 cross, 64% of
reaching '2' after a 1.0 cross, and 18% for an unconfirmed '3'.

Those numbers are almost certainly "did price ever touch the level before the
close", which is a much weaker statement than a tradeable win rate — with no stop
and a whole session to wait, hit rates inflate toward certainty. So both are
measured here: `touch_rate` on that charitable reading, and `win_rate` for the
same signal traded with a stop back at the anchor. The gap between them is the
point of the exercise.

Usage:
  python3 scripts/backtest_structural_fib.py
  python3 scripts/backtest_structural_fib.py --symbols NVDA,AAPL --days 180
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
sys.path.insert(0, str(ROOT / "core"))

from core.data_fetcher import fetch_alpaca_bars, load_env  # noqa: E402

ET = ZoneInfo("America/New_York")
PRE_OPEN, REG_OPEN, REG_CLOSE = dtime(4, 0), dtime(9, 30), dtime(16, 0)
CACHE = ROOT / "data" / "bar_cache"

RANGE_FILTER_PCT = 1.5
OVEREXTENSION_FRAC = 0.5


def to_et(value):
    stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return stamp.astimezone(ET)


def load_5m(symbol, days, refresh=False):
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{symbol}_5Min_{days}d.json"
    if path.exists() and not refresh:
        raw = json.loads(path.read_text())
    else:
        end = datetime.now(timezone.utc).replace(tzinfo=None)
        raw = fetch_alpaca_bars(symbol, end - timedelta(days=days), end,
                                timeframe="5Min", creds=load_env())
        if raw:
            path.write_text(json.dumps(raw))
    return raw or []


def split_days(bars):
    """{date: {"pre": [...], "reg": [...]}} in exchange local time."""
    days = defaultdict(lambda: {"pre": [], "reg": []})
    for b in bars:
        try:
            moment = to_et(b["t"])
        except (ValueError, KeyError):
            continue
        clock = moment.time()
        bar = {"t": moment, "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"], "v": b.get("v", 0)}
        if PRE_OPEN <= clock < REG_OPEN:
            days[moment.date()]["pre"].append(bar)
        elif REG_OPEN <= clock < REG_CLOSE:
            days[moment.date()]["reg"].append(bar)
    return days


def premarket_extent(day):
    pre = day["pre"]
    if not pre:
        return None, None
    return max(b["h"] for b in pre), min(b["l"] for b in pre)


def evaluate(symbol, bars, use_fallback=True):
    """One record per tradeable day per direction."""
    days = split_days(bars)
    ordered = sorted(days)
    records = []
    # Most recent qualifying session's pre-market levels, for the fallback rule.
    last_good = None

    for date in ordered:
        day = days[date]
        reg = day["reg"]
        if len(reg) < 12:
            continue
        pmh, pml = premarket_extent(day)
        own_range = ((pmh - pml) / pml * 100.0) if (pmh and pml) else None

        if own_range is not None and own_range <= RANGE_FILTER_PCT:
            levels_pmh, levels_pml, source = pmh, pml, "today"
            last_good = (pmh, pml)
        elif use_fallback and last_good:
            levels_pmh, levels_pml, source = last_good[0], last_good[1], "fallback"
        else:
            continue
        unit = levels_pmh - levels_pml
        if unit <= 0:
            continue

        # Anchor: opening candle's extreme wick, unless it is a massive overextension.
        first = reg[0]
        anchor_idx = 0
        if (first["h"] - first["l"]) > OVEREXTENSION_FRAC * unit and len(reg) > 1:
            anchor_idx = 1
        anchor_bar = reg[anchor_idx]

        for direction in ("long", "short"):
            anchor = anchor_bar["l"] if direction == "long" else anchor_bar["h"]
            sign = 1 if direction == "long" else -1
            lv = {k: anchor + sign * k * unit for k in (0.5, 1.0, 2.0)}
            after = reg[anchor_idx + 1:]
            if len(after) < 4:
                continue

            def clean_break(level, bars_):
                """Index of the first candle whose BODY closes past `level`."""
                for i, b in enumerate(bars_):
                    body_hi, body_lo = max(b["o"], b["c"]), min(b["o"], b["c"])
                    if direction == "long" and body_lo > level:
                        return i
                    if direction == "short" and body_hi < level:
                        return i
                return None

            def touched(level, bars_):
                for b in bars_:
                    if direction == "long" and b["h"] >= level:
                        return True
                    if direction == "short" and b["l"] <= level:
                        return True
                return False

            def resolve(entry_price, target, stop, bars_):
                """Target-vs-stop with the stop assumed first on an ambiguous bar."""
                for b in bars_:
                    hit_stop = b["l"] <= stop if direction == "long" else b["h"] >= stop
                    hit_tgt = b["h"] >= target if direction == "long" else b["l"] <= target
                    if hit_stop:
                        return "stop", (stop - entry_price) * sign / entry_price * 100
                    if hit_tgt:
                        return "target", (target - entry_price) * sign / entry_price * 100
                last = bars_[-1]["c"]
                return "close", (last - entry_price) * sign / entry_price * 100

            # ── continuation: break of 0.5, target 1 ────────────────────────
            i_half = clean_break(lv[0.5], after)
            if i_half is not None:
                rest = after[i_half + 1:]
                if rest:
                    entry = after[i_half]["c"]
                    outcome, ret = resolve(entry, lv[1.0], anchor, rest)
                    records.append({
                        "symbol": symbol, "date": date.isoformat(), "direction": direction,
                        "leg": "0.5->1", "source": source, "pm_range": own_range,
                        "touched": touched(lv[1.0], rest),
                        "outcome": outcome, "return_pct": ret,
                    })

            # ── extension: break of 1, target 2 ─────────────────────────────
            i_one = clean_break(lv[1.0], after)
            if i_one is not None:
                rest = after[i_one + 1:]
                if rest:
                    entry = after[i_one]["c"]
                    outcome, ret = resolve(entry, lv[2.0], lv[0.5], rest)
                    records.append({
                        "symbol": symbol, "date": date.isoformat(), "direction": direction,
                        "leg": "1->2", "source": source, "pm_range": own_range,
                        "touched": touched(lv[2.0], rest),
                        "outcome": outcome, "return_pct": ret,
                    })
    return records


def report(records, label):
    print(f"\n=== {label} ===")
    if not records:
        print("  no signals")
        return
    by_leg = defaultdict(list)
    for r in records:
        by_leg[r["leg"]].append(r)
    print(f"  {'leg':<8}{'n':>6}{'touch%':>9}{'win%':>8}{'avg%':>9}{'med%':>9}")
    for leg in ("0.5->1", "1->2"):
        rows = by_leg.get(leg) or []
        if not rows:
            continue
        touch = 100.0 * sum(1 for r in rows if r["touched"]) / len(rows)
        wins = sum(1 for r in rows if r["outcome"] == "target")
        rets = sorted(r["return_pct"] for r in rows)
        avg = sum(rets) / len(rets)
        med = rets[len(rets) // 2]
        print(f"  {leg:<8}{len(rows):>6}{touch:>8.1f}%{100.0*wins/len(rows):>7.1f}%{avg:>8.3f}%{med:>8.3f}%")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="NVDA,AAPL,SPY,QQQ,TSLA,AMD,META,MSFT,AMZN,GOOGL")
    ap.add_argument("--days", type=int, default=180)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--json-out", default="")
    args = ap.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    everything = []
    for sym in symbols:
        bars = load_5m(sym, args.days, refresh=args.refresh)
        if not bars:
            print(f"{sym}: no bars", file=sys.stderr)
            continue
        recs = evaluate(sym, bars)
        everything.extend(recs)
        print(f"{sym}: {len(bars)} bars -> {len(recs)} signals")

    report(everything, "ALL SIGNALS")
    report([r for r in everything if r["source"] == "today"],
           "TRENDING DAYS ONLY (pre-market range <= 1.5%, no fallback)")
    report([r for r in everything if r["source"] == "fallback"],
           "CHOPPY DAYS using the fallback session's levels")

    print("\nPublished claims: 98% for '1' from a 0.5 cross, 64% for '2' from a 1.0 cross.")
    print("touch% is the charitable reading (did price ever reach the level before the")
    print("close). win% is the same signal traded with a stop, which is what a position")
    print("would actually have returned.")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(everything, indent=2))
        print(f"\nwrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
