from __future__ import annotations

import json
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from .config import ExecutorConfig, load_config
from .database import PaperExecutorDatabase
from .health import health_payload
from .models import Mode
from .runtime import RuntimeCoordinator


class PaperExecutorApp:
    def __init__(self, cfg: ExecutorConfig):
        self.cfg = cfg
        self.db = PaperExecutorDatabase(cfg.database_path)
        self.runtime = RuntimeCoordinator(cfg, self.db)
        self.runtime.recover()
        self._rate: dict[str, list[float]] = {}

    @property
    def mode(self) -> Mode:
        return self.runtime.mode

    def ingest(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.runtime.ingest_payload(payload)

    def set_mode(self, mode: str, token: str | None) -> dict[str, Any]:
        self._require_token(token)
        parsed = Mode(mode)
        if parsed == Mode.PAPER:
            ok, reason = self.runtime.promote_to_paper()
            if not ok:
                raise PermissionError(f"paper promotion blocked: {reason}")
        else:
            self.runtime.mode = parsed
        return {"mode": self.runtime.mode.value}

    def kill(self) -> dict[str, Any]:
        self.cfg.kill_switch_path.parent.mkdir(parents=True, exist_ok=True)
        self.cfg.kill_switch_path.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")
        return {"kill_switch": True}

    def market_data_probe(self, ticker: str) -> dict[str, Any]:
        ticker = ticker.strip().upper()
        if not ticker.isalpha() or not 1 <= len(ticker) <= 6:
            raise ValueError("ticker must contain 1-6 letters")
        try:
            expirations = self.runtime.market_data.expirations(ticker)
            contracts = sum(len(self.runtime.market_data.chain(ticker, expiration)) for expiration in expirations)
        except Exception as exc:
            self.db.insert_system_event("MARKET_DATA_PROBE_FAILED", {
                "ticker": ticker, "reason": str(exc)[:300],
            })
            raise RuntimeError(str(exc)) from exc
        self.db.insert_system_event("MARKET_DATA_PROBE_OK", {
            "ticker": ticker, "expirations": len(expirations), "contracts": contracts,
        })
        return {
            "ok": True, "ticker": ticker, "feed": "opra",
            "expirations": len(expirations), "contracts": contracts,
            "paper_only": True, "live_execution_capability": False,
        }

    def resume(self, token: str | None) -> dict[str, Any]:
        self._require_token(token)
        if self.cfg.kill_switch_path.exists():
            self.cfg.kill_switch_path.unlink()
        self.runtime.mode = Mode.SHADOW
        return {"kill_switch": False, "mode": self.runtime.mode.value}

    def _require_token(self, token: str | None) -> None:
        path = self.cfg.server.control_token_path
        if not path.exists() or not token or token != path.read_text(encoding="utf-8").strip():
            raise PermissionError("local control token required")


def make_handler(app: PaperExecutorApp):
    class Handler(BaseHTTPRequestHandler):
        server_version = "CipherPaperExecutor/1.0"

        def _origin_ok(self) -> bool:
            origin = self.headers.get("Origin")
            return not origin or origin in app.cfg.server.approved_origins

        def _send(self, code: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, default=str).encode("utf-8")
            self.send_response(code)
            origin = self.headers.get("Origin")
            if origin and origin in app.cfg.server.approved_origins:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self) -> None:
            if not self._origin_ok():
                self._send(403, {"error": "origin rejected"})
                return
            self.send_response(204)
            origin = self.headers.get("Origin")
            if origin:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type,X-Cipher-Control-Token")
                self.send_header("Access-Control-Allow-Private-Network", "true")
            self.end_headers()

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/health":
                payload = health_payload(app.cfg, app.db, app.runtime.mode, app.runtime.quote_manager.degraded)
                payload.update(app.runtime.health())
                self._send(200, payload)
            elif parsed.path == "/api/paper/status":
                payload = health_payload(app.cfg, app.db, app.runtime.mode, app.runtime.quote_manager.degraded)
                payload.update(app.runtime.health())
                self._send(200, payload)
            elif parsed.path == "/api/paper/market-data-probe":
                try:
                    ticker = (parse_qs(parsed.query).get("ticker") or ["SPY"])[0]
                    self._send(200, app.market_data_probe(ticker))
                except ValueError as exc:
                    self._send(400, {"error": str(exc), "paper_only": True})
                except RuntimeError as exc:
                    self._send(503, {"error": str(exc), "paper_only": True})
            elif parsed.path == "/api/paper/" + "positions":
                self._send(200, {"positions": app.db.rows("paper_positions")})
            elif parsed.path == "/api/paper/events":
                self._send(200, {"events": []})
            elif parsed.path == "/api/paper/episodes":
                self._send(200, {"episodes": app.db.rows("signal_episodes")})
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self) -> None:
            if not self._origin_ok():
                self._send(403, {"error": "origin rejected"})
                return
            length = int(self.headers.get("Content-Length") or "0")
            if length > app.cfg.server.max_body_bytes:
                self._send(413, {"error": "body too large"})
                return
            try:
                body = self.rfile.read(length).decode("utf-8") if length else "{}"
                payload = json.loads(body)
                if self.path == "/api/scanner-ingest":
                    self._send(202, app.ingest(payload))
                elif self.path == "/api/paper/kill":
                    self._send(200, app.kill())
                elif self.path == "/api/paper/resume":
                    self._send(200, app.resume(self.headers.get("X-Cipher-Control-Token")))
                elif self.path == "/api/paper/mode":
                    self._send(200, app.set_mode(str(payload.get("mode")), self.headers.get("X-Cipher-Control-Token")))
                else:
                    self._send(404, {"error": "not found"})
            except PermissionError as exc:
                self._send(403, {"error": str(exc)})
            except Exception as exc:
                self._send(400, {"error": str(exc)})

        def log_message(self, format: str, *args: Any) -> None:
            return

    return Handler


def run(config_path: str | None = None) -> None:
    cfg = load_config(config_path)
    app = PaperExecutorApp(cfg)
    app.runtime.start()
    server = ThreadingHTTPServer((cfg.server.host, cfg.server.port), make_handler(app))
    try:
        threading.Thread(target=server.serve_forever, daemon=False).start()
    except KeyboardInterrupt:
        app.runtime.stop()


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else None)
