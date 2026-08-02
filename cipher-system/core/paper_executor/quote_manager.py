from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Protocol

from .config import ExecutorConfig
from .models import Quote


class MarketDataClient(Protocol):
    def quotes(self, symbols: list[str]) -> dict[str, Quote]: ...
    def expirations(self, ticker: str) -> list[str]: ...
    def chain(self, ticker: str, expiration: str) -> list[dict]: ...


class QuoteManager:
    """Single shared quote cache and subscription registry for the executor."""

    def __init__(self, cfg: ExecutorConfig, market_data: MarketDataClient):
        self.cfg = cfg
        self.market_data = market_data
        self._lock = threading.RLock()
        self._active: set[str] = set()
        self._latest: dict[str, Quote] = {}
        self._degraded = False
        self._last_error: str | None = None
        self._last_fresh_quote_at: str | None = None
        self._reconnect_attempts = 0

    @property
    def degraded(self) -> bool:
        with self._lock:
            return self._degraded

    @property
    def last_error(self) -> str | None:
        with self._lock:
            return self._last_error

    @property
    def active_symbols(self) -> list[str]:
        with self._lock:
            return sorted(self._active)

    @property
    def last_fresh_quote_at(self) -> str | None:
        with self._lock:
            return self._last_fresh_quote_at

    def subscribe(self, symbols: list[str]) -> None:
        with self._lock:
            self._active.update(s.upper() for s in symbols if s)
            if self._active:
                self._degraded = True

    def unsubscribe(self, symbols: list[str]) -> None:
        with self._lock:
            for symbol in symbols:
                self._active.discard(symbol.upper())

    def inject_quote(self, quote: Quote) -> None:
        with self._lock:
            self._latest[quote.symbol.upper()] = quote
            self._refresh_degraded_locked()

    def latest(self, symbol: str) -> Quote | None:
        with self._lock:
            return self._latest.get(symbol.upper())

    def fresh(self, symbol: str, now: datetime | None = None) -> Quote | None:
        quote = self.latest(symbol)
        if not quote:
            return None
        now = now or datetime.now(timezone.utc)
        age = (now - quote.timestamp.astimezone(timezone.utc)).total_seconds()
        return quote if age <= self.cfg.market_data.quote_maximum_age_seconds else None

    def refresh(self, symbols: list[str] | None = None) -> dict[str, Quote]:
        requested = [s.upper() for s in (symbols or self.active_symbols)]
        if not requested:
            return {}
        try:
            quotes = self.market_data.quotes(requested)
            with self._lock:
                self._latest.update({symbol.upper(): quote for symbol, quote in quotes.items()})
                self._last_error = None
                if quotes:
                    self._last_fresh_quote_at = datetime.now(timezone.utc).isoformat()
                self._reconnect_attempts = 0
                self._refresh_degraded_locked()
            return quotes
        except Exception as exc:
            with self._lock:
                self._degraded = True
                self._last_error = str(exc)
                self._reconnect_attempts += 1
            return {}

    def reconnect_delay(self) -> int:
        with self._lock:
            return min(
                self.cfg.market_data.reconnect_maximum_seconds,
                self.cfg.market_data.reconnect_initial_seconds * (2 ** max(0, self._reconnect_attempts - 1)),
            )

    def reconnect_once(self) -> None:
        self.refresh(self.active_symbols)
        delay = self.reconnect_delay()
        if self.degraded and delay > 0:
            time.sleep(min(delay, 1))

    def _refresh_degraded_locked(self) -> None:
        now = datetime.now(timezone.utc)
        for symbol in self._active:
            quote = self._latest.get(symbol)
            if not quote:
                self._degraded = True
                return
            if (now - quote.timestamp.astimezone(timezone.utc)).total_seconds() > self.cfg.market_data.quote_maximum_age_seconds:
                self._degraded = True
                return
        self._degraded = False
        if self._active and self._latest:
            self._last_fresh_quote_at = now.isoformat()
