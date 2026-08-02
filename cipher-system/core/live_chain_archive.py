"""Verified hot/cold lifecycle for large live option-chain JSONL files.

A source file is deleted only after its compressed object has been uploaded to
GCS, reloaded from the API, and verified against content metadata. The local
SQLite ledger makes retries idempotent and records enough provenance to restore
or audit each archived trading day.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Protocol

DAILY_FILE_RE = re.compile(r"^(?P<day>\d{4}-\d{2}-\d{2})_(?P<ticker>[A-Z0-9.\-]+)\.jsonl$")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class ArchiveCandidate:
    path: Path
    trading_day: date
    ticker: str


@dataclass(frozen=True)
class ArchiveReceipt:
    source_path: str
    source_sha256: str
    source_size_bytes: int
    compressed_sha256: str
    compressed_size_bytes: int
    object_uri: str
    archived_at: str
    source_deleted: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ArchiveStore(Protocol):
    def upload_verified(
        self,
        path: Path,
        *,
        object_name: str,
        metadata: dict[str, str],
    ) -> str: ...

    def verify(
        self,
        *,
        object_uri: str,
        compressed_sha256: str,
        compressed_size_bytes: int,
    ) -> bool: ...


class GCSArchiveStore:
    def __init__(self, bucket_name: str):
        if not bucket_name:
            raise ValueError("GCS bucket name is required")
        from google.cloud import storage

        self.client = storage.Client()
        self.bucket = self.client.bucket(bucket_name)
        self.bucket_name = bucket_name

    def upload_verified(
        self,
        path: Path,
        *,
        object_name: str,
        metadata: dict[str, str],
    ) -> str:
        blob = self.bucket.blob(object_name)
        blob.metadata = dict(metadata)
        blob.upload_from_filename(
            str(path),
            content_type="application/zstd",
            timeout=900,
            checksum="auto",
        )
        blob.reload()
        expected_size = path.stat().st_size
        expected_sha = metadata["compressed-sha256"]
        if int(blob.size or -1) != expected_size:
            raise RuntimeError(
                f"GCS size verification failed for gs://{self.bucket_name}/{object_name}: "
                f"expected {expected_size}, got {blob.size}"
            )
        remote_metadata = blob.metadata or {}
        if remote_metadata.get("compressed-sha256") != expected_sha:
            raise RuntimeError(
                f"GCS metadata verification failed for gs://{self.bucket_name}/{object_name}"
            )
        return f"gs://{self.bucket_name}/{object_name}"

    def verify(
        self,
        *,
        object_uri: str,
        compressed_sha256: str,
        compressed_size_bytes: int,
    ) -> bool:
        prefix = f"gs://{self.bucket_name}/"
        if not object_uri.startswith(prefix):
            return False
        blob = self.bucket.blob(object_uri[len(prefix) :])
        try:
            blob.reload()
        except Exception:
            return False
        metadata = blob.metadata or {}
        return (
            int(blob.size or -1) == int(compressed_size_bytes)
            and metadata.get("compressed-sha256") == compressed_sha256
        )


class ArchiveLedger:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("pragma journal_mode=WAL")
        db.execute("pragma busy_timeout=30000")
        return db

    def _migrate(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                create table if not exists archive_receipts (
                    source_path text primary key,
                    trading_day text not null,
                    ticker text not null,
                    source_sha256 text not null,
                    source_size_bytes integer not null,
                    compressed_sha256 text not null,
                    compressed_size_bytes integer not null,
                    object_uri text not null,
                    archived_at text not null,
                    source_deleted integer not null,
                    payload_json text not null
                );
                create unique index if not exists idx_archive_object_uri
                    on archive_receipts(object_uri);
                """
            )

    def get(self, source_path: str | Path) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "select * from archive_receipts where source_path = ?",
                (str(Path(source_path).resolve()),),
            ).fetchone()
        return dict(row) if row else None

    def record(self, candidate: ArchiveCandidate, receipt: ArchiveReceipt) -> None:
        payload = receipt.to_dict()
        with self._connect() as db:
            db.execute(
                """
                insert into archive_receipts (
                    source_path, trading_day, ticker, source_sha256,
                    source_size_bytes, compressed_sha256,
                    compressed_size_bytes, object_uri, archived_at,
                    source_deleted, payload_json
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(source_path) do update set
                    source_sha256=excluded.source_sha256,
                    source_size_bytes=excluded.source_size_bytes,
                    compressed_sha256=excluded.compressed_sha256,
                    compressed_size_bytes=excluded.compressed_size_bytes,
                    object_uri=excluded.object_uri,
                    archived_at=excluded.archived_at,
                    source_deleted=excluded.source_deleted,
                    payload_json=excluded.payload_json
                """,
                (
                    str(candidate.path.resolve()),
                    candidate.trading_day.isoformat(),
                    candidate.ticker,
                    receipt.source_sha256,
                    receipt.source_size_bytes,
                    receipt.compressed_sha256,
                    receipt.compressed_size_bytes,
                    receipt.object_uri,
                    receipt.archived_at,
                    int(receipt.source_deleted),
                    json.dumps(payload, sort_keys=True),
                ),
            )

    def mark_deleted(self, source_path: str | Path) -> None:
        with self._connect() as db:
            row = db.execute(
                "select payload_json from archive_receipts where source_path = ?",
                (str(Path(source_path).resolve()),),
            ).fetchone()
            if not row:
                return
            payload = json.loads(row[0])
            payload["source_deleted"] = True
            db.execute(
                "update archive_receipts set source_deleted = 1, payload_json = ? where source_path = ?",
                (json.dumps(payload, sort_keys=True), str(Path(source_path).resolve())),
            )


def select_archive_candidates(
    root: str | Path,
    *,
    keep_dates: int = 2,
    max_files: int | None = None,
) -> list[ArchiveCandidate]:
    directory = Path(root)
    candidates: list[ArchiveCandidate] = []
    for path in directory.glob("*.jsonl"):
        match = DAILY_FILE_RE.match(path.name)
        if not match:
            continue
        candidates.append(
            ArchiveCandidate(
                path=path.resolve(),
                trading_day=date.fromisoformat(match.group("day")),
                ticker=match.group("ticker"),
            )
        )
    dates = sorted({candidate.trading_day for candidate in candidates})
    retained = set(dates[-max(0, keep_dates) :]) if keep_dates > 0 else set()
    selected = [candidate for candidate in candidates if candidate.trading_day not in retained]
    selected.sort(key=lambda item: (item.trading_day, item.ticker, item.path.name))
    if max_files is not None and max_files > 0:
        selected = selected[:max_files]
    return selected


def _compress(source: Path, destination: Path, *, level: int = 3) -> None:
    command = [
        "ionice",
        "-c2",
        "-n7",
        "nice",
        "-n",
        "15",
        "zstd",
        "-q",
        "-T1",
        f"-{int(level)}",
        "-f",
        "-o",
        str(destination),
        str(source),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False, timeout=1800)
    if completed.returncode != 0:
        raise RuntimeError(
            f"zstd failed for {source}: {(completed.stderr or completed.stdout).strip()}"
        )


def archive_candidate(
    candidate: ArchiveCandidate,
    *,
    store: ArchiveStore,
    ledger: ArchiveLedger,
    object_prefix: str = "cold/live-option-chains",
    compression_level: int = 3,
) -> ArchiveReceipt:
    source = candidate.path
    existing = ledger.get(source)
    if existing:
        remote_ok = store.verify(
            object_uri=existing["object_uri"],
            compressed_sha256=existing["compressed_sha256"],
            compressed_size_bytes=int(existing["compressed_size_bytes"]),
        )
        if remote_ok:
            if source.exists():
                current_sha = sha256_file(source)
                if current_sha != existing["source_sha256"]:
                    raise RuntimeError(f"source changed after archive receipt: {source}")
                source.unlink()
                ledger.mark_deleted(source)
            payload = json.loads(existing["payload_json"])
            payload["source_deleted"] = True
            return ArchiveReceipt(**payload)

    if not source.is_file():
        raise FileNotFoundError(source)
    source_size = source.stat().st_size
    source_sha = sha256_file(source)
    object_name = (
        f"{object_prefix.rstrip('/')}/{candidate.trading_day:%Y/%m/%d}/"
        f"{source.name}.zst"
    )

    source.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{source.name}.", suffix=".zst.partial", dir=source.parent
    )
    os.close(fd)
    compressed = Path(temp_name)
    try:
        _compress(source, compressed, level=compression_level)
        compressed_sha = sha256_file(compressed)
        compressed_size = compressed.stat().st_size
        object_uri = store.upload_verified(
            compressed,
            object_name=object_name,
            metadata={
                "source-sha256": source_sha,
                "source-size-bytes": str(source_size),
                "compressed-sha256": compressed_sha,
                "compressed-size-bytes": str(compressed_size),
                "trading-day": candidate.trading_day.isoformat(),
                "ticker": candidate.ticker,
                "source-format": "jsonl",
                "compression": "zstd",
            },
        )
        receipt = ArchiveReceipt(
            source_path=str(source),
            source_sha256=source_sha,
            source_size_bytes=source_size,
            compressed_sha256=compressed_sha,
            compressed_size_bytes=compressed_size,
            object_uri=object_uri,
            archived_at=utcnow().isoformat(),
            source_deleted=False,
        )
        ledger.record(candidate, receipt)
        source.unlink()
        ledger.mark_deleted(source)
        return ArchiveReceipt(**{**receipt.to_dict(), "source_deleted": True})
    finally:
        compressed.unlink(missing_ok=True)


def archive_cold_files(
    root: str | Path,
    *,
    store: ArchiveStore,
    ledger_path: str | Path,
    keep_dates: int = 2,
    max_files: int | None = None,
    object_prefix: str = "cold/live-option-chains",
    compression_level: int = 3,
) -> dict[str, Any]:
    ledger = ArchiveLedger(ledger_path)
    candidates = select_archive_candidates(root, keep_dates=keep_dates, max_files=max_files)
    receipts: list[ArchiveReceipt] = []
    errors: list[dict[str, str]] = []
    for candidate in candidates:
        try:
            receipts.append(
                archive_candidate(
                    candidate,
                    store=store,
                    ledger=ledger,
                    object_prefix=object_prefix,
                    compression_level=compression_level,
                )
            )
        except Exception as exc:
            errors.append(
                {
                    "path": str(candidate.path),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            break
    source_bytes = sum(receipt.source_size_bytes for receipt in receipts)
    compressed_bytes = sum(receipt.compressed_size_bytes for receipt in receipts)
    return {
        "selected": len(candidates),
        "archived": len(receipts),
        "errors": errors,
        "source_bytes_archived": source_bytes,
        "compressed_bytes_uploaded": compressed_bytes,
        "local_bytes_freed": source_bytes,
        "compression_ratio": compressed_bytes / source_bytes if source_bytes else None,
        "receipts": [receipt.to_dict() for receipt in receipts],
        "completed_at": utcnow().isoformat(),
    }
