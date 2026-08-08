from __future__ import annotations

import importlib.util
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from conftest import require_artifact

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def load_registration_script():
    path = SCRIPTS / "register_holdout_c_canonical_dataset.py"
    name = "cipher_test_holdout_c_canonical_registration"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_protected_answer_key_is_exactly_744_and_11_of_12():
    # load_answer_key() reads the frozen price-only scope artifact internally.
    require_artifact(
        "data/market_quality/alpaca_holdout_c_price_only_scope_20260803T235944Z.json"
    )
    module = load_registration_script()
    _, _, answer = module.load_answer_key(module.DEFAULT_SCOPE, module.DEFAULT_COHORT)
    assert answer["partition_count"] == 744
    assert len(answer["partition_identities_sha256"]) == 744
    assert answer["selected_block"]["start"] == "2023-06-06"
    assert answer["selected_block"]["end"] == "2025-12-31"
    assert answer["selected_block"]["sessions"] == 638
    assert answer["selected_block"]["minimum_common_tickers"] == 8
    assert answer["selected_block"]["strict_independent_origins"] == 11
    assert answer["required_strict_independent_origins"] == 12
    assert answer["ranking_outcomes_evaluated"] is False
    assert answer["volume_features_or_evaluation"] is False
    assert answer["gate_relaxed"] is False


def make_bundle(tmp_path: Path):
    from core.research_platform.models import DatasetManifest, RawObjectManifest

    observed = datetime(2026, 8, 4, tzinfo=timezone.utc)
    raws = []
    for index in range(2):
        path = tmp_path / f"part-{index}.parquet"
        path.write_bytes(f"partition-{index}".encode())
        raws.append(
            RawObjectManifest(
                source="Alpaca SIP",
                dataset="test_holdout_bundle",
                uri=path.as_uri(),
                checksum=f"sha-{index}",
                checksum_method="sha256",
                size_bytes=path.stat().st_size,
                received_at=observed,
                available_at=observed,
                ingestion_run_id=f"run-{index}",
            )
        )
    dataset = DatasetManifest(
        name="test_holdout_bundle",
        created_at=observed + timedelta(minutes=1),
        availability_cutoff=observed,
        sources=("Alpaca SIP",),
        raw_object_ids=tuple(item.raw_object_id for item in raws),
        symbol_universe_id="test-universe",
        corporate_action_version="test-actions",
        normalizer_version="test-normalizer",
        schema_name="test-schema",
        row_counts={"partitions": 2},
        quality_checks={"passed": True},
        frozen=True,
    )
    return raws, dataset


def table_count(path: Path, table: str) -> int:
    with sqlite3.connect(path) as db:
        return int(db.execute(f"select count(*) from {table}").fetchone()[0])


def test_atomic_dataset_bundle_rolls_back_when_precommit_verification_fails(tmp_path: Path):
    from core.research_platform.registry import ResearchRegistry

    registry_path = tmp_path / "registry.sqlite"
    registry = ResearchRegistry(registry_path)
    raws, dataset = make_bundle(tmp_path)

    def reject(_db, _manifest):
        raise RuntimeError("protected baseline mismatch")

    with pytest.raises(RuntimeError, match="protected baseline mismatch"):
        registry.register_dataset_bundle(raws, dataset, precommit_validator=reject)

    assert table_count(registry_path, "raw_objects") == 0
    assert table_count(registry_path, "datasets") == 0
    assert table_count(registry_path, "dataset_raw_objects") == 0
    assert table_count(registry_path, "audit_events") == 0


def test_atomic_dataset_bundle_commits_raws_dataset_links_and_audits(tmp_path: Path):
    from core.research_platform.registry import ResearchRegistry

    registry_path = tmp_path / "registry.sqlite"
    registry = ResearchRegistry(registry_path)
    raws, dataset = make_bundle(tmp_path)
    observed = {}

    def verify(db, manifest):
        observed["links"] = int(
            db.execute(
                "select count(*) from dataset_raw_objects where dataset_id = ?",
                (manifest.dataset_id,),
            ).fetchone()[0]
        )

    result = registry.register_dataset_bundle(raws, dataset, precommit_validator=verify)
    assert result == {
        "raw_object_count": 2,
        "raw_objects_inserted": 2,
        "raw_objects_existing": 0,
        "dataset_inserted": True,
        "links_inserted": 2,
    }
    assert observed["links"] == 2
    assert table_count(registry_path, "raw_objects") == 2
    assert table_count(registry_path, "datasets") == 1
    assert table_count(registry_path, "dataset_raw_objects") == 2
    assert table_count(registry_path, "audit_events") == 4
