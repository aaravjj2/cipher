from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import asdict, is_dataclass
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterator
from zoneinfo import ZoneInfo

SCHEMA_VERSION = 2


def _json_value(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Unsupported ledger JSON value: {type(value).__name__}")


def _session_bounds(now: datetime | str) -> tuple[str, str, str]:
    instant = datetime.fromisoformat(now.replace("Z", "+00:00")) if isinstance(now, str) else now
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    zone = ZoneInfo("America/New_York")
    day = instant.astimezone(zone).date()
    start = datetime.combine(day, time.min, zone)
    end = datetime.combine(day + timedelta(days=1), time.min, zone)
    return day.isoformat(), start.astimezone(timezone.utc).isoformat(), end.astimezone(timezone.utc).isoformat()


class PaperExecutorDatabase:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute("pragma journal_mode=WAL")
        db.execute("pragma foreign_keys=ON")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def migrate(self) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute("pragma journal_mode=WAL")
            db.execute("pragma foreign_keys=ON")
            db.executescript(
                """
                create table if not exists schema_migrations (
                    version integer primary key,
                    applied_at text not null default current_timestamp
                );
                create table if not exists signal_batches (
                    id text primary key,
                    source text not null,
                    received_at text not null,
                    status text not null,
                    checksum text not null,
                    raw_json text not null
                );
                create table if not exists signal_cards (
                    id text primary key,
                    batch_id text not null references signal_batches(id),
                    ticker text,
                    scanner_type text,
                    direction text,
                    setup text,
                    captured_at text,
                    status text not null,
                    skip_reason text,
                    raw_json text not null,
                    normalized_json text
                );
                create table if not exists signal_episodes (
                    id text primary key,
                    episode_key text not null,
                    scanner_type text not null,
                    ticker text not null,
                    direction text not null,
                    setup text not null,
                    started_at text not null,
                    last_seen_at text not null,
                    ended_at text,
                    poll_count integer not null default 1,
                    latest_json text not null
                );
                create index if not exists idx_signal_episodes_key on signal_episodes(episode_key, ended_at);
                create table if not exists episode_updates (
                    id text primary key,
                    episode_id text not null references signal_episodes(id),
                    card_id text not null references signal_cards(id),
                    seen_at text not null,
                    spot real,
                    target real,
                    invalidation real,
                    payload_json text not null
                );
                create table if not exists contract_candidates (
                    id text primary key,
                    episode_id text references signal_episodes(id),
                    symbol text not null,
                    strike real,
                    expiration text,
                    option_type text,
                    dte integer,
                    bid real,
                    ask real,
                    midpoint real,
                    spread_dollars real,
                    spread_pct real,
                    volume integer,
                    open_interest integer,
                    quote_timestamp text,
                    rejection_reasons text not null,
                    ranking_score real not null,
                    decision_json text not null
                );
                create table if not exists paper_orders (
                    id text primary key,
                    episode_id text,
                    position_id text,
                    side text not null,
                    symbol text not null,
                    quantity integer not null,
                    status text not null,
                    fill_json text not null,
                    created_at text not null
                );
                create table if not exists paper_positions (
                    id text primary key,
                    episode_id text,
                    ticker text not null,
                    direction text not null,
                    symbol text not null,
                    quantity integer not null,
                    entry_price real not null,
                    opened_at text not null,
                    closed_at text,
                    exit_price real,
                    exit_reason text,
                    status text not null,
                    payload_json text not null
                );
                create table if not exists paper_marks (
                    id text primary key,
                    position_id text not null references paper_positions(id),
                    marked_at text not null,
                    bid real,
                    ask real,
                    pnl_pct real,
                    payload_json text not null
                );
                create table if not exists contract_mark_tape (
                    id text primary key,
                    position_id text not null references paper_positions(id),
                    episode_id text,
                    symbol text not null,
                    role text not null,
                    observed_at text not null,
                    captured_at text not null,
                    source text not null,
                    bid real,
                    ask real,
                    bid_size integer,
                    ask_size integer,
                    last real,
                    volume integer,
                    open_interest integer,
                    quote_age_seconds real,
                    payload_json text not null
                );
                create index if not exists idx_contract_tape_position on contract_mark_tape(position_id,symbol,observed_at);
                create table if not exists paper_events (
                    id text primary key,
                    event_time text not null,
                    event_type text not null,
                    payload_json text not null
                );
                create table if not exists daily_account_state (
                    trade_date text primary key,
                    new_positions integer not null default 0,
                    stopped_trades integer not null default 0,
                    payload_json text not null
                );
                create table if not exists system_events (
                    id text primary key,
                    event_time text not null,
                    event_type text not null,
                    payload_json text not null
                );
                create table if not exists forward_queue (
                    id text primary key,
                    batch_id text not null references signal_batches(id),
                    status text not null,
                    attempts integer not null default 0,
                    next_attempt_at text,
                    endpoint text,
                    payload_json text not null,
                    last_error text
                );
                """
            )
            db.execute("insert or ignore into schema_migrations(version) values (?)", (SCHEMA_VERSION,))

    @staticmethod
    def now_text() -> str:
        return datetime.now(timezone.utc).isoformat()

    def insert_batch(self, batch: dict[str, Any], checksum: str) -> bool:
        with self.connect() as db:
            try:
                db.execute(
                    "insert into signal_batches(id, source, received_at, status, checksum, raw_json) values (?, ?, ?, ?, ?, ?)",
                    (batch["batch_id"], batch["source"], batch["received_at"], "RECEIVED", checksum, json.dumps(batch["raw"], default=_json_value)),
                )
            except sqlite3.IntegrityError:
                return False
        return True

    def batch(self, batch_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("select * from signal_batches where id = ?", (batch_id,)).fetchone()
            return dict(row) if row else None

    def update_batch_status(self, batch_id: str, status: str) -> None:
        with self.connect() as db:
            db.execute("update signal_batches set status = ? where id = ?", (status, batch_id))

    def insert_card(self, card_id: str, batch_id: str, raw: dict[str, Any], status: str, skip_reason: str | None, normalized: dict[str, Any] | None = None) -> None:
        with self.connect() as db:
            db.execute(
                """
                insert or replace into signal_cards(id, batch_id, ticker, scanner_type, direction, setup, captured_at, status, skip_reason, raw_json, normalized_json)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    card_id, batch_id, (normalized or raw).get("ticker"), (normalized or raw).get("scanner_type"),
                    (normalized or raw).get("direction"), (normalized or raw).get("setup"), (normalized or raw).get("captured_at"),
                    status, skip_reason, json.dumps(raw, default=_json_value), json.dumps(normalized, default=_json_value) if normalized else None,
                ),
            )

    def rows(self, table: str) -> list[dict[str, Any]]:
        if table not in {"signal_batches", "signal_cards", "signal_episodes", "contract_candidates", "paper_orders", "paper_positions", "paper_marks", "paper_events", "system_events", "forward_queue"}:
            raise ValueError("unsupported table")
        with self.connect() as db:
            return [dict(row) for row in db.execute(f"select * from {table}").fetchall()]

    def operational_snapshot(self) -> dict[str, Any]:
        with self.connect() as db:
            counts = {
                "signal_batches": db.execute("select count(*) from signal_batches").fetchone()[0],
                "signal_cards": db.execute("select count(*) from signal_cards").fetchone()[0],
                "signal_episodes": db.execute("select count(*) from signal_episodes").fetchone()[0],
                "contract_candidates": db.execute("select count(*) from contract_candidates").fetchone()[0],
                "paper_orders": db.execute("select count(*) from paper_orders").fetchone()[0],
                "open_shadow_positions": db.execute("select count(*) from paper_positions where status = 'SHADOW_OPEN'").fetchone()[0],
                "open_paper_positions": db.execute("select count(*) from paper_positions where status = 'OPEN'").fetchone()[0],
                "closed_positions": db.execute("select count(*) from paper_positions where status = 'CLOSED'").fetchone()[0],
                "entry_blocks": db.execute("select count(*) from system_events where event_type = 'ENTRY_BLOCKED'").fetchone()[0],
                "forward_backlog": db.execute("select count(*) from forward_queue where status in ('PENDING', 'FAILED_RETRYABLE')").fetchone()[0],
            }
            latest_batch = db.execute("select received_at from signal_batches order by received_at desc limit 1").fetchone()
            latest_episode = db.execute("select last_seen_at from signal_episodes order by last_seen_at desc limit 1").fetchone()
            latest_mark = db.execute("select marked_at from paper_marks order by marked_at desc limit 1").fetchone()
            latest_worker_error = db.execute(
                "select event_time, payload_json from system_events where event_type = 'WORKER_ERROR' order by event_time desc limit 1"
            ).fetchone()
            latest_entry_block = db.execute(
                "select event_time, payload_json from system_events where event_type = 'ENTRY_BLOCKED' order by event_time desc limit 1"
            ).fetchone()
            return {
                "counts": counts,
                "last_batch_at": latest_batch["received_at"] if latest_batch else None,
                "last_episode_at": latest_episode["last_seen_at"] if latest_episode else None,
                "last_mark_at": latest_mark["marked_at"] if latest_mark else None,
                "last_worker_exception": self._event(latest_worker_error),
                "last_entry_block": self._event(latest_entry_block),
            }

    def session_snapshot(self, market_date: str) -> dict[str, int]:
        """Lifecycle counts for one market date; never mix them with lifetime totals."""
        _, start, end = _session_bounds(datetime.combine(date.fromisoformat(market_date), time(12), ZoneInfo("America/New_York")))
        with self.connect() as db:
            def scalar(sql: str, timestamp: str) -> int:
                sql += f" and julianday({timestamp})>=julianday(?) and julianday({timestamp})<julianday(?)"
                return int(db.execute(sql, (start, end)).fetchone()[0] or 0)
            return {
                "batches_received": scalar("select count(*) from signal_batches where 1", "received_at"),
                "cards_submitted": scalar(
                    "select count(*) from signal_cards c join signal_batches b on b.id=c.batch_id where 1", "b.received_at"
                ),
                "cards_admitted": scalar(
                    "select count(*) from signal_cards c join signal_batches b on b.id=c.batch_id where c.status='ELIGIBLE'", "b.received_at"
                ),
                "contracts_evaluated": scalar(
                    "select count(*) from contract_candidates cc join signal_episodes e on e.id=cc.episode_id where 1", "e.started_at"
                ),
                "positions_opened": scalar("select count(*) from paper_positions where 1", "opened_at"),
                "positions_closed": scalar("select count(*) from paper_positions where 1", "closed_at"),
                "orders_filled": scalar(
                    "select count(*) from paper_orders where status in ('FILLED','SIMULATED_FILLED')", "created_at"
                ),
                "entry_blocks": scalar(
                    "select count(*) from system_events where event_type='ENTRY_BLOCKED'", "event_time"
                ),
            }

    def portfolio_snapshot(self, starting_cash: float) -> dict[str, Any]:
        """Derive the self-managed long-option paper account from its ledger."""
        with self.connect() as db:
            closed = db.execute(
                """select count(*) trades,
                          coalesce(sum((exit_price-entry_price)*quantity*100),0) pnl,
                          coalesce(sum(case when exit_price>entry_price then 1 else 0 end),0) wins
                     from paper_positions where status='CLOSED'"""
            ).fetchone()
            opened = db.execute(
                """select p.entry_price,p.quantity,
                          (select m.bid from paper_marks m where m.position_id=p.id order by m.marked_at desc limit 1) bid,
                          (select m.ask from paper_marks m where m.position_id=p.id order by m.marked_at desc limit 1) ask
                     from paper_positions p where p.status in ('OPEN','SHADOW_OPEN')"""
            ).fetchall()
        realized = float(closed["pnl"] or 0)
        cost = sum(float(row["entry_price"]) * int(row["quantity"]) * 100 for row in opened)
        midpoint_pnl = sum(
            (((float(row["bid"]) + float(row["ask"])) / 2) - float(row["entry_price"]))
            * int(row["quantity"]) * 100
            for row in opened if row["bid"] is not None and row["ask"] is not None
        )
        liquidation_pnl = sum(
            (float(row["bid"]) - float(row["entry_price"])) * int(row["quantity"]) * 100
            for row in opened if row["bid"] is not None
        )
        return {
            "kind": "cipher_local_paper", "external_order_capability": False,
            "starting_cash": round(starting_cash, 2),
            "cash_balance": round(starting_cash + realized - cost, 2),
            "realized_pnl": round(realized, 2), "open_positions": len(opened),
            "closed_trades": int(closed["trades"]), "wins": int(closed["wins"]),
            "marked_equity": round(starting_cash + realized + midpoint_pnl, 2),
            "liquidation_equity": round(starting_cash + realized + liquidation_pnl, 2),
        }

    @staticmethod
    def _event(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if not row:
            return None
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, json.JSONDecodeError):
            payload = {"reason": "unreadable event payload"}
        return {"event_time": row["event_time"], **payload}

    def due_forward_items(self, now_text: str) -> list[str]:
        with self.connect() as db:
            rows = db.execute(
                """
                select id from forward_queue
                where status in ('PENDING', 'FAILED_RETRYABLE')
                  and (next_attempt_at is null or next_attempt_at <= ?)
                order by attempts asc
                """,
                (now_text,),
            ).fetchall()
            return [str(row["id"]) for row in rows]

    def insert_system_event(self, event_type: str, payload: dict[str, Any]) -> str:
        from .models import sha256_id

        event_id = sha256_id("system_event", {"type": event_type, "time": self.now_text(), "payload": payload})
        with self.connect() as db:
            db.execute(
                "insert into system_events(id, event_time, event_type, payload_json) values (?, ?, ?, ?)",
                (event_id, self.now_text(), event_type, json.dumps(payload, default=_json_value)),
            )
        return event_id

    def end_episode(self, episode_id: str) -> None:
        with self.connect() as db:
            db.execute(
                "update signal_episodes set ended_at = coalesce(ended_at, ?) where id = ?",
                (self.now_text(), episode_id),
            )

    def end_orphaned_episodes(self) -> int:
        """End episodes that have no open position after recovery."""
        with self.connect() as db:
            cursor = db.execute(
                """update signal_episodes set ended_at = coalesce(ended_at, ?)
                   where ended_at is null and id not in (
                       select episode_id from paper_positions
                       where status in ('OPEN','SHADOW_OPEN') and episode_id is not null
                   )""",
                (self.now_text(),),
            )
            return int(cursor.rowcount)

    def persist_candidates(self, episode_id: str, candidates: list[Any]) -> None:
        from .models import sha256_id

        with self.connect() as db:
            for candidate in candidates:
                quote = candidate.quote
                contract = candidate.contract
                payload = {
                    "symbol": contract.symbol,
                    "strike": contract.strike,
                    "expiration": contract.expiration,
                    "type": contract.option_type.value,
                    "dte": candidate.dte,
                    "quote": quote,
                    "rejection_reasons": candidate.rejection_reasons,
                    "ranking_score": candidate.ranking_score,
                }
                cid = sha256_id("candidate", {"episode_id": episode_id, "symbol": contract.symbol, "score": candidate.ranking_score})
                db.execute(
                    """
                    insert or replace into contract_candidates(
                        id, episode_id, symbol, strike, expiration, option_type, dte,
                        bid, ask, midpoint, spread_dollars, spread_pct, volume, open_interest,
                        quote_timestamp, rejection_reasons, ranking_score, decision_json
                    )
                    values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        cid, episode_id, contract.symbol, contract.strike, contract.expiration, contract.option_type.value,
                        candidate.dte, quote.bid if quote else None, quote.ask if quote else None,
                        quote.midpoint if quote else None, quote.spread if quote else None,
                        quote.spread_pct if quote else None, quote.volume if quote else None,
                        quote.open_interest if quote else None, quote.timestamp.isoformat() if quote else None,
                        json.dumps(list(candidate.rejection_reasons)), candidate.ranking_score,
                        json.dumps(payload, default=_json_value),
                    ),
                )

    def open_positions(self, include_shadow: bool = True) -> list[dict[str, Any]]:
        statuses = ("OPEN", "SHADOW_OPEN") if include_shadow else ("OPEN",)
        placeholders = ",".join("?" for _ in statuses)
        with self.connect() as db:
            return [dict(row) for row in db.execute(f"select * from paper_positions where status in ({placeholders})", statuses).fetchall()]

    @staticmethod
    def _session_counts(db: sqlite3.Connection, now: datetime | str, ticker: str | None = None) -> dict[str, int]:
        _, start, end = _session_bounds(now)
        row = db.execute(
            """select
                coalesce(sum(julianday(opened_at)>=julianday(?) and julianday(opened_at)<julianday(?)),0) new_positions,
                coalesce(sum(status='CLOSED' and exit_price<entry_price and julianday(closed_at)>=julianday(?) and julianday(closed_at)<julianday(?)),0) stopped_trades,
                coalesce(sum(ticker=? and julianday(opened_at)>=julianday(?) and julianday(opened_at)<julianday(?)),0) ticker_entries
               from paper_positions""", (start, end, start, end, ticker, start, end),
        ).fetchone()
        return {key: int(row[key]) for key in ("new_positions", "stopped_trades", "ticker_entries")}

    def session_counts(self, now: datetime | str, ticker: str | None = None) -> dict[str, int]:
        with self.connect() as db:
            return self._session_counts(db, now, ticker)

    @classmethod
    def _sync_daily_state(cls, db: sqlite3.Connection, now: datetime | str) -> None:
        today, _, _ = _session_bounds(now)
        counts = cls._session_counts(db, now)
        payload = {"trade_date": today, **counts}
        db.execute(
            """insert into daily_account_state(trade_date,new_positions,stopped_trades,payload_json)
               values(?,?,?,?) on conflict(trade_date) do update set
               new_positions=excluded.new_positions, stopped_trades=excluded.stopped_trades,
               payload_json=excluded.payload_json""",
            (today, counts["new_positions"], counts["stopped_trades"], json.dumps(payload)),
        )

    def create_position_transactional(
        self,
        *,
        position_id: str,
        episode_id: str,
        ticker: str,
        direction: str,
        symbol: str,
        quantity: int,
        entry_price: float,
        status: str,
        payload: dict[str, Any],
        max_open_positions: int,
        max_positions_per_ticker: int,
        max_new_positions_per_day: int,
        max_new_positions_per_ticker_per_day: int,
        stop_after_daily_losses: int,
        starting_cash: float | None = None,
        entry_order: dict[str, Any] | None = None,
    ) -> tuple[bool, str | None]:
        opened_at = str(payload.get("opened_at") or self.now_text())
        _session_bounds(opened_at)
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0 or not math.isfinite(entry_price) or entry_price <= 0:
            raise ValueError("Position requires positive integer quantity and finite positive entry price")
        if starting_cash is not None and (not math.isfinite(starting_cash) or starting_cash < 0):
            raise ValueError("starting_cash must be finite and nonnegative")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing_episode = db.execute(
                "select id from paper_positions where episode_id = ? and status in ('OPEN','SHADOW_OPEN','CLOSED')",
                (episode_id,),
            ).fetchone()
            if existing_episode:
                return False, "SKIPPED_DUPLICATE"
            open_count = db.execute("select count(*) from paper_positions where status in ('OPEN','SHADOW_OPEN')").fetchone()[0]
            if open_count >= max_open_positions:
                return False, "SKIPPED_MAX_POSITIONS"
            ticker_count = db.execute(
                "select count(*) from paper_positions where ticker = ? and status in ('OPEN','SHADOW_OPEN')",
                (ticker,),
            ).fetchone()[0]
            if ticker_count >= max_positions_per_ticker:
                return False, "SKIPPED_POSITION_EXISTS"
            counts = self._session_counts(db, opened_at, ticker)
            new_positions, stopped = counts["new_positions"], counts["stopped_trades"]
            if new_positions >= max_new_positions_per_day:
                return False, "SKIPPED_DAILY_LIMIT"
            ticker_entries = counts["ticker_entries"]
            if ticker_entries >= max_new_positions_per_ticker_per_day:
                return False, "SKIPPED_TICKER_DAILY_LIMIT"
            if stopped >= stop_after_daily_losses:
                return False, "SKIPPED_DAILY_STOP_LIMIT"
            if starting_cash is not None:
                cash_delta = db.execute("""select coalesce(sum(case
                    when status='CLOSED' then (exit_price-entry_price)*quantity*100
                    when status in ('OPEN','SHADOW_OPEN') then -entry_price*quantity*100
                    else 0 end),0) from paper_positions""").fetchone()[0]
                if entry_price * quantity * 100 > starting_cash + float(cash_delta) + 1e-8:
                    return False, "SKIPPED_INSUFFICIENT_CASH"
            now = opened_at
            db.execute(
                """
                insert into paper_positions(id, episode_id, ticker, direction, symbol, quantity, entry_price, opened_at, status, payload_json)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (position_id, episode_id, ticker, direction, symbol, quantity, entry_price, now, status, json.dumps(payload, default=_json_value)),
            )
            if entry_order is not None:
                if entry_order.get("position_id") != position_id:
                    raise ValueError("Entry order position mismatch")
                self._insert_order(db, **entry_order)
            self._sync_daily_state(db, opened_at)
        return True, None

    @staticmethod
    def _insert_order(db: sqlite3.Connection, *, order_id: str, episode_id: str | None,
                      position_id: str, side: str, symbol: str, quantity: int, status: str,
                      fill: Any, created_at: str) -> bool:
        cursor = db.execute(
            """insert into paper_orders(id,episode_id,position_id,side,symbol,quantity,status,fill_json,created_at)
               values(?,?,?,?,?,?,?,?,?)""",
            (order_id, episode_id, position_id, side, symbol, quantity, status,
             json.dumps(fill, default=_json_value), created_at),
        )
        return cursor.rowcount == 1

    def insert_order(
        self, *, order_id: str, episode_id: str | None, position_id: str,
        side: str, symbol: str, quantity: int, status: str,
        fill: dict[str, Any], created_at: str,
    ) -> bool:
        """Persist a deterministic simulated order; duplicate recovery is a no-op."""
        with self.connect() as db:
            cursor = db.execute(
                """insert or ignore into paper_orders(
                       id,episode_id,position_id,side,symbol,quantity,status,fill_json,created_at)
                   values(?,?,?,?,?,?,?,?,?)""",
                (order_id, episode_id, position_id, side, symbol, quantity, status,
                 json.dumps(fill, default=_json_value), created_at),
            )
            return cursor.rowcount == 1

    def update_order(self, order_id: str, status: str, payload: dict[str, Any]) -> bool:
        """Replace the mutable broker snapshot while retaining the stable local ID."""
        with self.connect() as db:
            cursor = db.execute(
                "update paper_orders set status = ?, fill_json = ? where id = ?",
                (status, json.dumps(payload, default=_json_value), order_id),
            )
            return cursor.rowcount == 1

    def order(self, order_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("select * from paper_orders where id = ?", (order_id,)).fetchone()
            return dict(row) if row else None

    def insert_mark(self, position_id: str, payload: dict[str, Any]) -> str:
        from .models import sha256_id

        mark_id = sha256_id("mark", {"position_id": position_id, "time": payload.get("marked_at")})
        with self.connect() as db:
            db.execute(
                """
                insert or ignore into paper_marks(id, position_id, marked_at, bid, ask, pnl_pct, payload_json)
                values (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    mark_id, position_id, payload["marked_at"], payload.get("bid"), payload.get("ask"),
                    payload.get("pnl_pct"), json.dumps(payload, default=_json_value),
                ),
            )
        return mark_id

    def insert_contract_mark(self, *, position_id: str, episode_id: str | None, symbol: str,
                             role: str, quote: Any, captured_at: datetime,
                             source: str) -> str:
        """Persist an observed quote; never interpolate or synthesize absent fields."""
        from .models import sha256_id

        observed = quote.timestamp.astimezone(timezone.utc)
        captured = captured_at.astimezone(timezone.utc)
        payload = {
            "position_id": position_id, "episode_id": episode_id, "symbol": symbol.upper(),
            "role": role, "observed_at": observed.isoformat(), "captured_at": captured.isoformat(),
            "source": source, "bid": quote.bid, "ask": quote.ask,
            "bid_size": quote.bid_size, "ask_size": quote.ask_size, "last": quote.last,
            "volume": quote.volume, "open_interest": quote.open_interest,
            "quote_age_seconds": max(0.0, (captured - observed).total_seconds()),
            "crossed_or_locked": quote.ask <= quote.bid,
        }
        mark_id = sha256_id("contract_mark", {"position": position_id, "symbol": symbol,
                                                "role": role, "observed": payload["observed_at"],
                                                "captured": payload["captured_at"]})
        with self.connect() as db:
            db.execute("""insert or ignore into contract_mark_tape(
                id,position_id,episode_id,symbol,role,observed_at,captured_at,source,bid,ask,bid_size,
                ask_size,last,volume,open_interest,quote_age_seconds,payload_json)
                values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (mark_id, position_id, episode_id, symbol.upper(), role, payload["observed_at"],
                 payload["captured_at"], source, quote.bid, quote.ask, quote.bid_size, quote.ask_size,
                 quote.last, quote.volume, quote.open_interest, payload["quote_age_seconds"],
                 json.dumps(payload, default=_json_value)))
        return mark_id

    def mark_coverage(self, position_id: str, *, expected_interval_seconds: int = 30,
                      fresh_seconds: int = 30) -> dict[str, Any]:
        with self.connect() as db:
            rows = [dict(row) for row in db.execute(
                "select * from contract_mark_tape where position_id=? order by symbol,observed_at", (position_id,)
            ).fetchall()]
        by_symbol: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            by_symbol.setdefault(row["symbol"], []).append(row)
        symbols = []
        for symbol, marks in sorted(by_symbol.items()):
            times = [datetime.fromisoformat(row["observed_at"]) for row in marks]
            gaps = [(right - left).total_seconds() for left, right in zip(times, times[1:])]
            duration = max(0.0, (times[-1] - times[0]).total_seconds()) if times else 0.0
            expected = max(1, int(duration / max(1, expected_interval_seconds)) + 1)
            symbols.append({
                "symbol": symbol, "role": marks[0]["role"], "samples": len(marks),
                "expected_samples": expected, "coverage_pct": round(min(100.0, len(marks) / expected * 100), 1),
                "first_observed_at": marks[0]["observed_at"], "last_observed_at": marks[-1]["observed_at"],
                "maximum_gap_seconds": max(gaps) if gaps else None,
                "fresh_quote_pct": round(sum(float(row["quote_age_seconds"] or 0) <= fresh_seconds for row in marks) / len(marks) * 100, 1),
                "crossed_or_locked": sum((row["ask"] is not None and row["bid"] is not None and row["ask"] <= row["bid"]) for row in marks),
            })
        usable = bool(symbols) and all(row["coverage_pct"] >= 90 and row["fresh_quote_pct"] >= 90 and
                                       (row["maximum_gap_seconds"] is None or row["maximum_gap_seconds"] <= expected_interval_seconds * 2)
                                       for row in symbols if row["role"] != "underlying")
        return {"position_id": position_id, "status": "REPLAYABLE" if usable else "INSUFFICIENT_MARK_COVERAGE",
                "expected_interval_seconds": expected_interval_seconds, "symbols": symbols,
                "mark_assumption": "Long-option liquidation uses bid; displayed midpoint is non-executable reference only.",
                "interpolation": False, "actual_fill_claim": False}

    def close_position(self, position_id: str, exit_price: float, exit_reason: str, payload: dict[str, Any],
                       exit_order: dict[str, Any] | None = None) -> bool:
        if not math.isfinite(exit_price):
            raise ValueError("Exit price must be finite")
        now = str(payload.get("closed_at") or payload.get("marked_at") or self.now_text())
        _session_bounds(now)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("select * from paper_positions where id = ?", (position_id,)).fetchone()
            if not row or row["status"] == "CLOSED":
                return False
            db.execute(
                """
                insert into paper_events(id, event_time, event_type, payload_json)
                values (?, ?, ?, ?)
                """,
                (
                    f"event_{position_id}_{exit_reason}",
                    now,
                    "EXIT_TRIGGERED",
                    json.dumps({"position_id": position_id, "exit_reason": exit_reason, **payload}, default=_json_value),
                ),
            )
            db.execute(
                """
                update paper_positions
                set status = 'CLOSED', closed_at = ?, exit_price = ?, exit_reason = ?
                where id = ? and status != 'CLOSED'
                """,
                (now, exit_price, exit_reason, position_id),
            )
            if exit_order is not None:
                if exit_order.get("position_id") != position_id:
                    raise ValueError("Exit order position mismatch")
                self._insert_order(db, **exit_order)
            self._sync_daily_state(db, now)
        return True

    def integrity_ok(self) -> bool:
        with self.connect() as db:
            row = db.execute("pragma integrity_check").fetchone()
            return bool(row and row[0] == "ok")
