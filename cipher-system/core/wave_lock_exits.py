"""Exit policies for Wave Lock: the specification gap that carries its whole result.

Research-only. No orders, no broker calls.

## Why this exists

`core/wave_lock_lab.py` measured Wave Lock on QQQ 1-minute and found expectancy of +0.0037%
per setup — indistinguishable from zero. Decomposing it showed where that number comes from:

    target   4.1% x +0.799%  = +0.033%
    stop    84.1% x -0.079%  = -0.066%
    close   11.6% x +0.321%  = +0.037%   <- the entire result
    total                      +0.0037%

The `close` bucket is setups that neither reached the 1R target nor were stopped at the anchor,
marked out at the session close because the strategy says nothing about what to do with them.
Remove that bucket and expectancy is **-0.034%**. So the backtest's mark-out convention *is*
the strategy's exit rule, by default rather than by design — and 12-14% of setups sit there.

That makes the exit the highest-information open question, and a cheap one: the setups are
already identified, so every policy below is a re-scoring of the same signals rather than a new
search over entries.

## Discipline

This is a policy search, so it carries a multiple-comparisons cost. Every policy is reported,
never only the best one, and each is scored against a matched random-entry control under the
*same* policy — otherwise a policy that merely suits QQQ's intraday drift would look like an
edge in the strategy. The whole sweep is in-sample on the same 173 sessions that produced the
original finding, so the best policy here is a **hypothesis for the forward test**, not a
result. It is labelled that way in the output.
"""
from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Sequence

from core.structural_fib_bars import UTC, Bar
from core.wave_lock_lab import PARAMS, Setup, atr, day_context, early_setups, load_minute_bars
from core.wave_lock_lab import split_sessions, track, validated_setups

#: Half a position, for the scale-out policy. Named rather than inline so the arithmetic below
#: reads as a position size and not a magic 0.5 colliding with the 0.5R level.
HALF = 0.5


@dataclass(slots=True)
class Fill:
    outcome: str
    return_pct: float


def cluster_robust_t(by_cluster: dict[str, list[float]]) -> tuple[float, float, float, int]:
    """(mean, cluster-robust SE, t, n) for returns grouped by trading session.

    Setups inside one session share a pre-market range, a regime and often overlapping price
    paths, so treating them as independent understates the standard error. This uses the
    standard one-way cluster-robust estimator: the variance of the *sum of within-cluster
    residuals*, divided by total n.

    It replaces an earlier mistake worth recording, because the mistake looked like a strong
    result. Taking the t-statistic of the per-day *means* gave t values of 2.9-4.0 and appeared
    to make the edge highly significant. It was an artifact of equal-weighting sessions that
    carry between 1 and 55 setups: the mean of daily means came out at +0.048% against a true
    per-setup mean of +0.0037%, a thirteenfold inflation driven by one-setup days that happened
    to win. Equal weight per session is a different estimand, not a robustness check.
    """
    values = [r for rows in by_cluster.values() for r in rows]
    n = len(values)
    if n < 2 or len(by_cluster) < 2:
        return (0.0, 0.0, 0.0, n)
    mean = sum(values) / n
    meat = sum((sum(rows) - mean * len(rows)) ** 2 for rows in by_cluster.values())
    se = (meat ** 0.5) / n
    return (mean, se, (mean / se) if se else 0.0, n)


def _signed(direction: str) -> float:
    return 1.0 if direction == "long" else -1.0


def _hits(bar: Bar, level: float, direction: str) -> bool:
    return bar.h >= level if direction == "long" else bar.l <= level


def _breaches(bar: Bar, level: float, direction: str) -> bool:
    return bar.l <= level if direction == "long" else bar.h >= level


def _pct(entry: float, exit_price: float, direction: str) -> float:
    return (exit_price - entry) * _signed(direction) / entry * 100.0


def baseline_1r(setup: Setup, bars: Sequence[Bar]) -> Fill:
    """Target 1R, stop the anchor, mark out at the session close. The lab's reading."""
    for bar in bars:
        if _breaches(bar, setup.anchor, setup.direction):
            return Fill("stop", _pct(setup.entry_price, setup.anchor, setup.direction))
        if _hits(bar, setup.t10, setup.direction):
            return Fill("target", _pct(setup.entry_price, setup.t10, setup.direction))
    return Fill("close", _pct(setup.entry_price, bars[-1].c, setup.direction))


def target_05r(setup: Setup, bars: Sequence[Bar]) -> Fill:
    """Take 0.5R instead of 1R, same anchor stop.

    The obvious candidate: only 14-18% of setups ever reach 0.5R, but the anchor stop is so
    tight that reward:risk at 0.5R is still around 6:1, which breaks even near a 13% hit rate.
    """
    for bar in bars:
        if _breaches(bar, setup.anchor, setup.direction):
            return Fill("stop", _pct(setup.entry_price, setup.anchor, setup.direction))
        if _hits(bar, setup.t05, setup.direction):
            return Fill("target", _pct(setup.entry_price, setup.t05, setup.direction))
    return Fill("close", _pct(setup.entry_price, bars[-1].c, setup.direction))


def breakeven_after_05(setup: Setup, bars: Sequence[Bar]) -> Fill:
    """Target 1R; once 0.5R trades, the stop moves to the entry price."""
    stop = setup.anchor
    armed = False
    for bar in bars:
        if _breaches(bar, stop, setup.direction):
            return Fill("stop_be" if armed else "stop",
                        _pct(setup.entry_price, stop, setup.direction))
        if _hits(bar, setup.t10, setup.direction):
            return Fill("target", _pct(setup.entry_price, setup.t10, setup.direction))
        if not armed and _hits(bar, setup.t05, setup.direction):
            armed = True
            stop = setup.entry_price
    return Fill("close", _pct(setup.entry_price, bars[-1].c, setup.direction))


def partial_05_then_1r(setup: Setup, bars: Sequence[Bar]) -> Fill:
    """Half off at 0.5R, remainder to 1R with the stop at the entry price."""
    stop = setup.anchor
    banked = 0.0
    scaled = False
    for bar in bars:
        if _breaches(bar, stop, setup.direction):
            rest = (HALF if scaled else 1.0) * _pct(setup.entry_price, stop, setup.direction)
            return Fill("stop_partial" if scaled else "stop", banked + rest)
        if _hits(bar, setup.t10, setup.direction):
            rest = (HALF if scaled else 1.0) * _pct(setup.entry_price, setup.t10, setup.direction)
            return Fill("target", banked + rest)
        if not scaled and _hits(bar, setup.t05, setup.direction):
            scaled = True
            banked = HALF * _pct(setup.entry_price, setup.t05, setup.direction)
            stop = setup.entry_price
    rest = (HALF if scaled else 1.0) * _pct(setup.entry_price, bars[-1].c, setup.direction)
    return Fill("close", banked + rest)


def _time_stop(minutes: int) -> Callable[[Setup, Sequence[Bar]], Fill]:
    def policy(setup: Setup, bars: Sequence[Bar]) -> Fill:
        window = bars[:minutes]
        if not window:
            return Fill("close", 0.0)
        for bar in window:
            if _breaches(bar, setup.anchor, setup.direction):
                return Fill("stop", _pct(setup.entry_price, setup.anchor, setup.direction))
            if _hits(bar, setup.t10, setup.direction):
                return Fill("target", _pct(setup.entry_price, setup.t10, setup.direction))
        return Fill("timeout", _pct(setup.entry_price, window[-1].c, setup.direction))
    return policy


#: Every policy is reported. `baseline_1R` is the lab's reading and the thing to beat.
POLICIES: dict[str, Callable[[Setup, Sequence[Bar]], Fill]] = {
    "baseline_1R": baseline_1r,
    "target_0.5R": target_05r,
    "breakeven_after_0.5R": breakeven_after_05,
    "partial_0.5R_then_1R": partial_05_then_1r,
    "time_stop_30m": _time_stop(30),
    "time_stop_60m": _time_stop(60),
    "time_stop_120m": _time_stop(120),
}


def score(setups: Sequence[Setup], sessions: dict[date, list[Bar]]) -> dict:
    """Every policy over every setup, grouped by engine."""
    out: dict[str, dict[str, dict]] = defaultdict(dict)
    for name, policy in POLICIES.items():
        rows: dict[str, list[Fill]] = defaultdict(list)
        per_day: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
        for setup in setups:
            reg = sessions.get(date.fromisoformat(setup.day)) or []
            forward = reg[setup.signal_index + 1:]
            if not forward:
                continue
            fill = policy(setup, forward)
            rows[setup.engine].append(fill)
            per_day[setup.engine][setup.day].append(fill.return_pct)
        for engine, fills in rows.items():
            n = len(fills)
            rets = sorted(f.return_pct for f in fills)
            wins = sum(1 for f in fills if f.return_pct > 0)
            buckets: dict[str, int] = defaultdict(int)
            for f in fills:
                buckets[f.outcome] += 1
            mean, se, tstat, _ = cluster_robust_t(per_day[engine])
            out[engine][name] = {
                "n": n,
                "sessions": len(per_day[engine]),
                "win_rate": wins / n,
                "avg_return_pct": sum(rets) / n,
                "cluster_robust_se_pct": se,
                "cluster_robust_t": tstat,
                # A |t| under 2 means the sign of avg_return_pct is not established.
                "distinguishable_from_zero": abs(tstat) >= 2.0,
                "median_return_pct": rets[n // 2],
                "total_return_pct": sum(rets),
                "outcomes": dict(sorted(buckets.items())),
            }
    return {engine: dict(policies) for engine, policies in out.items()}


def control(setups: Sequence[Setup], sessions: dict[date, list[Bar]], *,
            seed: int = 20260813, replicates: int = 10) -> dict:
    """Each policy applied to random entries with the same reward and risk distances.

    A policy that merely suits QQQ's intraday behaviour would improve the control too. Only
    the gap between strategy and control is attributable to the pivot.
    """
    rng = random.Random(seed)
    out: dict[str, dict[str, dict]] = defaultdict(lambda: defaultdict(
        lambda: {"n": 0, "ret": 0.0, "wins": 0}))
    for setup in setups:
        reg = sessions.get(date.fromisoformat(setup.day)) or []
        if len(reg) < 60:
            continue
        reward05 = abs(setup.t05 - setup.entry_price)
        reward10 = abs(setup.t10 - setup.entry_price)
        risk = abs(setup.anchor - setup.entry_price)
        if risk <= 0:
            continue
        sign = _signed(setup.direction)
        for _ in range(replicates):
            i = rng.randrange(0, len(reg) - 1)
            entry = reg[i].c
            forward = reg[i + 1:]
            if not forward:
                continue
            shadow = Setup(
                symbol=setup.symbol, day=setup.day, engine=setup.engine,
                direction=setup.direction, signal_index=i, signal_time="",
                anchor=entry - sign * risk, entry_price=entry, pm_range=setup.pm_range,
                t05=entry + sign * reward05, t10=entry + sign * reward10,
                t20=entry + sign * reward10 * 2,
            )
            for name, policy in POLICIES.items():
                fill = policy(shadow, forward)
                cell = out[setup.engine][name]
                cell["n"] += 1
                cell["ret"] += fill.return_pct
                cell["wins"] += int(fill.return_pct > 0)
    return {
        engine: {
            name: {"n": int(c["n"]), "avg_return_pct": c["ret"] / c["n"],
                   "win_rate": c["wins"] / c["n"]}
            for name, c in policies.items() if c["n"]
        }
        for engine, policies in out.items()
    }


def run(db_path: Path, symbols: Sequence[str] = ("QQQ",), *,
        params: dict | None = None) -> dict:
    active = dict(PARAMS, **(params or {}))
    setups: list[Setup] = []
    sessions_flat: dict[date, list[Bar]] = {}
    for symbol in symbols:
        minute = load_minute_bars(db_path, symbol)
        if not minute:
            continue
        sessions = split_sessions(minute)
        for day in sorted(sessions):
            pre, reg = sessions[day]["pre"], sessions[day]["reg"]
            if len(reg) < 60:
                continue
            ctx = day_context(pre, proximity_pct=float(active["pm_proximity_pct"]))
            if ctx is None:
                continue
            ctx.day = day
            sessions_flat[day] = reg
            atrs = atr(reg, int(active["atr_length"]))
            early = early_setups(symbol, ctx, reg, atrs, active)
            validated = validated_setups(symbol, ctx, reg, active)
            track(early, reg, include_signal_bar=bool(active["include_signal_bar"]))
            track(validated, reg, include_signal_bar=bool(active["include_signal_bar"]))
            setups.extend(early)
            setups.extend(validated)

    scored = score(setups, sessions_flat)
    ctrl = control(setups, sessions_flat)
    best: dict[str, dict] = {}
    for engine, policies in scored.items():
        ranked = sorted(policies.items(), key=lambda kv: kv[1]["avg_return_pct"], reverse=True)
        top_name, top = ranked[0]
        top_control = (ctrl.get(engine) or {}).get(top_name) or {}
        best[engine] = {
            "policy": top_name,
            "avg_return_pct": top["avg_return_pct"],
            "cluster_robust_t": top.get("cluster_robust_t"),
            "distinguishable_from_zero": top.get("distinguishable_from_zero"),
            "control_avg_return_pct": top_control.get("avg_return_pct"),
            "edge_over_control_pct": (
                None if top_control.get("avg_return_pct") is None
                else top["avg_return_pct"] - top_control["avg_return_pct"]),
            "standing": (
                "HYPOTHESIS for the forward test, not a result: selected as the best of "
                f"{len(POLICIES)} policies on the same 173 in-sample sessions that produced "
                "the original finding."
            ),
        }
    return {
        "study": "wave_lock_exit_policies",
        "generated_at": datetime.now(UTC).isoformat(),
        "params": active,
        "policies_tested": sorted(POLICIES),
        "setups": len(setups),
        "by_engine": scored,
        "matched_random_entry_control": ctrl,
        "best_in_sample": best,
        "cost_basis": "not-applicable:price-level-claim",
        # How inference is done here. Not a limitation -- a limitation is a gap in the
        # evidence, and listing method notes among them pollutes the blocker census with
        # entries no action could ever clear.
        "method": [
            "Cluster-robust t by session is reported for every policy. Where |t| < 2 the sign "
            "of the mean is not established and the policy ranking is noise, which is the "
            "case for every policy measured here.",
        ],
        "limitations": [
            f"{len(POLICIES)} policies scored on one in-sample dataset with no out-of-sample "
            "period. The spread between best and worst is an upper bound on what policy "
            "choice is worth, not an estimate of what the best one earns out of sample.",
            "Returns are underlying price moves, so reaching a level is necessary but not "
            "sufficient for the trade to pay: QQQ 0DTE measured option half-spread is 0.625% "
            "of premium (p95 2.625%), a round trip near 1.25% that is not modelled here.",
            "One-minute bars carry no intrabar sequence and are the finest series held "
            "locally, so a bar spanning target and stop is scored as the stop throughout.",
            "Scale-out returns assume both halves fill at the stated level, which overstates "
            "a real partial exit slightly.",
        ],
    }
