"""The remote MCP bridge is internet-facing, so its gate is tested before its features.

The bridge exists because ChatGPT connectors cannot spawn a local process; they only reach
outward to an HTTPS endpoint. Publishing through Tailscale Funnel means anyone can reach the
socket, so the bearer check is the only thing between the open internet and Cipher's market
data. These tests drive a real server on a throwaway port over real HTTP.

The fail-closed test is the important one. On 2026-08-12 `sync-secrets.py` wrote
`CIPHER_APP_AUTH=off` when it could not resolve a password hash, and absent configuration
disabling the gate left the published site serving unauthenticated. A bridge with no token
file must refuse every request, never serve openly.
"""
from __future__ import annotations

import json
import socket
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import market_server  # noqa: E402
import remote_bridge  # noqa: E402

TOKEN = "test-token-" + "a" * 32


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture()
def bridge(tmp_path, monkeypatch):
    """A live bridge with a known token, on its own port."""
    token_file = tmp_path / "token.txt"
    token_file.write_text(TOKEN)
    monkeypatch.setattr(remote_bridge, "TOKEN_PATH", token_file)
    port = free_port()
    from http.server import ThreadingHTTPServer

    server = ThreadingHTTPServer(("127.0.0.1", port), remote_bridge.Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}", token_file
    finally:
        server.shutdown()
        server.server_close()


def call(base: str, body, token: str | None = TOKEN, path: str = "/mcp", method: str = "POST"):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"{base}{path}", data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if token is not None:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode()
            return response.status, (json.loads(raw) if raw else None), dict(response.headers)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        return exc.code, (json.loads(raw) if raw else None), dict(exc.headers)


INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}


# ------------------------------------------------------------------ the gate

def test_no_token_header_is_rejected(bridge):
    base, _ = bridge
    status, body, headers = call(base, INIT, token=None)
    assert status == 401
    assert "Bearer" in headers.get("WWW-Authenticate", "")
    assert body["error"] == "unauthorized"


def test_wrong_token_is_rejected(bridge):
    base, _ = bridge
    status, body, _ = call(base, INIT, token="not-the-token")
    assert status == 401
    assert body["detail"] == "token rejected"


def test_a_token_that_is_a_prefix_of_the_real_one_is_rejected(bridge):
    base, _ = bridge
    status, _, _ = call(base, INIT, token=TOKEN[:-1])
    assert status == 401


def test_a_missing_token_file_fails_closed(bridge):
    """Absent configuration must disable the service, never the gate."""
    base, token_file = bridge
    token_file.unlink()
    status, body, _ = call(base, INIT)
    assert status == 401
    assert "no bearer token" in body["detail"]


def test_an_empty_token_file_fails_closed(bridge):
    base, token_file = bridge
    token_file.write_text("   \n")
    status, body, _ = call(base, INIT)
    assert status == 401


def test_the_correct_token_is_accepted(bridge):
    base, _ = bridge
    status, body, headers = call(base, INIT)
    assert status == 200
    assert body["result"]["serverInfo"]["name"] == "cipher-market-mcp"
    assert headers.get("Mcp-Session-Id")


def test_health_is_reachable_without_a_token_but_reveals_nothing(bridge):
    base, _ = bridge
    status, body, _ = call(base, None, token=None, path="/health", method="GET")
    assert status == 200
    assert body["status"] == "ok"
    assert set(body) == {"status", "service", "protocol_version", "token_configured"}


def test_unknown_paths_are_not_the_mcp_endpoint(bridge):
    base, _ = bridge
    status, _, _ = call(base, None, path="/", method="GET")
    assert status == 404


def test_a_rejected_post_does_not_desync_the_next_request_on_the_same_connection(bridge):
    """The regression, and it only appeared through a connection-reusing proxy.

    `_authorized` answered 401 without reading the request body. Under HTTP/1.1 keep-alive
    the unread body stayed in the socket buffer, so the next request on that connection was
    parsed starting mid-JSON -- producing request lines like '{"jsonrpc":...}POST /mcp' and
    an HTTP 501. Tailscale Funnel reuses connections, so the public endpoint returned 401
    and 501 alternately for the same wrong token.
    """
    base, _ = bridge
    host, port = base.removeprefix("http://").split(":")
    body = json.dumps(INIT).encode()

    def frame(token: str) -> bytes:
        return (
            f"POST /mcp HTTP/1.1\r\nHost: {host}:{port}\r\n"
            f"Authorization: Bearer {token}\r\n"
            f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n"
        ).encode() + body

    with socket.create_connection((host, int(port)), timeout=30) as sock:
        # Rejected request first, then a valid one down the same socket.
        sock.sendall(frame("wrong-token"))
        sock.sendall(frame(TOKEN))
        sock.settimeout(30)
        chunks = []
        while True:
            try:
                data = sock.recv(65536)
            except socket.timeout:
                break
            if not data:
                break
            chunks.append(data)
    raw = b"".join(chunks).decode(errors="replace")
    assert "401" in raw
    # The bug's signature: the body echoed back into a request line, answered 501.
    assert "501" not in raw, raw[:400]
    assert "Unsupported method" not in raw


# ------------------------------------------------------------------ protocol

def test_tools_are_the_same_set_the_stdio_server_exposes(bridge):
    base, _ = bridge
    status, body, _ = call(base, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    assert status == 200
    served = {tool["name"] for tool in body["result"]["tools"]}
    assert served == {spec["name"] for spec in market_server.tool_specs()}


def test_chatgpt_deep_research_finds_a_search_and_fetch_pair(bridge):
    base, _ = bridge
    _, body, _ = call(base, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    served = {tool["name"] for tool in body["result"]["tools"]}
    assert {"search", "fetch"} <= served


def test_a_notification_gets_202_and_no_body(bridge):
    base, _ = bridge
    status, body, _ = call(base, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
    assert status == 202
    assert body is None


def test_a_batch_returns_a_list_of_replies(bridge):
    base, _ = bridge
    status, body, _ = call(base, [
        INIT,
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ])
    assert status == 200
    assert isinstance(body, list) and len(body) == 2
    assert [item["id"] for item in body] == [1, 2]


def test_malformed_json_is_a_parse_error(bridge):
    base, _ = bridge
    request = urllib.request.Request(f"{base}/mcp", data=b"{not json", method="POST")
    request.add_header("Authorization", f"Bearer {TOKEN}")
    try:
        urllib.request.urlopen(request, timeout=30)
        raise AssertionError("expected an error status")
    except urllib.error.HTTPError as exc:
        assert exc.code == 400
        assert json.loads(exc.read())["error"]["code"] == -32700


def test_an_oversized_body_is_refused(bridge):
    base, _ = bridge
    request = urllib.request.Request(
        f"{base}/mcp", data=b"x" * (remote_bridge.MAX_BODY + 10), method="POST")
    request.add_header("Authorization", f"Bearer {TOKEN}")
    try:
        urllib.request.urlopen(request, timeout=30)
        raise AssertionError("expected an error status")
    except urllib.error.HTTPError as exc:
        assert exc.code == 413


def test_an_unknown_method_is_an_error_not_a_crash(bridge):
    base, _ = bridge
    status, body, _ = call(base, {"jsonrpc": "2.0", "id": 9, "method": "nope", "params": {}})
    assert status == 200
    assert body["error"]["code"] == -32603


def test_get_on_mcp_declines_rather_than_holding_a_socket(bridge):
    base, _ = bridge
    status, _, _ = call(base, None, path="/mcp", method="GET")
    assert status == 405


# ------------------------------------------------------------------ read-only

def test_the_bridge_adds_no_reachable_write_path():
    """The bridge imports the stdio server's handlers; it must not widen the allowlist."""
    for path in ("/api/holdings", "/api/backtest", "/api/alerts", "/api/ask"):
        assert path not in market_server.ALLOWED_PATHS


def test_search_never_invents_a_symbol():
    result = market_server._search("ZZZZ nonexistent")
    assert result["results"] == []
    assert "not in Cipher's covered universe" in result["note"]


def test_search_resolves_a_company_name_and_a_ticker():
    assert market_server._search("nvidia gamma")["results"][0]["id"] == "NVDA"
    assert market_server._search("NVDA")["results"][0]["id"] == "NVDA"
    assert market_server._search("semis")["results"][0]["id"] == "SMH"


def test_fetch_refuses_an_uncovered_symbol():
    with pytest.raises(ValueError, match="not in Cipher's covered universe"):
        market_server._fetch("ZZZZ")
