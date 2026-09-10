"""Private loopback provider-session handshake; never an MCP tool or broker action."""
import json
import os
import threading
import urllib.request
from urllib.parse import urlparse

_lock = threading.Lock()
_session = None


def headers(base_url, timeout):
    global _session
    token = os.environ.get("CIPHER_INTERNAL_PROXY_TOKEN", "")
    if not token:
        return {"Accept": "application/json"}
    # Unit/integration harnesses may provide only the internal token and use
    # the core's bounded guest fixtures. Production service env includes keys.
    if not (os.environ.get("ALPACA_ALGO_KEY") or os.environ.get("ALPACA_ALGO_PLUS_KEY")
            or os.environ.get("ALPACA_API_KEY")):
        return {"Accept": "application/json", "X-Cipher-Internal-Token": token,
                "X-Cipher-Guest": "1", "X-Cipher-User-Id": "guest"}
    # Server credentials must never follow a configured remote URL or redirect.
    parsed = urlparse(base_url)
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
        raise ValueError("authenticated Cipher core URL must be loopback HTTP")
    auth = {"Accept": "application/json", "Content-Type": "application/json",
            "X-Cipher-Internal-Token": token, "X-Cipher-User-Id": "cipher-mcp-operator"}

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    def request(body):
        req = urllib.request.Request(base_url + "/internal/provider-session",
                                     data=json.dumps(body).encode(), headers=auth, method="POST")
        try:
            with urllib.request.build_opener(NoRedirect).open(req, timeout=timeout) as response:
                return json.load(response)
        except Exception:
            raise ValueError(f"cannot reach cipher-core provider session at {base_url}; check service configuration") from None

    with _lock:
        # Check every call: core restarts and inactivity expiry otherwise silently
        # downgrade valid MCP users to Yahoo data. This is a cheap loopback check.
        if _session and request({"action": "status", "provider_session_id": _session}).get("status") != "connected":
            _session = None
        if not _session:
            key = os.environ.get("ALPACA_ALGO_KEY") or os.environ.get("ALPACA_ALGO_PLUS_KEY") or os.environ.get("ALPACA_API_KEY")
            secret = os.environ.get("ALPACA_ALGO_SECRET") or os.environ.get("ALPACA_ALGO_PLUS_SECRET") or os.environ.get("ALPACA_API_SECRET")
            if not key or not secret:
                raise ValueError("MCP service has no Alpaca data credentials configured")
            _session = request({"action": "connect", "key": key, "secret": secret,
                                "options_feed": os.environ.get("ALPACA_DATA_FEED", "opra"),
                                "stock_feed": os.environ.get("ALPACA_STOCK_FEED", "sip")}).get("provider_session_id")
            if not _session:
                raise ValueError("Cipher provider session was not established")
        return {**auth, "X-Cipher-Provider-Session": _session}
