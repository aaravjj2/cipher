"""Download and analyze Discord watchlist option history.

Research-only utility. It downloads historical option daily bars from Tradier's
market-data history endpoint and writes local reports. It never calls account,
preview, order, or trading endpoints.
"""
from __future__ import annotations

import csv
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


CORE_DIR = Path(__file__).resolve().parent
ROOT = CORE_DIR.parents[0]
DATA = ROOT / "data"
OUT = DATA / "watchlist_analysis"
RAW = OUT / "option_history_raw"

if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

import tradier_stream_capture as tradier  # noqa: E402


MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


@dataclass
class Trade:
    ticker: str
    strike: float | None
    option_type: str
    expiry: str | None
    alert_at: str
    posted_best_pct: float | None = None
    posted_outcome: str | None = None
    note: str = ""


def d(month_day: str) -> str:
    raw_month, raw_day = month_day.split()
    return date(2026, MONTHS[raw_month.lower()], int(raw_day)).isoformat()


TRADES: list[Trade] = [
    # Week of July 2.
    Trade("JNJ", 250, "put", d("July 10"), "2026-07-02 12:27", 23, "win"),
    Trade("ADBE", 217.5, "put", d("July 2"), "2026-07-02 12:37", None, None, "Omitted from recap."),
    Trade("CCL", 25, "put", d("September 18"), "2026-07-02 15:10", 53, "win"),
    Trade("NOK", 15, "call", d("August 21"), "2026-07-02 15:15", 20, "win"),
    Trade("PATH", 13, "call", d("July 31"), "2026-07-06 10:38", 46, "win"),
    Trade("ZETA", 25, "call", d("July 24"), "2026-07-06 13:04", 26, "win"),
    Trade("DELL", 460, "call", d("July 10"), "2026-07-07 09:48", 156, "win"),
    Trade("CVX", 177.5, "call", d("July 10"), "2026-07-07 10:36", 205, "win"),
    Trade("HIMS", 40, "call", d("July 17"), "2026-07-08 09:44", None, "loss"),
    Trade("IONQ", 40, "put", d("July 10"), "2026-07-08 11:15", None, "loss", "Marked lotto/risky/2DTE."),
    Trade("XYZ", 80, "call", d("July 10"), "2026-07-09 14:02", 231, "win"),
    Trade("BKNG", 177.5, "call", d("July 10"), "2026-07-09 15:47", 312, "win"),
    Trade("COHR", 300, "put", d("July 10"), "2026-07-10 10:03", None, "loss"),
    # Week of July 13.
    Trade("WMT", 118, "call", None, "2026-07-13 10:00", None, "loss", "Strike inferred from 'run to 118'; expiry not in pasted text."),
    Trade("S", 19.5, "call", d("July 17"), "2026-07-13 10:34", 356, "win"),
    Trade("SNOW", 290, "call", d("July 17"), "2026-07-13 13:05", 62, "win"),
    Trade("RDW", 9, "put", d("July 17"), "2026-07-13 13:12", 525, "win"),
    Trade("CRML", 9.5, "call", d("August 7"), "2026-07-13 13:20", 50, "win"),
    Trade("NOG", 22, "call", d("July 17"), "2026-07-13 14:32", None, "loss"),
    Trade("CME", 255, "call", d("July 17"), "2026-07-14 12:50", None, "loss"),
    Trade("TEM", 50, "put", d("July 17"), "2026-07-16 15:41", 166, "win"),
    Trade("IWM", 290, "put", d("July 17"), "2026-07-16 15:47", 172, "win"),
    Trade("NOK", 11, "call", d("July 24"), "2026-07-17 13:31", 40, "win"),
    # Week of July 20.
    Trade("COST", 910, "put", d("July 24"), "2026-07-20 11:49", 104, "win"),
    Trade("POET", 9, "call", d("July 31"), "2026-07-20 12:54", 110, "win"),
    Trade("ZS", 160, "call", d("July 24"), "2026-07-20 12:55", None, "loss"),
    Trade("CRH", 95, "put", d("July 24"), "2026-07-20 13:51", None, "loss"),
    Trade("SMMT", 16, "call", d("July 24"), "2026-07-21 10:52", 117, "win"),
    Trade("SPY", 741, "put", d("July 23"), "2026-07-21 12:14", 348, "win"),
    Trade("CVNA", 62, "put", d("July 24"), "2026-07-22 14:14", 164, "win"),
    Trade("AMBA", 60, "put", d("July 24"), "2026-07-22 14:28", 112, "win"),
    Trade("SPY", 731, "put", d("July 23"), "2026-07-23 11:02", 123, "win", "Marked lotto."),
    Trade("META", 700, "call", d("July 31"), "2026-07-23 12:02", None, None, "No recap outcome in pasted text."),
    Trade("FUTU", 95, "put", d("July 24"), "2026-07-23 13:30", None, "loss"),
    Trade("TXN", 300, "call", d("July 24"), "2026-07-23 19:14", None, "win", "Strike/expiry inferred from text; posted after close."),
]


def occ_symbol(ticker: str, expiry: str, option_type: str, strike: float) -> str:
    exp = date.fromisoformat(expiry)
    cp = "C" if option_type.lower().startswith("c") else "P"
    strike_int = int(round(float(strike) * 1000))
    return f"{ticker.upper()}{exp:%y%m%d}{cp}{strike_int:08d}"


def num(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        value = float(value)
        if math.isnan(value):
            return None
        return value
    except Exception:
        return None


def tradier_get(path: str, params: dict[str, Any]) -> dict[str, Any]:
    token, _ = tradier.load_credentials("production")
    url = f"https://api.tradier.com{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def history(symbol: str, start: str, end: str) -> list[dict[str, Any]]:
    payload = tradier_get("/v1/markets/history", {"symbol": symbol, "interval": "daily", "start": start, "end": end})
    day = (payload.get("history") or {}).get("day") or []
    if isinstance(day, dict):
        day = [day]
    return [dict(row) for row in day]


def underlying_history(ticker: str, start: str, end: str) -> list[dict[str, Any]]:
    payload = tradier_get("/v1/markets/history", {"symbol": ticker.upper(), "interval": "daily", "start": start, "end": end})
    day = (payload.get("history") or {}).get("day") or []
    if isinstance(day, dict):
        day = [day]
    return [dict(row) for row in day]


def trade_window(trade: Trade) -> tuple[str, str]:
    alert_day = trade.alert_at[:10]
    end = trade.expiry or date.today().isoformat()
    if end > date.today().isoformat():
        end = date.today().isoformat()
    start = (date.fromisoformat(alert_day) - timedelta(days=1)).isoformat()
    return start, end


def analyze_trade(trade: Trade) -> dict[str, Any]:
    row = asdict(trade)
    row["occ_symbol"] = None
    row["data_status"] = "missing_contract"
    row["history_rows"] = 0
    row["entry_open"] = row["entry_close"] = row["max_high"] = row["min_low"] = row["last_close"] = None
    row["max_from_open_pct"] = row["close_from_open_pct"] = row["worst_from_open_pct"] = None
    row["underlying_entry_open"] = row["underlying_max_high"] = row["underlying_min_low"] = row["underlying_last_close"] = None
    row["underlying_directional_move_pct"] = row["underlying_best_toward_pct"] = row["moneyness_at_open_pct"] = None
    row["hit_posted_best"] = None
    row["warning"] = ""
    if trade.strike is None or trade.expiry is None:
        return row
    symbol = occ_symbol(trade.ticker, trade.expiry, trade.option_type, trade.strike)
    row["occ_symbol"] = symbol
    start, end = trade_window(trade)
    RAW.mkdir(parents=True, exist_ok=True)
    raw_path = RAW / f"{symbol}_{start}_{end}.json"
    try:
        bars = history(symbol, start, end)
        raw_path.write_text(json.dumps({"symbol": symbol, "start": start, "end": end, "bars": bars}, indent=2), encoding="utf-8")
    except Exception as exc:
        row["data_status"] = "download_error"
        row["warning"] = str(exc)[:300]
        return row
    row["history_rows"] = len(bars)
    if not bars:
        row["data_status"] = "no_history"
        return row
    try:
        u_bars = underlying_history(trade.ticker, start, end)
        (RAW / f"{trade.ticker.upper()}_underlying_{start}_{end}.json").write_text(
            json.dumps({"symbol": trade.ticker.upper(), "start": start, "end": end, "bars": u_bars}, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        u_bars = []
        row["warning"] = f"underlying_history_error: {str(exc)[:160]}"
    alert_day = trade.alert_at[:10]
    entry_bar = next((bar for bar in bars if bar.get("date") == alert_day), bars[0])
    entry_open = num(entry_bar.get("open")) or num(entry_bar.get("close"))
    max_high = max((num(bar.get("high")) for bar in bars if num(bar.get("high")) is not None), default=None)
    min_low = min((num(bar.get("low")) for bar in bars if num(bar.get("low")) is not None), default=None)
    last_close = next((num(bar.get("close")) for bar in reversed(bars) if num(bar.get("close")) is not None), None)
    row["data_status"] = "ok"
    row["entry_open"] = entry_open
    row["entry_close"] = num(entry_bar.get("close"))
    row["max_high"] = max_high
    row["min_low"] = min_low
    row["last_close"] = last_close
    if entry_open and entry_open > 0:
        if max_high is not None:
            row["max_from_open_pct"] = round((max_high - entry_open) / entry_open * 100, 2)
        if last_close is not None:
            row["close_from_open_pct"] = round((last_close - entry_open) / entry_open * 100, 2)
        if min_low is not None:
            row["worst_from_open_pct"] = round((min_low - entry_open) / entry_open * 100, 2)
    if u_bars:
        u_entry_bar = next((bar for bar in u_bars if bar.get("date") == alert_day), u_bars[0])
        u_entry = num(u_entry_bar.get("open")) or num(u_entry_bar.get("close"))
        u_high = max((num(bar.get("high")) for bar in u_bars if num(bar.get("high")) is not None), default=None)
        u_low = min((num(bar.get("low")) for bar in u_bars if num(bar.get("low")) is not None), default=None)
        u_last = next((num(bar.get("close")) for bar in reversed(u_bars) if num(bar.get("close")) is not None), None)
        row["underlying_entry_open"] = u_entry
        row["underlying_max_high"] = u_high
        row["underlying_min_low"] = u_low
        row["underlying_last_close"] = u_last
        if u_entry and u_entry > 0:
            if trade.option_type == "call":
                row["underlying_directional_move_pct"] = round((u_last - u_entry) / u_entry * 100, 3) if u_last is not None else None
                row["underlying_best_toward_pct"] = round((u_high - u_entry) / u_entry * 100, 3) if u_high is not None else None
                row["moneyness_at_open_pct"] = round((u_entry - trade.strike) / u_entry * 100, 3)
            else:
                row["underlying_directional_move_pct"] = round((u_entry - u_last) / u_entry * 100, 3) if u_last is not None else None
                row["underlying_best_toward_pct"] = round((u_entry - u_low) / u_entry * 100, 3) if u_low is not None else None
                row["moneyness_at_open_pct"] = round((trade.strike - u_entry) / u_entry * 100, 3)
    if trade.posted_best_pct is not None and row["max_from_open_pct"] is not None:
        row["hit_posted_best"] = row["max_from_open_pct"] >= trade.posted_best_pct
    return row


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [r for r in rows if r["data_status"] == "ok"]
    closed_labeled = [r for r in rows if r.get("posted_outcome") in {"win", "loss"}]
    wins = [r for r in closed_labeled if r.get("posted_outcome") == "win"]
    losses = [r for r in closed_labeled if r.get("posted_outcome") == "loss"]
    option_green = [r for r in ok if (r.get("max_from_open_pct") or -999) > 0]
    option_50 = [r for r in ok if (r.get("max_from_open_pct") or -999) >= 50]
    option_100 = [r for r in ok if (r.get("max_from_open_pct") or -999) >= 100]
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "trade_count": len(rows),
        "download_ok": len(ok),
        "download_missing_or_error": len(rows) - len(ok),
        "labeled_count": len(closed_labeled),
        "posted_wins": len(wins),
        "posted_losses": len(losses),
        "posted_win_rate_pct": round(len(wins) / len(closed_labeled) * 100, 2) if closed_labeled else None,
        "daily_open_proxy_green_count": len(option_green),
        "daily_open_proxy_green_rate_pct": round(len(option_green) / len(ok) * 100, 2) if ok else None,
        "daily_open_proxy_50pct_runner_count": len(option_50),
        "daily_open_proxy_100pct_runner_count": len(option_100),
        "caveat": "Uses Tradier daily option OHLCV. Entry is alert-date daily open/close proxy, not exact Discord alert-time fill.",
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    keys = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def write_md(path: Path, summary: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    ok = [r for r in rows if r["data_status"] == "ok"]
    best = sorted(ok, key=lambda r: r.get("max_from_open_pct") if r.get("max_from_open_pct") is not None else -999, reverse=True)[:12]
    worst = sorted(ok, key=lambda r: r.get("close_from_open_pct") if r.get("close_from_open_pct") is not None else 999)[:12]
    lines = [
        "# Watchlist Option History Analysis",
        "",
        f"Generated: `{summary['generated_at']}`",
        "",
        "## Summary",
        "",
    ]
    for key, value in summary.items():
        lines.append(f"- `{key}`: {value}")
    lines += [
        "",
        "## Best Max Runs From Alert-Day Open Proxy",
        "",
        "| Trade | Alert | Expiry | Posted | Entry open | Max high | Max % | Last close % | Status |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for r in best:
        posted = "" if r.get("posted_best_pct") is None else f"{r['posted_best_pct']}%"
        lines.append(
            f"| {r['ticker']} {r['strike']:g} {r['option_type']} | {r['alert_at']} | {r['expiry']} | {posted} | "
            f"{r.get('entry_open')} | {r.get('max_high')} | {r.get('max_from_open_pct')}% | "
            f"{r.get('close_from_open_pct')}% | {r.get('posted_outcome') or ''} |"
        )
    lines += [
        "",
        "## Worst Close Outcomes From Alert-Day Open Proxy",
        "",
        "| Trade | Alert | Expiry | Posted outcome | Entry open | Last close | Close % | Max % | Worst % | Note |",
        "|---|---:|---:|---|---:|---:|---:|---:|---:|---|",
    ]
    for r in worst:
        lines.append(
            f"| {r['ticker']} {r['strike']:g} {r['option_type']} | {r['alert_at']} | {r['expiry']} | "
            f"{r.get('posted_outcome') or ''} | {r.get('entry_open')} | {r.get('last_close')} | "
            f"{r.get('close_from_open_pct')}% | {r.get('max_from_open_pct')}% | {r.get('worst_from_open_pct')}% | {r.get('note') or ''} |"
        )
    lines += [
        "",
        "## Missing Or Incomplete",
        "",
    ]
    for r in rows:
        if r["data_status"] != "ok":
            lines.append(f"- {r['ticker']} {r.get('strike')} {r['option_type']} exp={r.get('expiry')}: `{r['data_status']}` {r.get('warning') or r.get('note') or ''}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for trade in TRADES:
        rows.append(analyze_trade(trade))
        time.sleep(0.05)
    summary = summarize(rows)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = OUT / f"watchlist_option_history_{stamp}.json"
    csv_path = OUT / f"watchlist_option_history_{stamp}.csv"
    md_path = OUT / f"watchlist_option_history_{stamp}.md"
    latest = OUT / "latest_watchlist_option_history.json"
    payload = {"summary": summary, "rows": rows}
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    latest.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    write_csv(csv_path, rows)
    write_md(md_path, summary, rows)
    print(json.dumps({"summary": summary, "paths": {"json": str(json_path), "csv": str(csv_path), "md": str(md_path), "latest": str(latest)}}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
