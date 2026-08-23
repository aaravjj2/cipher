#!/usr/bin/env python3
"""Storage compaction and coverage catalog generator for Cipher.

Follows the strict governance invariant:
1. Every compacted object receives a SHA-256 source and compressed checksum manifest.
2. An integrity test (decompress and verify byte-identical SHA-256) is executed and passed.
3. Source uncompressed files are removed only after verified integrity check passes.
4. Generates and updates runtime/data/coverage_catalog.json.
5. Only operates on completed historical days (strictly < today UTC).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
CATALOG_PATH = DATA_DIR / "coverage_catalog.json"
COMPACTION_LEDGER = DATA_DIR / "compaction_ledger.sqlite"


def utc_today() -> date:
    return datetime.now(timezone.utc).date()


def sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _get_ledger(ledger_path: Path = COMPACTION_LEDGER) -> sqlite3.Connection:
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(ledger_path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("""
        CREATE TABLE IF NOT EXISTS compaction_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_name TEXT NOT NULL,
            relative_path TEXT NOT NULL UNIQUE,
            trading_day TEXT,
            source_bytes INTEGER NOT NULL,
            source_sha256 TEXT NOT NULL,
            compressed_bytes INTEGER NOT NULL,
            compressed_sha256 TEXT NOT NULL,
            compression_format TEXT NOT NULL,
            verified_at TEXT NOT NULL,
            source_removed INTEGER NOT NULL CHECK (source_removed IN (0, 1))
        )
    """)
    return db


def compress_and_verify_file(
    source_path: Path,
    output_path: Path | None = None,
    *,
    use_zstd: bool = True,
    remove_source: bool = True,
    ledger_path: Path = COMPACTION_LEDGER,
    store_name: str = "general",
    trading_day: str | None = None,
) -> dict[str, Any]:
    """Compress a single file, verify decompress-integrity SHA-256 match, and optionally prune source."""
    source_path = source_path.resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"Source file not found: {source_path}")

    ext = ".zst" if use_zstd else ".gz"
    dest_path = (output_path or source_path.with_name(source_path.name + ext)).resolve()

    source_bytes = source_path.stat().st_size
    source_hash = sha256_file(source_path)

    # Compress to a temporary partial file
    partial_dest = dest_path.with_name(f"{dest_path.name}.partial-{os.getpid()}")
    try:
        if use_zstd:
            # Use zstd CLI
            cmd = ["zstd", "-q", "-f", "-3", str(source_path), "-o", str(partial_dest)]
            subprocess.run(cmd, capture_output=True, text=True, check=True)
        else:
            # Use gzip CLI
            with source_path.open("rb") as f_in, open(partial_dest, "wb") as f_out:
                subprocess.run(["gzip", "-c", "-9"], stdin=f_in, stdout=f_out, check=True)

        compressed_bytes = partial_dest.stat().st_size
        compressed_hash = sha256_file(partial_dest)

        # Integrity verification: test decompression
        if use_zstd:
            test_cmd = ["zstd", "-t", "-q", str(partial_dest)]
            subprocess.run(test_cmd, capture_output=True, check=True)
            # Full round-trip decompress hash check
            pipe = subprocess.Popen(["zstd", "-d", "-c", "-q", str(partial_dest)], stdout=subprocess.PIPE)
            digest = hashlib.sha256()
            assert pipe.stdout is not None
            while chunk := pipe.stdout.read(8 * 1024 * 1024):
                digest.update(chunk)
            pipe.wait()
            if pipe.returncode != 0:
                raise RuntimeError(f"zstd decompress pipe failed with code {pipe.returncode}")
            restored_hash = digest.hexdigest()
        else:
            # gzip decompress test
            test_cmd = ["gzip", "-t", str(partial_dest)]
            subprocess.run(test_cmd, capture_output=True, check=True)
            pipe = subprocess.Popen(["gzip", "-d", "-c", str(partial_dest)], stdout=subprocess.PIPE)
            digest = hashlib.sha256()
            assert pipe.stdout is not None
            while chunk := pipe.stdout.read(8 * 1024 * 1024):
                digest.update(chunk)
            pipe.wait()
            if pipe.returncode != 0:
                raise RuntimeError(f"gzip decompress pipe failed with code {pipe.returncode}")
            restored_hash = digest.hexdigest()

        if restored_hash != source_hash:
            raise ValueError(f"Integrity check failed! Source {source_hash} != Restored {restored_hash}")

        # Atomic rename partial -> dest
        os.replace(partial_dest, dest_path)

        verified_time = datetime.now(timezone.utc).isoformat()
        rel_path = str(source_path.relative_to(ROOT)) if source_path.is_relative_to(ROOT) else str(source_path)

        # Record in ledger
        with _get_ledger(ledger_path) as db:
            db.execute("""
                INSERT OR REPLACE INTO compaction_records (
                    store_name, relative_path, trading_day, source_bytes, source_sha256,
                    compressed_bytes, compressed_sha256, compression_format, verified_at, source_removed
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                store_name, rel_path, trading_day, source_bytes, source_hash,
                compressed_bytes, compressed_hash, "zstd" if use_zstd else "gzip",
                verified_time, 1 if remove_source else 0
            ))

        if remove_source and source_path.exists() and source_path != dest_path:
            source_path.unlink()

        return {
            "status": "success",
            "source_path": str(source_path),
            "dest_path": str(dest_path),
            "source_bytes": source_bytes,
            "compressed_bytes": compressed_bytes,
            "compression_ratio": round(source_bytes / max(compressed_bytes, 1), 2),
            "source_sha256": source_hash,
            "compressed_sha256": compressed_hash,
            "verified": True,
        }
    except Exception:
        if partial_dest.exists():
            partial_dest.unlink(missing_ok=True)
        raise


def compact_live_option_chains(
    chains_dir: Path = DATA_DIR / "live_option_chains",
    *,
    today: date | None = None,
    ledger_path: Path = COMPACTION_LEDGER,
) -> list[dict[str, Any]]:
    """Compact closed daily live option chain JSONL files from past days (< today)."""
    current_day = today or utc_today()
    if not chains_dir.is_dir():
        return []

    results = []
    # Pattern: YYYY-MM-DD_TICKER.jsonl
    pattern = re.compile(r"^(\d{4}-\d{2}-\d{2})_([A-Z0-9.\-]+)\.jsonl$")
    for item in sorted(chains_dir.iterdir()):
        if not item.is_file():
            continue
        match = pattern.match(item.name)
        if not match:
            continue
        day_str, ticker = match.groups()
        file_day = date.fromisoformat(day_str)
        if file_day >= current_day:
            # Skip today or future
            continue

        res = compress_and_verify_file(
            item,
            use_zstd=True,
            remove_source=True,
            ledger_path=ledger_path,
            store_name="live_option_chains",
            trading_day=day_str,
        )
        results.append(res)

    return results


def compact_tradier_stream_events(
    events_dir: Path = DATA_DIR / "tradier_stream_events",
    *,
    today: date | None = None,
    ledger_path: Path = COMPACTION_LEDGER,
) -> list[dict[str, Any]]:
    """Compact completed daily event directories in tradier_stream_events/ (< today)."""
    current_day = today or utc_today()
    if not events_dir.is_dir():
        return []

    results = []
    day_re = re.compile(r"^(\d{4}-\d{2}-\d{2})$")
    for day_dir in sorted(events_dir.iterdir()):
        if not day_dir.is_dir():
            continue
        match = day_re.match(day_dir.name)
        if not match:
            continue
        day_str = match.group(1)
        file_day = date.fromisoformat(day_str)
        if file_day >= current_day:
            continue

        for jsonl_file in sorted(day_dir.glob("*.jsonl")):
            if not jsonl_file.is_file():
                continue
            res = compress_and_verify_file(
                jsonl_file,
                use_zstd=True,
                remove_source=True,
                ledger_path=ledger_path,
                store_name="tradier_stream_events",
                trading_day=day_str,
            )
            results.append(res)

    return results


def build_coverage_catalog(
    data_dir: Path = DATA_DIR,
    output_file: Path = CATALOG_PATH,
) -> dict[str, Any]:
    """Generate a comprehensive coverage catalog for all data stores in runtime/data."""
    catalog = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_root": str(data_dir.resolve()),
        "stores": {},
        "summary": {
            "total_bytes": 0,
            "total_files": 0,
        },
    }

    total_bytes = 0
    total_files = 0

    if data_dir.is_dir():
        for item in sorted(data_dir.iterdir()):
            name = item.name
            if name.startswith("."):
                continue

            if item.is_file():
                size = item.stat().st_size
                total_bytes += size
                total_files += 1
                catalog["stores"][name] = {
                    "type": "file",
                    "bytes": size,
                    "mtime": datetime.fromtimestamp(item.stat().st_mtime, tz=timezone.utc).isoformat(),
                }
            elif item.is_dir():
                dir_size = sum(f.stat().st_size for f in item.rglob("*") if f.is_file())
                dir_files = sum(1 for f in item.rglob("*") if f.is_file())
                total_bytes += dir_size
                total_files += dir_files
                catalog["stores"][name] = {
                    "type": "directory",
                    "bytes": dir_size,
                    "file_count": dir_files,
                }

    catalog["summary"]["total_bytes"] = total_bytes
    catalog["summary"]["total_files"] = total_files
    catalog["summary"]["total_gb"] = round(total_bytes / (1024**3), 2)

    # Add disk stats
    try:
        statvfs = os.statvfs(str(data_dir if data_dir.exists() else "/"))
        free_bytes = statvfs.f_bavail * statvfs.f_frsize
        total_disk_bytes = statvfs.f_blocks * statvfs.f_frsize
        used_disk_bytes = total_disk_bytes - free_bytes
        catalog["disk"] = {
            "total_gb": round(total_disk_bytes / (1024**3), 2),
            "used_gb": round(used_disk_bytes / (1024**3), 2),
            "free_gb": round(free_bytes / (1024**3), 2),
            "used_pct": round((used_disk_bytes / total_disk_bytes) * 100, 1),
        }
    except Exception:
        pass

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(catalog, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return catalog


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog-only", action="store_true", help="Generate catalog without compacting")
    parser.add_argument("--chains", action="store_true", help="Compact live option chains")
    parser.add_argument("--events", action="store_true", help="Compact tradier stream events")
    parser.add_argument("--all", action="store_true", help="Run full compaction on all eligible stores")
    args = parser.parse_args()

    print(f"[{datetime.now(timezone.utc).isoformat()}] Starting storage compaction...")

    if not args.catalog_only:
        if args.all or args.chains:
            print("Compacting completed live option chains...")
            chain_res = compact_live_option_chains()
            print(f"Compacted {len(chain_res)} chain files.")
            for r in chain_res:
                print(f"  {Path(r['source_path']).name}: {r['source_bytes']} -> {r['compressed_bytes']} bytes ({r['compression_ratio']}x)")

        if args.all or args.events:
            print("Compacting completed Tradier stream event logs...")
            event_res = compact_tradier_stream_events()
            print(f"Compacted {len(event_res)} event log files.")
            for r in event_res:
                print(f"  {Path(r['source_path']).name}: {r['source_bytes']} -> {r['compressed_bytes']} bytes ({r['compression_ratio']}x)")

    # Clean up stale debug logs if any
    debug_log = ROOT / "pytestdebug.log"
    if debug_log.is_file():
        print(f"Removing stale pytest debug log: {debug_log}")
        debug_log.unlink()

    catalog = build_coverage_catalog()
    print(f"Coverage catalog updated at: {CATALOG_PATH}")
    if "disk" in catalog:
        print(f"Disk Status: {catalog['disk']['free_gb']} GB free ({catalog['disk']['used_pct']}% used of {catalog['disk']['total_gb']} GB)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
