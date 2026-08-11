import csv
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))

import flash_gex_pairs as pairs  # noqa: E402
import weight_lab  # noqa: E402


def _browser(path: Path, rows: list[dict]) -> None:
    fields = [
        "card_timestamp", "client_timestamp", "received_at", "ticker", "score",
        "rank", "spot", "geometry_valid", "direction", "setup_type", "request_id",
    ]
    path.parent.mkdir(parents=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _db(path: Path, snapshots: list[tuple]) -> None:
    with sqlite3.connect(path) as db:
        db.execute(
            "create table gex_snapshots (id integer, ticker text, captured_at text, spot real, raw_json_path text, feed text)"
        )
        db.executemany("insert into gex_snapshots values (?,?,?,?,?,?)", snapshots)


def _features() -> dict:
    return {name: float(i + 1) for i, name in enumerate(weight_lab.FLASH_FEATURE_NAMES)}


def test_canonical_observation_is_first_valid_scored_row(tmp_path):
    browser = tmp_path / "browser"
    _browser(browser / "flash-observations-v2-2026-08-01.csv", [
        {"card_timestamp": "2026-08-01T14:00:00Z", "ticker": "SPY", "score": "bad", "spot": 100, "geometry_valid": "true"},
        {"card_timestamp": "2026-08-01T14:01:00Z", "ticker": "SPY", "score": 70, "spot": 100, "geometry_valid": "true"},
        {"card_timestamp": "2026-08-01T14:02:00Z", "ticker": "SPY", "score": 90, "spot": 100, "geometry_valid": "true"},
    ])
    rows, report = pairs.load_canonical_observations(browser)
    assert len(rows) == 1
    assert rows[0]["score"] == 70
    assert report["duplicate_observations"] == 1


def test_asof_join_never_uses_future_snapshot(tmp_path, monkeypatch):
    browser = tmp_path / "browser"
    raw = tmp_path / "snapshot.json"
    raw.write_text("{}")
    _browser(browser / "flash-observations-v2-2026-08-01.csv", [
        {"card_timestamp": "2026-08-01T14:00:00Z", "ticker": "SPY", "score": 80, "spot": 100, "geometry_valid": "true"},
    ])
    db = tmp_path / "gex.sqlite"
    _db(db, [
        (1, "SPY", "2026-08-01T13:55:00Z", 100, str(raw), "opra"),
        (2, "SPY", "2026-08-01T14:01:00Z", 100, str(raw), "opra"),
    ])
    monkeypatch.setattr(pairs, "_snapshot_features", lambda snap, as_of: (_features(), {"snapshot_spot": snap["spot"]}))
    records, _ = pairs.build_pairs(browser_dir=browser, db_path=db)
    assert records[0]["provenance"]["snapshot_id"] == 1


def test_age_and_spot_drift_fail_closed(tmp_path, monkeypatch):
    browser = tmp_path / "browser"
    raw = tmp_path / "snapshot.json"
    raw.write_text("{}")
    _browser(browser / "flash-observations-v2-2026-08-01.csv", [
        {"card_timestamp": "2026-08-01T14:00:00Z", "ticker": "OLD", "score": 80, "spot": 100, "geometry_valid": "true"},
        {"card_timestamp": "2026-08-01T14:00:00Z", "ticker": "DRIFT", "score": 80, "spot": 100, "geometry_valid": "true"},
    ])
    db = tmp_path / "gex.sqlite"
    _db(db, [
        (1, "OLD", "2026-08-01T13:39:59Z", 100, str(raw), "opra"),
        (2, "DRIFT", "2026-08-01T13:59:00Z", 99.49, str(raw), "opra"),
    ])
    monkeypatch.setattr(pairs, "_snapshot_features", lambda snap, as_of: (_features(), {}))
    records, report = pairs.build_pairs(browser_dir=browser, db_path=db)
    assert records == []
    assert report["groups_without_pairs"] == 2
    assert report["attempt_rejections"] == {
        "snapshot_too_old": 1, "groups_without_admissible_pair": 2, "spot_drift": 1
    }


def test_paired_loader_preserves_days_and_rejects_missing_features(tmp_path, monkeypatch):
    paired = tmp_path / "paired"
    paired.mkdir()
    good = {"pair_version": 1, "session_date": "2026-08-01", "ticker": "SPY", "score": 80, "features": _features()}
    later = {**good, "session_date": "2026-08-02", "score": 81}
    bad = {**good, "ticker": "QQQ", "features": {}}
    (paired / "pairs.jsonl").write_text("\n".join(map(json.dumps, [good, later, bad])))
    monkeypatch.setattr(weight_lab, "PAIRED_DIR", paired)
    monkeypatch.setattr(weight_lab, "ensure_dirs", lambda: None)
    loaded = weight_lab.load_paired_flash_labels()
    assert [(row["session_date"], row["ticker"]) for row in loaded] == [
        ("2026-08-01", "SPY"), ("2026-08-02", "SPY")
    ]
    assert set(loaded[0]["feat"]) == set(weight_lab.PAIRED_FLASH_FEATURE_NAMES)
    assert "runway_clarity_norm" not in loaded[0]["feat"]
    assert "dist_to_event" not in loaded[0]["feat"]
