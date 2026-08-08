#!/usr/bin/env python3
"""Register the verified Holdout C panel as one canonical frozen dataset.

This is a formalization task. The protected 11/12 result is loaded before any
registry mutation. All 744 partition hashes are verified, and registration is
performed in one transaction whose pre-commit validator re-runs the original
scope and 52-session cohort implementations through the newly visible registry
rows. Any mismatch raises and rolls back the complete bundle.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from datetime import date, datetime, time as clock_time, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import unquote, urlparse
from zoneinfo import ZoneInfo

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = ROOT.parent
SCRIPTS = ROOT / "scripts"
GOVERNANCE = ROOT / "data" / "governance"
REGISTRY_PATH = GOVERNANCE / "research_registry.sqlite"
DEFAULT_SCOPE = ROOT / "data" / "market_quality" / "alpaca_holdout_c_price_only_scope_20260803T235944Z.json"
DEFAULT_COHORT = GOVERNANCE / "holdout_c_alpaca_cohort_construction_20260803T235952Z.json"
NORMALIZER_PATH = ROOT / "scripts" / "ingest_alpaca_holdout_c_panel.py"
RAW_ROOT = ROOT / "data" / "raw" / "alpaca_sip_holdout_c_1m"
DATASET_NAME = "holdout_c_price_only_original_nine_2023_2025"
EXPECTED_PARTITIONS = 744
EXPECTED_BLOCK_START = "2023-06-06"
EXPECTED_BLOCK_END = "2025-12-31"
EXPECTED_BLOCK_SESSIONS = 638
EXPECTED_MINIMUM_COMMON_TICKERS = 8
EXPECTED_ORIGINS = 11
REQUIRED_ORIGINS = 12

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from core.research_platform.hashing import sha256_file, stable_id
from core.research_platform.models import DataDisposition, DatasetManifest, RawObjectManifest
from core.research_platform.registry import ResearchRegistry
from construct_alpaca_holdout_c_cohort import build_cohort_payload
from scope_alpaca_holdout_c_price_only import build_scope


class BaselineDiscrepancyError(RuntimeError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"expected JSON object: {path}")
    return value


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp is not timezone-aware: {value}")
    return parsed.astimezone(timezone.utc)


def git_output(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=REPOSITORY,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or f"git {' '.join(args)} failed")
    return completed.stdout.strip()


def registration_code_identity() -> dict[str, Any]:
    relative_paths = (
        "cipher-system/core/research_platform/registry.py",
        "cipher-system/scripts/scope_alpaca_holdout_c_price_only.py",
        "cipher-system/scripts/construct_alpaca_holdout_c_cohort.py",
        "cipher-system/scripts/audit_post_merge_verification.py",
        "cipher-system/scripts/register_holdout_c_canonical_dataset.py",
    )
    hashes = {
        relative: sha256_file(REPOSITORY / relative)
        for relative in relative_paths
    }
    completed = subprocess.run(
        ["git", "diff", "--quiet", "HEAD", "--", *relative_paths],
        cwd=REPOSITORY,
        check=False,
        timeout=60,
    )
    return {
        "registration_base_commit": git_output("rev-parse", "HEAD"),
        "registration_worktree_clean_for_code": completed.returncode == 0,
        "registration_code_files_sha256": hashes,
        "identity_note": (
            "The registration implementation is pinned by exact file hashes. "
            "The base commit identifies the checkout before this uncommitted task work."
        ),
    }


def normalizer_identity(registration_identity: Mapping[str, Any]) -> dict[str, Any]:
    relative = NORMALIZER_PATH.relative_to(REPOSITORY).as_posix()
    current_bytes = NORMALIZER_PATH.read_bytes()
    implementation_sha256 = hashlib.sha256(current_bytes).hexdigest()
    commits = [line for line in git_output("log", "--format=%H", "--", relative).splitlines() if line]
    exact_commits: list[str] = []
    for commit in commits:
        completed = subprocess.run(
            ["git", "show", f"{commit}:{relative}"],
            cwd=REPOSITORY,
            capture_output=True,
            check=False,
            timeout=60,
        )
        if completed.returncode == 0 and hashlib.sha256(completed.stdout).hexdigest() == implementation_sha256:
            exact_commits.append(commit)
    if not exact_commits:
        raise RuntimeError("no committed revision contains the exact normalizer implementation bytes")
    producer_source_commit = exact_commits[-1]
    return {
        "normalizer_path": str(NORMALIZER_PATH),
        "normalizer_relative_path": relative,
        "normalizer_implementation_sha256": implementation_sha256,
        "producer_source_commit": producer_source_commit,
        "producer_identity_basis": (
            "reconstructed from the earliest commit containing the exact normalizer script bytes; "
            "the original ingest artifacts did not record runtime HEAD"
        ),
        **dict(registration_identity),
        "normalizer_version": (
            f"ingest_alpaca_holdout_c_panel.py@{producer_source_commit}:sha256:{implementation_sha256}"
        ),
    }


def load_answer_key(scope_path: Path, cohort_path: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    scope = read_json(scope_path)
    cohort = read_json(cohort_path)
    partition_hashes = scope.get("normalized_partition_sha256")
    selected = cohort.get("selected_block") or {}
    requirements = cohort.get("requirements") or {}
    if not isinstance(partition_hashes, dict):
        raise BaselineDiscrepancyError("baseline scope does not contain a partition hash mapping")
    answer_key = {
        "scope_artifact": str(scope_path),
        "scope_artifact_sha256": sha256_file(scope_path),
        "cohort_artifact": str(cohort_path),
        "cohort_artifact_sha256": sha256_file(cohort_path),
        "provider": scope.get("provider"),
        "feed": scope.get("feed"),
        "panel": scope.get("panel"),
        "partition_count": int(scope.get("normalized_partitions") or 0),
        "partition_identities_sha256": dict(sorted((str(k), str(v)) for k, v in partition_hashes.items())),
        "selected_block": {
            "start": selected.get("start"),
            "end": selected.get("end"),
            "sessions": int(selected.get("sessions") or 0),
            "minimum_common_tickers": int(selected.get("minimum_common_tickers") or 0),
            "strict_independent_origins": int(selected.get("strict_independent_origins") or 0),
            "origin_windows": selected.get("origin_windows") or [],
        },
        "required_strict_independent_origins": int(
            requirements.get("minimum_strict_independent_origins") or 0
        ),
        "ranking_outcomes_evaluated": bool(cohort.get("ranking_outcomes_evaluated")),
        "volume_features_or_evaluation": bool(cohort.get("volume_features_or_evaluation")),
        "gate_relaxed": bool(cohort.get("full_volume_reconciled_gate_changed")),
        "scope_created_at": scope.get("created_at"),
        "cohort_created_at": cohort.get("created_at"),
        "holdout_period": cohort.get("holdout_period"),
    }
    expected = {
        "partition_count": EXPECTED_PARTITIONS,
        "selected_block": {
            "start": EXPECTED_BLOCK_START,
            "end": EXPECTED_BLOCK_END,
            "sessions": EXPECTED_BLOCK_SESSIONS,
            "minimum_common_tickers": EXPECTED_MINIMUM_COMMON_TICKERS,
            "strict_independent_origins": EXPECTED_ORIGINS,
        },
        "required_strict_independent_origins": REQUIRED_ORIGINS,
    }
    observed_core = {
        "partition_count": answer_key["partition_count"],
        "selected_block": {
            key: answer_key["selected_block"][key]
            for key in (
                "start",
                "end",
                "sessions",
                "minimum_common_tickers",
                "strict_independent_origins",
            )
        },
        "required_strict_independent_origins": answer_key["required_strict_independent_origins"],
    }
    if observed_core != expected:
        raise BaselineDiscrepancyError(
            "protected baseline is not the verified 744 / 638 sessions / 8 common tickers / 11 of 12 result: "
            + json.dumps({"expected": expected, "observed": observed_core}, sort_keys=True)
        )
    if len(answer_key["partition_identities_sha256"]) != EXPECTED_PARTITIONS:
        raise BaselineDiscrepancyError("baseline partition mapping does not contain exactly 744 identities")
    if answer_key["ranking_outcomes_evaluated"] or answer_key["volume_features_or_evaluation"] or answer_key["gate_relaxed"]:
        raise BaselineDiscrepancyError("baseline violates unchanged outcome, volume, or gate boundaries")
    return scope, cohort, answer_key


def event_bounds(session_day: date) -> tuple[datetime, datetime]:
    zone = ZoneInfo("America/New_York")
    start = datetime.combine(session_day, clock_time(9, 30), zone).astimezone(timezone.utc)
    end = datetime.combine(session_day, clock_time(16, 0), zone).astimezone(timezone.utc)
    return start, end


def build_manifests(
    answer_key: Mapping[str, Any],
    identity: Mapping[str, Any],
) -> tuple[tuple[RawObjectManifest, ...], DatasetManifest, list[dict[str, Any]]]:
    manifests: list[RawObjectManifest] = []
    evidence: list[dict[str, Any]] = []
    total_rows = 0
    normalized_root = ROOT / "data" / "normalized" / "alpaca_sip_holdout_c_1m"
    expected_root = normalized_root.absolute()

    for partition_text, expected_hash in answer_key["partition_identities_sha256"].items():
        partition = Path(partition_text)
        if not partition.is_absolute():
            raise BaselineDiscrepancyError(f"partition identity is not absolute: {partition}")
        try:
            partition.relative_to(expected_root)
        except ValueError as exc:
            raise BaselineDiscrepancyError(f"partition is outside the protected panel: {partition}") from exc
        if not partition.is_file():
            raise FileNotFoundError(partition)
        actual_hash = sha256_file(partition)
        if actual_hash != expected_hash:
            raise BaselineDiscrepancyError(
                f"partition hash mismatch before registration: {partition}: expected {expected_hash}, got {actual_hash}"
            )

        session_day = date.fromisoformat(partition.stem)
        raw_path = RAW_ROOT / f"year={session_day.year}" / f"month={session_day.month:02d}" / f"{session_day.isoformat()}.json"
        if not raw_path.is_file():
            raise FileNotFoundError(f"source raw response missing for {partition}: {raw_path}")
        raw_payload = read_json(raw_path)
        if raw_payload.get("provider") != "Alpaca SIP" or raw_payload.get("session_date") != session_day.isoformat():
            raise BaselineDiscrepancyError(f"raw provider/session mapping mismatch: {raw_path}")
        provider_received_at = parse_time(str(raw_payload["received_at"]))
        stat = partition.stat()
        normalized_observed_at = datetime.fromtimestamp(stat.st_mtime, timezone.utc)
        available_at = max(provider_received_at, normalized_observed_at)
        raw_hash = sha256_file(raw_path)
        start, end = event_bounds(session_day)
        rows = int(pq.ParquetFile(partition).metadata.num_rows)
        total_rows += rows
        ingestion_run_id = stable_id(
            "holdout_c_partition",
            {
                "session_date": session_day.isoformat(),
                "parquet_sha256": actual_hash,
                "source_raw_sha256": raw_hash,
                "normalizer_sha256": identity["normalizer_implementation_sha256"],
            },
        )
        manifest = RawObjectManifest(
            source="Alpaca SIP",
            dataset=DATASET_NAME,
            uri=partition.as_uri(),
            checksum=actual_hash,
            checksum_method="sha256",
            size_bytes=stat.st_size,
            received_at=provider_received_at,
            available_at=available_at,
            ingestion_run_id=ingestion_run_id,
            content_type="application/vnd.apache.parquet",
            event_time_start=start,
            event_time_end=end,
            request_metadata={
                "baseline_partition_path": str(partition),
                "baseline_partition_sha256": expected_hash,
                "normalized_partition": True,
                "normalized_partition_mtime_ns": stat.st_mtime_ns,
                "normalized_partition_observed_at": normalized_observed_at.isoformat(),
                "source_raw_path": str(raw_path),
                "source_raw_sha256": raw_hash,
                "provider_received_at": provider_received_at.isoformat(),
                "session_date": session_day.isoformat(),
                "row_count": rows,
                "normalizer_version": identity["normalizer_version"],
                "normalizer_implementation_sha256": identity["normalizer_implementation_sha256"],
                "producer_source_commit": identity["producer_source_commit"],
                "producer_identity_basis": identity["producer_identity_basis"],
            },
            disposition=DataDisposition.FROZEN_SNAPSHOT,
        )
        manifests.append(manifest)
        evidence.append(
            {
                "session_date": session_day.isoformat(),
                "partition_path": str(partition),
                "expected_sha256": expected_hash,
                "actual_sha256": actual_hash,
                "hash_match": True,
                "size_bytes": stat.st_size,
                "row_count": rows,
                "raw_object_id": manifest.raw_object_id,
                "source_raw_path": str(raw_path),
                "source_raw_sha256": raw_hash,
                "provider_received_at": provider_received_at.isoformat(),
                "normalized_available_at": available_at.isoformat(),
            }
        )

    if len(manifests) != EXPECTED_PARTITIONS:
        raise BaselineDiscrepancyError(
            f"manifest preparation produced {len(manifests)} objects instead of {EXPECTED_PARTITIONS}"
        )
    created_at = parse_time(str(answer_key["cohort_created_at"]))
    availability_cutoff = max(item.available_at for item in manifests)
    if availability_cutoff > created_at:
        raise BaselineDiscrepancyError(
            "latest partition availability exceeds the protected cohort artifact creation time"
        )
    dataset = DatasetManifest(
        name=DATASET_NAME,
        created_at=created_at,
        availability_cutoff=availability_cutoff,
        sources=("Alpaca SIP",),
        raw_object_ids=tuple(item.raw_object_id for item in manifests),
        symbol_universe_id="holdout_c_original_nine_symbols_2023_2025",
        corporate_action_version="unadjusted_provider_prices_with_split_like_close_ratio_gate_v1",
        normalizer_version=str(identity["normalizer_version"]),
        schema_name="alpaca_sip_holdout_c_1m_parquet_v1",
        row_counts={
            "partitions": len(manifests),
            "sessions": len(manifests),
            "panel_symbols": len(answer_key["panel"]),
            "rows": total_rows,
            "selected_block_sessions": int(answer_key["selected_block"]["sessions"]),
        },
        quality_checks={
            "passed": True,
            "frozen_answer_key": True,
            "baseline_partition_count": EXPECTED_PARTITIONS,
            "partition_hashes_verified": True,
            "expected_strict_independent_origins": EXPECTED_ORIGINS,
            "required_strict_independent_origins": REQUIRED_ORIGINS,
            "registration_is_formalization_not_gap_closure": True,
            "ranking_outcomes_evaluated": False,
            "volume_evaluated": False,
            "gate_relaxed": False,
            "baseline_scope_sha256": answer_key["scope_artifact_sha256"],
            "baseline_cohort_sha256": answer_key["cohort_artifact_sha256"],
            "normalizer_implementation_sha256": identity["normalizer_implementation_sha256"],
            "producer_source_commit": identity["producer_source_commit"],
            "registration_base_commit": identity["registration_base_commit"],
            "registration_code_files_sha256": identity["registration_code_files_sha256"],
        },
        frozen=True,
    )
    return tuple(manifests), dataset, evidence


def paths_from_transaction(db: sqlite3.Connection, dataset_id: str) -> list[Path]:
    rows = db.execute(
        """
        select r.payload_json
        from dataset_raw_objects d
        join raw_objects r on r.raw_object_id = d.raw_object_id
        where d.dataset_id = ?
        order by r.uri
        """,
        (dataset_id,),
    ).fetchall()
    paths: list[Path] = []
    for row in rows:
        payload = json.loads(row[0])
        uri = str(payload["uri"])
        parsed = urlparse(uri)
        if parsed.scheme != "file":
            raise BaselineDiscrepancyError(f"canonical Holdout C object is not a local file URI: {uri}")
        paths.append(Path(unquote(parsed.path)))
    return paths


def compare_with_answer_key(
    canonical_scope: Mapping[str, Any],
    canonical_cohort: Mapping[str, Any],
    baseline_scope: Mapping[str, Any],
    baseline_cohort: Mapping[str, Any],
    answer_key: Mapping[str, Any],
) -> dict[str, Any]:
    canonical_selected = canonical_cohort.get("selected_block") or {}
    baseline_selected = baseline_cohort.get("selected_block") or {}
    fields = {
        "partition_count": {
            "expected": answer_key["partition_count"],
            "actual": int(canonical_scope.get("normalized_partitions") or 0),
        },
        "partition_identities_sha256": {
            "expected": answer_key["partition_identities_sha256"],
            "actual": canonical_scope.get("normalized_partition_sha256") or {},
        },
        "provider": {"expected": baseline_scope.get("provider"), "actual": canonical_scope.get("provider")},
        "feed": {"expected": baseline_scope.get("feed"), "actual": canonical_scope.get("feed")},
        "panel": {"expected": baseline_scope.get("panel"), "actual": canonical_scope.get("panel")},
        "selected_block_start": {"expected": baseline_selected.get("start"), "actual": canonical_selected.get("start")},
        "selected_block_end": {"expected": baseline_selected.get("end"), "actual": canonical_selected.get("end")},
        "selected_block_sessions": {
            "expected": int(baseline_selected.get("sessions") or 0),
            "actual": int(canonical_selected.get("sessions") or 0),
        },
        "minimum_common_tickers": {
            "expected": int(baseline_selected.get("minimum_common_tickers") or 0),
            "actual": int(canonical_selected.get("minimum_common_tickers") or 0),
        },
        "strict_independent_origins": {
            "expected": int(baseline_selected.get("strict_independent_origins") or 0),
            "actual": int(canonical_selected.get("strict_independent_origins") or 0),
        },
        "required_strict_independent_origins": {
            "expected": int(
                (baseline_cohort.get("requirements") or {}).get("minimum_strict_independent_origins") or 0
            ),
            "actual": int(
                (canonical_cohort.get("requirements") or {}).get("minimum_strict_independent_origins") or 0
            ),
        },
        "origin_windows": {
            "expected": baseline_selected.get("origin_windows") or [],
            "actual": canonical_selected.get("origin_windows") or [],
        },
        "daily_results": {
            "expected_sha256": hashlib.sha256(
                json.dumps(baseline_scope.get("daily_results") or [], sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            "actual_sha256": hashlib.sha256(
                json.dumps(canonical_scope.get("daily_results") or [], sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
        },
        "common_eligible_by_day": {
            "expected_sha256": hashlib.sha256(
                json.dumps(
                    baseline_scope.get("common_eligible_by_day") or [],
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest(),
            "actual_sha256": hashlib.sha256(
                json.dumps(
                    canonical_scope.get("common_eligible_by_day") or [],
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest(),
        },
    }
    checks: dict[str, bool] = {}
    for name, values in fields.items():
        if "expected" in values:
            checks[name] = values["expected"] == values["actual"]
        else:
            checks[name] = values["expected_sha256"] == values["actual_sha256"]
    checks.update(
        {
            "ranking_outcomes_not_evaluated": not bool(canonical_cohort.get("ranking_outcomes_evaluated")),
            "volume_not_evaluated": not bool(canonical_cohort.get("volume_features_or_evaluation")),
            "gate_not_relaxed": not bool(canonical_cohort.get("full_volume_reconciled_gate_changed")),
        }
    )
    return {"fields": fields, "checks": checks, "exact_match": all(checks.values())}


def registry_counts(db_path: Path, dataset_id: str) -> dict[str, int]:
    with sqlite3.connect(f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True, timeout=60) as db:
        return {
            "dataset_manifests": int(
                db.execute("select count(*) from datasets where dataset_id = ?", (dataset_id,)).fetchone()[0]
            ),
            "raw_objects": int(
                db.execute("select count(*) from raw_objects where dataset = ?", (DATASET_NAME,)).fetchone()[0]
            ),
            "dataset_raw_links": int(
                db.execute(
                    "select count(*) from dataset_raw_objects where dataset_id = ?",
                    (dataset_id,),
                ).fetchone()[0]
            ),
        }


def existing_holdout_counts(db_path: Path) -> dict[str, int]:
    with sqlite3.connect(f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True, timeout=60) as db:
        return {
            "datasets": int(db.execute("select count(*) from datasets where name = ?", (DATASET_NAME,)).fetchone()[0]),
            "raw_objects": int(db.execute("select count(*) from raw_objects where dataset = ?", (DATASET_NAME,)).fetchone()[0]),
            "links": int(
                db.execute(
                    """
                    select count(*) from dataset_raw_objects d
                    join datasets s on s.dataset_id = d.dataset_id
                    where s.name = ?
                    """,
                    (DATASET_NAME,),
                ).fetchone()[0]
            ),
        }


def load_post_merge_module():
    path = SCRIPTS / "audit_post_merge_verification.py"
    spec = importlib.util.spec_from_file_location("cipher_holdout_c_lineage_refresh", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load post-merge verification module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def run(scope_path: Path, cohort_path: Path) -> dict[str, Any]:
    registration_identity = registration_code_identity()
    identity = normalizer_identity(registration_identity)
    baseline_scope, baseline_cohort, answer_key = load_answer_key(scope_path, cohort_path)
    manifests, dataset, partition_evidence = build_manifests(answer_key, identity)
    before = existing_holdout_counts(REGISTRY_PATH)
    if before not in (
        {"datasets": 0, "raw_objects": 0, "links": 0},
        {"datasets": 1, "raw_objects": EXPECTED_PARTITIONS, "links": EXPECTED_PARTITIONS},
    ):
        raise RuntimeError(
            "partial or conflicting Holdout C canonical registration already exists; refusing to continue: "
            + json.dumps(before, sort_keys=True)
        )

    verification_holder: dict[str, Any] = {}

    def validator(db: sqlite3.Connection, registered_dataset: DatasetManifest) -> None:
        paths = paths_from_transaction(db, registered_dataset.dataset_id)
        if len(paths) != EXPECTED_PARTITIONS:
            raise BaselineDiscrepancyError(
                f"canonical transaction exposes {len(paths)} linked partitions instead of {EXPECTED_PARTITIONS}"
            )
        # The baseline scope was built over a bounded period ("2023-01-01..2025-12-31"),
        # and build_scope() date-filters `daily_results` while hashing every selected
        # path. Omitting start/end here re-derived over the full 2017-2025 panel, so
        # daily_results came back at roughly double the baseline's 6696 rows, the
        # sha comparison could never match, and the transaction rolled back reporting
        # "did not match the protected answer key" — which reads like corrupted data
        # rather than a missing argument. This is very likely why the original
        # registration left no artifact behind.
        baseline_period = str(baseline_scope.get("period") or "")
        period_start, _, period_end = baseline_period.partition("..")
        canonical_scope = build_scope(
            paths,
            start=period_start or None,
            end=period_end or None,
            created_at=parse_time(str(answer_key["scope_created_at"])),
        )
        canonical_cohort = build_cohort_payload(
            canonical_scope,
            scope_artifact=f"canonical-dataset:{registered_dataset.dataset_id}",
            period=str(answer_key["holdout_period"]),
            created_at=parse_time(str(answer_key["cohort_created_at"])),
        )
        comparison = compare_with_answer_key(
            canonical_scope,
            canonical_cohort,
            baseline_scope,
            baseline_cohort,
            answer_key,
        )
        verification_holder.update(
            {
                "canonical_scope": canonical_scope,
                "canonical_cohort": canonical_cohort,
                "comparison": comparison,
            }
        )
        if not comparison["exact_match"]:
            failed = [name for name, passed in comparison["checks"].items() if not passed]
            raise BaselineDiscrepancyError(
                "canonical re-derivation did not match the protected 11/12 answer key; transaction rolled back: "
                + json.dumps({"failed_checks": failed, "comparison": comparison["fields"]}, sort_keys=True)
            )

    registry = ResearchRegistry(REGISTRY_PATH)
    registration = registry.register_dataset_bundle(
        manifests,
        dataset,
        actor="holdout_c_canonical_registration",
        precommit_validator=validator,
    )
    if before == {"datasets": 0, "raw_objects": 0, "links": 0}:
        required_registration = {
            "raw_objects_inserted": EXPECTED_PARTITIONS,
            "dataset_inserted": True,
            "links_inserted": EXPECTED_PARTITIONS,
        }
        observed_registration = {
            key: registration[key] for key in required_registration
        }
        if observed_registration != required_registration:
            raise RuntimeError(
                "first registration did not insert exactly 744 raw objects, one dataset, and 744 links: "
                + json.dumps(
                    {"expected": required_registration, "observed": observed_registration},
                    sort_keys=True,
                )
            )

    after = registry_counts(REGISTRY_PATH, dataset.dataset_id)
    required_after = {
        "dataset_manifests": 1,
        "raw_objects": EXPECTED_PARTITIONS,
        "dataset_raw_links": EXPECTED_PARTITIONS,
    }
    if after != required_after:
        raise RuntimeError(
            "post-commit registry counts do not match the canonical requirement: "
            + json.dumps({"expected": required_after, "observed": after}, sort_keys=True)
        )

    post_merge = load_post_merge_module().refresh_lineage_verification()
    result = {
        "schema_version": 1,
        "created_at": utc_now().isoformat(),
        "status": "completed_exact_match",
        "task": "Holdout C canonical dataset registration",
        "baseline_answer_key": answer_key,
        "normalizer_and_code_identity": identity,
        "dataset_manifest": dataset.to_dict(),
        "partition_evidence": partition_evidence,
        "registration_counts_before": before,
        "registration_operation": registration,
        "registration_counts_after": after,
        "canonical_rederivation": {
            "comparison": verification_holder["comparison"],
            "selected_block": verification_holder["canonical_cohort"]["selected_block"],
            "requirements": verification_holder["canonical_cohort"]["requirements"],
            "scope_partition_count": verification_holder["canonical_scope"]["normalized_partitions"],
        },
        "exact_match_to_protected_baseline": True,
        "known_canonical_lineage_gap": bool(post_merge.get("known_canonical_lineage_gap")),
        "origin_gap": {
            "strict_independent_origins": EXPECTED_ORIGINS,
            "required_strict_independent_origins": REQUIRED_ORIGINS,
            "gap": REQUIRED_ORIGINS - EXPECTED_ORIGINS,
            "status": "open_unresolved",
            "registration_closed_origin_gap": False,
        },
        "ranking_or_model_outcomes_evaluated": False,
        "volume_evaluated": False,
        "gate_relaxed": False,
        "live_execution": False,
    }
    if result["known_canonical_lineage_gap"]:
        raise RuntimeError("post-merge verification still reports the canonical lineage gap after exact registration")
    stamp = utc_now().strftime("%Y%m%dT%H%M%S%fZ")
    timestamped = GOVERNANCE / f"holdout_c_canonical_dataset_registration_{stamp}.json"
    stable = GOVERNANCE / "holdout_c_canonical_dataset_registration.json"
    atomic_write(timestamped, result)
    atomic_write(stable, result)
    return {**result, "stable_artifact": str(stable), "timestamped_artifact": str(timestamped)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scope", type=Path, default=DEFAULT_SCOPE)
    parser.add_argument("--cohort", type=Path, default=DEFAULT_COHORT)
    args = parser.parse_args()
    try:
        result = run(args.scope, args.cohort)
    except Exception as exc:
        failure = {
            "schema_version": 1,
            "created_at": utc_now().isoformat(),
            "status": "blocked_discrepancy_or_registration_failure",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "registry_registration_considered_complete": False,
            "protected_answer_changed": False,
            "live_execution": False,
        }
        atomic_write(GOVERNANCE / "holdout_c_canonical_dataset_registration_failure.json", failure)
        print(json.dumps(failure, indent=2, sort_keys=True), file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "status": result["status"],
                "dataset_id": result["dataset_manifest"]["dataset_id"],
                "registration_counts_after": result["registration_counts_after"],
                "strict_independent_origins": result["origin_gap"]["strict_independent_origins"],
                "required_strict_independent_origins": result["origin_gap"]["required_strict_independent_origins"],
                "known_canonical_lineage_gap": result["known_canonical_lineage_gap"],
                "artifact": result["stable_artifact"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
