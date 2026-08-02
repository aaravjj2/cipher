from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

CORE = Path(__file__).resolve().parents[1] / "core"
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
for path in (CORE, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from hermes_delivery import send_hermes_message
from live_chain_archive import ArchiveLedger, ArchiveStore, archive_cold_files, sha256_file


def _executable(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "fake-hermes"
    path.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
    path.chmod(0o755)
    return path


def test_hermes_delivery_accepts_acknowledgement_from_lingering_wrapper(tmp_path, monkeypatch):
    executable = _executable(
        tmp_path,
        "import time\nprint('Sent to telegram home channel (chat_id: 1)', flush=True)\ntime.sleep(30)\n",
    )
    monkeypatch.setenv("HERMES_BIN", str(executable))
    assert send_hermes_message("hello", target="telegram", timeout_seconds=3) == 0


def test_hermes_delivery_preserves_real_failure(tmp_path, monkeypatch):
    executable = _executable(
        tmp_path,
        "import sys\nprint('delivery failed', flush=True)\nsys.exit(7)\n",
    )
    monkeypatch.setenv("HERMES_BIN", str(executable))
    assert send_hermes_message("hello", target="telegram", timeout_seconds=3) == 7


def test_hermes_delivery_returns_timeout_without_acknowledgement(tmp_path, monkeypatch):
    executable = _executable(tmp_path, "import time\ntime.sleep(30)\n")
    monkeypatch.setenv("HERMES_BIN", str(executable))
    assert send_hermes_message("hello", target="telegram", timeout_seconds=1) == 124


class FakeArchiveStore(ArchiveStore):
    def __init__(self, root: Path):
        self.root = root
        self.metadata: dict[str, dict[str, str]] = {}

    def upload_verified(self, path: Path, *, object_name: str, metadata: dict[str, str]) -> str:
        destination = self.root / object_name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
        self.metadata[object_name] = dict(metadata)
        return f"fake://bucket/{object_name}"

    def verify(self, *, object_uri: str, compressed_sha256: str, compressed_size_bytes: int) -> bool:
        prefix = "fake://bucket/"
        if not object_uri.startswith(prefix):
            return False
        object_name = object_uri[len(prefix) :]
        path = self.root / object_name
        return (
            path.is_file()
            and path.stat().st_size == compressed_size_bytes
            and sha256_file(path) == compressed_sha256
            and self.metadata.get(object_name, {}).get("compressed-sha256") == compressed_sha256
        )


def test_live_chain_archive_uploads_verifies_and_prunes_only_cold_dates(tmp_path):
    if shutil.which("zstd") is None:
        pytest.skip("zstd is required")
    source = tmp_path / "chains"
    source.mkdir()
    old = source / "2026-07-30_NVDA.jsonl"
    middle = source / "2026-07-31_NVDA.jsonl"
    hot = source / "2026-08-01_NVDA.jsonl"
    old.write_text('{"row": 1}\n' * 200, encoding="utf-8")
    middle.write_text('{"row": 2}\n' * 200, encoding="utf-8")
    hot.write_text('{"row": 3}\n' * 200, encoding="utf-8")

    store = FakeArchiveStore(tmp_path / "remote")
    ledger_path = tmp_path / "archive.sqlite"
    result = archive_cold_files(
        source,
        store=store,
        ledger_path=ledger_path,
        keep_dates=1,
    )

    assert result["archived"] == 2
    assert not old.exists()
    assert not middle.exists()
    assert hot.exists()
    assert result["local_bytes_freed"] > result["compressed_bytes_uploaded"]
    ledger = ArchiveLedger(ledger_path)
    assert ledger.get(old)["source_deleted"] == 1
    assert ledger.get(middle)["source_deleted"] == 1


def _load_data_health_module():
    path = SCRIPTS / "hermes_data_health_alerts.py"
    spec = importlib.util.spec_from_file_location("hermes_data_health_alerts_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_post_market_maintenance_due_logic_is_idempotent():
    module = _load_data_health_module()
    ny = ZoneInfo("America/New_York")
    state: dict = {}
    before = module.run_post_market_maintenance(
        state,
        dry_run=True,
        now_et=datetime(2026, 7, 31, 16, 0, tzinfo=ny),
    )
    assert before["status"] == "not_due"
    due = module.run_post_market_maintenance(
        state,
        dry_run=True,
        now_et=datetime(2026, 7, 31, 16, 45, tzinfo=ny),
    )
    assert due["status"] == "dry_run"
    state["maintenance"] = {"last_successful_day": "2026-07-31"}
    repeated = module.run_post_market_maintenance(
        state,
        dry_run=False,
        now_et=datetime(2026, 7, 31, 16, 45, tzinfo=ny),
    )
    assert repeated["status"] == "already_completed"
