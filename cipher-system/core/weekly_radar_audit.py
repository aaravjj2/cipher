"""Retrospective, condition-aware audit for a published weekly radar.

This is intentionally not a newsletter "accuracy" score. Conditional bullish
and bearish paths are evaluated separately, both-trigger weeks are labelled,
and hypothetical entries occur at the next 5-minute open after confirmation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

import pandas as pd


@dataclass(frozen=True, slots=True)
class RadarIdea:
    ticker: str
    pivot: float
    upside_targets: tuple[float, ...] = ()
    downside_targets: tuple[float, ...] = ()
    bullish_only: bool = False
    option_note: str = ""


def _bars(rows: Iterable[Mapping[str, Any]], start: str, end: str) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    frame["timestamp"] = pd.to_datetime(frame.get("time", frame.get("t")), utc=True)
    for target, choices in {
        "open": ("open", "o"), "high": ("high", "h"), "low": ("low", "l"),
        "close": ("close", "c"),
    }.items():
        source = next(name for name in choices if name in frame)
        frame[target] = pd.to_numeric(frame[source], errors="coerce")
    minute = frame["timestamp"].dt.hour * 60 + frame["timestamp"].dt.minute
    day = frame["timestamp"].dt.strftime("%Y-%m-%d")
    return frame.loc[day.between(start, end) & minute.between(13 * 60 + 30, 19 * 60 + 55)].sort_values("timestamp").reset_index(drop=True)


def _path(frame: pd.DataFrame, pivot: float, targets: tuple[float, ...], direction: int) -> dict[str, Any] | None:
    crossed = frame["close"] >= pivot if direction > 0 else frame["close"] < pivot
    if not crossed.any():
        return None
    signal_index = int(crossed.idxmax())
    entry_index = min(signal_index + 1, len(frame) - 1)
    entry = float(frame.loc[entry_index, "open"])
    after = frame.loc[signal_index:]
    week_close = float(frame.iloc[-1]["close"])
    reached = [
        level for level in targets
        if (float(after["high"].max()) >= level if direction > 0 else float(after["low"].min()) <= level)
    ]
    return {
        "signal_timestamp": frame.loc[signal_index, "timestamp"].isoformat(),
        "signal_close": float(frame.loc[signal_index, "close"]),
        "entry_timestamp": frame.loc[entry_index, "timestamp"].isoformat(),
        "next_bar_entry": entry,
        "week_close": week_close,
        "return_to_week_close_pct": direction * (week_close / entry - 1) * 100,
        "maximum_favorable_excursion_pct": (
            float(after["high"].max()) / entry - 1 if direction > 0
            else 1 - float(after["low"].min()) / entry
        ) * 100,
        "maximum_adverse_excursion_pct": (
            float(after["low"].min()) / entry - 1 if direction > 0
            else 1 - float(after["high"].max()) / entry
        ) * 100,
        "targets_reached": reached,
    }


def evaluate_radar(
    ideas: Iterable[RadarIdea],
    bars_by_ticker: Mapping[str, Iterable[Mapping[str, Any]]],
    *,
    start: str,
    end: str,
) -> dict[str, Any]:
    rows = []
    for idea in ideas:
        frame = _bars(bars_by_ticker.get(idea.ticker, ()), start, end)
        if frame.empty:
            rows.append({"ticker": idea.ticker, "status": "NO_BARS"})
            continue
        bullish = _path(frame, idea.pivot, idea.upside_targets, 1)
        bearish = None if idea.bullish_only else _path(frame, idea.pivot, idea.downside_targets, -1)
        if bullish and bearish:
            label = "BOTH_DIRECTIONS_TRIGGERED"
        elif bullish:
            label = "BULLISH_TRIGGERED"
        elif bearish:
            label = "BEARISH_TRIGGERED"
        else:
            label = "NOT_TRIGGERED"
        rows.append({
            "ticker": idea.ticker,
            "pivot": idea.pivot,
            "status": label,
            "week_low": float(frame["low"].min()),
            "week_high": float(frame["high"].max()),
            "bullish": bullish,
            "bearish": bearish,
            "option_note": idea.option_note,
        })
    triggered_paths = [path for row in rows for path in (row.get("bullish"), row.get("bearish")) if path]
    profitable = [path for path in triggered_paths if path["return_to_week_close_pct"] > 0]
    return {
        "question": "How did the stated conditional radar levels perform during the week?",
        "window": {"start": start, "end": end},
        "method": "first confirmed 5-minute close; hypothetical next-bar open entry; marked at Friday RTH close",
        "ideas": rows,
        "summary": {
            "ideas": len(rows),
            "not_triggered": sum(row["status"] == "NOT_TRIGGERED" for row in rows),
            "both_directions_triggered": sum(row["status"] == "BOTH_DIRECTIONS_TRIGGERED" for row in rows),
            "triggered_conditional_paths": len(triggered_paths),
            "profitable_to_week_close_paths": len(profitable),
        },
        "caveats": [
            "A two-sided pivot note is conditional; bullish and bearish paths are not one simultaneous trade.",
            "A first-bar trigger can begin beyond the first target; target-touch statistics are descriptive.",
            "No stop, sizing, slippage, or option premium is inferred from the email.",
        ],
        "execution_authority": False,
    }
