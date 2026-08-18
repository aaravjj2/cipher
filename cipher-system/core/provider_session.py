"""Ephemeral, user-scoped provider credentials for hosted sessions.

Raw values intentionally live only in this process. This module has no file,
SQLite, cache, logging, or serialization path for credentials.
"""
from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True, repr=False)
class ProviderCredentials:
    key: str
    secret: str
    options_feed: str
    stock_feed: str

    def __repr__(self) -> str:
        return (
            "ProviderCredentials("
            f"options_feed={self.options_feed!r}, stock_feed={self.stock_feed!r})"
        )


@dataclass
class _Session:
    user_id: str
    credentials: ProviderCredentials
    created_at: float
    last_used_at: float
    sequence: int


class SessionStore:
    def __init__(
        self,
        *,
        now_fn: Callable[[], float],
        inactivity_seconds: float = 30 * 60,
        absolute_seconds: float = 12 * 60 * 60,
        max_sessions: int = 32,
    ) -> None:
        if inactivity_seconds <= 0 or absolute_seconds <= 0 or max_sessions < 1:
            raise ValueError("session limits must be positive")
        self._now = now_fn
        self._inactivity_seconds = float(inactivity_seconds)
        self._absolute_seconds = float(absolute_seconds)
        self._max_sessions = int(max_sessions)
        self._sessions: dict[str, _Session] = {}
        self._sequence = 0
        self._lock = threading.RLock()

    def __repr__(self) -> str:
        with self._lock:
            return f"SessionStore(active_sessions={len(self._sessions)})"

    def _expired(self, record: _Session, now: float) -> bool:
        return (
            now - record.last_used_at >= self._inactivity_seconds
            or now - record.created_at >= self._absolute_seconds
        )

    def _prune(self, now: float) -> None:
        expired = [
            session_id
            for session_id, record in self._sessions.items()
            if self._expired(record, now)
        ]
        for session_id in expired:
            self._sessions.pop(session_id, None)

    def connect(self, user_id: str, credentials: ProviderCredentials) -> str:
        owner = str(user_id or "").strip()
        if not owner or not isinstance(credentials, ProviderCredentials):
            raise ValueError("user and provider credentials are required")
        if not credentials.key or not credentials.secret:
            raise ValueError("provider credentials are required")
        now = float(self._now())
        with self._lock:
            self._prune(now)
            while len(self._sessions) >= self._max_sessions:
                oldest_id = min(
                    self._sessions,
                    key=lambda item: self._sessions[item].sequence,
                )
                self._sessions.pop(oldest_id, None)
            self._sequence += 1
            session_id = secrets.token_urlsafe(32)
            self._sessions[session_id] = _Session(
                user_id=owner,
                credentials=credentials,
                created_at=now,
                last_used_at=now,
                sequence=self._sequence,
            )
            return session_id

    def get(self, user_id: str, session_id: str) -> ProviderCredentials | None:
        owner = str(user_id or "").strip()
        opaque_id = str(session_id or "")
        if not owner or not opaque_id:
            return None
        now = float(self._now())
        with self._lock:
            record = self._sessions.get(opaque_id)
            if record is None or record.user_id != owner:
                return None
            if self._expired(record, now):
                self._sessions.pop(opaque_id, None)
                return None
            record.last_used_at = now
            return record.credentials

    def disconnect(self, user_id: str, session_id: str) -> bool:
        owner = str(user_id or "").strip()
        opaque_id = str(session_id or "")
        with self._lock:
            record = self._sessions.get(opaque_id)
            if record is None or record.user_id != owner:
                return False
            self._sessions.pop(opaque_id, None)
            return True

    def clear_user(self, user_id: str) -> int:
        owner = str(user_id or "").strip()
        with self._lock:
            owned = [
                session_id
                for session_id, record in self._sessions.items()
                if record.user_id == owner
            ]
            for session_id in owned:
                self._sessions.pop(session_id, None)
            return len(owned)
