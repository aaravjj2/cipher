"""Structural Fib, tested with a trailed anchor and the reversal setup separated out.

Research-only. Reads Alpaca SIP 1-minute bars (extended hours included) from a local
sqlite mirror, resamples to 5 minutes in exchange local time, and measures the
strategy's own published hit rates. No orders, no broker calls, no live data.

## Why this exists when `scripts/backtest_structural_fib.py` already ran

That script tested the strategy with the anchor **fixed** to the opening 5-minute
candle for the whole session. The method as taught trails the anchor: "you just
trail it", "keep moving up my anchor to follow the high", and every worked example
re-anchors on the most recent swing extreme. A static anchor is a different
strategy, and it is not the one whose hit rates are claimed.

Second, and more consequentially, the earlier run reported a single `1->2` leg. The
method treats two distinct setups that both trigger at the 1 level:

  * **continuation** — anchor on the recent extreme in the direction of travel,
    entry on a body close past 0.5, target 1. Taught as an early-session trade.
  * **reversal** — anchor on the trailed *high of day* after the session has pushed
    up and rolled over, entry only on a body close below 1 (the method explicitly
    forbids the 0.5 entry here), target 2. This is the headline claim: "only 2% of
    the days did it go through one and not end up hitting two."

Folding those together measures neither. The 98% reversal claim is the one the
strategy is actually sold on, and it had never been tested.

## The claims, as stated

    continuation 0.5 -> 1     95-98%
    continuation 1   -> 2     63-64%
    reversal     1   -> 2     98%      (bearish, from the high of day)
    extension    2   -> 3     17-18%
    pre-market range <= 1.5%  selects trending days ~90% of the time

## What a hit rate here is and is not

`touch_rate` is the claimed quantity: did price reach the level at any point before
the close, with no stop and the whole session to wait. It is a statement about the
price path and needs no execution-cost model, which is why it can be measured
cleanly. It is also not a win rate — a level reached after trading through the stop
is not a trade that made money.

`race_rate` is the same signal as a position: first touch of target or stop wins,
with the stop assumed first on a bar that spans both. The gap between the two
readings is the whole point, and it is where the earlier run's finding lived — a
74.6% win rate alongside a negative average return.

Neither reading includes option cost. The method trades 0DTE/1DTE contracts, and
the measured option half-spread for names of this liquidity class is large enough
to matter (see `core/execution_calibration.py`). A price level being reached is a
necessary condition for the option trade to work, not a sufficient one, so the
cost basis here is recorded as not-applicable rather than assumed.
"""
from __future__ import annotations

import json
import math
import random
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, time as dtime
from pathlib import Path
from typing import Sequence

# Re-exported so callers and tests keep one import site for the whole study.
from core.structural_fib_bars import (  # noqa: F401
    NY, PRE_OPEN, REG_CLOSE, REG_OPEN, UTC, Bar, _race, _touched,
    load_minute_bars, resample_5m, split_sessions,
)

CORE = Path(__file__).resolve().parent
CIPHER_ROOT = CORE.parent

SYMBOLS = ("NVDA", "AAPL")
RANGE_FILTER_PCT = 1.5
OVEREXTENSION_FRAC = 0.5
# The method allows continuation trades from just after the open until late
# morning, and treats the afternoon as reversal-only.
CONTINUATION_CUTOFF = dtime(11, 30)
ANCHOR_GRACE_BARS = 2  # ~10 minutes after the open before a continuation may fire.
# A reversal is "from the high of day", so the day has to have gone up first.
REVERSAL_MIN_ADVANCE_R = 0.5
MIN_REPORT_N = 12

CLAIMED = {
    "continuation 0.5->1": 0.965,
    "continuation 1->2": 0.635,
    "reversal 1->2": 0.98,
    "extension 2->3": 0.175,
}


# ────────────────────────────────────────────────────────────── signals

@dataclass(slots=True)
class Signal:
    symbol: str
    day: str
    setup: str          # "continuation" | "reversal"
    leg: str            # "0.5->1" | "1->2" | "2->3"
    direction: str      # "long" | "short"
    regime: str         # "trending" | "choppy"
    leg_source: str     # "today" | "fallback"
    pm_range_pct: float | None
    entry_time: str
    entry_price: float
    target: float
    stop: float
    touched: bool       # reached target before the close, ignoring the stop
    race: str           # "target" | "stop" | "close"
    return_pct: float   # outcome of the raced position, price only


@dataclass(slots=True)
class Trigger:
    """A fired entry, with everything decidable at the signal bar's close and nothing else.

    Deliberately carries no outcome. The forward recorder writes exactly this to disk the
    moment a signal fires, and the outcome is scored strictly later from a separate pass, so
    a pre-registered signal cannot acquire its result at detection time.
    """
    index: int
    at: datetime
    setup: str
    leg: str
    direction: str
    level: float        # the trigger price
    entry_price: float  # what the active entry mode actually fills at
    target: float
    stop: float
    anchor: float


def iter_triggers(reg: Sequence[Bar], unit: float, *,
                  min_advance_r: float = REVERSAL_MIN_ADVANCE_R,
                  entry_mode: str = "confirmed") -> list[Trigger]:
    """Every trigger in a session, with the anchor trailed bar by bar.

    The anchor is the running session extreme *including* the current bar, so every decision
    uses only information available at that bar's close. A bar that makes a new extreme and
    then closes hard against it moves the level away from price, which makes entry harder
    rather than easier — the conservative direction.

    Shared by the backtest and the forward recorder on purpose. Two implementations of this
    geometry would make the forward-versus-backtest comparison meaningless, because any
    difference in rates could be the geometry rather than the market.
    """
    if len(reg) < 2 or unit <= 0:
        return []
    out: list[Trigger] = []
    session_open = reg[0].o

    # A "massive overextension" on the opening candle disqualifies it as an anchor.
    start = 1 if (reg[0].h - reg[0].l) > OVEREXTENSION_FRAC * unit else 0
    running_low = reg[start].l
    running_high = reg[start].h
    fired: set[tuple[str, str, str]] = set()

    for i in range(start, len(reg)):
        bar = reg[i]
        running_low = min(running_low, bar.l)
        running_high = max(running_high, bar.h)
        early = bar.t.time() < CONTINUATION_CUTOFF and i >= start + ANCHOR_GRACE_BARS

        def crossed(level: float, direction: str) -> bool:
            if entry_mode == "confirmed":
                return bar.body_lo > level if direction == "long" else bar.body_hi < level
            return bar.h >= level if direction == "long" else bar.l <= level

        def fire(setup: str, leg: str, direction: str,
                 level: float, target: float, stop: float, anchor: float) -> None:
            key = (setup, leg, direction)
            if key in fired or not crossed(level, direction):
                return
            fired.add(key)
            out.append(Trigger(
                index=i, at=bar.t, setup=setup, leg=leg, direction=direction,
                level=level, entry_price=bar.c if entry_mode == "confirmed" else level,
                target=target, stop=stop, anchor=anchor,
            ))

        if early:
            fire("continuation", "0.5->1", "long", running_low + 0.5 * unit,
                 running_low + 1.0 * unit, running_low, running_low)
            fire("continuation", "1->2", "long", running_low + 1.0 * unit,
                 running_low + 2.0 * unit, running_low + 0.5 * unit, running_low)
            fire("continuation", "0.5->1", "short", running_high - 0.5 * unit,
                 running_high - 1.0 * unit, running_high, running_high)
            fire("continuation", "1->2", "short", running_high - 1.0 * unit,
                 running_high - 2.0 * unit, running_high - 0.5 * unit, running_high)

        # A reversal is "from the high of day", so the day has to have advanced first.
        if running_high - session_open >= min_advance_r * unit:
            fire("reversal", "1->2", "short", running_high - 1.0 * unit,
                 running_high - 2.0 * unit, running_high - 0.5 * unit, running_high)
            fire("reversal", "2->3", "short", running_high - 2.0 * unit,
                 running_high - 3.0 * unit, running_high - 1.5 * unit, running_high)
        # Bullish, which the method itself calls the weaker side.
        if session_open - running_low >= min_advance_r * unit:
            fire("reversal", "1->2", "long", running_low + 1.0 * unit,
                 running_low + 2.0 * unit, running_low + 0.5 * unit, running_low)
            fire("reversal", "2->3", "long", running_low + 2.0 * unit,
                 running_low + 3.0 * unit, running_low + 1.5 * unit, running_low)
    return out


def evaluate_day(symbol: str, day: date, reg: Sequence[Bar], unit: float,
                 regime: str, leg_source: str, pm_range_pct: float | None,
                 *, min_advance_r: float = REVERSAL_MIN_ADVANCE_R,
                 entry_mode: str = "confirmed") -> list[Signal]:
    """Scored signals for one complete session, built on `iter_triggers`."""
    if len(reg) < 8 or unit <= 0:
        return []
    out: list[Signal] = []
    for trigger in iter_triggers(reg, unit, min_advance_r=min_advance_r,
                                 entry_mode=entry_mode):
        rest = reg[trigger.index + 1:]
        if not rest:
            # No bar left to resolve against; an unresolvable signal is dropped rather than
            # scored at its entry price, which would inject a fake zero-return outcome.
            continue
        race, ret = _race(trigger.entry_price, trigger.target, trigger.stop,
                          rest, trigger.direction)
        out.append(Signal(
            symbol=symbol, day=day.isoformat(), setup=trigger.setup, leg=trigger.leg,
            direction=trigger.direction, regime=regime, leg_source=leg_source,
            pm_range_pct=pm_range_pct, entry_time=trigger.at.strftime("%H:%M"),
            entry_price=trigger.entry_price, target=trigger.target, stop=trigger.stop,
            touched=_touched(trigger.target, rest, trigger.direction),
            race=race, return_pct=ret,
        ))
    return out


# ────────────────────────────────────────────────────────────── regime + driver

@dataclass(slots=True)
class DayContext:
    day: date
    pm_high: float | None
    pm_low: float | None
    pm_range_pct: float | None
    regime: str
    unit: float | None
    leg_source: str
    trended: bool | None = None   # did the regular session actually trend?


def classify_days(sessions: dict[date, dict[str, list[Bar]]]) -> list[DayContext]:
    """Pre-market regime per day, with the fallback leg carried forward.

    The method's own rule: at or under 1.5% the day is expected to trend and its own
    pre-market range is the fib unit; wider, and you fall back to the most recent
    session that did qualify and use that one's range.
    """
    out: list[DayContext] = []
    last_good: float | None = None
    for day in sorted(sessions):
        pre = sessions[day]["pre"]
        pm_high = max((b.h for b in pre), default=None)
        pm_low = min((b.l for b in pre), default=None)
        rng = ((pm_high - pm_low) / pm_low * 100.0) if (pm_high and pm_low) else None
        if rng is not None and rng <= RANGE_FILTER_PCT and pm_high and pm_low:
            unit, source, regime = pm_high - pm_low, "today", "trending"
            last_good = unit
        elif last_good:
            unit, source = last_good, "fallback"
            regime = "choppy" if rng is not None else "unknown"
        else:
            unit, source = None, "none"
            regime = "choppy" if rng is not None else "unknown"
        out.append(DayContext(day, pm_high, pm_low, rng, regime, unit, source))
    return out


def measure_trend(reg: Sequence[Bar]) -> bool | None:
    """Did the regular session trend, judged without reference to the strategy?

    A day is called trending when its close sits in the outer third of its own
    session range — price went somewhere and stayed there. A day that ends mid-range
    travelled and came back, which is the definition of chop. This is deliberately
    independent of the fib levels so the 1.5% filter can be scored against
    something it did not help construct.
    """
    if len(reg) < 8:
        return None
    high = max(b.h for b in reg)
    low = min(b.l for b in reg)
    if high <= low:
        return None
    close_pos = (reg[-1].c - low) / (high - low)
    return close_pos >= 2 / 3 or close_pos <= 1 / 3


# ────────────────────────────────────────────────────────────── statistics

def wilson(hits: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval. Normal approximation fails at the rates in play here."""
    if n == 0:
        return (0.0, 0.0)
    p = hits / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def summarise(signals: Sequence[Signal], key: str) -> dict:
    n = len(signals)
    if not n:
        return {"n": 0}
    touch = sum(1 for s in signals if s.touched)
    won = sum(1 for s in signals if s.race == "target")
    rets = sorted(s.return_pct for s in signals)
    lo, hi = wilson(touch, n)
    claimed = CLAIMED.get(key)
    return {
        "n": n,
        "touch_rate": touch / n,
        "touch_ci95": [lo, hi],
        "claimed": claimed,
        # The claim is refuted when it sits outside the interval the data supports.
        "claim_excluded": (None if claimed is None else not (lo <= claimed <= hi)),
        "race_win_rate": won / n,
        "avg_return_pct": sum(rets) / n,
        "median_return_pct": rets[n // 2],
        "underpowered": n < MIN_REPORT_N,
    }


def matched_control(signals: Sequence[Signal],
                    sessions_by_symbol: dict[str, dict[date, dict[str, list[Bar]]]],
                    *, seed: int = 20260813, replicates: int = 20) -> dict:
    """The same geometry, entered at random times. The discipline every other
    strategy in this codebase has to clear.

    For each real signal, an entry bar is drawn uniformly from the same session and
    the *same absolute target and stop distances* are applied in the same direction.
    Everything is held constant except the one thing under test: whether the
    Fibonacci level carried information about where price would go.

    If the control reproduces the strategy's hit rates, then the rates are a
    property of the bar geometry — how far the target sits versus the stop — and not
    evidence that the levels predict anything.
    """
    rng = random.Random(seed)
    tallies: dict[str, dict[str, float]] = defaultdict(
        lambda: {"n": 0, "touch": 0, "target": 0, "ret": 0.0}
    )
    for sig in signals:
        reg = sessions_by_symbol.get(sig.symbol, {}).get(
            date.fromisoformat(sig.day), {}).get("reg", [])
        if len(reg) < 8:
            continue
        reward = abs(sig.target - sig.entry_price)
        risk = abs(sig.stop - sig.entry_price)
        sign = 1.0 if sig.direction == "long" else -1.0
        key = f"{sig.setup} {sig.leg}"
        for _ in range(replicates):
            i = rng.randrange(0, len(reg) - 1)
            entry = reg[i].c
            rest = reg[i + 1:]
            target = entry + sign * reward
            stop = entry - sign * risk
            race, ret = _race(entry, target, stop, rest, sig.direction)
            t = tallies[key]
            t["n"] += 1
            t["touch"] += int(_touched(target, rest, sig.direction))
            t["target"] += int(race == "target")
            t["ret"] += ret
    out = {}
    for key, t in sorted(tallies.items()):
        n = int(t["n"])
        if not n:
            continue
        out[key] = {
            "n": n,
            "touch_rate": t["touch"] / n,
            "race_win_rate": t["target"] / n,
            "avg_return_pct": t["ret"] / n,
            "replicates": replicates,
        }
    return out


def run(db_path: Path, symbols: Sequence[str] = SYMBOLS,
        *, min_advance_r: float = REVERSAL_MIN_ADVANCE_R,
        entry_mode: str = "confirmed") -> dict:
    """Measure every claim on every symbol. Returns a report payload."""
    signals: list[Signal] = []
    sessions_by_symbol: dict[str, dict[date, dict[str, list[Bar]]]] = {}
    coverage: dict[str, dict] = {}
    regime_scoreboard = {"trending": {"trended": 0, "n": 0}, "choppy": {"trended": 0, "n": 0}}

    for symbol in symbols:
        minute = load_minute_bars(db_path, symbol)
        if not minute:
            coverage[symbol] = {"days": 0, "note": "no bars"}
            continue
        sessions = split_sessions(resample_5m(minute))
        sessions_by_symbol[symbol] = sessions
        contexts = classify_days(sessions)
        traded = 0
        for ctx in contexts:
            reg = sessions[ctx.day]["reg"]
            trended = measure_trend(reg)
            ctx.trended = trended
            if trended is not None and ctx.regime in regime_scoreboard:
                regime_scoreboard[ctx.regime]["n"] += 1
                regime_scoreboard[ctx.regime]["trended"] += int(trended)
            if ctx.unit is None:
                continue
            day_signals = evaluate_day(
                symbol, ctx.day, reg, ctx.unit, ctx.regime, ctx.leg_source,
                ctx.pm_range_pct, min_advance_r=min_advance_r,
                entry_mode=entry_mode,
            )
            if day_signals:
                traded += 1
            signals.extend(day_signals)
        days = [c for c in contexts if sessions[c.day]["reg"]]
        coverage[symbol] = {
            "days": len(days),
            "days_with_premarket": sum(1 for c in days if c.pm_range_pct is not None),
            "trending_days": sum(1 for c in days if c.regime == "trending"),
            "choppy_days": sum(1 for c in days if c.regime == "choppy"),
            "days_with_signals": traded,
            "start": days[0].day.isoformat() if days else None,
            "end": days[-1].day.isoformat() if days else None,
        }

    def bucket(pred) -> dict:
        groups: dict[str, list[Signal]] = defaultdict(list)
        for s in signals:
            if pred(s):
                groups[f"{s.setup} {s.leg}"].append(s)
        return {k: summarise(v, k) for k, v in sorted(groups.items())}

    # The filter's own claim: does <=1.5% pre-market actually select trending days?
    filter_check = {}
    for regime, tally in regime_scoreboard.items():
        if tally["n"]:
            lo, hi = wilson(tally["trended"], tally["n"])
            filter_check[regime] = {
                "n": tally["n"], "trended": tally["trended"],
                "rate": tally["trended"] / tally["n"], "ci95": [lo, hi],
            }

    return {
        "study": "structural_fib_faithful",
        "generated_at": datetime.now(UTC).isoformat(),
        "source_db": str(db_path),
        "params": {
            "range_filter_pct": RANGE_FILTER_PCT,
            "overextension_frac": OVEREXTENSION_FRAC,
            "continuation_cutoff_et": CONTINUATION_CUTOFF.strftime("%H:%M"),
            "anchor_grace_bars": ANCHOR_GRACE_BARS,
            "reversal_min_advance_r": min_advance_r,
            "entry_mode": entry_mode,
            "anchor": "trailed session extreme, inclusive of the signal bar",
            "ambiguous_bar": "scored as the stop",
        },
        "coverage": coverage,
        "premarket_filter_check": filter_check,
        "claimed_rates": CLAIMED,
        "overall": bucket(lambda s: True),
        "matched_random_entry_control": matched_control(signals, sessions_by_symbol),
        "by_direction": {
            d: bucket(lambda s, d=d: s.direction == d) for d in ("short", "long")
        },
        "by_symbol": {
            sym: bucket(lambda s, sym=sym: s.symbol == sym) for sym in symbols
        },
        "by_regime": {
            r: bucket(lambda s, r=r: s.regime == r) for r in ("trending", "choppy")
        },
        "cost_basis": "not-applicable:price-level-claim",
        "limitations": [
            "Touch and race rates are price-path facts about the underlying. The method "
            "trades 0DTE/1DTE options, whose spread and theta are not modelled here, so a "
            "level being reached is necessary but not sufficient for the trade to pay.",
            "Five-minute bars carry no intrabar sequence; bars spanning target and stop are "
            "scored as the stop.",
            "'Clean break' and 'strength' are discretionary in the source method and are "
            "encoded here as a body close past the level. A more permissive reading would "
            "fire more signals of lower quality.",
            "At most one signal per setup/leg/direction per day, so a day is counted once "
            "rather than compounding repeated re-entries the method also allows.",
        ],
        "signals": [asdict(s) for s in signals],
    }
