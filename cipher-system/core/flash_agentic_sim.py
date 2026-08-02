"""Simulated Flash Agentic signal ledger.

This watches visible Flash Agentic scanner rows, records unique signals, and
simulates entry/exit rules. It is alerting/research infrastructure only: no
broker account, order, preview, or trading endpoints are used.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CORE_DIR = Path(__file__).resolve().parent
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

import tradier_stream_capture as tradier


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SCAN_DIR = DATA / "accessobsidian_scans"
OUT_DIR = DATA / "flash_agentic"
DEFAULT_DB = OUT_DIR / "flash_agentic.sqlite"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def num(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def latest_flash_run(scan_dir: Path = SCAN_DIR) -> Path:
    summaries = sorted(scan_dir.glob("20*/20*/summary.json"), reverse=True)
    for summary in summaries:
        flash = summary.parent / "flash_agentic.json"
        if flash.is_file():
            try:
                if load_json(flash).get("rows"):
                    return summary.parent
            except (OSError, json.JSONDecodeError):
                continue
    raise FileNotFoundError("No Flash Agentic capture with rows found.")


def ensure_schema(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as db:
        db.executescript(
            """
            create table if not exists flash_trades (
                id text primary key,
                status text not null,
                opened_at text not null,
                closed_at text,
                ticker text not null,
                direction text not null,
                entry_spot real not null,
                latest_spot real,
                target real,
                invalidation real,
                setup text,
                setup_family text,
                regime text,
                latest_event text,
                surface_event text,
                gamma_regime text,
                vwap_state text,
                tape_state text,
                take_profit_pct real not null,
                stop_loss_pct real not null,
                pnl_pct real,
                exit_reason text,
                source_run_dir text not null,
                payload_json text not null
            );

            create table if not exists flash_marks (
                id integer primary key autoincrement,
                trade_id text not null references flash_trades(id),
                captured_at text not null,
                spot real,
                pnl_pct real,
                status text not null,
                payload_json text not null
            );

            create index if not exists idx_flash_trades_status
                on flash_trades(status, opened_at);
            """
        )
        existing = {row[1] for row in db.execute("pragma table_info(flash_trades)")}
        for column in (
            "setup",
            "setup_family",
            "regime",
            "latest_event",
            "surface_event",
            "gamma_regime",
            "vwap_state",
            "tape_state",
        ):
            if column not in existing:
                db.execute(f"alter table flash_trades add column {column} text")


def tradier_quotes(symbols: list[str]) -> dict[str, dict[str, Any]]:
    token, _ = tradier.load_credentials("production")
    url = "https://api.tradier.com/v1/markets/quotes?" + urllib.parse.urlencode({"symbols": ",".join(symbols)})
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=25) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    quotes = (payload.get("quotes") or {}).get("quote") or []
    if isinstance(quotes, dict):
        quotes = [quotes]
    return {str(row.get("symbol") or "").upper(): row for row in quotes}


def quote_spots(tickers: list[str]) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for idx in range(0, len(tickers), 80):
        quotes = tradier_quotes(tickers[idx:idx + 80])
        for ticker in tickers[idx:idx + 80]:
            quote = quotes.get(ticker.upper()) or {}
            bid = num(quote.get("bid"))
            ask = num(quote.get("ask"))
            last = num(quote.get("last"))
            out[ticker.upper()] = last or ((bid + ask) / 2 if bid is not None and ask is not None else None)
    return out


def direction(row: dict[str, Any]) -> str:
    raw = f"{row.get('bias') or ''} {row.get('raw_card') or ''}".upper()
    return "short" if "BEARISH" in raw or "DOWNSIDE" in raw else "long"


def trade_id(row: dict[str, Any], run_dir: Path) -> str:
    ticker = str(row.get("ticker") or "").upper()
    rank = row.get("rank")
    side = direction(row)
    target = num(row.get("first_target") or row.get("push_target") or row.get("target") or row.get("stretch"))
    return f"{run_dir.name}|{rank}|{ticker}|{side}|{target or 0:.4f}".lower()


def open_from_latest(
    *,
    db_path: Path = DEFAULT_DB,
    take_profit_pct: float = 20.0,
    stop_loss_pct: float = 12.0,
    max_new: int = 5,
) -> dict[str, Any]:
    ensure_schema(db_path)
    run_dir = latest_flash_run()
    payload = load_json(run_dir / "flash_agentic.json")
    rows = [r for r in payload.get("rows") or [] if r.get("ticker")]
    tickers = [str(r["ticker"]).upper() for r in rows]
    spots = quote_spots(tickers)
    opened = []
    skipped = []
    with sqlite3.connect(db_path) as db:
        for row in rows[:max_new]:
            ticker = str(row["ticker"]).upper()
            spot = spots.get(ticker) or num(row.get("spot"))
            if not spot:
                skipped.append({"ticker": ticker, "reason": "missing_spot"})
                continue
            tid = trade_id(row, run_dir)
            exists = db.execute("select 1 from flash_trades where id = ?", (tid,)).fetchone()
            if exists:
                skipped.append({"ticker": ticker, "reason": "already_open_or_seen"})
                continue
            side = direction(row)
            target = num(row.get("first_target") or row.get("push_target") or row.get("stretch"))
            open_duplicate = db.execute(
                """
                select 1 from flash_trades
                where status = 'open'
                  and ticker = ?
                  and direction = ?
                  and coalesce(round(target, 4), 0) = coalesce(round(?, 4), 0)
                """,
                (ticker, side, target),
            ).fetchone()
            if open_duplicate:
                skipped.append({"ticker": ticker, "reason": "open_duplicate_same_direction_target"})
                continue
            invalidation = num(row.get("invalidation"))
            opened_at = now_utc()
            record = {
                "id": tid,
                "status": "open",
                "opened_at": opened_at,
                "ticker": ticker,
                "direction": side,
                "setup": row.get("setup") or "",
                "setup_family": row.get("setup_family") or "",
                "regime": row.get("regime") or "",
                "latest_event": row.get("latest_event") or "",
                "surface_event": row.get("surface_event") or "",
                "gamma_regime": row.get("gamma_regime") or "",
                "vwap_state": row.get("vwap_state") or "",
                "tape_state": row.get("tape_state") or "",
                "entry_spot": spot,
                "latest_spot": spot,
                "target": target,
                "invalidation": invalidation,
                "take_profit_pct": float(take_profit_pct),
                "stop_loss_pct": float(stop_loss_pct),
                "source_run_dir": str(run_dir),
                "source_row": row,
                "caveat": "Simulated Flash Agentic alert only. No broker/order endpoints used.",
            }
            db.execute(
                """
                insert into flash_trades (
                    id, status, opened_at, closed_at, ticker, direction, entry_spot,
                    latest_spot, target, invalidation, setup, setup_family, regime,
                    latest_event, surface_event, gamma_regime, vwap_state, tape_state,
                    take_profit_pct, stop_loss_pct, pnl_pct, exit_reason, source_run_dir,
                    payload_json
                )
                values (?, 'open', ?, null, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, null, ?, ?)
                """,
                (
                    tid,
                    opened_at,
                    ticker,
                    side,
                    spot,
                    spot,
                    target,
                    invalidation,
                    row.get("setup") or "",
                    row.get("setup_family") or "",
                    row.get("regime") or "",
                    row.get("latest_event") or "",
                    row.get("surface_event") or "",
                    row.get("gamma_regime") or "",
                    row.get("vwap_state") or "",
                    row.get("tape_state") or "",
                    float(take_profit_pct),
                    float(stop_loss_pct),
                    str(run_dir),
                    json.dumps(record, separators=(",", ":"), default=str),
                ),
            )
            opened.append(record)
    return write_report({"mode": "open", "opened": opened, "skipped": skipped, "source_run_dir": str(run_dir)}, db_path)


def mark_open(*, db_path: Path = DEFAULT_DB) -> dict[str, Any]:
    ensure_schema(db_path)
    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute("select * from flash_trades where status = 'open' order by opened_at").fetchall()
    tickers = sorted({row["ticker"] for row in rows})
    spots = quote_spots(tickers)
    marks = []
    alerts = []
    with sqlite3.connect(db_path) as db:
        for row in rows:
            spot = spots.get(row["ticker"])
            if spot is None:
                continue
            entry = float(row["entry_spot"])
            side = row["direction"]
            pnl_pct = ((spot - entry) / entry * 100.0) if side == "long" else ((entry - spot) / entry * 100.0)
            status = "open"
            exit_reason = None
            if pnl_pct >= float(row["take_profit_pct"]):
                status = "closed"
                exit_reason = "take_profit_hit"
            elif pnl_pct <= -float(row["stop_loss_pct"]):
                status = "closed"
                exit_reason = "stop_loss_hit"
            elif row["target"] is not None and ((side == "long" and spot >= row["target"]) or (side == "short" and spot <= row["target"])):
                status = "closed"
                exit_reason = "flash_target_hit"
            elif row["invalidation"] is not None and ((side == "long" and spot <= row["invalidation"]) or (side == "short" and spot >= row["invalidation"])):
                status = "closed"
                exit_reason = "flash_invalidation_hit"
            mark = {
                "id": row["id"],
                "ticker": row["ticker"],
                "direction": side,
                "setup": row["setup"],
                "setup_family": row["setup_family"],
                "regime": row["regime"],
                "latest_event": row["latest_event"],
                "surface_event": row["surface_event"],
                "gamma_regime": row["gamma_regime"],
                "vwap_state": row["vwap_state"],
                "tape_state": row["tape_state"],
                "entry_spot": entry,
                "latest_spot": spot,
                "pnl_pct": round(pnl_pct, 3),
                "status": status,
                "exit_reason": exit_reason,
            }
            if status == "closed":
                db.execute(
                    "update flash_trades set status='closed', closed_at=?, latest_spot=?, pnl_pct=?, exit_reason=? where id=?",
                    (now_utc(), spot, pnl_pct, exit_reason, row["id"]),
                )
                alerts.append(mark)
            else:
                db.execute("update flash_trades set latest_spot=?, pnl_pct=? where id=?", (spot, pnl_pct, row["id"]))
            db.execute(
                "insert into flash_marks (trade_id, captured_at, spot, pnl_pct, status, payload_json) values (?, ?, ?, ?, ?, ?)",
                (row["id"], now_utc(), spot, pnl_pct, status, json.dumps(mark, separators=(",", ":"), default=str)),
            )
            marks.append(mark)
    return write_report({"mode": "mark", "marks": marks, "alerts": alerts}, db_path)


def write_report(payload: dict[str, Any], db_path: Path) -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {**payload, "generated_at": now_utc(), "db_path": str(db_path)}
    latest = OUT_DIR / "latest.json"
    latest.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    sub = parser.add_subparsers(dest="cmd", required=True)
    open_cmd = sub.add_parser("open-latest")
    open_cmd.add_argument("--take-profit-pct", type=float, default=20.0)
    open_cmd.add_argument("--stop-loss-pct", type=float, default=12.0)
    open_cmd.add_argument("--max-new", type=int, default=5)
    sub.add_parser("mark")
    args = parser.parse_args()
    if args.cmd == "open-latest":
        payload = open_from_latest(
            db_path=args.db,
            take_profit_pct=args.take_profit_pct,
            stop_loss_pct=args.stop_loss_pct,
            max_new=args.max_new,
        )
    else:
        payload = mark_open(db_path=args.db)
    print(json.dumps(payload, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
