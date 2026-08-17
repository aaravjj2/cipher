"""Prospective local paper account for the Structural Fib V6 baseline.

Signals are accepted only when they belong to the latest fully closed five-minute
bar. Missed passes are never backfilled. Fills and exits are simulated from stock
bars and persisted to a dedicated SQLite database; no broker trading API exists.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Callable, Sequence

from core import structural_fib_v6 as v6
from core.structural_fib_bars import Bar, NY, split_sessions

DEFAULT_DB = Path("/home/aarav/Aarav/cipher/runtime/data/structural_fib_v6/v6_paper.sqlite")
SYMBOLS = ("NVDA", "AAPL")


def connect(path: Path = DEFAULT_DB) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        pragma journal_mode=WAL;
        create table if not exists account_meta (
          key text primary key, value text not null
        );
        create table if not exists signals (
          signal_id text primary key, params_hash text not null, symbol text not null,
          day text not null, setup_id text not null, direction text not null,
          signal_time_et text not null, captured_at text not null, payload_json text not null
        );
        create table if not exists positions (
          position_id text primary key, signal_id text not null unique references signals(signal_id),
          status text not null, allocated_capital real, quantity real,
          entry_at text, entry_price real, exit_at text, exit_price real,
          exit_reason text, pnl real, return_pct real, mfe_pct real, mae_pct real
        );
        create index if not exists ix_v6_positions_status on positions(status);
    """)
    conn.execute("insert or ignore into account_meta values ('starting_equity','100000.0')")
    conn.execute("insert or ignore into account_meta values ('created_at',?)", (datetime.now(timezone.utc).isoformat(),))
    conn.execute("insert or ignore into account_meta values ('params_hash',?)", (v6.params_hash(),))
    conn.commit()
    return conn


def _latest_regular_bar(bars: Sequence[Bar], today: date) -> Bar | None:
    regular = split_sessions(bars).get(today, {}).get("reg", [])
    return regular[-1] if regular else None


def record_latest_signals(conn: sqlite3.Connection, symbol: str, bars: Sequence[Bar], now: datetime) -> int:
    today = now.astimezone(NY).date()
    latest = _latest_regular_bar(bars, today)
    if latest is None:
        return 0
    result = v6.run_symbol(symbol, bars)
    latest_clock = latest.t.strftime("%H:%M")
    rows = [s for s in result["signals"] if s["day"] == today.isoformat() and s["signal_time"] == latest_clock]
    added = 0
    for signal in rows:
        signal_id = f"{v6.params_hash()}|{symbol}|{signal['day']}|{signal['setup_id']}"
        before = conn.total_changes
        conn.execute(
            "insert or ignore into signals values (?,?,?,?,?,?,?,?,?)",
            (signal_id, v6.params_hash(), symbol, signal["day"], signal["setup_id"],
             signal["direction"], signal["signal_time"], now.astimezone(timezone.utc).isoformat(),
             json.dumps(signal, sort_keys=True)),
        )
        if conn.total_changes > before:
            conn.execute(
                "insert into positions(position_id,signal_id,status) values (?,?, 'PENDING')",
                ("position|" + signal_id, signal_id),
            )
            added += 1
    conn.commit()
    return added


def _realized_equity(conn: sqlite3.Connection) -> float:
    start = float(conn.execute("select value from account_meta where key='starting_equity'").fetchone()[0])
    pnl = conn.execute("select coalesce(sum(pnl),0) from positions where status='CLOSED'").fetchone()[0]
    return start + float(pnl or 0.0)


def reconcile_symbol(conn: sqlite3.Connection, symbol: str, bars: Sequence[Bar]) -> int:
    sessions = split_sessions(bars)
    rows = conn.execute(
        "select p.*,s.day,s.direction,s.signal_time_et,s.payload_json from positions p "
        "join signals s on s.signal_id=p.signal_id where p.status in ('PENDING','OPEN') and s.symbol=?",
        (symbol,),
    ).fetchall()
    changed = 0
    for row in rows:
        day = date.fromisoformat(row["day"])
        regular = sessions.get(day, {}).get("reg", [])
        signal_index = next((i for i, b in enumerate(regular) if b.t.strftime("%H:%M") == row["signal_time_et"]), None)
        if signal_index is None or signal_index + 1 >= len(regular):
            continue
        payload = json.loads(row["payload_json"])
        entry_index = signal_index + 1
        entry_bar = regular[entry_index]
        entry = float(row["entry_price"] or entry_bar.o)
        allocated = float(row["allocated_capital"] or (_realized_equity(conn) * v6.DEFAULT_PARAMS.equity_fraction))
        quantity = float(row["quantity"] or (allocated / entry))
        if row["status"] == "PENDING":
            conn.execute(
                "update positions set status='OPEN',allocated_capital=?,quantity=?,entry_at=?,entry_price=? where position_id=?",
                (allocated, quantity, entry_bar.t.isoformat(), entry, row["position_id"]),
            )
            changed += 1

        direction, target, stop = row["direction"], float(payload["target"]), float(payload["stop"])
        pending_stop = False
        max_high, min_low = entry, entry
        exit_bar = None
        exit_price = None
        reason = None
        for bar in regular[entry_index:]:
            if pending_stop:
                exit_bar, exit_price, reason = bar, bar.o, "fib_invalidation"
                break
            max_high, min_low = max(max_high, bar.h), min(min_low, bar.l)
            hit_target = bar.h >= target if direction == "long" else bar.l <= target
            if hit_target:
                exit_bar = bar
                exit_price = max(target, bar.o) if direction == "long" else min(target, bar.o)
                reason = "target"
                break
            pending_stop = bar.c < stop if direction == "long" else bar.c > stop
            if v6.DEFAULT_PARAMS.close_at_rth and bar.t.time() == time(15, 55):
                exit_bar, exit_price, reason = bar, bar.c, "rth_close"
                break
        if exit_bar is None:
            continue
        sign = 1.0 if direction == "long" else -1.0
        ret = (exit_price - entry) * sign / entry * 100.0
        pnl = allocated * ret / 100.0
        mfe = ((max_high - entry) if sign > 0 else (entry - min_low)) / entry * 100.0
        mae = ((entry - min_low) if sign > 0 else (max_high - entry)) / entry * 100.0
        conn.execute(
            "update positions set status='CLOSED',exit_at=?,exit_price=?,exit_reason=?,pnl=?,return_pct=?,mfe_pct=?,mae_pct=? where position_id=?",
            (exit_bar.t.isoformat(), exit_price, reason, pnl, ret, max(0.0, mfe), max(0.0, mae), row["position_id"]),
        )
        changed += 1
    conn.commit()
    return changed


def account_status(conn: sqlite3.Connection) -> dict:
    counts = dict(conn.execute("select status,count(*) from positions group by status").fetchall())
    realized = float(conn.execute("select coalesce(sum(pnl),0) from positions where status='CLOSED'").fetchone()[0])
    start = float(conn.execute("select value from account_meta where key='starting_equity'").fetchone()[0])
    return {
        "mode": "paper_simulation", "paper_only": True, "params_hash": v6.params_hash(),
        "starting_equity": start, "realized_pnl": realized, "realized_equity": start + realized,
        "signals": conn.execute("select count(*) from signals").fetchone()[0],
        "positions": counts, "database_integrity": conn.execute("pragma quick_check").fetchone()[0],
    }


def run_pass(
    fetch: Callable[[str], Sequence[Bar]],
    *,
    symbols: Sequence[str] = SYMBOLS,
    db_path: Path = DEFAULT_DB,
    now: datetime | None = None,
) -> dict:
    moment = now or datetime.now(timezone.utc)
    conn = connect(db_path)
    added = changed = 0
    errors: list[dict[str, str]] = []
    try:
        for symbol in symbols:
            try:
                bars = list(fetch(symbol))
                added += record_latest_signals(conn, symbol, bars, moment)
                changed += reconcile_symbol(conn, symbol, bars)
            except Exception as exc:
                errors.append({"symbol": symbol, "error": f"{type(exc).__name__}: {exc}"})
        return {"new_signals": added, "position_changes": changed, "errors": errors, "account": account_status(conn)}
    finally:
        conn.close()
