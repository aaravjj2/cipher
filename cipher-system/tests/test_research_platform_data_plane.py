from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.research_platform.artifact_store import ArtifactStore
from core.research_platform.canonical_exports import CanonicalSQLiteExporter
from core.research_platform.datasets import DatasetService, inspect_sqlite
from core.research_platform.raw_lake import RawLake
from core.research_platform.registry import ResearchRegistry
from core.research_platform.warehouse import BigQueryWarehousePlan

NOW = datetime(2026, 8, 1, 15, 0, tzinfo=timezone.utc)


def make_db(path: Path) -> None:
    with sqlite3.connect(path) as db:
        db.execute(
            "create table bars(symbol text not null, timestamp text not null, close real not null, primary key(symbol, timestamp))"
        )
        db.executemany(
            "insert into bars values (?, ?, ?)",
            [
                ("SPY", "2026-08-01T14:00:00+00:00", 100.0),
                ("SPY", "2026-08-01T14:05:00+00:00", 101.0),
            ],
        )


def services(tmp_path: Path):
    registry = ResearchRegistry(tmp_path / "registry.sqlite")
    artifacts = ArtifactStore(tmp_path / "artifacts")
    lake = RawLake(tmp_path / "lake", registry=registry, artifact_store=artifacts)
    datasets = DatasetService(
        registry=registry,
        raw_lake=lake,
        artifact_store=artifacts,
        snapshot_root=tmp_path / "snapshots",
    )
    return registry, artifacts, lake, datasets


def test_sqlite_profile_and_frozen_snapshot(tmp_path: Path):
    source = tmp_path / "bars.sqlite"
    make_db(source)
    profile = inspect_sqlite(source)
    assert profile.integrity_ok
    assert profile.row_counts == {"bars": 2}
    assert profile.tables[0].minimum_timestamp == "2026-08-01T14:00:00+00:00"

    registry, artifacts, lake, datasets = services(tmp_path / "platform")
    manifest, frozen_profile, raw, profile_artifact = datasets.freeze_sqlite(
        source,
        dataset_name="bars_frozen",
        source_name="test_bars",
        availability_cutoff=NOW,
        symbol_universe_id="universe_spy",
        corporate_action_version="ca_v1",
        normalizer_version="normalizer_v1",
        schema_name="bars_v1",
    )
    assert manifest.frozen
    assert manifest.quality_passed
    assert frozen_profile.row_counts["bars"] == 2
    assert raw.local_frozen_path is not None
    assert Path(raw.local_frozen_path).is_file()
    assert profile_artifact.artifact_id.startswith("artifact_")
    assert registry.counts()["datasets"] == 1


def test_operational_catalog_is_explicitly_mutable(tmp_path: Path):
    source = tmp_path / "ops.sqlite"
    make_db(source)
    registry, artifacts, lake, datasets = services(tmp_path / "platform")
    manifest, profile, raw, _ = datasets.catalog_operational_sqlite(
        source,
        dataset_name="ops",
        source_name="test",
        include_row_counts=False,
    )
    assert not manifest.frozen
    assert raw.local_frozen_path is None
    assert raw.manifest.disposition.value == "mutable_operational"
    assert raw.manifest.request_metadata["immutable_snapshot"] is False
    assert profile.integrity_ok


def test_canonical_export_requires_frozen_dataset_and_preserves_cutoff(tmp_path: Path):
    source = tmp_path / "historical.sqlite"
    with sqlite3.connect(source) as db:
        db.execute(
            """
            create table historical_bars(
                symbol text not null, timestamp text not null, open real, high real,
                low real, close real, volume real, vwap real, trades integer,
                primary key(symbol, timestamp)
            )
            """
        )
        db.execute(
            "insert into historical_bars values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("SPY", "2026-07-31T20:00:00+00:00", 100, 102, 99, 101, 1000, 100.5, 50),
        )
    registry, artifacts, lake, datasets = services(tmp_path / "platform")
    manifest, _, raw, _ = datasets.freeze_sqlite(
        source,
        dataset_name="historical_frozen",
        source_name="test",
        availability_cutoff=NOW,
        symbol_universe_id="spy",
        corporate_action_version="ca_v1",
        normalizer_version="bars_v1",
        schema_name="historical_bars_v1",
    )
    warehouse = BigQueryWarehousePlan(
        project="project",
        dataset="cipher_research",
        export_root=tmp_path / "exports",
        artifact_store=artifacts,
        registry=registry,
    )
    export = CanonicalSQLiteExporter(registry, warehouse).export(
        dataset_id=manifest.dataset_id,
        sqlite_path=raw.local_frozen_path,
        kind="historical_bars",
    )[0]
    row = json.loads(Path(export.jsonl_path).read_text(encoding="utf-8"))
    assert row["symbol"] == "SPY"
    assert row["event_time"] == "2026-07-31T20:00:00+00:00"
    assert row["available_at"] == NOW.isoformat()
    assert row["raw_object_id"] == manifest.raw_object_ids[0]


def test_bigquery_plan_requires_availability_and_emits_partitioned_ddl(tmp_path: Path):
    registry = ResearchRegistry(tmp_path / "registry.sqlite")
    artifacts = ArtifactStore(tmp_path / "artifacts")
    plan = BigQueryWarehousePlan(
        project="project",
        dataset="cipher_research",
        export_root=tmp_path / "exports",
        artifact_store=artifacts,
        registry=registry,
    )
    ddl = plan.ddl()
    assert "CREATE TABLE IF NOT EXISTS `project.cipher_research.market_bars`" in ddl
    assert "PARTITION BY DATE(`available_at`)" in ddl
    export = plan.export_rows(
        "market_bars",
        [
            {
                "record_id": "row1",
                "event_time": NOW,
                "received_at": NOW,
                "available_at": NOW,
                "source": "test",
                "raw_object_id": "raw_test",
                "schema_version": 1,
                "symbol": "SPY",
                "timeframe": "5m",
                "close": 100.0,
                "payload_json": {},
            }
        ],
    )
    assert export.row_count == 1
    assert Path(export.jsonl_path).is_file()
    line = json.loads(Path(export.jsonl_path).read_text(encoding="utf-8"))
    assert line["available_at"].endswith("+00:00")
    with pytest.raises(ValueError, match="available_at"):
        plan.export_rows("market_bars", [{"record_id": "bad"}])
