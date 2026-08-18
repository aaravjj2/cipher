from core.request_context import ProviderRequestContext, activate, clear, current


def test_context_exposes_user_and_provider_scope_then_clears():
    assert current() is None
    context = ProviderRequestContext(
        user_id="user-a",
        access_token="supabase-token",
        provider_session_id="session-a",
    )

    activate(context)
    assert current() == context
    assert current().user_id == "user-a"
    assert current().provider_session_id == "session-a"
    clear()

    assert current() is None


def test_context_repr_does_not_include_access_token():
    context = ProviderRequestContext(
        user_id="user-a",
        access_token="token-that-must-not-leak",
        provider_session_id=None,
    )

    assert "token-that-must-not-leak" not in repr(context)
