from __future__ import annotations

import json
import os
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from scripts import storage_compaction


def test_sha256_file(tmp_path: Path):
    sample = tmp_path / "sample.txt"
    sample.write_text("hello world from cipher", encoding="utf-8")
    expected = storage_compaction.hashlib.sha256(b"hello world from cipher").hexdigest()
    assert storage_compaction.sha256_file(sample) == expected


def test_compress_and_verify_roundtrip(tmp_path: Path):
    source = tmp_path / "test.jsonl"
    content = "{\"event\": \"trade\", \"symbol\": \"AAPL\", \"price\": 220.5}\n" * 100
    source.write_text(content, encoding="utf-8")
    original_size = source.stat().st_size
    original_hash = storage_compaction.sha256_file(source)

    ledger_db = tmp_path / "test_ledger.sqlite"
    res = storage_compaction.compress_and_verify_file(
        source,
        use_zstd=True,
        remove_source=True,
        ledger_path=ledger_db,
        store_name="test_store",
        trading_day="2026-08-01",
    )

    assert res["status"] == "success"
    assert res["verified"] is True
    assert res["source_bytes"] == original_size
    assert res["source_sha256"] == original_hash
    assert not source.exists()

    zst_file = tmp_path / "test.jsonl.zst"
    assert zst_file.is_file()

    # Check ledger row
    with sqlite3.connect(ledger_db) as db:
        row = db.execute("SELECT * FROM compaction_records").fetchone()
        assert row is not None
        assert row[1] == "test_store"
        assert row[3] == "2026-08-01"
        assert row[4] == original_size
        assert row[5] == original_hash
        assert row[8] == "zstd"
        assert row[10] == 1


def test_compact_live_option_chains_skips_today(tmp_path: Path):
    chains_dir = tmp_path / "live_option_chains"
    chains_dir.mkdir()
    ledger_db = tmp_path / "test_ledger.sqlite"

    past_file = chains_dir / "2026-08-10_NVDA.jsonl"
    past_file.write_text("{\"contract\":\"NVDA\"}\n", encoding="utf-8")

    today_file = chains_dir / "2026-08-19_NVDA.jsonl"
    today_file.write_text("{\"contract\":\"NVDA\"}\n", encoding="utf-8")

    results = storage_compaction.compact_live_option_chains(
        chains_dir=chains_dir,
        today=date(2026, 8, 19),
        ledger_path=ledger_db,
    )

    assert len(results) == 1
    assert not past_file.exists()
    assert (chains_dir / "2026-08-10_NVDA.jsonl.zst").is_file()
    assert today_file.is_file()  # Today's active capture must be untouched


def test_build_coverage_catalog(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "test_file.sqlite").write_bytes(b"0" * 1024)
    sub = data_dir / "sub_store"
    sub.mkdir()
    (sub / "part.json").write_text("{}", encoding="utf-8")

    catalog_path = tmp_path / "coverage_catalog.json"
    catalog = storage_compaction.build_coverage_catalog(data_dir, catalog_path)

    assert catalog_path.is_file()
    assert "stores" in catalog
    assert "test_file.sqlite" in catalog["stores"]
    assert "sub_store" in catalog["stores"]
    assert catalog["summary"]["total_files"] == 2
