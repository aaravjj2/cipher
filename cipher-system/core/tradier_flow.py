"""Truthful trader-facing option timesales from the captured Tradier stream.

The chain snapshot in :mod:`core.app` contains one latest trade per listed
contract.  Combining those records is useful as a coverage fallback, but it is
not a tape: the records can come from different sessions and its bid/ask is not
necessarily the quote that accompanied the trade.  This module reads the narrow
``tradier_option_timesales`` projection populated by ``tradier_stream_capture``.

It is deliberately read-only and bounded.  It never calls a provider and has no
account or order capability.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
from typing import Any


DEFAULT_DB = Path("/home/aarav/Aarav/cipher/runtime/data/tradier_stream.sqlite")
MAX_QUERY_ROWS = 20_000
MAX_RESPONSE_ROWS = 400


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _side(price: float | None, bid: float | None, ask: float | None) -> str:
    """Classify only when the event-time quote supports the claim."""
    if price is None or bid is None or ask is None or bid < 0 or ask <= bid:
        return "unknown"
    # A small tolerance absorbs sub-penny serialization without turning every
    # inside-spread print into a directional assertion.
    tolerance = max((ask - bid) * 0.08, 0.0001)
    if price >= ask - tolerance:
        return "buy"
    if price <= bid + tolerance:
        return "sell"
    return "unknown"


def _normalize_option_type(value: str) -> str:
    lowered = str(value or "all").lower()
    if lowered in {"call", "calls", "c"}:
        return "call"
    if lowered in {"put", "puts", "p"}:
        return "put"
    return "all"


def _normalize_side(value: str) -> str:
    lowered = str(value or "all").lower()
    if lowered in {"buy", "ask", "bought"}:
        return "buy"
    if lowered in {"sell", "bid", "sold"}:
        return "sell"
    return "all"


def _has_projection(db: sqlite3.Connection) -> bool:
    return db.execute(
        "select 1 from sqlite_master where type='table' and name='tradier_option_timesales'"
    ).fetchone() is not None


def latest_session(ticker: str, *, db_path: Path = DEFAULT_DB) -> dict[str, Any] | None:
    """Return the latest captured session containing projected timesales."""
    symbol = str(ticker or "").strip().upper()
    if not symbol or not db_path.exists():
        return None
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0) as db:
        if not _has_projection(db):
            return None
        row = db.execute(
            """
            select session_date, min(provider_ts), max(provider_ts), count(*),
                   count(distinct symbol)
            from tradier_option_timesales
            where underlying = ?
              and session_date = (
                  select max(session_date) from tradier_option_timesales where underlying = ?
              )
            group by session_date
            """,
            (symbol, symbol),
        ).fetchone()
    if not row:
        return None
    return {
        "session_date": row[0],
        "oldest_event_at": row[1],
        "newest_event_at": row[2],
        "events": int(row[3] or 0),
        "contracts": int(row[4] or 0),
    }


def flow(
    ticker: str,
    *,
    spot: float | None,
    min_premium: float = 5_000,
    max_price: float | None = None,
    option_type: str = "all",
    side: str = "all",
    moneyness: str = "all",
    limit: int = 150,
    db_path: Path = DEFAULT_DB,
) -> dict[str, Any] | None:
    """Read a single captured session without mixing dates.

    ``None`` means the projection has no usable captured session for this
    underlying, so the caller may use a clearly-labelled fallback.
    """
    symbol = str(ticker or "").strip().upper()
    session = latest_session(symbol, db_path=db_path)
    if not session:
        return None

    wanted_type = _normalize_option_type(option_type)
    wanted_side = _normalize_side(side)
    wanted_money = str(moneyness or "all").lower()
    response_limit = max(1, min(int(limit), MAX_RESPONSE_ROWS))
    query_limit = min(MAX_QUERY_ROWS, max(response_limit * 30, 2_000))

    clauses = ["underlying = ?", "session_date = ?", "premium >= ?"]
    args: list[Any] = [symbol, session["session_date"], float(min_premium)]
    if max_price is not None:
        clauses.append("price <= ?")
        args.append(float(max_price))
    if wanted_type != "all":
        clauses.append("option_type = ?")
        args.append(wanted_type)

    sql = f"""
        select stream_event_id, captured_at, provider_ts, symbol,
               option_expiration, option_type, strike, bid, ask, price,
               size, premium, exchange
        from tradier_option_timesales
        where {' and '.join(clauses)}
        order by provider_ts desc, stream_event_id desc
        limit ?
    """
    args.append(query_limit)

    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0) as db:
        db.row_factory = sqlite3.Row
        rows = [dict(row) for row in db.execute(sql, args).fetchall()]

    prints: list[dict[str, Any]] = []
    for row in rows:
        price = float(row["price"]) if row.get("price") is not None else None
        bid = float(row["bid"]) if row.get("bid") is not None else None
        ask = float(row["ask"]) if row.get("ask") is not None else None
        inferred = _side(price, bid, ask)
        if wanted_side != "all" and inferred != wanted_side:
            continue
        strike = float(row["strike"]) if row.get("strike") is not None else None
        kind = str(row.get("option_type") or "")
        is_otm: bool | None = None
        otm_pct: float | None = None
        if spot and spot > 0 and strike is not None:
            is_otm = strike > spot if kind == "call" else strike < spot
            otm_pct = (strike / spot - 1.0) * 100.0
            if kind == "put":
                otm_pct = -otm_pct
        if wanted_money == "otm" and is_otm is not True:
            continue
        if wanted_money == "itm" and is_otm is not False:
            continue
        premium = float(row["premium"]) if row.get("premium") is not None else 0.0
        prints.append(
            {
                "ticker": symbol,
                "contract": row["symbol"],
                "time": row.get("provider_ts") or row.get("captured_at"),
                "captured_at": row.get("captured_at"),
                "premium": premium,
                "size": int(row["size"]) if row.get("size") is not None else 0,
                "price": price,
                "strike": strike,
                "expiration": row.get("option_expiration"),
                "type": kind,
                "bid": bid,
                "ask": ask,
                "side": inferred,
                "side_basis": "event_time_bid_ask" if inferred != "unknown" else "unclassified",
                "tier": (
                    "Whale" if premium >= 500_000 else
                    "Large" if premium >= 150_000 else
                    "Medium" if premium >= 50_000 else
                    "Small"
                ),
                "otm_pct": otm_pct,
                "exchange": row.get("exchange"),
                "feed": "tradier_stream",
                "session_date": session["session_date"],
            }
        )
        if len(prints) >= response_limit:
            break

    newest = max((p["time"] for p in prints if p.get("time")), default=session["newest_event_at"])
    oldest = min((p["time"] for p in prints if p.get("time")), default=session["oldest_event_at"])
    parsed_newest = _parse_time(newest)
    age_seconds = (
        max(0.0, (datetime.now(timezone.utc) - parsed_newest).total_seconds())
        if parsed_newest else None
    )
    return {
        "ticker": symbol,
        "generated_at": _utcnow(),
        "as_of": newest,
        "oldest_event_at": oldest,
        "newest_event_at": newest,
        "event_age_seconds": age_seconds,
        "session_date": session["session_date"],
        "source": "tradier_stream",
        "capture_mode": "event_timesales",
        "feed": "tradier",
        "min_premium": float(min_premium),
        "count": len(prints),
        "prints": prints,
        "query_truncated": len(rows) >= query_limit,
        "coverage": {
            "captured_events": session["events"],
            "captured_contracts": session["contracts"],
            "scope": "selected streamed option contracts; not the full listed chain",
        },
        "caveat": (
            "Timesales are real captured stream events for one session and only the selected "
            "streamed contracts. Buy/sell is inferred only when the bid/ask carried by that "
            "timesale supports it; otherwise side is unknown."
        ),
        "read_only": True,
    }


def backfill_session(session_date: str, *, db_path: Path = DEFAULT_DB) -> int:
    """Idempotently project one already-captured UTC/RTH session."""
    start = datetime.fromisoformat(session_date).replace(tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    with sqlite3.connect(db_path, timeout=30.0) as db:
        if not _has_projection(db):
            return 0
        before = db.total_changes
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
                coalesce(last, price) * size * 100.0,
                json_extract(raw_json, '$.exch')
            from tradier_stream_events
            where event_type = 'timesale' and asset_class = 'option'
              and captured_at >= ? and captured_at < ?
              and substr(coalesce(provider_ts, captured_at), 1, 10) = ?
              and symbol is not null and underlying is not null
              and coalesce(last, price) is not null and size is not null
            """,
            (start.isoformat(), end.isoformat(), session_date),
        )
        return db.total_changes - before
