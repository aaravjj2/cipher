import pytest

from core import app as core_app
from core.provider_session import ProviderCredentials
from core.request_context import ProviderRequestContext, activate, clear


def test_local_settings_resolves_the_authenticated_provider_session():
    session_id = core_app.PROVIDER_SESSIONS.connect(
        "user-a",
        ProviderCredentials("session-key", "session-secret", "indicative", "iex"),
    )
    activate(ProviderRequestContext("user-a", "access-token", session_id))
    try:
        assert core_app.local_settings() == (
            "session-key",
            "session-secret",
            "indicative",
            "iex",
        )
    finally:
        clear()
        core_app.PROVIDER_SESSIONS.disconnect("user-a", session_id)


def test_hosted_background_context_without_session_fails_closed(monkeypatch):
    monkeypatch.setenv("CIPHER_HOSTED", "1")
    clear()
    with pytest.raises(ValueError, match="provider session context is required"):
        core_app.local_settings()


def test_authenticated_request_without_provider_session_fails_closed():
    activate(ProviderRequestContext("user-a", "access-token", None))
    try:
        with pytest.raises(ValueError, match="provider session is required"):
            core_app.local_settings()
    finally:
        clear()
