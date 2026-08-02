"""Reconstruct and backtest exits for timestamped option watchlist alerts.

Research-only. Uses Alpaca historical OPRA one-minute trade bars. These are not
historical NBBO quotes, and no broker/account/order endpoint is imported or used.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import pandas as pd

from core.historical_options_download import alpaca_credentials

NY = ZoneInfo("America/New_York")
UTC = timezone.utc
DATA_URL = "https://data.alpaca.markets/v1beta1/options/bars"


@dataclass(frozen=True)
class AlertTrade:
    ticker: str
    strike: float
    option_type: str
    expiration: str
    alert_time: datetime
    occ_symbol: str

    @property
    def key(self) -> str:
        return f"{self.occ_symbol}@{self.alert_time.isoformat()}"


def occ_symbol(ticker: str, expiration: str, option_type: str, strike: float) -> str:
    root = "".join(ch for ch in ticker.upper() if ch.isalnum())
    if not root or len(root) > 6:
        raise ValueError(f"unsupported OCC root {ticker!r}")
    expiry = datetime.fromisoformat(expiration).strftime("%y%m%d")
    side = "C" if option_type.lower() == "call" else "P"
    strike_code = int(round(float(strike) * 1000))
    return f"{root}{expiry}{side}{strike_code:08d}"


def parse_alerts(path: Path) -> tuple[list[AlertTrade], pd.DataFrame]:
    frame = pd.read_csv(path)
    frame["date"] = frame["date"].astype(str)
    frame["time"] = frame["time"].astype(str)
    frame["timestamp_et"] = pd.to_datetime(frame["date"] + " " + frame["time"]).dt.tz_localize(NY)
    new_rows = frame[frame["event_type"] == "new"].copy()
    trades: list[AlertTrade] = []
    for row in new_rows.itertuples(index=False):
        symbol = occ_symbol(row.ticker, row.expiration, row.option_type, float(row.strike))
        trades.append(
            AlertTrade(
                ticker=str(row.ticker).upper(),
                strike=float(row.strike),
                option_type=str(row.option_type).lower(),
                expiration=str(row.expiration),
                alert_time=pd.Timestamp(row.timestamp_et).to_pydatetime(),
                occ_symbol=symbol,
            )
        )
    return trades, frame


def _request_json(url: str, params: dict[str, Any], key: str, secret: str) -> dict[str, Any]:
    query = urllib.parse.urlencode(params)
    req = urllib.request.Request(
        f"{url}?{query}",
        headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
    )
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_option_bars(symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
    key, secret, _ = alpaca_credentials()
    params: dict[str, Any] = {
        "symbols": symbol,
        "timeframe": "1Min",
        "start": start.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "end": end.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "limit": 10000,
        "sort": "asc",
    }
    rows: list[dict[str, Any]] = []
    while True:
        payload = _request_json(DATA_URL, params, key, secret)
        data = payload.get("bars") or {}
        if isinstance(data, dict):
            rows.extend(data.get(symbol) or [])
        token = payload.get("next_page_token")
        if not token:
            break
        params["page_token"] = token
    if not rows:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume", "vwap", "trades"])
    frame = pd.DataFrame(rows).rename(
        columns={"t": "timestamp", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume", "vw": "vwap", "n": "trades"}
    )
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    for column in ("open", "high", "low", "close", "volume", "vwap", "trades"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)


def expiry_end(expiration: str) -> datetime:
    day = datetime.fromisoformat(expiration).date()
    return datetime.combine(day, time(16, 1), NY)


def choose_entry(bars: pd.DataFrame, alert_time: datetime) -> tuple[int, float, pd.Timestamp] | None:
    if bars.empty:
        return None
    alert_utc = pd.Timestamp(alert_time).tz_convert("UTC")
    # Strictly later minute prevents using any trade that occurred before the alert
    # within the alert's timestamped minute.
    eligible = bars[bars["timestamp"] > alert_utc.floor("min")]
    if eligible.empty:
        return None
    idx = int(eligible.index[0])
    price = float(bars.loc[idx, "open"])
    if not math.isfinite(price) or price <= 0:
        return None
    return idx, price, pd.Timestamp(bars.loc[idx, "timestamp"])


def horizon_end(entry_time: pd.Timestamp, expiration: str, horizon: str) -> pd.Timestamp:
    local = entry_time.tz_convert(NY)
    if horizon == "30m":
        return entry_time + pd.Timedelta(minutes=30)
    if horizon == "60m":
        return entry_time + pd.Timedelta(minutes=60)
    if horizon == "eod":
        return pd.Timestamp(datetime.combine(local.date(), time(16, 0), NY)).tz_convert("UTC")
    if horizon == "next_eod":
        next_day = local.date() + timedelta(days=1)
        while next_day.weekday() >= 5:
            next_day += timedelta(days=1)
        return pd.Timestamp(datetime.combine(next_day, time(16, 0), NY)).tz_convert("UTC")
    if horizon == "expiry":
        return pd.Timestamp(expiry_end(expiration)).tz_convert("UTC")
    raise ValueError(horizon)


def simulate_single_exit(
    bars: pd.DataFrame,
    entry_idx: int,
    entry_price: float,
    expiration: str,
    *,
    target_pct: float | None,
    stop_pct: float | None,
    horizon: str,
) -> dict[str, Any]:
    entry_time = pd.Timestamp(bars.loc[entry_idx, "timestamp"])
    end = horizon_end(entry_time, expiration, horizon)
    path = bars[(bars.index > entry_idx) & (bars["timestamp"] <= end)].copy()
    if path.empty:
        return {"return_pct": None, "reason": "NO_POST_ENTRY_BARS", "exit_time": None, "exit_price": None}
    target_price = entry_price * (1.0 + target_pct) if target_pct is not None else None
    stop_price = entry_price * (1.0 + stop_pct) if stop_pct is not None else None
    for row in path.itertuples(index=False):
        target_hit = target_price is not None and float(row.high) >= target_price
        stop_hit = stop_price is not None and float(row.low) <= stop_price
        if target_hit and stop_hit:
            return {
                "return_pct": float(stop_pct),
                "reason": "AMBIGUOUS_STOP_FIRST",
                "exit_time": pd.Timestamp(row.timestamp).isoformat(),
                "exit_price": float(stop_price),
            }
        if stop_hit:
            return {
                "return_pct": float(stop_pct),
                "reason": "STOP",
                "exit_time": pd.Timestamp(row.timestamp).isoformat(),
                "exit_price": float(stop_price),
            }
        if target_hit:
            return {
                "return_pct": float(target_pct),
                "reason": "TARGET",
                "exit_time": pd.Timestamp(row.timestamp).isoformat(),
                "exit_price": float(target_price),
            }
    last = path.iloc[-1]
    exit_price = float(last["close"])
    return {
        "return_pct": exit_price / entry_price - 1.0,
        "reason": f"{horizon.upper()}_EXIT",
        "exit_time": pd.Timestamp(last["timestamp"]).isoformat(),
        "exit_price": exit_price,
    }


def simulate_scale_exit(
    bars: pd.DataFrame,
    entry_idx: int,
    entry_price: float,
    expiration: str,
    *,
    targets: tuple[tuple[float, float], ...],
    stop_pct: float,
    horizon: str,
    breakeven_after_first: bool,
) -> dict[str, Any]:
    entry_time = pd.Timestamp(bars.loc[entry_idx, "timestamp"])
    end = horizon_end(entry_time, expiration, horizon)
    path = bars[(bars.index > entry_idx) & (bars["timestamp"] <= end)].copy()
    if path.empty:
        return {"return_pct": None, "reason": "NO_POST_ENTRY_BARS", "exit_time": None}
    remaining = 1.0
    realized = 0.0
    filled: list[float] = []
    active_stop = stop_pct
    last_time: pd.Timestamp | None = None
    for row in path.itertuples(index=False):
        last_time = pd.Timestamp(row.timestamp)
        stop_hit = float(row.low) <= entry_price * (1.0 + active_stop)
        pending_hits = [
            (target, weight)
            for target, weight in targets
            if target not in filled and float(row.high) >= entry_price * (1.0 + target)
        ]
        if stop_hit and pending_hits:
            realized += remaining * active_stop
            remaining = 0.0
            return {"return_pct": realized, "reason": "SCALE_AMBIGUOUS_STOP_FIRST", "exit_time": last_time.isoformat()}
        if stop_hit:
            realized += remaining * active_stop
            remaining = 0.0
            return {"return_pct": realized, "reason": "SCALE_STOP", "exit_time": last_time.isoformat()}
        for target, weight in sorted(pending_hits):
            allocation = min(weight, remaining)
            realized += allocation * target
            remaining -= allocation
            filled.append(target)
            if breakeven_after_first and len(filled) == 1:
                active_stop = 0.0
        if remaining <= 1e-9:
            return {"return_pct": realized, "reason": "ALL_TARGETS", "exit_time": last_time.isoformat()}
    last = path.iloc[-1]
    realized += remaining * (float(last["close"]) / entry_price - 1.0)
    return {"return_pct": realized, "reason": f"SCALE_{horizon.upper()}_EXIT", "exit_time": pd.Timestamp(last["timestamp"]).isoformat()}


def simulate_scale_trailing_exit(
    bars: pd.DataFrame,
    entry_idx: int,
    entry_price: float,
    expiration: str,
    *,
    horizon: str,
    initial_stop_pct: float = -0.35,
    first_target_pct: float = 0.25,
    first_weight: float = 0.50,
    second_target_pct: float = 0.50,
    second_weight: float = 0.25,
    trail_fraction_from_peak: float = 0.25,
) -> dict[str, Any]:
    """Scale twice, then trail the final runner from its premium peak.

    After the first target, the remaining stop moves to breakeven. After the
    second target, the final runner uses a percentage-of-premium trailing stop.
    Same-minute stop/target ambiguity remains stop-first.
    """
    entry_time = pd.Timestamp(bars.loc[entry_idx, "timestamp"])
    end = horizon_end(entry_time, expiration, horizon)
    path = bars[(bars.index > entry_idx) & (bars["timestamp"] <= end)].copy()
    if path.empty:
        return {"return_pct": None, "reason": "NO_POST_ENTRY_BARS", "exit_time": None}
    remaining = 1.0
    realized = 0.0
    first_filled = False
    second_filled = False
    active_stop_price = entry_price * (1.0 + initial_stop_pct)
    peak_price = entry_price
    last_time: pd.Timestamp | None = None
    for row in path.itertuples(index=False):
        last_time = pd.Timestamp(row.timestamp)
        high = float(row.high)
        low = float(row.low)
        peak_price = max(peak_price, high)
        first_hit = (not first_filled) and high >= entry_price * (1.0 + first_target_pct)
        second_hit = first_filled and (not second_filled) and high >= entry_price * (1.0 + second_target_pct)
        stop_hit = low <= active_stop_price
        if stop_hit and (first_hit or second_hit):
            stop_return = active_stop_price / entry_price - 1.0
            realized += remaining * stop_return
            return {"return_pct": realized, "reason": "TRAIL_AMBIGUOUS_STOP_FIRST", "exit_time": last_time.isoformat()}
        if stop_hit:
            stop_return = active_stop_price / entry_price - 1.0
            realized += remaining * stop_return
            return {"return_pct": realized, "reason": "TRAIL_STOP", "exit_time": last_time.isoformat()}
        if first_hit:
            allocation = min(first_weight, remaining)
            realized += allocation * first_target_pct
            remaining -= allocation
            first_filled = True
            active_stop_price = entry_price
        if second_hit or (first_filled and not second_filled and high >= entry_price * (1.0 + second_target_pct)):
            allocation = min(second_weight, remaining)
            realized += allocation * second_target_pct
            remaining -= allocation
            second_filled = True
        if second_filled and remaining > 0:
            active_stop_price = max(entry_price, peak_price * (1.0 - trail_fraction_from_peak))
        if remaining <= 1e-9:
            return {"return_pct": realized, "reason": "TRAIL_ALL_SCALED", "exit_time": last_time.isoformat()}
    last = path.iloc[-1]
    realized += remaining * (float(last["close"]) / entry_price - 1.0)
    return {"return_pct": realized, "reason": f"TRAIL_{horizon.upper()}_EXIT", "exit_time": pd.Timestamp(last["timestamp"]).isoformat()}


def simulate_first_update_exit(
    bars: pd.DataFrame,
    entry_idx: int,
    entry_price: float,
    expiration: str,
    *,
    first_update_time: pd.Timestamp | None,
    sell_fraction: float,
    runner_trail_fraction: float,
    initial_stop_pct: float = -0.35,
) -> dict[str, Any]:
    """Sell at the first posted update and trail any remaining runner.

    A missing update falls back to a -35% emergency stop through expiration.
    The update fill is the next minute's open strictly after the posted minute.
    """
    if first_update_time is None or pd.isna(first_update_time):
        fallback = simulate_single_exit(
            bars, entry_idx, entry_price, expiration,
            target_pct=None, stop_pct=initial_stop_pct, horizon="expiry",
        )
        fallback["reason"] = "NO_UPDATE_" + str(fallback["reason"])
        return fallback
    update_utc = pd.Timestamp(first_update_time).tz_convert("UTC")
    fill_candidates = bars[
        (bars.index > entry_idx)
        & (bars["timestamp"] > update_utc.floor("min"))
        & (bars["timestamp"] <= pd.Timestamp(expiry_end(expiration)).tz_convert("UTC"))
    ]
    if fill_candidates.empty:
        return simulate_single_exit(
            bars, entry_idx, entry_price, expiration,
            target_pct=None, stop_pct=initial_stop_pct, horizon="expiry",
        )
    fill_idx = int(fill_candidates.index[0])
    stop_price = entry_price * (1.0 + initial_stop_pct)
    pre = bars[(bars.index > entry_idx) & (bars.index <= fill_idx)]
    for row in pre.itertuples(index=False):
        if float(row.low) <= stop_price:
            return {
                "return_pct": initial_stop_pct,
                "reason": "STOP_BEFORE_FIRST_UPDATE",
                "exit_time": pd.Timestamp(row.timestamp).isoformat(),
            }
    fill_bar = bars.loc[fill_idx]
    update_price = float(fill_bar["open"])
    update_return = update_price / entry_price - 1.0
    realized = sell_fraction * update_return
    remaining = 1.0 - sell_fraction
    if remaining <= 1e-9:
        return {
            "return_pct": realized,
            "reason": "SOLD_ALL_FIRST_UPDATE",
            "exit_time": pd.Timestamp(fill_bar["timestamp"]).isoformat(),
        }
    peak_price = update_price
    active_stop = max(entry_price, peak_price * (1.0 - runner_trail_fraction))
    path = bars[
        (bars.index > fill_idx)
        & (bars["timestamp"] <= pd.Timestamp(expiry_end(expiration)).tz_convert("UTC"))
    ]
    for row in path.itertuples(index=False):
        if float(row.low) <= active_stop:
            realized += remaining * (active_stop / entry_price - 1.0)
            return {
                "return_pct": realized,
                "reason": "FIRST_UPDATE_RUNNER_TRAIL",
                "exit_time": pd.Timestamp(row.timestamp).isoformat(),
            }
        peak_price = max(peak_price, float(row.high))
        active_stop = max(entry_price, peak_price * (1.0 - runner_trail_fraction))
    if path.empty:
        return {
            "return_pct": realized + remaining * update_return,
            "reason": "FIRST_UPDATE_NO_RUNNER_BARS",
            "exit_time": pd.Timestamp(fill_bar["timestamp"]).isoformat(),
        }
    last = path.iloc[-1]
    realized += remaining * (float(last["close"]) / entry_price - 1.0)
    return {
        "return_pct": realized,
        "reason": "FIRST_UPDATE_EXPIRY_EXIT",
        "exit_time": pd.Timestamp(last["timestamp"]).isoformat(),
    }


def summarize_path(trade: AlertTrade, bars: pd.DataFrame, entry_idx: int, entry_price: float, entry_time: pd.Timestamp) -> dict[str, Any]:
    path = bars[bars.index >= entry_idx].copy()
    max_idx = path["high"].idxmax()
    min_idx = path["low"].idxmin()
    max_return = float(path.loc[max_idx, "high"] / entry_price - 1.0)
    min_return = float(path.loc[min_idx, "low"] / entry_price - 1.0)
    last = path.iloc[-1]
    return {
        "key": trade.key,
        "ticker": trade.ticker,
        "occ_symbol": trade.occ_symbol,
        "strike": trade.strike,
        "option_type": trade.option_type,
        "expiration": trade.expiration,
        "alert_time_et": trade.alert_time.isoformat(),
        "entry_time_utc": entry_time.isoformat(),
        "entry_price": entry_price,
        "bar_count": int(len(path)),
        "max_return_pct": max_return,
        "max_time_utc": pd.Timestamp(path.loc[max_idx, "timestamp"]).isoformat(),
        "min_return_pct": min_return,
        "min_time_utc": pd.Timestamp(path.loc[min_idx, "timestamp"]).isoformat(),
        "last_return_pct": float(float(last["close"]) / entry_price - 1.0),
        "last_time_utc": pd.Timestamp(last["timestamp"]).isoformat(),
    }


def posted_update_stats(alerts: pd.DataFrame, trades: list[AlertTrade]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for trade in trades:
        updates = alerts[
            (alerts["event_type"] == "update")
            & (alerts["ticker"].str.upper() == trade.ticker)
            & (pd.to_numeric(alerts["strike"], errors="coerce") == trade.strike)
            & (alerts["option_type"].str.lower() == trade.option_type)
            & (alerts["expiration"].astype(str) == trade.expiration)
            & (alerts["timestamp_et"] >= pd.Timestamp(trade.alert_time))
        ].sort_values("timestamp_et")
        if updates.empty:
            rows.append({"key": trade.key, "posted_updates": 0, "first_update_pct": None, "max_posted_pct": None, "minutes_to_first_update": None})
            continue
        first = updates.iloc[0]
        rows.append(
            {
                "key": trade.key,
                "posted_updates": int(len(updates)),
                "first_update_pct": float(first["return_pct"]) / 100.0,
                "max_posted_pct": float(pd.to_numeric(updates["return_pct"]).max()) / 100.0,
                "minutes_to_first_update": (pd.Timestamp(first["timestamp_et"]) - pd.Timestamp(trade.alert_time)).total_seconds() / 60.0,
            }
        )
    return pd.DataFrame(rows)


def build_report(
    coverage: pd.DataFrame,
    strategies: pd.DataFrame,
    posted: pd.DataFrame,
    unresolved: list[str],
) -> str:
    valid = coverage[coverage["status"] == "OK"].copy()
    lines = [
        "# Watchlist option exit backtest",
        "",
        "> Research only. Alpaca historical OPRA trade bars are not historical NBBO quotes.",
        "",
        "## Coverage",
        "",
        f"- New trade alerts: **{len(coverage)}**",
        f"- Contracts reconstructed: **{len(valid)}**",
        f"- Missing/invalid/no-bar contracts: **{len(coverage) - len(valid)}**",
        f"- Alerts with at least one posted return update: **{int((posted['posted_updates'] > 0).sum())}**",
        f"- Alerts without a posted update: **{int((posted['posted_updates'] == 0).sum())}**",
        "",
    ]
    if unresolved:
        lines.extend(["Unresolved contracts: " + ", ".join(f"`{item}`" for item in unresolved), ""])
    if not valid.empty:
        lines.extend(
            [
                "## Reconstructed paths",
                "",
                f"- Median maximum return: **{valid['max_return_pct'].median():.1%}**",
                f"- Mean maximum return: **{valid['max_return_pct'].mean():.1%}**",
                f"- Contracts that ever reached +25%: **{int((valid['max_return_pct'] >= .25).sum())}/{len(valid)}**",
                f"- Contracts that ever reached +50%: **{int((valid['max_return_pct'] >= .50).sum())}/{len(valid)}**",
                f"- Contracts that ever reached +100%: **{int((valid['max_return_pct'] >= 1.0).sum())}/{len(valid)}**",
                "",
            ]
        )
    lines.extend(
        [
            "## Exit-policy comparison",
            "",
            "| Policy | n | Mean return | Median | Win rate | Total equal-weight return |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for row in strategies.sort_values("mean_return", ascending=False).itertuples(index=False):
        lines.append(
            f"| {row.strategy} | {row.n} | {row.mean_return:.1%} | {row.median_return:.1%} | {row.win_rate:.1%} | {row.total_return:.1%} |"
        )
    lines.extend(
        [
            "",
            "## Important limitations",
            "",
            "- Entry is the next one-minute bar open strictly after the alert minute; actual alert fills may differ.",
            "- Threshold fills use bar high/low. If stop and target occur in the same minute, stop is assumed first.",
            "- OPRA trade bars omit historical bid/ask spreads and can be sparse for illiquid contracts.",
            "- Results exclude commissions and slippage. Deep OTM contracts can have very large percentage changes from tiny premiums.",
            "- Ranking many exit rules on 30 alerts is exploratory and vulnerable to overfitting.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Backtest exits for timestamped option watchlist alerts")
    parser.add_argument("--alerts", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    output = args.output_dir.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    cache = output / "bars"
    cache.mkdir(exist_ok=True)

    trades, alerts = parse_alerts(args.alerts)
    posted = posted_update_stats(alerts, trades)
    coverage_rows: list[dict[str, Any]] = []
    strategy_rows: list[dict[str, Any]] = []

    strategy_specs: list[tuple[str, str, float | None, float | None]] = []
    for horizon in ("30m", "60m", "eod", "next_eod", "expiry"):
        strategy_specs.append((f"{horizon}: no target / -35% stop", horizon, None, -0.35))
        for target in (0.25, 0.50, 0.75, 1.00, 1.50, 2.00):
            for stop in (-0.25, -0.35, -0.50):
                strategy_specs.append((f"{horizon}: +{target:.0%} / {stop:.0%}", horizon, target, stop))

    for trade in trades:
        cache_path = cache / f"{trade.occ_symbol}.csv"
        if args.refresh or not cache_path.exists():
            start = trade.alert_time - timedelta(minutes=2)
            end = expiry_end(trade.expiration)
            try:
                bars = fetch_option_bars(trade.occ_symbol, start, end)
            except Exception as exc:
                coverage_rows.append({"key": trade.key, "ticker": trade.ticker, "occ_symbol": trade.occ_symbol, "status": f"FETCH_ERROR:{type(exc).__name__}", "error": str(exc)[:300]})
                continue
            bars.to_csv(cache_path, index=False)
        else:
            bars = pd.read_csv(cache_path)
            if not bars.empty:
                bars["timestamp"] = pd.to_datetime(bars["timestamp"], utc=True)
        if bars.empty:
            coverage_rows.append({"key": trade.key, "ticker": trade.ticker, "occ_symbol": trade.occ_symbol, "status": "NO_BARS"})
            continue
        entry = choose_entry(bars, trade.alert_time)
        if entry is None:
            coverage_rows.append({"key": trade.key, "ticker": trade.ticker, "occ_symbol": trade.occ_symbol, "status": "NO_ENTRY_BAR"})
            continue
        entry_idx, entry_price, entry_time = entry
        path_summary = summarize_path(trade, bars, entry_idx, entry_price, entry_time)
        coverage_rows.append({**path_summary, "status": "OK"})
        for name, horizon, target, stop in strategy_specs:
            result = simulate_single_exit(
                bars, entry_idx, entry_price, trade.expiration,
                target_pct=target, stop_pct=stop, horizon=horizon,
            )
            strategy_rows.append({"key": trade.key, "strategy": name, **result})
        matching_updates = alerts[
            (alerts["event_type"] == "update")
            & (alerts["ticker"].str.upper() == trade.ticker)
            & (pd.to_numeric(alerts["strike"], errors="coerce") == trade.strike)
            & (alerts["option_type"].str.lower() == trade.option_type)
            & (alerts["expiration"].astype(str) == trade.expiration)
            & (alerts["timestamp_et"] >= pd.Timestamp(trade.alert_time))
        ].sort_values("timestamp_et")
        first_update_time = None if matching_updates.empty else pd.Timestamp(matching_updates.iloc[0]["timestamp_et"])
        for sell_fraction in (.50, .75, 1.00):
            for trail in (.20, .25, .30):
                result = simulate_first_update_exit(
                    bars, entry_idx, entry_price, trade.expiration,
                    first_update_time=first_update_time,
                    sell_fraction=sell_fraction,
                    runner_trail_fraction=trail,
                )
                strategy_rows.append({
                    "key": trade.key,
                    "strategy": f"first update sell {sell_fraction:.0%}; trail runner {trail:.0%}",
                    **result,
                })

        for horizon in ("60m", "eod", "next_eod", "expiry"):
            for name, targets, stop, breakeven in (
                (f"{horizon} scale 1/3 at +25/+50/+100; -35%", ((.25, 1/3), (.50, 1/3), (1.00, 1/3)), -.35, False),
                (f"{horizon} 50% +25, 25% +50, 25% +100; BE after first", ((.25, .50), (.50, .25), (1.00, .25)), -.35, True),
                (f"{horizon} 50% +50, 25% +100, 25% +200; -35%", ((.50, .50), (1.00, .25), (2.00, .25)), -.35, False),
            ):
                result = simulate_scale_exit(
                    bars, entry_idx, entry_price, trade.expiration,
                    targets=targets, stop_pct=stop, horizon=horizon, breakeven_after_first=breakeven,
                )
                strategy_rows.append({"key": trade.key, "strategy": name, **result})
            for trail in (.20, .25, .30):
                result = simulate_scale_trailing_exit(
                    bars, entry_idx, entry_price, trade.expiration,
                    horizon=horizon, trail_fraction_from_peak=trail,
                )
                strategy_rows.append({
                    "key": trade.key,
                    "strategy": f"{horizon} 50% +25, 25% +50, trail runner {trail:.0%}",
                    **result,
                })

    coverage = pd.DataFrame(coverage_rows)
    per_trade = pd.DataFrame(strategy_rows)
    if per_trade.empty:
        raise RuntimeError("no contracts could be reconstructed")
    per_trade["return_pct"] = pd.to_numeric(per_trade["return_pct"], errors="coerce")
    summaries = []
    for name, group in per_trade.dropna(subset=["return_pct"]).groupby("strategy"):
        summaries.append(
            {
                "strategy": name,
                "n": int(len(group)),
                "mean_return": float(group["return_pct"].mean()),
                "median_return": float(group["return_pct"].median()),
                "win_rate": float((group["return_pct"] > 0).mean()),
                "total_return": float(group["return_pct"].sum()),
            }
        )
    strategies = pd.DataFrame(summaries)
    combined = coverage.merge(posted, on="key", how="left")
    unresolved = coverage.loc[coverage["status"] != "OK", "occ_symbol"].astype(str).tolist()
    report = build_report(combined, strategies, posted, unresolved)

    combined.to_csv(output / "contract_paths.csv", index=False)
    per_trade.to_csv(output / "policy_trade_results.csv", index=False)
    strategies.sort_values("mean_return", ascending=False).to_csv(output / "policy_summary.csv", index=False)
    posted.to_csv(output / "posted_update_summary.csv", index=False)
    (output / "report.md").write_text(report, encoding="utf-8")
    print(json.dumps({
        "status": "PASS",
        "alerts": len(trades),
        "reconstructed": int((coverage["status"] == "OK").sum()),
        "output_dir": str(output),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
