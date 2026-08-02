from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .artifact_store import ArtifactReference, ArtifactStore
from .hashing import sha256_file, stable_id
from .models import AuditEvent, utc_now
from .registry import ResearchRegistry

_SOURCE_SUFFIXES = {".py", ".js", ".mjs", ".sh", ".ps1", ".json", ".toml", ".yaml", ".yml", ".md"}


@dataclass(frozen=True)
class InventoryFile:
    path: str
    lifecycle: str
    size_bytes: int
    sha256: str


class SystemInventoryBuilder:
    """Build a machine-readable inventory without traversing generated data."""

    DEFAULT_EXCLUDES = {
        ".git",
        ".pytest_cache",
        "__pycache__",
        "node_modules",
        ".venv",
        "venv",
        "data",
        "logs",
        "Stock data",
        "access-obsidian-complete-audit",
    }

    def __init__(
        self,
        root: str | Path,
        *,
        registry: ResearchRegistry,
        artifact_store: ArtifactStore,
        active_paths: Iterable[str],
        shadow_paths: Iterable[str],
        archived_prefixes: Iterable[str] = (),
        excludes: Iterable[str] = (),
    ):
        self.root = Path(root).resolve()
        self.registry = registry
        self.artifact_store = artifact_store
        self.active_paths = tuple(Path(value).as_posix().rstrip("/") for value in active_paths)
        self.shadow_paths = tuple(Path(value).as_posix().rstrip("/") for value in shadow_paths)
        self.archived_prefixes = tuple(Path(value).as_posix().rstrip("/") for value in archived_prefixes)
        self.excludes = self.DEFAULT_EXCLUDES | set(excludes)

    def build(self) -> tuple[dict[str, Any], ArtifactReference]:
        files: list[InventoryFile] = []
        for path in sorted(self.root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in _SOURCE_SUFFIXES:
                continue
            relative = path.relative_to(self.root)
            if any(part in self.excludes for part in relative.parts):
                continue
            files.append(
                InventoryFile(
                    path=relative.as_posix(),
                    lifecycle=self._lifecycle(relative.as_posix()),
                    size_bytes=path.stat().st_size,
                    sha256=sha256_file(path),
                )
            )
        code_hash = stable_id(
            "code",
            [{"path": item.path, "sha256": item.sha256} for item in files],
            length=64,
        )
        created_at = utc_now()
        payload = {
            "schema_version": 1,
            "created_at": created_at.isoformat(),
            "repository_root": str(self.root),
            "git_commit": self._git_commit(),
            "code_hash": code_hash,
            "counts": {
                "total": len(files),
                "active": sum(1 for item in files if item.lifecycle == "active"),
                "shadow": sum(1 for item in files if item.lifecycle == "shadow"),
                "experimental": sum(1 for item in files if item.lifecycle == "experimental"),
                "archived": sum(1 for item in files if item.lifecycle == "archived"),
            },
            "files": [item.__dict__ for item in files],
            "systemd_units": self._systemd_units(),
            "execution_boundary": {
                "research_terminal": "read_only",
                "shadow_executor": "simulation_only",
                "broker_adapter_present": False,
                "live_order_authorized": False,
            },
        }
        inventory_id = stable_id("inventory", payload)
        artifact = self.artifact_store.put_json(
            payload,
            metadata={"kind": "system_inventory", "inventory_id": inventory_id},
        )
        self.registry.register_artifact(artifact.to_dict())
        with self.registry.connect() as db:
            existing = db.execute(
                "select payload_json from inventory_snapshots where inventory_id = ?",
                (inventory_id,),
            ).fetchone()
            serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            if existing and existing["payload_json"] != serialized:
                raise RuntimeError("inventory ID collision")
            db.execute(
                "insert or ignore into inventory_snapshots(inventory_id, created_at, code_hash, payload_json) values (?, ?, ?, ?)",
                (inventory_id, created_at.isoformat(), code_hash, serialized),
            )
        self.registry.audit(
            AuditEvent(
                event_type="SYSTEM_INVENTORY_CREATED",
                entity_type="inventory",
                entity_id=inventory_id,
                occurred_at=created_at,
                payload={"code_hash": code_hash, "file_count": len(files), "artifact_id": artifact.artifact_id},
            )
        )
        return {"inventory_id": inventory_id, **payload}, artifact

    def _lifecycle(self, relative: str) -> str:
        if any(relative == prefix or relative.startswith(prefix + "/") for prefix in self.archived_prefixes):
            return "archived"
        if any(relative == prefix or relative.startswith(prefix + "/") for prefix in self.shadow_paths):
            return "shadow"
        if any(relative == prefix or relative.startswith(prefix + "/") for prefix in self.active_paths):
            return "active"
        return "experimental"

    def _git_commit(self) -> str:
        git = self.root / ".git"
        head = git / "HEAD"
        if not head.exists():
            return "UNVERSIONED"
        text = head.read_text(encoding="utf-8").strip()
        if text.startswith("ref: "):
            ref = git / text[5:]
            if ref.exists():
                value = ref.read_text(encoding="utf-8").strip()
                return value or "UNCOMMITTED"
            packed = git / "packed-refs"
            if packed.exists():
                for line in packed.read_text(encoding="utf-8").splitlines():
                    if not line.startswith("#") and line.endswith(" " + text[5:]):
                        return line.split(" ", 1)[0]
            return "UNCOMMITTED"
        return text or "UNCOMMITTED"

    def _systemd_units(self) -> list[str]:
        directory = self.root / "infra" / "gcp-cipher-vm" / "systemd"
        if not directory.exists():
            return []
        return sorted(path.name for path in directory.iterdir() if path.is_file())
