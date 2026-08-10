from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.research_platform.config import ResearchPlatformConfig


def test_config_allows_the_repository_data_symlink(tmp_path: Path):
    repository = tmp_path / "repository"
    runtime = tmp_path / "runtime"
    (repository / "cipher-system").mkdir(parents=True)
    (runtime / "data").mkdir(parents=True)
    (repository / "cipher-system" / "data").symlink_to(
        runtime / "data", target_is_directory=True
    )
    config_path = repository / "research-platform.json"
    config_path.write_text(
        json.dumps(
            {
                "registry_path": "cipher-system/data/governance/registry.sqlite",
                "artifact_root": "cipher-system/data/governance/artifacts",
                "raw_lake_root": "cipher-system/data/raw_lake",
                "snapshot_root": "cipher-system/data/research_snapshots",
                "warehouse_export_root": "cipher-system/data/warehouse_exports",
                "inventory_output_path": "cipher-system/data/governance/inventory.json",
            }
        ),
        encoding="utf-8",
    )

    config = ResearchPlatformConfig.load(config_path, repository)

    assert config.registry_path == runtime / "data" / "governance" / "registry.sqlite"
    assert config.raw_lake_root == runtime / "data" / "raw_lake"


def test_config_still_rejects_an_unrelated_external_path(tmp_path: Path):
    repository = tmp_path / "repository"
    repository.mkdir()
    outside = tmp_path / "unrelated" / "registry.sqlite"
    config_path = repository / "research-platform.json"
    config_path.write_text(
        json.dumps({"registry_path": str(outside)}), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="repository or its configured data mount"):
        ResearchPlatformConfig.load(config_path, repository)
