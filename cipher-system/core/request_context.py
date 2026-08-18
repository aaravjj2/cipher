"""Request-scoped identity and provider context for the Python core."""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True, repr=False)
class ProviderRequestContext:
    user_id: str
    access_token: str | None
    provider_session_id: str | None
    guest: bool = False

    def __repr__(self) -> str:
        return (
            "ProviderRequestContext("
            f"user_id={self.user_id!r}, "
            f"provider_session_id={self.provider_session_id!r}, "
            f"guest={self.guest!r})"
        )


_CURRENT: ContextVar[ProviderRequestContext | None] = ContextVar(
    "cipher_provider_request_context", default=None
)


def activate(context: ProviderRequestContext) -> None:
    if not context.user_id:
        raise ValueError("request context requires a user ID")
    _CURRENT.set(context)


def current() -> ProviderRequestContext | None:
    return _CURRENT.get()


def clear() -> None:
    _CURRENT.set(None)
