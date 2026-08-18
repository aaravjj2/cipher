"""A manually-entered position tracker. Never connects to a real brokerage account
and never places an order — the user tells it what they already hold (ticker,
shares, entry price, entry date), and it marks that against real market data this
service already fetches (the same quote()/bars() every other panel uses).

Positions are the one thing here with no real-data source to read from, so unlike
governance_status()/standing_status() (which only ever read other subsystems'
existing databases), this module owns real read-write storage: a JSON file under
data/holdings/, written atomically so a reader never observes a half-written file.

Pricing functions are injected (quote_fn/bars_fn) rather than imported from app.py,
so this module has no dependency on Alpaca credentials or the HTTP server and can
be exercised with fake pricing in isolation.

Research only. No broker/account/order APIs are imported or called.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "holdings"
POSITIONS_PATH = DATA_DIR / "positions.json"
SCHEMA_VERSION = 1

_LOCK = threading.Lock()

TICKER_RE = re.compile(r"^[A-Z]{1,6}(\.[A-Z]{1,2})?$")

CAVEAT = (
    "User-declared positions entered manually here — never synced with a real "
    "brokerage or exchange account, and no orders are ever placed or possible from "
    "this app. Prices are mark-to-market snapshots from the same delayed/free market "
    "data every other panel uses, not an official broker statement; verify against "
    "your actual account before relying on any figure shown here."
)


class HoldingsError(ValueError):
    """A rejected holdings request. Subclasses ValueError so app.py's existing
    `except ValueError as exc: send_json(422, ...)` handles it with no new plumbing."""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _validate_ticker(raw: Any) -> str:
    ticker = str(raw or "").strip().upper()
    if not TICKER_RE.match(ticker):
        raise HoldingsError(f"'{raw}' is not a valid ticker symbol")
    return ticker


def _validate_positive_number(raw: Any, field_name: str) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise HoldingsError(f"{field_name} must be a number") from None
    if not (value > 0):
        raise HoldingsError(f"{field_name} must be greater than zero")
    return value


def _validate_date(raw: Any, field_name: str, *, allow_future: bool = False) -> str:
    try:
        parsed = date.fromisoformat(str(raw or "").strip())
    except ValueError:
        raise HoldingsError(f"{field_name} must be an ISO date (YYYY-MM-DD)") from None
    if not allow_future and parsed > _today():
        raise HoldingsError(f"{field_name} cannot be in the future")
    return parsed.isoformat()


def _load() -> dict:
    if not POSITIONS_PATH.is_file():
        return {"schema_version": SCHEMA_VERSION, "positions": []}
    # A parse failure is not swallowed: silently resetting a user's manually-entered
    # portfolio on a read error is a worse failure mode than a loud one they can act on.
    with POSITIONS_PATH.open("r", encoding="utf-8") as handle:
        store = json.load(handle)
    store.setdefault("positions", [])
    return store


def _save(store: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(DATA_DIR), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(store, handle, indent=2, default=str)
        os.replace(tmp, POSITIONS_PATH)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _find(store: dict, position_id: str) -> dict:
    for row in store["positions"]:
        if row["id"] == position_id:
            return row
    raise HoldingsError(f"no position with id {position_id!r}")


def add_position(
    ticker: str, shares: Any, entry_price: Any, entry_date: str, notes: str | None = None,
    *, repository=None,
) -> dict:
    ticker = _validate_ticker(ticker)
    shares = _validate_positive_number(shares, "shares")
    entry_price = _validate_positive_number(entry_price, "entry_price")
    entry_date = _validate_date(entry_date, "entry_date")
    now = _utcnow()
    record = {
        "id": uuid.uuid4().hex,
        "ticker": ticker,
        "shares": shares,
        "entry_price": entry_price,
        "entry_date": entry_date,
        "status": "OPEN",
        "notes": (str(notes).strip() or None) if notes else None,
        "exit_price": None,
        "exit_date": None,
        "closed_from_id": None,
        "created_at": now,
        "updated_at": now,
    }
    if repository is not None:
        rows = repository.insert_row("holdings", record) or []
        if not rows:
            raise HoldingsError("holding was not saved")
        return dict(rows[0])
    with _LOCK:
        store = _load()
        store["positions"].append(record)
        _save(store)
    return dict(record)


def close_position(
    position_id: str, exit_price: Any, exit_date: str, shares: Any = None,
    *, repository=None,
) -> dict:
    exit_price = _validate_positive_number(exit_price, "exit_price")
    if repository is not None:
        original = repository.get_row("holdings", position_id)
        if not original or original.get("status") != "OPEN":
            raise HoldingsError(f"position {position_id!r} is already closed or unknown")
        exit_date = _validate_date(exit_date, "exit_date")
        if exit_date < str(original.get("entry_date")):
            raise HoldingsError("exit_date cannot precede entry_date")
        remaining = float(original["shares"])
        close_qty = remaining if shares is None else _validate_positive_number(shares, "shares")
        if close_qty > remaining + 1e-9:
            raise HoldingsError(f"cannot close {close_qty:g} shares; only {remaining:g} open on this position")
        now = _utcnow()
        if close_qty >= remaining - 1e-9:
            rows = repository.update_row(
                "holdings", position_id,
                {"status": "CLOSED", "exit_price": exit_price, "exit_date": exit_date, "updated_at": now},
            ) or []
            return dict(rows[0]) if rows else {"id": position_id, "status": "CLOSED"}
        repository.update_row("holdings", position_id, {"shares": remaining - close_qty, "updated_at": now})
        rows = repository.insert_row("holdings", {
            "ticker": original["ticker"], "shares": close_qty, "entry_price": original["entry_price"],
            "entry_date": original["entry_date"], "status": "CLOSED", "notes": original.get("notes"),
            "exit_price": exit_price, "exit_date": exit_date, "closed_from_id": position_id,
            "created_at": now, "updated_at": now,
        }) or []
        if not rows:
            raise HoldingsError("closed holding was not saved")
        return dict(rows[0])
    with _LOCK:
        store = _load()
        original = _find(store, position_id)
        if original["status"] != "OPEN":
            raise HoldingsError(f"position {position_id!r} is already closed")
        exit_date = _validate_date(exit_date, "exit_date")
        if exit_date < original["entry_date"]:
            raise HoldingsError("exit_date cannot precede entry_date")
        remaining = float(original["shares"])
        close_qty = remaining if shares is None else _validate_positive_number(shares, "shares")
        if close_qty > remaining + 1e-9:
            raise HoldingsError(
                f"cannot close {close_qty:g} shares; only {remaining:g} open on this position"
            )
        now = _utcnow()
        if close_qty >= remaining - 1e-9:
            # Full close: the original row itself becomes the closed record.
            original.update({
                "status": "CLOSED", "exit_price": exit_price, "exit_date": exit_date,
                "updated_at": now,
            })
            result = dict(original)
        else:
            # Partial close: shrink the open lot, and record the sold quantity as its
            # own closed row so realized P&L history is never conflated with what's
            # still open.
            original["shares"] = remaining - close_qty
            original["updated_at"] = now
            closed_row = {
                "id": uuid.uuid4().hex,
                "ticker": original["ticker"],
                "shares": close_qty,
                "entry_price": original["entry_price"],
                "entry_date": original["entry_date"],
                "status": "CLOSED",
                "notes": original.get("notes"),
                "exit_price": exit_price,
                "exit_date": exit_date,
                "closed_from_id": original["id"],
                "created_at": now,
                "updated_at": now,
            }
            store["positions"].append(closed_row)
            result = dict(closed_row)
        _save(store)
    return result


def delete_position(position_id: str, *, repository=None) -> dict:
    """Removes a record outright — for correcting a mis-entry, not for recording a
    sale (close_position keeps realized P&L history; this does not)."""
    if repository is not None:
        if not repository.get_row("holdings", position_id):
            raise HoldingsError(f"no position with id {position_id!r}")
        repository.delete_row("holdings", position_id)
        return {"deleted": True, "id": str(position_id)}
    with _LOCK:
        store = _load()
        row = _find(store, position_id)
        store["positions"] = [r for r in store["positions"] if r["id"] != position_id]
        _save(store)
    return {"deleted": True, "id": row["id"]}


def list_positions(status: str | None = None, *, repository=None) -> list[dict]:
    if repository is not None:
        rows = repository.list_rows("holdings", query={"status": f"eq.{status}"} if status else {}) or []
        return [dict(row) for row in rows]
    with _LOCK:
        store = _load()
    rows = store["positions"]
    if status:
        rows = [r for r in rows if r["status"] == status]
    return [dict(r) for r in rows]


def _close_on_or_after(bars: list[dict], target: str) -> float | None:
    for bar in bars:
        if str(bar.get("time", ""))[:10] >= target:
            return bar.get("close")
    return bars[-1].get("close") if bars else None


def holdings_status(
    quote_fn: Callable[[str], dict],
    bars_fn: Callable[..., dict] | None = None,
    include_benchmark: bool = False,
    benchmarks: tuple[str, ...] = ("SPY", "QQQ"),
    *, repository=None,
) -> dict:
    positions = list_positions(repository=repository)
    open_rows, closed_rows = [], []
    quote_cache: dict[str, dict | None] = {}
    unresolved: list[str] = []

    total_cost_basis = total_market_value = total_day_change = 0.0
    total_realized = 0.0

    for row in positions:
        ticker = row["ticker"]
        if row["status"] == "OPEN":
            if ticker not in quote_cache:
                try:
                    quote_cache[ticker] = quote_fn(ticker)
                except Exception as exc:  # noqa: BLE001 - one bad quote must not fail the page
                    quote_cache[ticker] = None
                    if ticker not in unresolved:
                        unresolved.append(ticker)
            q = quote_cache[ticker]
            shares, entry_price = row["shares"], row["entry_price"]
            cost_basis = shares * entry_price
            out = dict(row)
            out["cost_basis"] = cost_basis
            if q and q.get("price_context") is not None:
                price = float(q["price_context"])
                market_value = shares * price
                pnl_dollars = market_value - cost_basis
                day_change = shares * (price - float(q.get("prior_close") or price))
                out.update({
                    "current_price": price,
                    "price_as_of": q.get("as_of"),
                    "market_value": market_value,
                    "unrealized_pnl_dollars": pnl_dollars,
                    "unrealized_pnl_pct": (pnl_dollars / cost_basis * 100.0) if cost_basis else None,
                    "day_change_dollars": day_change,
                    "quote_error": None,
                })
                total_cost_basis += cost_basis
                total_market_value += market_value
                total_day_change += day_change
            else:
                out.update({
                    "current_price": None, "price_as_of": None, "market_value": None,
                    "unrealized_pnl_dollars": None, "unrealized_pnl_pct": None,
                    "day_change_dollars": None, "quote_error": "quote unavailable",
                })
            open_rows.append(out)
        else:
            shares, entry_price = row["shares"], row["entry_price"]
            cost_basis = shares * entry_price
            proceeds = shares * row["exit_price"]
            realized = proceeds - cost_basis
            out = dict(row)
            out.update({
                "cost_basis": cost_basis, "proceeds": proceeds,
                "realized_pnl_dollars": realized,
                "realized_pnl_pct": (realized / cost_basis * 100.0) if cost_basis else None,
            })
            total_realized += realized
            closed_rows.append(out)

    allocation = [
        {
            "ticker": r["ticker"],
            "market_value": r["market_value"],
            "weight_pct": (r["market_value"] / total_market_value * 100.0) if total_market_value else 0.0,
        }
        for r in open_rows if r.get("market_value") is not None
    ]

    benchmark = None
    if include_benchmark and bars_fn is not None:
        open_with_dates = [r for r in open_rows if r.get("market_value") is not None]
        if open_with_dates:
            oldest = min(r["entry_date"] for r in open_with_dates)
            comparisons = []
            for bench_ticker in benchmarks:
                try:
                    series = (bars_fn(bench_ticker, "1d", limit=1000, start=oldest) or {}).get("bars") or []
                except Exception:  # noqa: BLE001 - a bad benchmark fetch must not fail holdings
                    series = []
                if not series:
                    continue
                hypothetical_value = 0.0
                for r in open_with_dates:
                    entry_close = _close_on_or_after(series, r["entry_date"])
                    if not entry_close:
                        continue
                    hyp_shares = r["cost_basis"] / entry_close
                    hypothetical_value += hyp_shares * series[-1]["close"]
                if hypothetical_value:
                    pnl = hypothetical_value - total_cost_basis
                    comparisons.append({
                        "ticker": bench_ticker,
                        "hypothetical_value": hypothetical_value,
                        "hypothetical_pnl_dollars": pnl,
                        "hypothetical_pnl_pct": (pnl / total_cost_basis * 100.0) if total_cost_basis else None,
                    })
            if comparisons:
                benchmark = {
                    "since": oldest,
                    "actual_market_value": total_market_value,
                    "comparisons": comparisons,
                }

    return {
        "as_of": _utcnow(),
        "read_only": True,
        "caveat": CAVEAT,
        "open_positions": open_rows,
        "closed_positions": closed_rows,
        "summary": {
            "open_position_count": len(open_rows),
            "closed_position_count": len(closed_rows),
            "total_cost_basis_open": total_cost_basis,
            "total_market_value_open": total_market_value,
            "unresolved_tickers": unresolved,
            "total_unrealized_pnl_dollars": (total_market_value - total_cost_basis) if total_cost_basis else 0.0,
            "total_unrealized_pnl_pct": (
                (total_market_value - total_cost_basis) / total_cost_basis * 100.0
            ) if total_cost_basis else None,
            "total_day_change_dollars": total_day_change,
            "total_realized_pnl_dollars": total_realized,
        },
        "allocation": allocation,
        "benchmark": benchmark,
    }
