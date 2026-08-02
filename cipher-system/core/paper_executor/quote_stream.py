from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class QuoteStreamState:
    active_symbols: set[str] = field(default_factory=set)
    degraded: bool = False
    last_fresh_quote_at: datetime | None = None
    reconnect_attempts: int = 0

    def subscribe(self, symbols: list[str]) -> None:
        self.active_symbols.update(s.upper() for s in symbols)

    def mark_degraded(self) -> None:
        self.degraded = True

    def mark_fresh(self) -> None:
        self.degraded = False
        self.last_fresh_quote_at = datetime.now(timezone.utc)
        self.reconnect_attempts = 0

    def next_backoff(self, initial: int, maximum: int) -> int:
        delay = min(maximum, initial * (2 ** self.reconnect_attempts))
        self.reconnect_attempts += 1
        return delay
