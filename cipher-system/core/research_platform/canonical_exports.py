from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

from .hashing import stable_id
from .models import AuditEvent, utc_now
from .registry import ResearchRegistry
from .warehouse import BigQueryWarehousePlan, WarehouseExport


class CanonicalExportError(RuntimeError):
    pass


class CanonicalSQLiteExporter:
    """Stage frozen SQLite data into canonical point-in-time warehouse batches."""

    def __init__(self, registry: ResearchRegistry, warehouse: BigQueryWarehousePlan):
        self.registry = registry
        self.warehouse = warehouse

    def export(
        self,
        *,
        dataset_id: str,
        sqlite_path: str | Path,
        kind: str,
        after: str | None = None,
        limit: int | None = None,
    ) -> tuple[WarehouseExport, ...]:
        dataset = self.registry.get_payload("datasets", "dataset_id", dataset_id)
        if not dataset.get("frozen"):
            raise CanonicalExportError("canonical warehouse exports require a frozen dataset")
        quality = dataset.get("quality_checks") or {}
        if quality.get("passed") is False or quality.get("failures"):
            raise CanonicalExportError("dataset quality checks did not pass")
        raw_ids = list(dataset.get("raw_object_ids") or ())
        if not raw_ids:
            raise CanonicalExportError("dataset has no raw-object provenance")
        context = {
            "dataset_id": dataset_id,
            "source": str((dataset.get("sources") or ["unknown"])[0]),
            "raw_object_id": str(raw_ids[0]),
            "availability_cutoff": _timestamp(dataset["availability_cutoff"]),
        }
        path = Path(sqlite_path).resolve()
        if kind == "historical_bars":
            exports = (
                self.warehouse.export_rows(
                    "market_bars",
                    self._historical_bars(path, context, after=after, limit=limit),
                ),
            )
        elif kind == "gex_history":
            exports = (
                self.warehouse.export_rows(
                    "gex_snapshots",
                    self._gex_snapshots(path, context, after=after, limit=limit),
                ),
                self.warehouse.export_rows(
                    "gex_strike_cells",
                    self._gex_cells(path, context, after=after, limit=limit),
                ),
            )
        elif kind == "tradier_stream":
            exports = (
                self.warehouse.export_rows(
                    "option_quotes",
                    self._tradier_quotes(path, context, after=after, limit=limit),
                ),
                self.warehouse.export_rows(
                    "option_trades",
                    self._tradier_trades(path, context, after=after, limit=limit),
                ),
            )
        else:
            raise CanonicalExportError(f"unsupported SQLite export kind: {kind}")
        self.registry.audit(
            AuditEvent(
                event_type="CANONICAL_WAREHOUSE_BATCH_STAGED",
                entity_type="dataset",
                entity_id=dataset_id,
                occurred_at=utc_now(),
                payload={
                    "kind": kind,
                    "source_path": str(path),
                    "after": after,
                    "limit": limit,
                    "exports": [item.to_dict() for item in exports],
                    "loaded": False,
                },
            )
        )
        return exports

    def _historical_bars(
        self,
        path: Path,
        context: Mapping[str, Any],
        *,
        after: str | None,
        limit: int | None,
    ) -> Iterator[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if after:
            clauses.append("timestamp > ?")
            params.append(after)
        query = "select * from historical_bars"
        if clauses:
            query += " where " + " and ".join(clauses)
        query += " order by timestamp, symbol"
        if limit is not None:
            query += " limit ?"
            params.append(int(limit))
        with _readonly(path) as db:
            for row in db.execute(query, tuple(params)):
                event_time = _timestamp(row["timestamp"])
                payload = dict(row)
                yield self._base(
                    context,
                    record_key={"symbol": row["symbol"], "timestamp": event_time.isoformat()},
                    event_time=event_time,
                    payload=payload,
                    symbol=str(row["symbol"]),
                    timeframe=str(payload.get("timeframe") or "unknown"),
                    open=payload.get("open"),
                    high=payload.get("high"),
                    low=payload.get("low"),
                    close=payload.get("close"),
                    volume=payload.get("volume"),
                    vwap=payload.get("vwap"),
                )

    def _gex_snapshots(
        self,
        path: Path,
        context: Mapping[str, Any],
        *,
        after: str | None,
        limit: int | None,
    ) -> Iterator[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if after:
            clauses.append("captured_at > ?")
            params.append(after)
        query = "select * from gex_snapshots"
        if clauses:
            query += " where " + " and ".join(clauses)
        query += " order by captured_at, ticker, id"
        if limit is not None:
            query += " limit ?"
            params.append(int(limit))
        with _readonly(path) as db:
            for row in db.execute(query, tuple(params)):
                event_time = _timestamp(row["captured_at"])
                snapshot_id = stable_id(
                    "gex_snapshot",
                    {"raw_object_id": context["raw_object_id"], "source_id": row["id"]},
                )
                yield self._base(
                    context,
                    record_key={"snapshot_id": snapshot_id},
                    event_time=event_time,
                    payload=dict(row),
                    snapshot_id=snapshot_id,
                    symbol=str(row["ticker"]),
                    spot=row["spot"],
                    call_wall=row["call_wall_strike"],
                    put_wall=row["put_wall_strike"],
                    gamma_flip=row["gamma_flip_level"],
                )

    def _gex_cells(
        self,
        path: Path,
        context: Mapping[str, Any],
        *,
        after: str | None,
        limit: int | None,
    ) -> Iterator[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if after:
            clauses.append("c.captured_at > ?")
            params.append(after)
        query = """
            select c.*, s.id as source_snapshot_id
            from gex_strike_cells c
            join gex_snapshots s on s.id = c.snapshot_id
        """
        if clauses:
            query += " where " + " and ".join(clauses)
        query += " order by c.captured_at, c.ticker, c.expiration, c.strike"
        if limit is not None:
            query += " limit ?"
            params.append(int(limit))
        with _readonly(path) as db:
            for row in db.execute(query, tuple(params)):
                event_time = _timestamp(row["captured_at"])
                snapshot_id = stable_id(
                    "gex_snapshot",
                    {"raw_object_id": context["raw_object_id"], "source_id": row["source_snapshot_id"]},
                )
                payload = dict(row)
                yield self._base(
                    context,
                    record_key={
                        "snapshot_id": snapshot_id,
                        "expiration": row["expiration"],
                        "strike": row["strike"],
                    },
                    event_time=event_time,
                    payload=payload,
                    snapshot_id=snapshot_id,
                    symbol=str(row["ticker"]),
                    expiration=str(row["expiration"]),
                    strike=row["strike"],
                    call_gex=row["call_gex"],
                    put_gex=row["put_gex"],
                    net_gex=row["net_gex"],
                    call_oi=row["call_oi"],
                    put_oi=row["put_oi"],
                    available=bool(row["available"]),
                )

    def _tradier_quotes(
        self,
        path: Path,
        context: Mapping[str, Any],
        *,
        after: str | None,
        limit: int | None,
    ) -> Iterator[dict[str, Any]]:
        where = "event_type in ('quote','summary') and (bid is not null or ask is not null)"
        params: list[Any] = []
        if after:
            where += " and captured_at > ?"
            params.append(after)
        query = f"select * from tradier_stream_events where {where} order by captured_at, id"
        if limit is not None:
            query += " limit ?"
            params.append(int(limit))
        with _readonly(path) as db:
            for row in db.execute(query, tuple(params)):
                event_time = _timestamp(row["provider_ts"] or row["captured_at"])
                available = _timestamp(row["captured_at"])
                payload = dict(row)
                yield self._base(
                    context,
                    record_key={"source_event_id": row["id"], "record_type": "quote"},
                    event_time=event_time,
                    available_at=min(available, context["availability_cutoff"]),
                    payload=payload,
                    symbol=str(row["symbol"]),
                    underlying=str(row["underlying"] or row["symbol"]),
                    expiration=row["option_expiration"],
                    strike=row["strike"],
                    option_type=row["option_type"],
                    bid=row["bid"],
                    ask=row["ask"],
                    bid_size=None,
                    ask_size=None,
                )

    def _tradier_trades(
        self,
        path: Path,
        context: Mapping[str, Any],
        *,
        after: str | None,
        limit: int | None,
    ) -> Iterator[dict[str, Any]]:
        where = "event_type = 'trade' and coalesce(price, last) is not null"
        params: list[Any] = []
        if after:
            where += " and captured_at > ?"
            params.append(after)
        query = f"select * from tradier_stream_events where {where} order by captured_at, id"
        if limit is not None:
            query += " limit ?"
            params.append(int(limit))
        with _readonly(path) as db:
            for row in db.execute(query, tuple(params)):
                event_time = _timestamp(row["provider_ts"] or row["captured_at"])
                available = _timestamp(row["captured_at"])
                payload = dict(row)
                yield self._base(
                    context,
                    record_key={"source_event_id": row["id"], "record_type": "trade"},
                    event_time=event_time,
                    available_at=min(available, context["availability_cutoff"]),
                    payload=payload,
                    symbol=str(row["symbol"]),
                    underlying=str(row["underlying"] or row["symbol"]),
                    price=row["price"] if row["price"] is not None else row["last"],
                    size=None if row["size"] is None else int(row["size"]),
                    exchange=None,
                    conditions=[],
                )

    @staticmethod
    def _base(
        context: Mapping[str, Any],
        *,
        record_key: Mapping[str, Any],
        event_time: datetime,
        payload: Mapping[str, Any],
        available_at: datetime | None = None,
        **fields: Any,
    ) -> dict[str, Any]:
        availability = available_at or context["availability_cutoff"]
        if availability < event_time:
            # A frozen dataset may have an incorrectly declared cutoff. Do not
            # manufacture a record that appears available before its event.
            availability = context["availability_cutoff"]
            if availability < event_time:
                raise CanonicalExportError("dataset availability cutoff precedes an event timestamp")
        return {
            "record_id": stable_id(
                "warehouse_record",
                {
                    "dataset_id": context["dataset_id"],
                    "raw_object_id": context["raw_object_id"],
                    "record_key": dict(record_key),
                },
            ),
            "event_time": event_time,
            "received_at": availability,
            "available_at": availability,
            "source": context["source"],
            "raw_object_id": context["raw_object_id"],
            "schema_version": 1,
            "payload_json": dict(payload),
            **fields,
        }


@contextmanager
def _readonly(path: Path):
    uri = f"file:{path.as_posix()}?mode=ro"
    db = sqlite3.connect(uri, uri=True, timeout=60)
    db.row_factory = sqlite3.Row
    try:
        yield db
    finally:
        db.close()


def _timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)) or str(value).strip().isdigit():
        numeric = float(value)
        while numeric > 10_000_000_000:
            numeric /= 1000.0
        parsed = datetime.fromtimestamp(numeric, timezone.utc)
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CanonicalExportError(f"timestamp lacks timezone: {value}")
    return parsed.astimezone(timezone.utc)
