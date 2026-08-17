from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from core.paper_executor.database import PaperExecutorDatabase
from core.paper_executor.training_dataset import build_dataset


def _seed_closed_sample(path: Path, *, position_id: str, opened: str, closed: str) -> None:
    PaperExecutorDatabase(path)
    raw = {
        "ticker": "NVDA", "direction": "BULLISH", "setup_type": "PUT FLOOR",
        "score": 82, "reward_risk": 2.1, "spot": 100,
        "evidence_snapshot": {"snapshot_id": "rth-1", "feed": "opra", "coverage": {"status": "sufficient"}},
        "autopilot": {
            "premarket_evidence_snapshot_id": "pm-1",
            "sentiment": {"status": "current", "score": 0.2, "events": 3},
        },
    }
    with sqlite3.connect(path) as db:
        db.execute("insert into signal_batches values(?,?,?,?,?,?)", ("b" + position_id, "test", opened, "PROCESSED", position_id, "{}"))
        db.execute("insert into signal_cards values(?,?,?,?,?,?,?,?,?,?,?)", (
            "c" + position_id, "b" + position_id, "NVDA", "flash_agentic", "bullish", "put floor", opened,
            "ELIGIBLE", None, json.dumps(raw), json.dumps(raw),
        ))
        db.execute("""insert into signal_episodes(
            id,episode_key,scanner_type,ticker,direction,setup,started_at,last_seen_at,ended_at,poll_count,latest_json
        ) values(?,?,?,?,?,?,?,?,?,?,?)""", (
            "e" + position_id, "key" + position_id, "flash_agentic", "NVDA", "bullish", "put floor",
            opened, opened, None, 1, json.dumps(raw),
        ))
        db.execute("insert into episode_updates values(?,?,?,?,?,?,?,?)", (
            "u" + position_id, "e" + position_id, "c" + position_id, opened, 100, 102, 99, json.dumps(raw),
        ))
        db.execute("insert into paper_positions values(?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            position_id, "e" + position_id, "NVDA", "bullish", "NVDA260821C00100000", 1, 1.0,
            opened, closed, 1.2, "option_take_profit", "CLOSED", "{}",
        ))
        db.execute("insert into paper_marks values(?,?,?,?,?,?,?)", (
            "m" + position_id, position_id, closed, 1.19, 1.21, 19.0, "{}",
        ))


def test_dataset_uses_entry_cutoff_and_blocks_tiny_corpus(tmp_path):
    db = tmp_path / "paper.sqlite"
    _seed_closed_sample(db, position_id="p1", opened="2026-08-17T14:00:00+00:00", closed="2026-08-17T14:30:00+00:00")
    manifest = build_dataset(db, tmp_path / "out", minimum_samples=2, minimum_market_dates=2)
    assert manifest["samples"] == 1
    assert manifest["training_status"] == "INSUFFICIENT_PROSPECTIVE_DATA"
    sample = json.loads((tmp_path / "out" / "prospective.jsonl").read_text())
    assert sample["feature_cutoff_at"] == "2026-08-17T14:00:00+00:00"
    assert sample["features"]["finbert_score"] == 0.2
    assert sample["labels"]["pnl_pct"] == 20.0
    assert manifest["train_samples"] == 0  # no pretend split with one date
    assert manifest["policies"]["model_may_authorize_live_orders"] is False
