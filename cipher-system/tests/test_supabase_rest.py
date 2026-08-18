import json

import pytest

from core.supabase_rest import SupabaseRepositoryError, SupabaseRestClient
from core.user_state import UserStateRepository


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status

    def read(self):
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False



def test_rest_client_uses_user_jwt_and_public_anon_key_without_logging(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout=None):
        calls.append((request, timeout))
        return FakeResponse([{"id": "row-a", "user_id": "user-a"}])

    client = SupabaseRestClient(
        "https://project.supabase.co",
        user_jwt="user-token-that-must-not-leak",
        anon_key="public-anon-key",
        urlopen_fn=fake_urlopen,
    )
    result = client.request("GET", "watchlists")

    assert result == [{"id": "row-a", "user_id": "user-a"}]
    request = calls[0][0]
    assert request.get_header("Authorization") == "Bearer user-token-that-must-not-leak"
    assert request.get_header("Apikey") == "public-anon-key"
    assert "user-token-that-must-not-leak" not in repr(client)


def test_rest_client_turns_provider_errors_into_safe_unavailable_errors():
    def fake_urlopen(_request, timeout=None):
        return FakeResponse({"message": "user-token-that-must-not-leak"}, status=503)

    client = SupabaseRestClient(
        "https://project.supabase.co",
        user_jwt="user-token-that-must-not-leak",
        anon_key="public-anon-key",
        urlopen_fn=fake_urlopen,
    )
    with pytest.raises(SupabaseRepositoryError, match="HTTP 503") as error:
        client.request("GET", "watchlists")
    assert "user-token-that-must-not-leak" not in str(error.value)


def test_user_repository_adds_authenticated_owner_and_rejects_owner_override():
    calls = []

    class FakeClient:
        def request(self, method, table, **kwargs):
            calls.append((method, table, kwargs))
            return [{"id": "row-a", "user_id": "user-a"}]

    repository = UserStateRepository(FakeClient(), "user-a")
    assert repository.insert_row("watchlists", {"name": "same-name"}) == [{"id": "row-a", "user_id": "user-a"}]
    assert calls[0][2]["payload"]["user_id"] == "user-a"

    with pytest.raises(ValueError, match="user_id cannot be overridden"):
        repository.insert_row("watchlists", {"name": "bad", "user_id": "user-b"})
