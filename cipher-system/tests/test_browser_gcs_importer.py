from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "import_browser_gcs_payloads.py"
SPEC = importlib.util.spec_from_file_location("browser_gcs_importer", MODULE_PATH)
assert SPEC and SPEC.loader
importer = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = importer
SPEC.loader.exec_module(importer)


class _IngestHandler(BaseHTTPRequestHandler):
    requests: list[dict] = []

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length))
        self.__class__.requests.append(payload)
        response = {
            "ok": True,
            "request_id": f"request-{len(self.__class__.requests)}",
            "records_written": len(payload.get("cards") or []),
            "new_signals": len(payload.get("cards") or []),
            "invalid_records": 0,
        }
        body = json.dumps(response).encode("utf-8")
        self.send_response(202)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


@pytest.fixture(autouse=True)
def disable_production_governance_hooks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Importer tests must never register temporary fixtures in the real registry."""

    monkeypatch.setenv("CIPHER_GOVERNANCE_HOOKS", "0")


@pytest.fixture()
def ingest_server():
    _IngestHandler.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _IngestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/api/scanner-ingest"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _payload(batch_id: str = "batch-12345678") -> dict:
    return {
        "schema_version": 2,
        "source": "accessobsidian",
        "scan_type": "flash",
        "captured_at": "2026-07-27T15:30:00-04:00",
        "batch_id": batch_id,
        "sha256": "a" * 64,
        "cards": [
            {
                "ticker": "SPY",
                "direction": "bullish",
                "setup_type": "MOMENTUM PUSH",
                "spot": 635.0,
                "pivot": 634.5,
                "target": 637.0,
                "invalidation": 633.0,
                "score": 88,
            }
        ],
    }


def test_imports_once_and_deduplicates(tmp_path: Path, ingest_server: str) -> None:
    input_root = tmp_path / "uploaded"
    input_root.mkdir()
    payload_path = input_root / "flash_test.json"
    payload_path.write_text(json.dumps(_payload()), encoding="utf-8-sig")
    token_file = tmp_path / "token"
    token_file.write_text("secret-token", encoding="utf-8")
    ledger = tmp_path / "ledger.sqlite"

    first = importer.run_import(
        input_root=input_root,
        ledger_path=ledger,
        endpoint=ingest_server,
        token_file=token_file,
    )
    second = importer.run_import(
        input_root=input_root,
        ledger_path=ledger,
        endpoint=ingest_server,
        token_file=token_file,
    )

    assert first["counts"] == {"imported": 1}
    assert second["counts"] == {"skipped_already_imported": 1}
    assert len(_IngestHandler.requests) == 1
    assert _IngestHandler.requests[0]["gcs_import"]["file_sha256"]

    with sqlite3.connect(ledger) as db:
        row = db.execute(
            "select status, request_id, card_count from imported_batches"
        ).fetchone()
    assert row == ("imported", "request-1", 1)


def test_rejects_invalid_ticker_without_posting(
    tmp_path: Path, ingest_server: str
) -> None:
    input_root = tmp_path / "uploaded"
    input_root.mkdir()
    payload = _payload("batch-87654321")
    payload["cards"][0]["ticker"] = "NOT A TICKER"
    (input_root / "bad.json").write_text(json.dumps(payload), encoding="utf-8")
    token_file = tmp_path / "token"
    token_file.write_text("secret-token", encoding="utf-8")

    result = importer.run_import(
        input_root=input_root,
        ledger_path=tmp_path / "ledger.sqlite",
        endpoint=ingest_server,
        token_file=token_file,
    )

    assert result["counts"] == {"error": 1}
    assert "malformed ticker" in result["results"][0]["error"]
    assert _IngestHandler.requests == []


def test_market_window_uses_new_york_weekdays_and_buffers() -> None:
    assert importer.within_market_window(
        datetime(2026, 7, 27, 13, 25, tzinfo=timezone.utc)
    )
    assert importer.within_market_window(
        datetime(2026, 7, 27, 20, 25, tzinfo=timezone.utc)
    )
    assert not importer.within_market_window(
        datetime(2026, 7, 27, 20, 26, tzinfo=timezone.utc)
    )
    assert not importer.within_market_window(
        datetime(2026, 8, 1, 14, 0, tzinfo=timezone.utc)
    )


def test_dry_run_does_not_require_token(tmp_path: Path) -> None:
    input_root = tmp_path / "uploaded"
    input_root.mkdir()
    (input_root / "cluster.json").write_text(
        json.dumps(
            {
                **_payload("batch-abcdef12"),
                "scan_type": "cluster",
                "cards": [
                    {
                        "ticker": "NVDA",
                        "direction": "bullish",
                        "setup_type": "CLUSTER",
                        "spot": 195.0,
                        "target": 200.0,
                        "strength": 250,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = importer.run_import(
        input_root=input_root,
        ledger_path=tmp_path / "ledger.sqlite",
        endpoint="http://127.0.0.1:1/unused",
        token_file=tmp_path / "missing-token",
        dry_run=True,
    )

    assert result["counts"] == {"validated_dry_run": 1}
