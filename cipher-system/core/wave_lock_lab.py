"""Wave Lock v6 (QQQ 1-minute), measured on real bars.

Research-only. Reads a local sqlite mirror of Alpaca SIP 1-minute bars. No orders, no broker
calls.

## What this strategy is, and how it differs from Structural Fib

Both use the pre-market range as the unit R and project 0.5R / 1R / 2R. Everything else is
different, and the differences all point the same way — this one is more carefully specified:

  * **The anchor is a swing pivot**, not a trailed session extreme. A pivot is a real
    structural point confirmed by `rightbars` lower highs after it.
  * **The anchor is a stop.** A setup is dead the moment price trades back through its pivot.
    Structural Fib had no such rule, and its anchor moved as price moved.
  * **Pivots must sit near the pre-market range** (inside it, or within 25% of R beyond
    PMH/PML). That is a real location filter, not a day-type filter.
  * **Noise gates**: swing span >= 0.50 ATR(14) and reversal body >= 0.15 ATR(14).
  * Two sensitivities: EARLY (3 left / 1 right, plus a reversal trigger) is the actionable
    signal; VALIDATED (3/3, confirmed once price reaches 0.5R) is confirmation.

## What the indicator cannot measure about itself, and this module adds

1. **Same-bar chains.** The Pine registers a setup and checks its targets on the same bar, so
   one bar can register, confirm, and record a 2.0R hit, at zero bars elapsed. Measured both
   ways here via `include_signal_bar`.
2. **P(confirm | pivot) for VALIDATED.** The Pine replaces the displayed wave when a newer
   pivot qualifies, so an unconfirmed wave vanishes with no record and the denominator for
   "how often does a pivot confirm" does not exist. Every qualified pivot is recorded here.
3. **Whether any of it beats chance.** A touch rate is not an edge. Reported alongside a
   barrier race (target versus the anchor stop, ambiguous bars scored as the stop), the
   resulting expectancy, and a matched random-entry control holding the geometry constant.

The published statistics are touch-based by the indicator's own admission ("not P&L, slippage,
option premium, stop-loss, or realized trade results"). This module keeps that reading and puts
the tradeable one beside it.
"""
from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Sequence

from core.structural_fib_bars import (  # noqa: F401
    NY, PRE_OPEN, REG_CLOSE, REG_OPEN, UTC, Bar, _race, _touched,
    load_minute_bars, split_sessions,
)
from core.structural_fib_lab import wilson

SYMBOLS = ("QQQ",)

#: The indicator's own v6 defaults, which are the QQQ 1-minute tuning target.
PARAMS = {
    "early_left": 3,
    "early_right": 1,
    "validated_pivot": 3,
    "pm_proximity_pct": 25.0,
    "require_rth_pivot": True,
    "early_trigger": "prior_close",     # pivot_only | prior_close | prior_high_low
    "early_reversal_buffer_atr": 0.0,
    "early_min_swing_atr": 0.50,
    "early_min_body_atr": 0.15,
    "atr_length": 14,
    "suppress_repeat_same_direction": True,
    "cooldown_bars": 0,
    "include_signal_bar": True,         # the Pine's behaviour; False is the honest reading
}

MIN_REPORT_N = 12


# ───────────────────────────────────────────────────────────── indicators

def atr(bars: Sequence[Bar], length: int) -> list[float | None]:
    """Wilder ATR, matching Pine's `ta.atr` (RMA of true range, not SMA)."""
    out: list[float | None] = []
    prev_close: float | None = None
    running: float | None = None
    trs: list[float] = []
    for bar in bars:
        if prev_close is None:
            true_range = bar.h - bar.l
        else:
            true_range = max(bar.h - bar.l, abs(bar.h - prev_close), abs(bar.l - prev_close))
        prev_close = bar.c
        trs.append(true_range)
        if len(trs) < length:
            out.append(None)
        elif len(trs) == length:
            running = sum(trs) / length
            out.append(running)
        else:
            running = (running * (length - 1) + true_range) / length  # type: ignore[operator]
            out.append(running)
    return out


def pivots(bars: Sequence[Bar], left: int, right: int, kind: str) -> dict[int, tuple[int, float]]:
    """{confirmation_bar_index: (pivot_bar_index, pivot_price)}.

    A pivot high needs `left` lower highs before it and `right` lower highs after, so it is
    only knowable `right` bars later — which is the index it is reported at, exactly as Pine
    reports `ta.pivothigh` on the confirming bar. Strict comparison on both sides; on
    1-minute bars equal highs are common enough that admitting ties would inflate the count
    with pivots that are not turning points.
    """
    found: dict[int, tuple[int, float]] = {}
    for i in range(left, len(bars) - right):
        if kind == "high":
            value = bars[i].h
            if all(bars[j].h < value for j in range(i - left, i)) and \
               all(bars[j].h < value for j in range(i + 1, i + right + 1)):
                found[i + right] = (i, value)
        else:
            value = bars[i].l
            if all(bars[j].l > value for j in range(i - left, i)) and \
               all(bars[j].l > value for j in range(i + 1, i + right + 1)):
                found[i + right] = (i, value)
    return found


# ───────────────────────────────────────────────────────────── day context

@dataclass(slots=True)
class DayContext:
    day: date
    pm_high: float | None
    pm_low: float | None
    pm_range: float | None
    permitted_low: float | None
    permitted_high: float | None


def day_context(pre: Sequence[Bar], *, proximity_pct: float) -> DayContext | None:
    if not pre:
        return None
    pm_high = max(b.h for b in pre)
    pm_low = min(b.l for b in pre)
    if pm_high <= pm_low:
        return None
    unit = pm_high - pm_low
    slack = unit * proximity_pct / 100.0
    return DayContext(pre[0].t.date(), pm_high, pm_low, unit, pm_low - slack, pm_high + slack)


# ───────────────────────────────────────────────────────────── signals

@dataclass(slots=True)
class Setup:
    symbol: str
    day: str
    engine: str          # "early" | "validated"
    direction: str       # "long" | "short"
    signal_index: int
    signal_time: str
    anchor: float        # the pivot, which is also the stop
    entry_price: float
    pm_range: float
    t05: float
    t10: float
    t20: float
    # outcomes, filled by tracking
    hit05: bool = False
    hit10: bool = False
    hit20: bool = False
    failed_before_05: bool = False
    bars_to_05: int | None = None
    bars_to_10: int | None = None
    # Unconditional touches, ignoring the anchor stop entirely. Needed because the tracked
    # rates above stop counting once a setup fails, and a random-entry control has no anchor
    # to fail against -- comparing the two directly would credit the control with paths the
    # strategy was never allowed to finish.
    touch_05_uncond: bool = False
    touch_10_uncond: bool = False
    # the tradeable reading the indicator does not compute
    race: str = "open"
    return_pct: float = 0.0


def _pm_ok(price: float, ctx: DayContext) -> bool:
    return (ctx.permitted_low is not None and ctx.permitted_high is not None
            and ctx.permitted_low <= price <= ctx.permitted_high)


def early_setups(symbol: str, ctx: DayContext, reg: Sequence[Bar],
                 atrs: Sequence[float | None], params: dict) -> list[Setup]:
    """EARLY signals: a fast pivot plus a reversal candle that clears the noise gates."""
    left, right = int(params["early_left"]), int(params["early_right"])
    highs = pivots(reg, left, right, "high")
    lows = pivots(reg, left, right, "low")
    window = left + right + 1
    out: list[Setup] = []
    last_direction = 0
    last_signal_index: int | None = None

    for i, bar in enumerate(reg):
        a = atrs[i]
        if a is None or a <= 0:
            continue
        # Swing span over [pivot-left, pivot+right], which is the `window` bars ending here.
        if i + 1 < window:
            continue
        span_bars = reg[i + 1 - window:i + 1]
        span = max(b.h for b in span_bars) - min(b.l for b in span_bars)
        if span < float(params["early_min_swing_atr"]) * a:
            continue
        if abs(bar.c - bar.o) < float(params["early_min_body_atr"]) * a:
            continue
        cooldown = int(params["cooldown_bars"])
        if cooldown > 0 and last_signal_index is not None and i - last_signal_index < cooldown:
            continue
        buffer = float(params["early_reversal_buffer_atr"]) * a
        prior = reg[i - 1] if i else None
        if prior is None:
            continue

        for kind, direction, table in (("high", "short", highs), ("low", "long", lows)):
            hit = table.get(i)
            if hit is None:
                continue
            pivot_index, anchor = hit
            if not _pm_ok(anchor, ctx):
                continue
            if params["require_rth_pivot"] and not (
                    REG_OPEN <= reg[pivot_index].t.time() < REG_CLOSE):
                continue
            mode = params["early_trigger"]
            if mode == "prior_close":
                triggered = (bar.c < prior.c - buffer) if direction == "short" \
                    else (bar.c > prior.c + buffer)
            elif mode == "prior_high_low":
                triggered = (bar.c < prior.l - buffer) if direction == "short" \
                    else (bar.c > prior.h + buffer)
            else:
                triggered = True
            if not triggered:
                continue
            if params["suppress_repeat_same_direction"]:
                this_direction = -1 if direction == "short" else 1
                if last_direction == this_direction:
                    continue
                last_direction = this_direction
            last_signal_index = i
            sign = -1.0 if direction == "short" else 1.0
            unit = ctx.pm_range or 0.0
            out.append(Setup(
                symbol=symbol, day=ctx.day.isoformat(), engine="early", direction=direction,
                signal_index=i, signal_time=bar.t.strftime("%H:%M"), anchor=anchor,
                entry_price=bar.c, pm_range=unit,
                t05=anchor + sign * 0.5 * unit,
                t10=anchor + sign * 1.0 * unit,
                t20=anchor + sign * 2.0 * unit,
            ))
    return out


def validated_setups(symbol: str, ctx: DayContext, reg: Sequence[Bar],
                     params: dict) -> list[Setup]:
    """Every qualified VALIDATED pivot, recorded whether or not it goes on to confirm.

    The indicator keeps one displayed wave and drops an unconfirmed one when a newer pivot
    qualifies, which makes "how often does a pivot reach 0.5R" unanswerable from its own
    output. Recording all of them is the only way to get that denominator.
    """
    length = int(params["validated_pivot"])
    highs = pivots(reg, length, length, "high")
    lows = pivots(reg, length, length, "low")
    out: list[Setup] = []
    for i, bar in enumerate(reg):
        for direction, table in (("short", highs), ("long", lows)):
            hit = table.get(i)
            if hit is None:
                continue
            pivot_index, anchor = hit
            if not _pm_ok(anchor, ctx):
                continue
            if params["require_rth_pivot"] and not (
                    REG_OPEN <= reg[pivot_index].t.time() < REG_CLOSE):
                continue
            sign = -1.0 if direction == "short" else 1.0
            unit = ctx.pm_range or 0.0
            out.append(Setup(
                symbol=symbol, day=ctx.day.isoformat(), engine="validated",
                direction=direction, signal_index=i, signal_time=bar.t.strftime("%H:%M"),
                anchor=anchor, entry_price=bar.c, pm_range=unit,
                t05=anchor + sign * 0.5 * unit,
                t10=anchor + sign * 1.0 * unit,
                t20=anchor + sign * 2.0 * unit,
            ))
    return out


def track(setups: Sequence[Setup], reg: Sequence[Bar], *, include_signal_bar: bool) -> None:
    """Resolve each setup forward, in place.

    `include_signal_bar` reproduces the indicator, which checks targets on the bar that
    created the setup. That permits a register-confirm-target chain inside one bar at zero
    elapsed bars. False starts from the next bar, which is what a position could actually do.
    """
    for setup in setups:
        start = setup.signal_index if include_signal_bar else setup.signal_index + 1
        forward = reg[start:]
        if not forward:
            continue
        long_side = setup.direction == "long"
        for offset, bar in enumerate(forward):
            through_anchor = bar.l < setup.anchor if long_side else bar.h > setup.anchor
            if not setup.hit05 and through_anchor:
                setup.failed_before_05 = True
                break
            reached = (lambda level: bar.h >= level) if long_side else (lambda level: bar.l <= level)
            if not setup.hit05 and reached(setup.t05):
                setup.hit05 = True
                setup.bars_to_05 = start + offset - setup.signal_index
            if setup.hit05 and not setup.hit10 and reached(setup.t10):
                setup.hit10 = True
                setup.bars_to_10 = start + offset - setup.signal_index
            if setup.hit10 and not setup.hit20 and reached(setup.t20):
                setup.hit20 = True
                break
        # Unconditional touches over the same forward window, no stop applied.
        setup.touch_05_uncond = _touched(setup.t05, forward, setup.direction)
        setup.touch_10_uncond = _touched(setup.t10, forward, setup.direction)
        # The tradeable reading: 1.0R target against the anchor stop, from the entry price.
        race_bars = reg[setup.signal_index + 1:]
        if race_bars:
            setup.race, setup.return_pct = _race(
                setup.entry_price, setup.t10, setup.anchor, race_bars, setup.direction)


# ───────────────────────────────────────────────────────────── control

def matched_control(setups: Sequence[Setup], sessions: dict[date, list[Bar]], *,
                    seed: int = 20260813, replicates: int = 20) -> dict:
    """Same reward and risk distances, random entry time in the same session.

    Isolates the one thing under test: whether a pivot near the pre-market range predicted
    where price would go, or whether the hit rates are the geometry of a 1R target against a
    nearer stop.
    """
    rng = random.Random(seed)
    tally: dict[str, dict[str, float]] = defaultdict(
        lambda: {"n": 0, "hit10": 0, "target": 0, "ret": 0.0})
    for setup in setups:
        reg = sessions.get(date.fromisoformat(setup.day)) or []
        if len(reg) < 30:
            continue
        reward = abs(setup.t10 - setup.entry_price)
        risk = abs(setup.anchor - setup.entry_price)
        if risk <= 0:
            continue
        sign = 1.0 if setup.direction == "long" else -1.0
        row = tally[setup.engine]
        for _ in range(replicates):
            i = rng.randrange(0, len(reg) - 1)
            entry = reg[i].c
            forward = reg[i + 1:]
            target = entry + sign * reward
            stop = entry - sign * risk
            race, ret = _race(entry, target, stop, forward, setup.direction)
            row["n"] += 1
            row["hit10"] += int(_touched(target, forward, setup.direction))
            row["target"] += int(race == "target")
            row["ret"] += ret
    return {
        engine: {
            "n": int(r["n"]),
            "touch_1R": r["hit10"] / r["n"],
            "race_win_rate": r["target"] / r["n"],
            "avg_return_pct": r["ret"] / r["n"],
        }
        for engine, r in sorted(tally.items()) if r["n"]
    }


# ───────────────────────────────────────────────────────────── reporting

def summarise(setups: Sequence[Setup]) -> dict:
    n = len(setups)
    if not n:
        return {"n": 0}
    hit05 = sum(1 for s in setups if s.hit05)
    hit10 = sum(1 for s in setups if s.hit10)
    hit20 = sum(1 for s in setups if s.hit20)
    failed = sum(1 for s in setups if s.failed_before_05)
    raced = [s for s in setups if s.race in ("target", "stop", "close")]
    won = sum(1 for s in raced if s.race == "target")
    rets = sorted(s.return_pct for s in raced)
    b05 = [s.bars_to_05 for s in setups if s.bars_to_05 is not None]
    b10 = [s.bars_to_10 for s in setups if s.bars_to_10 is not None]
    same_bar_05 = sum(1 for s in setups if s.bars_to_05 == 0)
    return {
        "n": n,
        "reach_05_rate": hit05 / n,
        "reach_05_ci95": list(wilson(hit05, n)),
        "reach_10_rate": hit10 / n,
        "reach_20_rate": hit20 / n,
        "failure_rate": failed / n,
        # The indicator's headline conditional: given 0.5R, how often 1.0R?
        "reach_10_given_05": (hit10 / hit05) if hit05 else None,
        "reach_20_given_05": (hit20 / hit05) if hit05 else None,
        "avg_bars_to_05": (sum(b05) / len(b05)) if b05 else None,
        "avg_bars_to_10": (sum(b10) / len(b10)) if b10 else None,
        "same_bar_05_share": same_bar_05 / n,
        "touch_05_uncond_rate": sum(1 for s in setups if s.touch_05_uncond) / n,
        "touch_10_uncond_rate": sum(1 for s in setups if s.touch_10_uncond) / n,
        "race_n": len(raced),
        "race_win_rate": (won / len(raced)) if raced else None,
        "avg_return_pct": (sum(rets) / len(rets)) if rets else None,
        "median_return_pct": rets[len(rets) // 2] if rets else None,
        "avg_reward_pct": sum(abs(s.t10 - s.entry_price) / s.entry_price * 100
                              for s in setups) / n,
        "avg_risk_pct": sum(abs(s.anchor - s.entry_price) / s.entry_price * 100
                            for s in setups) / n,
        "underpowered": n < MIN_REPORT_N,
    }


def run(db_path: Path, symbols: Sequence[str] = SYMBOLS, *,
        params: dict | None = None) -> dict:
    active = dict(PARAMS, **(params or {}))
    all_setups: list[Setup] = []
    coverage: dict[str, dict] = {}
    sessions_by_symbol: dict[str, dict[date, list[Bar]]] = {}

    for symbol in symbols:
        minute = load_minute_bars(db_path, symbol)
        if not minute:
            coverage[symbol] = {"days": 0, "note": "no bars"}
            continue
        sessions = split_sessions(minute)
        reg_by_day: dict[date, list[Bar]] = {}
        days_used = 0
        for day in sorted(sessions):
            pre, reg = sessions[day]["pre"], sessions[day]["reg"]
            if len(reg) < 60:
                continue
            ctx = day_context(pre, proximity_pct=float(active["pm_proximity_pct"]))
            if ctx is None:
                continue
            ctx.day = day
            reg_by_day[day] = reg
            days_used += 1
            atrs = atr(reg, int(active["atr_length"]))
            early = early_setups(symbol, ctx, reg, atrs, active)
            validated = validated_setups(symbol, ctx, reg, active)
            track(early, reg, include_signal_bar=bool(active["include_signal_bar"]))
            track(validated, reg, include_signal_bar=bool(active["include_signal_bar"]))
            all_setups.extend(early)
            all_setups.extend(validated)
        sessions_by_symbol[symbol] = reg_by_day
        used = sorted(reg_by_day)
        coverage[symbol] = {
            "days": days_used,
            "start": used[0].isoformat() if used else None,
            "end": used[-1].isoformat() if used else None,
        }

    by_engine = defaultdict(list)
    for setup in all_setups:
        by_engine[setup.engine].append(setup)

    flat_sessions: dict[date, list[Bar]] = {}
    for mapping in sessions_by_symbol.values():
        flat_sessions.update(mapping)

    return {
        "study": "wave_lock_v6",
        "generated_at": datetime.now(UTC).isoformat(),
        "source_db": str(db_path),
        "bar_minutes": 1,
        "params": active,
        "coverage": coverage,
        "engines": {name: summarise(rows) for name, rows in sorted(by_engine.items())},
        "by_direction": {
            f"{engine} {direction}": summarise(
                [s for s in rows if s.direction == direction])
            for engine, rows in sorted(by_engine.items())
            for direction in ("short", "long")
        },
        "matched_random_entry_control": matched_control(all_setups, flat_sessions),
        "cost_basis": "not-applicable:price-level-claim",
        "limitations": [
            "Touch rates are price-path facts about the underlying. The method trades options, "
            "whose spread and theta are not modelled here, so reaching a level is necessary "
            "but not sufficient for a trade to pay.",
            "One-minute bars carry no intrabar sequence; a bar spanning target and stop is "
            "scored as the stop.",
            "Pivots use strict comparison on both sides. Admitting equal highs would raise the "
            "pivot count with points that are not turning points.",
            "Setups within one session are not independent, so a session-clustered interval "
            "would be wider than the Wilson interval shown.",
        ],
        "setups": [asdict(s) for s in all_setups],
    }
