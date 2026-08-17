"""Python port of the normal, default-enabled portion of Structural Fib V6.

The source strategy is Pine's "Validated Normal Baseline (Native 5m)".  This
module intentionally freezes its default profile: fixed first/second opening
anchor, normal 0.5->1 and fresh 1->2 legs, reversals disabled, next-bar-open
entries, price-touch targets, confirmed-close invalidations, and no overnight
positions.  It models the underlying, not option premium.

No broker client is imported and no order can leave this process.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from datetime import date, time
import hashlib
import json
import math
from typing import Iterable, Sequence

from core.structural_fib_bars import Bar, split_sessions


@dataclass(frozen=True, slots=True)
class V6Params:
    max_pm_range_pct: float = 1.50
    fallback_sessions: int = 4
    normal_window_minutes: int = 120
    max_first_candle_pct: float = 0.75
    use_buffer: bool = True
    buffer_pct: float = 0.05
    require_strong_body: bool = True
    min_body_pct: float = 50.0
    min_bull_close_location: float = 65.0
    max_bear_close_location: float = 35.0
    max_penetration_pct: float = 50.0
    enable_continuations: bool = True
    require_fresh_continuation: bool = True
    max_continuation_bars: int = 5
    reset_continuation_on_half_loss: bool = True
    one_entry_per_leg: bool = True
    close_at_rth: bool = True
    equity_fraction: float = 0.10
    initial_capital: float = 100_000.0


DEFAULT_PARAMS = V6Params()


def params_hash(p: V6Params = DEFAULT_PARAMS) -> str:
    payload = json.dumps(asdict(p), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


@dataclass(slots=True)
class Signal:
    symbol: str
    day: str
    setup_id: str
    direction: str
    signal_time: str
    signal_close: float
    target: float
    stop: float
    pm_age: int
    pm_range_pct: float
    anchor_source: str
    body_pct: float
    penetration_pct: float
    half_delay_bars: int


@dataclass(slots=True)
class Trade:
    symbol: str
    day: str
    setup_id: str
    direction: str
    signal_time: str
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    target: float
    stop: float
    exit_reason: str
    return_pct: float
    mfe_pct: float
    mae_pct: float
    bars_held: int
    pm_age: int
    pm_range_pct: float
    anchor_source: str
    body_pct: float
    penetration_pct: float
    half_delay_bars: int


@dataclass(slots=True)
class _OpenTrade:
    signal: Signal
    entry_price: float
    entry_time: str
    entry_index: int
    max_high: float
    min_low: float


def body_pct(bar: Bar) -> float:
    spread = bar.h - bar.l
    return abs(bar.c - bar.o) / spread * 100.0 if spread > 0 else 0.0


def close_location(bar: Bar) -> float:
    spread = bar.h - bar.l
    return (bar.c - bar.l) / spread * 100.0 if spread > 0 else 50.0


def bull_quality(bar: Bar, p: V6Params) -> bool:
    quality = not p.require_strong_body or (
        body_pct(bar) >= p.min_body_pct
        and close_location(bar) >= p.min_bull_close_location
    )
    return bar.c > bar.o and quality


def bear_quality(bar: Bar, p: V6Params) -> bool:
    quality = not p.require_strong_body or (
        body_pct(bar) >= p.min_body_pct
        and close_location(bar) <= p.max_bear_close_location
    )
    return bar.c < bar.o and quality


def penetration(entry_level: float, target: float, close: float, direction: str) -> float:
    distance = (target - entry_level) if direction == "long" else (entry_level - target)
    progress = (close - entry_level) if direction == "long" else (entry_level - close)
    return progress / distance * 100.0 if distance > 0 else 999.0


def _finish(opened: _OpenTrade, bar: Bar, index: int, price: float, reason: str) -> Trade:
    opened.max_high = max(opened.max_high, bar.h)
    opened.min_low = min(opened.min_low, bar.l)
    sign = 1.0 if opened.signal.direction == "long" else -1.0
    ret = (price - opened.entry_price) * sign / opened.entry_price * 100.0
    favorable = (
        opened.max_high - opened.entry_price
        if sign > 0 else opened.entry_price - opened.min_low
    ) / opened.entry_price * 100.0
    adverse = (
        opened.entry_price - opened.min_low
        if sign > 0 else opened.max_high - opened.entry_price
    ) / opened.entry_price * 100.0
    s = opened.signal
    return Trade(
        symbol=s.symbol, day=s.day, setup_id=s.setup_id, direction=s.direction,
        signal_time=s.signal_time, entry_time=opened.entry_time,
        exit_time=bar.t.strftime("%H:%M"), entry_price=opened.entry_price,
        exit_price=price, target=s.target, stop=s.stop, exit_reason=reason,
        return_pct=ret, mfe_pct=max(0.0, favorable), mae_pct=max(0.0, adverse),
        bars_held=index - opened.entry_index + 1, pm_age=s.pm_age,
        pm_range_pct=s.pm_range_pct, anchor_source=s.anchor_source,
        body_pct=s.body_pct, penetration_pct=s.penetration_pct,
        half_delay_bars=s.half_delay_bars,
    )


def evaluate_day(
    symbol: str,
    day: date,
    reg: Sequence[Bar],
    pm_high: float,
    pm_low: float,
    pm_age: int,
    pm_range_pct: float,
    p: V6Params = DEFAULT_PARAMS,
) -> tuple[list[Signal], list[Trade]]:
    """Evaluate one symbol-session using only information available bar by bar."""
    if len(reg) < 2 or pm_high <= pm_low:
        return [], []
    unit = pm_high - pm_low
    first = reg[0]
    first_range_pct = (first.h - first.l) / first.o * 100.0 if first.o else 999.0
    anchor_index = 1 if first_range_pct > p.max_first_candle_pct else 0
    if anchor_index >= len(reg) - 1:
        return [], []
    anchor = reg[anchor_index]
    anchor_source = "2nd 5m wick" if anchor_index else "1st 5m wick"
    c0, p0 = anchor.l, anchor.h
    c05, c1, c2 = c0 + 0.5 * unit, c0 + unit, c0 + 2.0 * unit
    p05, p1, p2 = p0 - 0.5 * unit, p0 - unit, p0 - 2.0 * unit
    up = 1.0 + p.buffer_pct / 100.0 if p.use_buffer else 1.0
    down = 1.0 - p.buffer_pct / 100.0 if p.use_buffer else 1.0

    signals: list[Signal] = []
    trades: list[Trade] = []
    fired: set[str] = set()
    call_half_bar: int | None = None
    put_half_bar: int | None = None
    pending_entry: Signal | None = None
    pending_stop = False
    opened: _OpenTrade | None = None

    for i in range(anchor_index + 1, len(reg)):
        bar, prev = reg[i], reg[i - 1]

        # Pine's default broker emulator fills orders created at the prior close
        # on this bar's open. Confirmed-close invalidations use the same rule.
        if opened is not None and pending_stop:
            trades.append(_finish(opened, bar, i, bar.o, "fib_invalidation"))
            opened, pending_stop = None, False
        if opened is None and pending_entry is not None:
            opened = _OpenTrade(
                pending_entry, bar.o, bar.t.strftime("%H:%M"), i, bar.h, bar.l
            )
            pending_entry = None

        if opened is not None:
            opened.max_high = max(opened.max_high, bar.h)
            opened.min_low = min(opened.min_low, bar.l)
            target_hit = (
                bar.h >= opened.signal.target
                if opened.signal.direction == "long"
                else bar.l <= opened.signal.target
            )
            if target_hit:
                # A favorable gap through a standing limit receives the open.
                price = (
                    max(opened.signal.target, bar.o)
                    if opened.signal.direction == "long"
                    else min(opened.signal.target, bar.o)
                )
                trades.append(_finish(opened, bar, i, price, "target"))
                opened = None

        minutes = (bar.t.hour * 60 + bar.t.minute) - 570
        normal_time = 0 <= minutes < p.normal_window_minutes
        c05_trig, c1_trig = c05 * up, c1 * up
        p05_trig, p1_trig = p05 * down, p1 * down
        cross_c05 = prev.c <= c05_trig < bar.c
        cross_p05 = prev.c >= p05_trig > bar.c
        cross_c1 = prev.c <= c1_trig < bar.c
        cross_p1 = prev.c >= p1_trig > bar.c

        if cross_c05:
            call_half_bar = i
        if cross_p05:
            put_half_bar = i
        if p.reset_continuation_on_half_loss and call_half_bar is not None and i > call_half_bar and bar.c < c05_trig:
            call_half_bar = None
        if p.reset_continuation_on_half_loss and put_half_bar is not None and i > put_half_bar and bar.c > p05_trig:
            put_half_bar = None
        if p.require_fresh_continuation and call_half_bar is not None and i - call_half_bar > p.max_continuation_bars:
            call_half_bar = None
        if p.require_fresh_continuation and put_half_bar is not None and i - put_half_bar > p.max_continuation_bars:
            put_half_bar = None

        # Confirmed-close stops are submitted at this close and fill next open.
        if opened is not None:
            stopped = (
                bar.c < opened.signal.stop
                if opened.signal.direction == "long"
                else bar.c > opened.signal.stop
            )
            if stopped:
                pending_stop = True

        flat = opened is None and pending_entry is None
        if flat and normal_time:
            candidates: list[tuple[str, str, bool, float, float, float, int]] = [
                ("C05", "long", cross_c05 and bull_quality(bar, p), c05, c1, c0, 0),
                ("P05", "short", cross_p05 and bear_quality(bar, p), p05, p1, p0, 0),
                (
                    "C1", "long",
                    p.enable_continuations and call_half_bar is not None and i > call_half_bar
                    and (not p.require_fresh_continuation or i - call_half_bar <= p.max_continuation_bars)
                    and cross_c1 and bull_quality(bar, p),
                    c1, c2, c05, i - call_half_bar if call_half_bar is not None else 0,
                ),
                (
                    "P1", "short",
                    p.enable_continuations and put_half_bar is not None and i > put_half_bar
                    and (not p.require_fresh_continuation or i - put_half_bar <= p.max_continuation_bars)
                    and cross_p1 and bear_quality(bar, p),
                    p1, p2, p05, i - put_half_bar if put_half_bar is not None else 0,
                ),
            ]
            for setup_id, direction, condition, level, target, stop, delay in candidates:
                pen = penetration(level, target, bar.c, direction)
                can_fire = not p.one_entry_per_leg or setup_id not in fired
                if condition and can_fire and pen <= p.max_penetration_pct:
                    signal = Signal(
                        symbol=symbol, day=day.isoformat(), setup_id=setup_id,
                        direction=direction, signal_time=bar.t.strftime("%H:%M"),
                        signal_close=bar.c, target=target, stop=stop, pm_age=pm_age,
                        pm_range_pct=pm_range_pct, anchor_source=anchor_source,
                        body_pct=body_pct(bar), penetration_pct=pen,
                        half_delay_bars=delay,
                    )
                    fired.add(setup_id)
                    signals.append(signal)
                    pending_entry = signal
                    break  # Pine's else-if priority permits one entry per bar.

        if p.close_at_rth and bar.t.time() == time(15, 55) and opened is not None:
            trades.append(_finish(opened, bar, i, bar.c, "rth_close"))
            opened, pending_stop = None, False

    if opened is not None:
        trades.append(_finish(opened, reg[-1], len(reg) - 1, reg[-1].c, "end_of_data"))
    # A signal on a final bar has no next-tick fill and is correctly left untraded.
    return signals, trades


def run_symbol(symbol: str, bars_5m: Sequence[Bar], p: V6Params = DEFAULT_PARAMS) -> dict:
    sessions = split_sessions(bars_5m)
    history: deque[tuple[float, float, float]] = deque(maxlen=p.fallback_sessions)
    signals: list[Signal] = []
    trades: list[Trade] = []
    covered: list[str] = []
    for day in sorted(sessions):
        pre, reg = sessions[day]["pre"], sessions[day]["reg"]
        pm_high = max((b.h for b in pre), default=None)
        pm_low = min((b.l for b in pre), default=None)
        current_range = (
            (pm_high - pm_low) / pm_low * 100.0
            if pm_high is not None and pm_low not in (None, 0) else None
        )
        selected: tuple[float, float, float] | None = None
        age = 0
        if current_range is not None and current_range <= p.max_pm_range_pct:
            selected = (pm_high, pm_low, current_range)  # type: ignore[arg-type]
        else:
            for idx, candidate in enumerate(history):
                if candidate[2] <= p.max_pm_range_pct:
                    selected, age = candidate, idx + 1
                    break
        if selected is not None and reg:
            s, t = evaluate_day(symbol, day, reg, *selected[:2], age, selected[2], p)
            signals.extend(s)
            trades.extend(t)
            covered.append(day.isoformat())
        if current_range is not None and pm_high is not None and pm_low is not None:
            history.appendleft((pm_high, pm_low, current_range))
    return {
        "symbol": symbol,
        "coverage": {
            "sessions": len(covered),
            "start": covered[0] if covered else None,
            "end": covered[-1] if covered else None,
        },
        "signals": [asdict(x) for x in signals],
        "trades": [asdict(x) for x in trades],
    }


def summarise(trades: Iterable[dict]) -> dict:
    rows = list(trades)
    if not rows:
        return {"n": 0}
    wins = [r for r in rows if r["return_pct"] > 0]
    target_hits = [r for r in rows if r["exit_reason"] == "target"]
    gross_win = sum(max(0.0, r["return_pct"]) for r in rows)
    gross_loss = -sum(min(0.0, r["return_pct"]) for r in rows)
    returns = sorted(r["return_pct"] for r in rows)
    hit_rate = len(target_hits) / len(rows)
    z = 1.96
    denominator = 1.0 + z * z / len(rows)
    center = (hit_rate + z * z / (2 * len(rows))) / denominator
    half = z * math.sqrt(
        hit_rate * (1.0 - hit_rate) / len(rows) + z * z / (4 * len(rows) ** 2)
    ) / denominator
    return {
        "n": len(rows),
        "target_hit_rate": hit_rate,
        "target_hit_ci95": [max(0.0, center - half), min(1.0, center + half)],
        "win_rate": len(wins) / len(rows),
        "avg_return_pct": sum(returns) / len(rows),
        "median_return_pct": returns[len(returns) // 2],
        "avg_mfe_pct": sum(r["mfe_pct"] for r in rows) / len(rows),
        "avg_mae_pct": sum(r["mae_pct"] for r in rows) / len(rows),
        "avg_bars": sum(r["bars_held"] for r in rows) / len(rows),
        "profit_factor": gross_win / gross_loss if gross_loss else None,
    }


def report(results: Sequence[dict], p: V6Params = DEFAULT_PARAMS) -> dict:
    trades = [trade for result in results for trade in result["trades"]]
    by_setup: dict[str, list[dict]] = defaultdict(list)
    by_symbol: dict[str, list[dict]] = defaultdict(list)
    by_direction: dict[str, list[dict]] = defaultdict(list)
    by_pm_source: dict[str, list[dict]] = defaultdict(list)
    for trade in trades:
        by_setup[trade["setup_id"]].append(trade)
        by_symbol[trade["symbol"]].append(trade)
        by_direction[trade["direction"]].append(trade)
        by_pm_source["today" if trade["pm_age"] == 0 else "fallback"].append(trade)
    days = sorted({trade["day"] for trade in trades})
    cutoff = days[len(days) // 2] if days else None
    by_period = {
        "first_half": summarise(t for t in trades if cutoff and t["day"] < cutoff),
        "second_half": summarise(t for t in trades if cutoff and t["day"] >= cutoff),
    }
    equity = p.initial_capital
    peak = equity
    max_drawdown = 0.0
    for trade in sorted(trades, key=lambda x: (x["day"], x["entry_time"], x["symbol"])):
        equity *= 1.0 + p.equity_fraction * trade["return_pct"] / 100.0
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, (peak - equity) / peak * 100.0)
    return {
        "study": "structural_fib_v6_normal_baseline",
        "params": asdict(p),
        "params_hash": params_hash(p),
        "coverage": {r["symbol"]: r["coverage"] for r in results},
        "overall": summarise(trades),
        "by_setup": {key: summarise(value) for key, value in sorted(by_setup.items())},
        "by_symbol": {key: summarise(value) for key, value in sorted(by_symbol.items())},
        "by_direction": {key: summarise(value) for key, value in sorted(by_direction.items())},
        "by_pm_source": {key: summarise(value) for key, value in sorted(by_pm_source.items())},
        "temporal_split": {"cutoff": cutoff, **by_period},
        "signals": sum(len(r["signals"]) for r in results),
        "trades": len(trades),
        "ending_equity_serialized": equity,
        "net_equity_return_pct_serialized": (equity / p.initial_capital - 1.0) * 100.0,
        "max_drawdown_pct_serialized": max_drawdown,
        "trade_records": trades,
        "limitations": [
            "Underlying-price simulation only; option premium, IV, theta, and option spreads are not modeled.",
            "Portfolio equity is a deterministic serialized diagnostic across symbols, not a claim about simultaneous fills.",
            "Five-minute OHLC cannot reveal intrabar path; standing targets are evaluated before confirmed-close stops, matching the Pine order types.",
            "Reversals are disabled in the supplied V6 baseline and are not pooled into these results.",
        ],
    }
