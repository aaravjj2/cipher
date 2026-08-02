"""Resumable read-only historical options downloader for Cipher.

The primary source is Alpaca historical option bars and trades.  The downloader
also archives contract metadata and timestamp-aligned underlying bars.  It does
not invent historical bid/ask quotes: generated manifests explicitly mark the
bid/ask source as absent.

This module never imports Alpaca's trading client and never calls order/account
endpoints.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import os
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence
from zoneinfo import ZoneInfo


CORE = Path(__file__).resolve().parent
CIPHER_ROOT = CORE.parent
REPO_ROOT = CIPHER_ROOT.parent
DEFAULT_ROOT = CIPHER_ROOT / "data" / "historical_options" / "alpaca_spy"
DATA_BASE = "https://data.alpaca.markets"
PAPER_BASE = "https://paper-api.alpaca.markets"
NY = ZoneInfo("America/New_York")
UTC = timezone.utc


class DownloadError(RuntimeError):
    """Raised when a provider request or persistence operation fails."""


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso_utc(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def parse_day(value: str | date) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def integer(value: Any) -> int | None:
    result = number(value)
    return int(result) if result is not None else None


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def stable_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def load_env_values() -> dict[str, str]:
    values: dict[str, str] = {}
    for path in (REPO_ROOT / ".env", CIPHER_ROOT / "app" / ".env"):
        if not path.exists():
            continue
        for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    for key in (
        "ALPACA_ALGO_PLUS_KEY",
        "ALPACA_ALGO_PLUS_SECRET",
        "ALPACA_ALGO_KEY",
        "ALPACA_ALGO_SECRET",
        "ALPACA_API_KEY",
        "ALPACA_API_SECRET",
        "ALPACA_STOCK_FEED",
    ):
        if os.environ.get(key):
            values[key] = str(os.environ[key])
    return values


def _secret_manager_alpaca_values() -> dict[str, str]:
    """Fetch Alpaca credentials through ADC without persisting secret material.

    This fallback is intended for the Cipher VM, where runtime credentials are
    root-owned and the attached service account has Secret Manager access.  It
    is deliberately best-effort: local development remains functional when the
    Google client library, ADC, project metadata, or individual secrets are
    unavailable.
    """
    try:
        import google.auth
        from google.cloud import secretmanager
    except ImportError:
        return {}
    try:
        credentials, project_id = google.auth.default()
    except Exception:
        return {}
    project_id = (
        os.environ.get("GOOGLE_CLOUD_PROJECT")
        or os.environ.get("GCP_PROJECT")
        or project_id
    )
    if not project_id:
        return {}
    secret_names = {
        "ALPACA_ALGO_PLUS_KEY": "cipher-alpaca-algo-plus-key",
        "ALPACA_ALGO_PLUS_SECRET": "cipher-alpaca-algo-plus-secret",
        "ALPACA_ALGO_KEY": "cipher-alpaca-algo-key",
        "ALPACA_ALGO_SECRET": "cipher-alpaca-algo-secret",
    }
    try:
        client = secretmanager.SecretManagerServiceClient(credentials=credentials)
    except Exception:
        return {}
    values: dict[str, str] = {}
    for env_name, secret_name in secret_names.items():
        resource = f"projects/{project_id}/secrets/{secret_name}/versions/latest"
        try:
            response = client.access_secret_version(request={"name": resource})
            value = response.payload.data.decode("utf-8").strip()
        except Exception:
            continue
        if value:
            values[env_name] = value
    return values


def alpaca_credentials() -> tuple[str, str, str]:
    values = load_env_values()
    key = (
        values.get("ALPACA_ALGO_PLUS_KEY")
        or values.get("ALPACA_ALGO_KEY")
        or values.get("ALPACA_API_KEY")
    )
    secret = (
        values.get("ALPACA_ALGO_PLUS_SECRET")
        or values.get("ALPACA_ALGO_SECRET")
        or values.get("ALPACA_API_SECRET")
    )
    if not key or not secret:
        secret_values = _secret_manager_alpaca_values()
        key = (
            secret_values.get("ALPACA_ALGO_PLUS_KEY")
            or secret_values.get("ALPACA_ALGO_KEY")
        )
        secret = (
            secret_values.get("ALPACA_ALGO_PLUS_SECRET")
            or secret_values.get("ALPACA_ALGO_SECRET")
        )
    if not key or not secret:
        raise DownloadError("Alpaca market-data credentials are not configured")
    stock_feed = (values.get("ALPACA_STOCK_FEED") or "sip").lower()
    if stock_feed not in {"sip", "iex"}:
        stock_feed = "sip"
    return key, secret, stock_feed


@dataclass(frozen=True, slots=True)
class RawPage:
    provider: str
    endpoint: str
    query: dict[str, Any]
    path: str
    sha256: str
    downloaded_at: str
    page_number: int
    row_count: int
    next_page_token_present: bool


@dataclass(frozen=True, slots=True)
class ContractSelection:
    decision_date: str
    symbol: str
    expiration_date: str
    strike: float
    option_type: str
    spot: float
    dte: int
    moneyness: float
    rank: int


class JsonHttpClient:
    def __init__(
        self,
        headers: dict[str, str],
        *,
        timeout: int = 60,
        retries: int = 6,
        base_sleep: float = 1.0,
    ) -> None:
        self.headers = dict(headers)
        self.timeout = int(timeout)
        self.retries = int(retries)
        self.base_sleep = float(base_sleep)

    def get(self, url: str, query: dict[str, Any]) -> tuple[dict[str, Any], bytes, int]:
        clean = {
            str(key): value
            for key, value in query.items()
            if value is not None and value != ""
        }
        request_url = f"{url}?{urllib.parse.urlencode(clean)}"
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            request = urllib.request.Request(request_url, headers=self.headers)
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    raw = response.read()
                    status = int(response.status)
                payload = json.loads(raw.decode("utf-8"))
                if not isinstance(payload, dict):
                    raise DownloadError("provider response must be a JSON object")
                return payload, raw, status
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:500]
                last_error = DownloadError(f"HTTP {exc.code}: {detail}")
                if exc.code not in {408, 429, 500, 502, 503, 504} or attempt >= self.retries:
                    raise last_error from exc
                retry_after = exc.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else self.base_sleep * (2**attempt)
                time.sleep(min(delay, 30.0))
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt >= self.retries:
                    raise DownloadError(f"provider request failed: {exc}") from exc
                time.sleep(min(self.base_sleep * (2**attempt), 30.0))
        raise DownloadError(f"provider request failed: {last_error}")


class HistoricalOptionsStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.raw_root = self.root / "raw"
        self.db_path = self.root / "historical_options.sqlite"
        self.root.mkdir(parents=True, exist_ok=True)
        self.raw_root.mkdir(parents=True, exist_ok=True)
        self.ensure_schema()

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path)
        db.execute("pragma journal_mode=WAL")
        db.execute("pragma synchronous=NORMAL")
        db.execute("pragma foreign_keys=ON")
        return db

    def ensure_schema(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                create table if not exists download_runs (
                    id integer primary key autoincrement,
                    started_at text not null,
                    completed_at text,
                    status text not null,
                    underlying text not null,
                    start_date text not null,
                    end_date text not null,
                    config_json text not null,
                    summary_json text,
                    error text
                );

                create table if not exists raw_pages (
                    id integer primary key autoincrement,
                    run_id integer,
                    provider text not null,
                    endpoint text not null,
                    query_json text not null,
                    response_path text not null unique,
                    sha256 text not null,
                    downloaded_at text not null,
                    http_status integer not null,
                    page_number integer not null,
                    row_count integer not null,
                    next_page_token_present integer not null,
                    foreign key(run_id) references download_runs(id)
                );

                create table if not exists contracts (
                    symbol text primary key,
                    underlying text not null,
                    expiration_date text not null,
                    strike real not null,
                    option_type text not null,
                    status text,
                    style text,
                    multiplier integer,
                    size integer,
                    tradable integer,
                    close_price real,
                    close_price_date text,
                    open_interest real,
                    open_interest_date text,
                    metadata_observed_at text not null,
                    raw_json text not null
                );
                create index if not exists idx_contracts_underlying_expiry
                    on contracts(underlying, expiration_date, option_type, strike);

                create table if not exists decision_selections (
                    decision_date text not null,
                    symbol text not null,
                    expiration_date text not null,
                    strike real not null,
                    option_type text not null,
                    spot real not null,
                    dte integer not null,
                    moneyness real not null,
                    rank integer not null,
                    selected_at text not null,
                    primary key(decision_date, symbol),
                    foreign key(symbol) references contracts(symbol)
                );

                create table if not exists selection_observation_audit (
                    decision_date text not null,
                    symbol text not null,
                    first_bar_at text,
                    first_trade_at text,
                    bars_on_decision integer not null,
                    trades_on_decision integer not null,
                    observed_on_decision integer not null,
                    audited_at text not null,
                    primary key(decision_date, symbol),
                    foreign key(decision_date, symbol)
                        references decision_selections(decision_date, symbol)
                );

                create table if not exists underlying_bars (
                    symbol text not null,
                    timestamp text not null,
                    timeframe text not null,
                    open real,
                    high real,
                    low real,
                    close real,
                    volume real,
                    vwap real,
                    trades integer,
                    source text not null,
                    primary key(symbol, timestamp, timeframe)
                );

                create table if not exists option_bars (
                    symbol text not null,
                    timestamp text not null,
                    timeframe text not null,
                    open real,
                    high real,
                    low real,
                    close real,
                    volume real,
                    vwap real,
                    trades integer,
                    source text not null,
                    primary key(symbol, timestamp, timeframe)
                );
                create index if not exists idx_option_bars_time
                    on option_bars(timestamp, symbol);

                create table if not exists option_trades (
                    trade_id text primary key,
                    symbol text not null,
                    timestamp text not null,
                    price real not null,
                    size real,
                    exchange text,
                    conditions_json text,
                    source text not null,
                    raw_json text not null
                );
                create index if not exists idx_option_trades_time
                    on option_trades(timestamp, symbol);

                create table if not exists download_windows (
                    window_key text primary key,
                    run_id integer,
                    kind text not null,
                    symbols_hash text not null,
                    symbols_json text not null,
                    start_at text not null,
                    end_at text not null,
                    timeframe text,
                    status text not null,
                    page_count integer not null default 0,
                    row_count integer not null default 0,
                    completed_at text,
                    error text,
                    foreign key(run_id) references download_runs(id)
                );
                """
            )

    def start_run(self, config: dict[str, Any]) -> int:
        now = iso_utc(utcnow())
        with self.connect() as db:
            cur = db.execute(
                """insert into download_runs
                   (started_at,status,underlying,start_date,end_date,config_json)
                   values (?,?,?,?,?,?)""",
                (
                    now,
                    "running",
                    config["underlying"],
                    config["start_date"],
                    config["end_date"],
                    stable_json(config),
                ),
            )
            return int(cur.lastrowid)

    def finish_run(self, run_id: int, status: str, summary: dict[str, Any], error: str | None = None) -> None:
        with self.connect() as db:
            db.execute(
                """update download_runs
                   set completed_at=?,status=?,summary_json=?,error=? where id=?""",
                (iso_utc(utcnow()), status, stable_json(summary), error, int(run_id)),
            )

    def archive_page(
        self,
        *,
        run_id: int,
        provider: str,
        endpoint: str,
        query: dict[str, Any],
        raw: bytes,
        http_status: int,
        page_number: int,
        row_count: int,
        next_page_token_present: bool,
    ) -> RawPage:
        digest = sha256_bytes(raw)
        endpoint_slug = endpoint.strip("/").replace("/", "_") or "root"
        rel = Path(provider) / endpoint_slug / f"{digest}.json.gz"
        path = self.raw_root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            with gzip.open(path, "wb", compresslevel=6) as fh:
                fh.write(raw)
        page = RawPage(
            provider=provider,
            endpoint=endpoint,
            query=dict(query),
            path=str(path.relative_to(self.root)),
            sha256=digest,
            downloaded_at=iso_utc(utcnow()),
            page_number=int(page_number),
            row_count=int(row_count),
            next_page_token_present=bool(next_page_token_present),
        )
        with self.connect() as db:
            db.execute(
                """insert or ignore into raw_pages
                   (run_id,provider,endpoint,query_json,response_path,sha256,
                    downloaded_at,http_status,page_number,row_count,next_page_token_present)
                   values (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    int(run_id),
                    provider,
                    endpoint,
                    stable_json(query),
                    page.path,
                    page.sha256,
                    page.downloaded_at,
                    int(http_status),
                    page.page_number,
                    page.row_count,
                    1 if page.next_page_token_present else 0,
                ),
            )
        return page

    def upsert_contracts(self, rows: Iterable[dict[str, Any]], observed_at: str) -> int:
        payload = []
        for row in rows:
            symbol = str(row.get("symbol") or "").upper().strip()
            expiry = str(row.get("expiration_date") or "")[:10]
            strike = number(row.get("strike_price"))
            option_type = str(row.get("type") or "").lower()
            underlying = str(row.get("underlying_symbol") or "").upper().strip()
            if not symbol or not underlying or not expiry or strike is None or option_type not in {"call", "put"}:
                continue
            payload.append(
                (
                    symbol,
                    underlying,
                    expiry,
                    strike,
                    option_type,
                    row.get("status"),
                    row.get("style"),
                    integer(row.get("multiplier")),
                    integer(row.get("size")),
                    1 if bool(row.get("tradable")) else 0,
                    number(row.get("close_price")),
                    row.get("close_price_date"),
                    number(row.get("open_interest")),
                    row.get("open_interest_date"),
                    observed_at,
                    stable_json(row),
                )
            )
        if not payload:
            return 0
        with self.connect() as db:
            db.executemany(
                """insert into contracts
                   (symbol,underlying,expiration_date,strike,option_type,status,style,
                    multiplier,size,tradable,close_price,close_price_date,open_interest,
                    open_interest_date,metadata_observed_at,raw_json)
                   values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   on conflict(symbol) do update set
                    status=excluded.status,tradable=excluded.tradable,
                    close_price=excluded.close_price,close_price_date=excluded.close_price_date,
                    open_interest=excluded.open_interest,open_interest_date=excluded.open_interest_date,
                    metadata_observed_at=excluded.metadata_observed_at,raw_json=excluded.raw_json""",
                payload,
            )
        return len(payload)

    def upsert_underlying_bars(self, symbol: str, rows: Iterable[dict[str, Any]], timeframe: str) -> int:
        payload = []
        for row in rows:
            timestamp = str(row.get("t") or "")
            if not timestamp:
                continue
            payload.append(
                (
                    symbol.upper(), timestamp, timeframe,
                    number(row.get("o")), number(row.get("h")), number(row.get("l")),
                    number(row.get("c")), number(row.get("v")), number(row.get("vw")),
                    integer(row.get("n")), "alpaca_sip_or_iex",
                )
            )
        with self.connect() as db:
            db.executemany(
                """insert or replace into underlying_bars
                   (symbol,timestamp,timeframe,open,high,low,close,volume,vwap,trades,source)
                   values (?,?,?,?,?,?,?,?,?,?,?)""",
                payload,
            )
        return len(payload)

    def upsert_option_bars(self, rows_by_symbol: dict[str, list[dict[str, Any]]], timeframe: str) -> int:
        payload = []
        for symbol, rows in rows_by_symbol.items():
            for row in rows or []:
                timestamp = str(row.get("t") or "")
                if not timestamp:
                    continue
                payload.append(
                    (
                        str(symbol).upper(), timestamp, timeframe,
                        number(row.get("o")), number(row.get("h")), number(row.get("l")),
                        number(row.get("c")), number(row.get("v")), number(row.get("vw")),
                        integer(row.get("n")), "alpaca_opra_historical_bars",
                    )
                )
        if payload:
            with self.connect() as db:
                db.executemany(
                    """insert or replace into option_bars
                       (symbol,timestamp,timeframe,open,high,low,close,volume,vwap,trades,source)
                       values (?,?,?,?,?,?,?,?,?,?,?)""",
                    payload,
                )
        return len(payload)

    def upsert_option_trades(self, rows_by_symbol: dict[str, list[dict[str, Any]]]) -> int:
        payload = []
        for symbol, rows in rows_by_symbol.items():
            for row in rows or []:
                timestamp = str(row.get("t") or "")
                price = number(row.get("p"))
                if not timestamp or price is None:
                    continue
                normalized = {
                    "symbol": str(symbol).upper(),
                    "timestamp": timestamp,
                    "price": price,
                    "size": number(row.get("s")),
                    "exchange": row.get("x"),
                    "conditions": row.get("c") or [],
                }
                trade_id = sha256_bytes(stable_json(normalized).encode("utf-8"))
                payload.append(
                    (
                        trade_id, normalized["symbol"], timestamp, price,
                        normalized["size"], normalized["exchange"],
                        stable_json(normalized["conditions"]),
                        "alpaca_opra_historical_trades", stable_json(row),
                    )
                )
        if payload:
            with self.connect() as db:
                db.executemany(
                    """insert or ignore into option_trades
                       (trade_id,symbol,timestamp,price,size,exchange,conditions_json,source,raw_json)
                       values (?,?,?,?,?,?,?,?,?)""",
                    payload,
                )
        return len(payload)

    def save_selections(
        self,
        rows: Sequence[ContractSelection],
        decision_dates: Sequence[str] | None = None,
        *,
        underlying: str | None = None,
        option_type: str | None = None,
    ) -> None:
        selected_at = iso_utc(utcnow())
        decision_dates = sorted(
            set(decision_dates or ()) | {row.decision_date for row in rows}
        )
        normalized_underlying = str(underlying).upper() if underlying else None
        normalized_option_type = str(option_type).lower() if option_type else None
        with self.connect() as db:
            for decision_date in decision_dates:
                if normalized_underlying and normalized_option_type:
                    delete_params = (
                        decision_date,
                        normalized_underlying,
                        normalized_option_type,
                    )
                    db.execute(
                        """delete from selection_observation_audit
                           where decision_date=? and symbol in (
                               select symbol from contracts
                               where underlying=? and option_type=?
                           )""",
                        delete_params,
                    )
                    db.execute(
                        """delete from decision_selections
                           where decision_date=? and symbol in (
                               select symbol from contracts
                               where underlying=? and option_type=?
                           )""",
                        delete_params,
                    )
                elif normalized_underlying:
                    delete_params = (decision_date, normalized_underlying)
                    db.execute(
                        """delete from selection_observation_audit
                           where decision_date=? and symbol in (
                               select symbol from contracts where underlying=?
                           )""",
                        delete_params,
                    )
                    db.execute(
                        """delete from decision_selections
                           where decision_date=? and symbol in (
                               select symbol from contracts where underlying=?
                           )""",
                        delete_params,
                    )
                else:
                    db.execute(
                        "delete from selection_observation_audit where decision_date=?",
                        (decision_date,),
                    )
                    db.execute(
                        "delete from decision_selections where decision_date=?",
                        (decision_date,),
                    )
            db.executemany(
                """insert or replace into decision_selections
                   (decision_date,symbol,expiration_date,strike,option_type,spot,dte,moneyness,rank,selected_at)
                   values (?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        row.decision_date, row.symbol, row.expiration_date, row.strike,
                        row.option_type, row.spot, row.dte, row.moneyness, row.rank,
                        selected_at,
                    )
                    for row in rows
                ],
            )

    def window_complete(self, window_key: str) -> bool:
        with self.connect() as db:
            row = db.execute(
                "select status from download_windows where window_key=?", (window_key,)
            ).fetchone()
        return bool(row and row[0] == "complete")

    def begin_window(
        self,
        *,
        window_key: str,
        run_id: int,
        kind: str,
        symbols: Sequence[str],
        start_at: str,
        end_at: str,
        timeframe: str | None,
    ) -> None:
        symbols_json = stable_json(list(symbols))
        symbols_hash = sha256_bytes(symbols_json.encode("utf-8"))
        with self.connect() as db:
            db.execute(
                """insert into download_windows
                   (window_key,run_id,kind,symbols_hash,symbols_json,start_at,end_at,timeframe,status)
                   values (?,?,?,?,?,?,?,?,?)
                   on conflict(window_key) do update set
                    run_id=excluded.run_id,status='running',error=null""",
                (
                    window_key, int(run_id), kind, symbols_hash, symbols_json,
                    start_at, end_at, timeframe, "running",
                ),
            )

    def finish_window(self, window_key: str, pages: int, rows: int, error: str | None = None) -> None:
        status = "failed" if error else "complete"
        with self.connect() as db:
            db.execute(
                """update download_windows set status=?,page_count=?,row_count=?,
                   completed_at=?,error=? where window_key=?""",
                (status, int(pages), int(rows), iso_utc(utcnow()), error, window_key),
            )

    def save_selection_observation_audit(self, rows: Sequence[dict[str, Any]]) -> None:
        audited_at = iso_utc(utcnow())
        with self.connect() as db:
            db.executemany(
                """insert or replace into selection_observation_audit
                   (decision_date,symbol,first_bar_at,first_trade_at,bars_on_decision,
                    trades_on_decision,observed_on_decision,audited_at)
                   values (?,?,?,?,?,?,?,?)""",
                [
                    (
                        row["decision_date"], row["symbol"], row.get("first_bar_at"),
                        row.get("first_trade_at"), int(row["bars_on_decision"]),
                        int(row["trades_on_decision"]),
                        1 if row["observed_on_decision"] else 0, audited_at,
                    )
                    for row in rows
                ],
            )

    def counts(self) -> dict[str, int]:
        tables = (
            "contracts", "decision_selections", "selection_observation_audit",
            "underlying_bars", "option_bars", "option_trades", "raw_pages",
            "download_windows",
        )
        with self.connect() as db:
            return {table: int(db.execute(f"select count(*) from {table}").fetchone()[0]) for table in tables}

    def raw_page_manifest(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                """select provider,endpoint,response_path,sha256,downloaded_at,page_number,row_count
                   from raw_pages order by id"""
            ).fetchall()
        return [
            {
                "provider": row[0], "endpoint": row[1], "path": row[2],
                "sha256": row[3], "downloaded_at": row[4],
                "page_number": row[5], "row_count": row[6],
            }
            for row in rows
        ]


class AlpacaHistoricalOptionsDownloader:
    def __init__(self, store: HistoricalOptionsStore, client: JsonHttpClient) -> None:
        self.store = store
        self.client = client

    def _paged(
        self,
        *,
        run_id: int,
        url: str,
        endpoint: str,
        query: dict[str, Any],
        data_key: str,
        max_pages: int | None = None,
    ) -> Iterator[tuple[dict[str, Any], int]]:
        page = 0
        next_token: str | None = None
        while True:
            page += 1
            current = dict(query)
            if next_token:
                current["page_token"] = next_token
            payload, raw, status = self.client.get(url, current)
            data = payload.get(data_key) or {}
            if isinstance(data, dict):
                row_count = sum(len(rows or []) for rows in data.values())
            elif isinstance(data, list):
                row_count = len(data)
            else:
                row_count = 0
            next_token = payload.get("next_page_token")
            self.store.archive_page(
                run_id=run_id,
                provider="alpaca",
                endpoint=endpoint,
                query=current,
                raw=raw,
                http_status=status,
                page_number=page,
                row_count=row_count,
                next_page_token_present=bool(next_token),
            )
            yield payload, page
            if not next_token or (max_pages is not None and page >= max_pages):
                break

    def download_underlying_bars(
        self,
        run_id: int,
        symbol: str,
        start_at: str,
        end_at: str,
        timeframe: str,
        stock_feed: str,
    ) -> int:
        endpoint = f"/v2/stocks/{symbol.upper()}/bars"
        total = 0
        query = {
            "timeframe": timeframe,
            "start": start_at,
            "end": end_at,
            "limit": 10000,
            "feed": stock_feed,
            "adjustment": "raw",
            "sort": "asc",
        }
        for payload, _ in self._paged(
            run_id=run_id,
            url=f"{DATA_BASE}{endpoint}",
            endpoint=endpoint,
            query=query,
            data_key="bars",
        ):
            rows = payload.get("bars") or []
            if isinstance(rows, dict):
                rows = rows.get(symbol.upper()) or []
            total += self.store.upsert_underlying_bars(symbol, rows, timeframe)
        return total

    def enumerate_contracts(
        self,
        run_id: int,
        underlying: str,
        expiration_gte: str,
        expiration_lte: str,
    ) -> list[dict[str, Any]]:
        endpoint = "/v2/options/contracts"
        merged: dict[str, dict[str, Any]] = {}
        for status_name in ("inactive", "active"):
            query = {
                "underlying_symbols": underlying.upper(),
                "status": status_name,
                "expiration_date_gte": expiration_gte,
                "expiration_date_lte": expiration_lte,
                "limit": 1000,
            }
            for payload, _ in self._paged(
                run_id=run_id,
                url=f"{PAPER_BASE}{endpoint}",
                endpoint=f"{endpoint}_{status_name}",
                query=query,
                data_key="option_contracts",
            ):
                observed_at = iso_utc(utcnow())
                rows = payload.get("option_contracts") or payload.get("contracts") or []
                self.store.upsert_contracts(rows, observed_at)
                for row in rows:
                    symbol = str(row.get("symbol") or "").upper()
                    if symbol:
                        merged[symbol] = dict(row)
        return list(merged.values())

    def download_option_window(
        self,
        *,
        run_id: int,
        kind: str,
        symbols: Sequence[str],
        start_at: str,
        end_at: str,
        timeframe: str | None,
        resume: bool,
    ) -> dict[str, Any]:
        symbols = tuple(sorted({str(symbol).upper() for symbol in symbols if symbol}))
        if not symbols:
            return {"kind": kind, "symbols": 0, "pages": 0, "rows": 0, "skipped": True}
        key_payload = {
            "kind": kind, "symbols": symbols, "start": start_at,
            "end": end_at, "timeframe": timeframe,
        }
        window_key = sha256_bytes(stable_json(key_payload).encode("utf-8"))
        if resume and self.store.window_complete(window_key):
            return {"kind": kind, "symbols": len(symbols), "pages": 0, "rows": 0, "skipped": True}
        self.store.begin_window(
            window_key=window_key,
            run_id=run_id,
            kind=kind,
            symbols=symbols,
            start_at=start_at,
            end_at=end_at,
            timeframe=timeframe,
        )
        endpoint = f"/v1beta1/options/{kind}"
        query: dict[str, Any] = {
            "symbols": ",".join(symbols),
            "start": start_at,
            "end": end_at,
            "limit": 10000,
            "sort": "asc",
        }
        if timeframe:
            query["timeframe"] = timeframe
        pages = rows_total = 0
        try:
            for payload, page_number in self._paged(
                run_id=run_id,
                url=f"{DATA_BASE}{endpoint}",
                endpoint=endpoint,
                query=query,
                data_key=kind,
            ):
                pages = page_number
                rows_by_symbol = payload.get(kind) or {}
                if kind == "bars":
                    rows_total += self.store.upsert_option_bars(rows_by_symbol, timeframe or "1Min")
                elif kind == "trades":
                    rows_total += self.store.upsert_option_trades(rows_by_symbol)
                else:
                    raise DownloadError(f"unsupported option data kind {kind!r}")
            self.store.finish_window(window_key, pages, rows_total)
            return {"kind": kind, "symbols": len(symbols), "pages": pages, "rows": rows_total, "skipped": False}
        except Exception as exc:
            self.store.finish_window(window_key, pages, rows_total, str(exc)[:1000])
            raise


def daily_closes(daily_rows: Sequence[dict[str, Any]]) -> list[tuple[date, float]]:
    rows: list[tuple[date, float]] = []
    for row in daily_rows:
        timestamp = str(row.get("t") or "")
        close = number(row.get("c"))
        if not timestamp or close is None:
            continue
        bar_day = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone(NY).date()
        rows.append((bar_day, close))
    return sorted(rows)


def first_period_decision_days(
    daily_rows: Sequence[dict[str, Any]],
    start_day: date,
    end_day: date,
    *,
    cadence: str,
) -> list[tuple[date, float]]:
    """Return first trading days of each requested period with prior-close spot.

    The current decision day's completed daily close is future information at a
    15:45 entry. Using the prior close keeps contract-universe filtering
    point-in-time safe before minute underlying bars are downloaded.
    """
    if cadence not in {"monthly", "weekly", "daily"}:
        raise ValueError(f"unsupported decision cadence {cadence!r}")
    ordered = daily_closes(daily_rows)
    selected: dict[tuple[int, ...], tuple[date, float]] = {}
    daily: list[tuple[date, float]] = []
    for index, (bar_day, _close) in enumerate(ordered):
        if not start_day <= bar_day <= end_day or index == 0:
            continue
        row = (bar_day, ordered[index - 1][1])
        if cadence == "daily":
            daily.append(row)
            continue
        if cadence == "monthly":
            key = (bar_day.year, bar_day.month)
        else:
            iso_year, iso_week, _ = bar_day.isocalendar()
            key = (iso_year, iso_week)
        if key not in selected:
            selected[key] = row
    if cadence == "daily":
        return daily
    return [selected[key] for key in sorted(selected)]


def first_monthly_decision_days(
    daily_rows: Sequence[dict[str, Any]],
    start_day: date,
    end_day: date,
) -> list[tuple[date, float]]:
    """Backward-compatible monthly decision helper."""
    return first_period_decision_days(
        daily_rows,
        start_day,
        end_day,
        cadence="monthly",
    )


def is_third_friday(value: date) -> bool:
    return value.weekday() == 4 and 15 <= value.day <= 21


def select_contracts(
    contracts: Sequence[dict[str, Any]],
    decisions: Sequence[tuple[date, float]],
    *,
    option_type: str,
    expiry_policy: str,
    min_dte: int,
    max_dte: int,
    target_dte: int,
    min_moneyness: float,
    max_moneyness: float,
    target_moneyness: float,
    max_contracts_per_decision: int,
    single_expiry: bool,
    max_contracts_per_expiry: int | None = None,
) -> list[ContractSelection]:
    normalized = []
    for row in contracts:
        symbol = str(row.get("symbol") or "").upper()
        expiry_raw = str(row.get("expiration_date") or "")[:10]
        strike = number(row.get("strike_price"))
        row_type = str(row.get("type") or "").lower()
        if not symbol or not expiry_raw or strike is None or row_type != option_type:
            continue
        expiry = date.fromisoformat(expiry_raw)
        if expiry_policy == "monthly" and not is_third_friday(expiry):
            continue
        if expiry_policy == "friday" and expiry.weekday() != 4:
            continue
        normalized.append((symbol, expiry, strike, row_type))

    selected: list[ContractSelection] = []
    for decision_day, spot in decisions:
        eligible = []
        for symbol, expiry, strike, row_type in normalized:
            dte = (expiry - decision_day).days
            moneyness = strike / spot if spot > 0 else 0.0
            if min_dte <= dte <= max_dte and min_moneyness <= moneyness <= max_moneyness:
                eligible.append((symbol, expiry, strike, row_type, dte, moneyness))
        if single_expiry and eligible:
            expiry = min(
                {row[1] for row in eligible},
                key=lambda value: (abs((value - decision_day).days - target_dte), value),
            )
            eligible = [row for row in eligible if row[1] == expiry]
        eligible.sort(
            key=lambda row: (
                abs(row[5] - target_moneyness),
                abs(row[4] - target_dte),
                row[2],
            )
        )
        expiry_counts: dict[date, int] = {}
        chosen: list[tuple[str, date, float, str, int, float]] = []
        for row in eligible:
            if max_contracts_per_expiry is not None:
                current = expiry_counts.get(row[1], 0)
                if current >= int(max_contracts_per_expiry):
                    continue
                expiry_counts[row[1]] = current + 1
            chosen.append(row)
            if len(chosen) >= max(0, int(max_contracts_per_decision)):
                break
        for rank, row in enumerate(chosen, start=1):
            selected.append(
                ContractSelection(
                    decision_date=decision_day.isoformat(),
                    symbol=row[0],
                    expiration_date=row[1].isoformat(),
                    strike=row[2],
                    option_type=row[3],
                    spot=float(spot),
                    dte=int(row[4]),
                    moneyness=float(row[5]),
                    rank=rank,
                )
            )
    return selected


def chunks(values: Sequence[str], size: int) -> Iterator[tuple[str, ...]]:
    size = max(1, int(size))
    for index in range(0, len(values), size):
        yield tuple(values[index : index + size])


def aggregate_window_results(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    by_kind: dict[str, dict[str, int]] = {}
    for row in rows:
        kind = str(row.get("kind") or "unknown")
        bucket = by_kind.setdefault(
            kind,
            {"calls": 0, "symbols": 0, "pages": 0, "rows": 0, "skipped_calls": 0},
        )
        bucket["calls"] += 1
        bucket["symbols"] += int(row.get("symbols") or 0)
        bucket["pages"] += int(row.get("pages") or 0)
        bucket["rows"] += int(row.get("rows") or 0)
        bucket["skipped_calls"] += 1 if row.get("skipped") else 0
    return {"total_calls": len(rows), "by_kind": by_kind}


def market_window(start_day: date, end_day: date) -> tuple[str, str]:
    start = datetime.combine(start_day, dt_time(9, 30), tzinfo=NY).astimezone(UTC)
    end = datetime.combine(end_day, dt_time(16, 1), tzinfo=NY).astimezone(UTC)
    return iso_utc(start), iso_utc(end)


def observation_rows(
    store: HistoricalOptionsStore,
    selections: Sequence[ContractSelection],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with store.connect() as db:
        for selection in selections:
            day = date.fromisoformat(selection.decision_date)
            start_at, end_at = market_window(day, day)
            first_bar = db.execute(
                "select min(timestamp) from option_bars where symbol=?",
                (selection.symbol,),
            ).fetchone()[0]
            first_trade = db.execute(
                "select min(timestamp) from option_trades where symbol=?",
                (selection.symbol,),
            ).fetchone()[0]
            bars_on_decision = int(
                db.execute(
                    """select count(*) from option_bars
                       where symbol=? and timestamp>=? and timestamp<=?""",
                    (selection.symbol, start_at, end_at),
                ).fetchone()[0]
            )
            trades_on_decision = int(
                db.execute(
                    """select count(*) from option_trades
                       where symbol=? and timestamp>=? and timestamp<=?""",
                    (selection.symbol, start_at, end_at),
                ).fetchone()[0]
            )
            rows.append(
                {
                    "decision_date": selection.decision_date,
                    "symbol": selection.symbol,
                    "first_bar_at": first_bar,
                    "first_trade_at": first_trade,
                    "bars_on_decision": bars_on_decision,
                    "trades_on_decision": trades_on_decision,
                    "observed_on_decision": bool(bars_on_decision or trades_on_decision),
                }
            )
    return rows


def finalize_observed_candidates(
    candidates: Sequence[ContractSelection],
    audits: Sequence[dict[str, Any]],
    *,
    max_contracts_per_decision: int,
    target_dte: int,
    target_moneyness: float,
    single_expiry: bool,
) -> list[ContractSelection]:
    observed = {
        (str(row["decision_date"]), str(row["symbol"]))
        for row in audits
        if bool(row.get("observed_on_decision"))
    }
    by_day: dict[str, list[ContractSelection]] = {}
    for row in candidates:
        if (row.decision_date, row.symbol) in observed:
            by_day.setdefault(row.decision_date, []).append(row)

    final: list[ContractSelection] = []
    for decision_date, rows in sorted(by_day.items()):
        if single_expiry and rows:
            by_expiry: dict[str, list[ContractSelection]] = {}
            for row in rows:
                by_expiry.setdefault(row.expiration_date, []).append(row)
            chosen_expiry = min(
                by_expiry,
                key=lambda expiry: (
                    0 if len(by_expiry[expiry]) >= max_contracts_per_decision else 1,
                    abs(by_expiry[expiry][0].dte - target_dte),
                    -len(by_expiry[expiry]),
                    expiry,
                ),
            )
            rows = by_expiry[chosen_expiry]
        rows = sorted(
            rows,
            key=lambda row: (
                abs(row.moneyness - target_moneyness),
                abs(row.dte - target_dte),
                row.strike,
            ),
        )[: max(0, int(max_contracts_per_decision))]
        for rank, row in enumerate(rows, start=1):
            final.append(
                ContractSelection(
                    decision_date=row.decision_date,
                    symbol=row.symbol,
                    expiration_date=row.expiration_date,
                    strike=row.strike,
                    option_type=row.option_type,
                    spot=row.spot,
                    dte=row.dte,
                    moneyness=row.moneyness,
                    rank=rank,
                )
            )
    return final


def selection_observation_audit(
    store: HistoricalOptionsStore,
    selections: Sequence[ContractSelection],
) -> list[dict[str, Any]]:
    rows = observation_rows(store, selections)
    store.save_selection_observation_audit(rows)
    (store.root / "selection_observation_audit.json").write_text(
        json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8"
    )
    return rows


def sqlite_export_selections(store: HistoricalOptionsStore) -> Path:
    path = store.root / "selected_contracts.csv"
    with store.connect() as db:
        rows = db.execute(
            """select s.decision_date,s.symbol,s.expiration_date,s.strike,s.option_type,
                      s.spot,s.dte,s.moneyness,s.rank,
                      coalesce(a.observed_on_decision,0),a.bars_on_decision,a.trades_on_decision
               from decision_selections s
               left join selection_observation_audit a
                 on a.decision_date=s.decision_date and a.symbol=s.symbol
               order by s.decision_date,s.rank"""
        ).fetchall()
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            ["decision_date", "symbol", "expiration_date", "strike", "option_type", "spot", "dte", "moneyness", "rank", "observed_on_decision", "bars_on_decision", "trades_on_decision"]
        )
        writer.writerows(rows)
    return path


def cumulative_coverage(store: HistoricalOptionsStore) -> dict[str, Any]:
    with store.connect() as db:
        selection = db.execute(
            """select min(decision_date),max(decision_date),count(distinct decision_date),
                      min(expiration_date),max(expiration_date),count(*)
               from decision_selections"""
        ).fetchone()
        bars = db.execute(
            "select min(timestamp),max(timestamp),count(*),count(distinct symbol) from option_bars"
        ).fetchone()
        trades = db.execute(
            "select min(timestamp),max(timestamp),count(*),count(distinct symbol) from option_trades"
        ).fetchone()
        observed = db.execute(
            """select count(*),sum(case when observed_on_decision=1 then 1 else 0 end)
               from selection_observation_audit"""
        ).fetchone()
        runs = db.execute(
            "select count(*),sum(case when status='complete' then 1 else 0 end) from download_runs"
        ).fetchone()
    return {
        "decision_date_min": selection[0],
        "decision_date_max": selection[1],
        "decision_date_count": int(selection[2] or 0),
        "expiration_date_min": selection[3],
        "expiration_date_max": selection[4],
        "selected_contract_rows": int(selection[5] or 0),
        "option_bar_min": bars[0],
        "option_bar_max": bars[1],
        "option_bar_rows": int(bars[2] or 0),
        "option_bar_symbols": int(bars[3] or 0),
        "option_trade_min": trades[0],
        "option_trade_max": trades[1],
        "option_trade_rows": int(trades[2] or 0),
        "option_trade_symbols": int(trades[3] or 0),
        "selection_audit_rows": int(observed[0] or 0),
        "observed_on_decision_rows": int(observed[1] or 0),
        "download_runs": int(runs[0] or 0),
        "completed_download_runs": int(runs[1] or 0),
    }


def write_manifests(store: HistoricalOptionsStore, config: dict[str, Any], summary: dict[str, Any]) -> None:
    generated = iso_utc(utcnow())
    raw_pages = store.raw_page_manifest()
    with store.connect() as db:
        db.execute("pragma wal_checkpoint(TRUNCATE)")
    coverage = cumulative_coverage(store)
    detailed = {
        "schema_version": 1,
        "dataset_id": f"alpaca_{config['underlying'].lower()}_historical_options",
        "generated_at": generated,
        "status": "HISTORICAL_BARS_AND_TRADES_WITHOUT_HISTORICAL_NBBO",
        "provider": "Alpaca",
        "latest_run_config": config,
        "latest_run_summary": summary,
        "cumulative_coverage": coverage,
        "capabilities": {
            "expired_contract_metadata": True,
            "historical_option_bars": True,
            "historical_option_trades": bool(config.get("include_trades")),
            "historical_option_bid_ask": False,
            "historical_iv_greeks": False,
            "timestamp_aligned_underlying_bars": True,
            "historical_open_interest": False,
            "occ_adjustment_history": False,
        },
        "provenance": {
            "raw_pages": raw_pages,
            "database": {
                "path": str(store.db_path.relative_to(store.root)),
                "sha256": sha256_bytes(store.db_path.read_bytes()),
            },
        },
        "caveats": [
            "Historical option bars and trades are observed market events, not historical NBBO quotes.",
            "Current contract metadata fields such as open interest and close price are not point-in-time historical values.",
            "Contract-universe completeness and OCC adjustment coverage are not certified.",
            "Only rows marked observed_on_decision may be considered available at the decision timestamp.",
            "Execution tests using this dataset must be labeled as bar/trade approximations.",
        ],
    }
    (store.root / "download_manifest.json").write_text(
        json.dumps(detailed, indent=2, sort_keys=True), encoding="utf-8"
    )
    strict_manifest = {
        "dataset_id": detailed["dataset_id"],
        "provider": "Alpaca historical options bars/trades",
        "generated_at": generated,
        "quote_granularity": "minute",
        "point_in_time": True,
        "bid_ask_source": "absent",
        "timezone_name": "UTC",
        "includes_underlying_marks": True,
        "includes_historical_open_interest": False,
        "includes_historical_volume": True,
        "includes_iv_or_greeks": False,
        "includes_rates": False,
        "includes_dividends": False,
        "includes_contract_adjustments": False,
        "survivorship_safe_universe": False,
        "source_files": [
            str(store.db_path.relative_to(store.root)),
            *[page["path"] for page in raw_pages],
        ],
        "notes": detailed["caveats"],
    }
    (store.root / "option_dataset_manifest.json").write_text(
        json.dumps(strict_manifest, indent=2, sort_keys=True), encoding="utf-8"
    )


def run_download(args: argparse.Namespace) -> dict[str, Any]:
    underlying = str(args.underlying).upper()
    start_day = parse_day(args.start)
    end_day = parse_day(args.end)
    if end_day < start_day:
        raise DownloadError("end date must not precede start date")
    if args.min_dte < 0 or args.max_dte < args.min_dte:
        raise DownloadError("invalid DTE range")
    if not 0 < args.min_moneyness <= args.max_moneyness:
        raise DownloadError("invalid moneyness range")
    key, secret, stock_feed = alpaca_credentials()
    client = JsonHttpClient(
        {
            "APCA-API-KEY-ID": key,
            "APCA-API-SECRET-KEY": secret,
            "Accept": "application/json",
            "User-Agent": "Cipher-Historical-Options-Research/1.0",
        },
        timeout=args.timeout,
        retries=args.retries,
    )
    root = Path(args.output_root or DEFAULT_ROOT)
    store = HistoricalOptionsStore(root)
    config = {
        "underlying": underlying,
        "start_date": start_day.isoformat(),
        "end_date": end_day.isoformat(),
        "min_dte": int(args.min_dte),
        "max_dte": int(args.max_dte),
        "target_dte": int(args.target_dte),
        "option_type": args.option_type,
        "min_moneyness": float(args.min_moneyness),
        "max_moneyness": float(args.max_moneyness),
        "target_moneyness": float(args.target_moneyness),
        "max_contracts_per_decision": int(args.max_contracts),
        "discovery_contracts_per_decision": int(args.discovery_contracts),
        "discovery_contracts_per_expiry": int(args.discovery_per_expiry),
        "single_expiry": bool(args.single_expiry),
        "expiry_policy": str(args.expiry_policy),
        "batch_size": int(args.batch_size),
        "include_trades": bool(args.include_trades),
        "resume": bool(args.resume),
        "stock_feed": stock_feed,
        "options_history_start_constraint": "2024-02-01",
        "underlying_history_start": (
            parse_day(args.underlying_history_start).isoformat()
            if args.underlying_history_start
            else None
        ),
        "decision_cadence": str(args.decision_cadence),
    }
    run_id = store.start_run(config)
    downloader = AlpacaHistoricalOptionsDownloader(store, client)
    summary: dict[str, Any] = {"run_id": run_id, "config": config, "windows": []}
    try:
        broad_start = (
            parse_day(args.underlying_history_start)
            if args.underlying_history_start
            else start_day - timedelta(days=7)
        )
        if broad_start >= start_day:
            raise DownloadError("underlying-history-start must precede the option decision period")
        broad_end = end_day + timedelta(days=args.max_dte + 3)
        daily_start, daily_end = market_window(broad_start, broad_end)
        downloader.download_underlying_bars(
            run_id, underlying, daily_start, daily_end, "1Day", stock_feed
        )
        with store.connect() as db:
            daily_db_rows = db.execute(
                """select timestamp,close from underlying_bars
                   where symbol=? and timeframe='1Day' order by timestamp""",
                (underlying,),
            ).fetchall()
        daily_rows = [{"t": row[0], "c": row[1]} for row in daily_db_rows]
        decisions = first_period_decision_days(
            daily_rows,
            start_day,
            end_day,
            cadence=str(args.decision_cadence),
        )
        if args.decision_date:
            requested = parse_day(args.decision_date)
            ordered = daily_closes(daily_rows)
            matching_index = next((index for index, row in enumerate(ordered) if row[0] == requested), None)
            if matching_index is None:
                raise DownloadError(f"no underlying daily bar exists for decision date {requested}")
            if matching_index == 0:
                raise DownloadError(f"no prior daily close exists before decision date {requested}")
            decisions = [(requested, float(ordered[matching_index - 1][1]))]
        if not decisions:
            raise DownloadError(
                f"no valid {args.decision_cadence} decision dates found"
            )

        expiry_gte = (min(day for day, _ in decisions) + timedelta(days=args.min_dte)).isoformat()
        expiry_lte = (max(day for day, _ in decisions) + timedelta(days=args.max_dte)).isoformat()
        contracts = downloader.enumerate_contracts(run_id, underlying, expiry_gte, expiry_lte)
        candidates = select_contracts(
            contracts,
            decisions,
            option_type=args.option_type,
            expiry_policy=args.expiry_policy,
            min_dte=args.min_dte,
            max_dte=args.max_dte,
            target_dte=args.target_dte,
            min_moneyness=args.min_moneyness,
            max_moneyness=args.max_moneyness,
            target_moneyness=args.target_moneyness,
            max_contracts_per_decision=args.discovery_contracts,
            single_expiry=False,
            max_contracts_per_expiry=args.discovery_per_expiry,
        )
        if not candidates:
            raise DownloadError("contract discovery returned zero candidates")

        summary["discovery_windows"] = []
        candidates_by_day: dict[str, list[str]] = {}
        for row in candidates:
            candidates_by_day.setdefault(row.decision_date, []).append(row.symbol)
        for decision_date, symbols in sorted(candidates_by_day.items()):
            day = date.fromisoformat(decision_date)
            start_at, end_at = market_window(day, day)
            for batch in chunks(sorted(set(symbols)), args.batch_size):
                result = downloader.download_option_window(
                    run_id=run_id,
                    kind="bars",
                    symbols=batch,
                    start_at=start_at,
                    end_at=end_at,
                    timeframe="1Min",
                    resume=args.resume,
                )
                summary["discovery_windows"].append(result)

        candidate_audits = observation_rows(store, candidates)
        (store.root / "candidate_observation_audit.json").write_text(
            json.dumps(candidate_audits, indent=2, sort_keys=True), encoding="utf-8"
        )
        selections = finalize_observed_candidates(
            candidates,
            candidate_audits,
            max_contracts_per_decision=args.max_contracts,
            target_dte=args.target_dte,
            target_moneyness=args.target_moneyness,
            single_expiry=args.single_expiry,
        )
        if not selections:
            raise DownloadError("no discovery candidates were observed on a decision date")
        store.save_selections(
            selections,
            [day.isoformat() for day, _spot in decisions],
            underlying=underlying,
            option_type=args.option_type,
        )

        by_window: dict[tuple[str, str], list[str]] = {}
        for row in selections:
            start_at, end_at = market_window(
                date.fromisoformat(row.decision_date),
                date.fromisoformat(row.expiration_date),
            )
            by_window.setdefault((start_at, end_at), []).append(row.symbol)

        for (start_at, end_at), symbols in sorted(by_window.items()):
            downloader.download_underlying_bars(
                run_id, underlying, start_at, end_at, "1Min", stock_feed
            )
            for batch in chunks(sorted(set(symbols)), args.batch_size):
                bar_result = downloader.download_option_window(
                    run_id=run_id,
                    kind="bars",
                    symbols=batch,
                    start_at=start_at,
                    end_at=end_at,
                    timeframe="1Min",
                    resume=args.resume,
                )
                summary["windows"].append(bar_result)
                if args.include_trades:
                    trade_result = downloader.download_option_window(
                        run_id=run_id,
                        kind="trades",
                        symbols=batch,
                        start_at=start_at,
                        end_at=end_at,
                        timeframe=None,
                        resume=args.resume,
                    )
                    summary["windows"].append(trade_result)

        final_observation_rows = selection_observation_audit(store, selections)
        sqlite_export_selections(store)
        observed_count = sum(bool(row["observed_on_decision"]) for row in final_observation_rows)
        summary["discovery_windows"] = aggregate_window_results(summary["discovery_windows"])
        summary["windows"] = aggregate_window_results(summary["windows"])
        summary.update(
            {
                "decision_dates": [day.isoformat() for day, _ in decisions],
                "enumerated_contracts": len(contracts),
                "discovery_candidates": len(candidates),
                "discovery_observed_candidates": sum(
                    bool(row["observed_on_decision"]) for row in candidate_audits
                ),
                "candidate_observation_audit_file": "candidate_observation_audit.json",
                "selected_contracts": len(selections),
                "selected_expirations": sorted({row.expiration_date for row in selections}),
                "observed_on_decision_count": observed_count,
                "unobserved_on_decision_count": len(final_observation_rows) - observed_count,
                "selection_observation_audit_file": "selection_observation_audit.json",
                "selection_observation_audit_sample": final_observation_rows[:20],
                "counts": store.counts(),
                "output_root": str(store.root),
                "database": str(store.db_path),
                "selection_sample": [asdict(row) for row in selections[:10]],
            }
        )
        store.finish_run(run_id, "complete", summary)
        write_manifests(store, config, summary)
        (store.root / "latest_run.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8"
        )
        return summary
    except Exception as exc:
        summary["counts"] = store.counts()
        summary["error"] = str(exc)
        store.finish_run(run_id, "failed", summary, str(exc)[:1000])
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download immutable Alpaca historical option bars/trades for local research."
    )
    parser.add_argument("--underlying", default="SPY")
    parser.add_argument("--start", required=True, help="Decision-period start date")
    parser.add_argument("--end", required=True, help="Decision-period end date")
    parser.add_argument("--decision-date", help="Use one exact trading decision date")
    parser.add_argument(
        "--decision-cadence",
        choices=("monthly", "weekly", "daily"),
        default="monthly",
        help="Choose the first trading day of each month/week, or every trading day",
    )
    parser.add_argument(
        "--underlying-history-start",
        help="Optional earlier date for daily underlying feature history",
    )
    parser.add_argument("--option-type", choices=("put", "call"), default="put")
    parser.add_argument("--min-dte", type=int, default=28)
    parser.add_argument("--max-dte", type=int, default=45)
    parser.add_argument("--target-dte", type=int, default=35)
    parser.add_argument("--min-moneyness", type=float, default=0.85)
    parser.add_argument("--max-moneyness", type=float, default=1.02)
    parser.add_argument("--target-moneyness", type=float, default=0.94)
    parser.add_argument("--max-contracts", type=int, default=24)
    parser.add_argument("--discovery-contracts", type=int, default=96)
    parser.add_argument("--discovery-per-expiry", type=int, default=32)
    parser.add_argument("--single-expiry", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--expiry-policy", choices=("monthly", "friday", "any"), default="friday")
    parser.add_argument("--batch-size", type=int, default=6)
    parser.add_argument("--include-trades", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--output-root")
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--retries", type=int, default=6)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = run_download(args)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
