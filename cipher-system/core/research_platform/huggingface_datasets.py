"""Revision-pinned local ingestion helpers for approved Hugging Face datasets."""
from __future__ import annotations

import csv
import importlib.util
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from huggingface_hub import HfApi, hf_hub_download

from .bootstrap import ResearchPlatform
from .config import ResearchPlatformConfig


@dataclass(frozen=True, slots=True)
class HuggingFaceDatasetSource:
    name: str
    repo_id: str
    revision: str
    dataset: str
    files: tuple[str, ...]


OPTIONS_IV_SP500 = HuggingFaceDatasetSource(
    name="options_iv_sp500",
    repo_id="gauss314/options-IV-SP500",
    revision="34f269b94a2680054d327a8f3c303facc7c7ed3f",
    dataset="options_iv_sp500_daily",
    files=("data_IV_USA.csv",),
)

OHLCV_1M = HuggingFaceDatasetSource(
    name="ohlcv_1m",
    repo_id="mito0o852/OHLCV-1m",
    revision="776328445b7ac6e7815ef3a483e9c8ded1eb6d56",
    dataset="ohlcv_1m_monthly",
    files=(),
)


class HuggingFaceDatasetError(RuntimeError):
    """Raised when an approved public dataset changes or cannot be inspected."""


def approved_source(name: str) -> HuggingFaceDatasetSource:
    sources = {source.name: source for source in (OPTIONS_IV_SP500, OHLCV_1M)}
    try:
        return sources[name]
    except KeyError as exc:
        raise HuggingFaceDatasetError(f"unknown approved dataset: {name}") from exc


def verify_revision(source: HuggingFaceDatasetSource, *, api: HfApi | None = None) -> dict[str, Any]:
    """Return remote metadata only when the pinned revision is still available."""
    info = (api or HfApi()).dataset_info(source.repo_id, revision=source.revision, files_metadata=True)
    if info.sha != source.revision:
        raise HuggingFaceDatasetError(
            f"revision mismatch for {source.repo_id}: expected {source.revision}, got {info.sha}"
        )
    files = {item.rfilename: item.size for item in info.siblings or []}
    return {
        "repo_id": source.repo_id,
        "revision": source.revision,
        "private": bool(info.private),
        "gated": bool(info.gated),
        "files": files,
    }


def download_approved_file(
    source: HuggingFaceDatasetSource,
    filename: str,
    *,
    destination_root: str | Path,
    downloader: Callable[..., str] = hf_hub_download,
) -> Path:
    """Resumably download one revision-pinned file into Cipher's local data root."""
    filename = str(filename or "").strip()
    if not filename:
        raise HuggingFaceDatasetError("filename is required")
    if source.files and filename not in source.files:
        raise HuggingFaceDatasetError(f"{filename} is not approved for {source.name}")
    if source is OHLCV_1M and not filename.startswith("data/ohlcv_"):
        raise HuggingFaceDatasetError("OHLCV files must be monthly data/ohlcv_*.parquet files")
    root = Path(destination_root).resolve() / source.name / source.revision
    root.mkdir(parents=True, exist_ok=True)
    downloaded = Path(
        downloader(
            repo_id=source.repo_id,
            repo_type="dataset",
            revision=source.revision,
            filename=filename,
            local_dir=root,
        )
    ).resolve()
    if not downloaded.is_file():
        raise HuggingFaceDatasetError(f"download did not produce a file: {downloaded}")
    return downloaded


def inspect_local_file(path: str | Path) -> dict[str, Any]:
    """Read only enough data to validate a downloaded tabular file's schema."""
    path = Path(path).resolve()
    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8", errors="replace") as handle:
            reader = csv.reader(handle)
            columns = next(reader, [])
            sample = next(reader, [])
        return {"format": "csv", "columns": columns, "sample": sample}
    if path.suffix.lower() == ".parquet":
        if importlib.util.find_spec("pyarrow") is None:
            return {"format": "parquet", "schema_status": "pyarrow_not_installed"}
        import pyarrow.parquet as pq

        parquet = pq.ParquetFile(path)
        return {
            "format": "parquet",
            "columns": list(parquet.schema_arrow.names),
            "rows": parquet.metadata.num_rows,
            "schema": str(parquet.schema_arrow),
        }
    raise HuggingFaceDatasetError(f"unsupported dataset file: {path.name}")


def ingest_approved_file(
    source: HuggingFaceDatasetSource,
    filename: str,
    *,
    repository_root: str | Path,
    register: bool = True,
) -> dict[str, Any]:
    """Download, inspect, and immutably register one approved public data file."""
    root = Path(repository_root).resolve()
    remote = verify_revision(source)
    target = download_approved_file(
        source,
        filename,
        destination_root=root / "cipher-system" / "data" / "external" / "huggingface",
    )
    result: dict[str, Any] = {
        "source": source.name,
        "repo_id": source.repo_id,
        "revision": source.revision,
        "remote_file_size": remote["files"].get(filename),
        "path": str(target),
        "inspection": inspect_local_file(target),
        "cloud_write_attempted": False,
    }
    if register:
        platform = ResearchPlatform(ResearchPlatformConfig.default(root))
        lake = platform.raw_lake.freeze_file(
            target,
            source="huggingface",
            dataset=source.dataset,
            request_metadata={
                "repo_id": source.repo_id,
                "revision": source.revision,
                "filename": filename,
                "license_from_dataset_card": "verify_before_strategy_use",
            },
        )
        result["raw_object_id"] = lake.manifest.raw_object_id
        result["sha256"] = lake.manifest.checksum
        result["frozen_local_path"] = lake.local_frozen_path
    return result
