from core.provider_session import ProviderCredentials, SessionStore


def credentials(value: str = "secret-a") -> ProviderCredentials:
    return ProviderCredentials(
        key=f"key-{value}",
        secret=value,
        options_feed="opra",
        stock_feed="sip",
    )


def test_session_credentials_are_owned_and_redacted():
    now = [1_000.0]
    store = SessionStore(
        now_fn=lambda: now[0],
        inactivity_seconds=30,
        absolute_seconds=120,
        max_sessions=4,
    )

    session_id = store.connect("user-a", credentials())

    assert store.get("user-a", session_id) == credentials()
    assert store.get("user-b", session_id) is None
    assert "secret-a" not in repr(store.get("user-a", session_id))
    assert "secret-a" not in repr(store)


def test_session_expires_after_inactivity_and_disconnect_clears_it():
    now = [1_000.0]
    store = SessionStore(now_fn=lambda: now[0], inactivity_seconds=30, absolute_seconds=120)
    session_id = store.connect("user-a", credentials())

    now[0] += 31
    assert store.get("user-a", session_id) is None
    assert store.disconnect("user-a", session_id) is False


def test_session_has_absolute_lifetime_even_when_active():
    now = [1_000.0]
    store = SessionStore(now_fn=lambda: now[0], inactivity_seconds=30, absolute_seconds=120)
    session_id = store.connect("user-a", credentials())

    now[0] += 29
    assert store.get("user-a", session_id) == credentials()
    now[0] += 29
    assert store.get("user-a", session_id) == credentials()
    now[0] += 63
    assert store.get("user-a", session_id) is None


def test_session_count_is_bounded_per_process():
    now = [1_000.0]
    store = SessionStore(now_fn=lambda: now[0], max_sessions=2)
    first = store.connect("user-a", credentials("first"))
    second = store.connect("user-b", credentials("second"))
    third = store.connect("user-c", credentials("third"))

    assert store.get("user-a", first) is None
    assert store.get("user-b", second) == credentials("second")
    assert store.get("user-c", third) == credentials("third")
