from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .hashing import canonical_json, sha256_bytes


@dataclass(frozen=True)
class ArtifactReference:
    artifact_id: str
    sha256: str
    size_bytes: int
    content_type: str
    data_path: str
    metadata_path: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "content_type": self.content_type,
            "data_path": self.data_path,
            "metadata_path": self.metadata_path,
            "created_at": self.created_at,
        }


class ArtifactIntegrityError(RuntimeError):
    pass


class ArtifactStore:
    """Content-addressed immutable artifact store.

    Files are written once under a SHA-256 path. Reusing the same payload is
    idempotent. Metadata is immutable and excluded from the payload digest so
    callers can independently verify the artifact bytes.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _paths(self, digest: str) -> tuple[Path, Path]:
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise ValueError("invalid SHA-256 digest")
        directory = self.root / "sha256" / digest[:2] / digest[2:4]
        return directory / digest, directory / f"{digest}.metadata.json"

    def put_bytes(
        self,
        payload: bytes,
        *,
        content_type: str = "application/octet-stream",
        metadata: dict[str, Any] | None = None,
    ) -> ArtifactReference:
        digest = sha256_bytes(payload)
        data_path, metadata_path = self._paths(digest)
        data_path.parent.mkdir(parents=True, exist_ok=True)
        created_at = datetime.now(timezone.utc).isoformat()
        metadata_payload = {
            "artifact_id": f"artifact_{digest[:24]}",
            "sha256": digest,
            "size_bytes": len(payload),
            "content_type": content_type,
            "created_at": created_at,
            "metadata": dict(metadata or {}),
        }
        if data_path.exists():
            existing = data_path.read_bytes()
            if sha256_bytes(existing) != digest:
                raise ArtifactIntegrityError(f"artifact path is corrupted: {data_path}")
        else:
            self._atomic_create(data_path, payload)
        if metadata_path.exists():
            existing_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if existing_metadata.get("sha256") != digest or int(existing_metadata.get("size_bytes", -1)) != len(payload):
                raise ArtifactIntegrityError(f"artifact metadata is corrupted: {metadata_path}")
            created_at = str(existing_metadata.get("created_at") or created_at)
            content_type = str(existing_metadata.get("content_type") or content_type)
        else:
            self._atomic_create(metadata_path, (canonical_json(metadata_payload) + "\n").encode("utf-8"))
        return ArtifactReference(
            artifact_id=f"artifact_{digest[:24]}",
            sha256=digest,
            size_bytes=len(payload),
            content_type=content_type,
            data_path=str(data_path),
            metadata_path=str(metadata_path),
            created_at=created_at,
        )

    def put_text(
        self,
        text: str,
        *,
        content_type: str = "text/plain; charset=utf-8",
        metadata: dict[str, Any] | None = None,
    ) -> ArtifactReference:
        return self.put_bytes(text.encode("utf-8"), content_type=content_type, metadata=metadata)

    def put_json(self, value: Any, *, metadata: dict[str, Any] | None = None) -> ArtifactReference:
        return self.put_text(
            canonical_json(value) + "\n",
            content_type="application/json",
            metadata=metadata,
        )

    def put_file(
        self,
        source: str | Path,
        *,
        content_type: str = "application/octet-stream",
        metadata: dict[str, Any] | None = None,
    ) -> ArtifactReference:
        candidate = Path(source)
        return self.put_bytes(candidate.read_bytes(), content_type=content_type, metadata=metadata)

    def get_bytes(self, artifact_id_or_digest: str) -> bytes:
        digest = self._resolve_digest(artifact_id_or_digest)
        data_path, _ = self._paths(digest)
        payload = data_path.read_bytes()
        if sha256_bytes(payload) != digest:
            raise ArtifactIntegrityError(f"artifact checksum mismatch: {artifact_id_or_digest}")
        return payload

    def verify(self, artifact_id_or_digest: str) -> bool:
        try:
            self.get_bytes(artifact_id_or_digest)
            digest = self._resolve_digest(artifact_id_or_digest)
            _, metadata_path = self._paths(digest)
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            return metadata.get("sha256") == digest
        except (OSError, ValueError, json.JSONDecodeError, ArtifactIntegrityError):
            return False

    @staticmethod
    def _resolve_digest(value: str) -> str:
        result = value.removeprefix("artifact_")
        if len(result) == 24:
            raise ValueError("abbreviated artifact IDs require registry resolution")
        if len(result) != 64:
            raise ValueError("artifact digest must contain the complete SHA-256 value")
        return result

    @staticmethod
    def _atomic_create(path: Path, payload: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                pass
        finally:
            temporary.unlink(missing_ok=True)
