from __future__ import annotations

import importlib.util
import sqlite3
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest


SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "infra"
    / "gcp-cipher-vm"
    / "bin"
    / "backup-to-gcs.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location("cipher_backup_to_gcs", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sqlite_backup_uses_consistent_backup_api(tmp_path: Path):
    module = load_module()
    source = tmp_path / "source.sqlite"
    destination = tmp_path / "destination.sqlite"
    with sqlite3.connect(source) as db:
        db.execute("create table events(id integer primary key, value text)")
        db.execute("insert into events(value) values ('captured')")

    assert module.sqlite_safe_copy(source, destination)
    with sqlite3.connect(f"file:{destination}?mode=ro", uri=True) as db:
        assert db.execute("select value from events").fetchone() == ("captured",)


def test_sqlite_backup_never_falls_back_to_raw_copy(tmp_path: Path):
    module = load_module()
    source = tmp_path / "not-a-database.sqlite"
    destination = tmp_path / "destination.sqlite"
    source.write_text("not sqlite", encoding="utf-8")
    destination.write_text("partial", encoding="utf-8")

    assert not module.sqlite_safe_copy(source, destination)
    assert not destination.exists()


class FakeBlob:
    def __init__(self):
        self.metadata = None
        self.size = None

    def upload_from_filename(self, path: str):
        self.size = Path(path).stat().st_size

    def reload(self):
        return None


class FakeBucket:
    def __init__(self):
        self.objects: dict[str, FakeBlob] = {}

    def blob(self, name: str):
        return self.objects.setdefault(name, FakeBlob())


def test_verified_upload_records_application_checksum(tmp_path: Path):
    module = load_module()
    payload = tmp_path / "payload"
    payload.write_bytes(b"irreplaceable")
    bucket = FakeBucket()

    module.upload_verified(bucket, payload, "backup/payload", metadata={"kind": "test"})

    blob = bucket.objects["backup/payload"]
    assert blob.size == len(b"irreplaceable")
    assert blob.metadata["kind"] == "test"
    assert blob.metadata["cipher-sha256"] == module.sha256_file(payload)


def test_headroom_requires_largest_snapshot_plus_reserve(tmp_path: Path, monkeypatch):
    module = load_module()
    database = tmp_path / "large.sqlite"
    database.write_bytes(b"x" * 100)
    monkeypatch.setattr(module, "MIN_FREE_RESERVE_BYTES", 50)
    monkeypatch.setattr(module.shutil, "disk_usage", lambda _path: SimpleNamespace(free=149))

    with pytest.raises(RuntimeError, match="insufficient backup headroom"):
        module.require_backup_headroom([database], tmp_path)


def test_operational_tar_failure_is_not_silently_accepted(tmp_path: Path, monkeypatch):
    module = load_module()
    existing = tmp_path / "reports"
    existing.mkdir()
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "TAR_INCLUDES", ["reports"])

    def fail_tar(command, *, check):
        assert check is True
        assert "--ignore-failed-read" not in command
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(module.subprocess, "run", fail_tar)

    with pytest.raises(subprocess.CalledProcessError):
        module.create_operational_archive(tmp_path / "out.tar.zst", tmp_path, ["reports"])
