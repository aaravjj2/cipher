#!/usr/bin/env python3
"""Merge the legacy Cipher runtime state into one canonical local product.

This script performs an idempotent, local-only migration:

- canonical source remains the Git checkout containing this script;
- persistent data/logs/config move to a neutral runtime directory;
- legacy and canonical source paths become compatibility symlinks;
- the two research registries are merged without importing known pytest
  contamination;
- no broker, paper, or live-execution behavior is introduced.

Run without ``--execute`` to print a preflight plan. All Cipher processes must
be stopped before execution.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


CANONICAL_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OLD_ROOT = Path("/home/aarav/Aarav/cipher/cipher-system")
DEFAULT_RUNTIME_ROOT = Path("/home/aarav/Aarav/cipher/runtime")
PROCESS_MARKERS = (
    "/core/app.py",
    "/app/server.mjs",
    "/run_safe_scheduled_jobs.py",
    "/run_build_healing_loop.py",
)
KNOWN_TEST_RAW_ID = "raw_7bd7d51f57d93436f54a1375"


@dataclass(frozen=True)
class MigrationPaths:
    old_root: Path
    canonical_root: Path
    runtime_root: Path
    backup_root: Path

    @property
    def runtime_data(self) -> Path:
        return self.runtime_root / "data"

    @property
    def runtime_logs(self) -> Path:
        return self.runtime_root / "logs"

    @property
    def runtime_env(self) -> Path:
        return self.runtime_root / "config" / "cipher.env"

    @property
    def runtime_token(self) -> Path:
        return self.runtime_root / "config" / "scanner-ingest-token"


def utcstamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def active_cipher_processes() -> list[dict[str, Any]]:
    active: list[dict[str, Any]] = []
    proc = Path("/proc")
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            command = (entry / "cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", errors="ignore")
        except OSError:
            continue
        if command and any(marker in command for marker in PROCESS_MARKERS):
            active.append({"pid": int(entry.name), "command": command.strip()})
    return sorted(active, key=lambda item: item["pid"])


def env_entries(path: Path) -> tuple[list[str], dict[str, str]]:
    comments: list[str] = []
    values: dict[str, str] = {}
    if not path.is_file():
        return comments, values
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            if raw not in comments:
                comments.append(raw)
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if key:
            values[key] = value.strip()
    return comments, values


def merge_env(paths: Iterable[Path], destination: Path) -> dict[str, Any]:
    comments: list[str] = [
        "# Unified Cipher runtime environment.",
        "# Generated locally by scripts/unify_cipher_runtime.py; never commit this file.",
    ]
    values: dict[str, str] = {}
    sources: list[str] = []
    conflicts: list[str] = []
    for path in paths:
        if not path.is_file():
            continue
        sources.append(str(path))
        source_comments, source_values = env_entries(path)
        for comment in source_comments:
            if comment and comment not in comments:
                comments.append(comment)
        for key, value in source_values.items():
            if key in values and values[key] != value:
                conflicts.append(key)
            values[key] = value
    destination.parent.mkdir(parents=True, exist_ok=True)
    lines = comments + [""] + [f"{key}={values[key]}" for key in sorted(values)]
    temporary = destination.with_suffix(".tmp")
    temporary.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
    os.replace(temporary, destination)
    return {
        "sources": sources,
        "keys": sorted(values),
        "conflicting_keys_resolved_by_later_source": sorted(set(conflicts)),
        "destination": str(destination),
    }


def replace_with_symlink(path: Path, target: Path, *, backup_dir: Path) -> dict[str, Any]:
    target = target.resolve()
    if path.is_symlink():
        observed = path.resolve()
        if observed == target:
            return {"path": str(path), "target": str(target), "status": "already_linked"}
        path.unlink()
    elif path.exists():
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / path.name
        if backup.exists():
            backup = backup_dir / f"{path.name}_{utcstamp()}"
        os.replace(path, backup)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(target, target_is_directory=target.is_dir())
    return {"path": str(path), "target": str(target), "status": "linked"}


def move_directory(source: Path, destination: Path) -> dict[str, Any]:
    if source.is_symlink() and source.resolve() == destination.resolve():
        return {"source": str(source), "destination": str(destination), "status": "already_moved"}
    if destination.exists():
        return {"source": str(source), "destination": str(destination), "status": "destination_exists"}
    if not source.is_dir():
        destination.mkdir(parents=True, exist_ok=True)
        return {"source": str(source), "destination": str(destination), "status": "created_empty"}
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.replace(source, destination)
    return {"source": str(source), "destination": str(destination), "status": "moved"}


def table_columns(db: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in db.execute(f'pragma table_info("{table}")')]


def merge_registry(destination: Path, source: Path) -> dict[str, Any]:
    if not source.is_file():
        return {"status": "source_missing", "source": str(source), "destination": str(destination)}
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.is_file():
        shutil.copy2(source, destination)
        return {"status": "copied", "source": str(source), "destination": str(destination)}

    inserted: dict[str, int] = {}
    skipped: dict[str, int] = {}
    with sqlite3.connect(destination, timeout=120) as dst, sqlite3.connect(f"file:{source}?mode=ro", uri=True) as src:
        src.row_factory = sqlite3.Row
        dst.execute("pragma foreign_keys=off")
        source_tables = [
            str(row[0])
            for row in src.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'")
        ]
        destination_tables = {
            str(row[0])
            for row in dst.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'")
        }
        for table in sorted(source_tables):
            if table not in destination_tables:
                schema = src.execute(
                    "select sql from sqlite_master where type='table' and name=?",
                    (table,),
                ).fetchone()
                if schema and schema[0]:
                    dst.execute(str(schema[0]))
                    destination_tables.add(table)
            if table not in destination_tables:
                continue
            source_columns = table_columns(src, table)
            destination_columns = table_columns(dst, table)
            columns = [column for column in source_columns if column in destination_columns]
            if not columns:
                continue
            placeholders = ",".join("?" for _ in columns)
            column_sql = ",".join(f'"{column}"' for column in columns)
            before = dst.total_changes
            table_skips = 0
            for row in src.execute(f'select {column_sql} from "{table}"'):
                record = {column: row[column] for column in columns}
                text = " ".join(str(value) for value in record.values())
                if table == "raw_objects" and (KNOWN_TEST_RAW_ID in text or "/tmp/pytest-of-" in text):
                    table_skips += 1
                    continue
                if table == "audit_events" and (KNOWN_TEST_RAW_ID in text or "/tmp/pytest-of-" in text):
                    table_skips += 1
                    continue
                dst.execute(
                    f'insert or ignore into "{table}" ({column_sql}) values ({placeholders})',
                    tuple(record[column] for column in columns),
                )
            inserted[table] = dst.total_changes - before
            skipped[table] = table_skips
        dst.commit()
        integrity = dst.execute("pragma integrity_check").fetchone()
        if not integrity or integrity[0] != "ok":
            raise RuntimeError(f"merged registry integrity check failed: {integrity}")
    return {
        "status": "merged",
        "source": str(source),
        "destination": str(destination),
        "inserted": inserted,
        "skipped_known_test_contamination": skipped,
        "destination_sha256": sha256(destination),
    }


def merge_tree(source: Path, destination: Path, *, registry_relative: str | None = None) -> dict[str, Any]:
    copied = same = conflicts = 0
    conflict_paths: list[str] = []
    registry_result: dict[str, Any] | None = None
    if not source.is_dir():
        return {"status": "source_missing", "source": str(source), "destination": str(destination)}
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        target = destination / relative
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        if registry_relative and relative.as_posix() == registry_relative:
            registry_result = merge_registry(target, path)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.copy2(path, target)
            copied += 1
            continue
        if target.is_file() and path.stat().st_size == target.stat().st_size and sha256(path) == sha256(target):
            same += 1
            continue
        conflicts += 1
        conflict_paths.append(relative.as_posix())
    return {
        "status": "merged",
        "source": str(source),
        "destination": str(destination),
        "copied": copied,
        "identical_existing": same,
        "unresolved_conflicts": conflicts,
        "conflict_paths": conflict_paths[:100],
        "registry": registry_result,
    }


def directory_summary(path: Path) -> dict[str, Any]:
    files = 0
    bytes_total = 0
    if path.is_dir():
        for item in path.rglob("*"):
            if item.is_file():
                files += 1
                try:
                    bytes_total += item.stat().st_size
                except OSError:
                    pass
    return {"path": str(path), "files": files, "bytes": bytes_total}


def build_paths(args: argparse.Namespace) -> MigrationPaths:
    old_root = args.old_root.resolve()
    canonical_root = args.canonical_root.resolve()
    runtime_root = args.runtime_root.resolve()
    backup_root = runtime_root / "backups" / f"pre_unification_{args.backup_stamp}"
    return MigrationPaths(old_root, canonical_root, runtime_root, backup_root)


def preflight(paths: MigrationPaths) -> dict[str, Any]:
    return {
        "old_root": str(paths.old_root),
        "canonical_root": str(paths.canonical_root),
        "runtime_root": str(paths.runtime_root),
        "backup_root": str(paths.backup_root),
        "old_data": directory_summary(paths.old_root / "data"),
        "canonical_data": directory_summary(paths.canonical_root / "data"),
        "old_logs": directory_summary(paths.old_root / "logs"),
        "canonical_logs": directory_summary(paths.canonical_root / "logs"),
        "active_cipher_processes": active_cipher_processes(),
        "execution_authority": False,
    }


def execute(paths: MigrationPaths) -> dict[str, Any]:
    active = active_cipher_processes()
    if active:
        raise RuntimeError(f"stop Cipher processes before migration: {[item['pid'] for item in active]}")

    paths.runtime_root.mkdir(parents=True, exist_ok=True)
    paths.backup_root.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "schema_version": 1,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "paths": {
            "old_root": str(paths.old_root),
            "canonical_root": str(paths.canonical_root),
            "runtime_root": str(paths.runtime_root),
            "backup_root": str(paths.backup_root),
        },
        "execution_authority": False,
        "steps": {},
    }

    old_data = paths.old_root / "data"
    canonical_data = paths.canonical_root / "data"
    old_logs = paths.old_root / "logs"
    canonical_logs = paths.canonical_root / "logs"

    report["steps"]["move_old_data"] = move_directory(old_data, paths.runtime_data)
    canonical_data_backup = paths.backup_root / "canonical_data_original"
    report["steps"]["move_canonical_data"] = move_directory(canonical_data, canonical_data_backup)
    report["steps"]["merge_canonical_data"] = merge_tree(
        canonical_data_backup,
        paths.runtime_data,
        registry_relative="governance/research_registry.sqlite",
    )

    report["steps"]["move_old_logs"] = move_directory(old_logs, paths.runtime_logs)
    canonical_logs_backup = paths.backup_root / "canonical_logs_original"
    report["steps"]["move_canonical_logs"] = move_directory(canonical_logs, canonical_logs_backup)
    report["steps"]["merge_canonical_logs"] = merge_tree(canonical_logs_backup, paths.runtime_logs)

    env_sources = (
        paths.old_root / ".env",
        paths.old_root / "app" / ".env",
        paths.canonical_root / ".env",
        paths.canonical_root / "app" / ".env",
    )
    report["steps"]["merge_environment"] = merge_env(env_sources, paths.runtime_env)

    old_token = paths.old_root / "app" / ".scanner-ingest-token"
    canonical_token = paths.canonical_root / "app" / ".scanner-ingest-token"
    token_source = old_token if old_token.is_file() else canonical_token
    if token_source.is_file():
        paths.runtime_token.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(token_source, paths.runtime_token)
        os.chmod(paths.runtime_token, stat.S_IRUSR | stat.S_IWUSR)
        token_status = "copied"
    else:
        token_status = "not_present"
    report["steps"]["scanner_token"] = {
        "status": token_status,
        "destination": str(paths.runtime_token),
    }

    compatibility_backup = paths.backup_root / "replaced_runtime_paths"
    links: list[dict[str, Any]] = []
    for path in (old_data, canonical_data):
        links.append(replace_with_symlink(path, paths.runtime_data, backup_dir=compatibility_backup))
    for path in (old_logs, canonical_logs):
        links.append(replace_with_symlink(path, paths.runtime_logs, backup_dir=compatibility_backup))
    for path in (
        paths.old_root / ".env",
        paths.old_root / "app" / ".env",
        paths.canonical_root / ".env",
        paths.canonical_root / "app" / ".env",
    ):
        links.append(replace_with_symlink(path, paths.runtime_env, backup_dir=compatibility_backup))
    if paths.runtime_token.is_file():
        for path in (old_token, canonical_token):
            links.append(replace_with_symlink(path, paths.runtime_token, backup_dir=compatibility_backup))
    report["steps"]["compatibility_links"] = links

    report["completed_at"] = datetime.now(timezone.utc).isoformat()
    report["runtime_data"] = directory_summary(paths.runtime_data)
    report["runtime_logs"] = directory_summary(paths.runtime_logs)
    report_path = paths.runtime_root / "governance" / "unification_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = report_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, report_path)
    report["report_path"] = str(report_path)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--old-root", type=Path, default=DEFAULT_OLD_ROOT)
    parser.add_argument("--canonical-root", type=Path, default=CANONICAL_ROOT)
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME_ROOT)
    parser.add_argument("--backup-stamp", default=utcstamp())
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    paths = build_paths(args)
    if not args.execute:
        print(json.dumps(preflight(paths), indent=2, sort_keys=True))
        return 0
    try:
        result = execute(paths)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": f"{type(exc).__name__}: {exc}"}, indent=2))
        return 1
    print(json.dumps({
        "status": "completed",
        "report_path": result["report_path"],
        "runtime_data": result["runtime_data"],
        "runtime_logs": result["runtime_logs"],
        "execution_authority": False,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
