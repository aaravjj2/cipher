from core.paper_executor.database import PaperExecutorDatabase


def test_database_migrates_and_integrity_ok(tmp_path):
    db = PaperExecutorDatabase(tmp_path / "paper.sqlite")
    assert db.integrity_ok()
    assert db.rows("signal_batches") == []
