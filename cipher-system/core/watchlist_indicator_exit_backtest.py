"""Indicator-driven exit research for timestamped watchlist option alerts.

Research-only. Uses completed five-minute underlying bars for signals and the
next available one-minute option bar open for fills. No account or order API is
imported or called.
"""
from __future__ import annotations

import argparse
import json
import math
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from core.historical_options_download import alpaca_credentials
from core.watchlist_exit_backtest import parse_alerts, choose_entry, expiry_end

NY = ZoneInfo("America/New_York")
UTC = timezone.utc
STOCK_URL = "https://data.alpaca.markets/v2/stocks/{symbol}/bars"


@dataclass(frozen=True)
class SignalResult:
    timestamp: pd.Timestamp | None
    reason: str


def request_json(url: str, params: dict[str, Any], key: str, secret: str) -> dict[str, Any]:
    req = urllib.request.Request(
        f"{url}?{urllib.parse.urlencode(params)}",
        headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
    )
    with urllib.request.urlopen(req, timeout=90) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_stock_bars(symbol: str, start: datetime, end: datetime, feed: str) -> pd.DataFrame:
    key, secret, _ = alpaca_credentials()
    params: dict[str, Any] = {
        "timeframe": "1Min",
        "start": start.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "end": end.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "limit": 10000,
        "feed": feed,
        "adjustment": "raw",
        "sort": "asc",
    }
    rows: list[dict[str, Any]] = []
    while True:
        payload = request_json(STOCK_URL.format(symbol=symbol), params, key, secret)
        data = payload.get("bars") or []
        if isinstance(data, dict):
            data = data.get(symbol) or []
        rows.extend(data)
        token = payload.get("next_page_token")
        if not token:
            break
        params["page_token"] = token
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows).rename(
        columns={"t": "timestamp", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume", "vw": "bar_vwap", "n": "trades"}
    )
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    for column in ("open", "high", "low", "close", "volume", "bar_vwap", "trades"):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)


def cache_stock_bars(symbol: str, start: datetime, end: datetime, feed: str, cache_dir: Path) -> pd.DataFrame:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{symbol}_{start.date()}_{end.date()}_{feed}.csv"
    if path.exists():
        frame = pd.read_csv(path)
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
        return frame
    frame = fetch_stock_bars(symbol, start, end, feed)
    frame.to_csv(path, index=False)
    return frame


def rth_five_minute(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    data = frame.copy().set_index("timestamp")
    local = data.index.tz_convert(NY)
    mask = (local.time >= time(9, 30)) & (local.time < time(16, 0))
    data = data.loc[mask].copy()
    if data.empty:
        return pd.DataFrame()
    data["session_date"] = data.index.tz_convert(NY).date.astype(str)
    data["vwap_num"] = data["bar_vwap"].fillna(data["close"]) * data["volume"].fillna(0)
    grouped: list[pd.DataFrame] = []
    for session_date, day in data.groupby("session_date"):
        bars = day.resample("5min", label="right", closed="left", origin="start_day", offset="30min").agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
            vwap_num=("vwap_num", "sum"),
        ).dropna(subset=["open", "high", "low", "close"])
        bars["session_date"] = session_date
        denom = bars["volume"].replace(0, np.nan).cumsum()
        bars["session_vwap"] = bars["vwap_num"].cumsum() / denom
        grouped.append(bars)
    out = pd.concat(grouped).sort_index()
    close = out["close"]
    out["ema9"] = close.ewm(span=9, adjust=False).mean()
    out["ema20"] = close.ewm(span=20, adjust=False).mean()
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    out["macd"] = ema12 - ema26
    out["macd_signal"] = out["macd"].ewm(span=9, adjust=False).mean()
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    out["rsi14"] = 100 - 100 / (1 + rs)
    prev_close = close.shift(1)
    tr = pd.concat(
        [out["high"] - out["low"], (out["high"] - prev_close).abs(), (out["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    out["atr14"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    out["prior5_low"] = out["low"].rolling(5).min().shift(1)
    out["prior5_high"] = out["high"].rolling(5).max().shift(1)
    return out.reset_index().rename(columns={"timestamp": "signal_time"})


def favorable(row: pd.Series, option_type: str, kind: str) -> bool:
    bullish = option_type == "call"
    if kind == "vwap":
        return float(row.close) > float(row.session_vwap) if bullish else float(row.close) < float(row.session_vwap)
    if kind == "ema9":
        return float(row.close) > float(row.ema9) if bullish else float(row.close) < float(row.ema9)
    if kind == "ema_cross":
        return float(row.ema9) > float(row.ema20) if bullish else float(row.ema9) < float(row.ema20)
    if kind == "macd":
        return float(row.macd) > float(row.macd_signal) if bullish else float(row.macd) < float(row.macd_signal)
    raise ValueError(kind)


def simple_regime_signal(bars: pd.DataFrame, entry_time: pd.Timestamp, option_type: str, kind: str) -> SignalResult:
    path = bars[bars["signal_time"] > entry_time].copy()
    armed = False
    for row in path.itertuples(index=False):
        row_s = pd.Series(row._asdict())
        is_favorable = favorable(row_s, option_type, kind)
        if not armed and is_favorable:
            armed = True
            continue
        if armed and not is_favorable:
            return SignalResult(pd.Timestamp(row.signal_time), f"{kind.upper()}_ADVERSE")
    return SignalResult(None, f"{kind.upper()}_NO_EXIT")


def ema_macd_signal(
    bars: pd.DataFrame,
    entry_time: pd.Timestamp,
    option_type: str,
    *,
    fast: int,
    slow: int,
    exit_mode: str,
) -> SignalResult:
    """Exit after momentum was favorable and EMA/MACD confirmation degrades.

    `either_adverse` exits when either the EMA relationship or MACD relationship
    turns adverse. `both_adverse` waits until both are adverse.
    """
    working = bars.copy()
    working["ema_fast_dynamic"] = working["close"].ewm(span=fast, adjust=False).mean()
    working["ema_slow_dynamic"] = working["close"].ewm(span=slow, adjust=False).mean()
    path = working[working["signal_time"] > entry_time].copy()
    armed = False
    for row in path.itertuples(index=False):
        bullish = option_type == "call"
        ema_favorable = (
            float(row.ema_fast_dynamic) > float(row.ema_slow_dynamic)
            if bullish
            else float(row.ema_fast_dynamic) < float(row.ema_slow_dynamic)
        )
        macd_favorable = (
            float(row.macd) > float(row.macd_signal)
            if bullish
            else float(row.macd) < float(row.macd_signal)
        )
        if not armed and ema_favorable and macd_favorable:
            armed = True
            continue
        if not armed:
            continue
        adverse = (
            (not ema_favorable or not macd_favorable)
            if exit_mode == "either_adverse"
            else (not ema_favorable and not macd_favorable)
        )
        if adverse:
            return SignalResult(
                pd.Timestamp(row.signal_time),
                f"EMA{fast}_{slow}_{exit_mode.upper()}_MACD",
            )
    return SignalResult(None, f"EMA{fast}_{slow}_{exit_mode.upper()}_MACD_NO_EXIT")


def composite_signal(bars: pd.DataFrame, entry_time: pd.Timestamp, option_type: str) -> SignalResult:
    path = bars[bars["signal_time"] > entry_time].copy()
    armed = False
    for row in path.itertuples(index=False):
        row_s = pd.Series(row._asdict())
        count = sum(favorable(row_s, option_type, kind) for kind in ("vwap", "ema_cross", "macd"))
        if not armed and count >= 2:
            armed = True
            continue
        if armed and count <= 1:
            return SignalResult(pd.Timestamp(row.signal_time), "COMPOSITE_2OF3_ADVERSE")
    return SignalResult(None, "COMPOSITE_NO_EXIT")


def rsi_signal(bars: pd.DataFrame, entry_time: pd.Timestamp, option_type: str) -> SignalResult:
    path = bars[bars["signal_time"] > entry_time].copy()
    armed = False
    for row in path.itertuples(index=False):
        rsi = float(row.rsi14) if pd.notna(row.rsi14) else math.nan
        if not math.isfinite(rsi):
            continue
        if option_type == "call":
            if not armed and rsi >= 65:
                armed = True
            elif armed and rsi < 55:
                return SignalResult(pd.Timestamp(row.signal_time), "RSI_REVERSAL")
        else:
            if not armed and rsi <= 35:
                armed = True
            elif armed and rsi > 45:
                return SignalResult(pd.Timestamp(row.signal_time), "RSI_REVERSAL")
    return SignalResult(None, "RSI_NO_EXIT")


def chandelier_signal(bars: pd.DataFrame, entry_time: pd.Timestamp, option_type: str, multiple: float) -> SignalResult:
    path = bars[bars["signal_time"] > entry_time].copy()
    if path.empty:
        return SignalResult(None, "CHAND_NO_BARS")
    peak = -math.inf
    trough = math.inf
    for row in path.itertuples(index=False):
        atr = float(row.atr14) if pd.notna(row.atr14) else math.nan
        if not math.isfinite(atr) or atr <= 0:
            continue
        peak = max(peak, float(row.high))
        trough = min(trough, float(row.low))
        if option_type == "call" and float(row.close) < peak - multiple * atr:
            return SignalResult(pd.Timestamp(row.signal_time), f"CHAND_{multiple:g}ATR")
        if option_type == "put" and float(row.close) > trough + multiple * atr:
            return SignalResult(pd.Timestamp(row.signal_time), f"CHAND_{multiple:g}ATR")
    return SignalResult(None, f"CHAND_{multiple:g}ATR_NO_EXIT")


def structure_signal(bars: pd.DataFrame, entry_time: pd.Timestamp, option_type: str) -> SignalResult:
    path = bars[bars["signal_time"] > entry_time].copy()
    for row in path.itertuples(index=False):
        if option_type == "call" and pd.notna(row.prior5_low) and float(row.close) < float(row.prior5_low):
            return SignalResult(pd.Timestamp(row.signal_time), "BREAK_PRIOR_5BAR_LOW")
        if option_type == "put" and pd.notna(row.prior5_high) and float(row.close) > float(row.prior5_high):
            return SignalResult(pd.Timestamp(row.signal_time), "BREAK_PRIOR_5BAR_HIGH")
    return SignalResult(None, "STRUCTURE_NO_EXIT")


def option_exit_index(option_bars: pd.DataFrame, signal_time: pd.Timestamp | None, entry_idx: int) -> int | None:
    if signal_time is None:
        return None
    eligible = option_bars[(option_bars.index > entry_idx) & (option_bars["timestamp"] > signal_time)]
    return int(eligible.index[0]) if not eligible.empty else None


def simulate_indicator_exit(
    option_bars: pd.DataFrame,
    entry_idx: int,
    entry_price: float,
    signal: SignalResult,
    stop_pct: float = -0.35,
) -> dict[str, Any]:
    exit_idx = option_exit_index(option_bars, signal.timestamp, entry_idx)
    path = option_bars[option_bars.index > entry_idx]
    if path.empty:
        return {"return_pct": None, "reason": "NO_PATH"}
    stop_price = entry_price * (1 + stop_pct)
    for idx, row in path.iterrows():
        if exit_idx is not None and idx >= exit_idx:
            fill = float(row.open)
            return {"return_pct": fill / entry_price - 1, "reason": signal.reason, "exit_time": pd.Timestamp(row.timestamp).isoformat(), "exit_price": fill}
        if float(row.low) <= stop_price:
            return {"return_pct": stop_pct, "reason": "EMERGENCY_STOP", "exit_time": pd.Timestamp(row.timestamp).isoformat(), "exit_price": stop_price}
    last = path.iloc[-1]
    fill = float(last.close)
    return {"return_pct": fill / entry_price - 1, "reason": "EXPIRY_LAST_BAR", "exit_time": pd.Timestamp(last.timestamp).isoformat(), "exit_price": fill}


def simulate_scale_indicator(
    option_bars: pd.DataFrame,
    entry_idx: int,
    entry_price: float,
    signal: SignalResult,
    stop_pct: float = -0.35,
    first_target: float = 0.25,
    second_target: float = 0.50,
) -> dict[str, Any]:
    exit_idx = option_exit_index(option_bars, signal.timestamp, entry_idx)
    path = option_bars[option_bars.index > entry_idx]
    if path.empty:
        return {"return_pct": None, "reason": "NO_PATH"}
    remaining = 1.0
    realized = 0.0
    first = second = False
    active_stop = entry_price * (1 + stop_pct)
    for idx, row in path.iterrows():
        if exit_idx is not None and idx >= exit_idx:
            fill_return = float(row.open) / entry_price - 1
            realized += remaining * fill_return
            return {"return_pct": realized, "reason": f"SCALE_{signal.reason}", "exit_time": pd.Timestamp(row.timestamp).isoformat(), "exit_price": float(row.open)}
        high = float(row.high)
        low = float(row.low)
        first_hit = not first and high >= entry_price * (1 + first_target)
        second_hit = first and not second and high >= entry_price * (1 + second_target)
        stop_hit = low <= active_stop
        if stop_hit and (first_hit or second_hit):
            realized += remaining * (active_stop / entry_price - 1)
            return {"return_pct": realized, "reason": "SCALE_AMBIGUOUS_STOP_FIRST", "exit_time": pd.Timestamp(row.timestamp).isoformat(), "exit_price": active_stop}
        if stop_hit:
            realized += remaining * (active_stop / entry_price - 1)
            return {"return_pct": realized, "reason": "SCALE_STOP", "exit_time": pd.Timestamp(row.timestamp).isoformat(), "exit_price": active_stop}
        if first_hit:
            realized += 0.50 * first_target
            remaining -= 0.50
            first = True
            active_stop = entry_price
        if first and not second and high >= entry_price * (1 + second_target):
            realized += 0.25 * second_target
            remaining -= 0.25
            second = True
    last = path.iloc[-1]
    fill_return = float(last.close) / entry_price - 1
    realized += remaining * fill_return
    return {"return_pct": realized, "reason": "SCALE_EXPIRY_LAST_BAR", "exit_time": pd.Timestamp(last.timestamp).isoformat(), "exit_price": float(last.close)}


def metrics(frame: pd.DataFrame) -> dict[str, Any]:
    values = pd.to_numeric(frame["return_pct"], errors="coerce").dropna()
    wins = values[values > 0]
    losses = values[values < 0]
    gross_profit = float(wins.sum())
    gross_loss = abs(float(losses.sum()))
    ordered = frame.sort_values("alert_time_et")
    equity = pd.to_numeric(ordered["return_pct"], errors="coerce").fillna(0).cumsum()
    peak = equity.cummax().clip(lower=0)
    dd = equity - peak
    return {
        "n": int(len(values)),
        "mean_return": float(values.mean()) if len(values) else None,
        "median_return": float(values.median()) if len(values) else None,
        "win_rate": float((values > 0).mean()) if len(values) else None,
        "total_return": float(values.sum()),
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else None,
        "max_drawdown": abs(float(dd.min())) if len(dd) else 0.0,
        "worst": float(values.min()) if len(values) else None,
        "best": float(values.max()) if len(values) else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--alerts", type=Path, required=True)
    parser.add_argument("--option-bars-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--feed", choices=["sip", "iex"], default="sip")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = args.output_dir / "underlying_bars"
    trades, _ = parse_alerts(args.alerts)
    grouped: dict[str, tuple[datetime, datetime]] = {}
    for trade in trades:
        start = trade.alert_time - timedelta(days=7)
        end = expiry_end(trade.expiration)
        if trade.ticker not in grouped:
            grouped[trade.ticker] = (start, end)
        else:
            old_start, old_end = grouped[trade.ticker]
            grouped[trade.ticker] = (min(old_start, start), max(old_end, end))
    underlying_5m: dict[str, pd.DataFrame] = {}
    for ticker, (start, end) in sorted(grouped.items()):
        raw = cache_stock_bars(ticker, start, end, args.feed, cache_dir)
        underlying_5m[ticker] = rth_five_minute(raw)

    signal_builders: dict[str, Callable[[pd.DataFrame, pd.Timestamp, str], SignalResult]] = {
        "VWAP_FAIL": lambda b, t, o: simple_regime_signal(b, t, o, "vwap"),
        "EMA9_FAIL": lambda b, t, o: simple_regime_signal(b, t, o, "ema9"),
        "EMA9_20_CROSS": lambda b, t, o: simple_regime_signal(b, t, o, "ema_cross"),
        "MACD_CROSS": lambda b, t, o: simple_regime_signal(b, t, o, "macd"),
        "RSI_REVERSAL": rsi_signal,
        "CHAND_2ATR": lambda b, t, o: chandelier_signal(b, t, o, 2.0),
        "CHAND_2_5ATR": lambda b, t, o: chandelier_signal(b, t, o, 2.5),
        "BREAK_5BAR_STRUCTURE": structure_signal,
        "COMPOSITE_2OF3": composite_signal,
        "EMA5_13_OR_MACD": lambda b, t, o: ema_macd_signal(
            b, t, o, fast=5, slow=13, exit_mode="either_adverse"
        ),
        "EMA8_21_OR_MACD": lambda b, t, o: ema_macd_signal(
            b, t, o, fast=8, slow=21, exit_mode="either_adverse"
        ),
        "EMA9_20_AND_MACD": lambda b, t, o: ema_macd_signal(
            b, t, o, fast=9, slow=20, exit_mode="both_adverse"
        ),
    }
    result_rows: list[dict[str, Any]] = []
    coverage_rows: list[dict[str, Any]] = []
    for trade in trades:
        option_path = args.option_bars_dir / f"{trade.occ_symbol}.csv"
        if not option_path.exists():
            coverage_rows.append({"key": trade.key, "ticker": trade.ticker, "status": "OPTION_BARS_MISSING"})
            continue
        option_bars = pd.read_csv(option_path)
        option_bars["timestamp"] = pd.to_datetime(option_bars["timestamp"], utc=True)
        entry = choose_entry(option_bars, trade.alert_time)
        stock_bars = underlying_5m.get(trade.ticker, pd.DataFrame())
        if entry is None or stock_bars.empty:
            coverage_rows.append({"key": trade.key, "ticker": trade.ticker, "status": "ENTRY_OR_STOCK_MISSING"})
            continue
        entry_idx, entry_price, entry_time = entry
        coverage_rows.append({"key": trade.key, "ticker": trade.ticker, "status": "OK", "entry_price": entry_price})
        for name, builder in signal_builders.items():
            signal = builder(stock_bars, entry_time, trade.option_type)
            for scale in (False, True):
                outcome = (
                    simulate_scale_indicator(option_bars, entry_idx, entry_price, signal)
                    if scale else simulate_indicator_exit(option_bars, entry_idx, entry_price, signal)
                )
                result_rows.append(
                    {
                        "key": trade.key,
                        "ticker": trade.ticker,
                        "option_type": trade.option_type,
                        "expiration": trade.expiration,
                        "alert_time_et": trade.alert_time.isoformat(),
                        "strategy": f"SCALE25_50_{name}" if scale else name,
                        "signal_time": signal.timestamp.isoformat() if signal.timestamp is not None else None,
                        "signal_reason": signal.reason,
                        **outcome,
                    }
                )
    results = pd.DataFrame(result_rows)
    coverage = pd.DataFrame(coverage_rows)
    summary_rows: list[dict[str, Any]] = []
    for strategy, group in results.groupby("strategy"):
        summary_rows.append({"strategy": strategy, **metrics(group)})
    summary = pd.DataFrame(summary_rows).sort_values(["mean_return", "median_return"], ascending=False)
    results.to_csv(args.output_dir / "indicator_trade_results.csv", index=False)
    summary.to_csv(args.output_dir / "indicator_summary.csv", index=False)
    coverage.to_csv(args.output_dir / "coverage.csv", index=False)
    print(json.dumps({"status": "PASS", "trades": len(trades), "covered": int((coverage.status == "OK").sum()), "strategies": len(summary), "output_dir": str(args.output_dir.resolve())}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
