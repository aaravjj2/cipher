"""The OAuth flow the MCP bridge exposes, driven over real HTTP.

ChatGPT refuses a static bearer token: it discovers metadata, registers itself, runs an
authorization-code exchange with PKCE, and calls the server with the resulting token. Each of
those steps is a place the connector can fail with a message that names nothing, so each is
tested here rather than left to a retry in someone else's UI.

The security property that matters most is the consent step. `/authorize` is a public URL on
a public tunnel; if it issued codes to anyone who requested one, OAuth would be strictly worse
than the shared token it replaces. No code may be issued without the operator secret.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import socket
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import oauth_provider  # noqa: E402
import remote_bridge  # noqa: E402

TOKEN = "operator-token-" + "b" * 24
CALLBACK = "https://chatgpt.com/connector/oauth/testcallback"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture()
def server(tmp_path, monkeypatch):
    token_file = tmp_path / "token.txt"
    token_file.write_text(TOKEN)
    monkeypatch.setattr(remote_bridge, "TOKEN_PATH", token_file)
    monkeypatch.setattr(oauth_provider, "STATE_PATH", tmp_path / "oauth.json")
    port = free_port()
    from http.server import ThreadingHTTPServer

    httpd = ThreadingHTTPServer(("127.0.0.1", port), remote_bridge.Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def request(url, *, data=None, headers=None, method=None, follow=True):
    body = None
    if isinstance(data, dict):
        body = urllib.parse.urlencode(data).encode()
    elif isinstance(data, bytes):
        body = data
    req = urllib.request.Request(url, data=body, method=method or ("POST" if body else "GET"))
    if body and not (headers or {}).get("Content-Type"):
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    opener = urllib.request.build_opener() if follow else urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(req, timeout=30) as response:
            raw = response.read()
            return response.status, raw, dict(response.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers)


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).decode("ascii").rstrip("=")
    return verifier, challenge


# ----------------------------------------------------------------- discovery

def test_protected_resource_metadata_is_public_and_points_at_this_origin(server):
    status, raw, _ = request(f"{server}/.well-known/oauth-protected-resource")
    assert status == 200
    doc = json.loads(raw)
    assert doc["resource"].endswith("/mcp")
    assert doc["authorization_servers"] == [server]


def test_authorization_server_metadata_advertises_what_a_client_needs(server):
    status, raw, _ = request(f"{server}/.well-known/oauth-authorization-server")
    assert status == 200
    doc = json.loads(raw)
    assert doc["issuer"] == server
    assert doc["authorization_endpoint"] == f"{server}/authorize"
    assert doc["token_endpoint"] == f"{server}/token"
    # ChatGPT greys out DCR unless a registration endpoint is discovered.
    assert doc["registration_endpoint"] == f"{server}/register"
    assert doc["code_challenge_methods_supported"] == ["S256"]
    assert doc["grant_types_supported"] == ["authorization_code", "refresh_token"]


def test_discovery_is_reachable_with_the_resource_path_appended(server):
    """Clients probe both spellings; only one being served looks like no OAuth support."""
    status, _, _ = request(f"{server}/.well-known/oauth-protected-resource/mcp")
    assert status == 200


def test_an_unauthorized_call_points_at_the_metadata_document(server):
    status, _, headers = request(
        f"{server}/mcp", data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}).encode(),
        headers={"Content-Type": "application/json"})
    assert status == 401
    challenge = headers["WWW-Authenticate"]
    assert "resource_metadata=" in challenge
    assert "/.well-known/oauth-protected-resource" in challenge


def test_the_advertised_origin_follows_the_proxy_headers(server):
    """Behind a tunnel the bridge cannot know its public URL, and a document advertising
    127.0.0.1 would be discovered and then be unreachable."""
    status, raw, _ = request(
        f"{server}/.well-known/oauth-authorization-server",
        headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "example.trycloudflare.com"})
    assert status == 200
    assert json.loads(raw)["issuer"] == "https://example.trycloudflare.com"


# ----------------------------------------------------------------- registration

def register(server, uris=(CALLBACK,)) -> str:
    status, raw, _ = request(
        f"{server}/register",
        data=json.dumps({"redirect_uris": list(uris), "client_name": "ChatGPT"}).encode(),
        headers={"Content-Type": "application/json"})
    assert status == 201, raw
    return json.loads(raw)["client_id"]


def test_dynamic_registration_issues_a_public_client(server):
    status, raw, _ = request(
        f"{server}/register",
        data=json.dumps({"redirect_uris": [CALLBACK]}).encode(),
        headers={"Content-Type": "application/json"})
    assert status == 201
    doc = json.loads(raw)
    assert doc["client_id"].startswith("cipher-")
    assert doc["token_endpoint_auth_method"] == "none"
    assert "client_secret" not in doc


def test_registration_refuses_a_non_https_callback(server):
    status, raw, _ = request(
        f"{server}/register",
        data=json.dumps({"redirect_uris": ["http://evil.example/cb"]}).encode(),
        headers={"Content-Type": "application/json"})
    assert status == 400
    assert json.loads(raw)["error"] == "invalid_redirect_uri"


def test_registration_allows_loopback_for_native_clients(server):
    status, _, _ = request(
        f"{server}/register",
        data=json.dumps({"redirect_uris": ["http://127.0.0.1:33418/cb"]}).encode(),
        headers={"Content-Type": "application/json"})
    assert status == 201


# ----------------------------------------------------------------- authorize

def authorize_query(client_id: str, challenge: str, **extra) -> str:
    params = {
        "client_id": client_id, "redirect_uri": CALLBACK, "response_type": "code",
        "code_challenge": challenge, "code_challenge_method": "S256", "state": "xyz",
    }
    params.update(extra)
    return urllib.parse.urlencode(params)


def test_the_consent_page_renders_and_asks_for_the_operator_token(server):
    client_id = register(server)
    _, challenge = pkce()
    status, raw, _ = request(f"{server}/authorize?{authorize_query(client_id, challenge)}")
    assert status == 200
    page = raw.decode()
    assert 'type="password"' in page
    assert "read-only" in page
    assert "cannot place" in page


def test_authorize_rejects_an_unregistered_client(server):
    _, challenge = pkce()
    status, raw, _ = request(f"{server}/authorize?{authorize_query('nope', challenge)}")
    assert status == 400
    assert "unknown client_id" in json.loads(raw)["error_description"]


def test_authorize_rejects_a_redirect_uri_that_was_not_registered(server):
    client_id = register(server)
    _, challenge = pkce()
    query = authorize_query(client_id, challenge, redirect_uri="https://attacker.example/cb")
    status, raw, _ = request(f"{server}/authorize?{query}")
    assert status == 400
    assert "redirect_uri" in json.loads(raw)["error_description"]


def test_authorize_requires_pkce_s256(server):
    client_id = register(server)
    _, challenge = pkce()
    status, raw, _ = request(f"{server}/authorize?{authorize_query(client_id, challenge, code_challenge_method='plain')}")
    assert status == 400
    assert "S256" in json.loads(raw)["error_description"]

    status, raw, _ = request(f"{server}/authorize?{authorize_query(client_id, '')}")
    assert status == 400
    assert "code_challenge" in json.loads(raw)["error_description"]


def approve(server, client_id: str, challenge: str, secret: str = TOKEN):
    return request(
        f"{server}/authorize",
        data={
            "client_id": client_id, "redirect_uri": CALLBACK, "response_type": "code",
            "code_challenge": challenge, "code_challenge_method": "S256",
            "state": "xyz", "scope": "cipher.read", "secret": secret,
        },
        follow=False,
    )


def test_no_code_is_issued_without_the_operator_secret(server):
    """The property that keeps a public /authorize from being an open door."""
    client_id = register(server)
    _, challenge = pkce()
    status, raw, headers = approve(server, client_id, challenge, secret="wrong")
    assert status == 403
    assert "Location" not in headers
    assert "does not match" in raw.decode()


def test_approval_redirects_to_the_callback_with_a_code_and_state(server):
    client_id = register(server)
    _, challenge = pkce()
    status, _, headers = approve(server, client_id, challenge)
    assert status == 302
    location = urllib.parse.urlparse(headers["Location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == CALLBACK
    query = urllib.parse.parse_qs(location.query)
    assert query["state"] == ["xyz"]
    assert query["code"][0]


# ----------------------------------------------------------------- token

def full_flow(server) -> dict:
    client_id = register(server)
    verifier, challenge = pkce()
    _, _, headers = approve(server, client_id, challenge)
    code = urllib.parse.parse_qs(urllib.parse.urlparse(headers["Location"]).query)["code"][0]
    status, raw, _ = request(f"{server}/token", data={
        "grant_type": "authorization_code", "code": code, "code_verifier": verifier,
        "client_id": client_id, "redirect_uri": CALLBACK,
    })
    assert status == 200, raw
    return json.loads(raw)


def test_the_full_flow_yields_a_working_access_token(server):
    tokens = full_flow(server)
    assert tokens["token_type"] == "Bearer"
    assert tokens["expires_in"] > 0
    assert tokens["refresh_token"]
    status, raw, _ = request(
        f"{server}/mcp",
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {tokens['access_token']}"})
    assert status == 200
    assert len(json.loads(raw)["result"]["tools"]) >= 11


def test_a_wrong_pkce_verifier_is_refused(server):
    client_id = register(server)
    _, challenge = pkce()
    _, _, headers = approve(server, client_id, challenge)
    code = urllib.parse.parse_qs(urllib.parse.urlparse(headers["Location"]).query)["code"][0]
    status, raw, _ = request(f"{server}/token", data={
        "grant_type": "authorization_code", "code": code,
        "code_verifier": secrets.token_urlsafe(48), "client_id": client_id,
    })
    assert status == 400
    assert json.loads(raw)["error"] == "invalid_grant"


def test_an_authorization_code_cannot_be_used_twice(server):
    client_id = register(server)
    verifier, challenge = pkce()
    _, _, headers = approve(server, client_id, challenge)
    code = urllib.parse.parse_qs(urllib.parse.urlparse(headers["Location"]).query)["code"][0]
    form = {"grant_type": "authorization_code", "code": code,
            "code_verifier": verifier, "client_id": client_id}
    assert request(f"{server}/token", data=dict(form))[0] == 200
    status, raw, _ = request(f"{server}/token", data=dict(form))
    assert status == 400
    assert "used" in json.loads(raw)["error_description"]


def test_a_failed_exchange_still_consumes_the_code(server):
    """A code that survives a bad attempt is a code an attacker may keep guessing against."""
    client_id = register(server)
    verifier, challenge = pkce()
    _, _, headers = approve(server, client_id, challenge)
    code = urllib.parse.parse_qs(urllib.parse.urlparse(headers["Location"]).query)["code"][0]
    request(f"{server}/token", data={"grant_type": "authorization_code", "code": code,
                                     "code_verifier": "wrong", "client_id": client_id})
    status, _, _ = request(f"{server}/token", data={
        "grant_type": "authorization_code", "code": code,
        "code_verifier": verifier, "client_id": client_id})
    assert status == 400


def test_a_refresh_token_yields_a_new_access_token(server):
    tokens = full_flow(server)
    status, raw, _ = request(f"{server}/token", data={
        "grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]})
    assert status == 200
    refreshed = json.loads(raw)
    assert refreshed["access_token"] != tokens["access_token"]


def test_an_unsupported_grant_is_refused(server):
    status, raw, _ = request(f"{server}/token", data={"grant_type": "password"})
    assert status == 400
    assert json.loads(raw)["error"] == "unsupported_grant_type"


def test_a_forged_access_token_is_refused(server):
    status, _, _ = request(
        f"{server}/mcp",
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer cipher_at_madeup"})
    assert status == 401


def test_the_static_operator_token_still_works_for_stdio_style_clients(server):
    status, _, _ = request(
        f"{server}/mcp",
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {TOKEN}"})
    assert status == 200


def test_revoking_clears_issued_tokens(server):
    tokens = full_flow(server)
    oauth_provider.revoke_all()
    status, _, _ = request(
        f"{server}/mcp",
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {tokens['access_token']}"})
    assert status == 401
