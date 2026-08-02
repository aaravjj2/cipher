from __future__ import annotations

from datetime import datetime, timezone

from .config import ContractConfig, SimulationConfig
from .models import Quote, SimulatedFill


def slippage(price: float, cfg: SimulationConfig) -> float:
    return round(max(cfg.minimum_slippage_dollars, price * cfg.slippage_pct / 100.0), 4)


def quote_is_fresh(quote: Quote, max_age_seconds: int, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    return (now - quote.timestamp.astimezone(timezone.utc)).total_seconds() <= max_age_seconds


def simulate_entry(quote: Quote, sim: SimulationConfig, contract: ContractConfig, quantity: int, max_age_seconds: int, now: datetime | None = None) -> SimulatedFill:
    if not quote_is_fresh(quote, max_age_seconds, now):
        raise ValueError("stale quote")
    if quote.bid <= 0 or quote.ask <= quote.bid:
        raise ValueError("invalid quote")
    if quote.spread_pct > contract.maximum_spread_pct:
        raise ValueError("wide spread")
    slip = slippage(quote.ask, sim)
    fill = round(quote.ask + slip, 4)
    partial = quote.ask_size is not None and quote.ask_size < quantity
    return SimulatedFill("entry", quote.bid, quote.ask, quote.midpoint, slip, fill, quote.timestamp, min(quantity, quote.ask_size or quantity), partial)


def simulate_exit(quote: Quote, sim: SimulationConfig, contract: ContractConfig, quantity: int, max_age_seconds: int, now: datetime | None = None) -> SimulatedFill:
    if not quote_is_fresh(quote, max_age_seconds, now):
        raise ValueError("stale quote")
    if quote.bid <= 0 or quote.ask <= quote.bid:
        raise ValueError("invalid quote")
    if quote.spread_pct > contract.maximum_spread_pct:
        raise ValueError("wide spread")
    slip = slippage(quote.bid, sim)
    fill = round(max(0.0, quote.bid - slip), 4)
    return SimulatedFill("exit", quote.bid, quote.ask, quote.midpoint, slip, fill, quote.timestamp, quantity)


def simulate_spread_entry(long_quote: Quote, short_quote: Quote, sim: SimulationConfig, contract: ContractConfig, quantity: int, max_age_seconds: int, now: datetime | None = None) -> dict:
    long_fill = simulate_entry(long_quote, sim, contract, quantity, max_age_seconds, now)
    if not quote_is_fresh(short_quote, max_age_seconds, now):
        raise ValueError("stale quote")
    if short_quote.bid <= 0 or short_quote.ask <= short_quote.bid:
        raise ValueError("invalid quote")
    if short_quote.spread_pct > contract.maximum_spread_pct:
        raise ValueError("wide spread")
    short_slip = slippage(short_quote.bid, sim)
    short_fill_price = round(max(0.0, short_quote.bid - short_slip), 4)
    debit = round(long_fill.fill_price - short_fill_price, 4)
    if debit <= 0:
        raise ValueError("invalid spread debit")
    return {
        "side": "entry",
        "quantity": min(long_fill.quantity, short_quote.bid_size or quantity),
        "fill_price": debit,
        "long_fill": long_fill,
        "short_fill": SimulatedFill("entry_short", short_quote.bid, short_quote.ask, short_quote.midpoint, short_slip, short_fill_price, short_quote.timestamp, quantity),
    }


def simulate_spread_exit(long_quote: Quote, short_quote: Quote, sim: SimulationConfig, contract: ContractConfig, quantity: int, max_age_seconds: int, now: datetime | None = None) -> dict:
    long_fill = simulate_exit(long_quote, sim, contract, quantity, max_age_seconds, now)
    if not quote_is_fresh(short_quote, max_age_seconds, now):
        raise ValueError("stale quote")
    if short_quote.bid <= 0 or short_quote.ask <= short_quote.bid:
        raise ValueError("invalid quote")
    if short_quote.spread_pct > contract.maximum_spread_pct:
        raise ValueError("wide spread")
    short_slip = slippage(short_quote.ask, sim)
    short_fill_price = round(short_quote.ask + short_slip, 4)
    credit = round(max(0.0, long_fill.fill_price - short_fill_price), 4)
    return {
        "side": "exit",
        "quantity": quantity,
        "fill_price": credit,
        "long_fill": long_fill,
        "short_fill": SimulatedFill("exit_short", short_quote.bid, short_quote.ask, short_quote.midpoint, short_slip, short_fill_price, short_quote.timestamp, quantity),
    }
