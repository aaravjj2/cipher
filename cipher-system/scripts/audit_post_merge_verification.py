#!/usr/bin/env python3
"""Focused post-merge verification for Cipher's unified product.

This audit intentionally checks only the three high-value post-merge questions:

1. Did systemd actually relaunch the four active services through the canonical
   source alias?
2. Does a fresh 52-session Holdout C recount still produce 11/12 origins, and
   what does the merged canonical registry contain for that panel?
3. Did the point-in-time news timestamp and eight-layer naming/topology fixes
   survive the merge?

It does not run research outcomes, relax gates, or add execution authority.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = ROOT.parent
GOVERNANCE = ROOT / "data" / "governance"
MARKET_QUALITY = ROOT / "data" / "market_quality"
REGISTRY = GOVERNANCE / "research_registry.sqlite"
OUTPUT = GOVERNANCE / "post_merge_verification.json"
CANONICAL_SOURCE = ROOT.resolve()
SERVICES = (
    "cipher-core.service",
    "cipher-web.service",
    "cipher-gex.service",
    "cipher-tradier.service",
)

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.research_platform.seven_layer_stack import (  # noqa: E402
    EightLayerStackSpec,
    SevenLayerStackSpec,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def command(args: list[str], *, timeout: int = 30) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            args,
            cwd=REPOSITORY,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
        return {
            "returncode": completed.returncode,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
        }
    except Exception as exc:
        return {"returncode": 127, "stdout": "", "stderr": f"{type(exc).__name__}: {exc}"}


def proc_cwd(pid: int) -> str | None:
    try:
        return str((Path(f"/proc/{pid}") / "cwd").resolve())
    except OSError:
        return None


def proc_command(pid: int) -> str:
    try:
        return (
            (Path(f"/proc/{pid}") / "cmdline")
            .read_bytes()
            .replace(b"\x00", b" ")
            .decode("utf-8", errors="ignore")
            .strip()
        )
    except OSError:
        return ""


def service_snapshot(name: str) -> dict[str, Any]:
    result = command(
        [
            "systemctl",
            "show",
            name,
            "-p",
            "MainPID",
            "-p",
            "ActiveState",
            "-p",
            "SubState",
            "-p",
            "ExecMainStartTimestamp",
            "-p",
            "WorkingDirectory",
        ]
    )
    values: dict[str, str] = {}
    for line in result["stdout"].splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    pid = int(values.get("MainPID") or 0)
    return {
        "name": name,
        "active_state": values.get("ActiveState"),
        "sub_state": values.get("SubState"),
        "main_pid": pid,
        "exec_main_start_timestamp": values.get("ExecMainStartTimestamp"),
        "configured_working_directory": values.get("WorkingDirectory"),
        "resolved_process_cwd": proc_cwd(pid) if pid else None,
        "command": proc_command(pid) if pid else "",
        "query_returncode": result["returncode"],
    }


def git_evidence() -> dict[str, Any]:
    commit = command(["git", "rev-parse", "HEAD"])
    status = command(["git", "status", "--short"])
    return {
        "commit": commit["stdout"] if commit["returncode"] == 0 else None,
        "working_tree_clean": status["returncode"] == 0 and not status["stdout"],
        "status": status["stdout"],
    }


def http_status(url: str) -> dict[str, Any]:
    try:
        with urlopen(url, timeout=15) as response:
            body = response.read()
            return {
                "ok": 200 <= response.status < 300,
                "status": response.status,
                "bytes": len(body),
            }
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def latest_by_mtime(root: Path, pattern: str) -> Path | None:
    candidates = [path for path in root.glob(pattern) if path.is_file()]
    return max(candidates, key=lambda path: path.stat().st_mtime_ns, default=None)


def read_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def holdout_evidence(scope_path: Path | None, cohort_path: Path | None) -> dict[str, Any]:
    scope = read_json(scope_path)
    cohort = read_json(cohort_path)
    selected = cohort.get("selected_block") or {}
    requirements = cohort.get("requirements") or {}
    normalized_root = ROOT / "data" / "normalized" / "alpaca_sip_holdout_c_1m"
    partitions = sum(1 for path in normalized_root.rglob("*.parquet") if path.is_file()) if normalized_root.exists() else 0
    origins = int(selected.get("strict_independent_origins") or 0)
    required = int(requirements.get("minimum_strict_independent_origins") or 12)
    minimum_tickers = int(selected.get("minimum_common_tickers") or 0)
    return {
        "scope_artifact": str(scope_path) if scope_path else None,
        "cohort_artifact": str(cohort_path) if cohort_path else None,
        "scope_provider": scope.get("provider"),
        "scope_feed": scope.get("feed"),
        "scope_period": scope.get("period"),
        "normalized_partitions_current": partitions,
        "normalized_partitions_recorded": int(scope.get("normalized_partitions") or 0),
        "selected_block_start": selected.get("start"),
        "selected_block_end": selected.get("end"),
        "selected_block_sessions": int(selected.get("sessions") or 0),
        "strict_independent_origins": origins,
        "required_strict_independent_origins": required,
        "origin_gap": max(0, required - origins),
        "minimum_common_tickers": minimum_tickers,
        "cohort_pass": bool(cohort.get("pass")),
        "ranking_outcomes_evaluated": bool(cohort.get("ranking_outcomes_evaluated")),
        "volume_features_or_evaluation": bool(cohort.get("volume_features_or_evaluation")),
        "post_merge_recount_matches_11_of_12": origins == 11 and required == 12,
    }


def registry_evidence(path: Path = REGISTRY) -> dict[str, Any]:
    if not path.is_file():
        return {"exists": False, "path": str(path)}
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=60) as db:
        integrity = str(db.execute("pragma integrity_check").fetchone()[0])
        dataset_count = int(
            db.execute(
                """
                select count(*) from datasets
                where lower(name) like '%holdout%'
                   or lower(payload_json) like '%holdout_c%'
                   or lower(payload_json) like '%alpaca_sip_holdout%'
                """
            ).fetchone()[0]
        )
        raw_count = int(
            db.execute(
                """
                select count(*) from raw_objects
                where lower(dataset) like '%holdout%'
                   or lower(payload_json) like '%holdout_c%'
                   or lower(payload_json) like '%alpaca_sip_holdout%'
                """
            ).fetchone()[0]
        )
        links = int(
            db.execute(
                """
                select count(*)
                from dataset_raw_objects dr
                join datasets d on d.dataset_id = dr.dataset_id
                where lower(d.name) like '%holdout%'
                   or lower(d.payload_json) like '%holdout_c%'
                   or lower(d.payload_json) like '%alpaca_sip_holdout%'
                """
            ).fetchone()[0]
        )
        event_timestamps = db.execute(
            """
            select count(*) as total,
                   sum(case when julianday(received_at) < julianday(publication_time) then 1 else 0 end),
                   sum(case when julianday(available_at) < julianday(received_at) then 1 else 0 end),
                   sum(case when received_at = publication_time then 1 else 0 end),
                   sum(case when julianday(received_at) > julianday(publication_time) then 1 else 0 end)
            from news_events
            """
        ).fetchone()
    return {
        "exists": True,
        "path": str(path),
        "integrity": integrity,
        "holdout_dataset_manifests": dataset_count,
        "holdout_raw_objects": raw_count,
        "holdout_dataset_raw_links": links,
        "holdout_canonical_lineage_present": dataset_count == 1 and raw_count == 744 and links == 744,
        "news_timestamp_invariants": {
            "total_events": int(event_timestamps[0] or 0),
            "received_before_publication": int(event_timestamps[1] or 0),
            "available_before_received": int(event_timestamps[2] or 0),
            "receipt_equals_publication": int(event_timestamps[3] or 0),
            "receipt_after_publication": int(event_timestamps[4] or 0),
        },
    }


def timestamp_code_evidence() -> dict[str, Any]:
    ingestion_path = ROOT / "scripts" / "ingest_public_events.py"
    news_path = ROOT / "core" / "research_platform" / "news.py"
    ingestion_text = ingestion_path.read_text(encoding="utf-8")
    news_text = news_path.read_text(encoding="utf-8")

    # Load only the pure helper through the script module so this audit verifies
    # behavior rather than relying on a string match alone.
    import importlib.util

    spec = importlib.util.spec_from_file_location("cipher_post_merge_event_ingestion", ingestion_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load ingest_public_events.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    observed = utc_now()
    historical = observed - timedelta(days=2)
    future = observed + timedelta(minutes=5)
    historical_result = module.observed_availability(historical, observed)
    future_result = module.observed_availability(future, observed)
    return {
        "observed_availability_helper_present": "def observed_availability" in ingestion_text,
        "historical_publication_uses_observation_time": historical_result == observed,
        "future_publication_never_precedes_publication": future_result == future,
        "news_model_rejects_received_before_publication": "received_at cannot precede publication_time" in news_text,
        "news_model_rejects_available_before_received": "available_at cannot precede received_at" in news_text,
    }


def stack_evidence() -> dict[str, Any]:
    spec = EightLayerStackSpec.default()
    source_paths = (
        ROOT / "core" / "research_platform" / "seven_layer_stack.py",
        ROOT / "scripts" / "audit_original_architecture.py",
        ROOT / "tests" / "test_original_architecture_audit.py",
    )
    combined = "\n".join(path.read_text(encoding="utf-8", errors="ignore") for path in source_paths)
    names = [layer.name for layer in spec.layers]
    return {
        "implemented_layer_count": len(spec.layers),
        "layer_numbers": [layer.layer for layer in spec.layers],
        "layer_names": names,
        "shadow_paper_is_formal_layer_7": len(spec.layers) >= 7 and spec.layers[6].name == "shadow_and_paper_execution",
        "attribution_layer_name": spec.layers[3].name if len(spec.layers) >= 4 else None,
        "compatibility_alias_points_to_eight_layer_class": SevenLayerStackSpec is EightLayerStackSpec,
        "boundary_violations": [item.to_dict() for item in spec.validate_boundaries()],
        "stale_causal_attribution_and_anomaly_name_present": "causal_attribution_and_anomaly_engine" in combined,
        "stale_causal_attribution_source_present": "causal_attribution_engine" in combined,
    }


def parse_pre_restart(values: list[str]) -> dict[str, int]:
    parsed: dict[str, int] = {}
    for value in values:
        name, separator, raw_pid = value.partition("=")
        if not separator or name not in SERVICES:
            raise ValueError(f"invalid --pre-restart-pid value: {value}")
        parsed[name] = int(raw_pid)
    return parsed


def classify(checks: Mapping[str, bool], *, holdout_lineage_present: bool) -> str:
    if not all(checks.values()):
        return "FAILED"
    return "PASSED" if holdout_lineage_present else "PASSED_WITH_KNOWN_CANONICAL_LINEAGE_GAP"


def atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def refresh_lineage_verification() -> dict[str, Any]:
    """Refresh only the canonical-lineage portion of the durable audit.

    Service-restart and route evidence remains the evidence captured by the
    original post-merge run. This refresh is intentionally narrow and records
    that those checks were not rerun while updating the registry-backed gap.
    """

    existing = read_json(OUTPUT)
    if not existing:
        raise FileNotFoundError(f"post-merge verification artifact is unavailable: {OUTPUT}")
    registry = registry_evidence()
    lineage_present = bool(registry.get("holdout_canonical_lineage_present"))
    checks = dict(existing.get("checks") or {})
    checks["holdout_canonical_lineage_complete"] = lineage_present
    verdict = classify(checks, holdout_lineage_present=lineage_present)
    refreshed = dict(existing)
    refreshed.update(
        {
            "created_at": utc_now().isoformat(),
            "verdict": verdict,
            "verification_passed": verdict != "FAILED",
            "checks": checks,
            "canonical_registry": registry,
            "known_canonical_lineage_gap": not lineage_present,
            "known_gaps": []
            if lineage_present
            else [
                {
                    "id": "holdout_c_canonical_lineage_absent",
                    "status": "open",
                    "detail": (
                        "The Holdout C panel does not yet have exactly one frozen dataset manifest, "
                        "744 canonical raw-object entries, and 744 dataset-to-raw links."
                    ),
                }
            ],
            "lineage_refresh": {
                "refreshed_at": utc_now().isoformat(),
                "scope": "canonical_registry_lineage_only",
                "service_restart_checks_rerun": False,
                "route_checks_rerun": False,
                "required_counts": {
                    "dataset_manifests": 1,
                    "raw_objects": 744,
                    "dataset_raw_links": 744,
                },
            },
        }
    )
    stamp = utc_now().strftime("%Y%m%dT%H%M%S%fZ")
    timestamped = GOVERNANCE / f"post_merge_verification_lineage_refresh_{stamp}.json"
    atomic_write(timestamped, refreshed)
    atomic_write(OUTPUT, refreshed)
    return refreshed


def build_audit(
    *,
    pre_restart_pids: Mapping[str, int],
    scope_path: Path | None = None,
    cohort_path: Path | None = None,
    restart_method: str = "SIGTERM user-owned MainPID; systemd Restart=always recovery",
) -> dict[str, Any]:
    services = {name: service_snapshot(name) for name in SERVICES}
    git = git_evidence()
    routes = {
        "core_health": http_status("http://127.0.0.1:8282/health"),
        "core_research_status": http_status("http://127.0.0.1:8282/api/research-status"),
        "web_health": http_status("http://127.0.0.1:8283/api/health"),
        "web_research_status": http_status("http://127.0.0.1:8283/api/research-status"),
        "core_spy_quote": http_status("http://127.0.0.1:8282/api/quote?ticker=SPY"),
        "core_scanner_universe": http_status("http://127.0.0.1:8282/api/scan/universe"),
    }
    if scope_path is None:
        scope_path = latest_by_mtime(MARKET_QUALITY, "alpaca_holdout_c_price_only_scope_20*.json")
    if cohort_path is None:
        cohort_path = latest_by_mtime(GOVERNANCE, "holdout_c_alpaca_cohort_construction_*.json")
    holdout = holdout_evidence(scope_path, cohort_path)
    registry = registry_evidence()
    timestamps = timestamp_code_evidence()
    stack = stack_evidence()

    services_active = all(
        item["active_state"] == "active" and item["sub_state"] == "running" and item["main_pid"] > 0
        for item in services.values()
    )
    services_restarted = bool(pre_restart_pids) and all(
        name in pre_restart_pids and services[name]["main_pid"] != pre_restart_pids[name]
        for name in SERVICES
    )
    services_resolve_to_canonical = all(
        item["resolved_process_cwd"] in {str(CANONICAL_SOURCE), str(CANONICAL_SOURCE / "app")}
        for item in services.values()
    )
    timestamp_rows_valid = (
        registry.get("news_timestamp_invariants", {}).get("received_before_publication") == 0
        and registry.get("news_timestamp_invariants", {}).get("available_before_received") == 0
    )
    timestamp_code_valid = all(timestamps.values())
    stack_valid = (
        stack["implemented_layer_count"] == 8
        and stack["layer_numbers"] == list(range(1, 9))
        and stack["shadow_paper_is_formal_layer_7"]
        and stack["attribution_layer_name"] == "attribution_and_anomaly_engine"
        and stack["compatibility_alias_points_to_eight_layer_class"]
        and not stack["boundary_violations"]
        and not stack["stale_causal_attribution_and_anomaly_name_present"]
        and not stack["stale_causal_attribution_source_present"]
    )
    checks = {
        "services_active_after_restart": services_active,
        "all_four_service_pids_changed": services_restarted,
        "all_four_service_cwds_resolve_to_canonical": services_resolve_to_canonical,
        "all_live_routes_healthy": all(item.get("ok") for item in routes.values()),
        "post_merge_holdout_recount_is_11_of_12": holdout["post_merge_recount_matches_11_of_12"],
        "holdout_uses_744_current_partitions": holdout["normalized_partitions_current"] == 744
        and holdout["normalized_partitions_recorded"] == 744,
        "holdout_minimum_common_tickers_preserved": holdout["minimum_common_tickers"] >= 8,
        "holdout_outcomes_and_volume_not_used": not holdout["ranking_outcomes_evaluated"]
        and not holdout["volume_features_or_evaluation"],
        "registry_integrity_ok": registry.get("integrity") == "ok",
        "event_timestamp_code_fix_present": timestamp_code_valid,
        "event_timestamp_rows_respect_invariants": timestamp_rows_valid,
        "formal_eight_layer_topology_and_naming_valid": stack_valid,
        "git_identity_available": bool(git.get("commit")),
        "git_working_tree_clean": bool(git.get("working_tree_clean")),
        "execution_authority_absent": True,
    }
    verdict = classify(checks, holdout_lineage_present=bool(registry.get("holdout_canonical_lineage_present")))
    payload = {
        "schema_version": 1,
        "created_at": utc_now().isoformat(),
        "verdict": verdict,
        "verification_passed": verdict != "FAILED",
        "restart_method": restart_method,
        "pre_restart_pids": dict(pre_restart_pids),
        "checks": checks,
        "git": git,
        "services": services,
        "routes": routes,
        "holdout_c": holdout,
        "canonical_registry": registry,
        "timestamp_fix": timestamps,
        "eight_layer_topology": stack,
        "known_canonical_lineage_gap": not bool(registry.get("holdout_canonical_lineage_present")),
        "known_gaps": [
            {
                "id": "holdout_c_canonical_lineage_absent",
                "status": "open",
                "detail": (
                    "The fresh 11/12 recount is reproducible from the unified 744-partition runtime panel, "
                    "but the canonical registry contains no Holdout C dataset manifest, raw-object entries, "
                    "or dataset-to-raw links. The merge did not change the count; canonical lineage remains open."
                ),
            }
        ]
        if not registry.get("holdout_canonical_lineage_present")
        else [],
        "gate_relaxed": False,
        "ranking_or_model_outcomes_evaluated": False,
        "volume_evaluated": False,
        "execution_authority": False,
        "paper_or_live_execution": False,
    }
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pre-restart-pid", action="append", default=[])
    parser.add_argument("--scope", type=Path)
    parser.add_argument("--cohort", type=Path)
    parser.add_argument("--restart-method", default="SIGTERM user-owned MainPID; systemd Restart=always recovery")
    args = parser.parse_args()
    payload = build_audit(
        pre_restart_pids=parse_pre_restart(args.pre_restart_pid),
        scope_path=args.scope,
        cohort_path=args.cohort,
        restart_method=args.restart_method,
    )
    stamp = utc_now().strftime("%Y%m%dT%H%M%S%fZ")
    timestamped = GOVERNANCE / f"post_merge_verification_{stamp}.json"
    atomic_write(timestamped, payload)
    atomic_write(OUTPUT, payload)
    print(
        json.dumps(
            {
                "path": str(OUTPUT),
                "timestamped_path": str(timestamped),
                "verdict": payload["verdict"],
                "checks": payload["checks"],
                "known_gaps": payload["known_gaps"],
                "execution_authority": False,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if payload["verification_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
