"""Clean-room Python studies for the supplied MU and QQQ Pine indicators.

The indicators do not define trades.  This port preserves their signal/touch
logic, then exposes fixed-horizon records that can be mapped to captured option
NBBO without inventing proprietary or discretionary exits.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
from typing import Iterable, Mapping, Sequence

from core.structural_fib_bars import Bar, resample_5m, split_sessions
from core.structural_fib_v6_options import (
    _covering_run, _first_quote, _option_type, _run_contracts,
)


def _stats(values: Sequence[float]) -> dict:
    if not values:
        return {"n": 0}
    ordered = sorted(values)
    return {
        "n": len(values), "wins": sum(x > 0 for x in values),
        "win_rate": sum(x > 0 for x in values) / len(values),
        "average_return_pct": sum(values) / len(values),
        "median_return_pct": ordered[len(ordered) // 2],
    }


def _option_stats(rows: Sequence[dict]) -> dict:
    values = [float(row["option_return_pct"]) for row in rows]
    result = _stats(values)
    result["losses"] = sum(value < 0 for value in values)
    result["flat"] = sum(value == 0 for value in values)
    return result


def _forward_records(signals: list[dict], bars: Sequence[Bar], entry_delay: int) -> list[dict]:
    by_time = {bar.t: i for i, bar in enumerate(bars)}
    out: list[dict] = []
    for signal in signals:
        signal_at = datetime.fromisoformat(signal["signal_at"])
        entry_at = signal_at + timedelta(minutes=entry_delay)
        idx = by_time.get(entry_at)
        if idx is None:
            continue
        entry = bars[idx].o
        row = dict(signal, entry_at=entry_at.isoformat(), entry_price=entry)
        for minutes in (15, 30, 60):
            exit_idx = by_time.get(entry_at + timedelta(minutes=minutes))
            if exit_idx is None or bars[exit_idx].t.date() != entry_at.date():
                row[f"return_{minutes}m_pct"] = None
            else:
                sign = 1 if signal["direction"] == "long" else -1
                row[f"return_{minutes}m_pct"] = (
                    (bars[exit_idx].c - entry) * sign / entry * 100.0
                )
        out.append(row)
    return out


def mu_premarket_study(minute_bars: Sequence[Bar]) -> dict:
    bars = resample_5m(minute_bars)
    sessions = split_sessions(bars)
    signals: list[dict] = []
    for day in sorted(sessions):
        pre, reg = sessions[day]["pre"], sessions[day]["reg"]
        if not pre or not reg:
            continue
        pmh, pml = max(x.h for x in pre), min(x.l for x in pre)
        for i, bar in enumerate(reg):
            prev_close = reg[i - 1].c if i else pre[-1].c
            found = [
                ("bull_break", "long", bar.c > pmh and prev_close <= pmh),
                ("bear_break", "short", bar.c < pml and prev_close >= pml),
                ("top_sweep", "short", bar.h > pmh and bar.c <= pmh),
                ("bottom_sweep", "long", bar.l < pml and bar.c >= pml),
            ]
            for setup, direction, active in found:
                if active:
                    signals.append({
                        "symbol": "MU", "day": day.isoformat(), "setup_id": setup,
                        "direction": direction, "signal_at": bar.t.isoformat(),
                        "signal_price": bar.c, "pm_high": pmh, "pm_low": pml,
                    })
    records = _forward_records(signals, bars, 5)
    return {
        "study": "obsidian_mu_premarket_liquidity_signals",
        "timeframe": "5Min",
        "coverage": {"start": bars[0].t.date().isoformat() if bars else None,
                     "end": bars[-1].t.date().isoformat() if bars else None,
                     "bars": len(bars)},
        "signals": len(signals),
        "by_setup": dict(Counter(x["setup_id"] for x in signals)),
        "underlying_followthrough": {
            f"{horizon}m": _stats([x[f"return_{horizon}m_pct"] for x in records
                                    if x[f"return_{horizon}m_pct"] is not None])
            for horizon in (15, 30, 60)
        },
        "signal_records": records,
        "raw_signal_records": signals,
        "caveat": "The Pine source is an indicator with no exits; fixed horizons are diagnostics, not a claimed original strategy.",
    }


def _rma(values: Sequence[float], length: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) < length:
        return out
    seed = sum(values[:length]) / length
    out[length - 1] = seed
    prior = seed
    for i in range(length, len(values)):
        prior = (prior * (length - 1) + values[i]) / length
        out[i] = prior
    return out


def _pivothigh(bars: Sequence[Bar], k: int, left: int, right: int) -> bool:
    if k < left or k + right >= len(bars):
        return False
    value = bars[k].h
    return value >= max(x.h for x in bars[k-left:k]) and value > max(x.h for x in bars[k+1:k+right+1])


def _pivotlow(bars: Sequence[Bar], k: int, left: int, right: int) -> bool:
    if k < left or k + right >= len(bars):
        return False
    value = bars[k].l
    return value <= min(x.l for x in bars[k-left:k]) and value < min(x.l for x in bars[k+1:k+right+1])


@dataclass(slots=True)
class _Outcome:
    direction: int
    anchor: float
    t05: float
    t1: float
    t2: float
    start: int
    hit05: bool = False
    hit1: bool = False


def qqq_wave_study(minute_bars: Sequence[Bar]) -> dict:
    """Port the supplied default QQQ v6 signal and touch-stat configuration."""
    bars = list(minute_bars)
    true_ranges: list[float] = []
    for i, bar in enumerate(bars):
        prev = bars[i - 1].c if i else bar.c
        true_ranges.append(max(bar.h - bar.l, abs(bar.h - prev), abs(bar.l - prev)))
    atr = _rma(true_ranges, 14)
    sessions = split_sessions(bars)
    pm_by_day = {
        day: (max(x.h for x in value["pre"]), min(x.l for x in value["pre"]))
        for day, value in sessions.items() if value["pre"]
    }
    early_signals: list[dict] = []
    early_open: list[_Outcome] = []
    validated_open: list[dict] = []
    validated_signals: list[dict] = []
    early = Counter()
    validated = Counter()
    last_direction = 0
    wave: dict | None = None
    current_day = None

    for i, bar in enumerate(bars):
        day = bar.t.date()
        if day != current_day:
            early["failures"] += sum(not x.hit05 for x in early_open)
            early_open.clear()
            validated_open.clear()
            last_direction = 0
            wave = None
            current_day = day
        pm = pm_by_day.get(day)
        in_rth = bar.t.hour > 9 or (bar.t.hour == 9 and bar.t.minute >= 30)
        in_rth = in_rth and bar.t.hour < 16
        if not pm:
            continue
        pmh, pml = pm
        pm_range = pmh - pml
        permitted_high, permitted_low = pmh + pm_range * .25, pml - pm_range * .25

        # EARLY defaults: 3L/1R, prior-close trigger, 0.50 ATR swing,
        # 0.15 ATR confirmation-body, same-direction suppression.
        k = i - 1
        window = bars[i - 4:i + 1] if i >= 4 else []
        atr_ready = atr[i] is not None and atr[i] > 0
        strength = atr_ready and bool(window) and max(x.h for x in window) - min(x.l for x in window) >= .50 * atr[i]
        body = atr_ready and abs(bar.c - bar.o) >= .15 * atr[i]
        pivot_rth = k >= 0 and (bars[k].t.hour > 9 or (bars[k].t.hour == 9 and bars[k].t.minute >= 30)) and bars[k].t.hour < 16
        candidates: list[tuple[int, float]] = []
        if _pivothigh(bars, k, 3, 1) and permitted_low <= bars[k].h <= permitted_high and pivot_rth and bar.c < bars[i-1].c and strength and body:
            candidates.append((-1, bars[k].h))
        if _pivotlow(bars, k, 3, 1) and permitted_low <= bars[k].l <= permitted_high and pivot_rth and bar.c > bars[i-1].c and strength and body:
            candidates.append((1, bars[k].l))
        for direction, anchor in candidates:
            if direction == last_direction:
                continue
            last_direction = direction
            setup = "early_bull" if direction == 1 else "early_bear"
            signal = {
                "symbol": "QQQ", "day": day.isoformat(), "setup_id": setup,
                "direction": "long" if direction == 1 else "short",
                "signal_at": bar.t.isoformat(), "signal_price": bar.c,
                "anchor": anchor, "pm_range": pm_range,
            }
            early_signals.append(signal)
            early["signals"] += 1
            early["bull" if direction == 1 else "bear"] += 1
            early_open.append(_Outcome(direction, anchor, anchor + direction*.5*pm_range,
                                       anchor + direction*pm_range, anchor + direction*2*pm_range, i))

        # Pine processes newly created EARLY records on the confirmation bar.
        for outcome in list(reversed(early_open)):
            failed = not outcome.hit05 and ((outcome.direction == 1 and bar.l < outcome.anchor) or
                                             (outcome.direction == -1 and bar.h > outcome.anchor))
            if failed:
                early["failures"] += 1
                early_open.remove(outcome)
                continue
            touch05 = bar.h >= outcome.t05 if outcome.direction == 1 else bar.l <= outcome.t05
            touch1 = bar.h >= outcome.t1 if outcome.direction == 1 else bar.l <= outcome.t1
            touch2 = bar.h >= outcome.t2 if outcome.direction == 1 else bar.l <= outcome.t2
            if touch05 and not outcome.hit05:
                outcome.hit05 = True; early["hit05"] += 1; early["bars05"] += i - outcome.start
            if touch1 and not outcome.hit1:
                if not outcome.hit05:
                    outcome.hit05 = True; early["hit05"] += 1; early["bars05"] += i - outcome.start
                outcome.hit1 = True; early["hit1"] += 1; early["bars1"] += i - outcome.start
            if touch2:
                if not outcome.hit05:
                    early["hit05"] += 1; early["bars05"] += i - outcome.start
                if not outcome.hit1:
                    early["hit1"] += 1; early["bars1"] += i - outcome.start
                early["hit2"] += 1
                early_open.remove(outcome)

        # VALIDATED defaults: symmetric 3L/3R, same PM-location filter.
        vk = i - 3
        bear = _pivothigh(bars, vk, 3, 3) and permitted_low <= bars[vk].h <= permitted_high
        bull = _pivotlow(bars, vk, 3, 3) and permitted_low <= bars[vk].l <= permitted_high
        pivot_rth_v = vk >= 0 and (bars[vk].t.hour > 9 or (bars[vk].t.hour == 9 and bars[vk].t.minute >= 30)) and bars[vk].t.hour < 16
        if pivot_rth_v and bear and (wave is None or wave["direction"] != -1 or not wave["confirmed"]):
            anchor = bars[vk].h
            wave = {"direction": -1, "anchor": anchor, "t05": anchor-.5*pm_range,
                    "t1": anchor-pm_range, "t2": anchor-2*pm_range, "confirmed": False}
        if pivot_rth_v and bull and (wave is None or wave["direction"] != 1 or not wave["confirmed"]):
            anchor = bars[vk].l
            wave = {"direction": 1, "anchor": anchor, "t05": anchor+.5*pm_range,
                    "t1": anchor+pm_range, "t2": anchor+2*pm_range, "confirmed": False}
        if wave:
            invalid = bar.h > wave["anchor"] if wave["direction"] == -1 else bar.l < wave["anchor"]
            confirm = bar.l <= wave["t05"] if wave["direction"] == -1 else bar.h >= wave["t05"]
            if invalid:
                wave = None
            elif not wave["confirmed"] and confirm:
                wave["confirmed"] = True
                validated["total"] += 1
                validated["bull" if wave["direction"] == 1 else "bear"] += 1
                validated_open.append(dict(wave, start=i, hit1=False))
                validated_signals.append({
                    "symbol": "QQQ", "day": day.isoformat(),
                    "setup_id": "validated_bull" if wave["direction"] == 1 else "validated_bear",
                    "direction": "long" if wave["direction"] == 1 else "short",
                    "signal_at": bar.t.isoformat(), "signal_price": bar.c,
                    "anchor": wave["anchor"], "target": wave["t1"],
                    "stop": wave["anchor"], "pm_range": pm_range,
                })
        for outcome in list(reversed(validated_open)):
            hit1 = bar.h >= outcome["t1"] if outcome["direction"] == 1 else bar.l <= outcome["t1"]
            hit2 = bar.h >= outcome["t2"] if outcome["direction"] == 1 else bar.l <= outcome["t2"]
            if hit1 and not outcome["hit1"]:
                outcome["hit1"] = True; validated["hit1"] += 1
            if hit2:
                if not outcome["hit1"]: validated["hit1"] += 1
                validated["hit2"] += 1
                validated_open.remove(outcome)

    early["failures"] += sum(not x.hit05 for x in early_open)
    records = _forward_records(early_signals, bars, 1)
    return {
        "study": "obsidian_qqq_wave_lock_v6_default",
        "timeframe": "1Min",
        "coverage": {"start": bars[0].t.date().isoformat() if bars else None,
                     "end": bars[-1].t.date().isoformat() if bars else None,
                     "bars": len(bars)},
        "early": {
            **dict(early),
            "hit05_rate": early["hit05"] / early["signals"] if early["signals"] else None,
            "hit1_rate": early["hit1"] / early["signals"] if early["signals"] else None,
            "hit2_rate": early["hit2"] / early["signals"] if early["signals"] else None,
            "failure_rate": early["failures"] / early["signals"] if early["signals"] else None,
            "avg_bars_to_05": early["bars05"] / early["hit05"] if early["hit05"] else None,
            "avg_bars_to_1": early["bars1"] / early["hit1"] if early["hit1"] else None,
        },
        "validated": {
            **dict(validated),
            "hit1_rate": validated["hit1"] / validated["total"] if validated["total"] else None,
            "hit2_rate": validated["hit2"] / validated["total"] if validated["total"] else None,
        },
        "underlying_early_followthrough": {
            f"{horizon}m": _stats([x[f"return_{horizon}m_pct"] for x in records
                                    if x[f"return_{horizon}m_pct"] is not None])
            for horizon in (15, 30, 60)
        },
        "signal_records": records,
        "raw_early_signal_records": early_signals,
        "raw_validated_signal_records": validated_signals,
        "caveat": "Touch statistics port the indicator defaults; fixed-horizon returns are a separate diagnostic because the Pine source defines no trades.",
    }


def tradier_horizon_option_test(
    signals: Iterable[Mapping[str, object]], db_path: Path,
    *, horizons: Sequence[int] = (15, 30, 60), quote_window_seconds: int = 15,
) -> dict:
    """Map signal entries to ask/bid option returns at fixed horizons."""
    db = sqlite3.connect(db_path); db.row_factory = sqlite3.Row
    rows: list[dict] = []
    skips: Counter[str] = Counter()
    entry_mapped = 0
    entry_days: set[str] = set()
    try:
        for signal in signals:
            entry_at = datetime.fromisoformat(str(signal["entry_at"]))
            underlying = str(signal["symbol"])
            run = _covering_run(db, entry_at.astimezone(timezone.utc), underlying)
            if run is None:
                skips["no_stream_run_at_entry"] += 1; continue
            fake_trade = {"direction": signal["direction"]}
            contracts = _run_contracts(run, underlying, _option_type(fake_trade), float(signal["entry_price"]))
            selected = quote = None
            for contract in contracts:
                candidate = _first_quote(db, contract["symbol"], entry_at, quote_window_seconds)
                if candidate is not None:
                    selected, quote = contract, candidate; break
            if selected is None or quote is None:
                skips["no_fresh_entry_nbbo"] += 1; continue
            entry_mapped += 1
            entry_days.add(str(signal["day"]))
            for horizon in horizons:
                exit_quote = _first_quote(db, selected["symbol"], entry_at + timedelta(minutes=horizon), quote_window_seconds)
                if exit_quote is None:
                    skips[f"no_{horizon}m_exit_nbbo"] += 1; continue
                entry_price, exit_price = float(quote["ask"]), float(exit_quote["bid"])
                rows.append({
                    "underlying": underlying, "day": signal["day"], "setup_id": signal["setup_id"],
                    "direction": signal["direction"], "contract": selected["symbol"],
                    "expiration": selected["expiration"], "strike": selected["strike"],
                    "horizon_minutes": horizon, "entry_time": entry_at.isoformat(),
                    "entry_quote_at": quote["captured_at"], "exit_quote_at": exit_quote["captured_at"],
                    "entry_option_price": entry_price, "exit_option_price": exit_price,
                    "option_return_pct": (exit_price / entry_price - 1) * 100,
                })
    finally:
        db.close()
    by_horizon = {}
    for horizon in horizons:
        group = [x for x in rows if x["horizon_minutes"] == horizon]
        setups = sorted({x["setup_id"] for x in group})
        by_horizon[str(horizon)] = {
            **_option_stats(group),
            "by_setup": {
                setup: _option_stats([x for x in group if x["setup_id"] == setup])
                for setup in setups
            },
        }
    return {
        "study": "captured_tradier_fixed_horizon_options",
        "source_signals": entry_mapped + skips["no_stream_run_at_entry"] + skips["no_fresh_entry_nbbo"],
        "entry_mapped_signals": entry_mapped,
        "entry_mapped_days": len(entry_days),
        "by_horizon": by_horizon,
        "skips": dict(skips), "trade_records": rows,
        "caveat": "Entry pays ask and exit receives bid. Results are fixed-horizon diagnostics for indicator signals, not original-strategy P&L.",
    }
