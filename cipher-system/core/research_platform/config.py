from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ResearchPlatformConfig:
    repository_root: Path
    registry_path: Path
    artifact_root: Path
    raw_lake_root: Path
    snapshot_root: Path
    warehouse_export_root: Path
    inventory_output_path: Path
    bigquery_project: str
    bigquery_dataset: str
    gcs_bucket: str | None
    cloud_writes_enabled: bool

    @classmethod
    def default(cls, repository_root: str | Path) -> "ResearchPlatformConfig":
        root = Path(repository_root).resolve()
        governance = root / "cipher-system" / "data" / "governance"
        return cls(
            repository_root=root,
            registry_path=governance / "research_registry.sqlite",
            artifact_root=governance / "artifacts",
            raw_lake_root=root / "cipher-system" / "data" / "raw_lake",
            snapshot_root=root / "cipher-system" / "data" / "research_snapshots",
            warehouse_export_root=root / "cipher-system" / "data" / "warehouse_exports",
            inventory_output_path=governance / "latest_system_inventory.json",
            bigquery_project=os.environ.get("GOOGLE_CLOUD_PROJECT", "cipher-project-not-configured"),
            bigquery_dataset="cipher_research",
            gcs_bucket=os.environ.get("CIPHER_RAW_LAKE_BUCKET") or None,
            cloud_writes_enabled=os.environ.get("CIPHER_ENABLE_CLOUD_WRITES") == "1",
        )

    @classmethod
    def load(cls, path: str | Path | None, repository_root: str | Path) -> "ResearchPlatformConfig":
        base = cls.default(repository_root)
        if path is None:
            return base
        candidate = Path(path)
        if not candidate.exists():
            return base
        payload = json.loads(candidate.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("research platform configuration must be an object")
        allowed = {
            "registry_path",
            "artifact_root",
            "raw_lake_root",
            "snapshot_root",
            "warehouse_export_root",
            "inventory_output_path",
            "bigquery_project",
            "bigquery_dataset",
            "gcs_bucket",
            "cloud_writes_enabled",
        }
        unknown = set(payload) - allowed
        if unknown:
            raise ValueError(f"unknown research platform configuration fields: {sorted(unknown)}")
        root = Path(repository_root).resolve()

        def resolve(name: str, default: Path) -> Path:
            value = Path(payload.get(name, default))
            return value.resolve() if value.is_absolute() else (root / value).resolve()

        config = cls(
            repository_root=root,
            registry_path=resolve("registry_path", base.registry_path),
            artifact_root=resolve("artifact_root", base.artifact_root),
            raw_lake_root=resolve("raw_lake_root", base.raw_lake_root),
            snapshot_root=resolve("snapshot_root", base.snapshot_root),
            warehouse_export_root=resolve("warehouse_export_root", base.warehouse_export_root),
            inventory_output_path=resolve("inventory_output_path", base.inventory_output_path),
            bigquery_project=str(payload.get("bigquery_project", base.bigquery_project)),
            bigquery_dataset=str(payload.get("bigquery_dataset", base.bigquery_dataset)),
            gcs_bucket=payload.get("gcs_bucket", base.gcs_bucket),
            cloud_writes_enabled=bool(payload.get("cloud_writes_enabled", base.cloud_writes_enabled)),
        )
        config.validate()
        return config

    def validate(self) -> None:
        # Production keeps mutable state outside the checkout and exposes it at
        # ``cipher-system/data`` through a symlink.  Validate against both the
        # repository and that one deliberate runtime mount: resolving a configured
        # path and then requiring it to remain under only ``repository_root`` makes
        # every production governance hook fail merely because the data directory
        # is mounted safely outside Git.
        data_root = (self.repository_root / "cipher-system" / "data").resolve()
        allowed_roots = (self.repository_root.resolve(), data_root)
        for path in (
            self.registry_path,
            self.artifact_root,
            self.raw_lake_root,
            self.snapshot_root,
            self.warehouse_export_root,
            self.inventory_output_path,
        ):
            resolved = path.resolve()
            if not any(resolved == root or resolved.is_relative_to(root) for root in allowed_roots):
                raise ValueError(
                    "governance path must remain under the repository or its "
                    f"configured data mount: {resolved}"
                )
        if self.cloud_writes_enabled and not self.gcs_bucket:
            raise ValueError("cloud writes require a configured gcs_bucket")

    def ensure_directories(self) -> None:
        for path in (
            self.registry_path.parent,
            self.artifact_root,
            self.raw_lake_root,
            self.snapshot_root,
            self.warehouse_export_root,
            self.inventory_output_path.parent,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "repository_root": str(self.repository_root),
            "registry_path": str(self.registry_path),
            "artifact_root": str(self.artifact_root),
            "raw_lake_root": str(self.raw_lake_root),
            "snapshot_root": str(self.snapshot_root),
            "warehouse_export_root": str(self.warehouse_export_root),
            "inventory_output_path": str(self.inventory_output_path),
            "bigquery_project": self.bigquery_project,
            "bigquery_dataset": self.bigquery_dataset,
            "gcs_bucket": self.gcs_bucket,
            "cloud_writes_enabled": self.cloud_writes_enabled,
        }
