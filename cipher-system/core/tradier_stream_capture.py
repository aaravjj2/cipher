"""Read-only Tradier underlying and option market-data stream capture.

The collector resolves a bounded set of live OCC option symbols around each
underlying's spot price, subscribes to those contracts through Tradier's market
stream, and stores every raw event plus normalized audit columns. It never calls
account, order, preview, or execution endpoints.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import signal
import socket
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, time as clock_time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DEFAULT_DB = DATA_DIR / "tradier_stream.sqlite"
DEFAULT_RAW_DIR = DATA_DIR / "tradier_stream_events"
DEFAULT_LOCK_PATH = DATA_DIR / "tradier_stream_capture.lock"
DEFAULT_SELECTION_PATH = DATA_DIR / "tradier_stream_selection_latest.json"
APP_ENV = ROOT / ".env"
LEGACY_ENV_PATHS = (
    ROOT / "previous-work" / "keys.env",
    ROOT / "previous-work" / ".env",
)
PRODUCTION_API = "https://api.tradier.com"
PRODUCTION_STREAM = "https://stream.tradier.com"
SANDBOX_API = "https://sandbox.tradier.com"
DEFAULT_FILTERS = ("quote", "trade", "timesale", "tradex", "summary")
DEFAULT_UNDERLYINGS = "SPY,QQQ,IWM,NVDA,MSFT,AAPL,AVGO,AMZN,IBIT,GOOGL,TSLA,META,MU,AMD"
OCC_RE = re.compile(r"^([A-Z]{1,6})(\d{6})([CP])(\d{8})$")
STOP_REQUESTED = False
NEW_YORK = ZoneInfo("America/New_York")
REGULAR_SESSION_START = clock_time(9, 30)
REGULAR_SESSION_END = clock_time(16, 0)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def regular_session_open(now: datetime | None = None) -> bool:
    """Return whether local New York time is inside the weekday cash session.

    Tradier can still emit extended-hours events, but Cipher's validation dataset
    intentionally uses the 09:30-16:00 ET regular session. Exchange holidays are
    harmless here: the stream will simply receive no market events.
    """
    current = (now or datetime.now(timezone.utc)).astimezone(NEW_YORK)
    return (
        current.weekday() < 5
        and REGULAR_SESSION_START <= current.time().replace(tzinfo=None) < REGULAR_SESSION_END
    )


def seconds_until_regular_close(now: datetime | None = None) -> int:
    current = (now or datetime.now(timezone.utc)).astimezone(NEW_YORK)
    if not regular_session_open(current):
        return 0
    close = current.replace(hour=16, minute=0, second=0, microsecond=0)
    return max(0, int((close - current).total_seconds()))


def install_signal_handlers() -> None:
    def _request_stop(_signum: int, _frame: Any) -> None:
        global STOP_REQUESTED
        STOP_REQUESTED = True

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        if value:
            values[key.strip()] = value
    return values


def load_credentials(env: str) -> tuple[str, str]:
    merged: dict[str, str] = {}
    for path in (*LEGACY_ENV_PATHS, APP_ENV):
        merged.update(parse_env_file(path))
    merged.update({key: value for key, value in os.environ.items() if value})

    token_keys = (
        "TRADIER_ACCESS_TOKEN",
        "TRADIER_TOKEN",
        "TRADIER_API_KEY",
    )
    if env == "sandbox":
        token_keys = (
            "TRADIER_SANDBOX_TOKEN",
            "TRADIER_SANDBOX_API_KEY",
            *token_keys,
        )
    token = next((merged.get(key) for key in token_keys if merged.get(key)), "")
    if not token:
        raise RuntimeError(
            "Tradier token not found. Set TRADIER_ACCESS_TOKEN/TRADIER_TOKEN "
            "server-side or place it in cipher-system/.env."
        )
    return token, env


def parse_symbols(raw_symbols: str, position_files: Iterable[Path] = ()) -> list[str]:
    symbols = [part.strip().upper() for part in raw_symbols.split(",") if part.strip()]
    for path in position_files:
        if not path.is_file():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        ticker = str(payload.get("ticker") or "").upper()
        if ticker:
            symbols.append(ticker)
        for leg in payload.get("legs") or []:
            occ = str(leg.get("symbol") or leg.get("occ_symbol") or "").upper()
            if occ:
                symbols.append(occ)
    out: list[str] = []
    seen: set[str] = set()
    for symbol in symbols:
        if symbol and symbol not in seen:
            seen.add(symbol)
            out.append(symbol)
    return out


def acquire_lock(lock_path: Path) -> Any | None:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("w", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    handle.write(f"pid={os.getpid()} started_at={utcnow()}\n")
    handle.flush()
    return handle


def _columns(db: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in db.execute(f"pragma table_info({table})")}


def _add_column(db: sqlite3.Connection, table: str, definition: str) -> None:
    name = definition.split()[0]
    if name not in _columns(db, table):
        db.execute(f"alter table {table} add column {definition}")


def ensure_schema(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as db:
        db.executescript(
            """
            pragma journal_mode=WAL;
            pragma synchronous=NORMAL;

            create table if not exists tradier_stream_runs (
                id integer primary key autoincrement,
                started_at text not null,
                completed_at text,
                env text not null,
                symbols text not null,
                filters text not null,
                event_count integer not null default 0,
                error text,
                caveat text not null,
                requested_underlyings text not null default '',
                resolved_symbol_count integer not null default 0,
                option_contract_count integer not null default 0,
                selection_json text not null default '{}',
                last_event_at text,
                stop_reason text
            );

            create table if not exists tradier_stream_events (
                id integer primary key autoincrement,
                run_id integer not null references tradier_stream_runs(id),
                captured_at text not null,
                provider_ts text,
                event_type text,
                symbol text,
                bid real,
                ask real,
                last real,
                price real,
                size real,
                raw_json text not null,
                asset_class text,
                underlying text,
                option_expiration text,
                option_type text,
                strike real
            );

            create table if not exists tradier_stream_meta (
                key text primary key,
                value text not null,
                updated_at text not null
            );

            create table if not exists tradier_latest_quotes (
                symbol text primary key,
                updated_at text not null,
                provider_ts text,
                bid real,
                ask real,
                last real,
                event_type text,
                raw_json text not null,
                asset_class text,
                underlying text,
                option_expiration text,
                option_type text,
                strike real
            );

            -- A narrow, query-oriented projection of option timesales.  The raw
            -- event table is intentionally exhaustive and is now tens of GB; a
            -- trader-facing tape must not scan that table or pretend that the
            -- latest trade attached to every chain snapshot is a live tape.
            -- `stream_event_id` keeps one immutable link back to the authoritative
            -- captured event and makes bounded historical backfills idempotent.
            create table if not exists tradier_option_timesales (
                stream_event_id integer primary key,
                run_id integer not null references tradier_stream_runs(id),
                captured_at text not null,
                provider_ts text,
                session_date text not null,
                symbol text not null,
                underlying text not null,
                option_expiration text,
                option_type text,
                strike real,
                bid real,
                ask real,
                price real,
                size real,
                premium real,
                exchange text
            );
            """
        )
        for definition in (
            "requested_underlyings text not null default ''",
            "resolved_symbol_count integer not null default 0",
            "option_contract_count integer not null default 0",
            "selection_json text not null default '{}'",
            "last_event_at text",
            "stop_reason text",
        ):
            _add_column(db, "tradier_stream_runs", definition)
        for table in ("tradier_stream_events", "tradier_latest_quotes"):
            for definition in (
                "asset_class text",
                "underlying text",
                "option_expiration text",
                "option_type text",
                "strike real",
            ):
                _add_column(db, table, definition)
        db.executescript(
            """
            create index if not exists idx_tradier_events_run_id
                on tradier_stream_events(run_id);
            create index if not exists idx_tradier_events_symbol_time
                on tradier_stream_events(symbol, captured_at);
            create index if not exists idx_tradier_events_type_time
                on tradier_stream_events(event_type, captured_at);
            create index if not exists idx_tradier_timesales_underlying_session_time
                on tradier_option_timesales(underlying, session_date, provider_ts desc);
            create index if not exists idx_tradier_timesales_symbol_time
                on tradier_option_timesales(symbol, provider_ts desc);
            create index if not exists idx_tradier_timesales_underlying_session_premium
                on tradier_option_timesales(underlying, session_date, premium);
            """
        )


def _parse_iso(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def reconcile_run_counts(db_path: Path) -> int:
    """Repair run counters from the authoritative event table.

    Older collectors updated event_count only on graceful completion, so killed
    runs could contain hundreds of thousands of rows while reporting zero.
    """
    repaired = 0
    with sqlite3.connect(db_path) as db:
        marker = db.execute(
            "select value from tradier_stream_meta where key = 'run_counts_reconciled_v2'"
        ).fetchone()
        if marker:
            return 0
        rows = db.execute(
            """
            select r.id, r.event_count, count(e.id) as actual
            from tradier_stream_runs r
            left join tradier_stream_events e on e.run_id = r.id
            group by r.id
            having r.event_count != actual
            """
        ).fetchall()
        for run_id, _declared, actual in rows:
            db.execute(
                "update tradier_stream_runs set event_count = ? where id = ?",
                (int(actual), int(run_id)),
            )
            repaired += 1
        db.execute(
            """
            insert into tradier_stream_meta (key, value, updated_at)
            values ('run_counts_reconciled_v2', ?, ?)
            on conflict(key) do update set value = excluded.value, updated_at = excluded.updated_at
            """,
            (json.dumps({"repaired_runs": repaired}), utcnow()),
        )
    return repaired


def reconcile_stale_runs(db_path: Path, older_than_seconds: int = 1800) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=max(60, older_than_seconds))
    repaired = 0
    with sqlite3.connect(db_path) as db:
        rows = db.execute(
            "select id, started_at from tradier_stream_runs where completed_at is null"
        ).fetchall()
        for run_id, started_at in rows:
            parsed = _parse_iso(str(started_at))
            if parsed is None or parsed > cutoff:
                continue
            actual = int(
                db.execute(
                    "select count(*) from tradier_stream_events where run_id = ?",
                    (int(run_id),),
                ).fetchone()[0]
            )
            db.execute(
                """
                update tradier_stream_runs
                set completed_at = ?, event_count = ?,
                    error = coalesce(error, 'collector interrupted before normal completion'),
                    stop_reason = coalesce(stop_reason, 'reconciled_stale_run')
                where id = ?
                """,
                (utcnow(), actual, int(run_id)),
            )
            repaired += 1
    return repaired


def create_run(
    db_path: Path,
    *,
    env: str,
    requested_underlyings: list[str],
    symbols: list[str],
    filters: list[str],
    selection: dict[str, Any],
) -> int:
    with sqlite3.connect(db_path) as db:
        cur = db.execute(
            """
            insert into tradier_stream_runs (
                started_at, env, symbols, filters, caveat,
                requested_underlyings, resolved_symbol_count,
                option_contract_count, selection_json
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                utcnow(),
                env,
                ",".join(symbols),
                ",".join(filters),
                "Read-only Tradier market-data stream. No account or order endpoints are used.",
                ",".join(requested_underlyings),
                len(symbols),
                int(selection.get("option_contract_count") or 0),
                json.dumps(selection, separators=(",", ":"), default=str),
            ),
        )
        return int(cur.lastrowid)


def finish_run(
    db_path: Path,
    run_id: int,
    *,
    error: str | None = None,
    stop_reason: str | None = None,
) -> int:
    with sqlite3.connect(db_path) as db:
        actual = int(
            db.execute(
                "select count(*) from tradier_stream_events where run_id = ?",
                (int(run_id),),
            ).fetchone()[0]
        )
        db.execute(
            """
            update tradier_stream_runs
            set completed_at = ?, event_count = ?, error = ?, stop_reason = ?
            where id = ?
            """,
            (utcnow(), actual, error, stop_reason, int(run_id)),
        )
    return actual


def num(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def event_ts(event: dict[str, Any]) -> str | None:
    raw = event.get("date") or event.get("biddate") or event.get("askdate")
    if raw in (None, ""):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return str(raw)
    if value > 10_000_000_000:
        value /= 1000
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return str(raw)


def parse_occ_symbol(symbol: str) -> dict[str, Any] | None:
    match = OCC_RE.fullmatch(str(symbol or "").upper())
    if not match:
        return None
    root, yymmdd, cp, strike_raw = match.groups()
    try:
        expiration = datetime.strptime(yymmdd, "%y%m%d").date().isoformat()
    except ValueError:
        return None
    return {
        "underlying": root,
        "expiration": expiration,
        "option_type": "call" if cp == "C" else "put",
        "strike": int(strike_raw) / 1000.0,
    }


def store_events(
    db_path: Path,
    raw_path: Path,
    run_id: int,
    events: list[dict[str, Any]],
) -> None:
    if not events:
        return
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    with raw_path.open("a", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event, separators=(",", ":"), default=str) + "\n")

    rows = []
    latest_rows = []
    captured_at = utcnow()
    for event in events:
        event_type = str(event.get("type") or "")
        symbol = str(event.get("symbol") or "").upper()
        contract = parse_occ_symbol(symbol)
        asset_class = "option" if contract else ("underlying" if symbol else "system")
        underlying = contract["underlying"] if contract else symbol
        option_expiration = contract["expiration"] if contract else None
        option_type = contract["option_type"] if contract else None
        strike = contract["strike"] if contract else None
        provider_ts = event_ts(event)
        bid = num(event.get("bid"))
        ask = num(event.get("ask"))
        last = num(event.get("last"))
        price = num(event.get("price"))
        size = num(event.get("size"))
        raw_json = json.dumps(event, separators=(",", ":"), default=str)
        rows.append(
            (
                run_id,
                captured_at,
                provider_ts,
                event_type,
                symbol,
                bid,
                ask,
                last,
                price,
                size,
                raw_json,
                asset_class,
                underlying,
                option_expiration,
                option_type,
                strike,
            )
        )
        if symbol and event_type in {"quote", "trade", "timesale", "tradex"}:
            latest_rows.append(
                (
                    symbol,
                    captured_at,
                    provider_ts,
                    bid,
                    ask,
                    last if last is not None else price,
                    event_type,
                    raw_json,
                    asset_class,
                    underlying,
                    option_expiration,
                    option_type,
                    strike,
                )
            )

    with sqlite3.connect(db_path) as db:
        previous_event_id = int(
            db.execute("select coalesce(max(id), 0) from tradier_stream_events").fetchone()[0]
        )
        db.executemany(
            """
            insert into tradier_stream_events (
                run_id, captured_at, provider_ts, event_type, symbol,
                bid, ask, last, price, size, raw_json,
                asset_class, underlying, option_expiration, option_type, strike
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        # Project only the just-inserted batch.  The primary-key range is cheap
        # even when the authoritative event database is very large.  Timesale
        # events carry the bid/ask that accompanied the print, which is the only
        # basis this product may call contemporaneous side inference.
        db.execute(
            """
            insert or ignore into tradier_option_timesales (
                stream_event_id, run_id, captured_at, provider_ts, session_date,
                symbol, underlying, option_expiration, option_type, strike,
                bid, ask, price, size, premium, exchange
            )
            select
                id, run_id, captured_at, provider_ts,
                substr(coalesce(provider_ts, captured_at), 1, 10),
                symbol, underlying, option_expiration, option_type, strike,
                bid, ask, coalesce(last, price), size,
                case
                    when coalesce(last, price) is not null and size is not null
                    then coalesce(last, price) * size * 100.0
                    else null
                end,
                json_extract(raw_json, '$.exch')
            from tradier_stream_events
            where id > ? and run_id = ? and event_type = 'timesale'
              and asset_class = 'option' and symbol is not null
              and underlying is not null
            """,
            (previous_event_id, int(run_id)),
        )
        db.executemany(
            """
            insert into tradier_latest_quotes (
                symbol, updated_at, provider_ts, bid, ask, last, event_type,
                raw_json, asset_class, underlying, option_expiration,
                option_type, strike
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(symbol) do update set
                updated_at = excluded.updated_at,
                provider_ts = excluded.provider_ts,
                bid = coalesce(excluded.bid, tradier_latest_quotes.bid),
                ask = coalesce(excluded.ask, tradier_latest_quotes.ask),
                last = coalesce(excluded.last, tradier_latest_quotes.last),
                event_type = excluded.event_type,
                raw_json = excluded.raw_json,
                asset_class = excluded.asset_class,
                underlying = excluded.underlying,
                option_expiration = excluded.option_expiration,
                option_type = excluded.option_type,
                strike = excluded.strike
            """,
            latest_rows,
        )
        db.execute(
            """
            update tradier_stream_runs
            set event_count = event_count + ?, last_event_at = ?
            where id = ?
            """,
            (len(rows), captured_at, int(run_id)),
        )


def _http_error_detail(exc: urllib.error.HTTPError, stage: str) -> str:
    """Return a bounded provider error without leaking credentials or URLs."""
    try:
        body = exc.read(1024).decode("utf-8", errors="replace").strip()
    except (OSError, ValueError):
        body = ""
    body = " ".join(body.split())
    suffix = f": {body}" if body else ""
    return f"{stage}: HTTP {exc.code} {exc.reason}{suffix}"


def _request_json(
    *,
    token: str,
    env: str,
    path: str,
    params: dict[str, Any] | None = None,
    method: str = "GET",
    timeout: int = 30,
) -> Any:
    base = SANDBOX_API if env == "sandbox" else PRODUCTION_API
    query = urllib.parse.urlencode(params or {}, doseq=True)
    url = f"{base}{path}" + (f"?{query}" if query else "")
    req = urllib.request.Request(
        url,
        data=b"" if method.upper() == "POST" else None,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method=method.upper(),
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(_http_error_detail(exc, f"Tradier {method.upper()} {path}")) from exc


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def fetch_underlying_spots(token: str, env: str, symbols: list[str]) -> dict[str, float]:
    if not symbols:
        return {}
    payload = _request_json(
        token=token,
        env=env,
        path="/v1/markets/quotes",
        params={"symbols": ",".join(symbols), "greeks": "false"},
    )
    quotes = payload.get("quotes", {}).get("quote") if isinstance(payload, dict) else None
    spots: dict[str, float] = {}
    for quote in _as_list(quotes):
        if not isinstance(quote, dict):
            continue
        symbol = str(quote.get("symbol") or "").upper()
        bid = num(quote.get("bid"))
        ask = num(quote.get("ask"))
        spot = num(quote.get("last")) or num(quote.get("close"))
        if spot is None and bid is not None and ask is not None and ask >= bid:
            spot = (bid + ask) / 2.0
        if symbol and spot is not None and spot > 0:
            spots[symbol] = spot
    return spots


def fetch_expirations(token: str, env: str, underlying: str) -> list[str]:
    payload = _request_json(
        token=token,
        env=env,
        path="/v1/markets/options/expirations",
        params={
            "symbol": underlying,
            "includeAllRoots": "true",
            "strikes": "false",
        },
    )
    expirations = payload.get("expirations", {}) if isinstance(payload, dict) else {}
    raw_dates = expirations.get("date") if isinstance(expirations, dict) else expirations
    return [str(item) for item in _as_list(raw_dates) if item]


def fetch_option_chain(token: str, env: str, underlying: str, expiration: str) -> list[dict[str, Any]]:
    payload = _request_json(
        token=token,
        env=env,
        path="/v1/markets/options/chains",
        params={
            "symbol": underlying,
            "expiration": expiration,
            "greeks": "false",
        },
    )
    options = payload.get("options", {}) if isinstance(payload, dict) else {}
    raw_options = options.get("option") if isinstance(options, dict) else options
    return [item for item in _as_list(raw_options) if isinstance(item, dict)]


def eligible_expirations(
    expirations: list[str],
    *,
    today: date | None = None,
    min_dte: int = 0,
    max_dte: int = 14,
    count: int = 1,
) -> list[str]:
    current = today or datetime.now(timezone.utc).date()
    eligible: list[tuple[int, str]] = []
    for raw in expirations:
        try:
            expiry = date.fromisoformat(str(raw))
        except ValueError:
            continue
        dte = (expiry - current).days
        if min_dte <= dte <= max_dte:
            eligible.append((dte, expiry.isoformat()))
    eligible.sort()
    return [expiry for _, expiry in eligible[: max(1, count)]]


def select_chain_contracts(
    chain: list[dict[str, Any]],
    *,
    spot: float,
    strikes_per_side: int,
    max_contracts: int,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for option in chain:
        symbol = str(option.get("symbol") or "").upper()
        strike = num(option.get("strike"))
        option_type = str(option.get("option_type") or option.get("type") or "").lower()
        if option_type in {"c", "call"}:
            option_type = "call"
        elif option_type in {"p", "put"}:
            option_type = "put"
        else:
            parsed = parse_occ_symbol(symbol)
            option_type = parsed["option_type"] if parsed else ""
        if not symbol or strike is None or strike <= 0 or option_type not in {"call", "put"}:
            continue
        normalized.append(
            {
                "symbol": symbol,
                "strike": strike,
                "option_type": option_type,
                "distance_pct": abs(strike - spot) / spot,
            }
        )
    strikes = sorted({row["strike"] for row in normalized})
    below = sorted((strike for strike in strikes if strike <= spot), reverse=True)
    above = sorted(strike for strike in strikes if strike > spot)
    selected_strikes = set(
        below[: max(1, strikes_per_side)] + above[: max(1, strikes_per_side)]
    )
    selected = [row for row in normalized if row["strike"] in selected_strikes]
    selected.sort(
        key=lambda row: (
            row["distance_pct"],
            row["strike"],
            0 if row["option_type"] == "call" else 1,
        )
    )
    return selected[: max(2, max_contracts)]


def _round_robin_contracts(
    by_underlying: dict[str, list[dict[str, Any]]],
    underlying_order: list[str],
    limit: int,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    depth = 0
    while len(selected) < limit:
        added = False
        for underlying in underlying_order:
            contracts = by_underlying.get(underlying) or []
            if depth < len(contracts):
                selected.append(contracts[depth])
                added = True
                if len(selected) >= limit:
                    break
        if not added:
            break
        depth += 1
    return selected


def resolve_stream_universe(
    *,
    token: str,
    env: str,
    underlyings: list[str],
    option_underlyings: list[str],
    include_options: bool,
    expiration_count: int,
    strikes_per_side: int,
    min_dte: int,
    max_dte: int,
    max_options_per_underlying: int,
    max_stream_symbols: int,
    today: date | None = None,
) -> dict[str, Any]:
    requested = list(dict.fromkeys([*underlyings, *option_underlyings]))
    spots = fetch_underlying_spots(token, env, requested)
    by_underlying: dict[str, list[dict[str, Any]]] = {}
    details: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    if include_options:
        for underlying in option_underlyings:
            spot = spots.get(underlying)
            if spot is None:
                errors.append({"underlying": underlying, "error": "quote did not provide a positive spot"})
                continue
            try:
                expirations = eligible_expirations(
                    fetch_expirations(token, env, underlying),
                    today=today,
                    min_dte=min_dte,
                    max_dte=max_dte,
                    count=expiration_count,
                )
                contracts: list[dict[str, Any]] = []
                for expiration in expirations:
                    selected = select_chain_contracts(
                        fetch_option_chain(token, env, underlying, expiration),
                        spot=spot,
                        strikes_per_side=strikes_per_side,
                        max_contracts=max_options_per_underlying,
                    )
                    for contract in selected:
                        contract["underlying"] = underlying
                        contract["expiration"] = expiration
                    contracts.extend(selected)
                contracts.sort(key=lambda row: (row["distance_pct"], row["expiration"], row["symbol"]))
                contracts = contracts[: max_options_per_underlying]
                by_underlying[underlying] = contracts
                details.append(
                    {
                        "underlying": underlying,
                        "spot": spot,
                        "expirations": expirations,
                        "contracts": contracts,
                    }
                )
            except (urllib.error.HTTPError, urllib.error.URLError, RuntimeError, TimeoutError, OSError) as exc:
                errors.append({"underlying": underlying, "error": str(exc)})

    # Keep the underlying tape for every option family being validated. Older
    # wrappers passed only SPY/QQQ/IWM through --symbols; taking the union here
    # makes the corrected engine complete even before that root-owned wrapper is
    # replaced.
    base_symbols = list(dict.fromkeys([*underlyings, *option_underlyings]))
    option_slots = max(0, max_stream_symbols - len(base_symbols))
    option_contracts = _round_robin_contracts(
        by_underlying,
        option_underlyings,
        option_slots,
    )
    option_symbols = [row["symbol"] for row in option_contracts]
    stream_symbols = [*base_symbols, *option_symbols]
    return {
        "resolved_at": utcnow(),
        "underlyings": base_symbols,
        "option_underlyings": option_underlyings,
        "spots": spots,
        "stream_symbols": stream_symbols,
        "resolved_symbol_count": len(stream_symbols),
        "option_contract_count": len(option_symbols),
        "option_symbols": option_symbols,
        "selection_details": details,
        "errors": errors,
        "parameters": {
            "expiration_count": expiration_count,
            "strikes_per_side": strikes_per_side,
            "min_dte": min_dte,
            "max_dte": max_dte,
            "max_options_per_underlying": max_options_per_underlying,
            "max_stream_symbols": max_stream_symbols,
        },
    }


def create_market_session(token: str, env: str) -> str:
    payload = _request_json(
        token=token,
        env=env,
        path="/v1/markets/events/session",
        method="POST",
    )

    def find_sessionid(value: Any) -> str | None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key.lower() in {"sessionid", "session_id"} and item:
                    return str(item)
                found = find_sessionid(item)
                if found:
                    return found
        if isinstance(value, list):
            for item in value:
                found = find_sessionid(item)
                if found:
                    return found
        return None

    sessionid = find_sessionid(payload)
    if not sessionid:
        raise RuntimeError(f"Tradier session response did not include a session id: {payload}")
    return sessionid


def _decode_stream_message(raw: str | bytes) -> list[dict[str, Any]]:
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
    events: list[dict[str, Any]] = []
    for line in text.splitlines() or [text]:
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            events.append({"type": "parse_error", "raw": line})
            continue
        if isinstance(payload, dict):
            if payload.get("error"):
                raise RuntimeError(f"Tradier WebSocket subscription rejected: {payload['error']}")
            events.append(payload)
        elif isinstance(payload, list):
            events.extend(item for item in payload if isinstance(item, dict))
        else:
            events.append({"type": "parse_error", "raw": line})
    return events


def stream_events_http(
    *,
    token: str,
    sessionid: str,
    symbols: list[str],
    filters: list[str],
    duration_seconds: int,
    db_path: Path,
    raw_path: Path,
    run_id: int,
) -> tuple[int, str]:
    params = {
        "sessionid": sessionid,
        "symbols": ",".join(symbols),
        "filter": ",".join(filters),
        "linebreak": "true",
        "validOnly": "true",
    }
    url = f"{PRODUCTION_STREAM}/v1/markets/events?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
        method="GET",
    )
    deadline = time.monotonic() + max(1, duration_seconds)
    event_count = 0
    batch: list[dict[str, Any]] = []
    stop_reason = "duration_elapsed"
    socket.setdefaulttimeout(5)
    try:
        response = urllib.request.urlopen(req, timeout=15)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(_http_error_detail(exc, "Tradier HTTP market stream")) from exc
    with response as resp:
        while time.monotonic() < deadline:
            if STOP_REQUESTED:
                stop_reason = "signal_requested"
                break
            try:
                raw = resp.readline()
            except socket.timeout:
                continue
            if not raw:
                stop_reason = "stream_eof"
                break
            events = _decode_stream_message(raw)
            batch.extend(events)
            event_count += len(events)
            if len(batch) >= 100:
                store_events(db_path, raw_path, run_id, batch)
                batch = []
    if batch:
        store_events(db_path, raw_path, run_id, batch)
    return event_count, stop_reason


def stream_events_websocket(
    *,
    sessionid: str,
    symbols: list[str],
    filters: list[str],
    duration_seconds: int,
    db_path: Path,
    raw_path: Path,
    run_id: int,
) -> tuple[int, str]:
    try:
        from websockets.exceptions import ConnectionClosed, InvalidHandshake
        from websockets.sync.client import connect
    except ImportError as exc:
        raise RuntimeError(
            "Tradier WebSocket transport requires websockets==16.1.1"
        ) from exc

    subscription = {
        "sessionid": sessionid,
        "symbols": symbols,
        "filter": filters,
        "linebreak": True,
        "validOnly": True,
    }
    deadline = time.monotonic() + max(1, duration_seconds)
    event_count = 0
    batch: list[dict[str, Any]] = []
    stop_reason = "duration_elapsed"

    try:
        with connect(
            "wss://ws.tradier.com/v1/markets/events",
            compression=None,
            proxy=None,
            open_timeout=15,
            ping_interval=20,
            ping_timeout=20,
            close_timeout=5,
            max_size=4 * 1024 * 1024,
            max_queue=256,
        ) as websocket:
            websocket.send(json.dumps(subscription, separators=(",", ":")))
            while time.monotonic() < deadline:
                if STOP_REQUESTED:
                    stop_reason = "signal_requested"
                    break
                remaining = max(0.1, deadline - time.monotonic())
                try:
                    raw = websocket.recv(timeout=min(5.0, remaining))
                except TimeoutError:
                    continue
                except ConnectionClosed as exc:
                    if event_count == 0:
                        raise RuntimeError(
                            f"Tradier WebSocket closed before any events: {exc}"
                        ) from exc
                    stop_reason = "stream_closed"
                    break
                events = _decode_stream_message(raw)
                batch.extend(events)
                event_count += len(events)
                if len(batch) >= 100:
                    store_events(db_path, raw_path, run_id, batch)
                    batch = []
    except InvalidHandshake as exc:
        raise RuntimeError(f"Tradier WebSocket handshake failed: {exc}") from exc

    if batch:
        store_events(db_path, raw_path, run_id, batch)
    return event_count, stop_reason


def stream_events(
    *,
    transport: str,
    token: str,
    sessionid: str,
    symbols: list[str],
    filters: list[str],
    duration_seconds: int,
    db_path: Path,
    raw_path: Path,
    run_id: int,
) -> tuple[int, str]:
    if transport == "websocket":
        return stream_events_websocket(
            sessionid=sessionid,
            symbols=symbols,
            filters=filters,
            duration_seconds=duration_seconds,
            db_path=db_path,
            raw_path=raw_path,
            run_id=run_id,
        )
    return stream_events_http(
        token=token,
        sessionid=sessionid,
        symbols=symbols,
        filters=filters,
        duration_seconds=duration_seconds,
        db_path=db_path,
        raw_path=raw_path,
        run_id=run_id,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default=os.environ.get("TRADIER_STREAM_SYMBOLS", DEFAULT_UNDERLYINGS))
    parser.add_argument(
        "--option-underlyings",
        default=os.environ.get("TRADIER_OPTION_UNDERLYINGS", DEFAULT_UNDERLYINGS),
    )
    parser.add_argument("--no-options", action="store_true")
    parser.add_argument("--option-expirations", type=int, default=int(os.environ.get("TRADIER_OPTION_EXPIRATIONS", "1")))
    parser.add_argument("--option-strikes-per-side", type=int, default=int(os.environ.get("TRADIER_OPTION_STRIKES_PER_SIDE", "2")))
    parser.add_argument("--option-min-dte", type=int, default=int(os.environ.get("TRADIER_OPTION_MIN_DTE", "0")))
    parser.add_argument("--option-max-dte", type=int, default=int(os.environ.get("TRADIER_OPTION_MAX_DTE", "14")))
    parser.add_argument("--max-options-per-underlying", type=int, default=int(os.environ.get("TRADIER_MAX_OPTIONS_PER_UNDERLYING", "8")))
    parser.add_argument("--max-stream-symbols", type=int, default=int(os.environ.get("TRADIER_MAX_STREAM_SYMBOLS", "160")))
    parser.add_argument("--filters", default=os.environ.get("TRADIER_STREAM_FILTERS", ",".join(DEFAULT_FILTERS)))
    parser.add_argument(
        "--transport",
        choices=("websocket", "http"),
        default=os.environ.get("TRADIER_STREAM_TRANSPORT", "websocket"),
    )
    parser.add_argument("--duration-seconds", type=int, default=840)
    parser.add_argument("--env", choices=("production", "sandbox"), default=os.environ.get("TRADIER_ENV", "production"))
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--selection-output", type=Path, default=DEFAULT_SELECTION_PATH)
    parser.add_argument("--lock-path", type=Path, default=DEFAULT_LOCK_PATH)
    parser.add_argument("--position-file", action="append", type=Path, default=[])
    parser.add_argument("--stale-run-seconds", type=int, default=1800)
    parser.add_argument("--resolve-only", action="store_true")
    parser.add_argument("--maintenance-only", action="store_true")
    parser.add_argument(
        "--allow-outside-session",
        action="store_true",
        help="Allow an explicit smoke/research capture outside 09:30-16:00 ET.",
    )
    parser.add_argument("--no-lock", action="store_true")
    args = parser.parse_args()

    if args.env == "sandbox":
        raise SystemExit("Tradier sandbox market-data streaming is unavailable; use --env production.")
    if args.option_expirations < 1 or args.option_strikes_per_side < 1:
        raise SystemExit("Option expiration and strike counts must be positive.")
    if args.option_min_dte < 0 or args.option_max_dte < args.option_min_dte:
        raise SystemExit("Invalid option DTE bounds.")
    if args.max_options_per_underlying < 2 or args.max_stream_symbols < 1:
        raise SystemExit("Option and stream symbol caps are too small.")

    install_signal_handlers()
    if args.maintenance_only:
        ensure_schema(args.db)
        reconciled_counts = reconcile_run_counts(args.db)
        reconciled_stale = reconcile_stale_runs(args.db, args.stale_run_seconds)
        print(
            json.dumps(
                {
                    "status": "ok",
                    "db": str(args.db),
                    "reconciled_run_counts": reconciled_counts,
                    "reconciled_stale_runs": reconciled_stale,
                    "read_only_market_data": True,
                },
                indent=2,
            )
        )
        return 0

    if not args.resolve_only and not args.allow_outside_session and not regular_session_open():
        # The legacy installed shell wrapper may invoke this process outside the
        # regular session. Exit silently so it cannot create after-hours runs or
        # flood the journal while the root-owned wrapper awaits replacement.
        return 0

    underlyings = parse_symbols(args.symbols, args.position_file)
    option_underlyings = parse_symbols(args.option_underlyings)
    filters = [part.strip() for part in args.filters.split(",") if part.strip()]
    if not underlyings:
        raise SystemExit("At least one underlying symbol is required.")
    if not filters:
        raise SystemExit("At least one stream filter is required.")

    token, env = load_credentials(args.env)
    selection = resolve_stream_universe(
        token=token,
        env=env,
        underlyings=underlyings,
        option_underlyings=option_underlyings,
        include_options=not args.no_options,
        expiration_count=args.option_expirations,
        strikes_per_side=args.option_strikes_per_side,
        min_dte=args.option_min_dte,
        max_dte=args.option_max_dte,
        max_options_per_underlying=args.max_options_per_underlying,
        max_stream_symbols=args.max_stream_symbols,
    )
    if args.resolve_only:
        print(json.dumps(selection, indent=2))
        return 0 if selection.get("stream_symbols") else 1

    lock_handle = None
    if not args.no_lock:
        lock_handle = acquire_lock(args.lock_path)
        if lock_handle is None:
            print(json.dumps({"skipped": True, "reason": f"Another Tradier stream capture holds {args.lock_path}"}))
            return 0

    run_id: int | None = None
    error: str | None = None
    stop_reason = "not_started"
    stored_count = 0
    raw_path: Path | None = None
    try:
        ensure_schema(args.db)
        reconciled_counts = reconcile_run_counts(args.db)
        reconciled = reconcile_stale_runs(args.db, args.stale_run_seconds)
        args.selection_output.parent.mkdir(parents=True, exist_ok=True)
        args.selection_output.write_text(json.dumps(selection, indent=2), encoding="utf-8")
        stream_symbols = list(selection.get("stream_symbols") or [])
        if not stream_symbols:
            raise RuntimeError("Tradier option resolution produced no stream symbols")
        run_id = create_run(
            args.db,
            env=env,
            requested_underlyings=underlyings,
            symbols=stream_symbols,
            filters=filters,
            selection=selection,
        )
        raw_path = args.raw_dir / datetime.now(timezone.utc).strftime("%Y-%m-%d") / f"{stamp()}_run_{run_id}.jsonl"
        sessionid = create_market_session(token, env)
        capture_duration = args.duration_seconds
        if not args.allow_outside_session:
            capture_duration = min(capture_duration, max(1, seconds_until_regular_close()))
        _observed_count, stop_reason = stream_events(
            transport=args.transport,
            token=token,
            sessionid=sessionid,
            symbols=stream_symbols,
            filters=filters,
            duration_seconds=capture_duration,
            db_path=args.db,
            raw_path=raw_path,
            run_id=run_id,
        )
        stored_count = finish_run(args.db, run_id, stop_reason=stop_reason)
    except (urllib.error.HTTPError, urllib.error.URLError, RuntimeError, TimeoutError, OSError, sqlite3.Error) as exc:
        error = str(exc)
        stop_reason = "error"
        if run_id is not None:
            stored_count = finish_run(args.db, run_id, error=error, stop_reason=stop_reason)
        reconciled = 0
        reconciled_counts = 0
    finally:
        if lock_handle is not None:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
            lock_handle.close()

    governance_registration = None
    if raw_path and raw_path.exists():
        try:
            from research_platform.ingestion_hooks import hooks_enabled, register_ingestion_file

            if hooks_enabled():
                governance_registration = register_ingestion_file(
                    raw_path,
                    source="tradier_production",
                    dataset="tradier_stream_raw_events",
                    ingestion_run_id=f"tradier_run_{run_id}",
                    metadata={
                        "run_id": run_id,
                        "event_count": stored_count,
                        "stop_reason": stop_reason,
                        "error": error,
                        "transport": args.transport,
                        "resolved_symbol_count": selection.get("resolved_symbol_count"),
                    },
                )
        except Exception as exc:
            governance_registration = {
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
            }

    summary = {
        "run_id": run_id,
        "env": args.env,
        "requested_underlyings": underlyings,
        "resolved_symbol_count": selection.get("resolved_symbol_count"),
        "option_contract_count": selection.get("option_contract_count"),
        "sample_option_symbols": (selection.get("option_symbols") or [])[:12],
        "filters": filters,
        "transport": args.transport,
        "requested_duration_seconds": args.duration_seconds,
        "event_count": stored_count,
        "stop_reason": stop_reason,
        "reconciled_run_counts": reconciled_counts,
        "reconciled_stale_runs": reconciled,
        "db": str(args.db),
        "raw_path": str(raw_path) if raw_path else None,
        "selection_output": str(args.selection_output),
        "resolution_errors": selection.get("errors") or [],
        "error": error,
        "governance_registration": governance_registration,
        "read_only": True,
    }
    print(json.dumps(summary, indent=2))
    if error and "HTTP 403" in error:
        # The installed wrapper retries every five seconds. A bounded pause
        # prevents a provider-side denial from turning into an authorization
        # storm while still allowing automatic recovery during the session.
        time.sleep(max(5, int(os.environ.get("TRADIER_403_BACKOFF_SECONDS", "60"))))
    return 1 if error else 0


if __name__ == "__main__":
    raise SystemExit(main())
