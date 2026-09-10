import json
import pytest
import core_session
import market_server
import oauth_provider


def test_session_recovers_after_core_restart(monkeypatch):
    monkeypatch.setenv("CIPHER_INTERNAL_PROXY_TOKEN", "test-internal")
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "test-secret")
    monkeypatch.setattr(core_session, "_session", None)
    calls = []
    class Reply:
        def __init__(self, payload): self.payload = payload
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return json.dumps(self.payload).encode()
    class Opener:
        def open(self, req, timeout):
            body = json.loads(req.data)
            calls.append(body["action"])
            assert req.full_url == "http://127.0.0.1:8282/internal/provider-session"
            return Reply({"status": "disconnected"} if body["action"] == "status" else {"provider_session_id": "session-" + str(len(calls))})
    monkeypatch.setattr(core_session.urllib.request, "build_opener", lambda *args: Opener())
    first = core_session.headers("http://127.0.0.1:8282", 1)
    second = core_session.headers("http://127.0.0.1:8282", 1)
    assert calls == ["connect", "status", "connect"]
    assert first["X-Cipher-Provider-Session"] != second["X-Cipher-Provider-Session"]
    assert "test-key" not in json.dumps(second)
    with pytest.raises(ValueError, match="loopback"):
        core_session.headers("https://example.com", 1)


def test_expanded_tools_are_bounded_allowlisted_reads(monkeypatch):
    calls = []
    monkeypatch.setattr(market_server, "_get", lambda path, params: calls.append((path, params)) or {})
    for name, (path, _, needs_symbol) in market_server.READ_TOOLS.items():
        market_server.handle_tool(name, {"symbol": "SPY"} if needs_symbol else {})
        assert calls[-1][0] == path
        assert path in market_server.ALLOWED_PATHS
        with pytest.raises(ValueError):
            market_server.handle_tool(name, {"action": "start"})


@pytest.mark.parametrize("uri", ["http://localhost.attacker.test/cb", "http://127.0.0.1.attacker.test/cb", "https://", "https://user:pass@example.com/cb", "https://example.com/#fragment"])
def test_oauth_rejects_invalid_redirect_hosts(uri):
    assert not oauth_provider._redirect_allowed(uri)


def test_bounded_results_remain_valid_json_and_preserve_source_time():
    result = market_server.result({"as_of": "2026-09-04T20:00:00Z", "rows": ["株"*200]*2000})
    text = result["content"][0]["text"]
    assert len(text.encode()) <= market_server.MAX_RESULT_BYTES
    data = json.loads(text)
    assert data["truncated"] is True
    assert data["data"]["as_of"] == "2026-09-04T20:00:00Z"


def test_oauth_tokens_bound_to_resource_scope_and_refresh_client(tmp_path, monkeypatch):
    monkeypatch.setattr(oauth_provider, "STATE_PATH", tmp_path / "oauth.json")
    monkeypatch.setenv("CIPHER_MCP_PUBLIC_URL", "https://cipher.example")
    _, client = oauth_provider.register_client({"redirect_uris": ["https://chatgpt.com/connector/oauth/test"]})
    params = {"client_id": client["client_id"], "redirect_uri": client["redirect_uris"][0],
              "response_type": "code", "code_challenge_method": "S256",
              "code_challenge": oauth_provider._sha256_b64url("v"*43)}
    assert oauth_provider.validate_authorize({**params, "scope": "cipher.write"})[1]
    assert oauth_provider.validate_authorize({**params, "resource": "https://other.example/mcp"})[1]
    context, error = oauth_provider.validate_authorize(params)
    assert error is None
    code = oauth_provider.issue_code(context)
    status, tokens = oauth_provider.exchange({"grant_type": "authorization_code", "code": code,
        "code_verifier": "v"*43, "client_id": client["client_id"], "redirect_uri": params["redirect_uri"],
        "resource": "https://cipher.example/mcp"})
    assert status == 200
    assert oauth_provider.token_is_valid(tokens["access_token"])
    assert oauth_provider.exchange({"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"],
                                   "client_id": "another-client"})[0] == 400
    monkeypatch.setenv("CIPHER_MCP_PUBLIC_URL", "https://other.example")
    assert not oauth_provider.token_is_valid(tokens["access_token"])
