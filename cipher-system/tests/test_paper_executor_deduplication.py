from datetime import datetime, timezone

from core.paper_executor.config import ExecutorConfig
from core.paper_executor.database import PaperExecutorDatabase
from core.paper_executor.episode_tracker import EpisodeTracker
from core.paper_executor.models import Direction, SignalCard


def test_episode_repeated_poll_does_not_create_new_episode(tmp_path):
    db = PaperExecutorDatabase(tmp_path / "paper.sqlite")
    now = datetime.now(timezone.utc)
    db.insert_batch({"batch_id": "b1", "source": "test", "received_at": now.isoformat(), "raw": {}}, "abc")
    db.insert_card("card1", "b1", {}, "VALIDATED", None, {"ticker": "AAPL"})
    db.insert_card("card2", "b1", {}, "VALIDATED", None, {"ticker": "AAPL"})
    tracker = EpisodeTracker(db, 10)
    card = SignalCard("AAPL", "flash_agentic", Direction.BULLISH, "ceiling rejection", now, 100, 101, 99, {})
    first, dup1 = tracker.record(card, "card1")
    second, dup2 = tracker.record(card, "card2")
    assert first == second
    assert dup1 is False
    assert dup2 is True
