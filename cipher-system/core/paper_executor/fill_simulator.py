from __future__ import annotations

from datetime import datetime, timezone
import math

from .config import ContractConfig, SimulationConfig
from .models import Quote, SimulatedFill


def slippage(price: float, cfg: SimulationConfig) -> float:
    return round(max(cfg.minimum_slippage_dollars, price * cfg.slippage_pct / 100.0), 4)


def quote_is_fresh(quote: Quote, max_age_seconds: int, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    if quote.timestamp.tzinfo is None or now.tzinfo is None:
        return False
    age = (now - quote.timestamp.astimezone(timezone.utc)).total_seconds()
    return -2 <= age <= max_age_seconds


def validate_quote(quote: Quote, quantity: int, side: str, max_age_seconds: int, now=None) -> None:
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
        raise ValueError("invalid quantity")
    if not quote_is_fresh(quote, max_age_seconds, now):
        raise ValueError("stale quote")
    if not all(math.isfinite(x) for x in (quote.bid, quote.ask)) or quote.bid < 0 or quote.ask <= 0 or quote.ask < quote.bid:
        raise ValueError("invalid quote")
    size = quote.ask_size if side == "buy" else quote.bid_size
    if size is not None and (isinstance(size, bool) or not isinstance(size, int) or size < quantity):
        raise ValueError("insufficient quote size")


def validate_pair(long_quote: Quote, short_quote: Quote) -> None:
    if abs((long_quote.timestamp - short_quote.timestamp).total_seconds()) > 5:
        raise ValueError("asynchronous spread quotes")


def simulate_entry(quote: Quote, sim: SimulationConfig, contract: ContractConfig, quantity: int, max_age_seconds: int, now: datetime | None = None) -> SimulatedFill:
    validate_quote(quote, quantity, "buy", max_age_seconds, now)
    if not quote_is_fresh(quote, max_age_seconds, now):
        raise ValueError("stale quote")
    if quote.bid <= 0 or quote.ask <= quote.bid:
        raise ValueError("invalid quote")
    if quote.spread_pct > contract.maximum_spread_pct:
        raise ValueError("wide spread")
    slip = slippage(quote.ask, sim)
    fill = round(quote.ask + slip + sim.fee_per_contract / 100, 4)
    if fill * 100 * quantity > contract.maximum_contract_cost:
        raise ValueError("max cost")
    partial = quote.ask_size is not None and quote.ask_size < quantity
    return SimulatedFill("entry", quote.bid, quote.ask, quote.midpoint, slip, fill, quote.timestamp, min(quantity, quote.ask_size or quantity), partial)


def simulate_exit(quote: Quote, sim: SimulationConfig, contract: ContractConfig, quantity: int, max_age_seconds: int, now: datetime | None = None) -> SimulatedFill:
    validate_quote(quote, quantity, "sell", max_age_seconds, now)
    slip = slippage(quote.bid, sim)
    fill = round(max(0.0, quote.bid - slip) - sim.fee_per_contract / 100, 4)
    return SimulatedFill("exit", quote.bid, quote.ask, quote.midpoint, slip, fill, quote.timestamp, quantity)


def simulate_spread_entry(long_quote: Quote, short_quote: Quote, sim: SimulationConfig, contract: ContractConfig, quantity: int, max_age_seconds: int, now: datetime | None = None, *, width: float | None = None) -> dict:
    from dataclasses import replace
    validate_pair(long_quote, short_quote)
    validate_quote(short_quote, quantity, "sell", max_age_seconds, now)
    long_fill = simulate_entry(long_quote, sim, replace(contract, maximum_contract_cost=float("inf")), quantity, max_age_seconds, now)
    if not quote_is_fresh(short_quote, max_age_seconds, now):
        raise ValueError("stale quote")
    if short_quote.bid <= 0 or short_quote.ask <= short_quote.bid:
        raise ValueError("invalid quote")
    if short_quote.spread_pct > contract.maximum_spread_pct:
        raise ValueError("wide spread")
    short_slip = slippage(short_quote.bid, sim)
    short_fill_price = round(max(0.0, short_quote.bid - short_slip) - sim.fee_per_contract / 100, 4)
    debit = round(long_fill.fill_price - short_fill_price, 4)
    if debit <= 0:
        raise ValueError("invalid spread debit")
    if debit * 100 * quantity > contract.maximum_contract_cost:
        raise ValueError("max cost")
    if width is not None and debit >= width:
        raise ValueError("debit exceeds width")
    return {
        "side": "entry",
        "quantity": min(long_fill.quantity, short_quote.bid_size or quantity),
        "fill_price": debit,
        "long_fill": long_fill,
        "short_fill": SimulatedFill("entry_short", short_quote.bid, short_quote.ask, short_quote.midpoint, short_slip, short_fill_price, short_quote.timestamp, quantity),
    }


def simulate_spread_exit(long_quote: Quote, short_quote: Quote, sim: SimulationConfig, contract: ContractConfig, quantity: int, max_age_seconds: int, now: datetime | None = None) -> dict:
    validate_pair(long_quote, short_quote)
    validate_quote(short_quote, quantity, "buy", max_age_seconds, now)
    long_fill = simulate_exit(long_quote, sim, contract, quantity, max_age_seconds, now)
    if not quote_is_fresh(short_quote, max_age_seconds, now):
        raise ValueError("stale quote")
    short_slip = slippage(short_quote.ask, sim)
    short_fill_price = round(short_quote.ask + short_slip + sim.fee_per_contract / 100, 4)
    credit = round(long_fill.fill_price - short_fill_price, 4)
    return {
        "side": "exit",
        "quantity": quantity,
        "fill_price": credit,
        "long_fill": long_fill,
        "short_fill": SimulatedFill("exit_short", short_quote.bid, short_quote.ask, short_quote.midpoint, short_slip, short_fill_price, short_quote.timestamp, quantity),
    }
