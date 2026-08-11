"""The rebuild recipes are what make excluding 9.8 GB from backup safe.

`data/historical_options` is skipped by backup-to-gcs.py as reproducible-from-Alpaca. That
holds only while the recipe survives, and `download_manifest.json` does not carry it: it
records `latest_run_config`, one run out of the 205 that built the leveraged_etf_wheel
dataset. The full set is `download_runs.config_json` inside each dataset database — inside
the skipped directory.
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import export_options_rebuild_recipes as exporter  # noqa: E402


def _dataset(directory: Path, name: str, runs: list[tuple[str, str, dict]]) -> Path:
    d = directory / name
    d.mkdir(parents=True)
    db = d / "historical_options.sqlite"
    with sqlite3.connect(db) as connection:
        connection.execute(
            """CREATE TABLE download_runs (
                 id INTEGER PRIMARY KEY, started_at TEXT, completed_at TEXT, status TEXT,
                 underlying TEXT, start_date TEXT, end_date TEXT, config_json TEXT, error TEXT)"""
        )
        for index, (underlying, status, config) in enumerate(runs, start=1):
            connection.execute(
                "INSERT INTO download_runs VALUES (?,?,?,?,?,?,?,?,?)",
                (index, "2026-01-01", "2026-01-01", status, underlying,
                 config.get("start_date"), config.get("end_date"), json.dumps(config), None),
            )
    (d / "download_manifest.json").write_text(
        json.dumps({"provider": "alpaca", "dataset_id": f"{name}-id", "status": "OK",
                    "latest_run_config": runs[-1][2]}),
        encoding="utf-8",
    )
    return d


def test_every_run_is_captured_not_only_the_last(tmp_path):
    """The whole point: the manifest keeps one run, the recipe keeps all of them."""
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    _dataset(source, "wheel", [
        ("NVDL", "completed", {"start_date": "2025-01-02", "end_date": "2025-01-02", "target_dte": 30}),
        ("SOXL", "completed", {"start_date": "2025-02-03", "end_date": "2025-02-03", "target_dte": 30}),
        ("TQQQ", "failed", {"start_date": "2025-03-04", "end_date": "2025-03-04", "target_dte": 30}),
    ])

    index = exporter.export(source, output)

    recipe = json.loads((output / "wheel.json").read_text())
    assert recipe["run_count"] == 3
    assert recipe["completed_run_count"] == 2, "a failed run is still part of the history"
    assert recipe["underlyings"] == ["NVDL", "SOXL", "TQQQ"]
    # Each run carries its own parsed config, not just the manifest's last one.
    assert [r["config"]["start_date"] for r in recipe["runs"]] == [
        "2025-01-02", "2025-02-03", "2025-03-04"
    ]
    assert recipe["provider"] == "alpaca"
    assert index["total_runs"] == 3


def test_a_dataset_without_a_database_is_reported_not_skipped_silently(tmp_path):
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    (source / "empty_dataset").mkdir()
    _dataset(source, "real", [("NVDL", "completed", {"start_date": "2025-01-02"})])

    index = exporter.export(source, output)

    assert index["datasets_without_database"] == ["empty_dataset"]
    assert [d["dataset"] for d in index["datasets"]] == ["real"]


def test_a_malformed_config_is_preserved_rather_than_dropped(tmp_path):
    """A config that will not parse is still the only record of what that run asked for."""
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    d = _dataset(source, "broken", [("NVDL", "completed", {"start_date": "2025-01-02"})])
    with sqlite3.connect(d / "historical_options.sqlite") as connection:
        connection.execute("UPDATE download_runs SET config_json = '{not json'")

    exporter.export(source, output)

    run = json.loads((output / "broken.json").read_text())["runs"][0]
    assert run["config"] == {"unparsed": "{not json"}


def test_the_backup_includes_the_recipes_and_refreshes_them_first():
    spec = importlib.util.spec_from_file_location(
        "backup_to_gcs", REPO / "infra/gcp-cipher-vm/bin/backup-to-gcs.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["backup_to_gcs"] = module
    spec.loader.exec_module(module)

    assert "cipher-system/data/options_rebuild_recipes" in module.TAR_INCLUDES
    # historical_options is included for its manifests and lab reports, and its bulk is
    # dropped by TAR_EXCLUDES rather than by omitting the path. The recipes are still needed
    # because the run configs live inside the excluded databases.
    assert "cipher-system/data/historical_options" in module.TAR_INCLUDES
    assert "*.sqlite" in module.TAR_EXCLUDES
    source = (REPO / "infra/gcp-cipher-vm/bin/backup-to-gcs.py").read_text(encoding="utf-8")
    refresh_at = source.index("refresh_options_rebuild_recipes()")
    tar_at = source.index("create_operational_archive(archive, ROOT, TAR_INCLUDES)")
    assert refresh_at < tar_at, "recipes must be refreshed before the tar is built"


def test_a_recipe_export_failure_warns_instead_of_failing_the_backup(monkeypatch):
    """Losing irreplaceable data is worse than carrying a stale recovery recipe."""
    spec = importlib.util.spec_from_file_location(
        "backup_to_gcs2", REPO / "infra/gcp-cipher-vm/bin/backup-to-gcs.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["backup_to_gcs2"] = module
    spec.loader.exec_module(module)

    monkeypatch.setattr(module, "RECIPE_EXPORTER", Path("/nonexistent/exporter.py"))
    warning = module.refresh_options_rebuild_recipes()
    assert warning and "missing" in warning


def test_nested_datasets_are_found_not_silently_skipped(tmp_path):
    """eod_indices_targeted nests one dataset per index; a top-level walk missed all three.

    That is the failure mode this whole export exists to prevent: a recovery path that looks
    complete because the thing it omitted never appeared in any output.
    """
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    parent = source / "eod_indices_targeted"
    for index in ("iwm", "qqq", "spy"):
        _dataset(parent, index, [(index.upper(), "completed", {"start_date": "2026-02-02"})])
    _dataset(source, "flat", [("NVDL", "completed", {"start_date": "2026-01-01"})])

    index_payload = exporter.export(source, output)

    names = sorted(p.stem for p in output.glob("*.json") if p.name != "index.json")
    assert names == [
        "eod_indices_targeted__iwm",
        "eod_indices_targeted__qqq",
        "eod_indices_targeted__spy",
        "flat",
    ]
    # Nested siblings must not collide on a bare directory name.
    assert len(names) == len(set(names))
    assert index_payload["total_runs"] == 4
    # The parent directory holds no database of its own but is not "missing" -- it contains
    # datasets, and reporting it as missing would be noise.
    assert "eod_indices_targeted" not in index_payload["datasets_without_database"]


def test_a_directory_with_neither_database_nor_nested_dataset_is_reported(tmp_path):
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    (source / "lab_outputs_only").mkdir()
    (source / "lab_outputs_only" / "report.json").write_text("{}", encoding="utf-8")
    _dataset(source, "real", [("NVDL", "completed", {"start_date": "2026-01-01"})])

    payload = exporter.export(source, output)
    assert payload["datasets_without_database"] == ["lab_outputs_only"]


def test_the_backup_carries_option_manifests_while_excluding_the_bulk():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "backup_to_gcs3", REPO / "infra/gcp-cipher-vm/bin/backup-to-gcs.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["backup_to_gcs3"] = module
    spec.loader.exec_module(module)

    assert "cipher-system/data/historical_options" in module.TAR_INCLUDES
    # The point of including it is the manifests and lab reports, not 8.77 GB of databases
    # or 913 MB of raw pages.
    for pattern in ("*.gz", "*.sqlite", "*.sqlite-wal", "*.sqlite-shm"):
        assert pattern in module.TAR_EXCLUDES, f"{pattern} must stay excluded"
