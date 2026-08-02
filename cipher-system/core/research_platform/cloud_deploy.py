from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from .bootstrap import ResearchPlatform
from .hashing import sha256_file, stable_id
from .models import AuditEvent, utc_now

CONFIRMATION_TOKEN = "ENABLE_CIPHER_RESEARCH_CLOUD_WRITES"


class CloudWriteBlockedError(RuntimeError):
    pass


@dataclass(frozen=True)
class CloudCommand:
    purpose: str
    argv: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"purpose": self.purpose, "argv": list(self.argv)}


class CloudDeploymentService:
    """Explicitly gated GCP deployment for schema and immutable artifacts."""

    def __init__(self, platform: ResearchPlatform):
        self.platform = platform

    def plan(self) -> dict[str, Any]:
        config = self.platform.config
        commands = (
            CloudCommand(
                "apply_bigquery_schema",
                (
                    "bq",
                    "query",
                    f"--project_id={config.bigquery_project}",
                    "--use_legacy_sql=false",
                    "< canonical schema SQL on stdin >",
                ),
            ),
            CloudCommand(
                "upload_immutable_artifact",
                (
                    "gcloud",
                    "storage",
                    "cp",
                    "< local artifact >",
                    f"gs://{config.gcs_bucket}/research-platform/artifacts/<sha256>",
                ),
            ),
        )
        return {
            "project": config.bigquery_project,
            "dataset": config.bigquery_dataset,
            "gcs_bucket": config.gcs_bucket,
            "cloud_writes_enabled": config.cloud_writes_enabled,
            "confirmation_token_required": CONFIRMATION_TOKEN,
            "commands": [command.to_dict() for command in commands],
            "automatic_data_backfill": False,
            "live_execution": False,
        }

    def deploy_schema(self, *, confirmation: str) -> dict[str, Any]:
        self._authorize(confirmation)
        if shutil.which("bq") is None:
            raise CloudWriteBlockedError("bq CLI is not installed")
        ddl = self.platform.warehouse.ddl()
        command = [
            "bq",
            "query",
            f"--project_id={self.platform.config.bigquery_project}",
            "--use_legacy_sql=false",
        ]
        completed = subprocess.run(
            command,
            input=ddl,
            text=True,
            capture_output=True,
            check=False,
            timeout=300,
        )
        payload = {
            "operation": "deploy_bigquery_schema",
            "project": self.platform.config.bigquery_project,
            "dataset": self.platform.config.bigquery_dataset,
            "returncode": completed.returncode,
            "stdout": completed.stdout[-8000:],
            "stderr": completed.stderr[-8000:],
            "completed_at": utc_now().isoformat(),
        }
        artifact = self.platform.artifacts.put_json(
            payload,
            metadata={"kind": "cloud_deployment_receipt", "operation": "deploy_bigquery_schema"},
        )
        self.platform.registry.register_artifact(artifact.to_dict())
        self.platform.registry.audit(
            AuditEvent(
                event_type="CLOUD_SCHEMA_DEPLOYMENT_ATTEMPTED",
                entity_type="bigquery_dataset",
                entity_id=f"{self.platform.config.bigquery_project}.{self.platform.config.bigquery_dataset}",
                occurred_at=utc_now(),
                payload={
                    "returncode": completed.returncode,
                    "artifact_id": artifact.artifact_id,
                    "data_backfill": False,
                    "live_execution": False,
                },
                actor="explicit_cli_confirmation",
            )
        )
        if completed.returncode != 0:
            raise RuntimeError(f"BigQuery schema deployment failed; receipt={artifact.artifact_id}")
        return {**payload, "receipt_artifact_id": artifact.artifact_id}

    def load_export_manifest(
        self,
        manifest_path: str | Path,
        *,
        confirmation: str,
    ) -> dict[str, Any]:
        """Load staged canonical JSONL batches with idempotent export/job IDs."""

        self._authorize(confirmation)
        if shutil.which("bq") is None:
            raise CloudWriteBlockedError("bq CLI is not installed")
        manifest_file = Path(manifest_path).resolve()
        payload = json.loads(manifest_file.read_text(encoding="utf-8"))
        exports = payload.get("exports") if isinstance(payload, dict) else None
        if not isinstance(exports, list) or not exports:
            raise ValueError("export manifest must contain a non-empty exports array")
        results: list[dict[str, Any]] = []
        for item in exports:
            if not isinstance(item, dict):
                raise ValueError("each warehouse export must be an object")
            export_id = str(item.get("export_id") or "")
            table = str(item.get("table") or "")
            expected_sha = str(item.get("sha256") or "")
            source = Path(str(item.get("jsonl_path") or "")).resolve()
            if not export_id or not table or not expected_sha:
                raise ValueError("warehouse export is missing export_id, table, or sha256")
            if not source.is_file():
                raise FileNotFoundError(source)
            actual_sha = sha256_file(source)
            if actual_sha != expected_sha:
                raise RuntimeError(f"warehouse export checksum mismatch: {export_id}")
            destination = f"{self.platform.config.bigquery_project}:{self.platform.config.bigquery_dataset}.{table}"
            job_id = "cipher_" + stable_id(
                "bq_load",
                {"export_id": export_id, "sha256": expected_sha, "destination": destination},
                length=48,
            ).replace("-", "_")
            with self.platform.registry.connect() as db:
                existing = db.execute(
                    "select * from warehouse_loads where export_id = ?",
                    (export_id,),
                ).fetchone()
            if existing:
                existing_payload = json.loads(existing["payload_json"])
                if existing["source_sha256"] != expected_sha or existing["destination"] != destination:
                    raise RuntimeError(f"warehouse export ID was reused with different content: {export_id}")
                if existing["status"] == "LOADED":
                    results.append({
                        "export_id": export_id,
                        "table": table,
                        "destination": destination,
                        "job_id": existing["job_id"],
                        "status": "SKIPPED_ALREADY_LOADED",
                        "loaded_at": existing["loaded_at"],
                        "receipt": existing_payload,
                    })
                    continue
            started = utc_now()
            initial_payload = {
                "export_id": export_id,
                "table": table,
                "source": str(source),
                "source_sha256": expected_sha,
                "row_count": item.get("row_count"),
                "destination": destination,
                "job_id": job_id,
                "started_at": started.isoformat(),
            }
            with self.platform.registry.connect() as db:
                db.execute(
                    """
                    insert into warehouse_loads(
                        export_id, table_name, source_sha256, destination, status,
                        job_id, loaded_at, payload_json
                    ) values (?, ?, ?, ?, 'STARTED', ?, null, ?)
                    on conflict(export_id) do update set
                        status = excluded.status,
                        job_id = excluded.job_id,
                        loaded_at = null,
                        payload_json = excluded.payload_json
                    """,
                    (
                        export_id,
                        table,
                        expected_sha,
                        destination,
                        job_id,
                        json.dumps(initial_payload, sort_keys=True),
                    ),
                )
            command = [
                "bq",
                "load",
                f"--project_id={self.platform.config.bigquery_project}",
                "--source_format=NEWLINE_DELIMITED_JSON",
                "--ignore_unknown_values=false",
                "--noreplace",
                f"--job_id={job_id}",
                destination,
                str(source),
            ]
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=1800,
            )
            loaded_at = utc_now()
            status = "LOADED" if completed.returncode == 0 else "FAILED"
            receipt_payload = {
                **initial_payload,
                "status": status,
                "loaded_at": loaded_at.isoformat(),
                "returncode": completed.returncode,
                "stdout": completed.stdout[-8000:],
                "stderr": completed.stderr[-8000:],
            }
            receipt = self.platform.artifacts.put_json(
                receipt_payload,
                metadata={
                    "kind": "warehouse_load_receipt",
                    "export_id": export_id,
                    "table": table,
                },
            )
            self.platform.registry.register_artifact(receipt.to_dict())
            receipt_payload["receipt_artifact_id"] = receipt.artifact_id
            with self.platform.registry.connect() as db:
                db.execute(
                    """
                    update warehouse_loads
                    set status = ?, loaded_at = ?, payload_json = ?
                    where export_id = ?
                    """,
                    (
                        status,
                        loaded_at.isoformat() if status == "LOADED" else None,
                        json.dumps(receipt_payload, sort_keys=True),
                        export_id,
                    ),
                )
            self.platform.registry.audit(
                AuditEvent(
                    event_type="WAREHOUSE_EXPORT_LOAD_ATTEMPTED",
                    entity_type="warehouse_export",
                    entity_id=export_id,
                    occurred_at=loaded_at,
                    payload={
                        "table": table,
                        "destination": destination,
                        "job_id": job_id,
                        "status": status,
                        "returncode": completed.returncode,
                        "receipt_artifact_id": receipt.artifact_id,
                    },
                    actor="explicit_cli_confirmation",
                )
            )
            results.append(receipt_payload)
            if completed.returncode != 0:
                raise RuntimeError(
                    f"BigQuery load failed for {export_id}; receipt={receipt.artifact_id}"
                )
        return {
            "manifest_path": str(manifest_file),
            "loads": results,
            "default_cloud_write_setting": self.platform.config.cloud_writes_enabled,
            "live_execution": False,
        }

    def upload_artifact(
        self,
        local_path: str | Path,
        *,
        confirmation: str,
        destination_name: str | None = None,
    ) -> dict[str, Any]:
        self._authorize(confirmation)
        if shutil.which("gcloud") is None:
            raise CloudWriteBlockedError("gcloud CLI is not installed")
        source = Path(local_path).resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        reference = self.platform.artifacts.put_file(
            source,
            metadata={"kind": "cloud_upload_source", "original_path": str(source)},
        )
        self.platform.registry.register_artifact(reference.to_dict())
        name = destination_name or reference.sha256
        destination = f"gs://{self.platform.config.gcs_bucket}/research-platform/artifacts/{name}"
        command = ["gcloud", "storage", "cp", str(source), destination]
        completed = subprocess.run(command, capture_output=True, text=True, check=False, timeout=600)
        payload = {
            "operation": "upload_artifact",
            "source_artifact_id": reference.artifact_id,
            "source_sha256": reference.sha256,
            "destination": destination,
            "returncode": completed.returncode,
            "stdout": completed.stdout[-8000:],
            "stderr": completed.stderr[-8000:],
            "completed_at": utc_now().isoformat(),
        }
        receipt = self.platform.artifacts.put_json(
            payload,
            metadata={"kind": "cloud_deployment_receipt", "operation": "upload_artifact"},
        )
        self.platform.registry.register_artifact(receipt.to_dict())
        self.platform.registry.audit(
            AuditEvent(
                event_type="CLOUD_ARTIFACT_UPLOAD_ATTEMPTED",
                entity_type="artifact",
                entity_id=reference.artifact_id,
                occurred_at=utc_now(),
                payload={
                    "destination": destination,
                    "returncode": completed.returncode,
                    "receipt_artifact_id": receipt.artifact_id,
                },
                actor="explicit_cli_confirmation",
            )
        )
        if completed.returncode != 0:
            raise RuntimeError(f"GCS artifact upload failed; receipt={receipt.artifact_id}")
        return {**payload, "receipt_artifact_id": receipt.artifact_id}

    def _authorize(self, confirmation: str) -> None:
        config = self.platform.config
        if not config.cloud_writes_enabled:
            raise CloudWriteBlockedError(
                "cloud writes are disabled in research-platform.json; planning and local exports remain available"
            )
        if confirmation != CONFIRMATION_TOKEN:
            raise CloudWriteBlockedError("explicit cloud-write confirmation token is required")
        if not config.gcs_bucket:
            raise CloudWriteBlockedError("GCS bucket is not configured")
        if not config.bigquery_project or config.bigquery_project.endswith("not-configured"):
            raise CloudWriteBlockedError("BigQuery project is not configured")
