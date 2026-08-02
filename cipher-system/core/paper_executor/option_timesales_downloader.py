from __future__ import annotations

import argparse
import csv
import json
import math
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import MarketDataConfig
from .contract_selector import contracts_from_chain
from .models import OptionType
from .tradier_market_data import TradierMarketData


def parse_dt(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)


def local_market_text(day: str, hhmm: str) -> str:
    return f"{day} {hhmm}"


def num(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def bar_time(row: dict[str, Any]) -> datetime | None:
    text = str(row.get("time") or row.get("date") or row.get("timestamp") or "")
    if not text:
        return None
    if "T" in text:
        try:
            return parse_dt(text)
        except ValueError:
            pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text[:19], fmt).replace(tzinfo=datetime.now().astimezone().tzinfo).astimezone(timezone.utc)
        except ValueError:
            continue
    return None


def bar_mark(row: dict[str, Any]) -> float | None:
    close = num(row.get("close"))
    if close is not None and close > 0:
        return close
    price = num(row.get("price"))
    if price is not None and price > 0:
        return price
    bid = num(row.get("bid"))
    ask = num(row.get("ask"))
    if bid is not None and ask is not None and ask > 0:
        return round((bid + ask) / 2, 4)
    return None


def choose_expiration(md: TradierMarketData, ticker: str, trade_day: str, minimum_dte: int, maximum_dte: int) -> str | None:
    base = datetime.fromisoformat(trade_day).date()
    choices = []
    for exp in md.expirations(ticker):
        try:
            dte = (datetime.fromisoformat(exp).date() - base).days
        except ValueError:
            continue
        if minimum_dte <= dte <= maximum_dte:
            choices.append((dte, exp))
    return sorted(choices)[0][1] if choices else None


def choose_contract(md: TradierMarketData, trade: dict[str, Any], expiration: str) -> str | None:
    ticker = str(trade["ticker"]).upper()
    option_type = OptionType.CALL if trade["direction"] == "bullish" else OptionType.PUT
    spot = float(trade["entry_spot"])
    contracts = contracts_from_chain(ticker, md.chain(ticker, expiration), option_type)
    if not contracts:
        return None
    contracts.sort(key=lambda c: (abs(c.strike - spot), c.strike, c.symbol))
    return contracts[0].symbol


def load_or_download(md: TradierMarketData, symbol: str, trade_day: str, out_dir: Path, interval: str, pause_seconds: float) -> tuple[list[dict[str, Any]], bool, str | None]:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{symbol}_{trade_day}_{interval}.json"
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        return list(payload.get("bars") or []), False, None
    try:
        bars = md.timesales(
            symbol,
            start=local_market_text(trade_day, "09:30"),
            end=local_market_text(trade_day, "16:00"),
            interval=interval,
            session_filter="open",
        )
        payload = {
            "downloaded_at": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "trade_date": trade_day,
            "interval": interval,
            "bars": bars,
            "source": "tradier_market_data_timesales",
            "market_data_only": True,
        }
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        time.sleep(pause_seconds)
        return bars, True, None
    except Exception as exc:
        time.sleep(pause_seconds)
        return [], False, str(exc)


def nearest_mark(bars: list[dict[str, Any]], when: datetime, *, after: bool) -> tuple[float | None, str | None]:
    candidates = []
    for row in bars:
        ts = bar_time(row)
        mark = bar_mark(row)
        if ts is None or mark is None:
            continue
        delta = (ts - when).total_seconds()
        if after and delta < -90:
            continue
        candidates.append((abs(delta), ts, mark))
    if not candidates:
        return None, None
    _, ts, mark = sorted(candidates, key=lambda item: item[0])[0]
    return mark, ts.isoformat()


def enrich_backtest(backtest_path: Path, out_dir: Path, interval: str, minimum_dte: int, maximum_dte: int, max_symbols: int | None, pause_seconds: float) -> dict[str, Any]:
    report = json.loads(backtest_path.read_text(encoding="utf-8"))
    trade_day = str(report["trade_date"])
    md = TradierMarketData(MarketDataConfig())
    expirations: dict[str, str | None] = {}
    chains: dict[tuple[str, str], list[dict[str, Any]]] = {}
    option_symbols: dict[int, str | None] = {}
    selected_unique: set[str] = set()
    errors: list[dict[str, Any]] = []
    for idx, trade in enumerate(report["trades"]):
        if max_symbols is not None and len(selected_unique) >= max_symbols:
            option_symbols[idx] = None
            continue
        ticker = str(trade["ticker"]).upper()
        if ticker not in expirations:
            try:
                expirations[ticker] = choose_expiration(md, ticker, trade_day, minimum_dte, maximum_dte)
                time.sleep(pause_seconds)
            except Exception as exc:
                expirations[ticker] = None
                errors.append({"ticker": ticker, "stage": "expirations", "error": str(exc)})
        exp = expirations[ticker]
        if not exp:
            option_symbols[idx] = None
            continue
        try:
            key = (ticker, exp)
            if key not in chains:
                chains[key] = md.chain(ticker, exp)
                time.sleep(pause_seconds)
            option_type = OptionType.CALL if trade["direction"] == "bullish" else OptionType.PUT
            spot = float(trade["entry_spot"])
            contracts = contracts_from_chain(ticker, chains[key], option_type)
            contracts.sort(key=lambda c: (abs(c.strike - spot), c.strike, c.symbol))
            symbol = contracts[0].symbol if contracts else None
            option_symbols[idx] = symbol
            if symbol:
                selected_unique.add(symbol)
        except Exception as exc:
            option_symbols[idx] = None
            errors.append({"ticker": ticker, "stage": "chain", "expiration": exp, "error": str(exc)})
    unique_symbols = sorted({sym for sym in option_symbols.values() if sym})
    allowed = set(unique_symbols)
    bars_by_symbol: dict[str, list[dict[str, Any]]] = {}
    downloaded = cached = failed = 0
    for symbol in unique_symbols:
        bars, did_download, error = load_or_download(md, symbol, trade_day, out_dir / "timesales", interval, pause_seconds)
        if did_download:
            downloaded += 1
        else:
            cached += 1
        if error:
            failed += 1
            errors.append({"symbol": symbol, "stage": "timesales", "error": error})
        bars_by_symbol[symbol] = bars
    enriched = []
    marked = 0
    for idx, trade in enumerate(report["trades"]):
        row = dict(trade)
        symbol = option_symbols.get(idx)
        row["real_option_symbol"] = symbol
        row["real_option_interval"] = interval
        if symbol and symbol in allowed:
            bars = bars_by_symbol.get(symbol) or []
            entry_mark, entry_time = nearest_mark(bars, parse_dt(row["entry_time"]), after=True)
            exit_mark, exit_time = nearest_mark(bars, parse_dt(row["exit_time"]), after=False)
            row["real_entry_option_mark"] = entry_mark
            row["real_entry_option_time"] = entry_time
            row["real_exit_option_mark"] = exit_mark
            row["real_exit_option_time"] = exit_time
            if entry_mark is not None and exit_mark is not None and entry_mark > 0:
                row["real_option_pnl_dollars"] = round((exit_mark - entry_mark) * 100, 2)
                row["real_option_pnl_pct"] = round((exit_mark - entry_mark) / entry_mark * 100, 2)
                row["real_win"] = exit_mark > entry_mark
                marked += 1
        enriched.append(row)
    report["trades"] = enriched
    report["real_option_download"] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_backtest": str(backtest_path),
        "trade_date": trade_day,
        "interval": interval,
        "minimum_dte": minimum_dte,
        "maximum_dte": maximum_dte,
        "unique_symbols_selected": len({sym for sym in option_symbols.values() if sym}),
        "unique_symbols_attempted": len(unique_symbols),
        "downloaded": downloaded,
        "cached": cached,
        "failed": failed,
        "trades_with_real_marks": marked,
        "errors": errors[:100],
        "market_data_only": True,
    }
    report["real_option_summary"] = summarize_real(enriched)
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    json_path = out_dir / f"today_options_real_marks_{trade_day.replace('-', '')}_{stamp}.json"
    csv_path = out_dir / f"today_options_real_marks_{trade_day.replace('-', '')}_{stamp}.csv"
    md_path = out_dir / f"today_options_real_marks_{trade_day.replace('-', '')}_{stamp}.md"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    fields = sorted({key for row in enriched for key in row})
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(enriched)
    write_markdown(md_path, report)
    return {"report": report["real_option_download"], "summary": report["real_option_summary"], "paths": {"json": str(json_path), "csv": str(csv_path), "markdown": str(md_path)}}


def summarize_real(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in trades:
        if row.get("real_option_pnl_dollars") is not None:
            groups[str(row.get("scan_type"))].append(row)
    out = []
    for scan_type, rows in groups.items():
        pnl = [float(row["real_option_pnl_dollars"]) for row in rows]
        wins = sum(1 for row in rows if row.get("real_win"))
        out.append({
            "scan_type": scan_type,
            "trades_with_real_marks": len(rows),
            "wins": wins,
            "win_rate": round(wins / len(rows) * 100, 2) if rows else 0,
            "total_real_option_pnl_dollars": round(math.fsum(pnl), 2),
            "average_real_option_pnl_pct": round(math.fsum(float(row["real_option_pnl_pct"]) for row in rows) / len(rows), 2) if rows else 0,
        })
    return sorted(out, key=lambda row: row["total_real_option_pnl_dollars"], reverse=True)


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Today Options Real Marks",
        "",
        f"Generated: {report['real_option_download']['generated_at']}",
        f"Source backtest: `{report['real_option_download']['source_backtest']}`",
        f"Interval: `{report['real_option_download']['interval']}`",
        "",
        "| Scan | Trades With Marks | Win Rate | Total Real Option P/L | Avg Real Option P/L % |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in report["real_option_summary"]:
        lines.append(
            f"| {row['scan_type']} | {row['trades_with_real_marks']} | {row['win_rate']} | "
            f"{row['total_real_option_pnl_dollars']} | {row['average_real_option_pnl_pct']} |"
        )
    lines += [
        "",
        "Market-data-only download. No account, order, position, balance, or trading endpoints are used.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Download Tradier option timesales for a capture backtest and enrich it with real option marks.")
    parser.add_argument("--backtest", required=True)
    parser.add_argument("--out-dir", default=r"C:\Aarav\cipher-system\CipherCapture\data\backtests")
    parser.add_argument("--interval", default="1min", choices=("1min", "5min", "15min"))
    parser.add_argument("--minimum-dte", type=int, default=1)
    parser.add_argument("--maximum-dte", type=int, default=3)
    parser.add_argument("--max-symbols", type=int)
    parser.add_argument("--pause-seconds", type=float, default=0.08)
    args = parser.parse_args()
    result = enrich_backtest(Path(args.backtest), Path(args.out_dir), args.interval, args.minimum_dte, args.maximum_dte, args.max_symbols, args.pause_seconds)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
