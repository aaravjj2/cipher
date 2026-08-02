#!/usr/bin/env python3
"""Import immutable Windows/GCS scanner batches into Cipher scanner-ingest v2.

The Windows capture pipeline mirrors raw JSON batches onto the VM under
``data/browser_ingest/raw_windows``. This importer validates and deduplicates
those batches, then submits them to Cipher's loopback-only scanner ingest
endpoint so normalization and signal-episode assignment happen in the same
long-running Node process as live browser ingestion.

This module is read-only with respect to markets and brokerage accounts. It
never submits orders or calls a trading endpoint.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, time as clock_time, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
CORE_DIR = ROOT / "core"
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))
DEFAULT_INPUT_ROOT = (
    ROOT
    / "data"
    / "browser_ingest"
    / "raw_windows"
    / "device-windows"
    / "uploaded"
)
DEFAULT_LEDGER = ROOT / "data" / "browser_ingest" / "gcs_import_ledger.sqlite"
DEFAULT_TOKEN_FILE = ROOT / "app" / ".scanner-ingest-token"
DEFAULT_ENDPOINT = "http://127.0.0.1:8283/api/scanner-ingest"
MAX_FILE_BYTES = 1_048_576
MAX_CARDS = 1_000
ALLOWED_SCAN_TYPES = {"flash", "flash_agentic", "cluster"}
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
BATCH_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,11}$")
NEW_YORK = ZoneInfo("America/New_York")
DEFAULT_MARKET_START = "09:25"
DEFAULT_MARKET_END = "16:25"


class ImportValidationError(ValueError):
    """Raised when a raw browser batch is structurally unsafe to import."""


@dataclass(frozen=True)
class LoadedBatch:
    path: Path
    file_sha256: str
    file_bytes: int
    payload: dict[str, Any]
    batch_id: str
    scan_type: str
    captured_at: str
    declared_sha256: str
    card_count: int
    warnings: tuple[str, ...]


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def register_governance_batch(batch: LoadedBatch) -> dict[str, Any] | None:
    try:
        from research_platform.ingestion_hooks import hooks_enabled, register_ingestion_file

        if not hooks_enabled():
            return None
        captured = datetime.fromisoformat(batch.captured_at.replace("Z", "+00:00"))
        return register_ingestion_file(
            batch.path,
            source="browser_gcs_capture",
            dataset=f"scanner_{batch.scan_type}_raw",
            ingestion_run_id=batch.batch_id,
            event_time_start=captured,
            event_time_end=captured,
            metadata={
                "batch_id": batch.batch_id,
                "scan_type": batch.scan_type,
                "card_count": batch.card_count,
                "file_sha256": batch.file_sha256,
            },
        )
    except Exception as exc:
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}


def parse_clock(value: str) -> clock_time:
    try:
        hour_text, minute_text = value.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
    except (AttributeError, TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("time must use HH:MM format") from exc
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise argparse.ArgumentTypeError("time must use HH:MM format")
    return clock_time(hour, minute)


def within_market_window(
    now: datetime | None = None,
    *,
    start: clock_time = clock_time(9, 25),
    end: clock_time = clock_time(16, 25),
) -> bool:
    current = (now or datetime.now(timezone.utc)).astimezone(NEW_YORK)
    local_time = current.time().replace(tzinfo=None)
    return current.weekday() < 5 and start <= local_time <= end


def ensure_schema(ledger_path: Path) -> None:
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(ledger_path) as db:
        db.execute("pragma journal_mode=WAL")
        db.execute("pragma synchronous=NORMAL")
        db.executescript(
            """
            create table if not exists imported_batches (
                id integer primary key autoincrement,
                source_path text not null unique,
                batch_id text not null unique,
                scan_type text not null,
                captured_at text not null,
                file_sha256 text not null,
                declared_sha256 text not null default '',
                file_bytes integer not null,
                card_count integer not null,
                status text not null,
                first_seen_at text not null,
                last_attempt_at text not null,
                imported_at text,
                request_id text,
                response_json text,
                warnings_json text not null default '[]',
                error text
            );

            create index if not exists idx_imported_batches_status
                on imported_batches(status);
            create index if not exists idx_imported_batches_scan_type
                on imported_batches(scan_type);
            """
        )
        db.commit()


def discover_json_files(input_root: Path) -> list[Path]:
    if not input_root.exists():
        return []
    return sorted(
        path
        for path in input_root.rglob("*.json")
        if path.is_file() and not path.name.startswith(".")
    )


def _parse_timestamp(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise ImportValidationError("missing captured_at")
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ImportValidationError("captured_at is not ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ImportValidationError("captured_at must include a timezone")
    return text


def load_batch(path: Path, max_file_bytes: int = MAX_FILE_BYTES) -> LoadedBatch:
    raw = path.read_bytes()
    if not raw:
        raise ImportValidationError("file is empty")
    if len(raw) > max_file_bytes:
        raise ImportValidationError(
            f"file exceeds {max_file_bytes} byte ingest limit"
        )

    file_sha256 = hashlib.sha256(raw).hexdigest()
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ImportValidationError(f"invalid UTF-8 JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ImportValidationError("top-level JSON must be an object")

    schema_version = payload.get("schema_version")
    if schema_version != 2:
        raise ImportValidationError("schema_version must equal 2")

    source = str(payload.get("source") or "").strip().lower()
    if source != "accessobsidian":
        raise ImportValidationError("source must equal accessobsidian")

    scan_type = str(payload.get("scan_type") or "").strip().lower()
    if scan_type not in ALLOWED_SCAN_TYPES:
        raise ImportValidationError(
            f"scan_type must be one of {sorted(ALLOWED_SCAN_TYPES)}"
        )

    batch_id = str(payload.get("batch_id") or "").strip()
    if not BATCH_ID_RE.fullmatch(batch_id):
        raise ImportValidationError("batch_id is missing or malformed")

    captured_at = _parse_timestamp(payload.get("captured_at"))

    cards = payload.get("cards")
    if not isinstance(cards, list):
        raise ImportValidationError("cards must be an array")
    if len(cards) > MAX_CARDS:
        raise ImportValidationError(f"cards exceeds maximum of {MAX_CARDS}")

    warnings: list[str] = []
    for index, card in enumerate(cards):
        if not isinstance(card, dict):
            raise ImportValidationError(f"card {index} is not an object")
        ticker = str(card.get("ticker") or "").strip().upper()
        if not TICKER_RE.fullmatch(ticker):
            raise ImportValidationError(f"card {index} has malformed ticker")

    declared_sha256 = str(payload.get("sha256") or "").strip().lower()
    if declared_sha256:
        if not SHA256_RE.fullmatch(declared_sha256):
            raise ImportValidationError("declared sha256 is malformed")
        if declared_sha256 != file_sha256:
            # The Windows producer hashes its pre-serialization batch material,
            # not the final pretty-printed JSON bytes. Preserve both values for
            # provenance instead of falsely claiming byte-level equivalence.
            warnings.append("declared_hash_uses_producer_pre_serialization_scheme")
    else:
        warnings.append("missing_declared_sha256")

    return LoadedBatch(
        path=path,
        file_sha256=file_sha256,
        file_bytes=len(raw),
        payload=payload,
        batch_id=batch_id,
        scan_type=scan_type,
        captured_at=captured_at,
        declared_sha256=declared_sha256,
        card_count=len(cards),
        warnings=tuple(warnings),
    )


def read_token(token_file: Path) -> str:
    token = token_file.read_text(encoding="utf-8").strip()
    if not token:
        raise RuntimeError(f"scanner ingest token is empty: {token_file}")
    return token


def post_payload(
    *,
    endpoint: str,
    token: str,
    payload: dict[str, Any],
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
    request = urllib.request.Request(
        endpoint,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Cipher-Ingest-Token": token,
            "User-Agent": "cipher-gcs-importer/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            response_body = response.read().decode("utf-8")
            parsed = json.loads(response_body)
            if response.status not in {200, 201, 202}:
                raise RuntimeError(
                    f"scanner ingest returned HTTP {response.status}: {response_body}"
                )
            if not isinstance(parsed, dict) or not parsed.get("ok"):
                raise RuntimeError(f"scanner ingest rejected payload: {response_body}")
            return parsed
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"scanner ingest HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"scanner ingest connection failed: {exc.reason}") from exc


def _existing_batch(
    db: sqlite3.Connection, batch: LoadedBatch
) -> sqlite3.Row | None:
    db.row_factory = sqlite3.Row
    return db.execute(
        """
        select *
        from imported_batches
        where batch_id = ? or source_path = ?
        limit 1
        """,
        (batch.batch_id, str(batch.path.resolve())),
    ).fetchone()


def _mark_processing(db: sqlite3.Connection, batch: LoadedBatch) -> None:
    now = utcnow()
    existing = _existing_batch(db, batch)
    if existing is None:
        db.execute(
            """
            insert into imported_batches (
                source_path, batch_id, scan_type, captured_at,
                file_sha256, declared_sha256, file_bytes, card_count,
                status, first_seen_at, last_attempt_at, warnings_json
            ) values (?, ?, ?, ?, ?, ?, ?, ?, 'processing', ?, ?, ?)
            """,
            (
                str(batch.path.resolve()),
                batch.batch_id,
                batch.scan_type,
                batch.captured_at,
                batch.file_sha256,
                batch.declared_sha256,
                batch.file_bytes,
                batch.card_count,
                now,
                now,
                json.dumps(batch.warnings),
            ),
        )
    else:
        db.execute(
            """
            update imported_batches
            set status = 'processing', last_attempt_at = ?, error = null,
                warnings_json = ?
            where id = ?
            """,
            (now, json.dumps(batch.warnings), existing["id"]),
        )
    db.commit()


def _mark_imported(
    db: sqlite3.Connection, batch: LoadedBatch, response: dict[str, Any]
) -> None:
    db.execute(
        """
        update imported_batches
        set status = 'imported', imported_at = ?, request_id = ?,
            response_json = ?, error = null
        where batch_id = ?
        """,
        (
            utcnow(),
            str(response.get("request_id") or ""),
            json.dumps(response, sort_keys=True),
            batch.batch_id,
        ),
    )
    db.commit()


def _mark_error(
    db: sqlite3.Connection,
    *,
    path: Path,
    batch: LoadedBatch | None,
    error: str,
) -> None:
    now = utcnow()
    if batch is None:
        synthetic_id = f"invalid:{hashlib.sha256(str(path).encode()).hexdigest()[:24]}"
        db.execute(
            """
            insert into imported_batches (
                source_path, batch_id, scan_type, captured_at,
                file_sha256, declared_sha256, file_bytes, card_count,
                status, first_seen_at, last_attempt_at, warnings_json, error
            ) values (?, ?, 'unknown', '', '', '', 0, 0, 'rejected', ?, ?, '[]', ?)
            on conflict(source_path) do update set
                status = 'rejected', last_attempt_at = excluded.last_attempt_at,
                error = excluded.error
            """,
            (str(path.resolve()), synthetic_id, now, now, error),
        )
    else:
        existing = _existing_batch(db, batch)
        if existing is None:
            _mark_processing(db, batch)
        db.execute(
            """
            update imported_batches
            set status = 'error', last_attempt_at = ?, error = ?
            where batch_id = ?
            """,
            (now, error, batch.batch_id),
        )
    db.commit()


def import_file(
    *,
    path: Path,
    ledger_path: Path,
    endpoint: str,
    token: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    ensure_schema(ledger_path)
    batch: LoadedBatch | None = None
    with sqlite3.connect(ledger_path) as db:
        db.row_factory = sqlite3.Row
        try:
            batch = load_batch(path)
            existing = _existing_batch(db, batch)
            if existing is not None:
                if existing["file_sha256"] and existing["file_sha256"] != batch.file_sha256:
                    raise ImportValidationError(
                        "batch_id or source path already exists with different file bytes"
                    )
                if existing["status"] == "imported":
                    return {
                        "path": str(path),
                        "batch_id": batch.batch_id,
                        "status": "skipped_already_imported",
                        "governance_registration": register_governance_batch(batch),
                    }

            if dry_run:
                return {
                    "path": str(path),
                    "batch_id": batch.batch_id,
                    "scan_type": batch.scan_type,
                    "card_count": batch.card_count,
                    "status": "validated_dry_run",
                    "warnings": list(batch.warnings),
                }

            _mark_processing(db, batch)
            enriched_payload = dict(batch.payload)
            enriched_payload["gcs_import"] = {
                "source_path": str(path.resolve()),
                "file_sha256": batch.file_sha256,
                "declared_sha256": batch.declared_sha256,
                "imported_at": utcnow(),
                "warnings": list(batch.warnings),
            }
            response = post_payload(
                endpoint=endpoint,
                token=token,
                payload=enriched_payload,
            )
            _mark_imported(db, batch, response)
            governance_registration = register_governance_batch(batch)
            return {
                "path": str(path),
                "batch_id": batch.batch_id,
                "scan_type": batch.scan_type,
                "card_count": batch.card_count,
                "status": "imported",
                "request_id": response.get("request_id"),
                "records_written": response.get("records_written"),
                "new_signals": response.get("new_signals"),
                "invalid_records": response.get("invalid_records"),
                "warnings": list(batch.warnings),
                "governance_registration": governance_registration,
            }
        except Exception as exc:
            _mark_error(db, path=path, batch=batch, error=str(exc))
            return {
                "path": str(path),
                "batch_id": batch.batch_id if batch else None,
                "status": "error",
                "error": str(exc),
            }


def run_import(
    *,
    input_root: Path,
    ledger_path: Path,
    endpoint: str,
    token_file: Path,
    max_files: int = 0,
    dry_run: bool = False,
) -> dict[str, Any]:
    files = discover_json_files(input_root)
    if max_files > 0:
        files = files[:max_files]
    token = "" if dry_run else read_token(token_file)
    results = [
        import_file(
            path=path,
            ledger_path=ledger_path,
            endpoint=endpoint,
            token=token,
            dry_run=dry_run,
        )
        for path in files
    ]
    counts: dict[str, int] = {}
    for result in results:
        status = str(result.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return {
        "input_root": str(input_root),
        "ledger": str(ledger_path),
        "endpoint": endpoint,
        "files_discovered": len(files),
        "counts": counts,
        "results": results,
        "read_only_market_data": True,
        "trading_actions": False,
    }


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", type=Path, default=DEFAULT_INPUT_ROOT)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--token-file", type=Path, default=DEFAULT_TOKEN_FILE)
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--max-files", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--market-hours-only", action="store_true")
    parser.add_argument(
        "--market-start",
        type=parse_clock,
        default=parse_clock(DEFAULT_MARKET_START),
    )
    parser.add_argument(
        "--market-end",
        type=parse_clock,
        default=parse_clock(DEFAULT_MARKET_END),
    )
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    if args.market_end < args.market_start:
        raise SystemExit("market end must not be earlier than market start")
    if args.market_hours_only and not within_market_window(
        start=args.market_start,
        end=args.market_end,
    ):
        print(
            json.dumps(
                {
                    "status": "skipped_outside_market_window",
                    "timezone": "America/New_York",
                    "market_start": args.market_start.strftime("%H:%M"),
                    "market_end": args.market_end.strftime("%H:%M"),
                    "read_only_market_data": True,
                    "trading_actions": False,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    summary = run_import(
        input_root=args.input_root,
        ledger_path=args.ledger,
        endpoint=args.endpoint,
        token_file=args.token_file,
        max_files=args.max_files,
        dry_run=args.dry_run,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 1 if summary["counts"].get("error", 0) else 0


if __name__ == "__main__":
    raise SystemExit(main())
