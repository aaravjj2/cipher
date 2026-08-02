"""Backtest the final watchlist management plan on supplied timestamped alerts.

Rules:
- Enter at the next one-minute option bar open strictly after the alert minute.
- Initial emergency stop at -35% of option premium.
- Sell 50% at whichever occurs first: +25% premium or first posted update.
- After the first scale, remaining stop moves to entry.
- Sell 25% at whichever occurs first: +50% premium or second posted update.
- Exit all remaining size on the first completed 5-minute underlying signal where
  either EMA(5)/EMA(13) or MACD(12,26,9) turns adverse after being favorable.
- A third posted update also exits all remaining size.
- Indicator/update fills use the next available one-minute option bar open.
- Stop/target ambiguity within an option bar is handled stop-first.

Research only. No broker/account/order APIs are imported or called.
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from core.watchlist_exit_backtest import choose_entry, expiry_end, parse_alerts
from core.watchlist_indicator_exit_backtest import (
    cache_stock_bars,
    ema_macd_signal,
    metrics,
    rth_five_minute,
)

NY = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class ScheduledEvent:
    fill_index: int
    kind: str
    timestamp: pd.Timestamp


def next_option_index(option_bars: pd.DataFrame, after_time: pd.Timestamp, entry_idx: int) -> int | None:
    eligible = option_bars[
        (option_bars.index > entry_idx)
        & (option_bars["timestamp"] > after_time.floor("min"))
    ]
    return int(eligible.index[0]) if not eligible.empty else None


def matching_updates(alerts: pd.DataFrame, trade: Any) -> pd.DataFrame:
    return alerts[
        (alerts["event_type"] == "update")
        & (alerts["ticker"].str.upper() == trade.ticker)
        & (pd.to_numeric(alerts["strike"], errors="coerce") == trade.strike)
        & (alerts["option_type"].str.lower() == trade.option_type)
        & (alerts["expiration"].astype(str) == trade.expiration)
        & (alerts["timestamp_et"] >= pd.Timestamp(trade.alert_time))
    ].sort_values("timestamp_et").reset_index(drop=True)


def build_events(
    option_bars: pd.DataFrame,
    entry_idx: int,
    updates: pd.DataFrame,
    indicator_time: pd.Timestamp | None,
) -> list[ScheduledEvent]:
    events: list[ScheduledEvent] = []
    for update_number, row in enumerate(updates.itertuples(index=False), start=1):
        fill_index = next_option_index(
            option_bars,
            pd.Timestamp(row.timestamp_et).tz_convert("UTC"),
            entry_idx,
        )
        if fill_index is not None:
            events.append(
                ScheduledEvent(
                    fill_index=fill_index,
                    kind=f"UPDATE_{update_number}",
                    timestamp=pd.Timestamp(row.timestamp_et),
                )
            )
    if indicator_time is not None:
        fill_index = next_option_index(option_bars, indicator_time, entry_idx)
        if fill_index is not None:
            events.append(
                ScheduledEvent(
                    fill_index=fill_index,
                    kind="INDICATOR_EXIT",
                    timestamp=indicator_time,
                )
            )
    return sorted(events, key=lambda event: (event.fill_index, event.timestamp, event.kind))


def simulate_final_strategy(
    option_bars: pd.DataFrame,
    entry_idx: int,
    entry_price: float,
    expiration: str,
    updates: pd.DataFrame,
    indicator_time: pd.Timestamp | None,
    *,
    initial_stop_pct: float = -0.35,
    first_target_pct: float = 0.25,
    second_target_pct: float = 0.50,
) -> dict[str, Any]:
    events = build_events(option_bars, entry_idx, updates, indicator_time)
    events_by_index: dict[int, list[ScheduledEvent]] = {}
    for event in events:
        events_by_index.setdefault(event.fill_index, []).append(event)

    end_time = pd.Timestamp(expiry_end(expiration)).tz_convert("UTC")
    path = option_bars[
        (option_bars.index > entry_idx)
        & (option_bars["timestamp"] <= end_time)
    ].copy()
    if path.empty:
        return {"return_pct": None, "reason": "NO_POST_ENTRY_PATH"}

    remaining = 1.0
    realized = 0.0
    first_scaled = False
    second_scaled = False
    active_stop_price = entry_price * (1.0 + initial_stop_pct)
    first_source: str | None = None
    second_source: str | None = None
    first_fill_return: float | None = None
    second_fill_return: float | None = None
    event_log: list[dict[str, Any]] = []

    def finish(row: pd.Series, reason: str, fill_price: float) -> dict[str, Any]:
        nonlocal realized, remaining
        fill_return = fill_price / entry_price - 1.0
        realized += remaining * fill_return
        remaining = 0.0
        return {
            "return_pct": float(realized),
            "reason": reason,
            "exit_time": pd.Timestamp(row["timestamp"]).isoformat(),
            "exit_price": float(fill_price),
            "first_scale_source": first_source,
            "second_scale_source": second_source,
            "first_fill_return": first_fill_return,
            "second_fill_return": second_fill_return,
            "event_log": event_log,
        }

    for idx, row in path.iterrows():
        # Events known before this minute fill at its open. Posted updates and
        # completed 5-minute signals therefore precede this minute's high/low.
        for event in events_by_index.get(int(idx), []):
            open_price = float(row["open"])
            open_return = open_price / entry_price - 1.0
            if event.kind == "UPDATE_1" and not first_scaled:
                allocation = min(0.50, remaining)
                realized += allocation * open_return
                remaining -= allocation
                first_scaled = True
                first_source = "FIRST_UPDATE"
                first_fill_return = open_return
                active_stop_price = entry_price
                event_log.append({"event": event.kind, "fill_return": open_return, "time": pd.Timestamp(row["timestamp"]).isoformat()})
            elif event.kind == "UPDATE_2" and first_scaled and not second_scaled:
                allocation = min(0.25, remaining)
                realized += allocation * open_return
                remaining -= allocation
                second_scaled = True
                second_source = "SECOND_UPDATE"
                second_fill_return = open_return
                event_log.append({"event": event.kind, "fill_return": open_return, "time": pd.Timestamp(row["timestamp"]).isoformat()})
            elif event.kind == "UPDATE_3" and remaining > 0:
                event_log.append({"event": event.kind, "fill_return": open_return, "time": pd.Timestamp(row["timestamp"]).isoformat()})
                return finish(row, "THIRD_UPDATE_EXIT", open_price)
            elif event.kind == "INDICATOR_EXIT" and remaining > 0:
                event_log.append({"event": event.kind, "fill_return": open_return, "time": pd.Timestamp(row["timestamp"]).isoformat()})
                return finish(row, "EMA5_13_OR_MACD_EXIT", open_price)

        if remaining <= 1e-12:
            return {
                "return_pct": float(realized),
                "reason": "FULLY_SCALED",
                "exit_time": pd.Timestamp(row["timestamp"]).isoformat(),
                "exit_price": float(row["open"]),
                "first_scale_source": first_source,
                "second_scale_source": second_source,
                "first_fill_return": first_fill_return,
                "second_fill_return": second_fill_return,
                "event_log": event_log,
            }

        high = float(row["high"])
        low = float(row["low"])
        first_hit = (not first_scaled) and high >= entry_price * (1.0 + first_target_pct)
        second_hit = first_scaled and (not second_scaled) and high >= entry_price * (1.0 + second_target_pct)
        stop_hit = low <= active_stop_price

        # A one-minute OHLC bar cannot reveal whether its stop or target traded
        # first. Conservative handling assumes the stop happened first.
        if stop_hit and (first_hit or second_hit):
            return finish(row, "AMBIGUOUS_STOP_FIRST", active_stop_price)
        if stop_hit:
            return finish(row, "STOP", active_stop_price)

        if first_hit:
            allocation = min(0.50, remaining)
            realized += allocation * first_target_pct
            remaining -= allocation
            first_scaled = True
            first_source = "OPTION_PLUS_25"
            first_fill_return = first_target_pct
            active_stop_price = entry_price
            event_log.append({"event": "OPTION_PLUS_25", "fill_return": first_target_pct, "time": pd.Timestamp(row["timestamp"]).isoformat()})

        if first_scaled and not second_scaled and high >= entry_price * (1.0 + second_target_pct):
            allocation = min(0.25, remaining)
            realized += allocation * second_target_pct
            remaining -= allocation
            second_scaled = True
            second_source = "OPTION_PLUS_50"
            second_fill_return = second_target_pct
            event_log.append({"event": "OPTION_PLUS_50", "fill_return": second_target_pct, "time": pd.Timestamp(row["timestamp"]).isoformat()})

    last = path.iloc[-1]
    return finish(last, "EXPIRY_LAST_BAR", float(last["close"]))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--alerts", type=Path, required=True)
    parser.add_argument("--option-bars-dir", type=Path, required=True)
    parser.add_argument("--underlying-cache-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--feed", choices=["sip", "iex"], default="sip")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    trades, alerts = parse_alerts(args.alerts)
    grouped: dict[str, tuple[datetime, datetime]] = {}
    for trade in trades:
        start = trade.alert_time - timedelta(days=7)
        end = expiry_end(trade.expiration)
        if trade.ticker not in grouped:
            grouped[trade.ticker] = (start, end)
        else:
            prior_start, prior_end = grouped[trade.ticker]
            grouped[trade.ticker] = (min(start, prior_start), max(end, prior_end))

    stock_5m: dict[str, pd.DataFrame] = {}
    for ticker, (start, end) in grouped.items():
        raw = cache_stock_bars(ticker, start, end, args.feed, args.underlying_cache_dir)
        stock_5m[ticker] = rth_five_minute(raw)

    rows: list[dict[str, Any]] = []
    coverage: list[dict[str, Any]] = []
    for trade in trades:
        option_path = args.option_bars_dir / f"{trade.occ_symbol}.csv"
        if not option_path.exists():
            coverage.append({"key": trade.key, "ticker": trade.ticker, "status": "OPTION_BARS_MISSING"})
            continue
        option_bars = pd.read_csv(option_path)
        option_bars["timestamp"] = pd.to_datetime(option_bars["timestamp"], utc=True)
        entry = choose_entry(option_bars, trade.alert_time)
        underlying = stock_5m.get(trade.ticker, pd.DataFrame())
        if entry is None or underlying.empty:
            coverage.append({"key": trade.key, "ticker": trade.ticker, "status": "ENTRY_OR_UNDERLYING_MISSING"})
            continue
        entry_idx, entry_price, entry_time = entry
        updates = matching_updates(alerts, trade)
        indicator = ema_macd_signal(
            underlying,
            entry_time,
            trade.option_type,
            fast=5,
            slow=13,
            exit_mode="either_adverse",
        )
        outcome = simulate_final_strategy(
            option_bars,
            entry_idx,
            entry_price,
            trade.expiration,
            updates,
            indicator.timestamp,
        )
        local_alert = pd.Timestamp(trade.alert_time)
        dte = (datetime.fromisoformat(trade.expiration).date() - local_alert.date()).days
        rows.append(
            {
                "key": trade.key,
                "ticker": trade.ticker,
                "option_type": trade.option_type,
                "strike": trade.strike,
                "expiration": trade.expiration,
                "dte": dte,
                "alert_time_et": local_alert.isoformat(),
                "entry_time_utc": entry_time.isoformat(),
                "entry_price": entry_price,
                "posted_update_count": int(len(updates)),
                "indicator_signal_time": indicator.timestamp.isoformat() if indicator.timestamp is not None else None,
                "indicator_reason": indicator.reason,
                **outcome,
            }
        )
        coverage.append({"key": trade.key, "ticker": trade.ticker, "status": "OK"})

    results = pd.DataFrame(rows).sort_values("alert_time_et")
    coverage_frame = pd.DataFrame(coverage)
    summary = metrics(results)
    summary["wins"] = int((pd.to_numeric(results["return_pct"]) > 0).sum())
    summary["losses"] = int((pd.to_numeric(results["return_pct"]) < 0).sum())
    summary["breakeven"] = int((pd.to_numeric(results["return_pct"]) == 0).sum())
    ordered_returns = pd.to_numeric(results["return_pct"], errors="coerce").dropna().sort_values()
    summary["average_without_best"] = float(ordered_returns.iloc[:-1].mean()) if len(ordered_returns) > 1 else None
    summary["average_without_top3"] = float(ordered_returns.iloc[:-3].mean()) if len(ordered_returns) > 3 else None

    results_out = results.copy()
    results_out["event_log"] = results_out["event_log"].map(lambda value: json.dumps(value, sort_keys=True))
    results_out.to_csv(args.output_dir / "final_strategy_trades.csv", index=False)
    coverage_frame.to_csv(args.output_dir / "coverage.csv", index=False)
    (args.output_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    print(json.dumps({
        "status": "PASS",
        "complete_entry_alerts": len(trades),
        "covered": int((coverage_frame["status"] == "OK").sum()),
        "summary": summary,
        "output_dir": str(args.output_dir.resolve()),
    }, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
