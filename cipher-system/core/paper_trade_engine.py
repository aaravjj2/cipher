"""Local read-only paper ledger for option setup tracking.

This is a simulated ledger only. It uses market-data quotes for hypothetical
fills and marks, and it does not call broker account, trading, or order APIs.
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
PAPER_DIR = DATA / "paper_trades"
SCAN_MARK_DIR = DATA / "scan_option_marks"
DEFAULT_DB = PAPER_DIR / "paper_trades.sqlite"
DEFAULT_BOOK = "setup_blend_20260722"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def num(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def occ_symbol(root: str, expiry: str, option_type: str, strike: float) -> str:
    year, month, day = expiry.split("-")
    cp = "C" if option_type.lower().startswith("c") else "P"
    return f"{root.upper()}{year[2:]}{month}{day}{cp}{int(round(float(strike) * 1000)):08d}"


def ensure_schema(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as db:
        db.executescript(
            """
            create table if not exists paper_positions (
                id text primary key,
                book text not null,
                status text not null,
                opened_at text not null,
                closed_at text,
                ticker text not null,
                strategy text not null,
                direction text not null,
                expiry text not null,
                long_symbol text not null,
                short_symbol text,
                quantity integer not null,
                entry_debit real not null,
                width real,
                target_underlying real,
                thesis text,
                payload_json text not null
            );

            create table if not exists paper_marks (
                id integer primary key autoincrement,
                position_id text not null references paper_positions(id),
                captured_at text not null,
                spot real,
                mark real,
                market_value real,
                pnl_dollars real,
                pnl_pct real,
                long_bid real,
                long_ask real,
                short_bid real,
                short_ask real,
                payload_json text not null
            );

            create index if not exists idx_paper_positions_book_status
                on paper_positions(book, status);
            create index if not exists idx_paper_marks_position_time
                on paper_marks(position_id, captured_at);
            """
        )


def tradier_quotes(symbols: list[str], greeks: bool = True) -> dict[str, dict[str, Any]]:
    token, _ = tradier.load_credentials("production")
    url = "https://api.tradier.com/v1/markets/quotes?" + urllib.parse.urlencode({
        "symbols": ",".join(symbols),
        "greeks": "true" if greeks else "false",
    })
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=25) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    quotes = (payload.get("quotes") or {}).get("quote") or []
    if isinstance(quotes, dict):
        quotes = [quotes]
    return {str(row.get("symbol") or "").upper(): row for row in quotes}


def mid(quote: dict[str, Any]) -> float | None:
    bid = num(quote.get("bid"))
    ask = num(quote.get("ask"))
    last = num(quote.get("last"))
    if bid is not None and ask is not None and ask >= bid and ask > 0:
        return (bid + ask) / 2.0
    return last


def quote_spot(ticker: str) -> float | None:
    quote = tradier_quotes([ticker], greeks=False).get(ticker.upper()) or {}
    return num(quote.get("last")) or mid(quote)


def position_id(book: str, ticker: str, expiry: str, long_symbol: str, short_symbol: str | None) -> str:
    suffix = f"{ticker}_{expiry}_{long_symbol}_{short_symbol or 'single'}"
    return f"{book}_{suffix}".lower().replace("-", "")


def open_debit_spread(
    *,
    db_path: Path,
    book: str,
    ticker: str,
    direction: str,
    expiry: str,
    option_type: str,
    long_strike: float,
    short_strike: float,
    quantity: int,
    target_underlying: float | None,
    thesis: str,
) -> dict[str, Any]:
    ensure_schema(db_path)
    long_symbol = occ_symbol(ticker, expiry, option_type, long_strike)
    short_symbol = occ_symbol(ticker, expiry, option_type, short_strike)
    quotes = tradier_quotes([long_symbol, short_symbol, ticker], greeks=True)
    long_quote = quotes.get(long_symbol) or {}
    short_quote = quotes.get(short_symbol) or {}
    long_mid = mid(long_quote)
    short_mid = mid(short_quote)
    if long_mid is None or short_mid is None:
        raise RuntimeError(f"Unable to mark entry for {ticker}: missing leg quote.")
    entry_debit = round(long_mid - short_mid, 4)
    if entry_debit <= 0:
        raise RuntimeError(f"Invalid debit for {ticker}: {entry_debit}")
    width = abs(float(short_strike) - float(long_strike))
    pid = position_id(book, ticker, expiry, long_symbol, short_symbol)
    opened_at = now_utc()
    payload = {
        "id": pid,
        "book": book,
        "status": "open",
        "opened_at": opened_at,
        "ticker": ticker.upper(),
        "strategy": "call_debit_spread" if option_type.lower().startswith("c") else "put_debit_spread",
        "direction": direction,
        "expiry": expiry,
        "long_symbol": long_symbol,
        "short_symbol": short_symbol,
        "quantity": quantity,
        "entry_debit": entry_debit,
        "width": width,
        "target_underlying": target_underlying,
        "spot_at_entry": num((quotes.get(ticker.upper()) or {}).get("last")) or quote_spot(ticker),
        "long_quote_at_entry": long_quote,
        "short_quote_at_entry": short_quote,
        "thesis": thesis,
        "caveat": "Local paper simulation only. No broker account or trading endpoints used.",
    }
    with sqlite3.connect(db_path) as db:
        db.execute(
            """
            insert or replace into paper_positions (
                id, book, status, opened_at, closed_at, ticker, strategy, direction,
                expiry, long_symbol, short_symbol, quantity, entry_debit, width,
                target_underlying, thesis, payload_json
            )
            values (?, ?, ?, ?, null, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pid,
                book,
                "open",
                opened_at,
                ticker.upper(),
                payload["strategy"],
                direction,
                expiry,
                long_symbol,
                short_symbol,
                int(quantity),
                entry_debit,
                width,
                target_underlying,
                thesis,
                json.dumps(payload, separators=(",", ":"), default=str),
            ),
        )
    mark_position(db_path, pid)
    return payload


def open_long_option(
    *,
    db_path: Path,
    book: str,
    ticker: str,
    direction: str,
    expiry: str,
    option_type: str,
    strike: float,
    quantity: int,
    target_underlying: float | None,
    thesis: str,
) -> dict[str, Any]:
    ensure_schema(db_path)
    long_symbol = occ_symbol(ticker, expiry, option_type, strike)
    quotes = tradier_quotes([long_symbol, ticker], greeks=True)
    long_quote = quotes.get(long_symbol) or {}
    long_mid = mid(long_quote)
    if long_mid is None:
        raise RuntimeError(f"Unable to mark entry for {ticker}: missing option quote.")
    entry_debit = round(long_mid, 4)
    if entry_debit <= 0:
        raise RuntimeError(f"Invalid long-option debit for {ticker}: {entry_debit}")
    pid = position_id(book, ticker, expiry, long_symbol, None)
    opened_at = now_utc()
    payload = {
        "id": pid,
        "book": book,
        "status": "open",
        "opened_at": opened_at,
        "ticker": ticker.upper(),
        "strategy": "long_call" if option_type.lower().startswith("c") else "long_put",
        "direction": direction,
        "expiry": expiry,
        "long_symbol": long_symbol,
        "short_symbol": None,
        "quantity": quantity,
        "entry_debit": entry_debit,
        "width": None,
        "target_underlying": target_underlying,
        "spot_at_entry": num((quotes.get(ticker.upper()) or {}).get("last")) or quote_spot(ticker),
        "long_quote_at_entry": long_quote,
        "short_quote_at_entry": None,
        "thesis": thesis,
        "caveat": "Local paper simulation only. No broker account or trading endpoints used.",
    }
    with sqlite3.connect(db_path) as db:
        db.execute(
            """
            insert or replace into paper_positions (
                id, book, status, opened_at, closed_at, ticker, strategy, direction,
                expiry, long_symbol, short_symbol, quantity, entry_debit, width,
                target_underlying, thesis, payload_json
            )
            values (?, ?, ?, ?, null, ?, ?, ?, ?, ?, null, ?, ?, null, ?, ?, ?)
            """,
            (
                pid,
                book,
                "open",
                opened_at,
                ticker.upper(),
                payload["strategy"],
                direction,
                expiry,
                long_symbol,
                int(quantity),
                entry_debit,
                target_underlying,
                thesis,
                json.dumps(payload, separators=(",", ":"), default=str),
            ),
        )
    mark_position(db_path, pid)
    return payload


def open_long_symbol(
    *,
    db_path: Path,
    book: str,
    ticker: str,
    direction: str,
    expiry: str,
    long_symbol: str,
    quantity: int,
    target_underlying: float | None,
    thesis: str,
) -> dict[str, Any]:
    ensure_schema(db_path)
    quotes = tradier_quotes([long_symbol, ticker], greeks=True)
    long_quote = quotes.get(long_symbol) or {}
    long_mid = mid(long_quote)
    if long_mid is None:
        raise RuntimeError(f"Unable to mark entry for {ticker}: missing option quote.")
    entry_debit = round(long_mid, 4)
    if entry_debit <= 0:
        raise RuntimeError(f"Invalid long-option debit for {ticker}: {entry_debit}")
    option_type = str(long_quote.get("option_type") or ("call" if "C" in long_symbol else "put")).lower()
    pid = position_id(book, ticker, expiry, long_symbol, None)
    opened_at = now_utc()
    payload = {
        "id": pid,
        "book": book,
        "status": "open",
        "opened_at": opened_at,
        "ticker": ticker.upper(),
        "strategy": "long_call" if option_type.startswith("c") else "long_put",
        "direction": direction,
        "expiry": expiry,
        "long_symbol": long_symbol,
        "short_symbol": None,
        "quantity": quantity,
        "entry_debit": entry_debit,
        "width": None,
        "target_underlying": target_underlying,
        "spot_at_entry": num((quotes.get(ticker.upper()) or {}).get("last")) or quote_spot(ticker),
        "long_quote_at_entry": long_quote,
        "short_quote_at_entry": None,
        "thesis": thesis,
        "caveat": "Local paper simulation only. No broker account or trading endpoints used.",
    }
    with sqlite3.connect(db_path) as db:
        db.execute(
            """
            insert or replace into paper_positions (
                id, book, status, opened_at, closed_at, ticker, strategy, direction,
                expiry, long_symbol, short_symbol, quantity, entry_debit, width,
                target_underlying, thesis, payload_json
            )
            values (?, ?, ?, ?, null, ?, ?, ?, ?, ?, null, ?, ?, null, ?, ?, ?)
            """,
            (
                pid,
                book,
                "open",
                opened_at,
                ticker.upper(),
                payload["strategy"],
                direction,
                expiry,
                long_symbol,
                int(quantity),
                entry_debit,
                target_underlying,
                thesis,
                json.dumps(payload, separators=(",", ":"), default=str),
            ),
        )
    mark_position(db_path, pid)
    return payload


def load_position(db_path: Path, pid: str) -> dict[str, Any]:
    with sqlite3.connect(db_path) as db:
        row = db.execute("select payload_json from paper_positions where id = ?", (pid,)).fetchone()
    if not row:
        raise KeyError(pid)
    return json.loads(row[0])


def open_positions(db_path: Path, book: str | None = None) -> list[dict[str, Any]]:
    ensure_schema(db_path)
    sql = "select payload_json from paper_positions where status = 'open'"
    params: tuple[Any, ...] = ()
    if book:
        sql += " and book = ?"
        params = (book,)
    sql += " order by opened_at"
    with sqlite3.connect(db_path) as db:
        rows = db.execute(sql, params).fetchall()
    return [json.loads(row[0]) for row in rows]


def mark_position(db_path: Path, pid: str) -> dict[str, Any]:
    pos = load_position(db_path, pid)
    symbols = [pos["long_symbol"], pos["ticker"]]
    if pos.get("short_symbol"):
        symbols.append(pos["short_symbol"])
    quotes = tradier_quotes(symbols, greeks=True)
    long_quote = quotes.get(pos["long_symbol"]) or {}
    short_quote = quotes.get(pos["short_symbol"]) if pos.get("short_symbol") else {}
    long_mid = mid(long_quote)
    short_mid = mid(short_quote) if short_quote else 0.0
    if long_mid is None or short_mid is None:
        raise RuntimeError(f"Unable to mark {pid}: missing leg quote.")
    mark = round(long_mid - short_mid, 4)
    quantity = int(pos.get("quantity") or 1)
    entry_debit = float(pos["entry_debit"])
    market_value = mark * 100 * quantity
    cost_value = entry_debit * 100 * quantity
    pnl_dollars = market_value - cost_value
    pnl_pct = pnl_dollars / cost_value * 100 if cost_value else None
    captured_at = now_utc()
    payload = {
        "position_id": pid,
        "captured_at": captured_at,
        "ticker": pos["ticker"],
        "spot": num((quotes.get(pos["ticker"]) or {}).get("last")) or quote_spot(pos["ticker"]),
        "mark": mark,
        "entry_debit": entry_debit,
        "quantity": quantity,
        "market_value": round(market_value, 2),
        "pnl_dollars": round(pnl_dollars, 2),
        "pnl_pct": round(pnl_pct, 3) if pnl_pct is not None else None,
        "target_underlying": pos.get("target_underlying"),
        "long_quote": long_quote,
        "short_quote": short_quote,
        "caveat": "Local paper mark-to-mid simulation only.",
    }
    ensure_schema(db_path)
    with sqlite3.connect(db_path) as db:
        db.execute(
            """
            insert into paper_marks (
                position_id, captured_at, spot, mark, market_value, pnl_dollars, pnl_pct,
                long_bid, long_ask, short_bid, short_ask, payload_json
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pid,
                captured_at,
                payload["spot"],
                mark,
                payload["market_value"],
                payload["pnl_dollars"],
                payload["pnl_pct"],
                num(long_quote.get("bid")),
                num(long_quote.get("ask")),
                num(short_quote.get("bid")),
                num(short_quote.get("ask")),
                json.dumps(payload, separators=(",", ":"), default=str),
            ),
        )
    return payload


def mark_all(db_path: Path, book: str | None = None) -> list[dict[str, Any]]:
    return [mark_position(db_path, pos["id"]) for pos in open_positions(db_path, book)]


def write_latest(db_path: Path, book: str, marks: list[dict[str, Any]]) -> Path:
    out_dir = PAPER_DIR / book
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "latest.json"
    path.write_text(json.dumps({"generated_at": now_utc(), "book": book, "marks": marks}, indent=2), encoding="utf-8")
    return path


def seed_current_best(db_path: Path, book: str, quantity: int) -> list[dict[str, Any]]:
    specs = [
        {
            "ticker": "M",
            "direction": "up",
            "expiry": "2026-07-24",
            "option_type": "call",
            "long_strike": 24.0,
            "short_strike": 25.0,
            "target_underlying": 25.0,
            "thesis": "Best new candidate from blended scanner + company/week + spread liquidity filter.",
        },
        {
            "ticker": "SO",
            "direction": "up",
            "expiry": "2026-07-24",
            "option_type": "call",
            "long_strike": 95.0,
            "short_strike": 97.0,
            "target_underlying": 97.0,
            "thesis": "Best high-conviction scanner candidate; company/week context mixed but tape positive.",
        },
    ]
    opened = []
    for spec in specs:
        opened.append(open_debit_spread(db_path=db_path, book=book, quantity=quantity, **spec))
    return opened


def latest_scan_marks() -> Path:
    matches = sorted(SCAN_MARK_DIR.glob("scan_option_marks_*.json"))
    if not matches:
        raise FileNotFoundError("Run scan_option_mark_capture.py before seeding golden calls.")
    return matches[-1]


def seed_golden_calls(db_path: Path, book: str, quantity: int, max_contract_cost: float) -> list[dict[str, Any]]:
    payload = json.loads(latest_scan_marks().read_text(encoding="utf-8"))
    candidates = []
    for row in payload.get("rows") or []:
        setup = str(row.get("setup") or "").upper()
        mark = num(row.get("long_mark"))
        delta = num(row.get("long_delta"))
        width = num(row.get("long_width_pct"))
        distance = num(row.get("target_distance_pct"))
        symbol = str(row.get("long_symbol") or "").upper()
        if "UPSIDE" not in setup or "C" not in symbol:
            continue
        if mark is None or mark * 100 > max_contract_cost:
            continue
        if distance is not None and distance > 3.5:
            continue
        if delta is not None and delta < 0.25:
            continue
        if width is not None and width > 45:
            continue
        candidates.append(row)
    candidates.sort(
        key=lambda r: (
            0 if "QUAD" in str(r.get("setup") or "").upper() else 1,
            num(r.get("rank")) or 999,
            num(r.get("target_distance_pct")) or 999,
        )
    )
    opened = []
    for row in candidates[:2]:
        opened.append(open_long_symbol(
            db_path=db_path,
            book=book,
            ticker=str(row["ticker"]).upper(),
            direction="up",
            expiry=str(payload.get("expiry")),
            long_symbol=str(row["long_symbol"]).upper(),
            quantity=quantity,
            target_underlying=num(row.get("target")),
            thesis="Dynamic golden-spot call forward test from latest scan-time option marks.",
        ))
    return opened


def main() -> int:
    parser = argparse.ArgumentParser(description="Local paper ledger for option setup tracking.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    sub = parser.add_subparsers(dest="cmd", required=True)
    seed = sub.add_parser("seed-current-best")
    seed.add_argument("--book", default=DEFAULT_BOOK)
    seed.add_argument("--quantity", type=int, default=1)
    golden = sub.add_parser("seed-golden-calls")
    golden.add_argument("--book", default="golden_spot_calls_20260722")
    golden.add_argument("--quantity", type=int, default=1)
    golden.add_argument("--max-contract-cost", type=float, default=500.0)
    mark = sub.add_parser("mark")
    mark.add_argument("--book", default=DEFAULT_BOOK)
    args = parser.parse_args()

    if args.cmd == "seed-current-best":
        opened = seed_current_best(args.db, args.book, args.quantity)
        marks = mark_all(args.db, args.book)
        latest_path = write_latest(args.db, args.book, marks)
        print(json.dumps({"opened": opened, "marks": marks, "latest_path": str(latest_path), "db": str(args.db)}, indent=2))
        return 0
    if args.cmd == "seed-golden-calls":
        opened = seed_golden_calls(args.db, args.book, args.quantity, args.max_contract_cost)
        marks = mark_all(args.db, args.book)
        latest_path = write_latest(args.db, args.book, marks)
        print(json.dumps({"opened": opened, "marks": marks, "latest_path": str(latest_path), "db": str(args.db)}, indent=2))
        return 0
    marks = mark_all(args.db, args.book)
    latest_path = write_latest(args.db, args.book, marks)
    print(json.dumps({"marks": marks, "latest_path": str(latest_path), "db": str(args.db)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
