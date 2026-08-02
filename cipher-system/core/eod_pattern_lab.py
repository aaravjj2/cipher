"""Comprehensive end-of-day pattern study for SPY, QQQ, and IWM.

Research-only. Uses split/dividend-adjusted Alpaca SIP 5-minute bars, exact
America/New_York regular-session boundaries, next-bar entries, a 2 bp round-trip
equity cost assumption, HAC inference, circular block-bootstrap confidence
intervals, Benjamini-Hochberg multiple-testing control, and a recent three-month
stability check.

The study analyzes underlying ETF direction. It does not model option Greeks,
bid/ask spreads, implied volatility, or option execution.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sqlite3
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any, Callable, Iterable, Sequence
from zoneinfo import ZoneInfo

import numpy as np
from scipy import stats


CORE = Path(__file__).resolve().parent
CIPHER_ROOT = CORE.parent
DEFAULT_DB = CIPHER_ROOT / "data" / "historical_equities" / "alpaca_eod_indices" / "equity_bars.sqlite"
DEFAULT_OUT = CIPHER_ROOT / "data" / "eod_pattern_lab"
NY = ZoneInfo("America/New_York")
UTC = timezone.utc
SYMBOLS = ("SPY", "QQQ", "IWM")
ROUND_TRIP_COST_PCT = 0.02  # 2 basis points, underlying ETF only.
BLOCK_LENGTH = 5
BOOTSTRAP_REPS = 2000
MIN_REPORT_N = 12


@dataclass(slots=True)
class PatternObservation:
    day: str
    return_pct: float


@dataclass(slots=True)
class PatternSpec:
    pattern_id: str
    symbol: str
    family: str
    name: str
    signal_time: str
    holding_period: str
    side: str
    notes: str
    observations: list[PatternObservation]


@dataclass(slots=True)
class PatternResult:
    pattern_id: str
    sample: str
    symbol: str
    family: str
    name: str
    signal_time: str
    holding_period: str
    side: str
    notes: str
    n: int
    mean_return_pct: float | None
    median_return_pct: float | None
    net_mean_return_pct: float | None
    win_rate_pct: float | None
    profit_factor: float | None
    std_pct: float | None
    hac_se_pct: float | None
    hac_t_stat: float | None
    hac_p_value: float | None
    bootstrap_ci_low_pct: float | None
    bootstrap_ci_high_pct: float | None
    best_return_pct: float | None
    worst_return_pct: float | None
    fdr_q_value: float | None = None
    recent_n: int | None = None
    recent_net_mean_pct: float | None = None
    recent_win_rate_pct: float | None = None
    stable_sign: bool | None = None
    robustness_score: float | None = None


def utcnow() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def pct(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or a == 0:
        return None
    return (b / a - 1.0) * 100.0


def safe_div(a: float, b: float) -> float | None:
    return a / b if b else None


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def bar_end_index(hour: int, minute: int) -> int:
    """Index of the 5-minute bar whose close lands on the supplied ET time."""
    total = (hour * 60 + minute) - (9 * 60 + 30)
    if total <= 0 or total > 390 or total % 5:
        raise ValueError(f"invalid regular-session checkpoint {hour:02d}:{minute:02d}")
    return total // 5 - 1


IDX_1030 = bar_end_index(10, 30)
IDX_1200 = bar_end_index(12, 0)
IDX_1400 = bar_end_index(14, 0)
IDX_1430 = bar_end_index(14, 30)
IDX_1500 = bar_end_index(15, 0)
IDX_1530 = bar_end_index(15, 30)
IDX_1545 = bar_end_index(15, 45)
IDX_1555 = bar_end_index(15, 55)
IDX_CLOSE = bar_end_index(16, 0)


def load_sessions(db_path: Path) -> dict[str, list[dict[str, Any]]]:
    if not db_path.exists():
        raise FileNotFoundError(db_path)
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    with sqlite3.connect(db_path) as db:
        rows = db.execute(
            """select symbol,timestamp,open,high,low,close,volume,vwap,trades
               from bars where timeframe='5Min' and symbol in ('SPY','QQQ','IWM')
               order by symbol,timestamp"""
        ).fetchall()
    for symbol, ts, op, hi, lo, cl, volume, vwap, trades in rows:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(NY)
        if not (time(9, 30) <= dt.time() < time(16, 0)):
            continue
        grouped[(symbol, dt.date().isoformat())].append(
            {
                "timestamp": str(ts),
                "dt_et": dt,
                "open": float(op),
                "high": float(hi),
                "low": float(lo),
                "close": float(cl),
                "volume": float(volume or 0.0),
                "vwap": float(vwap) if vwap is not None else None,
                "trades": int(trades) if trades is not None else None,
            }
        )

    expected_times = [
        (datetime.combine(date(2000, 1, 1), time(9, 30)) + timedelta(minutes=5 * i)).time()
        for i in range(78)
    ]
    sessions: dict[str, list[dict[str, Any]]] = {symbol: [] for symbol in SYMBOLS}
    for (symbol, day), bars in sorted(grouped.items()):
        bars.sort(key=lambda row: row["dt_et"])
        actual_times = [row["dt_et"].time().replace(tzinfo=None) for row in bars]
        if len(bars) != 78 or actual_times != expected_times:
            continue
        sessions[symbol].append(build_session(symbol, day, bars))

    common_days = set.intersection(
        *[{row["day"] for row in sessions[symbol]} for symbol in SYMBOLS]
    )
    for symbol in SYMBOLS:
        sessions[symbol] = [row for row in sessions[symbol] if row["day"] in common_days]
        sessions[symbol].sort(key=lambda row: row["day"])
    attach_lagged_and_forward_features(sessions)
    return sessions


def cumulative_vwap(bars: Sequence[dict[str, Any]], end_index: int) -> float:
    numerator = 0.0
    denominator = 0.0
    for bar in bars[: end_index + 1]:
        reference = bar["vwap"]
        if reference is None:
            reference = (bar["high"] + bar["low"] + bar["close"]) / 3.0
        vol = bar["volume"]
        numerator += reference * vol
        denominator += vol
    return numerator / denominator if denominator else bars[end_index]["close"]


def range_position(bars: Sequence[dict[str, Any]], end_index: int) -> float:
    high = max(bar["high"] for bar in bars[: end_index + 1])
    low = min(bar["low"] for bar in bars[: end_index + 1])
    if high <= low:
        return 0.5
    return clamp((bars[end_index]["close"] - low) / (high - low), 0.0, 1.0)


def window_volume(bars: Sequence[dict[str, Any]], start_index: int, end_index: int) -> float:
    return sum(bar["volume"] for bar in bars[start_index : end_index + 1])


def next_bar_return(
    bars: Sequence[dict[str, Any]],
    signal_end_index: int,
    side: str,
    exit_index: int = IDX_CLOSE,
) -> float | None:
    entry_index = signal_end_index + 1
    if entry_index > exit_index or entry_index >= len(bars):
        return None
    entry = bars[entry_index]["open"]
    exit_price = bars[exit_index]["close"]
    raw = pct(entry, exit_price)
    if raw is None:
        return None
    return raw if side == "long" else -raw


def event_entry_return(
    bars: Sequence[dict[str, Any]],
    condition: Callable[[dict[str, Any]], bool],
    start_index: int,
    side: str,
) -> tuple[float | None, int | None]:
    for index in range(start_index, IDX_CLOSE):
        if condition(bars[index]):
            entry_index = index + 1
            if entry_index > IDX_CLOSE:
                return None, None
            raw = pct(bars[entry_index]["open"], bars[IDX_CLOSE]["close"])
            if raw is None:
                return None, None
            return (raw if side == "long" else -raw), index
    return None, None


def build_session(symbol: str, day: str, bars: list[dict[str, Any]]) -> dict[str, Any]:
    open_price = bars[0]["open"]
    close_price = bars[IDX_CLOSE]["close"]
    day_high = max(bar["high"] for bar in bars)
    day_low = min(bar["low"] for bar in bars)
    pre3_high = max(bar["high"] for bar in bars[: IDX_1500 + 1])
    pre3_low = min(bar["low"] for bar in bars[: IDX_1500 + 1])
    afternoon_high = max(bar["high"] for bar in bars[IDX_1400 + 1 : IDX_1500 + 1])
    afternoon_low = min(bar["low"] for bar in bars[IDX_1400 + 1 : IDX_1500 + 1])

    vwap_1430 = cumulative_vwap(bars, IDX_1430)
    vwap_1500 = cumulative_vwap(bars, IDX_1500)
    vwap_1530 = cumulative_vwap(bars, IDX_1530)
    vwap_1545 = cumulative_vwap(bars, IDX_1545)
    vwap_close = cumulative_vwap(bars, IDX_CLOSE)

    breakout_long, breakout_long_index = event_entry_return(
        bars, lambda bar: bar["close"] > pre3_high, IDX_1500 + 1, "long"
    )
    breakdown_short, breakdown_short_index = event_entry_return(
        bars, lambda bar: bar["close"] < pre3_low, IDX_1500 + 1, "short"
    )
    failed_breakout_short, failed_breakout_index = event_entry_return(
        bars,
        lambda bar: bar["high"] > pre3_high and bar["close"] <= pre3_high,
        IDX_1500 + 1,
        "short",
    )
    failed_breakdown_long, failed_breakdown_index = event_entry_return(
        bars,
        lambda bar: bar["low"] < pre3_low and bar["close"] >= pre3_low,
        IDX_1500 + 1,
        "long",
    )
    afternoon_breakout_long, _ = event_entry_return(
        bars, lambda bar: bar["close"] > afternoon_high, IDX_1500 + 1, "long"
    )
    afternoon_breakdown_short, _ = event_entry_return(
        bars, lambda bar: bar["close"] < afternoon_low, IDX_1500 + 1, "short"
    )

    close_location = (close_price - day_low) / (day_high - day_low) if day_high > day_low else 0.5
    feature = {
        "symbol": symbol,
        "day": day,
        "weekday": datetime.fromisoformat(day).strftime("%A"),
        "bars": bars,
        "open": open_price,
        "high": day_high,
        "low": day_low,
        "close": close_price,
        "day_return_pct": pct(open_price, close_price),
        "close_location": close_location,
        "price_1030": bars[IDX_1030]["close"],
        "price_1200": bars[IDX_1200]["close"],
        "price_1400": bars[IDX_1400]["close"],
        "price_1430": bars[IDX_1430]["close"],
        "price_1500": bars[IDX_1500]["close"],
        "price_1530": bars[IDX_1530]["close"],
        "price_1545": bars[IDX_1545]["close"],
        "price_1555": bars[IDX_1555]["close"],
        "ret_open_1030": pct(open_price, bars[IDX_1030]["close"]),
        "ret_open_1200": pct(open_price, bars[IDX_1200]["close"]),
        "ret_open_1400": pct(open_price, bars[IDX_1400]["close"]),
        "ret_open_1430": pct(open_price, bars[IDX_1430]["close"]),
        "ret_open_1500": pct(open_price, bars[IDX_1500]["close"]),
        "ret_open_1530": pct(open_price, bars[IDX_1530]["close"]),
        "ret_open_1545": pct(open_price, bars[IDX_1545]["close"]),
        "ret_1400_1430": pct(bars[IDX_1400]["close"], bars[IDX_1430]["close"]),
        "ret_1430_1500": pct(bars[IDX_1430]["close"], bars[IDX_1500]["close"]),
        "ret_1500_1530": pct(bars[IDX_1500]["close"], bars[IDX_1530]["close"]),
        "ret_1530_1545": pct(bars[IDX_1530]["close"], bars[IDX_1545]["close"]),
        "power_hour_long": next_bar_return(bars, IDX_1500, "long"),
        "power_hour_short": next_bar_return(bars, IDX_1500, "short"),
        "last30_long": next_bar_return(bars, IDX_1530, "long"),
        "last30_short": next_bar_return(bars, IDX_1530, "short"),
        "last15_long": next_bar_return(bars, IDX_1545, "long"),
        "last15_short": next_bar_return(bars, IDX_1545, "short"),
        "final5_long": pct(bars[IDX_1555 + 1]["open"], close_price),
        "final5_short": -pct(bars[IDX_1555 + 1]["open"], close_price),
        "vwap_1430": vwap_1430,
        "vwap_1500": vwap_1500,
        "vwap_1530": vwap_1530,
        "vwap_1545": vwap_1545,
        "vwap_close": vwap_close,
        "above_vwap_1430": bars[IDX_1430]["close"] > vwap_1430,
        "above_vwap_1500": bars[IDX_1500]["close"] > vwap_1500,
        "above_vwap_1530": bars[IDX_1530]["close"] > vwap_1530,
        "above_vwap_1545": bars[IDX_1545]["close"] > vwap_1545,
        "above_vwap_close": close_price > vwap_close,
        "range_pos_1430": range_position(bars, IDX_1430),
        "range_pos_1500": range_position(bars, IDX_1500),
        "range_pos_1530": range_position(bars, IDX_1530),
        "range_pos_1545": range_position(bars, IDX_1545),
        "pre3_high": pre3_high,
        "pre3_low": pre3_low,
        "pre3_range_pct": pct(pre3_low, pre3_high),
        "vol_1300_1400": window_volume(bars, bar_end_index(13, 0) + 1, IDX_1400),
        "vol_1400_1500": window_volume(bars, IDX_1400 + 1, IDX_1500),
        "vol_1500_close": window_volume(bars, IDX_1500 + 1, IDX_CLOSE),
        "vol_1500_1530": window_volume(bars, IDX_1500 + 1, IDX_1530),
        "vol_1530_close": window_volume(bars, IDX_1530 + 1, IDX_CLOSE),
        "vol_1530_1545": window_volume(bars, IDX_1530 + 1, IDX_1545),
        "vol_1545_close": window_volume(bars, IDX_1545 + 1, IDX_CLOSE),
        "breakout_long": breakout_long,
        "breakout_long_index": breakout_long_index,
        "breakdown_short": breakdown_short,
        "breakdown_short_index": breakdown_short_index,
        "failed_breakout_short": failed_breakout_short,
        "failed_breakout_index": failed_breakout_index,
        "failed_breakdown_long": failed_breakdown_long,
        "failed_breakdown_index": failed_breakdown_index,
        "afternoon_breakout_long": afternoon_breakout_long,
        "afternoon_breakdown_short": afternoon_breakdown_short,
    }
    feature["vol_ratio_14_15_vs_13_14"] = safe_div(feature["vol_1400_1500"], feature["vol_1300_1400"])
    feature["vol_ratio_last30_vs_first30"] = safe_div(feature["vol_1530_close"], feature["vol_1500_1530"])
    feature["vol_ratio_last15_vs_prior15"] = safe_div(feature["vol_1545_close"], feature["vol_1530_1545"])
    return feature


def attach_lagged_and_forward_features(sessions: dict[str, list[dict[str, Any]]]) -> None:
    for symbol in SYMBOLS:
        rows = sessions[symbol]
        ranges: list[float] = []
        for index, row in enumerate(rows):
            prior = rows[index - 1] if index else None
            next_row = rows[index + 1] if index + 1 < len(rows) else None
            row["prior_close"] = prior["close"] if prior else None
            row["gap_pct"] = pct(prior["close"], row["open"]) if prior else None
            true_range = row["high"] - row["low"]
            if prior:
                true_range = max(
                    true_range,
                    abs(row["high"] - prior["close"]),
                    abs(row["low"] - prior["close"]),
                )
            row["true_range_pct"] = true_range / row["open"] * 100.0
            trailing = ranges[max(0, index - 20) : index]
            row["trailing20_median_range_pct"] = median(trailing) if len(trailing) >= 10 else None
            row["pre3_range_regime_ratio"] = (
                row["pre3_range_pct"] / row["trailing20_median_range_pct"]
                if row["trailing20_median_range_pct"]
                else None
            )
            ranges.append(row["true_range_pct"])
            if next_row:
                row["overnight_return_pct"] = pct(row["close"], next_row["open"])
                row["next_open_to_1030_pct"] = pct(next_row["open"], next_row["price_1030"])
                row["next_open_to_close_pct"] = pct(next_row["open"], next_row["close"])
                row["next_close_to_close_pct"] = pct(row["close"], next_row["close"])
                row["next_power_hour_pct"] = next_row["power_hour_long"]
            else:
                row["overnight_return_pct"] = None
                row["next_open_to_1030_pct"] = None
                row["next_open_to_close_pct"] = None
                row["next_close_to_close_pct"] = None
                row["next_power_hour_pct"] = None


def make_pattern_id(symbol: str, family: str, name: str, signal_time: str, holding_period: str, side: str) -> str:
    raw = "|".join((symbol, family, name, signal_time, holding_period, side))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def observations(
    rows: Iterable[dict[str, Any]],
    condition: Callable[[dict[str, Any]], bool],
    return_key: str,
) -> list[PatternObservation]:
    output: list[PatternObservation] = []
    for row in rows:
        value = row.get(return_key)
        if value is None or not condition(row) or not math.isfinite(float(value)):
            continue
        output.append(PatternObservation(row["day"], float(value)))
    return output


def add_pattern(
    specs: list[PatternSpec],
    *,
    symbol: str,
    family: str,
    name: str,
    signal_time: str,
    holding_period: str,
    side: str,
    notes: str,
    rows: Iterable[dict[str, Any]],
    condition: Callable[[dict[str, Any]], bool],
    return_key: str,
) -> None:
    specs.append(
        PatternSpec(
            pattern_id=make_pattern_id(symbol, family, name, signal_time, holding_period, side),
            symbol=symbol,
            family=family,
            name=name,
            signal_time=signal_time,
            holding_period=holding_period,
            side=side,
            notes=notes,
            observations=observations(rows, condition, return_key),
        )
    )


def build_patterns(sessions: dict[str, list[dict[str, Any]]]) -> list[PatternSpec]:
    specs: list[PatternSpec] = []
    always = lambda _row: True
    for symbol in SYMBOLS:
        rows = sessions[symbol]

        # Baselines.
        for name, signal, hold, key in (
            ("Unconditional power hour", "15:00", "15:00-close", "power_hour_long"),
            ("Unconditional last 30 minutes", "15:30", "15:30-close", "last30_long"),
            ("Unconditional last 15 minutes", "15:45", "15:45-close", "last15_long"),
            ("Unconditional final 5 minutes", "15:55", "15:55-close", "final5_long"),
        ):
            add_pattern(
                specs,
                symbol=symbol,
                family="baseline",
                name=name,
                signal_time=signal,
                holding_period=hold,
                side="long",
                notes="Unconditional underlying ETF return.",
                rows=rows,
                condition=always,
                return_key=key,
            )

        # 3:00 PM trend and range-state continuation.
        threshold_conditions = [
            ("Up by 3 PM", lambda r: r["ret_open_1500"] > 0, "long", "power_hour_long"),
            ("Down by 3 PM", lambda r: r["ret_open_1500"] < 0, "short", "power_hour_short"),
            ("Up at least 0.25% by 3 PM", lambda r: r["ret_open_1500"] >= 0.25, "long", "power_hour_long"),
            ("Down at least 0.25% by 3 PM", lambda r: r["ret_open_1500"] <= -0.25, "short", "power_hour_short"),
            ("Up at least 0.50% by 3 PM", lambda r: r["ret_open_1500"] >= 0.50, "long", "power_hour_long"),
            ("Down at least 0.50% by 3 PM", lambda r: r["ret_open_1500"] <= -0.50, "short", "power_hour_short"),
            ("Up at least 0.75% by 3 PM", lambda r: r["ret_open_1500"] >= 0.75, "long", "power_hour_long"),
            ("Down at least 0.75% by 3 PM", lambda r: r["ret_open_1500"] <= -0.75, "short", "power_hour_short"),
            ("Above VWAP at 3 PM", lambda r: r["above_vwap_1500"], "long", "power_hour_long"),
            ("Below VWAP at 3 PM", lambda r: not r["above_vwap_1500"], "short", "power_hour_short"),
            ("Top 30% of range at 3 PM", lambda r: r["range_pos_1500"] >= 0.70, "long", "power_hour_long"),
            ("Bottom 30% of range at 3 PM", lambda r: r["range_pos_1500"] <= 0.30, "short", "power_hour_short"),
            ("Top 20% of range at 3 PM", lambda r: r["range_pos_1500"] >= 0.80, "long", "power_hour_long"),
            ("Bottom 20% of range at 3 PM", lambda r: r["range_pos_1500"] <= 0.20, "short", "power_hour_short"),
            ("Top 10% of range at 3 PM", lambda r: r["range_pos_1500"] >= 0.90, "long", "power_hour_long"),
            ("Bottom 10% of range at 3 PM", lambda r: r["range_pos_1500"] <= 0.10, "short", "power_hour_short"),
            (
                "Bull trend confluence at 3 PM",
                lambda r: r["ret_open_1500"] >= 0.25 and r["above_vwap_1500"] and r["range_pos_1500"] >= 0.70,
                "long",
                "power_hour_long",
            ),
            (
                "Bear trend confluence at 3 PM",
                lambda r: r["ret_open_1500"] <= -0.25 and not r["above_vwap_1500"] and r["range_pos_1500"] <= 0.30,
                "short",
                "power_hour_short",
            ),
            (
                "Bullish VWAP divergence at 3 PM",
                lambda r: r["ret_open_1500"] < 0 and r["above_vwap_1500"],
                "long",
                "power_hour_long",
            ),
            (
                "Bearish VWAP divergence at 3 PM",
                lambda r: r["ret_open_1500"] > 0 and not r["above_vwap_1500"],
                "short",
                "power_hour_short",
            ),
        ]
        for name, condition, side, key in threshold_conditions:
            add_pattern(
                specs,
                symbol=symbol,
                family="3pm_state",
                name=name,
                signal_time="15:00",
                holding_period="15:00-close",
                side=side,
                notes="Condition uses information available through the 14:55-15:00 bar; entry is next bar open.",
                rows=rows,
                condition=condition,
                return_key=key,
            )

        # Gap context known at the open, evaluated again using 3 PM state.
        gap_patterns = [
            ("Gap up at least 0.30%, still up at 3 PM", lambda r: (r["gap_pct"] or 0) >= 0.30 and r["ret_open_1500"] > 0, "long", "power_hour_long"),
            ("Gap down at least 0.30%, still down at 3 PM", lambda r: (r["gap_pct"] or 0) <= -0.30 and r["ret_open_1500"] < 0, "short", "power_hour_short"),
            ("Gap-up fade by 3 PM", lambda r: (r["gap_pct"] or 0) >= 0.30 and r["ret_open_1500"] < 0, "short", "power_hour_short"),
            ("Gap-down reversal by 3 PM", lambda r: (r["gap_pct"] or 0) <= -0.30 and r["ret_open_1500"] > 0, "long", "power_hour_long"),
        ]
        for name, condition, side, key in gap_patterns:
            add_pattern(
                specs,
                symbol=symbol,
                family="gap_context",
                name=name,
                signal_time="15:00",
                holding_period="15:00-close",
                side=side,
                notes="Gap is measured versus the prior regular-session close.",
                rows=rows,
                condition=condition,
                return_key=key,
            )

        # Late acceleration, deceleration, VWAP reclaim/reject, and volume regimes.
        late_patterns = [
            ("2:30-3:00 momentum positive", lambda r: r["ret_1430_1500"] > 0, "long", "power_hour_long"),
            ("2:30-3:00 momentum negative", lambda r: r["ret_1430_1500"] < 0, "short", "power_hour_short"),
            ("2:30-3:00 momentum at least +0.15%", lambda r: r["ret_1430_1500"] >= 0.15, "long", "power_hour_long"),
            ("2:30-3:00 momentum at most -0.15%", lambda r: r["ret_1430_1500"] <= -0.15, "short", "power_hour_short"),
            (
                "Bull trend accelerating into 3 PM",
                lambda r: r["ret_open_1430"] > 0 and r["ret_1430_1500"] >= 0.15,
                "long",
                "power_hour_long",
            ),
            (
                "Bear trend accelerating into 3 PM",
                lambda r: r["ret_open_1430"] < 0 and r["ret_1430_1500"] <= -0.15,
                "short",
                "power_hour_short",
            ),
            (
                "Strong up day decelerating into 3 PM",
                lambda r: r["ret_open_1430"] >= 0.50 and r["ret_1430_1500"] < 0,
                "short",
                "power_hour_short",
            ),
            (
                "Strong down day bouncing into 3 PM",
                lambda r: r["ret_open_1430"] <= -0.50 and r["ret_1430_1500"] > 0,
                "long",
                "power_hour_long",
            ),
            (
                "VWAP reclaim from 2:30 to 3 PM",
                lambda r: (not r["above_vwap_1430"]) and r["above_vwap_1500"],
                "long",
                "power_hour_long",
            ),
            (
                "VWAP rejection from 2:30 to 3 PM",
                lambda r: r["above_vwap_1430"] and (not r["above_vwap_1500"]),
                "short",
                "power_hour_short",
            ),
            (
                "2-3 PM volume acceleration",
                lambda r: (r["vol_ratio_14_15_vs_13_14"] or 0) >= 1.25 and r["ret_1430_1500"] > 0,
                "long",
                "power_hour_long",
            ),
            (
                "2-3 PM bearish volume acceleration",
                lambda r: (r["vol_ratio_14_15_vs_13_14"] or 0) >= 1.25 and r["ret_1430_1500"] < 0,
                "short",
                "power_hour_short",
            ),
        ]
        for name, condition, side, key in late_patterns:
            add_pattern(
                specs,
                symbol=symbol,
                family="late_momentum",
                name=name,
                signal_time="15:00",
                holding_period="15:00-close",
                side=side,
                notes="Late-momentum condition is complete by 3 PM; entry is next bar open.",
                rows=rows,
                condition=condition,
                return_key=key,
            )

        # Volatility regime measured point-in-time against prior sessions only.
        regime_patterns = [
            ("High realized-range regime by 3 PM", lambda r: (r["pre3_range_regime_ratio"] or 0) >= 1.25, "long", "power_hour_long"),
            ("Low realized-range regime by 3 PM", lambda r: 0 < (r["pre3_range_regime_ratio"] or 0) <= 0.75, "long", "power_hour_long"),
            (
                "High-range bull trend",
                lambda r: (r["pre3_range_regime_ratio"] or 0) >= 1.25 and r["ret_open_1500"] > 0,
                "long",
                "power_hour_long",
            ),
            (
                "High-range bear trend",
                lambda r: (r["pre3_range_regime_ratio"] or 0) >= 1.25 and r["ret_open_1500"] < 0,
                "short",
                "power_hour_short",
            ),
        ]
        for name, condition, side, key in regime_patterns:
            add_pattern(
                specs,
                symbol=symbol,
                family="volatility_regime",
                name=name,
                signal_time="15:00",
                holding_period="15:00-close",
                side=side,
                notes="Regime compares the range observed by 3 PM with the prior 20-session median full-day range.",
                rows=rows,
                condition=condition,
                return_key=key,
            )

        # 3:30 PM continuation/reversal into the closing half-hour.
        half_hour_patterns = [
            ("Power-hour first half positive", lambda r: r["ret_1500_1530"] > 0, "long", "last30_long"),
            ("Power-hour first half negative", lambda r: r["ret_1500_1530"] < 0, "short", "last30_short"),
            ("First half at least +0.15%", lambda r: r["ret_1500_1530"] >= 0.15, "long", "last30_long"),
            ("First half at most -0.15%", lambda r: r["ret_1500_1530"] <= -0.15, "short", "last30_short"),
            ("Above VWAP at 3:30", lambda r: r["above_vwap_1530"], "long", "last30_long"),
            ("Below VWAP at 3:30", lambda r: not r["above_vwap_1530"], "short", "last30_short"),
            ("Top 20% of range at 3:30", lambda r: r["range_pos_1530"] >= 0.80, "long", "last30_long"),
            ("Bottom 20% of range at 3:30", lambda r: r["range_pos_1530"] <= 0.20, "short", "last30_short"),
            (
                "3 PM bull trend survives through 3:30",
                lambda r: r["ret_open_1500"] >= 0.25 and r["ret_1500_1530"] > 0,
                "long",
                "last30_long",
            ),
            (
                "3 PM bear trend survives through 3:30",
                lambda r: r["ret_open_1500"] <= -0.25 and r["ret_1500_1530"] < 0,
                "short",
                "last30_short",
            ),
            (
                "Up day reverses in first half power hour",
                lambda r: r["ret_open_1500"] >= 0.25 and r["ret_1500_1530"] < 0,
                "short",
                "last30_short",
            ),
            (
                "Down day reverses in first half power hour",
                lambda r: r["ret_open_1500"] <= -0.25 and r["ret_1500_1530"] > 0,
                "long",
                "last30_long",
            ),
            (
                "Last-30-minute volume surge after positive first half",
                lambda r: (r["vol_ratio_last30_vs_first30"] or 0) >= 1.50 and r["ret_1500_1530"] > 0,
                "long",
                "last30_long",
            ),
            (
                "Last-30-minute volume surge after negative first half",
                lambda r: (r["vol_ratio_last30_vs_first30"] or 0) >= 1.50 and r["ret_1500_1530"] < 0,
                "short",
                "last30_short",
            ),
        ]
        for name, condition, side, key in half_hour_patterns:
            add_pattern(
                specs,
                symbol=symbol,
                family="closing_half_hour",
                name=name,
                signal_time="15:30",
                holding_period="15:30-close",
                side=side,
                notes="Condition is complete at 3:30 PM; entry is next bar open.",
                rows=rows,
                condition=condition,
                return_key=key,
            )

        # Final 15-minute auction behavior.
        final15_patterns = [
            ("3:30-3:45 positive", lambda r: r["ret_1530_1545"] > 0, "long", "last15_long"),
            ("3:30-3:45 negative", lambda r: r["ret_1530_1545"] < 0, "short", "last15_short"),
            ("Above VWAP at 3:45", lambda r: r["above_vwap_1545"], "long", "last15_long"),
            ("Below VWAP at 3:45", lambda r: not r["above_vwap_1545"], "short", "last15_short"),
            ("Top 10% of range at 3:45", lambda r: r["range_pos_1545"] >= 0.90, "long", "last15_long"),
            ("Bottom 10% of range at 3:45", lambda r: r["range_pos_1545"] <= 0.10, "short", "last15_short"),
            (
                "Final-15 volume surge after positive prior 15",
                lambda r: (r["vol_ratio_last15_vs_prior15"] or 0) >= 1.50 and r["ret_1530_1545"] > 0,
                "long",
                "last15_long",
            ),
            (
                "Final-15 volume surge after negative prior 15",
                lambda r: (r["vol_ratio_last15_vs_prior15"] or 0) >= 1.50 and r["ret_1530_1545"] < 0,
                "short",
                "last15_short",
            ),
        ]
        for name, condition, side, key in final15_patterns:
            add_pattern(
                specs,
                symbol=symbol,
                family="closing_auction",
                name=name,
                signal_time="15:45",
                holding_period="15:45-close",
                side=side,
                notes="Condition is complete at 3:45 PM; entry is next bar open.",
                rows=rows,
                condition=condition,
                return_key=key,
            )

        # Late breakouts and failed breakouts use next-bar execution after the event.
        for name, side, key in (
            ("Close above pre-3 PM high", "long", "breakout_long"),
            ("Close below pre-3 PM low", "short", "breakdown_short"),
            ("Failed break above pre-3 PM high", "short", "failed_breakout_short"),
            ("Failed break below pre-3 PM low", "long", "failed_breakdown_long"),
            ("Close above 2-3 PM range high", "long", "afternoon_breakout_long"),
            ("Close below 2-3 PM range low", "short", "afternoon_breakdown_short"),
        ):
            add_pattern(
                specs,
                symbol=symbol,
                family="late_breakout",
                name=name,
                signal_time="after 15:00",
                holding_period="event-next-bar to close",
                side=side,
                notes="Entry is the next five-minute bar open after the confirming close/pierce.",
                rows=rows,
                condition=lambda r, key=key: r.get(key) is not None,
                return_key=key,
            )

        # Weekday effects.
        for weekday in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday"):
            add_pattern(
                specs,
                symbol=symbol,
                family="weekday",
                name=f"{weekday} power hour",
                signal_time="15:00",
                holding_period="15:00-close",
                side="long",
                notes="Unconditional long power-hour return on the named weekday.",
                rows=rows,
                condition=lambda r, weekday=weekday: r["weekday"] == weekday,
                return_key="power_hour_long",
            )

        # Close-state implications for the overnight and following regular session.
        next_day_conditions = [
            ("Close in top 20% of range", lambda r: r["close_location"] >= 0.80),
            ("Close in bottom 20% of range", lambda r: r["close_location"] <= 0.20),
            ("Close in top 10% of range", lambda r: r["close_location"] >= 0.90),
            ("Close in bottom 10% of range", lambda r: r["close_location"] <= 0.10),
            ("Positive power hour", lambda r: (r["power_hour_long"] or 0) > 0),
            ("Negative power hour", lambda r: (r["power_hour_long"] or 0) < 0),
            ("Power hour at least +0.25%", lambda r: (r["power_hour_long"] or 0) >= 0.25),
            ("Power hour at most -0.25%", lambda r: (r["power_hour_long"] or 0) <= -0.25),
            ("Close above session VWAP", lambda r: r["above_vwap_close"]),
            ("Close below session VWAP", lambda r: not r["above_vwap_close"]),
        ]
        for condition_name, condition in next_day_conditions:
            for hold_name, key in (
                ("close-next open", "overnight_return_pct"),
                ("next open-10:30", "next_open_to_1030_pct"),
                ("next open-close", "next_open_to_close_pct"),
                ("close-next close", "next_close_to_close_pct"),
            ):
                add_pattern(
                    specs,
                    symbol=symbol,
                    family="next_day",
                    name=f"{condition_name}: {hold_name}",
                    signal_time="16:00",
                    holding_period=hold_name,
                    side="long",
                    notes="Raw long underlying return after the prior session's close state; sign reveals continuation versus reversal.",
                    rows=rows,
                    condition=condition,
                    return_key=key,
                )

    build_cross_index_patterns(specs, sessions)
    return specs


def build_cross_index_patterns(specs: list[PatternSpec], sessions: dict[str, list[dict[str, Any]]]) -> None:
    aligned = {
        day: {symbol: row for symbol in SYMBOLS for row in sessions[symbol] if row["day"] == day}
        for day in sorted(set.intersection(*[{r["day"] for r in sessions[s]} for s in SYMBOLS]))
    }
    # Convert aligned date maps into symbol rows with cross-sectional features attached.
    cross_rows: dict[str, list[dict[str, Any]]] = {symbol: [] for symbol in SYMBOLS}
    for day, bundle in aligned.items():
        returns = {symbol: bundle[symbol]["ret_open_1500"] for symbol in SYMBOLS}
        vwaps = {symbol: bundle[symbol]["above_vwap_1500"] for symbol in SYMBOLS}
        leader = max(SYMBOLS, key=lambda symbol: returns[symbol])
        lagger = min(SYMBOLS, key=lambda symbol: returns[symbol])
        dispersion = max(returns.values()) - min(returns.values())
        for symbol in SYMBOLS:
            row = dict(bundle[symbol])
            row.update(
                {
                    "all_up_3pm": all(value > 0 for value in returns.values()),
                    "all_down_3pm": all(value < 0 for value in returns.values()),
                    "all_above_vwap_3pm": all(vwaps.values()),
                    "all_below_vwap_3pm": not any(vwaps.values()),
                    "positive_count_3pm": sum(value > 0 for value in returns.values()),
                    "above_vwap_count_3pm": sum(vwaps.values()),
                    "cross_dispersion_3pm": dispersion,
                    "cross_leader_3pm": leader,
                    "cross_lagger_3pm": lagger,
                    "qqq_minus_spy_3pm": returns["QQQ"] - returns["SPY"],
                    "iwm_minus_spy_3pm": returns["IWM"] - returns["SPY"],
                    "all_first_half_up": all(bundle[item]["ret_1500_1530"] > 0 for item in SYMBOLS),
                    "all_first_half_down": all(bundle[item]["ret_1500_1530"] < 0 for item in SYMBOLS),
                    "first_half_up_count": sum(bundle[item]["ret_1500_1530"] > 0 for item in SYMBOLS),
                    "all_down_3pm_then_bounce": all(returns[item] <= -0.25 for item in SYMBOLS)
                    and all(bundle[item]["ret_1500_1530"] > 0 for item in SYMBOLS),
                    "all_up_3pm_then_fade": all(returns[item] >= 0.25 for item in SYMBOLS)
                    and all(bundle[item]["ret_1500_1530"] < 0 for item in SYMBOLS),
                    "majority_down_then_bounce": sum(returns[item] <= -0.25 for item in SYMBOLS) >= 2
                    and sum(bundle[item]["ret_1500_1530"] > 0 for item in SYMBOLS) >= 2,
                    "majority_up_then_fade": sum(returns[item] >= 0.25 for item in SYMBOLS) >= 2
                    and sum(bundle[item]["ret_1500_1530"] < 0 for item in SYMBOLS) >= 2,
                    "all_above_vwap_345": all(bundle[item]["above_vwap_1545"] for item in SYMBOLS),
                    "all_below_vwap_345": all(not bundle[item]["above_vwap_1545"] for item in SYMBOLS),
                }
            )
            cross_rows[symbol].append(row)

    for symbol in SYMBOLS:
        rows = cross_rows[symbol]
        conditions = [
            ("All three ETFs up by 3 PM", lambda r: r["all_up_3pm"], "long", "power_hour_long"),
            ("All three ETFs down by 3 PM", lambda r: r["all_down_3pm"], "short", "power_hour_short"),
            ("All three above VWAP at 3 PM", lambda r: r["all_above_vwap_3pm"], "long", "power_hour_long"),
            ("All three below VWAP at 3 PM", lambda r: r["all_below_vwap_3pm"], "short", "power_hour_short"),
            ("At least two ETFs up by 3 PM", lambda r: r["positive_count_3pm"] >= 2, "long", "power_hour_long"),
            ("At least two ETFs down by 3 PM", lambda r: r["positive_count_3pm"] <= 1, "short", "power_hour_short"),
            ("At least two ETFs above VWAP", lambda r: r["above_vwap_count_3pm"] >= 2, "long", "power_hour_long"),
            ("At least two ETFs below VWAP", lambda r: r["above_vwap_count_3pm"] <= 1, "short", "power_hour_short"),
            ("Low cross-index dispersion at 3 PM", lambda r: r["cross_dispersion_3pm"] <= 0.15, "long", "power_hour_long"),
            ("High cross-index dispersion at 3 PM", lambda r: r["cross_dispersion_3pm"] >= 0.50, "long", "power_hour_long"),
            ("ETF is the 3 PM leader", lambda r, symbol=symbol: r["cross_leader_3pm"] == symbol, "long", "power_hour_long"),
            ("ETF is the 3 PM lagger", lambda r, symbol=symbol: r["cross_lagger_3pm"] == symbol, "long", "power_hour_long"),
            ("QQQ leads SPY by at least 0.30%", lambda r: r["qqq_minus_spy_3pm"] >= 0.30, "long", "power_hour_long"),
            ("QQQ lags SPY by at least 0.30%", lambda r: r["qqq_minus_spy_3pm"] <= -0.30, "long", "power_hour_long"),
            ("IWM leads SPY by at least 0.30%", lambda r: r["iwm_minus_spy_3pm"] >= 0.30, "long", "power_hour_long"),
            ("IWM lags SPY by at least 0.30%", lambda r: r["iwm_minus_spy_3pm"] <= -0.30, "long", "power_hour_long"),
        ]
        for name, condition, side, key in conditions:
            add_pattern(
                specs,
                symbol=symbol,
                family="cross_index",
                name=name,
                signal_time="15:00",
                holding_period="15:00-close",
                side=side,
                notes="Condition uses SPY, QQQ, and IWM jointly through 3 PM; entry is next bar open.",
                rows=rows,
                condition=condition,
                return_key=key,
            )

        closing_cross_conditions = [
            ("All three positive from 3:00 to 3:30", lambda r: r["all_first_half_up"], "long", "last30_long"),
            ("All three negative from 3:00 to 3:30", lambda r: r["all_first_half_down"], "short", "last30_short"),
            ("At least two positive from 3:00 to 3:30", lambda r: r["first_half_up_count"] >= 2, "long", "last30_long"),
            ("At least two negative from 3:00 to 3:30", lambda r: r["first_half_up_count"] <= 1, "short", "last30_short"),
            ("All three down by 3 PM, then bounce to 3:30", lambda r: r["all_down_3pm_then_bounce"], "long", "last30_long"),
            ("All three up by 3 PM, then fade to 3:30", lambda r: r["all_up_3pm_then_fade"], "short", "last30_short"),
            ("Majority down by 3 PM, then majority bounce", lambda r: r["majority_down_then_bounce"], "long", "last30_long"),
            ("Majority up by 3 PM, then majority fade", lambda r: r["majority_up_then_fade"], "short", "last30_short"),
        ]
        for name, condition, side, key in closing_cross_conditions:
            add_pattern(
                specs,
                symbol=symbol,
                family="cross_index_330",
                name=name,
                signal_time="15:30",
                holding_period="15:30-close",
                side=side,
                notes="Condition uses all three ETFs through 3:30 PM; entry is the next five-minute bar open.",
                rows=rows,
                condition=condition,
                return_key=key,
            )

        auction_cross_conditions = [
            ("All three above VWAP at 3:45", lambda r: r["all_above_vwap_345"], "long", "last15_long"),
            ("All three below VWAP at 3:45", lambda r: r["all_below_vwap_345"], "short", "last15_short"),
        ]
        for name, condition, side, key in auction_cross_conditions:
            add_pattern(
                specs,
                symbol=symbol,
                family="cross_index_345",
                name=name,
                signal_time="15:45",
                holding_period="15:45-close",
                side=side,
                notes="Condition uses all three ETF VWAP states through 3:45 PM; entry is next bar open.",
                rows=rows,
                condition=condition,
                return_key=key,
            )


def hac_mean_se(values: np.ndarray, max_lag: int = 5) -> float | None:
    n = len(values)
    if n < 3:
        return None
    demeaned = values - values.mean()
    gamma0 = float(np.dot(demeaned, demeaned) / n)
    long_run = gamma0
    lag_cap = min(max_lag, n - 1)
    for lag in range(1, lag_cap + 1):
        gamma = float(np.dot(demeaned[lag:], demeaned[:-lag]) / n)
        weight = 1.0 - lag / (lag_cap + 1.0)
        long_run += 2.0 * weight * gamma
    variance = max(long_run / n, 0.0)
    return math.sqrt(variance)


def block_bootstrap_ci(values: np.ndarray, seed: int) -> tuple[float | None, float | None]:
    n = len(values)
    if n < 5:
        return None, None
    rng = np.random.default_rng(seed)
    blocks = math.ceil(n / BLOCK_LENGTH)
    starts = rng.integers(0, n, size=(BOOTSTRAP_REPS, blocks))
    offsets = np.arange(BLOCK_LENGTH)
    indices = (starts[:, :, None] + offsets[None, None, :]) % n
    indices = indices.reshape(BOOTSTRAP_REPS, -1)[:, :n]
    means = values[indices].mean(axis=1)
    low, high = np.quantile(means, [0.025, 0.975])
    return float(low), float(high)


def evaluate_pattern(spec: PatternSpec, sample: str, start_day: str | None) -> PatternResult:
    obs = [row for row in spec.observations if start_day is None or row.day >= start_day]
    values = np.asarray([row.return_pct for row in obs], dtype=float)
    n = len(values)
    empty = PatternResult(
        pattern_id=spec.pattern_id,
        sample=sample,
        symbol=spec.symbol,
        family=spec.family,
        name=spec.name,
        signal_time=spec.signal_time,
        holding_period=spec.holding_period,
        side=spec.side,
        notes=spec.notes,
        n=n,
        mean_return_pct=None,
        median_return_pct=None,
        net_mean_return_pct=None,
        win_rate_pct=None,
        profit_factor=None,
        std_pct=None,
        hac_se_pct=None,
        hac_t_stat=None,
        hac_p_value=None,
        bootstrap_ci_low_pct=None,
        bootstrap_ci_high_pct=None,
        best_return_pct=None,
        worst_return_pct=None,
    )
    if n == 0:
        return empty
    mean_return = float(values.mean())
    median_return = float(np.median(values))
    std = float(values.std(ddof=1)) if n > 1 else 0.0
    se = hac_mean_se(values)
    t_stat = mean_return / se if se and se > 0 else None
    p_value = float(2.0 * stats.t.sf(abs(t_stat), df=max(n - 1, 1))) if t_stat is not None else None
    seed = int(hashlib.sha256(f"{spec.pattern_id}|{sample}".encode()).hexdigest()[:8], 16)
    ci_low, ci_high = block_bootstrap_ci(values, seed)
    gains = values[values > 0].sum()
    losses = -values[values < 0].sum()
    profit_factor = float(gains / losses) if losses > 0 else (math.inf if gains > 0 else None)
    return PatternResult(
        **{key: getattr(empty, key) for key in (
            "pattern_id", "sample", "symbol", "family", "name", "signal_time",
            "holding_period", "side", "notes", "n"
        )},
        mean_return_pct=mean_return,
        median_return_pct=median_return,
        net_mean_return_pct=mean_return - ROUND_TRIP_COST_PCT,
        win_rate_pct=float((values > 0).mean() * 100.0),
        profit_factor=profit_factor,
        std_pct=std,
        hac_se_pct=se,
        hac_t_stat=t_stat,
        hac_p_value=p_value,
        bootstrap_ci_low_pct=ci_low,
        bootstrap_ci_high_pct=ci_high,
        best_return_pct=float(values.max()),
        worst_return_pct=float(values.min()),
    )


def apply_bh_fdr(results: list[PatternResult]) -> None:
    eligible = [
        (index, row.hac_p_value)
        for index, row in enumerate(results)
        if row.n >= MIN_REPORT_N and row.hac_p_value is not None
    ]
    if not eligible:
        return
    ordered = sorted(eligible, key=lambda item: item[1])
    m = len(ordered)
    q_values = [1.0] * m
    running = 1.0
    for rank_index in range(m - 1, -1, -1):
        _row_index, p_value = ordered[rank_index]
        rank = rank_index + 1
        running = min(running, p_value * m / rank)
        q_values[rank_index] = running
    for (row_index, _), q_value in zip(ordered, q_values):
        results[row_index].fdr_q_value = float(min(1.0, q_value))


def attach_stability(full_results: list[PatternResult], recent_results: list[PatternResult]) -> None:
    recent_by_id = {row.pattern_id: row for row in recent_results}
    for row in full_results:
        recent = recent_by_id.get(row.pattern_id)
        if recent is None:
            continue
        row.recent_n = recent.n
        row.recent_net_mean_pct = recent.net_mean_return_pct
        row.recent_win_rate_pct = recent.win_rate_pct
        if row.net_mean_return_pct is None or recent.net_mean_return_pct is None:
            continue
        row.stable_sign = (row.net_mean_return_pct > 0) == (recent.net_mean_return_pct > 0)
        if row.stable_sign and row.net_mean_return_pct > 0 and recent.net_mean_return_pct > 0:
            row.robustness_score = min(row.net_mean_return_pct, recent.net_mean_return_pct) * math.sqrt(min(row.n, recent.n))
        else:
            row.robustness_score = 0.0


def feature_rows_for_csv(sessions: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    excluded = {"bars"}
    rows: list[dict[str, Any]] = []
    for symbol in SYMBOLS:
        for row in sessions[symbol]:
            rows.append({key: value for key, value in row.items() if key not in excluded})
    return rows


def correlation_diagnostics(sessions: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    diagnostics: dict[str, Any] = {"per_symbol": {}, "cross_symbol_power_hour_correlation": {}}
    for symbol in SYMBOLS:
        rows = sessions[symbol]
        x = np.asarray([r["ret_open_1500"] for r in rows], dtype=float)
        y = np.asarray([r["power_hour_long"] for r in rows], dtype=float)
        z = np.asarray([r["ret_1500_1530"] for r in rows], dtype=float)
        last = np.asarray([r["last30_long"] for r in rows], dtype=float)
        diagnostics["per_symbol"][symbol] = {
            "three_pm_return_vs_power_hour_pearson": float(np.corrcoef(x, y)[0, 1]),
            "first_half_power_hour_vs_last_half_pearson": float(np.corrcoef(z, last)[0, 1]),
            "power_hour_autocorrelation_lag1": float(np.corrcoef(y[:-1], y[1:])[0, 1]),
        }
    by_symbol = {
        symbol: np.asarray([r["power_hour_long"] for r in sessions[symbol]], dtype=float)
        for symbol in SYMBOLS
    }
    for left in SYMBOLS:
        diagnostics["cross_symbol_power_hour_correlation"][left] = {}
        for right in SYMBOLS:
            diagnostics["cross_symbol_power_hour_correlation"][left][right] = float(
                np.corrcoef(by_symbol[left], by_symbol[right])[0, 1]
            )
    return diagnostics


def summarize_data(sessions: dict[str, list[dict[str, Any]]], db_path: Path) -> dict[str, Any]:
    coverage = {}
    for symbol in SYMBOLS:
        rows = sessions[symbol]
        coverage[symbol] = {
            "sessions": len(rows),
            "start": rows[0]["day"] if rows else None,
            "end": rows[-1]["day"] if rows else None,
            "bars_per_session": 78,
            "total_regular_bars": len(rows) * 78,
        }
    return {
        "provider": "Alpaca SIP",
        "database": str(db_path),
        "adjustment": "all (split and dividend adjusted)",
        "timeframe": "5Min",
        "session": "09:30-16:00 America/New_York",
        "coverage": coverage,
        "round_trip_cost_pct": ROUND_TRIP_COST_PCT,
        "bootstrap": {"method": "circular block", "block_length_sessions": BLOCK_LENGTH, "repetitions": BOOTSTRAP_REPS},
        "inference": "Newey-West/HAC mean standard error with lag 5; two-sided t reference; BH FDR across tested patterns.",
    }


def write_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def max_drawdown_pct(net_returns_pct: Sequence[float]) -> float:
    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for value in net_returns_pct:
        equity *= 1.0 + value / 100.0
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = min(max_drawdown, (equity / peak - 1.0) * 100.0)
    return max_drawdown


def max_losing_streak(net_returns_pct: Sequence[float]) -> int:
    current = maximum = 0
    for value in net_returns_pct:
        if value < 0:
            current += 1
            maximum = max(maximum, current)
        else:
            current = 0
    return maximum


def build_discovery_validation(
    specs: Sequence[PatternSpec],
    full_results: Sequence[PatternResult],
    recent_start: str,
) -> list[dict[str, Any]]:
    specs_by_id = {row.pattern_id: row for row in specs}
    validation: list[dict[str, Any]] = []
    for result in full_results:
        if result.n < MIN_REPORT_N or result.fdr_q_value is None or result.fdr_q_value > 0.10:
            continue
        spec = specs_by_id[result.pattern_id]
        multiplier = 1.0 if (result.mean_return_pct or 0.0) >= 0 else -1.0
        actionable_side = spec.side
        if multiplier < 0:
            actionable_side = "short" if spec.side == "long" else "long"
        observations = [(row.day, row.return_pct * multiplier) for row in spec.observations]
        raw = [value for _day, value in observations]
        net = [value - ROUND_TRIP_COST_PCT for value in raw]
        recent_raw = [value for day, value in observations if day >= recent_start]
        midpoint = len(raw) // 2
        sorted_raw = sorted(raw)
        without_best3 = sorted_raw[:-3] if len(sorted_raw) > 3 else sorted_raw
        monthly: dict[str, list[float]] = defaultdict(list)
        for day, value in observations:
            monthly[day[:7]].append(value)
        leave_one_month_out = {}
        for month in sorted(monthly):
            values = [value for day, value in observations if day[:7] != month]
            leave_one_month_out[month] = float(np.mean(values) - ROUND_TRIP_COST_PCT) if values else None
        validation.append(
            {
                "pattern_id": result.pattern_id,
                "symbol": result.symbol,
                "family": result.family,
                "original_tested_name": result.name,
                "original_tested_side": result.side,
                "actionable_side": actionable_side,
                "interpretation": "as-tested" if multiplier > 0 else "invert-significant-continuation-failure",
                "n": len(raw),
                "raw_mean_pct": float(np.mean(raw)),
                "net_mean_2bp_pct": float(np.mean(raw) - 0.02),
                "net_mean_5bp_pct": float(np.mean(raw) - 0.05),
                "net_mean_10bp_pct": float(np.mean(raw) - 0.10),
                "median_pct": float(np.median(raw)),
                "win_rate_pct": float(np.mean(np.asarray(raw) > 0) * 100.0),
                "compounded_return_2bp_pct": float((np.prod(1.0 + np.asarray(net) / 100.0) - 1.0) * 100.0),
                "max_drawdown_2bp_pct": max_drawdown_pct(net),
                "max_losing_streak": max_losing_streak(net),
                "first_half_net_mean_pct": float(np.mean(raw[:midpoint]) - ROUND_TRIP_COST_PCT) if midpoint else None,
                "second_half_net_mean_pct": float(np.mean(raw[midpoint:]) - ROUND_TRIP_COST_PCT) if raw[midpoint:] else None,
                "recent_n": len(recent_raw),
                "recent_net_mean_pct": float(np.mean(recent_raw) - ROUND_TRIP_COST_PCT) if recent_raw else None,
                "exclude_best_3_net_mean_pct": float(np.mean(without_best3) - ROUND_TRIP_COST_PCT) if without_best3 else None,
                "leave_one_month_out_min_net_pct": min(value for value in leave_one_month_out.values() if value is not None),
                "leave_one_month_out_max_net_pct": max(value for value in leave_one_month_out.values() if value is not None),
                "monthly_raw_means_pct": {month: float(np.mean(values)) for month, values in sorted(monthly.items())},
                "hac_p_value_original_test": result.hac_p_value,
                "fdr_q_value_original_test": result.fdr_q_value,
            }
        )
    validation.sort(key=lambda row: row["fdr_q_value_original_test"])
    return validation


def fmt(value: float | None, digits: int = 3) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def markdown_table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> list[str]:
    output = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for row in rows:
        output.append("| " + " | ".join(str(value) for value in row) + " |")
    return output


def build_report(
    data_summary: dict[str, Any],
    full_results: list[PatternResult],
    recent_results: list[PatternResult],
    diagnostics: dict[str, Any],
    discovery_validation: list[dict[str, Any]],
) -> str:
    full_by_id = {row.pattern_id: row for row in full_results}
    lines = [
        "# SPY / QQQ / IWM End-of-Day Pattern Lab",
        "",
        f"**Generated:** {utcnow()}",
        "",
        "## Scope and methodology",
        "",
        (
            f"The primary sample contains {data_summary['coverage']['SPY']['sessions']} complete regular sessions "
            f"from {data_summary['coverage']['SPY']['start']} through {data_summary['coverage']['SPY']['end']} "
            "for each ETF. The recent stability sample covers the final 90 calendar days. Signals use only "
            "information available at the stated checkpoint; executable studies enter at the next five-minute "
            "bar open and exit at the stated horizon."
        ),
        "",
        f"Underlying ETF results include a {ROUND_TRIP_COST_PCT:.2f}% ({ROUND_TRIP_COST_PCT * 100:.0f} bp) round-trip cost in the net-mean field. This is not an option backtest.",
        "",
    ]
    coverage_rows = []
    for symbol in SYMBOLS:
        item = data_summary["coverage"][symbol]
        coverage_rows.append((symbol, item["sessions"], item["start"], item["end"], item["total_regular_bars"]))
    lines.extend(markdown_table(("ETF", "Sessions", "Start", "End", "RTH 5m bars"), coverage_rows))

    lines.extend(["", "## Baseline closing-window behavior", ""])
    baseline_rows = []
    for symbol in SYMBOLS:
        for name in (
            "Unconditional power hour",
            "Unconditional last 30 minutes",
            "Unconditional last 15 minutes",
            "Unconditional final 5 minutes",
        ):
            row = next(r for r in full_results if r.symbol == symbol and r.name == name)
            baseline_rows.append(
                (
                    symbol,
                    name.replace("Unconditional ", ""),
                    row.n,
                    f"{fmt(row.mean_return_pct)}%",
                    f"{fmt(row.net_mean_return_pct)}%",
                    f"{fmt(row.win_rate_pct, 1)}%",
                    f"{fmt(row.bootstrap_ci_low_pct)}% to {fmt(row.bootstrap_ci_high_pct)}%",
                )
            )
    lines.extend(markdown_table(("ETF", "Window", "N", "Mean", "Net mean", "Win rate", "Block-bootstrap 95% CI"), baseline_rows))

    robust = [
        row for row in full_results
        if row.n >= 20
        and (row.recent_n or 0) >= 10
        and (row.robustness_score or 0) > 0
        and row.family not in {"baseline", "next_day", "weekday"}
    ]
    robust.sort(key=lambda row: row.robustness_score or 0, reverse=True)
    lines.extend(["", "## Strongest same-sign patterns in both samples", ""])
    robust_rows = []
    for row in robust[:25]:
        robust_rows.append(
            (
                row.symbol,
                row.family,
                row.name,
                row.n,
                f"{fmt(row.net_mean_return_pct)}%",
                row.recent_n,
                f"{fmt(row.recent_net_mean_pct)}%",
                f"{fmt(row.win_rate_pct, 1)}%",
                fmt(row.fdr_q_value),
            )
        )
    if robust_rows:
        lines.extend(markdown_table(("ETF", "Family", "Pattern", "6m N", "6m net", "3m N", "3m net", "6m wins", "FDR q"), robust_rows))
    else:
        lines.append("No pattern met the minimum sample and same-sign positive-net criteria in both windows.")

    significant = [
        row for row in full_results
        if row.n >= MIN_REPORT_N and row.fdr_q_value is not None and row.fdr_q_value <= 0.10
    ]
    significant.sort(key=lambda row: row.fdr_q_value or 1)
    lines.extend(["", "## Multiple-testing result", ""])
    if significant:
        sig_rows = [
            (
                row.symbol,
                row.name,
                row.n,
                f"{fmt(row.net_mean_return_pct)}%",
                fmt(row.hac_p_value),
                fmt(row.fdr_q_value),
                f"{fmt(row.bootstrap_ci_low_pct)}% to {fmt(row.bootstrap_ci_high_pct)}%",
            )
            for row in significant
        ]
        lines.extend(markdown_table(("ETF", "Pattern", "N", "Net mean", "HAC p", "FDR q", "Bootstrap CI"), sig_rows))
    else:
        lines.append("No tested pattern survived a 10% Benjamini-Hochberg false-discovery threshold. Treat rankings as exploratory, not established edge.")

    lines.extend(["", "## Actionable interpretation of FDR discoveries", ""])
    discovery_rows = []
    for row in discovery_validation:
        discovery_rows.append(
            (
                row["symbol"],
                row["original_tested_name"],
                row["actionable_side"],
                row["n"],
                f"{fmt(row['net_mean_2bp_pct'])}%",
                f"{fmt(row['net_mean_5bp_pct'])}%",
                f"{fmt(row['net_mean_10bp_pct'])}%",
                f"{fmt(row['recent_net_mean_pct'])}%",
                f"{fmt(row['exclude_best_3_net_mean_pct'])}%",
                f"{fmt(row['max_drawdown_2bp_pct'])}%",
            )
        )
    lines.extend(markdown_table(("ETF", "Observed condition", "Trade interpretation", "N", "Net @2bp", "Net @5bp", "Net @10bp", "Recent net", "Ex-best-3 net", "MDD"), discovery_rows))
    lines.append("")
    lines.append("A negative mean in the original directional hypothesis is interpreted by taking the opposite side. This does not alter the original p-value; it makes the economic implication explicit.")

    lines.extend(["", "## Best patterns by ETF and family", ""])
    family_rows = []
    for symbol in SYMBOLS:
        families = sorted({row.family for row in full_results if row.symbol == symbol and row.family not in {"baseline"}})
        for family in families:
            candidates = [
                row for row in full_results
                if row.symbol == symbol and row.family == family and row.n >= MIN_REPORT_N and row.net_mean_return_pct is not None
            ]
            if not candidates:
                continue
            best = max(candidates, key=lambda row: row.net_mean_return_pct or -999)
            family_rows.append(
                (
                    symbol,
                    family,
                    best.name,
                    best.n,
                    f"{fmt(best.net_mean_return_pct)}%",
                    f"{fmt(best.win_rate_pct, 1)}%",
                    f"{fmt(best.recent_net_mean_pct)}%" if best.recent_net_mean_pct is not None else "n/a",
                    fmt(best.fdr_q_value),
                )
            )
    lines.extend(markdown_table(("ETF", "Family", "Best tested condition", "N", "6m net", "Win rate", "3m net", "FDR q"), family_rows))

    next_day = [
        row for row in full_results
        if row.family == "next_day" and row.n >= 25 and row.net_mean_return_pct is not None
    ]
    next_day.sort(key=lambda row: abs(row.net_mean_return_pct or 0), reverse=True)
    lines.extend(["", "## Largest next-session conditional effects", ""])
    next_rows = [
        (
            row.symbol,
            row.name,
            row.n,
            f"{fmt(row.mean_return_pct)}%",
            f"{fmt(row.win_rate_pct, 1)}%",
            f"{fmt(row.recent_net_mean_pct)}%" if row.recent_net_mean_pct is not None else "n/a",
            fmt(row.fdr_q_value),
        )
        for row in next_day[:20]
    ]
    lines.extend(markdown_table(("ETF", "Prior close condition / horizon", "N", "Raw mean", "Win rate", "3m net", "FDR q"), next_rows))

    lines.extend(["", "## Correlation diagnostics", ""])
    corr_rows = []
    for symbol in SYMBOLS:
        item = diagnostics["per_symbol"][symbol]
        corr_rows.append(
            (
                symbol,
                fmt(item["three_pm_return_vs_power_hour_pearson"]),
                fmt(item["first_half_power_hour_vs_last_half_pearson"]),
                fmt(item["power_hour_autocorrelation_lag1"]),
            )
        )
    lines.extend(markdown_table(("ETF", "3 PM trend vs power hour", "3-3:30 vs 3:30-close", "Power-hour lag-1"), corr_rows))
    lines.append("")
    lines.append("Cross-ETF power-hour correlation:")
    cross_rows = []
    for left in SYMBOLS:
        cross_rows.append((left,) + tuple(fmt(diagnostics["cross_symbol_power_hour_correlation"][left][right]) for right in SYMBOLS))
    lines.extend(markdown_table(("ETF",) + SYMBOLS, cross_rows))

    lines.extend(
        [
            "",
            "## Interpretation constraints",
            "",
            f"- The sample is recent and deliberately market-regime focused, but {data_summary['coverage']['SPY']['sessions']} sessions is still small for hundreds of conditional tests.",
            "- Five-minute OHLCV bars are not executable bid/ask quotes. Results use next-bar opens and a simple ETF cost assumption.",
            "- Conditions with small N are retained in the CSV for discovery but should not drive deployment.",
            "- Option returns can diverge sharply because of implied volatility, theta, gamma, strike selection, and spreads.",
            "- A pattern that does not survive FDR may still be useful as a feature, but it is not standalone evidence of edge.",
            "",
            "## Output guide",
            "",
            "`all_patterns.csv` contains every tested pattern and both-window stability fields. `daily_features.csv` contains the complete point-in-time feature set for independent validation. `report.json` contains the same results and diagnostics in machine-readable form.",
        ]
    )
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace) -> dict[str, Any]:
    db_path = Path(args.database).resolve()
    out_dir = Path(args.output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    sessions = load_sessions(db_path)
    if any(len(sessions[symbol]) < 30 for symbol in SYMBOLS):
        raise RuntimeError("insufficient aligned regular-session history")
    last_day = max(row["day"] for rows in sessions.values() for row in rows)
    analysis_start = (date.fromisoformat(last_day) - timedelta(days=args.full_days)).isoformat()
    sessions = {
        symbol: [row for row in rows if row["day"] >= analysis_start]
        for symbol, rows in sessions.items()
    }
    specs = build_patterns(sessions)
    recent_start = (date.fromisoformat(last_day) - timedelta(days=args.recent_days)).isoformat()

    full_results = [evaluate_pattern(spec, "full", None) for spec in specs]
    recent_results = [evaluate_pattern(spec, "recent", recent_start) for spec in specs]
    apply_bh_fdr(full_results)
    apply_bh_fdr(recent_results)
    attach_stability(full_results, recent_results)

    data_summary = summarize_data(sessions, db_path)
    diagnostics = correlation_diagnostics(sessions)
    discovery_validation = build_discovery_validation(specs, full_results, recent_start)
    report_payload = {
        "generated_at": utcnow(),
        "research_grade": False,
        "research_grade_reason": "Exploratory recent-regime study; no historical NBBO and no out-of-sample future period.",
        "data": data_summary,
        "analysis_sample_start": analysis_start,
        "recent_sample_start": recent_start,
        "pattern_count": len(full_results),
        "patterns_full": [asdict(row) for row in full_results],
        "patterns_recent": [asdict(row) for row in recent_results],
        "diagnostics": diagnostics,
        "discovery_validation": discovery_validation,
        "caveats": [
            "Underlying ETF study only; not an options P/L study.",
            "No historical NBBO; five-minute bars are not executable quotes.",
            "Many hypotheses are tested; use FDR and stability fields.",
            "The three-month window overlaps the six-month sample and is a stability check, not independent validation.",
        ],
    }

    report_json = out_dir / "report.json"
    report_md = out_dir / "report.md"
    patterns_csv = out_dir / "all_patterns.csv"
    recent_csv = out_dir / "all_patterns_recent.csv"
    features_csv = out_dir / "daily_features.csv"
    discoveries_json = out_dir / "fdr_discovery_validation.json"
    report_json.write_text(json.dumps(report_payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    report_md.write_text(
        build_report(data_summary, full_results, recent_results, diagnostics, discovery_validation),
        encoding="utf-8",
    )
    write_csv(patterns_csv, [asdict(row) for row in full_results])
    write_csv(recent_csv, [asdict(row) for row in recent_results])
    write_csv(features_csv, feature_rows_for_csv(sessions))
    discoveries_json.write_text(json.dumps(discovery_validation, indent=2, sort_keys=True), encoding="utf-8")

    summary = {
        "generated_at": report_payload["generated_at"],
        "database": str(db_path),
        "output_dir": str(out_dir),
        "pattern_count": len(full_results),
        "analysis_start": analysis_start,
        "recent_start": recent_start,
        "sessions": {symbol: len(sessions[symbol]) for symbol in SYMBOLS},
        "fdr_10pct_discoveries": sum(
            row.n >= MIN_REPORT_N and row.fdr_q_value is not None and row.fdr_q_value <= 0.10
            for row in full_results
        ),
        "files": {
            "report_json": str(report_json),
            "report_markdown": str(report_md),
            "patterns_csv": str(patterns_csv),
            "recent_patterns_csv": str(recent_csv),
            "daily_features_csv": str(features_csv),
            "fdr_discovery_validation": str(discoveries_json),
        },
    }
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze end-of-day patterns in SPY, QQQ, and IWM.")
    parser.add_argument("--database", default=str(DEFAULT_DB))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUT))
    parser.add_argument("--full-days", type=int, default=180)
    parser.add_argument("--recent-days", type=int, default=90)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    print(json.dumps(run(args), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
