"""Use-case boundary between HTTP routes and market-data provider callables.

The active provider remains Alpaca.  Keeping provider-shaped calls behind this
module prevents browser/API concerns from spreading into the calculation and
storage modules, and makes unavailable provider fields stay explicit.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Callable, Protocol

from core import option_history, options_terminal, portfolio_risk


class QuoteProvider(Protocol):
    def __call__(self, ticker: str) -> dict: ...


class ChainProvider(Protocol):
    def __call__(self, ticker: str, feed: str | None, **kwargs) -> dict: ...


def options_chain_view(ticker: str, feed: str | None, expiration_count: int, *,
                       quote_fn: QuoteProvider, chain_fn: ChainProvider,
                       force: bool = False, today: date | None = None) -> dict:
    count = max(1, min(int(expiration_count), 12))
    current = today or date.today()
    horizon = current + timedelta(days=max(45, count * 18))
    contracts = chain_fn(ticker, feed, max_pages=12, expiration_gte=current.isoformat(),
                         expiration_lte=horizon.isoformat(), force=force)
    view = options_terminal.chain_view(ticker, quote_fn(ticker), contracts, expiration_limit=count)
    history = option_history.history_status(ticker)
    view.update(history)
    return view


def portfolio_snapshot(feed: str | None, *, quote_fn: QuoteProvider,
                       chain_fn: ChainProvider) -> dict:
    return portfolio_risk.status(
        quote_fn=quote_fn,
        chain_fn=lambda symbol, start, end: chain_fn(
            symbol, feed, max_pages=12, expiration_gte=start, expiration_lte=end,
        ),
    )
