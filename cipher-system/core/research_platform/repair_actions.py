"""Bounded operational repair actions with immutable incident records.

These helpers may retry delivery, rebuild derived caches, or recompute checksums.
They never alter frozen research inputs, source selection, gate thresholds,
parameters, promotion state, or execution authority.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from .hashing import canonical_json, stable_id
from .repair_boundary import RepairRequest, authorize_repair


@dataclass(frozen=True)
class RepairPolicy:
    max_attempts: int = 3
    backoff_seconds: float = 0.25

    def __post_init__(self) -> None:
        if not 1 <= self.max_attempts <= 3:
            raise ValueError("max_attempts must be between 1 and 3")
        if not 0 <= self.backoff_seconds <= 5:
            raise ValueError("backoff_seconds must be between 0 and 5")


class RepairExecutor:
    def __init__(self, incident_root: str | Path):
        self.incident_root = Path(incident_root).resolve()
        self.incident_root.mkdir(parents=True, exist_ok=True)

    def retry_transient_delivery(
        self,
        request: RepairRequest,
        operation: Callable[[], Mapping[str, Any]],
        *,
        policy: RepairPolicy = RepairPolicy(),
    ) -> dict[str, Any]:
        authorize_repair(request)
        if request.action != "retry_transient_delivery":
            raise ValueError("request action must be retry_transient_delivery")
        attempts: list[dict[str, Any]] = []
        result: Mapping[str, Any] | None = None
        for attempt in range(1, policy.max_attempts + 1):
            started = datetime.now(timezone.utc)
            try:
                result = dict(operation())
                attempts.append({"attempt": attempt, "started_at": started.isoformat(), "status": "succeeded"})
                break
            except Exception as exc:
                attempts.append({
                    "attempt": attempt,
                    "started_at": started.isoformat(),
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                })
                if attempt < policy.max_attempts:
                    time.sleep(policy.backoff_seconds * attempt)
        status = "repaired" if result is not None else "escalated_blocked"
        return self._record(request, status=status, attempts=attempts, result=dict(result or {}))

    def retry_validation_command(
        self,
        request: RepairRequest,
        operation: Callable[[], Mapping[str, Any]],
        *,
        policy: RepairPolicy = RepairPolicy(max_attempts=2, backoff_seconds=0.5),
    ) -> dict[str, Any]:
        """Retry a deterministic build/test command without modifying source.

        The operation must return a mapping containing ``returncode``. A zero
        return code ends the retry sequence; nonzero results are recorded and
        retried within the policy limit. Exceptions are recorded identically.
        """

        authorize_repair(request)
        if request.action != "retry_validation_command":
            raise ValueError("request action must be retry_validation_command")
        attempts: list[dict[str, Any]] = []
        result: Mapping[str, Any] | None = None
        for attempt in range(1, policy.max_attempts + 1):
            started = datetime.now(timezone.utc)
            try:
                candidate = dict(operation())
                returncode = int(candidate.get("returncode", 1))
                attempts.append(
                    {
                        "attempt": attempt,
                        "started_at": started.isoformat(),
                        "status": "succeeded" if returncode == 0 else "failed",
                        "returncode": returncode,
                    }
                )
                result = candidate
                if returncode == 0:
                    break
            except Exception as exc:
                attempts.append(
                    {
                        "attempt": attempt,
                        "started_at": started.isoformat(),
                        "status": "failed",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
            if attempt < policy.max_attempts:
                time.sleep(policy.backoff_seconds * attempt)
        repaired = bool(result is not None and int(result.get("returncode", 1)) == 0)
        return self._record(
            request,
            status="repaired" if repaired else "escalated_blocked",
            attempts=attempts,
            result=dict(result or {}),
        )

    def clear_generated_test_caches(
        self,
        request: RepairRequest,
        *,
        root: str | Path,
    ) -> dict[str, Any]:
        """Remove only generated Python/pytest cache material under ``root``."""

        authorize_repair(request)
        if request.action != "clear_generated_test_caches":
            raise ValueError("request action must be clear_generated_test_caches")
        root_path = Path(root).resolve()
        if not root_path.is_dir():
            raise FileNotFoundError(root_path)
        removed: list[str] = []
        protected_parts = {
            ".git",
            ".venv",
            "node_modules",
            "data",
            "logs",
            "previous-work",
            "access-obsidian-complete-audit",
            "mcp-server",
        }
        for path in sorted(root_path.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            resolved = path.resolve()
            try:
                relative = resolved.relative_to(root_path)
            except ValueError as exc:
                raise RuntimeError(f"cache path escaped repair root: {resolved}") from exc
            if protected_parts.intersection(relative.parts):
                continue
            if path.is_dir() and path.name in {"__pycache__", ".pytest_cache"}:
                shutil.rmtree(path)
                removed.append(str(path))
                continue
            if path.is_file() and path.suffix in {".pyc", ".pyo"}:
                path.unlink()
                removed.append(str(path))
        removed.sort()
        removed_digest = hashlib.sha256(canonical_json(removed).encode("utf-8")).hexdigest()
        result = {
            "root": str(root_path),
            "removed_count": len(removed),
            "removed_paths_sample": removed[:100],
            "removed_paths_truncated": len(removed) > 100,
            "removed_paths_sha256": removed_digest,
            "source_files_modified": False,
        }
        return self._record(
            request,
            status="repaired",
            attempts=[{"attempt": 1, "status": "succeeded"}],
            result=result,
        )

    def rebuild_derived_cache(
        self,
        request: RepairRequest,
        *,
        source: str | Path,
        destination: str | Path,
        transform: Callable[[bytes], bytes],
    ) -> dict[str, Any]:
        authorize_repair(request)
        if request.action != "rebuild_derived_cache":
            raise ValueError("request action must be rebuild_derived_cache")
        source_path = Path(source).resolve()
        destination_path = Path(destination).resolve()
        payload = source_path.read_bytes()
        derived = transform(payload)
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination_path.with_suffix(destination_path.suffix + ".tmp")
        temporary.write_bytes(derived)
        os.replace(temporary, destination_path)
        result = {
            "source": str(source_path),
            "source_sha256": hashlib.sha256(payload).hexdigest(),
            "destination": str(destination_path),
            "destination_sha256": hashlib.sha256(derived).hexdigest(),
            "destination_bytes": len(derived),
        }
        return self._record(request, status="repaired", attempts=[{"attempt": 1, "status": "succeeded"}], result=result)

    def recompute_checksum(
        self,
        request: RepairRequest,
        *,
        target: str | Path,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        authorize_repair(request)
        if request.action != "recompute_checksum":
            raise ValueError("request action must be recompute_checksum")
        path = Path(target).resolve()
        payload = path.read_bytes()
        observed = hashlib.sha256(payload).hexdigest()
        matches = expected_sha256 is None or observed == expected_sha256
        status = "verified" if matches else "escalated_blocked"
        result = {
            "target": str(path),
            "bytes": len(payload),
            "observed_sha256": observed,
            "expected_sha256": expected_sha256,
            "matches_expected": matches,
            "content_modified": False,
        }
        return self._record(request, status=status, attempts=[{"attempt": 1, "status": "succeeded"}], result=result)

    def _record(
        self,
        request: RepairRequest,
        *,
        status: str,
        attempts: list[dict[str, Any]],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        created_at = datetime.now(timezone.utc)
        identity = {
            "action": request.action,
            "target": request.target,
            "changes": dict(request.changes),
            "attempts": attempts,
            "result": result,
            "created_at": created_at.isoformat(),
        }
        incident_id = stable_id("repair", identity)
        payload = {
            "schema_version": 1,
            "incident_id": incident_id,
            "created_at": created_at.isoformat(),
            "action": request.action,
            "target": request.target,
            "changes": dict(request.changes),
            "status": status,
            "attempts": attempts,
            "result": result,
            "protected_research_fields_changed": False,
            "gate_relaxed": False,
            "promotion_changed": False,
            "execution_authority": False,
        }
        destination = self.incident_root / f"{incident_id}.json"
        encoded = (canonical_json(payload) + "\n").encode("utf-8")
        if destination.exists():
            if destination.read_bytes() != encoded:
                raise RuntimeError("immutable repair incident collision")
        else:
            temporary = destination.with_suffix(".tmp")
            temporary.write_bytes(encoded)
            try:
                os.link(temporary, destination)
            except FileExistsError:
                pass
            finally:
                temporary.unlink(missing_ok=True)
        return {**payload, "incident_path": str(destination)}


def canonical_json_cache(payload: bytes) -> bytes:
    parsed = json.loads(payload.decode("utf-8"))
    return (json.dumps(parsed, indent=2, sort_keys=True) + "\n").encode("utf-8")
