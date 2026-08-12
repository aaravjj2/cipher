#!/usr/bin/env python3
"""Remote MCP bridge — serves the Cipher Market MCP over HTTP for hosts that cannot spawn
a local process.

`market_server.py` speaks MCP over stdio, which is what Claude Desktop launches. ChatGPT
connectors cannot launch anything; they only connect outward to an HTTPS MCP endpoint. This
module puts the *same* server behind the Streamable HTTP transport so both hosts drive
identical code: `handle()`, `tool_specs()` and `handle_tool()` are imported, not
reimplemented, so the read-only allowlist and the payload projections cannot drift between
the two transports.

What is different from stdio, and why it matters:

*   **stdio is implicitly authenticated** -- the host already has local execution. An HTTP
    endpoint published through Tailscale Funnel is reachable by anyone, so every request
    must carry `Authorization: Bearer <token>`. The token lives in
    `runtime/config/mcp-bearer-token.txt`, deliberately not in `/etc/cipher/cipher.env`:
    that file was rebuilt from scratch and lost every credential during the 2026-08-12
    reboot, while this directory's secrets survived.
*   **Comparison is constant-time** (`hmac.compare_digest`) so a wrong token cannot be
    recovered a byte at a time from response timing.
*   **The bridge binds to localhost only.** Funnel is the sole path in, which keeps the
    tailnet boundary in one place.

It remains read-only. No mutating cipher-core route is reachable, and no tool can place,
size, modify or cancel an order.
"""
from __future__ import annotations

import hmac
import json
import os
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import market_server  # noqa: E402

HOST = os.environ.get("CIPHER_MCP_BRIDGE_HOST", "127.0.0.1")
PORT = int(os.environ.get("CIPHER_MCP_BRIDGE_PORT", "8284"))
TOKEN_PATH = Path(os.environ.get(
    "CIPHER_MCP_TOKEN_FILE",
    "/home/aarav/Aarav/cipher/runtime/config/mcp-bearer-token.txt",
))
MAX_BODY = 1 << 20
PROTOCOL_VERSION = "2025-06-18"

_SESSIONS: set[str] = set()
_SESSION_LOCK = threading.Lock()


def load_token() -> str | None:
    """Read the bearer token. Absent or empty means the bridge refuses to serve."""
    try:
        token = TOKEN_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return token or None


class Handler(BaseHTTPRequestHandler):
    server_version = "cipher-mcp-bridge/0.1"
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------------ helpers

    def _send(self, status: int, payload: Any, *, extra: dict[str, str] | None = None) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        # The bridge is consumed by servers, not browsers. Advertising no cross-origin
        # access keeps a hostile page from using a logged-in browser as a proxy.
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _drain_body(self) -> None:
        """Consume the request body before answering early.

        With HTTP/1.1 keep-alive, a body left unread stays in the socket buffer and the next
        request on that connection is parsed starting from those bytes. Rejecting an
        unauthorised POST without draining produced request lines like
        '{"jsonrpc":...}POST /mcp' and an HTTP 501, intermittently, depending on whether the
        proxy reused the connection -- which Tailscale Funnel does. Any early return must
        either drain or close.
        """
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self.close_connection = True
            return
        if length <= 0:
            return
        if length > MAX_BODY:
            # Refuse to read an unbounded body just to keep a socket tidy; drop it instead.
            self.close_connection = True
            return
        try:
            self.rfile.read(length)
        except OSError:
            self.close_connection = True

    def _unauthorized(self, detail: str) -> None:
        self._drain_body()
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Bearer realm="cipher-mcp"')
        body = json.dumps({"error": "unauthorized", "detail": detail}).encode("utf-8")
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        # An unauthenticated caller gets no persistent connection: cheap, and it removes the
        # desync risk entirely rather than relying on the drain above being complete.
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def _authorized(self) -> bool:
        expected = load_token()
        if not expected:
            # Fail closed. A missing token file must never mean "no auth required" -- that
            # is precisely the CIPHER_APP_AUTH=off mistake, where absent configuration
            # disabled the gate instead of the service.
            self._unauthorized("server has no bearer token configured")
            return False
        header = self.headers.get("Authorization") or ""
        scheme, _, presented = header.partition(" ")
        if scheme.lower() != "bearer" or not presented:
            self._unauthorized("expected an Authorization: Bearer header")
            return False
        if not hmac.compare_digest(presented.strip(), expected):
            self._unauthorized("token rejected")
            return False
        return True

    def log_message(self, fmt: str, *args: Any) -> None:
        # Never log the Authorization header or query strings; only method and status.
        sys.stderr.write(f"{self.address_string()} {self.command} {self.path.split('?')[0]} {fmt % args}\n")

    # ------------------------------------------------------------------ routes

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        if path in {"/health", "/healthz"}:
            # Unauthenticated liveness only. It reveals nothing beyond "the bridge is up",
            # and deliberately does not reach cipher-core.
            self._send(200, {"status": "ok", "service": "cipher-mcp-bridge",
                             "protocol_version": PROTOCOL_VERSION,
                             "token_configured": bool(load_token())})
            return
        if path != "/mcp":
            self._send(404, {"error": "not found", "detail": "the MCP endpoint is /mcp"})
            return
        if not self._authorized():
            return
        # Streamable HTTP allows a GET to open a server-to-client stream. This server never
        # initiates traffic, so declining is correct and simpler than holding an idle socket.
        self._send(405, {"error": "method not allowed",
                         "detail": "this server does not push; POST JSON-RPC to /mcp"})

    def do_DELETE(self) -> None:
        if self.path.split("?")[0] != "/mcp":
            self._send(404, {"error": "not found"})
            return
        if not self._authorized():
            return
        session = self.headers.get("Mcp-Session-Id")
        if session:
            with _SESSION_LOCK:
                _SESSIONS.discard(session)
        self._send(200, {"ok": True})

    def do_POST(self) -> None:
        if self.path.split("?")[0] != "/mcp":
            self._drain_body()
            self._send(404, {"error": "not found", "detail": "the MCP endpoint is /mcp"})
            return
        if not self._authorized():
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            # The framing is untrustworthy, so the connection cannot be reused.
            self.close_connection = True
            self._send(400, {"error": "bad request", "detail": "invalid Content-Length"})
            return
        if length <= 0:
            self._send(400, {"error": "bad request", "detail": "empty body"})
            return
        if length > MAX_BODY:
            # Do not read it just to discard it; drop the connection instead.
            self.close_connection = True
            self._send(413, {"error": "payload too large"})
            return
        raw = self.rfile.read(length)
        try:
            message = json.loads(raw.decode("utf-8"))
        except Exception:
            self._send(400, {"jsonrpc": "2.0", "id": None,
                             "error": {"code": -32700, "message": "parse error"}})
            return

        batch = isinstance(message, list)
        requests = message if batch else [message]
        replies = []
        session_header: dict[str, str] = {}
        for item in requests:
            if not isinstance(item, dict):
                replies.append({"jsonrpc": "2.0", "id": None,
                                "error": {"code": -32600, "message": "invalid request"}})
                continue
            method = item.get("method", "")
            request_id = item.get("id")
            if method == "initialize":
                session = uuid.uuid4().hex
                with _SESSION_LOCK:
                    _SESSIONS.add(session)
                session_header["Mcp-Session-Id"] = session
            try:
                result = market_server.handle(method, item.get("params") or {})
            except Exception as exc:
                if request_id is not None:
                    replies.append({"jsonrpc": "2.0", "id": request_id,
                                    "error": {"code": -32603, "message": str(exc)}})
                continue
            if request_id is None:
                # A notification. Streamable HTTP wants 202 with no body.
                continue
            replies.append({"jsonrpc": "2.0", "id": request_id, "result": result})

        if not replies:
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._send(200, replies if batch else replies[0], extra=session_header)


def main() -> int:
    if not load_token():
        sys.stderr.write(
            f"refusing to start: no bearer token at {TOKEN_PATH}\n"
            "create one with: openssl rand -hex 32 > "
            f"{TOKEN_PATH} && chmod 600 {TOKEN_PATH}\n"
        )
        return 2
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.daemon_threads = True
    sys.stderr.write(f"cipher-mcp-bridge listening on http://{HOST}:{PORT}/mcp (read-only)\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
