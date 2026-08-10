"""Bounded, read-only catalog for historical option datasets and stored reports.

The archive has multiple manifest generations and repeated provider dataset IDs.
Physical relative paths are therefore the stable identity; provider IDs remain
metadata and are never treated as unique. Large raw-page provenance arrays are
deliberately excluded from the browser envelope.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = ROOT / "data"
HISTORICAL_ROOT = DATA_ROOT / "historical_options"
CACHE_SECONDS = 30
MAX_REPORT_BYTES = 2 * 1024 * 1024

_CACHE_LOCK = threading.Lock()
_CACHE: tuple[float, dict[str, Any]] | None = None


def _stable_id(kind: str, relative: str) -> str:
    digest = hashlib.sha256(relative.encode("utf-8")).hexdigest()[:20]
    return f"{kind}_{digest}"


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"manifest must be an object: {path}")
    return payload


def _relative(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _standard_dataset(manifest_path: Path, historical_root: Path) -> dict[str, Any]:
    payload = _read_json(manifest_path)
    directory = manifest_path.parent
    relative = _relative(directory, historical_root)
    database = directory / "historical_options.sqlite"
    provenance_db = (payload.get("provenance") or {}).get("database") or {}
    return {
        "id": _stable_id("options_dataset", relative),
        "relative_path": relative,
        "manifest_type": "download_manifest",
        "provider_dataset_id": payload.get("dataset_id"),
        "status": payload.get("status"),
        "database_present": database.is_file(),
        "database_size_bytes": database.stat().st_size if database.is_file() else None,
        "database_sha256": provenance_db.get("sha256"),
        "coverage": dict(payload.get("cumulative_coverage") or {}),
        "capabilities": dict(payload.get("capabilities") or {}),
        "caveats": list(payload.get("caveats") or []),
    }


def _eod_dataset(manifest_path: Path, historical_root: Path) -> dict[str, Any]:
    payload = _read_json(manifest_path)
    directory = manifest_path.parent
    relative = _relative(directory, historical_root)
    database = directory / "historical_options.sqlite"
    reason = str(payload.get("research_grade_reason") or "Historical NBBO is unavailable.")
    return {
        "id": _stable_id("options_dataset", relative),
        "relative_path": relative,
        "manifest_type": "eod_archive_manifest",
        "provider_dataset_id": None,
        "status": "BAR_EXECUTION_PROXY_ONLY",
        "database_present": database.is_file(),
        "database_size_bytes": database.stat().st_size if database.is_file() else None,
        "database_sha256": None,
        "coverage": {
            "sessions": payload.get("sessions"),
            "option_bar_rows": payload.get("option_bar_rows"),
            "selected_contract_rows": payload.get("selection_rows"),
            "selected_contract_symbols": payload.get("selected_unique_symbols"),
            "observed_contract_symbols": payload.get("observed_unique_symbols"),
        },
        "capabilities": {
            "historical_option_bars": True,
            "historical_option_bid_ask": False,
            "historical_option_trades": False,
            "historical_iv_greeks": False,
            "historical_open_interest": False,
        },
        "caveats": [reason, "Execution results use conservative one-minute trade-bar proxies."],
        "research_grade": bool(payload.get("research_grade")),
    }


def _report_roots(data_root: Path) -> tuple[Path, ...]:
    return (
        data_root / "historical_options",
        data_root / "leveraged_etf_wheel",
        data_root / "earnings_defined_risk_lab",
        data_root / "eod_option_pattern_lab",
        data_root / "eod_option_walkforward",
    )


def _reports(data_root: Path, dataset_by_directory: dict[Path, str]) -> list[dict[str, Any]]:
    seen: set[Path] = set()
    reports: list[dict[str, Any]] = []
    patterns = ("report.json", "*_report.json", "historical_option_strategy_report.json")
    for root in _report_roots(data_root):
        if not root.is_dir():
            continue
        for pattern in patterns:
            for path in root.rglob(pattern):
                resolved = path.resolve()
                if resolved in seen or not path.is_file():
                    continue
                seen.add(resolved)
                relative = _relative(path, data_root)
                dataset_id = None
                for directory, candidate_id in sorted(
                    dataset_by_directory.items(), key=lambda item: len(item[0].parts), reverse=True
                ):
                    if resolved.is_relative_to(directory):
                        dataset_id = candidate_id
                        break
                reports.append(
                    {
                        "id": _stable_id("options_report", relative),
                        "relative_path": relative,
                        "size_bytes": path.stat().st_size,
                        "modified_at": datetime.fromtimestamp(
                            path.stat().st_mtime, timezone.utc
                        ).isoformat(),
                        "dataset_id": dataset_id,
                    }
                )
    return sorted(reports, key=lambda row: row["relative_path"])


def build_catalog(data_root: Path = DATA_ROOT) -> dict[str, Any]:
    historical_root = data_root / "historical_options"
    datasets: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    if historical_root.is_dir():
        manifests = sorted(historical_root.rglob("download_manifest.json"))
        manifests += sorted(historical_root.rglob("eod_archive_manifest.json"))
        for manifest in manifests:
            try:
                if manifest.name == "download_manifest.json":
                    datasets.append(_standard_dataset(manifest, historical_root))
                else:
                    datasets.append(_eod_dataset(manifest, historical_root))
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                errors.append(
                    {"relative_path": _relative(manifest, data_root), "error": type(exc).__name__}
                )
    datasets.sort(key=lambda row: row["relative_path"])
    dataset_by_directory = {
        (historical_root / row["relative_path"]).resolve(): row["id"] for row in datasets
    }
    reports = _reports(data_root, dataset_by_directory)
    return {
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "read_only": True,
        "datasets": datasets,
        "reports": reports,
        "errors": errors,
        "counts": {
            "datasets": len(datasets),
            "reports": len(reports),
            "manifest_errors": len(errors),
        },
        "caveat": (
            "Historical option execution is a bar/trade approximation unless a dataset "
            "explicitly declares point-in-time bid/ask capability. No dataset here does."
        ),
    }


def catalog(*, refresh: bool = False) -> dict[str, Any]:
    global _CACHE
    now = time.monotonic()
    with _CACHE_LOCK:
        if not refresh and _CACHE and now - _CACHE[0] < CACHE_SECONDS:
            return _CACHE[1]
        payload = build_catalog()
        _CACHE = (now, payload)
        return payload


def report_payload(report_id: str) -> dict[str, Any] | None:
    listing = catalog()
    report = next((row for row in listing["reports"] if row["id"] == report_id), None)
    if not report:
        return None
    path = (DATA_ROOT / report["relative_path"]).resolve()
    if not path.is_relative_to(DATA_ROOT.resolve()) or path.stat().st_size > MAX_REPORT_BYTES:
        raise ValueError("stored report is too large to serve")
    return {"report": report, "result": _read_json(path), "read_only": True}
