from __future__ import annotations

import json
import mimetypes
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .artifact_store import ArtifactReference, ArtifactStore
from .hashing import hash_file, sha256_file, stable_id
from .models import DataDisposition, RawObjectManifest, utc_now
from .registry import ResearchRegistry


@dataclass(frozen=True)
class LakeObject:
    manifest: RawObjectManifest
    artifact: ArtifactReference | None
    local_frozen_path: str | None


class RawLake:
    """Immutable local raw-lake facade with optional GCS destination metadata.

    This class does not perform network writes. It creates stable manifests and
    frozen local objects that the existing backup/GCS tooling can transfer.
    """

    def __init__(
        self,
        root: str | Path,
        *,
        registry: ResearchRegistry,
        artifact_store: ArtifactStore,
        gcs_bucket: str | None = None,
    ):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.registry = registry
        self.artifact_store = artifact_store
        self.gcs_bucket = (gcs_bucket or "").removeprefix("gs://").strip("/") or None

    def freeze_file(
        self,
        source_path: str | Path,
        *,
        source: str,
        dataset: str,
        received_at: datetime | None = None,
        available_at: datetime | None = None,
        event_time_start: datetime | None = None,
        event_time_end: datetime | None = None,
        request_metadata: dict[str, Any] | None = None,
        ingestion_run_id: str | None = None,
        content_type: str | None = None,
    ) -> LakeObject:
        candidate = Path(source_path).resolve()
        if not candidate.is_file():
            raise FileNotFoundError(candidate)
        received = (received_at or utc_now()).astimezone(timezone.utc)
        available = (available_at or received).astimezone(timezone.utc)
        run_id = ingestion_run_id or stable_id(
            "ingest",
            {"source": source, "dataset": dataset, "received_at": received.isoformat(), "path": candidate.name},
        )
        digest = sha256_file(candidate)
        suffix = candidate.suffix.lower()
        target = (
            self.root
            / "raw"
            / source
            / dataset
            / f"{received.year:04d}"
            / f"{received.month:02d}"
            / f"{received.day:02d}"
            / run_id
            / f"{digest}{suffix}"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if sha256_file(target) != digest:
                raise RuntimeError(f"raw-lake collision at {target}")
        else:
            self._copy_immutable(candidate, target)
        uri = self._gcs_uri(target) if self.gcs_bucket else target.as_uri()
        manifest = RawObjectManifest(
            source=source,
            dataset=dataset,
            uri=uri,
            checksum=digest,
            checksum_method="sha256",
            size_bytes=target.stat().st_size,
            received_at=received,
            available_at=available,
            ingestion_run_id=run_id,
            content_type=content_type or mimetypes.guess_type(candidate.name)[0] or "application/octet-stream",
            event_time_start=event_time_start,
            event_time_end=event_time_end,
            request_metadata={
                **dict(request_metadata or {}),
                "original_path": str(candidate),
                "frozen_local_path": str(target),
            },
            disposition=DataDisposition.IMMUTABLE_RAW,
        )
        self.registry.register_raw_object(manifest)
        artifact = self.artifact_store.put_json(
            manifest.to_dict(),
            metadata={"kind": "raw_object_manifest", "raw_object_id": manifest.raw_object_id},
        )
        self.registry.register_artifact(artifact.to_dict())
        return LakeObject(manifest=manifest, artifact=artifact, local_frozen_path=str(target))

    def register_existing_immutable_file(
        self,
        source_path: str | Path,
        *,
        source: str,
        dataset: str,
        received_at: datetime | None = None,
        available_at: datetime | None = None,
        event_time_start: datetime | None = None,
        event_time_end: datetime | None = None,
        request_metadata: dict[str, Any] | None = None,
        ingestion_run_id: str | None = None,
        content_type: str | None = None,
    ) -> LakeObject:
        """Register a file that is already final and append-closed in place."""

        candidate = Path(source_path).resolve()
        if not candidate.is_file():
            raise FileNotFoundError(candidate)
        stat = candidate.stat()
        received = received_at or datetime.fromtimestamp(stat.st_mtime, timezone.utc)
        received = received.astimezone(timezone.utc)
        available = (available_at or received).astimezone(timezone.utc)
        digest = sha256_file(candidate)
        run_id = ingestion_run_id or stable_id(
            "ingest",
            {"path": str(candidate), "sha256": digest, "source": source, "dataset": dataset},
        )
        manifest = RawObjectManifest(
            source=source,
            dataset=dataset,
            uri=candidate.as_uri(),
            checksum=digest,
            checksum_method="sha256",
            size_bytes=stat.st_size,
            received_at=received,
            available_at=available,
            ingestion_run_id=run_id,
            content_type=content_type or mimetypes.guess_type(candidate.name)[0] or "application/octet-stream",
            event_time_start=event_time_start,
            event_time_end=event_time_end,
            request_metadata={
                **dict(request_metadata or {}),
                "append_closed": True,
                "registered_in_place": True,
                "mtime_ns": stat.st_mtime_ns,
            },
            disposition=DataDisposition.IMMUTABLE_RAW,
        )
        self.registry.register_raw_object(manifest)
        artifact = self.artifact_store.put_json(
            manifest.to_dict(),
            metadata={"kind": "raw_object_manifest", "raw_object_id": manifest.raw_object_id},
        )
        self.registry.register_artifact(artifact.to_dict())
        return LakeObject(manifest=manifest, artifact=artifact, local_frozen_path=str(candidate))

    def catalog_mutable_file(
        self,
        source_path: str | Path,
        *,
        source: str,
        dataset: str,
        available_at: datetime | None = None,
        metadata: dict[str, Any] | None = None,
        full_hash_max_bytes: int = 256 * 1024 * 1024,
    ) -> LakeObject:
        candidate = Path(source_path).resolve()
        if not candidate.is_file():
            raise FileNotFoundError(candidate)
        stat = candidate.stat()
        observed = datetime.fromtimestamp(stat.st_mtime, timezone.utc)
        digest, method = hash_file(candidate, full_hash_max_bytes=full_hash_max_bytes)
        manifest = RawObjectManifest(
            source=source,
            dataset=dataset,
            uri=candidate.as_uri(),
            checksum=digest,
            checksum_method=method,
            size_bytes=stat.st_size,
            received_at=observed,
            available_at=(available_at or observed),
            ingestion_run_id=stable_id(
                "catalog",
                {"path": str(candidate), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "digest": digest},
            ),
            content_type=mimetypes.guess_type(candidate.name)[0] or "application/octet-stream",
            request_metadata={
                **dict(metadata or {}),
                "mtime_ns": stat.st_mtime_ns,
                "catalog_only": True,
                "immutable_snapshot": False,
            },
            disposition=DataDisposition.MUTABLE_OPERATIONAL,
        )
        self.registry.register_raw_object(manifest)
        artifact = self.artifact_store.put_json(
            manifest.to_dict(),
            metadata={"kind": "operational_object_manifest", "raw_object_id": manifest.raw_object_id},
        )
        self.registry.register_artifact(artifact.to_dict())
        return LakeObject(manifest=manifest, artifact=artifact, local_frozen_path=None)

    def write_transfer_manifest(self, objects: list[LakeObject]) -> ArtifactReference:
        payload = {
            "schema_version": 1,
            "created_at": utc_now().isoformat(),
            "gcs_bucket": self.gcs_bucket,
            "objects": [
                {
                    "raw_object_id": item.manifest.raw_object_id,
                    "source_uri": item.local_frozen_path,
                    "destination_uri": item.manifest.uri,
                    "sha256": item.manifest.checksum,
                    "size_bytes": item.manifest.size_bytes,
                }
                for item in objects
                if item.local_frozen_path
            ],
        }
        reference = self.artifact_store.put_json(payload, metadata={"kind": "raw_lake_transfer_manifest"})
        self.registry.register_artifact(reference.to_dict())
        return reference

    def _gcs_uri(self, local_path: Path) -> str:
        relative = local_path.relative_to(self.root).as_posix()
        return f"gs://{self.gcs_bucket}/{relative}"

    @staticmethod
    def _copy_immutable(source: Path, target: Path) -> None:
        fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=str(target.parent))
        os.close(fd)
        temporary = Path(temporary_name)
        try:
            shutil.copyfile(source, temporary)
            with temporary.open("rb") as handle:
                os.fsync(handle.fileno())
            try:
                os.link(temporary, target)
            except FileExistsError:
                pass
        finally:
            temporary.unlink(missing_ok=True)
