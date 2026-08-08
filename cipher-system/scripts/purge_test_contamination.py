#!/usr/bin/env python3
"""Remove pytest-temporary artifacts from the production research registry.

The architecture self-audit reports, at HIGH severity:

    Production registry contains 8 raw object(s) originating from pytest
    temporary paths.

The real count is 30, recorded between 2026-08-01 and 2026-08-03, all with
source URIs like `file:///tmp/pytest-of-aarav/pytest-61/test_imports_once_and_
deduplic0/uploaded/...`. They came from the browser-GCS importer suite writing
through the production governance hooks before
`tests/test_browser_gcs_importer.py`'s `disable_production_governance_hooks`
fixture existed. That fixture now sets CIPHER_GOVERNANCE_HOOKS=0 and holds --
verified by running the suite and confirming raw_objects stays at 1079 -- so this
is a one-off cleanup of historical rows, not a recurring sweep.

Why bother: the registry is the lineage substrate every dataset manifest and
promotion check reads. Rows whose source no longer exists, and never described
real market data, make "raw object count" meaningless as evidence.

Defaults to a dry run. Takes a timestamped backup before writing.

Usage:
  python3 scripts/purge_test_contamination.py            # report only
  python3 scripts/purge_test_contamination.py --apply
"""
from __future__ import annotations

import argparse
import re
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "data" / "governance" / "research_registry.sqlite"

# Anchored on the pytest tmpdir layout rather than a bare "/tmp": a legitimate
# ingest could stage through /tmp, but nothing real is ever sourced from
# `pytest-of-<user>/pytest-<n>/<test name>`.
CONTAMINATION = re.compile(r"/tmp/pytest-of-[^/]+/pytest-\d+/|pytest-of-[^/\"']+", re.IGNORECASE)


def find_contaminated(conn) -> list[tuple[str, str, str]]:
    rows = []
    for raw_id, uri, received, payload in conn.execute(
        "select raw_object_id, uri, received_at, payload_json from raw_objects"
    ):
        if CONTAMINATION.search(f"{uri or ''} {payload or ''}"):
            rows.append((raw_id, str(uri or "")[:100], str(received or "")[:19]))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="delete (default is a dry run)")
    args = ap.parse_args()

    if not REGISTRY.exists():
        print(f"registry not found: {REGISTRY}", file=sys.stderr)
        return 1

    with sqlite3.connect(f"file:{REGISTRY}?mode=ro", uri=True) as conn:
        contaminated = find_contaminated(conn)
        total = conn.execute("select count(*) from raw_objects").fetchone()[0]

    print(f"raw_objects total: {total}")
    print(f"pytest-sourced:    {len(contaminated)}")
    for raw_id, uri, received in contaminated[:10]:
        print(f"  {raw_id}  {received}  {uri}")
    if len(contaminated) > 10:
        print(f"  … and {len(contaminated) - 10} more")

    if not contaminated:
        print("\nnothing to purge")
        return 0
    if not args.apply:
        print("\ndry run — re-run with --apply to delete")
        return 0

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = REGISTRY.with_name(f"{REGISTRY.stem}.backup_{stamp}.sqlite")
    shutil.copy2(REGISTRY, backup)
    print(f"\nbackup: {backup.name}")

    ids = [row[0] for row in contaminated]
    with sqlite3.connect(REGISTRY) as conn:
        # Drop link rows first so no dataset is left pointing at a deleted object.
        links = conn.execute(
            "select count(*) from dataset_raw_objects where raw_object_id in "
            f"({','.join('?' * len(ids))})", ids
        ).fetchone()[0]
        conn.execute(
            f"delete from dataset_raw_objects where raw_object_id in ({','.join('?' * len(ids))})",
            ids,
        )
        conn.execute(
            f"delete from raw_objects where raw_object_id in ({','.join('?' * len(ids))})", ids
        )
        conn.commit()
        remaining = conn.execute("select count(*) from raw_objects").fetchone()[0]

    print(f"deleted {len(ids)} raw objects and {links} dataset links")
    print(f"raw_objects now: {remaining}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
