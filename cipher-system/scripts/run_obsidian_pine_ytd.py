#!/usr/bin/env python3
"""Backtest the pasted Obsidian EOD Pine strategy on 2026 YTD stock bars.

This runner intentionally does not use ``core.backtest_engine``: that engine is
for a different detector contract (next-bar-open entries plus ATR exits). The
Pine strategy supplied by the user has these materially different rules:

* 1-minute regular-session bars in America/New_York;
* CLPS-only by default, with a two-bar delayed entry;
* ``process_orders_on_close=true``: entry fills at the delayed bar close;
* at most one trade per New York trading day;
* mandatory liquidation at the 15:59 bar close;
* zero commission and one-tick slippage per order.

The downloader reuses the repository's Alpaca credential resolver and resumable
raw-page/SQLite store. It is research-only and never calls an account or order
endpoint.

Example:
    python3 scripts/run_obsidian_pine_ytd.py \
      --output-root data/historical_equities/obsidian_pine_ytd_2026 \
      --json-out data/backtests/obsidian_pine_ytd_2026.json
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

CIPHER_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = CIPHER_ROOT.parent
for path in (str(CIPHER_ROOT), str(CIPHER_ROOT / "core"), str(REPO_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from core import obsidian_eod  # noqa: E402
from core.equity_history_download import (  # noqa: E402
    EquityBarStore,
    JsonHttpClient,
    download_bars,
    iso_utc,
    parse_day,
    quarter_windows,
)
from core.historical_options_download import alpaca_credentials  # noqa: E402

NY = ZoneInfo("America/New_York")
UTC = timezone.utc
REQUIRED_SYMBOLS = (
    "SPY", "QQQ", "AAPL", "NVDA", "MSFT", "GOOGL", "AMZN", "META", "TSLA", "AMD",
)
PINE_DEFAULTS = {
    # These are copied from the pasted Pine strategy, not the older detector
    # defaults. In particular, the pasted source uses sigMult=1.10.
    "mode": "EOD Focus",
    "close_hour": 16,
    "close_minute": 0,
    "arm_minutes": 30,
    "hot_minutes": 10,
    "boost_on": True,
    "sig_mult": 1.10,
    "clps_thresh": 0.60,
    "max_bars": 4,
    "coil_amp": 0.85,
    "coil_len": 8,
    "rel_window": 6,
    "rel_hist": 0.90,
    "rel_thrust": 1.5,
    "rel_vol_min": 1.30,
    "trend_len": 150,
    "slope_bars": 10,
}


@dataclass(frozen=True)
class PineTrade:
    symbol: str
    direction: str
    signal_time: str
    entry_time: str
    exit_time: str
    signal_price: float
    entry_price_before_slippage: float
    entry_price: float
    exit_price_before_slippage: float
    exit_price: float
    gross_return_pct: float
    net_return_pct: float
    bars_held: int
    signal_kind: str


def _et(raw: str) -> datetime:
    value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(NY)


def _rth(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep 09:30 through 15:59 ET, preserving chronological order."""
    result = []
    for row in rows:
        try:
            local = _et(str(row["timestamp"]))
        except (KeyError, TypeError, ValueError):
            continue
        if (local.hour, local.minute) < (9, 30) or (local.hour, local.minute) >= (16, 0):
            continue
        result.append({
            "time": str(row["timestamp"]),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row["volume"] or 0),
        })
    result.sort(key=lambda row: row["time"])
    return result


def _mins_to_close(raw: str) -> int:
    local = _et(raw)
    return (16 * 60) - (local.hour * 60 + local.minute)


def _is_new_york_day(previous: str | None, current: str) -> bool:
    return previous is None or _et(previous).date() != _et(current).date()


def _signal_kind(state: obsidian_eod.BarState) -> tuple[str, str] | None:
    """Return the Pine CLPS direction/kind represented by one detector state."""
    # The existing faithful detector emits the same event vocabulary as the Pine
    # port. A collapse of positive histogram momentum is bearish; a collapse of
    # negative momentum is bullish. The setup label is only for audit output.
    if state.clps_up:
        return "LONG", "CLPS UP"
    if state.clps_down:
        return "SHORT", "CLPS DOWN"
    return None


def _recent_release(states: list[obsidian_eod.BarState], index: int, direction: str, lookback: int) -> bool:
    """Pine ``ta.barssince`` equivalent for an RLS->CLPS relation."""
    wanted = "Momentum push" if direction == "LONG" else "Momentum push (down)"
    lo = max(0, index - lookback)
    return any(wanted == event for j in range(lo, index) for event in states[j].events)


def _candidate_indices(
    bars: list[dict[str, Any]],
    states: list[obsidian_eod.BarState],
    *,
    evaluation_start: date | None,
    strategy_mode: str,
    entry_delay: int,
    min_signal_lead: int,
    rls_lookback: int,
    rls_relation: str,
    evaluation_end: date | None = None,
    bar_minutes: int = 1,
) -> list[tuple[int, str, str]]:
    """Reproduce Pine's gated candidate/arming phase.

    The returned index is the CLPS signal bar. Entry and one-trade-per-day state
    are applied separately, because Pine remembers a signal while the delay runs.
    """
    needs_rls = strategy_mode in {"RLS -> CLPS", "A-Grade RLS -> CLPS"}
    a_only = strategy_mode in {"A-Grade CLPS", "A-Grade RLS -> CLPS"}
    required_lead = max(min_signal_lead, (entry_delay + 2) * bar_minutes)
    out: list[tuple[int, str, str]] = []
    for i, state in enumerate(states):
        if i >= len(bars) or not state.in_window:
            continue
        bar_day = _et(str(bars[i]["time"])).date()
        if evaluation_start is not None and bar_day < evaluation_start:
            continue
        if evaluation_end is not None and bar_day > evaluation_end:
            continue
        # Pine's ``signalTimeOk`` includes the delayed-entry lead guard. With
        # normal 1-minute bars this excludes 15:57+ signals for delay=2.
        if _mins_to_close(str(bars[i]["time"])) < required_lead:
            continue
        signal = _signal_kind(state)
        if signal is None:
            continue
        direction, kind = signal
        if a_only:
            # Pine's A grade is the CLPS event running with trend: bearish CLPS
            # requires trendDown; bullish CLPS requires trendUp.
            if direction == "LONG" and not state.clps_up_a:
                continue
            if direction == "SHORT" and not state.clps_down_a:
                continue
        if needs_rls:
            opposite = "SHORT" if direction == "LONG" else "LONG"
            same_ok = _recent_release(states, i, direction, rls_lookback)
            opposite_ok = _recent_release(states, i, opposite, rls_lookback)
            if rls_relation == "Opposite":
                relation_ok = opposite_ok
            elif rls_relation == "Same":
                relation_ok = same_ok
            else:
                relation_ok = same_ok or opposite_ok
            if not relation_ok:
                continue
        out.append((i, direction, kind))
    return out


def _trade_for_signal(
    symbol: str,
    bars: list[dict[str, Any]],
    signal_index: int,
    direction: str,
    signal_kind: str,
    *,
    entry_delay: int,
    tick_size: float,
    eod_exit_minute: int = 59,
) -> PineTrade | None:
    entry_index = signal_index + entry_delay
    if entry_index >= len(bars):
        return None
    # Pine explicitly closes on the 15:59 bar. The filtered RTH list is expected
    # to contain that bar; if the provider has a gap, do not invent an exit.
    exit_index = None
    for index in range(entry_index, len(bars)):
        local = _et(bars[index]["time"])
        if local.hour == 15 and local.minute == eod_exit_minute:
            exit_index = index
            break
        if _is_new_york_day(bars[entry_index]["time"], bars[index]["time"]):
            break
    if exit_index is None or exit_index <= entry_index:
        return None

    raw_entry = float(bars[entry_index]["close"])
    raw_exit = float(bars[exit_index]["close"])
    # TradingView slippage=1 is one minimum tick on each order. Apply it in the
    # adverse direction, not as a generic percentage.
    if direction == "LONG":
        entry = raw_entry + tick_size
        exit = raw_exit - tick_size
        gross = (raw_exit / raw_entry - 1.0) * 100.0
        net = (exit / entry - 1.0) * 100.0
    else:
        entry = raw_entry - tick_size
        exit = raw_exit + tick_size
        gross = (raw_entry / raw_exit - 1.0) * 100.0
        net = (entry / exit - 1.0) * 100.0
    return PineTrade(
        symbol=symbol,
        direction=direction,
        signal_time=str(bars[signal_index]["time"]),
        entry_time=str(bars[entry_index]["time"]),
        exit_time=str(bars[exit_index]["time"]),
        signal_price=float(bars[signal_index]["close"]),
        entry_price_before_slippage=raw_entry,
        entry_price=entry,
        exit_price_before_slippage=raw_exit,
        exit_price=exit,
        gross_return_pct=round(gross, 6),
        net_return_pct=round(net, 6),
        bars_held=exit_index - entry_index,
        signal_kind=signal_kind,
    )


def _summary(trades: list[PineTrade], bars: list[dict[str, Any]]) -> dict[str, Any]:
    if not trades:
        return {
            "trades": 0,
            "first_bar": bars[0]["time"] if bars else None,
            "last_bar": bars[-1]["time"] if bars else None,
        }
    returns = [trade.net_return_pct for trade in trades]
    equity = 100_000.0
    peak = equity
    max_drawdown_pct = 0.0
    for value in returns:
        equity *= 1.0 + value / 100.0
        peak = max(peak, equity)
        max_drawdown_pct = max(max_drawdown_pct, (peak - equity) / peak * 100.0)
    wins = [value for value in returns if value > 0]
    losses = [-value for value in returns if value <= 0]
    gross_profit = sum(wins)
    gross_loss = sum(losses)
    return {
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(100.0 * len(wins) / len(returns), 3),
        "avg_trade_return_pct": round(sum(returns) / len(returns), 6),
        "median_trade_return_pct": round(sorted(returns)[len(returns) // 2], 6),
        "gross_sum_return_pct": round(sum(returns), 6),
        "compounded_return_pct": round((equity / 100_000.0 - 1.0) * 100.0, 6),
        "ending_equity": round(equity, 2),
        "max_drawdown_pct": round(max_drawdown_pct, 6),
        "profit_factor": round(gross_profit / gross_loss, 6) if gross_loss else None,
        "avg_bars_held": round(sum(t.bars_held for t in trades) / len(trades), 3),
        "long_trades": sum(t.direction == "LONG" for t in trades),
        "short_trades": sum(t.direction == "SHORT" for t in trades),
        "first_bar": bars[0]["time"] if bars else None,
        "last_bar": bars[-1]["time"] if bars else None,
    }


def backtest_symbol(
    symbol: str,
    rows: list[dict[str, Any]],
    *,
    strategy_mode: str,
    entry_delay: int,
    min_signal_lead: int,
    rls_lookback: int,
    rls_relation: str,
    tick_size: float,
    evaluation_start: date | None,
    detector_params: dict[str, Any] | None = None,
    evaluation_end: date | None = None,
    eod_exit_minute: int = 59,
    bar_minutes: int = 1,
) -> dict[str, Any]:
    bars = _rth(rows)
    if not bars:
        return {"symbol": symbol, "coverage": {"bars": 0}, "summary": {"trades": 0}, "trades": []}
    params = {**PINE_DEFAULTS, **(detector_params or {})}
    states, detector_summary = obsidian_eod.compute(bars, params)
    candidates = _candidate_indices(
        bars,
        states,
        evaluation_start=evaluation_start,
        evaluation_end=evaluation_end,
        bar_minutes=bar_minutes,
        strategy_mode=strategy_mode,
        entry_delay=entry_delay,
        min_signal_lead=min_signal_lead,
        rls_lookback=rls_lookback,
        rls_relation=rls_relation,
    )
    # Mirrors Pine's var tradedToday and first-qualifying-signal memory.
    trades: list[PineTrade] = []
    traded_dates: set[date] = set()
    armed_dates: set[date] = set()
    for signal_index, direction, kind in candidates:
        signal_day = _et(bars[signal_index]["time"]).date()
        if signal_day in traded_dates or signal_day in armed_dates:
            continue
        trade = _trade_for_signal(
            symbol,
            bars,
            signal_index,
            direction,
            kind,
            entry_delay=entry_delay,
            tick_size=tick_size,
            eod_exit_minute=eod_exit_minute,
        )
        # Pine arms the signal even if an abnormal missing-bar sequence later
        # prevents an executable delayed entry; one-trade-per-day remains consumed
        # only by a real order.
        armed_dates.add(signal_day)
        if trade is not None:
            trades.append(trade)
            traded_dates.add(signal_day)
    return {
        "symbol": symbol,
        "coverage": {
            "bars": len(bars),
            "first_bar": bars[0]["time"],
            "last_bar": bars[-1]["time"],
            "regular_session_days": len({_et(row["time"]).date() for row in bars}),
            # With timestamped one-minute bars, 09:30 through 15:59 is 390
            # bars. Some project reports call this 391 because they include a
            # 16:00 boundary marker; this Pine runner intentionally does not.
            "days_with_390_bars": sum(
                count == 390 for count in _bars_per_day(bars).values()
            ),
        },
        "detector_summary": detector_summary,
        "candidate_signals": len(candidates),
        "summary": _summary(trades, bars),
        "trades": [asdict(trade) for trade in trades],
    }


def _pooled_summary(trades: list[PineTrade], bars: list[dict[str, Any]]) -> dict[str, Any]:
    """Pool independent ticker trades without pretending they are one portfolio."""
    base = _summary(trades, bars)
    if not trades:
        return base
    returns = [trade.net_return_pct for trade in trades]
    wins = [value for value in returns if value > 0]
    losses = [-value for value in returns if value <= 0]
    base.pop("compounded_return_pct", None)
    base.pop("ending_equity", None)
    base["pooled_trade_return_sum_pct"] = round(sum(returns), 6)
    base["pooled_avg_trade_return_pct"] = round(sum(returns) / len(returns), 6)
    base["profit_factor"] = round(sum(wins) / sum(losses), 6) if losses else None
    base.pop("max_drawdown_pct", None)
    base["note"] = "Pooled trade statistics across tickers; no cross-ticker portfolio compounding or pooled drawdown."
    return base


def _bars_per_day(bars: list[dict[str, Any]]) -> dict[date, int]:
    counts: dict[date, int] = defaultdict(int)
    for row in bars:
        counts[_et(row["time"]).date()] += 1
    return counts


def load_rows(store: EquityBarStore, symbol: str, timeframe: str = "1Min") -> list[dict[str, Any]]:
    with store.connect() as db:
        rows = db.execute(
            """select timestamp,open,high,low,close,volume
               from bars where symbol=? and timeframe=? order by timestamp""",
            (symbol, timeframe),
        ).fetchall()
    return [
        {"timestamp": row[0], "open": row[1], "high": row[2], "low": row[3], "close": row[4], "volume": row[5]}
        for row in rows
    ]


def download_universe(store: EquityBarStore, symbols: tuple[str, ...], start: date, end: date, *, timeout: int, retries: int, resume: bool) -> dict[str, Any]:
    key, secret, feed = alpaca_credentials()
    client = JsonHttpClient(
        {
            "APCA-API-KEY-ID": key,
            "APCA-API-SECRET-KEY": secret,
            "Accept": "application/json",
            "User-Agent": "Cipher-Obsidian-Pine-Research/1.0",
        },
        timeout=timeout,
        retries=retries,
    )
    results = []
    for number, symbol in enumerate(symbols, start=1):
        pages = rows = 0
        for window_start, window_end in quarter_windows(start, end):
            result = download_bars(
                store,
                client,
                symbol=symbol,
                timeframe="1Min",
                start_day=window_start,
                end_day=window_end,
                feed=feed,
                resume=resume,
            )
            pages += result.pages
            rows += result.rows
        coverage = store.coverage(symbol)
        print(f"[{number}/{len(symbols)}] {symbol}: downloaded_pages={pages} new_rows={rows} feed={feed} coverage={coverage.get('timeframes', [])}")
        results.append({"symbol": symbol, "pages": pages, "new_rows": rows, "feed": feed, "coverage": coverage})
    return {"resolved_feed": feed, "symbols": results}


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "symbol", "trades", "wins", "losses", "win_rate_pct", "avg_trade_return_pct",
        "median_trade_return_pct", "gross_sum_return_pct", "compounded_return_pct",
        "ending_equity", "max_drawdown_pct", "profit_factor", "avg_bars_held",
        "long_trades", "short_trades", "candidate_signals", "bars", "regular_session_days",
        "days_with_390_bars", "first_bar", "last_bar",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({**row["summary"], "symbol": row["symbol"], "candidate_signals": row.get("candidate_signals", 0), **row["coverage"]})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2026-01-01")
    parser.add_argument("--end", default=date.today().isoformat())
    parser.add_argument("--symbols", default=",".join(REQUIRED_SYMBOLS))
    parser.add_argument("--output-root", default=str(CIPHER_ROOT / "data" / "historical_equities" / "obsidian_pine_ytd_2026"))
    parser.add_argument("--json-out", default=str(CIPHER_ROOT / "data" / "backtests" / "obsidian_pine_ytd_2026.json"))
    parser.add_argument("--csv-out", default=str(CIPHER_ROOT / "data" / "backtests" / "obsidian_pine_ytd_2026.csv"))
    parser.add_argument("--strategy-mode", choices=("CLPS Only", "A-Grade CLPS", "RLS -> CLPS", "A-Grade RLS -> CLPS"), default="CLPS Only")
    parser.add_argument("--entry-delay", type=int, default=2)
    parser.add_argument("--min-signal-lead", type=int, default=4)
    parser.add_argument("--rls-lookback", type=int, default=10)
    parser.add_argument("--rls-relation", choices=("Opposite", "Same", "Any"), default="Opposite")
    parser.add_argument("--tick-size", type=float, default=0.01)
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--retries", type=int, default=6)
    parser.add_argument("--refresh", action="store_true", help="redownload completed windows")
    args = parser.parse_args(argv)

    start = parse_day(args.start)
    end = parse_day(args.end)
    if end < start:
        parser.error("--end must not precede --start")
    symbols = tuple(dict.fromkeys(s.strip().upper() for s in args.symbols.split(",") if s.strip()))
    if not symbols:
        parser.error("at least one symbol is required")

    output_root = Path(args.output_root).resolve()
    store = EquityBarStore(output_root)
    # Fetch a pre-period warmup window so ta.stdev(100) and EMA(150) are already
    # formed on the first evaluated session. Warmup bars never contribute trades.
    download_start = start.fromordinal(start.toordinal() - 30)
    download = download_universe(
        store,
        symbols,
        download_start,
        end,
        timeout=args.timeout,
        retries=args.retries,
        resume=not args.refresh,
    )

    per_symbol = []
    for symbol in symbols:
        rows = load_rows(store, symbol)
        per_symbol.append(backtest_symbol(
            symbol,
            rows,
            strategy_mode=args.strategy_mode,
            entry_delay=args.entry_delay,
            min_signal_lead=args.min_signal_lead,
            rls_lookback=args.rls_lookback,
            rls_relation=args.rls_relation,
            tick_size=args.tick_size,
            evaluation_start=start,
        ))

    all_trades = [trade for row in per_symbol for trade in row["trades"]]
    aggregate_summary = _pooled_summary([PineTrade(**trade) for trade in all_trades], [
        {"time": trade["entry_time"], "close": trade["entry_price_before_slippage"]}
        for trade in all_trades
    ])
    report = {
        "schema_version": 1,
        "generated_at": iso_utc(datetime.now(UTC)),
        "provider": "Alpaca historical stock bars",
        "feed": download["resolved_feed"],
        "period": {
            "evaluation_start": start.isoformat(),
            "end": end.isoformat(),
            "download_start_with_warmup": download_start.isoformat(),
        },
        "symbols_requested": list(symbols),
        "symbols_returned": [row["symbol"] for row in per_symbol if row["coverage"].get("bars", 0)],
        "strategy": {
            "name": "Obsidian EOD Algo Strategy v4.1",
            "mode": args.strategy_mode,
            "timeframe": "1Min",
            "pine_parameters": {
                "signal_window_minutes": 30,
                "hot_zone_minutes": 10,
                "entry_delay_bars": args.entry_delay,
                "minimum_signal_lead_minutes": args.min_signal_lead,
                "one_trade_per_day": True,
                "mandatory_exit": "15:59 America/New_York bar close",
                "commission": 0.0,
                "slippage_ticks_per_order": 1,
                "tick_size": args.tick_size,
                "rls_lookback_bars": args.rls_lookback,
                "rls_relation": args.rls_relation,
            },
        },
        "download": download,
        "aggregate": aggregate_summary,
        "per_symbol": per_symbol,
        "caveats": [
            "This is an OHLCV-bar reconstruction, not TradingView's exact proprietary data stream.",
            "Entries use delayed-bar closes because process_orders_on_close=true; no ATR stop/target is added because the pasted Pine code has none.",
            "One-tick slippage uses $0.01 per share for every symbol; commissions are zero as specified.",
            "A 390-bar day (09:30 through 15:59 ET) is the completeness diagnostic for timestamped one-minute bars. Incomplete days remain visible in coverage.",
            "Results are research estimates, not live-trading evidence or financial advice.",
        ],
    }
    json_path = Path(args.json_out).resolve()
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    write_csv(Path(args.csv_out).resolve(), per_symbol)
    print(json.dumps({"json": str(json_path), "csv": str(Path(args.csv_out).resolve()), "aggregate": aggregate_summary}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
