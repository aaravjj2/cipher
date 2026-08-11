"""Resumable Alpaca SIP equity-bar archive for read-only strategy research.

The archive stores split/dividend-adjusted bars and compressed raw provider
pages.  It never imports a trading client or touches account/order endpoints.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence
from zoneinfo import ZoneInfo

from historical_options_download import JsonHttpClient, alpaca_credentials


CORE = Path(__file__).resolve().parent
DEFAULT_ROOT = CORE.parent / "data" / "historical_equities" / "alpaca_amzn"
DATA_URL = "https://data.alpaca.markets/v2/stocks/bars"
UTC = timezone.utc
NY = ZoneInfo("America/New_York")


class EquityDownloadError(RuntimeError):
    pass


def iso_utc(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def parse_day(value: str | date) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def window_times(start_day: date, end_day: date) -> tuple[str, str]:
    start = datetime.combine(start_day, time(0, 0), tzinfo=NY).astimezone(UTC)
    end = datetime.combine(end_day + timedelta(days=1), time(0, 0), tzinfo=NY).astimezone(UTC)
    return iso_utc(start), iso_utc(end)


@dataclass(frozen=True, slots=True)
class DownloadSummary:
    symbol: str
    timeframe: str
    start_date: str
    end_date: str
    pages: int
    rows: int
    skipped: bool


class EquityBarStore:
    def __init__(self, root: str | Path = DEFAULT_ROOT) -> None:
        self.root = Path(root).resolve()
        self.raw_root = self.root / "raw"
        self.db_path = self.root / "equity_bars.sqlite"
        self.root.mkdir(parents=True, exist_ok=True)
        self.raw_root.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path)
        db.execute("pragma journal_mode=WAL")
        db.execute("pragma synchronous=NORMAL")
        return db

    def _ensure_schema(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                create table if not exists bars (
                    symbol text not null,
                    timeframe text not null,
                    timestamp text not null,
                    open real not null,
                    high real not null,
                    low real not null,
                    close real not null,
                    volume real not null,
                    vwap real,
                    trades integer,
                    primary key(symbol,timeframe,timestamp)
                );
                create index if not exists bars_lookup
                    on bars(symbol,timeframe,timestamp);
                create table if not exists raw_pages (
                    sha256 text primary key,
                    symbol text not null,
                    timeframe text not null,
                    start_at text not null,
                    end_at text not null,
                    page_number integer not null,
                    path text not null,
                    downloaded_at text not null,
                    row_count integer not null,
                    next_page_token_present integer not null
                );
                create table if not exists download_windows (
                    window_key text primary key,
                    symbol text not null,
                    timeframe text not null,
                    start_at text not null,
                    end_at text not null,
                    status text not null,
                    pages integer not null default 0,
                    rows integer not null default 0,
                    error text,
                    updated_at text not null
                );
                """
            )

    @staticmethod
    def window_key(
        symbol: str,
        timeframe: str,
        start_at: str,
        end_at: str,
        feed: str = "sip",
    ) -> str:
        """Identify a download window including the market-data feed.

        Feed is part of the dataset identity: resuming an SIP window as IEX (or
        vice versa) would silently create a mixed-quality archive.
        """
        raw = f"{symbol}|{timeframe}|{start_at}|{end_at}|all|{feed.lower()}".encode()
        return hashlib.sha256(raw).hexdigest()

    def completed(self, key: str) -> bool:
        with self.connect() as db:
            row = db.execute(
                "select status from download_windows where window_key=?", (key,)
            ).fetchone()
        return bool(row and row[0] == "complete")

    def start_window(
        self, key: str, symbol: str, timeframe: str, start_at: str, end_at: str
    ) -> None:
        now = iso_utc(datetime.now(UTC))
        with self.connect() as db:
            db.execute(
                """insert into download_windows
                   (window_key,symbol,timeframe,start_at,end_at,status,updated_at)
                   values(?,?,?,?,?,'running',?)
                   on conflict(window_key) do update set
                     status='running',error=null,updated_at=excluded.updated_at""",
                (key, symbol, timeframe, start_at, end_at, now),
            )

    def finish_window(
        self, key: str, pages: int, rows: int, error: str | None = None
    ) -> None:
        now = iso_utc(datetime.now(UTC))
        with self.connect() as db:
            db.execute(
                """update download_windows set status=?,pages=?,rows=?,error=?,updated_at=?
                   where window_key=?""",
                ("failed" if error else "complete", pages, rows, error, now, key),
            )

    def save_page(
        self,
        *,
        symbol: str,
        timeframe: str,
        start_at: str,
        end_at: str,
        page_number: int,
        raw: bytes,
        row_count: int,
        has_next: bool,
    ) -> str:
        digest = sha256_bytes(raw)
        relative = Path("raw") / symbol / timeframe / f"{digest}.json.gz"
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(gzip.compress(raw, compresslevel=6))
        with self.connect() as db:
            db.execute(
                """insert or ignore into raw_pages
                   (sha256,symbol,timeframe,start_at,end_at,page_number,path,
                    downloaded_at,row_count,next_page_token_present)
                   values(?,?,?,?,?,?,?,?,?,?)""",
                (
                    digest,
                    symbol,
                    timeframe,
                    start_at,
                    end_at,
                    page_number,
                    str(relative),
                    iso_utc(datetime.now(UTC)),
                    row_count,
                    1 if has_next else 0,
                ),
            )
        return digest

    def upsert_bars(self, symbol: str, timeframe: str, rows: Sequence[dict[str, Any]]) -> int:
        values = []
        for row in rows:
            try:
                values.append(
                    (
                        symbol,
                        timeframe,
                        str(row["t"]),
                        float(row["o"]),
                        float(row["h"]),
                        float(row["l"]),
                        float(row["c"]),
                        float(row.get("v") or 0.0),
                        float(row["vw"]) if row.get("vw") is not None else None,
                        int(row["n"]) if row.get("n") is not None else None,
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        if not values:
            return 0
        with self.connect() as db:
            db.executemany(
                """insert into bars
                   (symbol,timeframe,timestamp,open,high,low,close,volume,vwap,trades)
                   values(?,?,?,?,?,?,?,?,?,?)
                   on conflict(symbol,timeframe,timestamp) do update set
                     open=excluded.open,high=excluded.high,low=excluded.low,
                     close=excluded.close,volume=excluded.volume,
                     vwap=excluded.vwap,trades=excluded.trades""",
                values,
            )
        return len(values)

    def coverage(self, symbol: str) -> dict[str, Any]:
        with self.connect() as db:
            rows = db.execute(
                """select timeframe,count(*),min(timestamp),max(timestamp)
                   from bars where symbol=? group by timeframe order by timeframe""",
                (symbol,),
            ).fetchall()
            pages = db.execute(
                "select count(*) from raw_pages where symbol=?", (symbol,)
            ).fetchone()[0]
            windows = db.execute(
                """select count(*),sum(case when status='complete' then 1 else 0 end)
                   from download_windows where symbol=?""",
                (symbol,),
            ).fetchone()
        return {
            "symbol": symbol,
            "timeframes": [
                {"timeframe": r[0], "rows": r[1], "start": r[2], "end": r[3]}
                for r in rows
            ],
            "raw_pages": int(pages),
            "windows": int(windows[0] or 0),
            "completed_windows": int(windows[1] or 0),
        }

    def write_manifest(self, symbol: str, latest: dict[str, Any]) -> Path:
        with self.connect() as db:
            db.execute("pragma wal_checkpoint(TRUNCATE)")
        manifest = {
            "schema_version": 1,
            "generated_at": iso_utc(datetime.now(UTC)),
            "dataset_id": f"alpaca_{symbol.lower()}_adjusted_sip_bars",
            "provider": "Alpaca SIP",
            "adjustment": "all",
            "historical_nbbo": False,
            "point_in_time_bars": True,
            "latest_run": latest,
            "coverage": self.coverage(symbol),
            "database": {
                "path": self.db_path.name,
                "sha256": sha256_bytes(self.db_path.read_bytes()),
            },
            "caveats": [
                "Minute OHLCV bars are not executable bid/ask quotes.",
                "Backtests must use next-bar execution and explicit slippage.",
                "Corporate actions are handled using Alpaca adjustment=all.",
                "Results remain research approximations and are not live-trading evidence.",
            ],
        }
        path = self.root / "dataset_manifest.json"
        path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        return path


def download_bars(
    store: EquityBarStore,
    client: JsonHttpClient,
    *,
    symbol: str,
    timeframe: str,
    start_day: date,
    end_day: date,
    feed: str,
    resume: bool = True,
) -> DownloadSummary:
    start_at, end_at = window_times(start_day, end_day)
    key = store.window_key(symbol, timeframe, start_at, end_at, feed=feed)
    if resume and store.completed(key):
        return DownloadSummary(
            symbol, timeframe, start_day.isoformat(), end_day.isoformat(), 0, 0, True
        )
    store.start_window(key, symbol, timeframe, start_at, end_at)
    token: str | None = None
    pages = rows_total = 0
    try:
        while True:
            query = {
                "symbols": symbol,
                "timeframe": timeframe,
                "start": start_at,
                "end": end_at,
                "limit": 10000,
                "adjustment": "all",
                "feed": feed,
                "sort": "asc",
                "page_token": token,
            }
            payload, raw, _status = client.get(DATA_URL, query)
            pages += 1
            rows = list((payload.get("bars") or {}).get(symbol) or [])
            store.save_page(
                symbol=symbol,
                timeframe=timeframe,
                start_at=start_at,
                end_at=end_at,
                page_number=pages,
                raw=raw,
                row_count=len(rows),
                has_next=bool(payload.get("next_page_token")),
            )
            rows_total += store.upsert_bars(symbol, timeframe, rows)
            token = payload.get("next_page_token")
            if not token:
                break
        store.finish_window(key, pages, rows_total)
        return DownloadSummary(
            symbol, timeframe, start_day.isoformat(), end_day.isoformat(), pages, rows_total, False
        )
    except Exception as exc:
        store.finish_window(key, pages, rows_total, str(exc)[:1000])
        raise


def quarter_windows(start_day: date, end_day: date) -> list[tuple[date, date]]:
    windows = []
    current = start_day
    while current <= end_day:
        month_end = ((current.month - 1) // 3 + 1) * 3
        year = current.year
        if month_end > 12:
            month_end -= 12
            year += 1
        next_quarter = date(year + (1 if month_end == 12 else 0), 1 if month_end == 12 else month_end + 1, 1)
        window_end = min(end_day, next_quarter - timedelta(days=1))
        windows.append((current, window_end))
        current = window_end + timedelta(days=1)
    return windows


def run_download(args: argparse.Namespace) -> dict[str, Any]:
    symbol = str(args.symbol).upper()
    start_day = parse_day(args.start)
    end_day = parse_day(args.end)
    if end_day < start_day:
        raise EquityDownloadError("end must not precede start")
    key, secret, feed = alpaca_credentials()
    client = JsonHttpClient(
        {
            "APCA-API-KEY-ID": key,
            "APCA-API-SECRET-KEY": secret,
            "Accept": "application/json",
            "User-Agent": "Cipher-Equity-Research/1.0",
        },
        timeout=args.timeout,
        retries=args.retries,
    )
    store = EquityBarStore(args.output_root or DEFAULT_ROOT)
    summaries = []
    for window_start, window_end in quarter_windows(start_day, end_day):
        result = download_bars(
            store,
            client,
            symbol=symbol,
            timeframe=args.timeframe,
            start_day=window_start,
            end_day=window_end,
            feed=feed,
            resume=args.resume,
        )
        summaries.append(result.__dict__ if hasattr(result, "__dict__") else {
            field: getattr(result, field) for field in result.__dataclass_fields__
        })
    latest = {
        "symbol": symbol,
        "timeframe": args.timeframe,
        "start": start_day.isoformat(),
        "end": end_day.isoformat(),
        "summaries": summaries,
    }
    manifest = store.write_manifest(symbol, latest)
    return {"manifest": str(manifest), "coverage": store.coverage(symbol), "summaries": summaries}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Download adjusted Alpaca SIP equity bars.")
    parser.add_argument("--symbol", default="AMZN")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument(
        "--timeframe",
        choices=("1Min", "5Min", "15Min", "1Hour", "1Day"),
        default="1Min",
    )
    parser.add_argument("--output-root")
    parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--retries", type=int, default=6)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    print(json.dumps(run_download(args), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
