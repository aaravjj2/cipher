from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA_VERSION = 2


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
                    (batch["batch_id"], batch["source"], batch["received_at"], "RECEIVED", checksum, json.dumps(batch["raw"], default=str)),
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
                    status, skip_reason, json.dumps(raw, default=str), json.dumps(normalized, default=str) if normalized else None,
                ),
            )

    def rows(self, table: str) -> list[dict[str, Any]]:
        if table not in {"signal_batches", "signal_cards", "signal_episodes", "contract_candidates", "paper_positions", "paper_marks", "paper_events", "system_events", "forward_queue"}:
            raise ValueError("unsupported table")
        with self.connect() as db:
            return [dict(row) for row in db.execute(f"select * from {table}").fetchall()]

    def operational_snapshot(self) -> dict[str, Any]:
        with self.connect() as db:
            counts = {
                "signal_batches": db.execute("select count(*) from signal_batches").fetchone()[0],
                "signal_episodes": db.execute("select count(*) from signal_episodes").fetchone()[0],
                "open_shadow_positions": db.execute("select count(*) from paper_positions where status = 'SHADOW_OPEN'").fetchone()[0],
                "open_paper_positions": db.execute("select count(*) from paper_positions where status = 'OPEN'").fetchone()[0],
                "forward_backlog": db.execute("select count(*) from forward_queue where status != 'SENT'").fetchone()[0],
            }
            latest_batch = db.execute("select received_at from signal_batches order by received_at desc limit 1").fetchone()
            latest_episode = db.execute("select last_seen_at from signal_episodes order by last_seen_at desc limit 1").fetchone()
            latest_mark = db.execute("select marked_at from paper_marks order by marked_at desc limit 1").fetchone()
            latest_worker_error = db.execute(
                "select event_time, payload_json from system_events where event_type = 'WORKER_ERROR' order by event_time desc limit 1"
            ).fetchone()
            return {
                "counts": counts,
                "last_batch_at": latest_batch["received_at"] if latest_batch else None,
                "last_episode_at": latest_episode["last_seen_at"] if latest_episode else None,
                "last_mark_at": latest_mark["marked_at"] if latest_mark else None,
                "last_worker_exception": dict(latest_worker_error) if latest_worker_error else None,
            }

    def due_forward_items(self, now_text: str) -> list[str]:
        with self.connect() as db:
            rows = db.execute(
                """
                select id from forward_queue
                where status != 'SENT'
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
                (event_id, self.now_text(), event_type, json.dumps(payload, default=str)),
            )
        return event_id

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
                        json.dumps(payload, default=str),
                    ),
                )

    def open_positions(self, include_shadow: bool = True) -> list[dict[str, Any]]:
        statuses = ("OPEN", "SHADOW_OPEN") if include_shadow else ("OPEN",)
        placeholders = ",".join("?" for _ in statuses)
        with self.connect() as db:
            return [dict(row) for row in db.execute(f"select * from paper_positions where status in ({placeholders})", statuses).fetchall()]

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
        stop_after_daily_losses: int,
    ) -> tuple[bool, str | None]:
        today = datetime.now(timezone.utc).date().isoformat()
        with self.connect() as db:
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
            state = db.execute("select * from daily_account_state where trade_date = ?", (today,)).fetchone()
            new_positions = int(state["new_positions"]) if state else 0
            stopped = int(state["stopped_trades"]) if state else 0
            if new_positions >= max_new_positions_per_day:
                return False, "SKIPPED_DAILY_LIMIT"
            if stopped >= stop_after_daily_losses:
                return False, "SKIPPED_DAILY_STOP_LIMIT"
            now = str(payload.get("opened_at") or self.now_text())
            db.execute(
                """
                insert into paper_positions(id, episode_id, ticker, direction, symbol, quantity, entry_price, opened_at, status, payload_json)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (position_id, episode_id, ticker, direction, symbol, quantity, entry_price, now, status, json.dumps(payload, default=str)),
            )
            account_payload = {"trade_date": today, "new_positions": new_positions + 1, "stopped_trades": stopped}
            db.execute(
                """
                insert into daily_account_state(trade_date, new_positions, stopped_trades, payload_json)
                values (?, ?, ?, ?)
                on conflict(trade_date) do update set
                    new_positions = excluded.new_positions,
                    stopped_trades = excluded.stopped_trades,
                    payload_json = excluded.payload_json
                """,
                (today, new_positions + 1, stopped, json.dumps(account_payload)),
            )
        return True, None

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
                    payload.get("pnl_pct"), json.dumps(payload, default=str),
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
                 json.dumps(payload, default=str)))
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

    def close_position(self, position_id: str, exit_price: float, exit_reason: str, payload: dict[str, Any]) -> bool:
        now = self.now_text()
        with self.connect() as db:
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
                    json.dumps({"position_id": position_id, "exit_reason": exit_reason, **payload}, default=str),
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
            if exit_reason == "option_stop_loss":
                today = datetime.now(timezone.utc).date().isoformat()
                state = db.execute("select * from daily_account_state where trade_date = ?", (today,)).fetchone()
                stopped = int(state["stopped_trades"]) if state else 0
                new_positions = int(state["new_positions"]) if state else 0
                account_payload = {"trade_date": today, "new_positions": new_positions, "stopped_trades": stopped + 1}
                db.execute(
                    """
                    insert into daily_account_state(trade_date, new_positions, stopped_trades, payload_json)
                    values (?, ?, ?, ?)
                    on conflict(trade_date) do update set
                        stopped_trades = excluded.stopped_trades,
                        payload_json = excluded.payload_json
                    """,
                    (today, new_positions, stopped + 1, json.dumps(account_payload)),
                )
        return True

    def integrity_ok(self) -> bool:
        with self.connect() as db:
            row = db.execute("pragma integrity_check").fetchone()
            return bool(row and row[0] == "ok")
