from datetime import datetime, timedelta, timezone

from core.paper_executor.database import PaperExecutorDatabase
from core.paper_executor.models import Quote


def test_contract_mark_tape_has_conservative_coverage_gate(tmp_path):
    db = PaperExecutorDatabase(tmp_path / "paper.sqlite")
    with db.connect() as conn:
        conn.execute("insert into signal_batches values(?,?,?,?,?,?)", ("b", "test", "now", "PROCESSED", "x", "{}"))
        conn.execute("insert into signal_episodes values(?,?,?,?,?,?,?,?,?,?,?)",
                     ("e", "key", "cipher", "NVDA", "bullish", "setup", "now", "now", None, 1, "{}"))
        conn.execute("insert into paper_positions(id,episode_id,ticker,direction,symbol,quantity,entry_price,opened_at,status,payload_json) values(?,?,?,?,?,?,?,?,?,?)",
                     ("p", "e", "NVDA", "bullish", "NVDA260821C00100000", 1, 1.0, "2026-08-17T14:00:00+00:00", "OPEN", "{}"))
    start = datetime(2026, 8, 17, 14, 0, tzinfo=timezone.utc)
    for offset in (0, 30, 60):
        moment = start + timedelta(seconds=offset)
        db.insert_contract_mark(position_id="p", episode_id="e", symbol="NVDA260821C00100000",
                                role="long_option", quote=Quote("NVDA260821C00100000", 1.0, 1.1, moment),
                                captured_at=moment, source="test")
    coverage = db.mark_coverage("p", expected_interval_seconds=30)
    assert coverage["status"] == "REPLAYABLE"
    assert coverage["interpolation"] is False and coverage["actual_fill_claim"] is False
