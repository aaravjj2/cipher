"""Capture live Cipher GEX matrix snapshots for later backtests.

This job uses the active local Alpaca-backed matrix path from ``core/app.py``.
It stores the raw matrix payload and normalized strike/expiration rows so later
tests can use the exact public-OI heuristic that the UI saw at capture time.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
UNIVERSE_JSON = DATA_DIR / "optionable_universe_by_cap.json"
DEFAULT_DB = DATA_DIR / "gex_history.sqlite"
DEFAULT_RAW_DIR = DATA_DIR / "gex_snapshots"
DEFAULT_TIERS = ("mega", "large", "medium")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _connect(db_path: Path) -> sqlite3.Connection:
    """Open a WAL-mode connection for concurrent read safety."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def ensure_schema(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _connect(db_path) as db:
        db.executescript(
            """
            create table if not exists gex_capture_runs (
                id integer primary key autoincrement,
                started_at text not null,
                completed_at text,
                source text not null,
                feed text not null,
                depth text not null,
                expirations integer not null,
                ticker_count integer not null,
                success_count integer not null default 0,
                error_count integer not null default 0,
                caveat text not null
            );

            create table if not exists gex_snapshots (
                id integer primary key autoincrement,
                run_id integer not null references gex_capture_runs(id),
                ticker text not null,
                captured_at text not null,
                feed text,
                spot real,
                day_change_pct real,
                raw_json_path text not null,
                contracts integer,
                calculated_cells integer,
                listed_cells integer,
                global_max_strike real,
                call_wall_strike real,
                put_wall_strike real,
                gamma_flip_level real,
                caveat text not null
            );

            create table if not exists gex_strike_cells (
                snapshot_id integer not null references gex_snapshots(id),
                ticker text not null,
                captured_at text not null,
                expiration text not null,
                strike real not null,
                call_gex real,
                put_gex real,
                net_gex real,
                call_vex real,
                put_vex real,
                net_vex real,
                call_oi real,
                put_oi real,
                volume real,
                call_mid real,
                put_mid real,
                listed integer not null,
                available integer not null
            );

            create index if not exists idx_gex_snapshots_ticker_time
                on gex_snapshots(ticker, captured_at);
            create index if not exists idx_gex_cells_ticker_time_exp_strike
                on gex_strike_cells(ticker, captured_at, expiration, strike);
            """
        )


def load_universe(tiers: Iterable[str] = DEFAULT_TIERS) -> list[str]:
    tiers = tuple(t.lower() for t in tiers)
    if not UNIVERSE_JSON.is_file():
        raise FileNotFoundError(f"Universe file not found: {UNIVERSE_JSON}")
    payload = json.loads(UNIVERSE_JSON.read_text(encoding="utf-8"))
    sorted_tickers = payload.get("sorted_tickers") or {}
    out: list[str] = []
    seen: set[str] = set()
    for tier in tiers:
        for raw in sorted_tickers.get(tier) or []:
            ticker = str(raw).upper().strip()
            if ticker and ticker not in seen:
                seen.add(ticker)
                out.append(ticker)
    return out


def create_run(
    db_path: Path,
    *,
    source: str,
    feed: str,
    depth: str,
    expirations: int,
    ticker_count: int,
) -> int:
    with _connect(db_path) as db:
        cur = db.execute(
            """
            insert into gex_capture_runs
                (started_at, source, feed, depth, expirations, ticker_count, caveat)
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                utcnow(),
                source,
                feed,
                str(depth),
                int(expirations),
                int(ticker_count),
                "Captured from Alpaca-backed Cipher matrix. Public-OI GEX heuristic, not verified dealer positioning.",
            ),
        )
        return int(cur.lastrowid)


def finish_run(db_path: Path, run_id: int, *, success_count: int, error_count: int) -> None:
    with _connect(db_path) as db:
        db.execute(
            """
            update gex_capture_runs
            set completed_at = ?, success_count = ?, error_count = ?
            where id = ?
            """,
            (utcnow(), int(success_count), int(error_count), int(run_id)),
        )


def write_snapshot(
    db_path: Path,
    raw_dir: Path,
    run_id: int,
    payload: dict,
) -> int:
    raw_dir.mkdir(parents=True, exist_ok=True)
    ticker = str(payload.get("ticker") or "").upper()
    captured_at = str(payload.get("as_of") or utcnow())
    raw_path = raw_dir / ticker / f"{captured_at.replace(':', '').replace('.', '_')}.json"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    try:
        from research_platform.ingestion_hooks import hooks_enabled, register_ingestion_file

        if hooks_enabled():
            captured_dt = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
            register_ingestion_file(
                raw_path,
                source="alpaca_opra",
                dataset="gex_raw_snapshots",
                ingestion_run_id=f"gex_run_{run_id}",
                event_time_start=captured_dt,
                event_time_end=captured_dt,
                metadata={
                    "ticker": ticker,
                    "feed": payload.get("feed"),
                    "run_id": run_id,
                    "gex_caveat": "Public-OI heuristic, not verified dealer positioning.",
                },
            )
    except Exception:
        # Governance is supplementary. Market-data capture must not fail because
        # the registry is unavailable; the raw file remains available to catalog.
        pass

    quote = payload.get("quote") or {}
    coverage = payload.get("coverage") or {}
    summary = payload.get("summary") or {}
    with _connect(db_path) as db:
        cur = db.execute(
            """
            insert into gex_snapshots (
                run_id, ticker, captured_at, feed, spot, day_change_pct,
                raw_json_path, contracts, calculated_cells, listed_cells,
                global_max_strike, call_wall_strike, put_wall_strike,
                gamma_flip_level, caveat
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                ticker,
                captured_at,
                payload.get("feed"),
                quote.get("price_context"),
                quote.get("day_change_pct"),
                str(raw_path),
                coverage.get("contracts"),
                coverage.get("calculated_cells"),
                coverage.get("listed_cells"),
                summary.get("global_max_strike"),
                summary.get("call_wall_strike"),
                summary.get("put_wall_strike"),
                summary.get("gamma_flip_level"),
                payload.get("caveat")
                or "Public-OI GEX heuristic, not verified dealer positioning.",
            ),
        )
        snapshot_id = int(cur.lastrowid)
        rows = []
        for row in payload.get("rows") or []:
            strike = row.get("strike")
            for cell in row.get("cells") or []:
                rows.append(
                    (
                        snapshot_id,
                        ticker,
                        captured_at,
                        cell.get("expiration"),
                        strike,
                        cell.get("call_gex"),
                        cell.get("put_gex"),
                        cell.get("net_gex"),
                        cell.get("call_vex"),
                        cell.get("put_vex"),
                        cell.get("net_vex"),
                        cell.get("call_oi"),
                        cell.get("put_oi"),
                        cell.get("volume"),
                        cell.get("call_mid"),
                        cell.get("put_mid"),
                        1 if cell.get("listed") else 0,
                        1 if cell.get("available") else 0,
                    )
                )
        db.executemany(
            """
            insert into gex_strike_cells (
                snapshot_id, ticker, captured_at, expiration, strike,
                call_gex, put_gex, net_gex, call_vex, put_vex, net_vex,
                call_oi, put_oi, volume, call_mid, put_mid, listed, available
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    return snapshot_id


def capture_ticker(
    ticker: str,
    *,
    feed: str,
    depth: str,
    expirations: int,
    chain_pages: int | None,
) -> dict:
    import app as cipher_app

    return cipher_app.matrix(
        ticker.upper(),
        feed,
        depth,
        int(expirations),
        force=True,
        chain_pages=chain_pages,
    )


def capture_once(args: argparse.Namespace) -> dict:
    ensure_schema(args.db)
    tickers = args.tickers or load_universe(args.tiers)
    if args.limit:
        tickers = tickers[: args.limit]
    run_id = create_run(
        args.db,
        source="cipher_alpaca_matrix",
        feed=args.feed,
        depth=args.depth,
        expirations=args.expirations,
        ticker_count=len(tickers),
    )
    ok = 0
    errors = []
    for index, ticker in enumerate(tickers, start=1):
        try:
            payload = capture_ticker(
                ticker,
                feed=args.feed,
                depth=args.depth,
                expirations=args.expirations,
                chain_pages=args.chain_pages,
            )
            snapshot_id = write_snapshot(args.db, args.raw_dir, run_id, payload)
            ok += 1
            print(f"[{index}/{len(tickers)}] {ticker} captured snapshot_id={snapshot_id}", flush=True)
        except Exception as exc:
            errors.append({"ticker": ticker, "error": str(exc)})
            print(f"[{index}/{len(tickers)}] {ticker} ERROR {exc}", flush=True)
        if args.sleep_ms > 0 and index < len(tickers):
            time.sleep(args.sleep_ms / 1000.0)
    finish_run(args.db, run_id, success_count=ok, error_count=len(errors))
    result = {
        "run_id": run_id,
        "db": str(args.db),
        "raw_dir": str(args.raw_dir),
        "tickers": len(tickers),
        "success_count": ok,
        "error_count": len(errors),
        "errors": errors[:50],
    }
    print(json.dumps(result, indent=2), flush=True)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture Cipher GEX matrix snapshots.")
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--all", action="store_true", help="Capture the cap-filtered optionable universe.")
    scope.add_argument("--ticker", action="append", dest="tickers", help="Capture one ticker; repeatable.")
    parser.add_argument("--tiers", default="mega,large,medium", help="Universe tiers for --all.")
    parser.add_argument("--limit", type=int, default=0, help="Limit ticker count for smoke tests.")
    parser.add_argument("--feed", default="opra", choices=("opra", "indicative"))
    parser.add_argument("--depth", default="0.06", help="Strike window, e.g. 0.06 or all.")
    parser.add_argument("--expirations", type=int, default=1, help="Expiration columns to capture.")
    parser.add_argument("--chain-pages", type=int, default=None, help="Override option snapshot pages.")
    parser.add_argument("--sleep-ms", type=int, default=1250, help="Delay between tickers to reduce 429s.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--loop", action="store_true", help="Repeat captures until stopped.")
    parser.add_argument("--interval-minutes", type=float, default=15.0, help="Loop delay between full passes.")
    args = parser.parse_args()
    args.tiers = [part.strip().lower() for part in str(args.tiers).split(",") if part.strip()]
    if args.tickers:
        args.tickers = [ticker.upper().strip() for ticker in args.tickers if ticker.strip()]
    return args


def main() -> int:
    args = parse_args()
    while True:
        started = time.time()
        capture_once(args)
        if not args.loop:
            return 0
        elapsed = time.time() - started
        delay = max(0.0, args.interval_minutes * 60.0 - elapsed)
        print(f"Sleeping {delay:.1f}s before next capture pass.", flush=True)
        time.sleep(delay)


if __name__ == "__main__":
    raise SystemExit(main())
