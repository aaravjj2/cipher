from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence

from .hashing import canonical_json
from .models import (
    AuditEvent,
    DatasetManifest,
    ExperimentManifest,
    ExperimentResult,
    FeatureSnapshot,
    FeatureSpec,
    PromotionEvent,
    PromotionState,
    RawObjectManifest,
    StrategySpec,
    utc_now,
)

SCHEMA_VERSION = 3


class RegistryConflictError(RuntimeError):
    """Raised when an immutable identifier is reused with different content."""


class RegistryNotFoundError(KeyError):
    pass


class ResearchRegistry:
    """SQLite governance registry with append-only evidence and audit events."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("pragma journal_mode=WAL")
        db.execute("pragma foreign_keys=ON")
        db.execute("pragma busy_timeout=30000")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def migrate(self) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute("pragma journal_mode=WAL")
            db.execute("pragma foreign_keys=ON")
            db.executescript(
                """
                create table if not exists schema_migrations (
                    version integer primary key,
                    applied_at text not null
                );

                create table if not exists raw_objects (
                    raw_object_id text primary key,
                    source text not null,
                    dataset text not null,
                    uri text not null,
                    checksum text not null,
                    checksum_method text not null,
                    size_bytes integer not null,
                    received_at text not null,
                    available_at text not null,
                    ingestion_run_id text not null,
                    disposition text not null,
                    payload_json text not null
                );
                create index if not exists idx_raw_objects_source_dataset
                    on raw_objects(source, dataset, available_at);
                create unique index if not exists idx_raw_objects_checksum_uri
                    on raw_objects(checksum, uri);

                create table if not exists datasets (
                    dataset_id text primary key,
                    name text not null,
                    created_at text not null,
                    availability_cutoff text not null,
                    schema_name text not null,
                    frozen integer not null,
                    quality_passed integer not null,
                    payload_json text not null
                );
                create index if not exists idx_datasets_name_cutoff
                    on datasets(name, availability_cutoff);

                create table if not exists dataset_raw_objects (
                    dataset_id text not null references datasets(dataset_id),
                    raw_object_id text not null references raw_objects(raw_object_id),
                    primary key(dataset_id, raw_object_id)
                );

                create table if not exists features (
                    feature_id text primary key,
                    name text not null,
                    version text not null,
                    allowed_use text not null,
                    implementation_hash text not null,
                    payload_json text not null
                );
                create index if not exists idx_features_name_version
                    on features(name, version);

                create table if not exists feature_snapshots (
                    snapshot_id text primary key,
                    feature_id text not null references features(feature_id),
                    dataset_id text not null references datasets(dataset_id),
                    symbol text not null,
                    event_time text not null,
                    available_at text not null,
                    payload_json text not null
                );
                create index if not exists idx_feature_snapshots_pit
                    on feature_snapshots(feature_id, symbol, available_at, event_time);

                create table if not exists strategies (
                    strategy_id text primary key,
                    name text not null,
                    version text not null,
                    current_state text not null,
                    created_at text not null,
                    payload_json text not null
                );
                create index if not exists idx_strategies_name_version
                    on strategies(name, version);

                create table if not exists experiments (
                    experiment_id text primary key,
                    strategy_id text not null references strategies(strategy_id),
                    dataset_id text not null references datasets(dataset_id),
                    engine text not null,
                    status text not null,
                    started_at text not null,
                    completed_at text,
                    verdict text,
                    manifest_json text not null,
                    result_json text
                );
                create index if not exists idx_experiments_strategy_engine
                    on experiments(strategy_id, engine, started_at);

                create table if not exists artifacts (
                    artifact_id text primary key,
                    sha256 text not null unique,
                    size_bytes integer not null,
                    content_type text not null,
                    data_path text not null,
                    metadata_path text not null,
                    created_at text not null,
                    payload_json text not null
                );

                create table if not exists experiment_artifacts (
                    experiment_id text not null references experiments(experiment_id),
                    role text not null,
                    artifact_id text not null references artifacts(artifact_id),
                    primary key(experiment_id, role)
                );

                create table if not exists promotion_events (
                    event_id text primary key,
                    strategy_id text not null references strategies(strategy_id),
                    from_state text not null,
                    to_state text not null,
                    decided_at text not null,
                    actor text not null,
                    reason text not null,
                    payload_json text not null
                );
                create index if not exists idx_promotion_strategy_time
                    on promotion_events(strategy_id, decided_at);

                create table if not exists audit_events (
                    event_id text primary key,
                    event_type text not null,
                    entity_type text not null,
                    entity_id text not null,
                    occurred_at text not null,
                    actor text not null,
                    payload_json text not null
                );
                create index if not exists idx_audit_entity_time
                    on audit_events(entity_type, entity_id, occurred_at);

                create table if not exists prospective_tests (
                    prospective_test_id text primary key,
                    strategy_id text not null references strategies(strategy_id),
                    registration_json text not null,
                    minimum_sample integer not null,
                    scored_count integer not null default 0,
                    status text not null,
                    created_at text not null,
                    updated_at text not null
                );

                create table if not exists prospective_observations (
                    observation_id text primary key,
                    prospective_test_id text not null references prospective_tests(prospective_test_id),
                    signal_time text not null,
                    available_at text not null,
                    status text not null,
                    payload_json text not null
                );
                create index if not exists idx_prospective_test_time
                    on prospective_observations(prospective_test_id, signal_time);

                create table if not exists news_events (
                    news_event_id text primary key,
                    source text not null,
                    publication_time text not null,
                    received_at text not null,
                    available_at text not null,
                    symbols_json text not null,
                    sentiment_model_id text,
                    payload_json text not null
                );
                create index if not exists idx_news_events_available
                    on news_events(available_at, source);

                create table if not exists anomaly_events (
                    anomaly_id text primary key,
                    symbol text not null,
                    event_time text not null,
                    available_at text not null,
                    severity real not null,
                    suitable_for_evaluation integer not null,
                    payload_json text not null
                );
                create index if not exists idx_anomaly_symbol_time
                    on anomaly_events(symbol, event_time);

                create table if not exists inventory_snapshots (
                    inventory_id text primary key,
                    created_at text not null,
                    code_hash text not null,
                    payload_json text not null
                );

                create table if not exists evidence_reconciliations (
                    reconciliation_id text primary key,
                    strategy_id text not null references strategies(strategy_id),
                    created_at text not null,
                    status text not null,
                    payload_json text not null
                );

                create table if not exists warehouse_loads (
                    export_id text primary key,
                    table_name text not null,
                    source_sha256 text not null,
                    destination text not null,
                    status text not null,
                    job_id text not null unique,
                    loaded_at text,
                    payload_json text not null
                );
                create index if not exists idx_warehouse_loads_status
                    on warehouse_loads(status, table_name);
                """
            )
            db.execute(
                "insert or ignore into schema_migrations(version, applied_at) values (?, ?)",
                (SCHEMA_VERSION, utc_now().isoformat()),
            )

    @staticmethod
    def _payload(value: Mapping[str, Any] | Any) -> str:
        if hasattr(value, "to_dict"):
            value = value.to_dict()
        return canonical_json(value)

    @staticmethod
    def _immutable_insert(
        db: sqlite3.Connection,
        *,
        table: str,
        id_column: str,
        entity_id: str,
        payload_column: str,
        payload_json: str,
        insert_sql: str,
        values: tuple[Any, ...],
    ) -> bool:
        existing = db.execute(
            f"select {payload_column} from {table} where {id_column} = ?",
            (entity_id,),
        ).fetchone()
        if existing:
            if existing[payload_column] != payload_json:
                raise RegistryConflictError(f"{table} identifier {entity_id} was reused with different content")
            return False
        db.execute(insert_sql, values)
        return True

    @classmethod
    def _audit_in_connection(cls, db: sqlite3.Connection, event: AuditEvent) -> bool:
        payload = cls._payload(event)
        return cls._immutable_insert(
            db,
            table="audit_events",
            id_column="event_id",
            entity_id=event.event_id,
            payload_column="payload_json",
            payload_json=payload,
            insert_sql="""
                insert into audit_events(
                    event_id, event_type, entity_type, entity_id, occurred_at, actor, payload_json
                ) values (?, ?, ?, ?, ?, ?, ?)
            """,
            values=(
                event.event_id,
                event.event_type,
                event.entity_type,
                event.entity_id,
                event.occurred_at.isoformat(),
                event.actor,
                payload,
            ),
        )

    def register_dataset_bundle(
        self,
        raw_manifests: Sequence[RawObjectManifest],
        manifest: DatasetManifest,
        *,
        actor: str = "system",
        precommit_validator: Callable[[sqlite3.Connection, DatasetManifest], None] | None = None,
    ) -> dict[str, int | bool]:
        """Atomically register raw objects, one dataset, and all lineage links.

        The optional validator runs after the rows are visible inside the same
        transaction but before commit. Raising from the validator rolls back the
        complete bundle. This is intended for evidence migrations where a
        canonical re-derivation must succeed before any lineage becomes durable.
        """

        raw_objects = tuple(raw_manifests)
        raw_ids = tuple(item.raw_object_id for item in raw_objects)
        if len(raw_ids) != len(set(raw_ids)):
            raise ValueError("raw_manifests contains duplicate raw_object_id values")
        if set(raw_ids) != set(manifest.raw_object_ids):
            raise ValueError("dataset raw_object_ids do not exactly match the supplied raw manifests")

        occurred_at = utc_now()
        raw_inserted = 0
        links_inserted = 0
        with self.connect() as db:
            for raw in raw_objects:
                payload = self._payload(raw)
                inserted = self._immutable_insert(
                    db,
                    table="raw_objects",
                    id_column="raw_object_id",
                    entity_id=raw.raw_object_id,
                    payload_column="payload_json",
                    payload_json=payload,
                    insert_sql="""
                        insert into raw_objects(
                            raw_object_id, source, dataset, uri, checksum, checksum_method,
                            size_bytes, received_at, available_at, ingestion_run_id,
                            disposition, payload_json
                        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values=(
                        raw.raw_object_id,
                        raw.source,
                        raw.dataset,
                        raw.uri,
                        raw.checksum,
                        raw.checksum_method,
                        raw.size_bytes,
                        raw.received_at.isoformat(),
                        raw.available_at.isoformat(),
                        raw.ingestion_run_id,
                        raw.disposition.value,
                        payload,
                    ),
                )
                if inserted:
                    raw_inserted += 1
                    self._audit_in_connection(
                        db,
                        AuditEvent(
                            event_type="RAW_OBJECT_REGISTERED",
                            entity_type="raw_object",
                            entity_id=raw.raw_object_id,
                            occurred_at=occurred_at,
                            actor=actor,
                            payload={"source": raw.source, "dataset": raw.dataset, "uri": raw.uri},
                        ),
                    )

            dataset_payload = self._payload(manifest)
            dataset_inserted = self._immutable_insert(
                db,
                table="datasets",
                id_column="dataset_id",
                entity_id=manifest.dataset_id,
                payload_column="payload_json",
                payload_json=dataset_payload,
                insert_sql="""
                    insert into datasets(
                        dataset_id, name, created_at, availability_cutoff, schema_name,
                        frozen, quality_passed, payload_json
                    ) values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values=(
                    manifest.dataset_id,
                    manifest.name,
                    manifest.created_at.isoformat(),
                    manifest.availability_cutoff.isoformat(),
                    manifest.schema_name,
                    1 if manifest.frozen else 0,
                    1 if manifest.quality_passed else 0,
                    dataset_payload,
                ),
            )
            for raw_id in manifest.raw_object_ids:
                cursor = db.execute(
                    "insert or ignore into dataset_raw_objects(dataset_id, raw_object_id) values (?, ?)",
                    (manifest.dataset_id, raw_id),
                )
                links_inserted += max(0, int(cursor.rowcount or 0))

            if precommit_validator is not None:
                precommit_validator(db, manifest)

            if dataset_inserted:
                self._audit_in_connection(
                    db,
                    AuditEvent(
                        event_type="DATASET_REGISTERED",
                        entity_type="dataset",
                        entity_id=manifest.dataset_id,
                        occurred_at=occurred_at,
                        actor=actor,
                        payload={"name": manifest.name, "quality_passed": manifest.quality_passed},
                    ),
                )
            self._audit_in_connection(
                db,
                AuditEvent(
                    event_type="DATASET_BUNDLE_VERIFIED_AND_REGISTERED",
                    entity_type="dataset",
                    entity_id=manifest.dataset_id,
                    occurred_at=occurred_at,
                    actor=actor,
                    payload={
                        "raw_object_count": len(raw_objects),
                        "raw_objects_inserted": raw_inserted,
                        "dataset_inserted": dataset_inserted,
                        "links_inserted": links_inserted,
                        "precommit_validator_used": precommit_validator is not None,
                    },
                ),
            )

        return {
            "raw_object_count": len(raw_objects),
            "raw_objects_inserted": raw_inserted,
            "raw_objects_existing": len(raw_objects) - raw_inserted,
            "dataset_inserted": dataset_inserted,
            "links_inserted": links_inserted,
        }

    def register_raw_object(self, manifest: RawObjectManifest) -> bool:
        payload = self._payload(manifest)
        with self.connect() as db:
            inserted = self._immutable_insert(
                db,
                table="raw_objects",
                id_column="raw_object_id",
                entity_id=manifest.raw_object_id,
                payload_column="payload_json",
                payload_json=payload,
                insert_sql="""
                    insert into raw_objects(
                        raw_object_id, source, dataset, uri, checksum, checksum_method,
                        size_bytes, received_at, available_at, ingestion_run_id,
                        disposition, payload_json
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values=(
                    manifest.raw_object_id,
                    manifest.source,
                    manifest.dataset,
                    manifest.uri,
                    manifest.checksum,
                    manifest.checksum_method,
                    manifest.size_bytes,
                    manifest.received_at.isoformat(),
                    manifest.available_at.isoformat(),
                    manifest.ingestion_run_id,
                    manifest.disposition.value,
                    payload,
                ),
            )
        if inserted:
            self.audit(
                AuditEvent(
                    event_type="RAW_OBJECT_REGISTERED",
                    entity_type="raw_object",
                    entity_id=manifest.raw_object_id,
                    occurred_at=utc_now(),
                    payload={"source": manifest.source, "dataset": manifest.dataset, "uri": manifest.uri},
                )
            )
        return inserted

    def register_dataset(self, manifest: DatasetManifest) -> bool:
        payload = self._payload(manifest)
        with self.connect() as db:
            for raw_id in manifest.raw_object_ids:
                if not db.execute("select 1 from raw_objects where raw_object_id = ?", (raw_id,)).fetchone():
                    raise RegistryNotFoundError(f"raw object not registered: {raw_id}")
            inserted = self._immutable_insert(
                db,
                table="datasets",
                id_column="dataset_id",
                entity_id=manifest.dataset_id,
                payload_column="payload_json",
                payload_json=payload,
                insert_sql="""
                    insert into datasets(
                        dataset_id, name, created_at, availability_cutoff, schema_name,
                        frozen, quality_passed, payload_json
                    ) values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values=(
                    manifest.dataset_id,
                    manifest.name,
                    manifest.created_at.isoformat(),
                    manifest.availability_cutoff.isoformat(),
                    manifest.schema_name,
                    1 if manifest.frozen else 0,
                    1 if manifest.quality_passed else 0,
                    payload,
                ),
            )
            for raw_id in manifest.raw_object_ids:
                db.execute(
                    "insert or ignore into dataset_raw_objects(dataset_id, raw_object_id) values (?, ?)",
                    (manifest.dataset_id, raw_id),
                )
        if inserted:
            self.audit(
                AuditEvent(
                    event_type="DATASET_REGISTERED",
                    entity_type="dataset",
                    entity_id=manifest.dataset_id,
                    occurred_at=utc_now(),
                    payload={"name": manifest.name, "quality_passed": manifest.quality_passed},
                )
            )
        return inserted

    def register_feature(self, spec: FeatureSpec) -> bool:
        payload = self._payload(spec)
        with self.connect() as db:
            inserted = self._immutable_insert(
                db,
                table="features",
                id_column="feature_id",
                entity_id=spec.feature_id,
                payload_column="payload_json",
                payload_json=payload,
                insert_sql="""
                    insert into features(feature_id, name, version, allowed_use, implementation_hash, payload_json)
                    values (?, ?, ?, ?, ?, ?)
                """,
                values=(spec.feature_id, spec.name, spec.version, spec.allowed_use.value, spec.implementation_hash, payload),
            )
        if inserted:
            self.audit(
                AuditEvent(
                    event_type="FEATURE_REGISTERED",
                    entity_type="feature",
                    entity_id=spec.feature_id,
                    occurred_at=utc_now(),
                    payload={"name": spec.name, "allowed_use": spec.allowed_use.value},
                )
            )
        return inserted

    def register_feature_snapshot(self, snapshot: FeatureSnapshot) -> bool:
        payload = self._payload(snapshot)
        with self.connect() as db:
            if not db.execute("select 1 from features where feature_id = ?", (snapshot.feature_id,)).fetchone():
                raise RegistryNotFoundError(f"feature not registered: {snapshot.feature_id}")
            if not db.execute("select 1 from datasets where dataset_id = ?", (snapshot.dataset_id,)).fetchone():
                raise RegistryNotFoundError(f"dataset not registered: {snapshot.dataset_id}")
            return self._immutable_insert(
                db,
                table="feature_snapshots",
                id_column="snapshot_id",
                entity_id=snapshot.snapshot_id,
                payload_column="payload_json",
                payload_json=payload,
                insert_sql="""
                    insert into feature_snapshots(
                        snapshot_id, feature_id, dataset_id, symbol, event_time, available_at, payload_json
                    ) values (?, ?, ?, ?, ?, ?, ?)
                """,
                values=(
                    snapshot.snapshot_id,
                    snapshot.feature_id,
                    snapshot.dataset_id,
                    snapshot.symbol,
                    snapshot.event_time.isoformat(),
                    snapshot.available_at.isoformat(),
                    payload,
                ),
            )

    def register_strategy(self, spec: StrategySpec) -> bool:
        payload = self._payload(spec)
        created_at = utc_now().isoformat()
        with self.connect() as db:
            for feature_id in spec.required_feature_ids:
                if not db.execute("select 1 from features where feature_id = ?", (feature_id,)).fetchone():
                    raise RegistryNotFoundError(f"required feature not registered: {feature_id}")
            inserted = self._immutable_insert(
                db,
                table="strategies",
                id_column="strategy_id",
                entity_id=spec.strategy_id,
                payload_column="payload_json",
                payload_json=payload,
                insert_sql="""
                    insert into strategies(strategy_id, name, version, current_state, created_at, payload_json)
                    values (?, ?, ?, ?, ?, ?)
                """,
                values=(spec.strategy_id, spec.name, spec.version, PromotionState.IDEA.value, created_at, payload),
            )
        if inserted:
            self.audit(
                AuditEvent(
                    event_type="STRATEGY_REGISTERED",
                    entity_type="strategy",
                    entity_id=spec.strategy_id,
                    occurred_at=utc_now(),
                    payload={"name": spec.name, "version": spec.version, "initial_state": PromotionState.IDEA.value},
                )
            )
        return inserted

    def begin_experiment(self, manifest: ExperimentManifest) -> bool:
        payload = self._payload(manifest)
        with self.connect() as db:
            if not db.execute("select 1 from strategies where strategy_id = ?", (manifest.strategy_id,)).fetchone():
                raise RegistryNotFoundError(f"strategy not registered: {manifest.strategy_id}")
            if not db.execute("select 1 from datasets where dataset_id = ?", (manifest.dataset_id,)).fetchone():
                raise RegistryNotFoundError(f"dataset not registered: {manifest.dataset_id}")
            inserted = self._immutable_insert(
                db,
                table="experiments",
                id_column="experiment_id",
                entity_id=manifest.experiment_id,
                payload_column="manifest_json",
                payload_json=payload,
                insert_sql="""
                    insert into experiments(
                        experiment_id, strategy_id, dataset_id, engine, status,
                        started_at, manifest_json
                    ) values (?, ?, ?, ?, 'RUNNING', ?, ?)
                """,
                values=(
                    manifest.experiment_id,
                    manifest.strategy_id,
                    manifest.dataset_id,
                    manifest.engine.value,
                    manifest.started_at.isoformat(),
                    payload,
                ),
            )
        if inserted:
            self.audit(
                AuditEvent(
                    event_type="EXPERIMENT_STARTED",
                    entity_type="experiment",
                    entity_id=manifest.experiment_id,
                    occurred_at=manifest.started_at,
                    payload={"strategy_id": manifest.strategy_id, "engine": manifest.engine.value},
                )
            )
        return inserted

    def complete_experiment(self, result: ExperimentResult) -> None:
        payload = self._payload(result)
        with self.connect() as db:
            row = db.execute(
                "select status, result_json from experiments where experiment_id = ?",
                (result.experiment_id,),
            ).fetchone()
            if not row:
                raise RegistryNotFoundError(f"experiment not registered: {result.experiment_id}")
            if row["result_json"]:
                if row["result_json"] != payload:
                    raise RegistryConflictError(f"experiment {result.experiment_id} already has a different result")
                return
            db.execute(
                """
                update experiments
                set status = 'COMPLETED', completed_at = ?, verdict = ?, result_json = ?
                where experiment_id = ? and result_json is null
                """,
                (result.completed_at.isoformat(), result.verdict.value, payload, result.experiment_id),
            )
        self.audit(
            AuditEvent(
                event_type="EXPERIMENT_COMPLETED",
                entity_type="experiment",
                entity_id=result.experiment_id,
                occurred_at=result.completed_at,
                payload={"verdict": result.verdict.value, "result_id": result.result_id},
            )
        )

    def register_artifact(self, reference: Mapping[str, Any]) -> bool:
        payload = self._payload(reference)
        artifact_id = str(reference["artifact_id"])
        with self.connect() as db:
            return self._immutable_insert(
                db,
                table="artifacts",
                id_column="artifact_id",
                entity_id=artifact_id,
                payload_column="payload_json",
                payload_json=payload,
                insert_sql="""
                    insert into artifacts(
                        artifact_id, sha256, size_bytes, content_type, data_path,
                        metadata_path, created_at, payload_json
                    ) values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values=(
                    artifact_id,
                    str(reference["sha256"]),
                    int(reference["size_bytes"]),
                    str(reference["content_type"]),
                    str(reference["data_path"]),
                    str(reference["metadata_path"]),
                    str(reference["created_at"]),
                    payload,
                ),
            )

    def link_experiment_artifact(self, experiment_id: str, role: str, artifact_id: str) -> None:
        with self.connect() as db:
            if not db.execute("select 1 from experiments where experiment_id = ?", (experiment_id,)).fetchone():
                raise RegistryNotFoundError(f"experiment not registered: {experiment_id}")
            if not db.execute("select 1 from artifacts where artifact_id = ?", (artifact_id,)).fetchone():
                raise RegistryNotFoundError(f"artifact not registered: {artifact_id}")
            existing = db.execute(
                "select artifact_id from experiment_artifacts where experiment_id = ? and role = ?",
                (experiment_id, role),
            ).fetchone()
            if existing and existing["artifact_id"] != artifact_id:
                raise RegistryConflictError(f"experiment artifact role already assigned: {role}")
            db.execute(
                "insert or ignore into experiment_artifacts(experiment_id, role, artifact_id) values (?, ?, ?)",
                (experiment_id, role, artifact_id),
            )

    def record_promotion(self, event: PromotionEvent) -> None:
        payload = self._payload(event)
        with self.connect() as db:
            row = db.execute(
                "select current_state from strategies where strategy_id = ?",
                (event.strategy_id,),
            ).fetchone()
            if not row:
                raise RegistryNotFoundError(f"strategy not registered: {event.strategy_id}")
            current = PromotionState(row["current_state"])
            if current != event.from_state:
                raise RegistryConflictError(
                    f"promotion state changed concurrently: expected {event.from_state.value}, found {current.value}"
                )
            inserted = self._immutable_insert(
                db,
                table="promotion_events",
                id_column="event_id",
                entity_id=event.event_id,
                payload_column="payload_json",
                payload_json=payload,
                insert_sql="""
                    insert into promotion_events(
                        event_id, strategy_id, from_state, to_state, decided_at,
                        actor, reason, payload_json
                    ) values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values=(
                    event.event_id,
                    event.strategy_id,
                    event.from_state.value,
                    event.to_state.value,
                    event.decided_at.isoformat(),
                    event.actor,
                    event.reason,
                    payload,
                ),
            )
            if inserted:
                db.execute(
                    "update strategies set current_state = ? where strategy_id = ? and current_state = ?",
                    (event.to_state.value, event.strategy_id, event.from_state.value),
                )
        if inserted:
            self.audit(
                AuditEvent(
                    event_type="STRATEGY_PROMOTED",
                    entity_type="strategy",
                    entity_id=event.strategy_id,
                    occurred_at=event.decided_at,
                    actor=event.actor,
                    payload={
                        "from_state": event.from_state.value,
                        "to_state": event.to_state.value,
                        "reason": event.reason,
                        "evidence_ids": list(event.evidence_ids),
                    },
                )
            )

    def audit(self, event: AuditEvent) -> bool:
        with self.connect() as db:
            return self._audit_in_connection(db, event)

    def get_payload(self, table: str, id_column: str, entity_id: str, payload_column: str = "payload_json") -> dict[str, Any]:
        allowed = {
            ("raw_objects", "raw_object_id", "payload_json"),
            ("datasets", "dataset_id", "payload_json"),
            ("features", "feature_id", "payload_json"),
            ("feature_snapshots", "snapshot_id", "payload_json"),
            ("strategies", "strategy_id", "payload_json"),
            ("experiments", "experiment_id", "manifest_json"),
            ("experiments", "experiment_id", "result_json"),
            ("promotion_events", "event_id", "payload_json"),
            ("audit_events", "event_id", "payload_json"),
            ("artifacts", "artifact_id", "payload_json"),
        }
        if (table, id_column, payload_column) not in allowed:
            raise ValueError("unsupported registry payload lookup")
        with self.connect() as db:
            row = db.execute(
                f"select {payload_column} from {table} where {id_column} = ?",
                (entity_id,),
            ).fetchone()
        if not row or not row[payload_column]:
            raise RegistryNotFoundError(entity_id)
        return json.loads(row[payload_column])

    def current_state(self, strategy_id: str) -> PromotionState:
        with self.connect() as db:
            row = db.execute(
                "select current_state from strategies where strategy_id = ?",
                (strategy_id,),
            ).fetchone()
        if not row:
            raise RegistryNotFoundError(strategy_id)
        return PromotionState(row["current_state"])

    def experiment_summary(self, experiment_id: str) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute("select * from experiments where experiment_id = ?", (experiment_id,)).fetchone()
            if not row:
                raise RegistryNotFoundError(experiment_id)
            artifacts = [
                dict(item)
                for item in db.execute(
                    """
                    select ea.role, a.artifact_id, a.sha256, a.data_path
                    from experiment_artifacts ea
                    join artifacts a on a.artifact_id = ea.artifact_id
                    where ea.experiment_id = ?
                    order by ea.role
                    """,
                    (experiment_id,),
                ).fetchall()
            ]
        result = dict(row)
        result["manifest"] = json.loads(result.pop("manifest_json"))
        result["result"] = json.loads(result.pop("result_json")) if result.get("result_json") else None
        result["artifacts"] = artifacts
        return result

    def point_in_time_features(
        self,
        *,
        feature_ids: list[str],
        symbol: str,
        decision_time: str,
    ) -> dict[str, dict[str, Any]]:
        if not feature_ids:
            return {}
        placeholders = ",".join("?" for _ in feature_ids)
        params: list[Any] = [*feature_ids, symbol.upper(), decision_time]
        query = f"""
            select fs.feature_id, fs.payload_json
            from feature_snapshots fs
            join (
                select feature_id, max(available_at) as max_available_at
                from feature_snapshots
                where feature_id in ({placeholders})
                  and symbol = ?
                  and available_at <= ?
                group by feature_id
            ) latest
              on latest.feature_id = fs.feature_id
             and latest.max_available_at = fs.available_at
            where fs.symbol = ?
        """
        params.append(symbol.upper())
        with self.connect() as db:
            rows = db.execute(query, tuple(params)).fetchall()
        return {str(row["feature_id"]): json.loads(row["payload_json"]) for row in rows}

    def counts(self) -> dict[str, int]:
        tables = (
            "raw_objects",
            "datasets",
            "features",
            "feature_snapshots",
            "strategies",
            "experiments",
            "artifacts",
            "promotion_events",
            "audit_events",
            "prospective_tests",
            "prospective_observations",
            "news_events",
            "anomaly_events",
            "inventory_snapshots",
            "evidence_reconciliations",
            "warehouse_loads",
        )
        with self.connect() as db:
            return {table: int(db.execute(f"select count(*) from {table}").fetchone()[0]) for table in tables}
