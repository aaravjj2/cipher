from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, BinaryIO


def _json_default(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, set):
        return sorted(value)
    raise TypeError(f"Unsupported canonical JSON value: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """Return a deterministic JSON representation suitable for content IDs."""

    return json.dumps(
        value,
        default=_json_default,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_text(payload: str) -> str:
    return sha256_bytes(payload.encode("utf-8"))


def stable_id(prefix: str, value: Any, *, length: int = 24) -> str:
    if not prefix or any(ch.isspace() for ch in prefix):
        raise ValueError("ID prefix must be non-empty and contain no whitespace")
    if length < 12 or length > 64:
        raise ValueError("ID digest length must be between 12 and 64")
    return f"{prefix}_{sha256_text(canonical_json(value))[:length]}"


def _stream_hash(handle: BinaryIO, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    while True:
        block = handle.read(chunk_size)
        if not block:
            break
        digest.update(block)
    return digest.hexdigest()


def sha256_file(path: str | Path) -> str:
    candidate = Path(path)
    with candidate.open("rb") as handle:
        return _stream_hash(handle)


def sampled_file_fingerprint(
    path: str | Path,
    *,
    sample_bytes: int = 1024 * 1024,
) -> str:
    """Fingerprint a large mutable file without pretending it is a full hash.

    The identity includes file metadata plus the first and last sample. It is
    appropriate for inventory/catalog records, not immutable research snapshots.
    """

    candidate = Path(path)
    stat = candidate.stat()
    size = stat.st_size
    digest = hashlib.sha256()
    digest.update(f"sampled-v1:{size}:{stat.st_mtime_ns}".encode("ascii"))
    with candidate.open("rb") as handle:
        digest.update(handle.read(sample_bytes))
        if size > sample_bytes:
            handle.seek(max(0, size - sample_bytes), os.SEEK_SET)
            digest.update(handle.read(sample_bytes))
    return f"sampled:{digest.hexdigest()}"


def hash_file(
    path: str | Path,
    *,
    full_hash_max_bytes: int = 256 * 1024 * 1024,
    force_full: bool = False,
) -> tuple[str, str]:
    """Return ``(digest, method)`` while making sampled hashes explicit."""

    candidate = Path(path)
    if force_full or candidate.stat().st_size <= full_hash_max_bytes:
        return sha256_file(candidate), "sha256"
    return sampled_file_fingerprint(candidate), "sampled_sha256"
