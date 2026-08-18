import json
import threading
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen

from core import app as core_app


def test_core_internal_provider_session_lifecycle(monkeypatch):
    monkeypatch.setenv("CIPHER_INTERNAL_PROXY_TOKEN", "internal-token")
    server = ThreadingHTTPServer(("127.0.0.1", 0), core_app.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}/internal/provider-session"
        headers = {
            "content-type": "application/json",
            "x-cipher-internal-token": "internal-token",
            "x-cipher-user-id": "user-a",
            "x-cipher-access-token": "access-token",
        }
        connect = Request(base, method="POST", headers=headers, data=json.dumps({
            "action": "connect",
            "key": "session-key",
            "secret": "session-secret",
            "options_feed": "opra",
            "stock_feed": "sip",
        }).encode())
        connected = json.loads(urlopen(connect).read())
        assert connected["status"] == "connected"
        assert "session-key" not in json.dumps(connected)
        session_id = connected["provider_session_id"]

        status = Request(base, method="POST", headers=headers, data=json.dumps({
            "action": "status",
            "provider_session_id": session_id,
        }).encode())
        assert json.loads(urlopen(status).read())["status"] == "connected"

        disconnect = Request(base, method="POST", headers=headers, data=json.dumps({
            "action": "disconnect",
            "provider_session_id": session_id,
        }).encode())
        assert json.loads(urlopen(disconnect).read())["status"] == "disconnected"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
