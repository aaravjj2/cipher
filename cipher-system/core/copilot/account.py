"""Read-only Alpaca paper-account views for Cipher Copilot.

All broker access goes through the sanctioned single touchpoint
(`core/paper_executor/alpaca_paper_broker.py`), which is the only file in the
tree permitted to name order endpoints. This module therefore contains no
endpoint strings and no submission path of its own - it calls the adapter's
read methods (account/orders/positions) and adds local arithmetic: FIFO
round-trip realization with option-contract dollar scaling.

Every failure is returned as data (`{"error": ...}`), matching the copilot's
dispatch contract.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from core.copilot.tools import env_key

# OCC option symbols settle at $100/point; equities at $1.
_OCC_OPTION_RE = re.compile(r"^[A-Z0-9]{1,6}\d{6}[CP]\d{8}$")


def _contract_multiplier(symbol: str) -> int:
    return 100 if _OCC_OPTION_RE.match(symbol or "") else 1


def _broker():
    from core.paper_executor.alpaca_paper_broker import AlpacaPaperBroker, PAPER_BASE_URL

    key = env_key("ALPACA_PAPER_API_KEY") or env_key("ALPACA_ALGO_KEY") or env_key("ALPACA_ALGO_PLUS_KEY") or ""
    secret = (
        env_key("ALPACA_PAPER_API_SECRET")
        or env_key("ALPACA_ALGO_SECRET")
        or env_key("ALPACA_ALGO_PLUS_SECRET")
        or ""
    )
    if not key or not secret:
        raise ValueError("no Alpaca credentials configured (ALPACA_ALGO_KEY/SECRET)")
    # Explicit paper-host pin: mirrors the adapter's own guard so a future
    # credential reshuffle can never silently retarget these reads.
    return AlpacaPaperBroker(key, secret, base_url=PAPER_BASE_URL)


def _stamp(source: str, payload: dict) -> dict:
    out = {
        "source": source,
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note": "read-only view of the Alpaca PAPER account; this surface can never place orders",
    }
    out.update(payload)
    return out


def get_account() -> dict:
    try:
        raw = _broker().account()
    except Exception as exc:  # noqa: BLE001 - errors are data downstream
        return {"error": f"{type(exc).__name__}: {exc}"}
    return _stamp(
        "alpaca_paper_account",
        {
            "account_number": raw.get("id"),
            "status": raw.get("status"),
            "equity": raw.get("equity"),
            "buying_power": raw.get("buying_power"),
            "trading_blocked": raw.get("trading_blocked"),
            "account_blocked": raw.get("account_blocked"),
            # The adapter's normalized account payload carries no cash/PDT
            # fields; disclosed rather than fabricated.
            "cash": None,
            "cash_note": "not exposed by the paper adapter's normalized account view",
        },
    )


def get_positions() -> dict:
    try:
        raw = _broker().positions()
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}
    rows = []
    for pos in raw or []:
        symbol = pos.get("symbol") or ""
        rows.append(
            {
                "symbol": symbol,
                "qty": pos.get("quantity"),
                "side": pos.get("side"),
                "avg_entry_price": pos.get("average_entry_price"),
                "market_value": pos.get("market_value"),
                "unrealized_pl": pos.get("unrealized_pl"),
                "is_option": bool(_OCC_OPTION_RE.match(symbol)),
            }
        )
    return _stamp("alpaca_paper_positions", {"position_count": len(rows), "positions": rows})


def get_orders(status: str = "all", limit: int = 25) -> dict:
    try:
        raw = _broker().orders(status=status, limit=min(max(int(limit), 1), 500))
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}
    rows = [
        {
            "submitted_at": o.get("submitted_at"),
            "filled_at": o.get("filled_at"),
            "symbol": o.get("symbol"),
            "side": o.get("side"),
            "qty": o.get("filled_quantity") if o.get("filled_quantity") else o.get("quantity"),
            "limit_price": o.get("limit_price"),
            "average_fill_price": o.get("average_fill_price"),
            "status": o.get("status"),
            "client_order_id": (o.get("client_order_id") or "")[:32],
        }
        for o in raw or []
    ]
    return _stamp("alpaca_paper_orders", {"order_count": len(rows), "orders": rows})


def get_trade_history(days: int = 7, *, now: datetime | None = None) -> dict:
    """FIFO round-trip realization over recent closed orders. Options scale to
    dollars at x100; equities at x1."""
    window_days = max(1, min(int(days), 90))
    since = ((now or datetime.now(timezone.utc)) - timedelta(days=window_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        raw = _broker().orders(status="closed", limit=500)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}

    fills = []
    for o in raw or []:
        price = o.get("average_fill_price")
        qty = o.get("filled_quantity")
        created = str(o.get("filled_at") or o.get("submitted_at") or "")
        if not price or not qty or float(qty) <= 0 or created[:10] < since[:10]:
            continue
        side = str(o.get("side") or "").lower()
        if side not in {"buy", "sell"}:
            continue
        fills.append({"symbol": o.get("symbol"), "side": side, "qty": float(qty), "price": float(price), "at": created})

    books: dict[str, list[dict]] = {}
    trades: list[dict] = []
    for fill in sorted(fills, key=lambda f_: (f_["at"], f_["symbol"])):
        book = books.setdefault(fill["symbol"], [])
        if not book or book[-1]["side"] == fill["side"]:
            book.append(dict(fill))
            continue
        remaining = fill["qty"]
        while remaining > 0 and book:
            lot = book[0]
            take = min(remaining, lot["qty"])
            sign = 1.0 if lot["side"] == "buy" else -1.0
            unit_pnl = sign * (fill["price"] - lot["price"]) * take
            mult = _contract_multiplier(fill["symbol"])
            trades.append(
                {
                    "symbol": fill["symbol"],
                    "opened_at": lot["at"],
                    "closed_at": fill["at"],
                    "qty": take,
                    "entry": lot["price"],
                    "exit": fill["price"],
                    "realized_pnl": round(unit_pnl, 2),
                    "realized_pnl_dollars": round(unit_pnl * mult, 2),
                }
            )
            lot["qty"] -= take
            remaining -= take
            if lot["qty"] <= 1e-9:
                book.pop(0)
        if remaining > 0:
            book.append({"side": fill["side"], "qty": remaining, "price": fill["price"], "at": fill["at"]})

    wins = [t for t in trades if t["realized_pnl_dollars"] > 0]
    return _stamp(
        "alpaca_paper_fills_fifo",
        {
            "window_days": window_days,
            "closed_trades": len(trades),
            "realized_pnl_total": round(sum(t["realized_pnl"] for t in trades), 2),
            "realized_pnl_dollars_total": round(sum(t["realized_pnl_dollars"] for t in trades), 2),
            "note": "options settle at $100/point; realized_pnl is per-unit, *_dollars is cash impact",
            "win_rate": round(len(wins) / len(trades), 3) if trades else None,
            "open_lots_left": {sym: sum(lot["qty"] for lot in lots) for sym, lots in books.items() if lots},
            "trades": trades[-40:],
        },
    )
