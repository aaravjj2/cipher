"""Strict, local-only quote execution primitives for Theta paper trading.

This module deliberately does not mutate the historical Telegram ledger.  A
caller supplies provider-resolved contracts and a synchronized quote set;
failure of any leg rejects the complete transaction.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Mapping, Sequence


@dataclass(frozen=True)
class Contract:
    symbol: str
    underlying: str
    expiration: str
    option_type: str
    strike: float
    settlement: str


@dataclass(frozen=True)
class Quote:
    symbol: str
    bid: float
    ask: float
    observed_at: datetime
    bid_size: int | None = None
    ask_size: int | None = None


@dataclass(frozen=True)
class Leg:
    contract: Contract
    side: str


def _fresh(q: Quote, now: datetime, max_age: int) -> bool:
    if q.observed_at.tzinfo is None or now.tzinfo is None:
        return False
    return 0 <= (now.astimezone(timezone.utc) - q.observed_at.astimezone(timezone.utc)).total_seconds() <= max_age


def validate_legs(legs: Sequence[Leg], contracts: Mapping[str, Contract], quotes: Mapping[str, Quote], *, now: datetime, max_age: int = 15) -> None:
    if not 1 <= len(legs) <= 4:
        raise ValueError("Theta supports one through four explicit legs")
    timestamps = []
    if len({x.contract.symbol for x in legs}) != len(legs):
        raise ValueError("duplicate contracts or ratio structure")
    for leg in legs:
        c = contracts.get(leg.contract.symbol)
        q = quotes.get(leg.contract.symbol)
        if c is None or c != leg.contract:
            raise ValueError("contract metadata mismatch")
        if not c.settlement or c.option_type not in {"C", "P"} or not math.isfinite(c.strike) or c.strike <= 0:
            raise ValueError("incomplete contract metadata")
        if leg.side not in {"BUY", "SELL"} or q is None:
            raise ValueError("missing quote blocks complete order")
        if q.symbol != c.symbol or not _fresh(q, now, max_age):
            raise ValueError("missing or stale quote blocks complete order")
        if not all(math.isfinite(v) for v in (q.bid, q.ask)) or q.bid <= 0 or q.ask < q.bid:
            raise ValueError("invalid quote blocks complete order")
        size = q.ask_size if leg.side == "BUY" else q.bid_size
        if isinstance(size, bool) or not isinstance(size, int) or size < 1:
            raise ValueError("missing executable size")
        timestamps.append(q.observed_at.astimezone(timezone.utc))
    if (max(timestamps) - min(timestamps)).total_seconds() > 5:
        raise ValueError("unsynchronized leg quotes block complete order")


def entry(legs: Sequence[Leg], contracts: Mapping[str, Contract], quotes: Mapping[str, Quote], *, now: datetime, slippage_bps: float = 0, fee_per_contract: float = 0, max_age: int = 15) -> dict:
    if not all(math.isfinite(v) and v >= 0 for v in (slippage_bps, fee_per_contract)) or slippage_bps > 10000:
        raise ValueError("invalid execution costs")
    validate_legs(legs, contracts, quotes, now=now, max_age=max_age)
    prices = []
    for leg in legs:
        q = quotes[leg.contract.symbol]
        raw = q.ask if leg.side == "BUY" else q.bid
        slip = raw * slippage_bps / 10000
        prices.append({"symbol": q.symbol, "side": leg.side, "quantity": 1, "quote_bid": q.bid, "quote_ask": q.ask, "quote_at": q.observed_at.isoformat(), "bid_size": q.bid_size, "ask_size": q.ask_size, "fee": fee_per_contract, "slippage": round(slip, 6), "fill": round(raw + slip if leg.side == "BUY" else raw - slip, 6)})
    debit = round(sum(x["fill"] if x["side"] == "BUY" else -x["fill"] for x in prices) * 100 + fee_per_contract * len(legs), 2)
    premium = round(sum(x["fill"] if x["side"] == "BUY" else -x["fill"] for x in prices) * 100, 2)
    return {"version": "theta-quote-v1", "quantity": 1, "legs": prices, "entry_value": debit, "premium": premium, "fees": fee_per_contract * len(legs), "entry_convention": "debit" if premium >= 0 else "credit", "take_profit_pct": 50, "stop_loss_pct": 25, "quote_as_of": min(quotes[l.contract.symbol].observed_at for l in legs).isoformat()}


def exit_request(position_id: str, reason: str, *, requested_at: datetime | None = None) -> dict:
    return {"position_id": position_id, "status": "PENDING", "reason": reason, "requested_at": (requested_at or datetime.now(timezone.utc)).isoformat()}


def liquidation_pnl(opening: dict, legs: Sequence[Leg], quotes: Mapping[str, Quote], *, now: datetime, max_age: int = 15, fee_per_contract: float = 0, slippage_bps: float = 0) -> float:
    # Requiring metadata and fresh synchronized quotes also applies to exits.
    reverse = [Leg(l.contract, "SELL" if l.side == "BUY" else "BUY") for l in legs]
    fill = entry(reverse, {x.contract.symbol: x.contract for x in legs}, quotes, now=now, max_age=max_age, fee_per_contract=fee_per_contract, slippage_bps=slippage_bps)
    return round(-fill["entry_value"] - float(opening["entry_value"]), 2)


def trigger(opening: dict, pnl: float) -> str | None:
    basis = abs(float(opening.get("premium", opening["entry_value"])))
    if basis == 0:
        return None
    if pnl >= basis * .5:
        return "TP"
    if pnl <= -basis * .25:
        return "SL"
    return None
