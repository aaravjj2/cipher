"""Read-only monitor for manually entered option positions.

This script records live mark-to-mid snapshots for a known spread/contract. It
does not connect to a broker trading endpoint and cannot place or close orders.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CORE_DIR = Path(__file__).resolve().parent
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

import app as cipher_app


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
POSITION_DIR = DATA / "positions"
DEFAULT_DB = POSITION_DIR / "position_monitor.sqlite"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def num(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def ensure_schema(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as db:
        db.executescript(
            """
            create table if not exists position_snapshots (
                id integer primary key autoincrement,
                position_id text not null,
                captured_at text not null,
                ticker text not null,
                spot real,
                spread_mark real,
                avg_cost real,
                quantity integer,
                market_value real,
                pnl_dollars real,
                pnl_pct real,
                long_mid real,
                short_mid real,
                long_bid real,
                long_ask real,
                short_bid real,
                short_ask real,
                long_delta real,
                short_delta real,
                net_delta real,
                long_theta real,
                short_theta real,
                net_theta real,
                status text,
                payload_json text not null
            );

            create index if not exists idx_position_snapshots_position_time
                on position_snapshots(position_id, captured_at);
            """
        )


def load_position(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def save_position(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def find_contract(chain: list[dict[str, Any]], symbol: str) -> dict[str, Any] | None:
    symbol = symbol.upper()
    for contract in chain:
        if str(contract.get("symbol") or "").upper() == symbol:
            return contract
    return None


def spread_pct(bid: float | None, ask: float | None, mid: float | None) -> float | None:
    if bid is None or ask is None or mid is None or mid <= 0 or ask < bid:
        return None
    return (ask - bid) / mid * 100.0


def status_flags(position: dict[str, Any], spot: float | None, spread_mark: float | None, pnl_pct: float | None) -> list[str]:
    flags = []
    target = num(position.get("setup_target"))
    breakeven = num(position.get("breakeven"))
    stop_loss_pct = num(position.get("stop_loss_pct"))
    take_profit_pct = num(position.get("take_profit_pct"))
    if spot is not None and target is not None and spot >= target:
        flags.append("underlying_at_or_above_target")
    if spot is not None and breakeven is not None and spot >= breakeven:
        flags.append("underlying_above_breakeven")
    if pnl_pct is not None and take_profit_pct is not None and pnl_pct >= take_profit_pct:
        flags.append("profit_target_reached")
    if pnl_pct is not None and stop_loss_pct is not None and pnl_pct <= -abs(stop_loss_pct):
        flags.append("loss_limit_hit")
    if spread_mark is not None and spread_mark <= 0.05:
        flags.append("spread_near_zero")
    return flags or ["tracking"]


def monitor_once(position_path: Path, db_path: Path, feed: str, max_pages: int) -> dict[str, Any]:
    position = load_position(position_path)
    ticker = str(position["ticker"]).upper()
    long_symbol = str(position["long_symbol"]).upper()
    short_symbol = str(position["short_symbol"]).upper()
    avg_cost = num(position.get("avg_cost"))
    quantity = int(position.get("quantity") or 1)
    chain = cipher_app.option_chain(ticker, feed, force=True, max_pages=max_pages)
    long = find_contract(chain, long_symbol)
    short = find_contract(chain, short_symbol)
    if not long or not short:
        missing = []
        if not long:
            missing.append(long_symbol)
        if not short:
            missing.append(short_symbol)
        raise RuntimeError(f"Missing contracts in chain: {', '.join(missing)}")
    quote_fn = getattr(cipher_app, "stock_quote", None) or getattr(cipher_app, "_stock_quote")
    quote = quote_fn(ticker, position.get("stock_feed") or "sip") or {}
    spot = num(quote.get("price_context") or quote.get("price"))
    long_mid = num(long.get("mid"))
    short_mid = num(short.get("mid"))
    spread_mark = (long_mid - short_mid) if long_mid is not None and short_mid is not None else None
    market_value = spread_mark * 100 * quantity if spread_mark is not None else None
    cost_value = avg_cost * 100 * quantity if avg_cost is not None else None
    pnl_dollars = market_value - cost_value if market_value is not None and cost_value is not None else None
    pnl_pct = pnl_dollars / cost_value * 100 if pnl_dollars is not None and cost_value else None
    net_delta = (num(long.get("delta")) or 0.0) - (num(short.get("delta")) or 0.0)
    net_theta = (num(long.get("theta")) or 0.0) - (num(short.get("theta")) or 0.0)
    payload = {
        "position_id": position["id"],
        "captured_at": now_utc(),
        "ticker": ticker,
        "spot": spot,
        "spread_mark": spread_mark,
        "avg_cost": avg_cost,
        "quantity": quantity,
        "market_value": market_value,
        "pnl_dollars": pnl_dollars,
        "pnl_pct": pnl_pct,
        "breakeven": position.get("breakeven"),
        "setup_target": position.get("setup_target"),
        "long_contract": long,
        "short_contract": short,
        "long_spread_pct": spread_pct(num(long.get("bid")), num(long.get("ask")), long_mid),
        "short_spread_pct": spread_pct(num(short.get("bid")), num(short.get("ask")), short_mid),
        "net_delta": net_delta,
        "net_theta": net_theta,
        "status_flags": status_flags(position, spot, spread_mark, pnl_pct),
        "caveat": "Read-only mark-to-mid monitor. Verify broker quote before acting.",
    }
    ensure_schema(db_path)
    with sqlite3.connect(db_path) as db:
        db.execute(
            """
            insert into position_snapshots (
                position_id, captured_at, ticker, spot, spread_mark, avg_cost, quantity,
                market_value, pnl_dollars, pnl_pct, long_mid, short_mid, long_bid,
                long_ask, short_bid, short_ask, long_delta, short_delta, net_delta,
                long_theta, short_theta, net_theta, status, payload_json
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["position_id"],
                payload["captured_at"],
                ticker,
                spot,
                spread_mark,
                avg_cost,
                quantity,
                market_value,
                pnl_dollars,
                pnl_pct,
                long_mid,
                short_mid,
                num(long.get("bid")),
                num(long.get("ask")),
                num(short.get("bid")),
                num(short.get("ask")),
                num(long.get("delta")),
                num(short.get("delta")),
                net_delta,
                num(long.get("theta")),
                num(short.get("theta")),
                net_theta,
                ",".join(payload["status_flags"]),
                json.dumps(payload, separators=(",", ":"), default=str),
            ),
        )
    out_dir = POSITION_DIR / position["id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    latest_path = out_dir / "latest.json"
    latest_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    snap_path = out_dir / f"{payload['captured_at'].replace(':', '').replace('+', '')}.json"
    snap_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return {"latest_path": str(latest_path), "snapshot_path": str(snap_path), "db": str(db_path), "payload": payload}


def create_baba_position(path: Path) -> dict[str, Any]:
    payload = {
        "id": "baba_20260724_118_120_call_debit_spread",
        "ticker": "BABA",
        "opened_at": "2026-07-22",
        "strategy": "call_debit_spread",
        "long_symbol": "BABA260724C00118000",
        "short_symbol": "BABA260724C00120000",
        "long_leg": {"type": "call", "expiry": "2026-07-24", "strike": 118, "action": "buy", "quantity": 1},
        "short_leg": {"type": "call", "expiry": "2026-07-24", "strike": 120, "action": "sell", "quantity": 1},
        "avg_cost": 0.56,
        "quantity": 1,
        "breakeven": 118.56,
        "setup_target": 120.0,
        "initial_underlying": 116.61,
        "take_profit_pct": 30.0,
        "stop_loss_pct": 35.0,
        "notes": "Manual Robinhood entry supplied by user. Read-only monitoring only.",
    }
    save_position(path, payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only option position monitor.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    create = sub.add_parser("create-baba", help="Create the confirmed BABA spread position file.")
    create.add_argument("--path", type=Path, default=POSITION_DIR / "baba_20260724_118_120_call_debit_spread.json")
    mon = sub.add_parser("monitor", help="Capture one live monitor snapshot.")
    mon.add_argument("--position", type=Path, required=True)
    mon.add_argument("--db", type=Path, default=DEFAULT_DB)
    mon.add_argument("--feed", default="opra", choices=("opra", "indicative"))
    mon.add_argument("--max-pages", type=int, default=12)
    args = parser.parse_args()
    if args.cmd == "create-baba":
        payload = create_baba_position(args.path)
        print(json.dumps({"position_path": str(args.path), "position": payload}, indent=2))
        return 0
    result = monitor_once(args.position, args.db, args.feed, args.max_pages)
    slim = {k: v for k, v in result["payload"].items() if k not in {"long_contract", "short_contract"}}
    print(json.dumps({**slim, "latest_path": result["latest_path"], "db": result["db"]}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
