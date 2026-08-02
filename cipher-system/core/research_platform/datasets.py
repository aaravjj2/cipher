from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .artifact_store import ArtifactReference, ArtifactStore
from .hashing import stable_id
from .models import DatasetManifest, utc_now
from .raw_lake import LakeObject, RawLake
from .registry import ResearchRegistry

_TIMESTAMP_CANDIDATES = (
    "available_at",
    "event_time",
    "timestamp",
    "captured_at",
    "received_at",
    "provider_ts",
    "started_at",
    "created_at",
    "as_of",
    "date",
)


@dataclass(frozen=True)
class SQLiteTableProfile:
    table: str
    row_count: int | None
    columns: tuple[dict[str, Any], ...]
    indexes: tuple[str, ...]
    timestamp_column: str | None
    minimum_timestamp: str | None
    maximum_timestamp: str | None


@dataclass(frozen=True)
class SQLiteProfile:
    path: str
    size_bytes: int
    integrity_ok: bool
    journal_mode: str
    user_version: int
    tables: tuple[SQLiteTableProfile, ...]
    errors: tuple[str, ...]

    @property
    def row_counts(self) -> dict[str, int]:
        return {item.table: item.row_count for item in self.tables if item.row_count is not None}

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "size_bytes": self.size_bytes,
            "integrity_ok": self.integrity_ok,
            "journal_mode": self.journal_mode,
            "user_version": self.user_version,
            "tables": [
                {
                    "table": item.table,
                    "row_count": item.row_count,
                    "columns": list(item.columns),
                    "indexes": list(item.indexes),
                    "timestamp_column": item.timestamp_column,
                    "minimum_timestamp": item.minimum_timestamp,
                    "maximum_timestamp": item.maximum_timestamp,
                }
                for item in self.tables
            ],
            "errors": list(self.errors),
        }


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def inspect_sqlite(
    path: str | Path,
    *,
    include_row_counts: bool = True,
    include_timestamp_ranges: bool = True,
    integrity_mode: str = "full",
) -> SQLiteProfile:
    candidate = Path(path).resolve()
    errors: list[str] = []
    tables: list[SQLiteTableProfile] = []
    uri = f"file:{candidate.as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=30) as db:
        db.row_factory = sqlite3.Row
        if integrity_mode not in {"full", "quick", "skip"}:
            raise ValueError("integrity_mode must be full, quick, or skip")
        integrity_row = (
            db.execute("pragma integrity_check").fetchone()
            if integrity_mode == "full"
            else db.execute("pragma quick_check").fetchone()
            if integrity_mode == "quick"
            else ("not_run",)
        )
        integrity_ok = bool(integrity_row and integrity_row[0] in {"ok", "not_run"})
        journal_mode = str(db.execute("pragma journal_mode").fetchone()[0])
        user_version = int(db.execute("pragma user_version").fetchone()[0])
        table_rows = db.execute(
            """
            select name, sql from sqlite_master
            where type = 'table' and name not like 'sqlite_%'
            order by name
            """
        ).fetchall()
        for table_row in table_rows:
            table = str(table_row["name"])
            columns = tuple(dict(row) for row in db.execute(f"pragma table_info({_quote_identifier(table)})").fetchall())
            column_names = {str(item["name"]) for item in columns}
            indexes = tuple(
                str(row["name"])
                for row in db.execute(f"pragma index_list({_quote_identifier(table)})").fetchall()
            )
            row_count: int | None = None
            if include_row_counts:
                try:
                    row_count = int(db.execute(f"select count(*) from {_quote_identifier(table)}").fetchone()[0])
                except sqlite3.DatabaseError as exc:
                    errors.append(f"{table}:row_count:{exc}")
            timestamp_column = next((name for name in _TIMESTAMP_CANDIDATES if name in column_names), None)
            minimum_timestamp: str | None = None
            maximum_timestamp: str | None = None
            if include_timestamp_ranges and timestamp_column:
                try:
                    range_row = db.execute(
                        f"select min({_quote_identifier(timestamp_column)}), max({_quote_identifier(timestamp_column)}) "
                        f"from {_quote_identifier(table)}"
                    ).fetchone()
                    minimum_timestamp = None if range_row[0] is None else str(range_row[0])
                    maximum_timestamp = None if range_row[1] is None else str(range_row[1])
                except sqlite3.DatabaseError as exc:
                    errors.append(f"{table}:timestamp_range:{exc}")
            tables.append(
                SQLiteTableProfile(
                    table=table,
                    row_count=row_count,
                    columns=columns,
                    indexes=indexes,
                    timestamp_column=timestamp_column,
                    minimum_timestamp=minimum_timestamp,
                    maximum_timestamp=maximum_timestamp,
                )
            )
    return SQLiteProfile(
        path=str(candidate),
        size_bytes=candidate.stat().st_size,
        integrity_ok=integrity_ok,
        journal_mode=journal_mode,
        user_version=user_version,
        tables=tuple(tables),
        errors=tuple(errors),
    )


class DatasetService:
    def __init__(
        self,
        *,
        registry: ResearchRegistry,
        raw_lake: RawLake,
        artifact_store: ArtifactStore,
        snapshot_root: str | Path,
    ):
        self.registry = registry
        self.raw_lake = raw_lake
        self.artifact_store = artifact_store
        self.snapshot_root = Path(snapshot_root).resolve()
        self.snapshot_root.mkdir(parents=True, exist_ok=True)

    def freeze_sqlite(
        self,
        source_path: str | Path,
        *,
        dataset_name: str,
        source_name: str,
        availability_cutoff: datetime,
        symbol_universe_id: str,
        corporate_action_version: str,
        normalizer_version: str,
        schema_name: str,
        request_metadata: dict[str, Any] | None = None,
    ) -> tuple[DatasetManifest, SQLiteProfile, LakeObject, ArtifactReference]:
        source = Path(source_path).resolve()
        cutoff = availability_cutoff.astimezone(timezone.utc)
        snapshot_name = stable_id(
            "sqlite_snapshot",
            {"path": str(source), "cutoff": cutoff.isoformat(), "dataset_name": dataset_name},
        )
        destination = self.snapshot_root / dataset_name / f"{snapshot_name}.sqlite"
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            self._sqlite_backup(source, destination)
        profile = inspect_sqlite(destination)
        snapshot_received_at = datetime.fromtimestamp(destination.stat().st_mtime, timezone.utc)
        lake_object = self.raw_lake.register_existing_immutable_file(
            destination,
            source=source_name,
            dataset=dataset_name,
            received_at=snapshot_received_at,
            available_at=snapshot_received_at,
            request_metadata={
                **dict(request_metadata or {}),
                "source_database": str(source),
                "dataset_availability_cutoff": cutoff.isoformat(),
                "sqlite_profile": profile.to_dict(),
            },
            content_type="application/vnd.sqlite3",
        )
        quality_checks = {
            "passed": profile.integrity_ok and not profile.errors,
            "integrity_ok": profile.integrity_ok,
            "errors": list(profile.errors),
            "table_count": len(profile.tables),
            "nonempty_tables": sum(1 for value in profile.row_counts.values() if value > 0),
        }
        manifest = DatasetManifest(
            name=dataset_name,
            created_at=snapshot_received_at,
            availability_cutoff=cutoff,
            sources=(source_name,),
            raw_object_ids=(lake_object.manifest.raw_object_id,),
            symbol_universe_id=symbol_universe_id,
            corporate_action_version=corporate_action_version,
            normalizer_version=normalizer_version,
            schema_name=schema_name,
            row_counts=profile.row_counts,
            quality_checks=quality_checks,
            frozen=True,
        )
        self.registry.register_dataset(manifest)
        profile_artifact = self.artifact_store.put_json(
            profile.to_dict(),
            metadata={"kind": "sqlite_profile", "dataset_id": manifest.dataset_id},
        )
        self.registry.register_artifact(profile_artifact.to_dict())
        return manifest, profile, lake_object, profile_artifact

    def catalog_operational_sqlite(
        self,
        source_path: str | Path,
        *,
        dataset_name: str,
        source_name: str,
        symbol_universe_id: str = "universe_unknown",
        corporate_action_version: str = "corporate_actions_unknown",
        schema_name: str = "sqlite_operational_v1",
        include_row_counts: bool = False,
        include_timestamp_ranges: bool = True,
        integrity_mode: str = "quick",
    ) -> tuple[DatasetManifest, SQLiteProfile, LakeObject, ArtifactReference]:
        source = Path(source_path).resolve()
        profile = inspect_sqlite(
            source,
            include_row_counts=include_row_counts,
            include_timestamp_ranges=include_timestamp_ranges,
            integrity_mode=integrity_mode,
        )
        observed = datetime.fromtimestamp(source.stat().st_mtime, timezone.utc)
        lake_object = self.raw_lake.catalog_mutable_file(
            source,
            source=source_name,
            dataset=dataset_name,
            available_at=observed,
            metadata={"sqlite_profile": profile.to_dict()},
        )
        manifest = DatasetManifest(
            name=dataset_name,
            created_at=observed,
            availability_cutoff=observed,
            sources=(source_name,),
            raw_object_ids=(lake_object.manifest.raw_object_id,),
            symbol_universe_id=symbol_universe_id,
            corporate_action_version=corporate_action_version,
            normalizer_version="catalog_only_no_normalization",
            schema_name=schema_name,
            row_counts=profile.row_counts,
            quality_checks={
                "passed": profile.integrity_ok and not profile.errors,
                "integrity_ok": profile.integrity_ok,
                "errors": list(profile.errors),
                "catalog_only": True,
                "immutable_snapshot": False,
                "row_counts_collected": include_row_counts,
            },
            frozen=False,
        )
        self.registry.register_dataset(manifest)
        profile_artifact = self.artifact_store.put_json(
            profile.to_dict(),
            metadata={"kind": "sqlite_operational_profile", "dataset_id": manifest.dataset_id},
        )
        self.registry.register_artifact(profile_artifact.to_dict())
        return manifest, profile, lake_object, profile_artifact

    def register_normalized_files(
        self,
        files: Iterable[str | Path],
        *,
        dataset_name: str,
        source_names: Iterable[str],
        availability_cutoff: datetime,
        symbol_universe_id: str,
        corporate_action_version: str,
        normalizer_version: str,
        schema_name: str,
        row_counts: dict[str, int],
        quality_checks: dict[str, Any],
    ) -> DatasetManifest:
        sources = tuple(source_names)
        objects = [
            self.raw_lake.freeze_file(
                path,
                source=sources[0] if len(sources) == 1 else "normalized_multi_source",
                dataset=dataset_name,
                available_at=availability_cutoff,
                request_metadata={"normalized": True, "normalizer_version": normalizer_version},
            )
            for path in files
        ]
        manifest = DatasetManifest(
            name=dataset_name,
            created_at=utc_now(),
            availability_cutoff=availability_cutoff,
            sources=sources,
            raw_object_ids=tuple(item.manifest.raw_object_id for item in objects),
            symbol_universe_id=symbol_universe_id,
            corporate_action_version=corporate_action_version,
            normalizer_version=normalizer_version,
            schema_name=schema_name,
            row_counts=row_counts,
            quality_checks=quality_checks,
            frozen=True,
        )
        self.registry.register_dataset(manifest)
        return manifest

    @staticmethod
    def _sqlite_backup(source: Path, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            source_uri = f"file:{source.as_posix()}?mode=ro"
            with sqlite3.connect(source_uri, uri=True, timeout=60) as src, sqlite3.connect(destination) as dst:
                src.backup(dst, pages=4096)
                dst.execute("pragma wal_checkpoint(FULL)")
                integrity = dst.execute("pragma integrity_check").fetchone()
                if not integrity or integrity[0] != "ok":
                    raise RuntimeError(f"SQLite snapshot integrity failed for {source}")
        except Exception:
            # A failed backup must not be catalogued later as a frozen dataset.
            destination.unlink(missing_ok=True)
            raise
