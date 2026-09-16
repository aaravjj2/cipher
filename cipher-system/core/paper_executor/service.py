from __future__ import annotations

import json
import os
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
    def __init__(self, cfg: ExecutorConfig, *, market_data=None, runtime_class=RuntimeCoordinator):
        self.cfg = cfg
        self.db = PaperExecutorDatabase(cfg.database_path)
        self.runtime = runtime_class(cfg, self.db, market_data=market_data)
        self.cohort_group = None
        self.runtime.recover()
        self._rate: dict[str, list[float]] = {}

    @property
    def mode(self) -> Mode:
        return self.runtime.mode

    def ingest(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.cohort_group is not None:
            return self.cohort_group.ingest(payload)
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
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                # Health clients may time out while the response is being
                # assembled under load. A disconnected reader is not a worker
                # failure and must not emit a misleading service traceback.
                return

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
                runtime_health = app.runtime.health()
                payload = health_payload(
                    app.cfg, app.db, app.runtime.mode, app.runtime.quote_manager.degraded,
                    runtime_health["observability"]["database_integrity_ok"],
                )
                payload.update(runtime_health)
                self._send(200, payload)
            elif parsed.path == "/api/paper/status":
                runtime_health = app.runtime.health()
                payload = health_payload(
                    app.cfg, app.db, app.runtime.mode, app.runtime.quote_manager.degraded,
                    runtime_health["observability"]["database_integrity_ok"],
                )
                payload.update(runtime_health)
                if app.cohort_group is not None:
                    payload["cohorts"] = app.cohort_group.summary()
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
                self._send(200, {"events": app.db.rows("paper_events")[-100:]})
            elif parsed.path == "/api/paper/cohorts":
                self._send(200, {"cohorts": app.cohort_group.summary() if app.cohort_group else []})
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
    import signal

    cfg = load_config(config_path)
    if os.environ.get("CIPHER_AUTOPILOT_COHORTS") == "1":
        from .alpaca_core_market_data import AlpacaCoreMarketData
        from .cohorts import CohortGroup, CohortRuntime, SharedObservations, configurations
        from .tradier_market_data import TradierMarketData

        provider = (AlpacaCoreMarketData(cfg.market_data) if cfg.market_data.provider == "alpaca_core"
                    else TradierMarketData(cfg.market_data))
        observations = SharedObservations(provider, cfg.runtime_root / "cohorts" / "observations.sqlite")
        apps = [PaperExecutorApp(c, market_data=observations.view(), runtime_class=CohortRuntime)
                for c in configurations(cfg)]
        if os.environ.get("CIPHER_AUTOPILOT_COST_AWARE") == "1":
            from .cost_aware import CostAwareRuntime, configuration
            apps.append(PaperExecutorApp(configuration(cfg), market_data=observations.view(),
                                         runtime_class=CostAwareRuntime))
        apps[0].cohort_group = CohortGroup(apps)
        apps[0].cohort_group.recover()
    else:
        apps = [PaperExecutorApp(cfg)]
    servers = []
    try:
        # Bind every endpoint before starting any worker. Port conflicts fail
        # startup without leaving a subset of experiments executing.
        for app in apps:
            servers.append(ThreadingHTTPServer((app.cfg.server.host, app.cfg.server.port), make_handler(app)))
        for app in apps:
            app.runtime.start()
    except Exception:
        for server in servers:
            server.server_close()
        for app in apps:
            app.runtime.stop()
        raise
    stop_event = threading.Event()

    def _request_shutdown(signum, frame) -> None:
        stop_event.set()

    # systemd sends SIGTERM on restart/stop; record a clean SHUTDOWN event and
    # stop the worker threads instead of dying mid-write.
    signal.signal(signal.SIGTERM, _request_shutdown)
    signal.signal(signal.SIGINT, _request_shutdown)
    server_threads = [threading.Thread(target=server.serve_forever, name=f"paper-executor-http-{index}", daemon=True)
                      for index, server in enumerate(servers)]
    for server_thread in server_threads:
        server_thread.start()
    try:
        while not stop_event.wait(0.5):
            if apps[0].cohort_group is not None:
                try:
                    apps[0].cohort_group.maintain()
                except Exception as exc:
                    apps[0].db.insert_system_event("COHORT_MAINTENANCE_FAILED", {"error": type(exc).__name__})
    finally:
        for server, server_thread in zip(servers, server_threads):
            server.shutdown()
            server_thread.join(timeout=5)
            server.server_close()
        for app in apps:
            app.runtime.stop()


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else None)
