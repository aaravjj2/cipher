from core.paper_executor.database import PaperExecutorDatabase
from core.paper_executor.vm_forwarder import VmForwarder


def test_forwarder_enqueues_to_sqlite_and_disk(tmp_path):
    db = PaperExecutorDatabase(tmp_path / "paper.sqlite")
    db.insert_batch({"batch_id": "b1", "source": "test", "received_at": "now", "raw": {}}, "abc")
    forwarder = VmForwarder(db, tmp_path / "queue", None)
    item_id = forwarder.enqueue("b1", {"batch_id": "b1"})
    assert (tmp_path / "queue" / f"{item_id}.json").exists()
    assert db.rows("forward_queue")[0]["status"] == "PENDING"


def test_forwarder_outage_marks_retryable_without_losing_payload(tmp_path):
    db = PaperExecutorDatabase(tmp_path / "paper.sqlite")
    db.insert_batch({"batch_id": "b1", "source": "test", "received_at": "now", "raw": {}}, "abc")
    forwarder = VmForwarder(db, tmp_path / "queue", "http://127.0.0.1:9/unreachable")
    item_id = forwarder.enqueue("b1", {"batch_id": "b1", "cards": []})

    assert forwarder.attempt(item_id) is False

    row = db.rows("forward_queue")[0]
    assert row["status"] == "FAILED_RETRYABLE"
    assert row["attempts"] == 1
    assert row["next_attempt_at"]
    assert row["last_error"]
    assert (tmp_path / "queue" / f"{item_id}.json").exists()
