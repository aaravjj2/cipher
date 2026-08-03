#!/usr/bin/env python3
"""Audit Zen watchlist option alerts using first observed mark as entry."""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time as sleep_time
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.historical_options_download import (  # noqa: E402
    DATA_BASE,
    PAPER_BASE,
    DownloadError,
    HistoricalOptionsStore,
    JsonHttpClient,
    alpaca_credentials,
    iso_utc,
    number,
)

NY = ZoneInfo("America/New_York")
UTC = timezone.utc
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


@dataclass(frozen=True)
class WatchlistTrade:
    alert_at: datetime
    underlying: str
    strike: float
    option_type: str
    expiration: date

    @property
    def key(self) -> str:
        side = "C" if self.option_type == "call" else "P"
        return f"{self.underlying} {self.expiration.isoformat()} {self.strike:g}{side}"


@dataclass(frozen=True)
class WatchlistUpdate:
    update_at: datetime
    underlying: str
    strike: float
    option_type: str
    expiration: date
    posted_pct: float

    @property
    def key(self) -> str:
        side = "C" if self.option_type == "call" else "P"
        return f"{self.underlying} {self.expiration.isoformat()} {self.strike:g}{side}"


@dataclass(frozen=True)
class AuditResult:
    key: str
    alert_at_et: str
    window_end_et: str
    contract_symbol: str | None
    entry_mark: float | None
    entry_at: str | None
    max_observed_price: float | None
    max_observed_at: str | None
    max_profit_pct: float | None
    ever_profitable: bool | None
    hit_20_pct: bool | None
    hit_30_pct: bool | None
    hit_50_pct: bool | None
    hit_100_pct: bool | None
    post_1030_reclaim: bool | None
    post_1030_hit_30_pct: bool | None
    first_30m_low_pct: float | None
    first_hour_close_pct: float | None
    bar_rows: int
    trade_rows: int
    max_posted_update_pct: float | None
    posted_green: bool
    notes: str


def load_local_env() -> None:
    for path in (ROOT / ".env",):
        if not path.exists():
            continue
        for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def parse_header_time(line: str) -> datetime | None:
    match = re.search(r"(\d{1,2})/(\d{1,2})/(\d{2}),\s+(\d{1,2}):(\d{2})\s+(AM|PM)", line, re.I)
    if not match:
        return None
    month, day, year, hour, minute, ampm = match.groups()
    hour_i = int(hour) % 12
    if ampm.upper() == "PM":
        hour_i += 12
    return datetime(2000 + int(year), int(month), int(day), hour_i, int(minute), tzinfo=NY)


def parse_expiration(line: str) -> date | None:
    match = re.search(r"([A-Za-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?\s+expiration", line, re.I)
    if not match:
        return None
    month_name, day = match.groups()
    month = MONTHS.get(month_name.lower())
    if not month:
        return None
    return date(2026, month, int(day))


def parse_contract(line: str) -> tuple[str, float, str] | None:
    match = re.search(r"\$([A-Z][A-Z0-9.]*)\s+(\d+(?:\.\d+)?)\s+(CALL|PUT)", line, re.I)
    if not match:
        return None
    underlying, strike, side = match.groups()
    return underlying.upper(), float(strike), side.lower()


def parse_text(path: Path) -> tuple[list[WatchlistTrade], list[WatchlistUpdate]]:
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    trades: list[WatchlistTrade] = []
    updates: list[WatchlistUpdate] = []
    current_time: datetime | None = None
    i = 0
    while i < len(lines):
        header_time = parse_header_time(lines[i])
        if header_time:
            current_time = header_time
        lower = lines[i].lower()
        if current_time and "watchlist" in lower and ("trade" in lower or "update" in lower):
            event_time = current_time
            is_new = "new" in lower and "trade" in lower
            is_update = "update" in lower
            contract = None
            expiration = None
            posted_pct = None
            footer_time = None
            for j in range(i + 1, min(len(lines), i + 8)):
                contract = contract or parse_contract(lines[j])
                expiration = expiration or parse_expiration(lines[j])
                pct_match = re.search(r"\+(\d+(?:\.\d+)?)%", lines[j])
                if pct_match:
                    posted_pct = float(pct_match.group(1))
                if "Cipher" in lines[j] and "Options Watchlist" in lines[j]:
                    footer_time = parse_header_time(lines[j])
            if contract and expiration:
                underlying, strike, option_type = contract
                item_time = footer_time or event_time
                if is_new:
                    trades.append(WatchlistTrade(item_time, underlying, strike, option_type, expiration))
                elif is_update and posted_pct is not None:
                    updates.append(WatchlistUpdate(item_time, underlying, strike, option_type, expiration, posted_pct))
        i += 1
    unique: dict[tuple[Any, ...], WatchlistTrade] = {}
    for row in trades:
        unique[(row.alert_at, row.underlying, row.strike, row.option_type, row.expiration)] = row
    return sorted(unique.values(), key=lambda row: row.alert_at), updates


def et_iso(value: datetime) -> str:
    return value.astimezone(NY).isoformat()


def alpaca_time(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def window_end(alert_at: datetime, expiration: date) -> datetime:
    return datetime.combine(expiration, time(16, 15), NY)


class Downloader:
    def __init__(self, store: HistoricalOptionsStore, client: JsonHttpClient) -> None:
        self.store = store
        self.client = client

    def _pages(self, *, run_id: int, endpoint: str, base: str, query: dict[str, Any], data_key: str) -> list[Any]:
        rows: list[Any] = []
        token = None
        page = 0
        while True:
            page += 1
            current = dict(query)
            if token:
                current["page_token"] = token
            payload, raw, status = self.client.get(f"{base}{endpoint}", current)
            data = payload.get(data_key) or []
            row_count = sum(len(v or []) for v in data.values()) if isinstance(data, dict) else len(data)
            self.store.archive_page(
                run_id=run_id,
                provider="alpaca",
                endpoint=endpoint,
                query=current,
                raw=raw,
                http_status=status,
                page_number=page,
                row_count=row_count,
                next_page_token_present=bool(payload.get("next_page_token")),
            )
            rows.append(data)
            token = payload.get("next_page_token")
            if not token:
                return rows

    def find_contract(self, run_id: int, trade: WatchlistTrade) -> str | None:
        merged: dict[str, dict[str, Any]] = {}
        for status in ("inactive", "active"):
            pages = self._pages(
                run_id=run_id,
                endpoint="/v2/options/contracts",
                base=PAPER_BASE,
                query={
                    "underlying_symbols": trade.underlying,
                    "status": status,
                    "expiration_date": trade.expiration.isoformat(),
                    "type": trade.option_type,
                    "limit": 1000,
                },
                data_key="option_contracts",
            )
            flat = [row for page in pages if isinstance(page, list) for row in page if isinstance(row, dict)]
            self.store.upsert_contracts(flat, iso_utc(datetime.now(UTC)))
            for row in flat:
                strike = number(row.get("strike_price"))
                if (
                    str(row.get("underlying_symbol") or "").upper() == trade.underlying
                    and str(row.get("expiration_date") or "")[:10] == trade.expiration.isoformat()
                    and str(row.get("type") or "").lower() == trade.option_type
                    and strike is not None
                    and abs(strike - trade.strike) < 0.0001
                ):
                    symbol = str(row.get("symbol") or "").upper()
                    if symbol:
                        merged[symbol] = row
        return sorted(merged)[0] if merged else None

    def download_history(self, run_id: int, symbol: str, start_at: datetime, end_at: datetime) -> None:
        start = alpaca_time(start_at)
        end = alpaca_time(end_at)
        bar_pages = self._pages(
            run_id=run_id,
            endpoint="/v1beta1/options/bars",
            base=DATA_BASE,
            query={"symbols": symbol, "start": start, "end": end, "timeframe": "1Min", "limit": 10000, "sort": "asc"},
            data_key="bars",
        )
        bars: list[dict[str, Any]] = []
        for page in bar_pages:
            if isinstance(page, dict):
                bars.extend(page.get(symbol, []) or [])
        self.store.upsert_option_bars({symbol: bars}, "1Min")
        trade_pages = self._pages(
            run_id=run_id,
            endpoint="/v1beta1/options/trades",
            base=DATA_BASE,
            query={"symbols": symbol, "start": start, "end": end, "limit": 10000, "sort": "asc"},
            data_key="trades",
        )
        trades: list[dict[str, Any]] = []
        for page in trade_pages:
            if isinstance(page, dict):
                trades.extend(page.get(symbol, []) or [])
        self.store.upsert_option_trades({symbol: trades})


def read_bars(store: HistoricalOptionsStore, symbol: str, start_at: datetime, end_at: datetime) -> list[tuple[str, float | None, float | None, float | None, float | None]]:
    with store.connect() as db:
        return db.execute(
            """select timestamp, open, high, low, close from option_bars
               where symbol=? and timestamp>=? and timestamp<=?
               order by timestamp""",
            (symbol, alpaca_time(start_at), alpaca_time(end_at)),
        ).fetchall()


def read_trades_count(store: HistoricalOptionsStore, symbol: str, start_at: datetime, end_at: datetime) -> int:
    with store.connect() as db:
        return int(
            db.execute(
                "select count(*) from option_trades where symbol=? and timestamp>=? and timestamp<=?",
                (symbol, alpaca_time(start_at), alpaca_time(end_at)),
            ).fetchone()[0]
            or 0
        )


def audit_trade(
    store: HistoricalOptionsStore,
    trade: WatchlistTrade,
    symbol: str | None,
    updates_by_key: dict[str, list[WatchlistUpdate]],
) -> AuditResult:
    end_at = window_end(trade.alert_at, trade.expiration)
    max_update = max((u.posted_pct for u in updates_by_key.get(trade.key, [])), default=None)
    if not symbol:
        return AuditResult(
            key=trade.key,
            alert_at_et=et_iso(trade.alert_at),
            window_end_et=et_iso(end_at),
            contract_symbol=None,
            entry_mark=None,
            entry_at=None,
            max_observed_price=None,
            max_observed_at=None,
            max_profit_pct=None,
            ever_profitable=None,
            hit_20_pct=None,
            hit_30_pct=None,
            hit_50_pct=None,
            hit_100_pct=None,
            post_1030_reclaim=None,
            post_1030_hit_30_pct=None,
            first_30m_low_pct=None,
            first_hour_close_pct=None,
            bar_rows=0,
            trade_rows=0,
            max_posted_update_pct=max_update,
            posted_green=max_update is not None,
            notes="No exact Alpaca contract matched the posted underlying/strike/type/expiration.",
        )
    bars = read_bars(store, symbol, trade.alert_at, end_at)
    trade_rows = read_trades_count(store, symbol, trade.alert_at, end_at)
    clean = [row for row in bars if number(row[4]) is not None or number(row[2]) is not None]
    if not clean:
        return AuditResult(
            key=trade.key,
            alert_at_et=et_iso(trade.alert_at),
            window_end_et=et_iso(end_at),
            contract_symbol=symbol,
            entry_mark=None,
            entry_at=None,
            max_observed_price=None,
            max_observed_at=None,
            max_profit_pct=None,
            ever_profitable=None,
            hit_20_pct=None,
            hit_30_pct=None,
            hit_50_pct=None,
            hit_100_pct=None,
            post_1030_reclaim=None,
            post_1030_hit_30_pct=None,
            first_30m_low_pct=None,
            first_hour_close_pct=None,
            bar_rows=len(bars),
            trade_rows=trade_rows,
            max_posted_update_pct=max_update,
            posted_green=max_update is not None,
            notes="Exact contract found, but no option bars were observed after the alert in the audit window.",
        )
    entry = clean[0]
    entry_mark = number(entry[4]) or number(entry[2])
    highs = [(row[0], number(row[2])) for row in clean if number(row[2]) is not None]
    lows = [(row[0], number(row[3])) for row in clean if number(row[3]) is not None]
    max_at, max_price = max(highs, key=lambda item: item[1])
    profit_pct = ((max_price / entry_mark) - 1.0) * 100.0 if entry_mark and max_price is not None else None
    first_30_end = trade.alert_at + timedelta(minutes=30)
    first_hour_end = trade.alert_at + timedelta(minutes=60)
    first_30_lows = [value for ts, value in lows if datetime.fromisoformat(ts.replace("Z", "+00:00")) <= first_30_end.astimezone(UTC)]
    first_hour_closes = [
        (row[0], number(row[4]))
        for row in clean
        if datetime.fromisoformat(row[0].replace("Z", "+00:00")) <= first_hour_end.astimezone(UTC)
        and number(row[4]) is not None
    ]
    first_30m_low_pct = ((min(first_30_lows) / entry_mark) - 1.0) * 100.0 if entry_mark and first_30_lows else None
    first_hour_close_pct = ((first_hour_closes[-1][1] / entry_mark) - 1.0) * 100.0 if entry_mark and first_hour_closes else None
    post_1030_start = datetime.combine(trade.alert_at.date(), time(10, 30), NY)
    post_1030 = [
        row for row in clean if datetime.fromisoformat(row[0].replace("Z", "+00:00")) >= post_1030_start.astimezone(UTC)
    ]
    post_1030_reclaim = any((number(row[2]) or 0.0) >= entry_mark for row in post_1030) if entry_mark else None
    post_1030_hit_30 = any((number(row[2]) or 0.0) >= entry_mark * 1.3 for row in post_1030) if entry_mark else None
    return AuditResult(
        key=trade.key,
        alert_at_et=et_iso(trade.alert_at),
        window_end_et=et_iso(end_at),
        contract_symbol=symbol,
        entry_mark=entry_mark,
        entry_at=entry[0],
        max_observed_price=max_price,
        max_observed_at=max_at,
        max_profit_pct=profit_pct,
        ever_profitable=(profit_pct is not None and profit_pct > 0),
        hit_20_pct=(profit_pct is not None and profit_pct >= 20),
        hit_30_pct=(profit_pct is not None and profit_pct >= 30),
        hit_50_pct=(profit_pct is not None and profit_pct >= 50),
        hit_100_pct=(profit_pct is not None and profit_pct >= 100),
        post_1030_reclaim=post_1030_reclaim,
        post_1030_hit_30_pct=post_1030_hit_30,
        first_30m_low_pct=first_30m_low_pct,
        first_hour_close_pct=first_hour_close_pct,
        bar_rows=len(bars),
        trade_rows=trade_rows,
        max_posted_update_pct=max_update,
        posted_green=max_update is not None,
        notes="Entry is first observed option bar close/high at or after the alert because no premium was included in the pasted text.",
    )


def summarize(results: list[AuditResult]) -> dict[str, Any]:
    observed = [row for row in results if row.entry_mark is not None and row.max_profit_pct is not None]
    return {
        "total_new_trades": len(results),
        "observed_contracts": len(observed),
        "no_exact_contract_or_no_bars": len(results) - len(observed),
        "ever_profitable": sum(1 for row in observed if row.ever_profitable),
        "hit_30_pct": sum(1 for row in observed if row.hit_30_pct),
        "hit_50_pct": sum(1 for row in observed if row.hit_50_pct),
        "hit_100_pct": sum(1 for row in observed if row.hit_100_pct),
        "posted_green_count": sum(1 for row in results if row.posted_green),
        "post_1030_hit_30_count": sum(1 for row in observed if row.post_1030_hit_30_pct),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-root", default=str(ROOT / "data" / "historical_options" / "zen_watchlist_202607"))
    parser.add_argument("--pause-seconds", type=float, default=0.04)
    args = parser.parse_args()

    load_local_env()
    if not os.environ.get("ALPACA_API_SECRET") and os.environ.get("ALPACA_SECRET_KEY"):
        os.environ["ALPACA_API_SECRET"] = os.environ["ALPACA_SECRET_KEY"]
    key, secret, _stock_feed = alpaca_credentials()
    client = JsonHttpClient({"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}, timeout=60, retries=4)
    store = HistoricalOptionsStore(Path(args.output_root))
    trades, updates = parse_text(Path(args.input))
    updates_by_key: dict[str, list[WatchlistUpdate]] = {}
    for row in updates:
        updates_by_key.setdefault(row.key, []).append(row)

    run_id = store.start_run(
        {
            "underlying": "ZEN_WATCHLIST",
            "start_date": min(row.alert_at.date() for row in trades).isoformat() if trades else "",
            "end_date": max(row.expiration for row in trades).isoformat() if trades else "",
            "config": "Zen watchlist audit; entry is first observed option bar at/after alert",
            "input": str(Path(args.input)),
            "new_trades": len(trades),
            "updates": len(updates),
        }
    )
    downloader = Downloader(store, client)
    results: list[AuditResult] = []
    try:
        for idx, trade in enumerate(trades, start=1):
            symbol = downloader.find_contract(run_id, trade)
            end_at = window_end(trade.alert_at, trade.expiration)
            if symbol:
                downloader.download_history(run_id, symbol, trade.alert_at, end_at)
                sleep_time.sleep(args.pause_seconds)
            result = audit_trade(store, trade, symbol, updates_by_key)
            results.append(result)
            print(f"{idx}/{len(trades)} {trade.key} -> {symbol or 'NO_CONTRACT'} max={result.max_profit_pct}")
        output_root = Path(args.output_root)
        json_path = output_root / "zen_watchlist_profitability_report.json"
        csv_path = output_root / "zen_watchlist_profitability_report.csv"
        summary_path = output_root / "zen_watchlist_profitability_summary.json"
        json_path.write_text(json.dumps([asdict(row) for row in results], indent=2), encoding="utf-8")
        summary = summarize(results)
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        with csv_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(asdict(results[0]).keys()))
            writer.writeheader()
            writer.writerows(asdict(row) for row in results)
        store.finish_run(run_id, "complete", {"summary": summary, "results": [asdict(row) for row in results]})
        print(json.dumps({"csv": str(csv_path), "json": str(json_path), "summary": summary}, indent=2))
        return 0
    except Exception as exc:
        store.finish_run(run_id, "failed", {}, str(exc))
        raise DownloadError(str(exc)) from exc


if __name__ == "__main__":
    raise SystemExit(main())
