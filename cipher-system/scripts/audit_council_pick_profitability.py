#!/usr/bin/env python3
"""Download exact council-pick option history and audit week profitability."""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
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
    stable_json,
)

NY = ZoneInfo("America/New_York")
UTC = timezone.utc


@dataclass(frozen=True)
class Pick:
    alert_date: date
    underlying: str
    option_type: str
    strike: float
    expiration: date
    premium: float
    stock_price: float

    @property
    def label(self) -> str:
        side = "C" if self.option_type == "call" else "P"
        return f"{self.underlying} {self.expiration.isoformat()} {self.strike:g}{side}"


@dataclass(frozen=True)
class AuditResult:
    label: str
    alert_date: str
    week_start: str
    week_end: str
    contract_symbol: str | None
    entry_premium: float
    max_bar_high: float | None
    max_trade_price: float | None
    max_observed_price: float | None
    max_profit_pct: float | None
    ever_profitable: bool | None
    bar_rows: int
    trade_rows: int
    first_observed_at: str | None
    max_observed_at: str | None
    notes: str


PICKS = (
    Pick(date(2026, 7, 12), "HOOD", "call", 130.0, date(2026, 7, 31), 2.45, 111.57),
    Pick(date(2026, 7, 19), "MSFT", "call", 410.0, date(2026, 7, 24), 3.10, 394.01),
    Pick(date(2026, 7, 26), "ONDS", "call", 7.5, date(2026, 8, 14), 1.03, 7.83),
    Pick(date(2026, 8, 2), "MSFT", "put", 447.5, date(2026, 8, 5), 2.00, 461.99),
)


def ny_iso(day: date, value: time) -> str:
    return datetime.combine(day, value, NY).astimezone(UTC).isoformat().replace("+00:00", "Z")


def week_window(pick: Pick) -> tuple[date, date, str, str]:
    start_day = pick.alert_date + timedelta(days=1)
    end_day = min(start_day + timedelta(days=4), pick.expiration)
    return (
        start_day,
        end_day,
        ny_iso(start_day, time(9, 30)),
        ny_iso(end_day, time(16, 15)),
    )


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


class ExactPickDownloader:
    def __init__(self, store: HistoricalOptionsStore, client: JsonHttpClient, stock_feed: str) -> None:
        self.store = store
        self.client = client
        self.stock_feed = stock_feed

    def _get_pages(
        self,
        *,
        run_id: int,
        url: str,
        endpoint: str,
        query: dict[str, Any],
        data_key: str,
    ) -> list[Any]:
        rows: list[Any] = []
        page = 0
        token: str | None = None
        while True:
            page += 1
            current = dict(query)
            if token:
                current["page_token"] = token
            payload, raw, status = self.client.get(url, current)
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
            if isinstance(data, list):
                rows.extend(data)
            else:
                rows.append(data)
            token = payload.get("next_page_token")
            if not token:
                return rows

    def find_contract(self, run_id: int, pick: Pick) -> str | None:
        endpoint = "/v2/options/contracts"
        merged: dict[str, dict[str, Any]] = {}
        for status_name in ("inactive", "active"):
            rows = self._get_pages(
                run_id=run_id,
                url=f"{PAPER_BASE}{endpoint}",
                endpoint=f"{endpoint}_{status_name}",
                query={
                    "underlying_symbols": pick.underlying,
                    "status": status_name,
                    "expiration_date": pick.expiration.isoformat(),
                    "type": pick.option_type,
                    "strike_price_gte": f"{pick.strike:.4f}",
                    "strike_price_lte": f"{pick.strike:.4f}",
                    "limit": 1000,
                },
                data_key="option_contracts",
            )
            flat = [row for row in rows if isinstance(row, dict)]
            self.store.upsert_contracts(flat, iso_utc(datetime.now(UTC)))
            for row in flat:
                symbol = str(row.get("symbol") or "").upper()
                strike = number(row.get("strike_price"))
                expiry = str(row.get("expiration_date") or "")[:10]
                kind = str(row.get("type") or "").lower()
                if (
                    symbol
                    and expiry == pick.expiration.isoformat()
                    and kind == pick.option_type
                    and strike is not None
                    and abs(strike - pick.strike) < 0.0001
                ):
                    merged[symbol] = row
        return sorted(merged)[0] if merged else None

    def download_symbol_history(self, run_id: int, symbol: str, start_at: str, end_at: str) -> None:
        bar_pages = self._get_pages(
            run_id=run_id,
            url=f"{DATA_BASE}/v1beta1/options/bars",
            endpoint="/v1beta1/options/bars",
            query={
                "symbols": symbol,
                "start": start_at,
                "end": end_at,
                "timeframe": "1Min",
                "limit": 10000,
                "sort": "asc",
            },
            data_key="bars",
        )
        bars: list[dict[str, Any]] = []
        for page in bar_pages:
            if isinstance(page, dict):
                bars.extend(page.get(symbol, []) or [])
        self.store.upsert_option_bars({symbol: bars}, "1Min")

        trade_pages = self._get_pages(
            run_id=run_id,
            url=f"{DATA_BASE}/v1beta1/options/trades",
            endpoint="/v1beta1/options/trades",
            query={
                "symbols": symbol,
                "start": start_at,
                "end": end_at,
                "limit": 10000,
                "sort": "asc",
            },
            data_key="trades",
        )
        trades: list[dict[str, Any]] = []
        for page in trade_pages:
            if isinstance(page, dict):
                trades.extend(page.get(symbol, []) or [])
        self.store.upsert_option_trades({symbol: trades})


def audit_pick(store: HistoricalOptionsStore, pick: Pick, symbol: str | None, week_start: date, week_end: date) -> AuditResult:
    if symbol is None:
        return AuditResult(
            label=pick.label,
            alert_date=pick.alert_date.isoformat(),
            week_start=week_start.isoformat(),
            week_end=week_end.isoformat(),
            contract_symbol=None,
            entry_premium=pick.premium,
            max_bar_high=None,
            max_trade_price=None,
            max_observed_price=None,
            max_profit_pct=None,
            ever_profitable=None,
            bar_rows=0,
            trade_rows=0,
            first_observed_at=None,
            max_observed_at=None,
            notes="No exact contract was returned by Alpaca.",
        )

    with store.connect() as db:
        bar = db.execute(
            """select count(*), min(timestamp), max(high) from option_bars
               where symbol=? and date(timestamp) between ? and ?""",
            (symbol, week_start.isoformat(), week_end.isoformat()),
        ).fetchone()
        trade = db.execute(
            """select count(*), min(timestamp), max(price) from option_trades
               where symbol=? and date(timestamp) between ? and ?""",
            (symbol, week_start.isoformat(), week_end.isoformat()),
        ).fetchone()
        max_row = db.execute(
            """select timestamp, price from (
                   select timestamp, high as price from option_bars
                   where symbol=? and date(timestamp) between ? and ?
                   union all
                   select timestamp, price from option_trades
                   where symbol=? and date(timestamp) between ? and ?
               ) where price is not null order by price desc, timestamp asc limit 1""",
            (
                symbol,
                week_start.isoformat(),
                week_end.isoformat(),
                symbol,
                week_start.isoformat(),
                week_end.isoformat(),
            ),
        ).fetchone()

    max_bar = number(bar[2]) if bar else None
    max_trade = number(trade[2]) if trade else None
    observed = [value for value in (max_bar, max_trade) if value is not None]
    max_observed = max(observed) if observed else None
    profit_pct = ((max_observed / pick.premium) - 1.0) * 100.0 if max_observed is not None else None
    first_observed = min([x for x in ((bar or [None, None])[1], (trade or [None, None])[1]) if x], default=None)
    has_rows = bool(int((bar or [0])[0] or 0) or int((trade or [0])[0] or 0))
    return AuditResult(
        label=pick.label,
        alert_date=pick.alert_date.isoformat(),
        week_start=week_start.isoformat(),
        week_end=week_end.isoformat(),
        contract_symbol=symbol,
        entry_premium=pick.premium,
        max_bar_high=max_bar,
        max_trade_price=max_trade,
        max_observed_price=max_observed,
        max_profit_pct=profit_pct,
        ever_profitable=(max_observed > pick.premium) if has_rows and max_observed is not None else None,
        bar_rows=int((bar or [0])[0] or 0),
        trade_rows=int((trade or [0])[0] or 0),
        first_observed_at=first_observed,
        max_observed_at=max_row[0] if max_row else None,
        notes="Profitable means max observed option price exceeded the posted premium.",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", default=str(ROOT / "data" / "historical_options" / "council_picks_202607_202608"))
    args = parser.parse_args()

    load_local_env()
    if not os.environ.get("ALPACA_API_SECRET") and os.environ.get("ALPACA_SECRET_KEY"):
        os.environ["ALPACA_API_SECRET"] = os.environ["ALPACA_SECRET_KEY"]
    key, secret, stock_feed = alpaca_credentials()
    client = JsonHttpClient(
        {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
        timeout=60,
        retries=4,
    )
    store = HistoricalOptionsStore(Path(args.output_root))
    run_id = store.start_run(
        {
            "underlying": "COUNCIL_PICKS",
            "start_date": min(p.alert_date for p in PICKS).isoformat(),
            "end_date": max(p.expiration for p in PICKS).isoformat(),
            "config": "exact posted contracts from pasted FT Council picks",
            "picks": [asdict(p) for p in PICKS],
        }
    )

    downloader = ExactPickDownloader(store, client, stock_feed)
    results: list[AuditResult] = []
    try:
        for pick in PICKS:
            week_start, week_end, start_at, end_at = week_window(pick)
            symbol = downloader.find_contract(run_id, pick)
            if symbol:
                downloader.download_symbol_history(run_id, symbol, start_at, end_at)
            results.append(audit_pick(store, pick, symbol, week_start, week_end))

        output_root = Path(args.output_root)
        json_path = output_root / "council_pick_profitability_report.json"
        csv_path = output_root / "council_pick_profitability_report.csv"
        json_path.write_text(json.dumps([asdict(row) for row in results], indent=2), encoding="utf-8")
        with csv_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(asdict(results[0]).keys()))
            writer.writeheader()
            writer.writerows(asdict(row) for row in results)
        store.finish_run(run_id, "complete", {"results": [asdict(row) for row in results]})
        print(json.dumps({"json": str(json_path), "csv": str(csv_path), "results": [asdict(row) for row in results]}, indent=2))
        return 0
    except Exception as exc:
        store.finish_run(run_id, "failed", {}, str(exc))
        raise DownloadError(str(exc)) from exc


if __name__ == "__main__":
    raise SystemExit(main())
