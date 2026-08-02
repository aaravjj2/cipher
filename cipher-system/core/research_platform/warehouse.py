from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .artifact_store import ArtifactReference, ArtifactStore
from .hashing import sha256_file, stable_id
from .models import utc_now
from .registry import ResearchRegistry


@dataclass(frozen=True)
class WarehouseColumn:
    name: str
    data_type: str
    mode: str = "NULLABLE"


@dataclass(frozen=True)
class WarehouseTable:
    name: str
    columns: tuple[WarehouseColumn, ...]
    partition_field: str
    cluster_fields: tuple[str, ...]

    def ddl(self, project: str, dataset: str) -> str:
        columns = ",\n  ".join(
            f"`{column.name}` {column.data_type}{' NOT NULL' if column.mode == 'REQUIRED' else ''}"
            for column in self.columns
        )
        clustering = ", ".join(f"`{field}`" for field in self.cluster_fields)
        return (
            f"CREATE TABLE IF NOT EXISTS `{project}.{dataset}.{self.name}` (\n"
            f"  {columns}\n"
            f")\n"
            f"PARTITION BY DATE(`{self.partition_field}`)\n"
            f"CLUSTER BY {clustering};"
        )


COMMON_COLUMNS = (
    WarehouseColumn("record_id", "STRING", "REQUIRED"),
    WarehouseColumn("event_time", "TIMESTAMP", "REQUIRED"),
    WarehouseColumn("received_at", "TIMESTAMP", "REQUIRED"),
    WarehouseColumn("available_at", "TIMESTAMP", "REQUIRED"),
    WarehouseColumn("source", "STRING", "REQUIRED"),
    WarehouseColumn("raw_object_id", "STRING", "REQUIRED"),
    WarehouseColumn("schema_version", "INT64", "REQUIRED"),
    WarehouseColumn("payload_json", "JSON", "REQUIRED"),
)


def _market_table(name: str, extra: tuple[WarehouseColumn, ...], clusters: tuple[str, ...]) -> WarehouseTable:
    return WarehouseTable(name, COMMON_COLUMNS + extra, "available_at", clusters)


CANONICAL_TABLES: tuple[WarehouseTable, ...] = (
    _market_table(
        "market_bars",
        (
            WarehouseColumn("symbol", "STRING", "REQUIRED"),
            WarehouseColumn("timeframe", "STRING", "REQUIRED"),
            WarehouseColumn("open", "FLOAT64"),
            WarehouseColumn("high", "FLOAT64"),
            WarehouseColumn("low", "FLOAT64"),
            WarehouseColumn("close", "FLOAT64"),
            WarehouseColumn("volume", "FLOAT64"),
            WarehouseColumn("vwap", "FLOAT64"),
        ),
        ("symbol", "timeframe", "source"),
    ),
    _market_table(
        "option_quotes",
        (
            WarehouseColumn("symbol", "STRING", "REQUIRED"),
            WarehouseColumn("underlying", "STRING", "REQUIRED"),
            WarehouseColumn("expiration", "DATE"),
            WarehouseColumn("strike", "FLOAT64"),
            WarehouseColumn("option_type", "STRING"),
            WarehouseColumn("bid", "FLOAT64"),
            WarehouseColumn("ask", "FLOAT64"),
            WarehouseColumn("bid_size", "INT64"),
            WarehouseColumn("ask_size", "INT64"),
        ),
        ("underlying", "symbol", "expiration"),
    ),
    _market_table(
        "option_trades",
        (
            WarehouseColumn("symbol", "STRING", "REQUIRED"),
            WarehouseColumn("underlying", "STRING", "REQUIRED"),
            WarehouseColumn("price", "FLOAT64"),
            WarehouseColumn("size", "INT64"),
            WarehouseColumn("exchange", "STRING"),
            WarehouseColumn("conditions", "ARRAY<STRING>"),
        ),
        ("underlying", "symbol", "source"),
    ),
    _market_table(
        "option_contract_reference",
        (
            WarehouseColumn("symbol", "STRING", "REQUIRED"),
            WarehouseColumn("underlying", "STRING", "REQUIRED"),
            WarehouseColumn("expiration", "DATE"),
            WarehouseColumn("strike", "FLOAT64"),
            WarehouseColumn("option_type", "STRING"),
            WarehouseColumn("open_interest", "INT64"),
            WarehouseColumn("open_interest_date", "DATE"),
        ),
        ("underlying", "expiration", "symbol"),
    ),
    _market_table(
        "gex_snapshots",
        (
            WarehouseColumn("snapshot_id", "STRING", "REQUIRED"),
            WarehouseColumn("symbol", "STRING", "REQUIRED"),
            WarehouseColumn("spot", "FLOAT64"),
            WarehouseColumn("call_wall", "FLOAT64"),
            WarehouseColumn("put_wall", "FLOAT64"),
            WarehouseColumn("gamma_flip", "FLOAT64"),
        ),
        ("symbol", "source", "snapshot_id"),
    ),
    _market_table(
        "gex_strike_cells",
        (
            WarehouseColumn("snapshot_id", "STRING", "REQUIRED"),
            WarehouseColumn("symbol", "STRING", "REQUIRED"),
            WarehouseColumn("expiration", "DATE", "REQUIRED"),
            WarehouseColumn("strike", "FLOAT64", "REQUIRED"),
            WarehouseColumn("call_gex", "FLOAT64"),
            WarehouseColumn("put_gex", "FLOAT64"),
            WarehouseColumn("net_gex", "FLOAT64"),
            WarehouseColumn("call_oi", "FLOAT64"),
            WarehouseColumn("put_oi", "FLOAT64"),
            WarehouseColumn("available", "BOOL", "REQUIRED"),
        ),
        ("symbol", "expiration", "snapshot_id"),
    ),
    _market_table(
        "scanner_signals",
        (
            WarehouseColumn("signal_id", "STRING", "REQUIRED"),
            WarehouseColumn("symbol", "STRING", "REQUIRED"),
            WarehouseColumn("scanner_type", "STRING", "REQUIRED"),
            WarehouseColumn("direction", "STRING"),
            WarehouseColumn("setup", "STRING"),
            WarehouseColumn("strength", "FLOAT64"),
            WarehouseColumn("rank", "INT64"),
        ),
        ("scanner_type", "symbol", "setup"),
    ),
    _market_table(
        "news_events",
        (
            WarehouseColumn("event_id", "STRING", "REQUIRED"),
            WarehouseColumn("symbols", "ARRAY<STRING>"),
            WarehouseColumn("publisher", "STRING"),
            WarehouseColumn("publication_time", "TIMESTAMP"),
            WarehouseColumn("event_type", "STRING"),
            WarehouseColumn("positive_probability", "FLOAT64"),
            WarehouseColumn("negative_probability", "FLOAT64"),
            WarehouseColumn("neutral_probability", "FLOAT64"),
            WarehouseColumn("model_artifact_id", "STRING"),
        ),
        ("event_type", "publisher", "source"),
    ),
    _market_table(
        "model_forecasts",
        (
            WarehouseColumn("forecast_id", "STRING", "REQUIRED"),
            WarehouseColumn("feature_id", "STRING", "REQUIRED"),
            WarehouseColumn("symbol", "STRING", "REQUIRED"),
            WarehouseColumn("horizon_seconds", "INT64"),
            WarehouseColumn("point_forecast", "FLOAT64"),
            WarehouseColumn("lower_bound", "FLOAT64"),
            WarehouseColumn("upper_bound", "FLOAT64"),
            WarehouseColumn("model_artifact_id", "STRING"),
            WarehouseColumn("allowed_use", "STRING", "REQUIRED"),
        ),
        ("symbol", "feature_id", "allowed_use"),
    ),
    _market_table(
        "feature_vectors",
        (
            WarehouseColumn("feature_vector_id", "STRING", "REQUIRED"),
            WarehouseColumn("symbol", "STRING", "REQUIRED"),
            WarehouseColumn("feature_family", "STRING", "REQUIRED"),
            WarehouseColumn("feature_values", "JSON", "REQUIRED"),
            WarehouseColumn("model_artifact_id", "STRING"),
            WarehouseColumn("allowed_use", "STRING", "REQUIRED"),
        ),
        ("symbol", "feature_family", "allowed_use"),
    ),
    _market_table(
        "factor_candidates",
        (
            WarehouseColumn("factor_id", "STRING", "REQUIRED"),
            WarehouseColumn("name", "STRING", "REQUIRED"),
            WarehouseColumn("expression", "STRING", "REQUIRED"),
            WarehouseColumn("base_columns", "ARRAY<STRING>", "REQUIRED"),
            WarehouseColumn("candidate_source", "STRING", "REQUIRED"),
            WarehouseColumn("allowed_use", "STRING", "REQUIRED"),
            WarehouseColumn("leakage_checks", "JSON", "REQUIRED"),
        ),
        ("candidate_source", "allowed_use", "name"),
    ),
    _market_table(
        "anomaly_log",
        (
            WarehouseColumn("anomaly_id", "STRING", "REQUIRED"),
            WarehouseColumn("symbol", "STRING", "REQUIRED"),
            WarehouseColumn("forecast_id", "STRING", "REQUIRED"),
            WarehouseColumn("realized_value", "FLOAT64", "REQUIRED"),
            WarehouseColumn("expected_lower", "FLOAT64", "REQUIRED"),
            WarehouseColumn("expected_upper", "FLOAT64", "REQUIRED"),
            WarehouseColumn("severity", "STRING", "REQUIRED"),
            WarehouseColumn("linked_event_ids", "ARRAY<STRING>"),
            WarehouseColumn("allowed_use", "STRING", "REQUIRED"),
        ),
        ("symbol", "severity", "allowed_use"),
    ),
    _market_table(
        "forward_outcomes",
        (
            WarehouseColumn("signal_id", "STRING", "REQUIRED"),
            WarehouseColumn("strategy_id", "STRING", "REQUIRED"),
            WarehouseColumn("symbol", "STRING", "REQUIRED"),
            WarehouseColumn("exit_time", "TIMESTAMP"),
            WarehouseColumn("pnl_pct", "FLOAT64"),
            WarehouseColumn("mfe_pct", "FLOAT64"),
            WarehouseColumn("mae_pct", "FLOAT64"),
            WarehouseColumn("exit_reason", "STRING"),
        ),
        ("strategy_id", "symbol", "exit_reason"),
    ),
    WarehouseTable(
        "backtest_gate_results",
        (
            WarehouseColumn("record_id", "STRING", "REQUIRED"),
            WarehouseColumn("strategy_id", "STRING", "REQUIRED"),
            WarehouseColumn("dataset_id", "STRING", "REQUIRED"),
            WarehouseColumn("tier", "STRING", "REQUIRED"),
            WarehouseColumn("engine", "STRING", "REQUIRED"),
            WarehouseColumn("completed_at", "TIMESTAMP", "REQUIRED"),
            WarehouseColumn("available_at", "TIMESTAMP", "REQUIRED"),
            WarehouseColumn("passed", "BOOL", "REQUIRED"),
            WarehouseColumn("metrics_json", "JSON", "REQUIRED"),
            WarehouseColumn("failure_reasons", "ARRAY<STRING>"),
            WarehouseColumn("payload_json", "JSON", "REQUIRED"),
        ),
        "available_at",
        ("strategy_id", "tier", "passed"),
    ),
    WarehouseTable(
        "experiment_metrics",
        (
            WarehouseColumn("record_id", "STRING", "REQUIRED"),
            WarehouseColumn("experiment_id", "STRING", "REQUIRED"),
            WarehouseColumn("strategy_id", "STRING", "REQUIRED"),
            WarehouseColumn("dataset_id", "STRING", "REQUIRED"),
            WarehouseColumn("engine", "STRING", "REQUIRED"),
            WarehouseColumn("completed_at", "TIMESTAMP", "REQUIRED"),
            WarehouseColumn("available_at", "TIMESTAMP", "REQUIRED"),
            WarehouseColumn("verdict", "STRING", "REQUIRED"),
            WarehouseColumn("metrics_json", "JSON", "REQUIRED"),
            WarehouseColumn("payload_json", "JSON", "REQUIRED"),
        ),
        "available_at",
        ("strategy_id", "engine", "verdict"),
    ),
    WarehouseTable(
        "portfolio_proposals",
        (
            WarehouseColumn("record_id", "STRING", "REQUIRED"),
            WarehouseColumn("proposal_id", "STRING", "REQUIRED"),
            WarehouseColumn("generated_at", "TIMESTAMP", "REQUIRED"),
            WarehouseColumn("available_at", "TIMESTAMP", "REQUIRED"),
            WarehouseColumn("objective", "STRING", "REQUIRED"),
            WarehouseColumn("weights_json", "JSON", "REQUIRED"),
            WarehouseColumn("cash_weight", "FLOAT64", "REQUIRED"),
            WarehouseColumn("simulation_only", "BOOL", "REQUIRED"),
            WarehouseColumn("payload_json", "JSON", "REQUIRED"),
        ),
        "available_at",
        ("objective", "simulation_only", "proposal_id"),
    ),
    WarehouseTable(
        "execution_audit",
        (
            WarehouseColumn("record_id", "STRING", "REQUIRED"),
            WarehouseColumn("event_time", "TIMESTAMP", "REQUIRED"),
            WarehouseColumn("available_at", "TIMESTAMP", "REQUIRED"),
            WarehouseColumn("strategy_id", "STRING", "REQUIRED"),
            WarehouseColumn("signal_id", "STRING"),
            WarehouseColumn("symbol", "STRING", "REQUIRED"),
            WarehouseColumn("mode", "STRING", "REQUIRED"),
            WarehouseColumn("simulated_fill_json", "JSON"),
            WarehouseColumn("latency_ms", "FLOAT64"),
            WarehouseColumn("payload_json", "JSON", "REQUIRED"),
        ),
        "available_at",
        ("mode", "strategy_id", "symbol"),
    ),
    WarehouseTable(
        "autoresearch_feedback",
        (
            WarehouseColumn("record_id", "STRING", "REQUIRED"),
            WarehouseColumn("feedback_id", "STRING", "REQUIRED"),
            WarehouseColumn("generated_at", "TIMESTAMP", "REQUIRED"),
            WarehouseColumn("available_at", "TIMESTAMP", "REQUIRED"),
            WarehouseColumn("target_layer", "STRING", "REQUIRED"),
            WarehouseColumn("routes_to_live", "BOOL", "REQUIRED"),
            WarehouseColumn("prompt_revisions_json", "JSON", "REQUIRED"),
            WarehouseColumn("bandit_updates_json", "JSON", "REQUIRED"),
            WarehouseColumn("payload_json", "JSON", "REQUIRED"),
        ),
        "available_at",
        ("target_layer", "routes_to_live", "feedback_id"),
    ),
    WarehouseTable(
        "audit_events",
        (
            WarehouseColumn("record_id", "STRING", "REQUIRED"),
            WarehouseColumn("event_type", "STRING", "REQUIRED"),
            WarehouseColumn("entity_type", "STRING", "REQUIRED"),
            WarehouseColumn("entity_id", "STRING", "REQUIRED"),
            WarehouseColumn("occurred_at", "TIMESTAMP", "REQUIRED"),
            WarehouseColumn("available_at", "TIMESTAMP", "REQUIRED"),
            WarehouseColumn("actor", "STRING", "REQUIRED"),
            WarehouseColumn("payload_json", "JSON", "REQUIRED"),
        ),
        "available_at",
        ("entity_type", "entity_id", "event_type"),
    ),
)


@dataclass(frozen=True)
class WarehouseExport:
    export_id: str
    table: str
    created_at: str
    row_count: int
    jsonl_path: str
    sha256: str
    artifact_id: str

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


class BigQueryWarehousePlan:
    """Generate canonical BigQuery DDL and idempotent JSONL load artifacts.

    Network deployment is intentionally separate from this package. Existing GCP
    tooling may upload the generated files after cost and project review.
    """

    def __init__(
        self,
        *,
        project: str,
        dataset: str,
        export_root: str | Path,
        artifact_store: ArtifactStore,
        registry: ResearchRegistry,
    ):
        self.project = project
        self.dataset = dataset
        self.export_root = Path(export_root).resolve()
        self.export_root.mkdir(parents=True, exist_ok=True)
        self.artifact_store = artifact_store
        self.registry = registry

    def ddl(self) -> str:
        header = (
            f"CREATE SCHEMA IF NOT EXISTS `{self.project}.{self.dataset}`\n"
            "OPTIONS(location='US');"
        )
        return header + "\n\n" + "\n\n".join(table.ddl(self.project, self.dataset) for table in CANONICAL_TABLES) + "\n"

    def write_ddl(self) -> ArtifactReference:
        reference = self.artifact_store.put_text(
            self.ddl(),
            content_type="application/sql",
            metadata={"kind": "bigquery_schema", "project": self.project, "dataset": self.dataset},
        )
        self.registry.register_artifact(reference.to_dict())
        return reference

    def export_rows(self, table: str, rows: Iterable[Mapping[str, Any]]) -> WarehouseExport:
        if table not in {item.name for item in CANONICAL_TABLES}:
            raise ValueError(f"unsupported canonical table: {table}")
        created = utc_now()
        export_id = stable_id(
            "warehouse_export",
            {"table": table, "created_at": created.isoformat(), "project": self.project, "dataset": self.dataset},
        )
        path = self.export_root / table / f"{export_id}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        with path.open("x", encoding="utf-8") as handle:
            for row in rows:
                self._validate_availability(row)
                handle.write(json.dumps(dict(row), sort_keys=True, separators=(",", ":"), default=self._json_default))
                handle.write("\n")
                count += 1
        digest = sha256_file(path)
        artifact = self.artifact_store.put_file(
            path,
            content_type="application/x-ndjson",
            metadata={"kind": "bigquery_load_batch", "table": table, "row_count": count, "export_id": export_id},
        )
        self.registry.register_artifact(artifact.to_dict())
        return WarehouseExport(
            export_id=export_id,
            table=table,
            created_at=created.isoformat(),
            row_count=count,
            jsonl_path=str(path),
            sha256=digest,
            artifact_id=artifact.artifact_id,
        )

    def load_command(self, export: WarehouseExport) -> tuple[str, ...]:
        """Return an explicit command vector rather than executing cloud writes."""

        return (
            "bq",
            "load",
            "--source_format=NEWLINE_DELIMITED_JSON",
            "--ignore_unknown_values=false",
            f"{self.project}:{self.dataset}.{export.table}",
            export.jsonl_path,
        )

    @staticmethod
    def _validate_availability(row: Mapping[str, Any]) -> None:
        if "available_at" not in row:
            raise ValueError("warehouse rows require available_at")
        value = row["available_at"]
        if isinstance(value, datetime):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("available_at must be timezone-aware")
        elif not isinstance(value, str) or not value.strip():
            raise ValueError("available_at must be an ISO timestamp or aware datetime")

    @staticmethod
    def _json_default(value: Any) -> Any:
        if isinstance(value, datetime):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("warehouse datetime values must be timezone-aware")
            return value.astimezone(timezone.utc).isoformat()
        if isinstance(value, Path):
            return str(value)
        raise TypeError(type(value).__name__)
