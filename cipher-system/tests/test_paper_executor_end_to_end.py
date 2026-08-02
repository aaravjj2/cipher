from datetime import datetime, timezone

from core.paper_executor.config import ExecutorConfig, ScannerConfig
from core.paper_executor.service import PaperExecutorApp
from test_paper_executor_runtime import MockMarketData


def test_end_to_end_ingests_valid_card_and_suppresses_duplicate(tmp_path):
    cfg = ExecutorConfig(
        runtime_root=tmp_path,
        database_path=tmp_path / "paper.sqlite",
        scanner=ScannerConfig(maximum_signal_age_seconds=999999999),
    )
    app = PaperExecutorApp(cfg)
    md = MockMarketData()
    app.runtime.market_data = md
    app.runtime.quote_manager.market_data = md
    payload = {
        "batch_id": "b1",
        "source": "access_obsidian_browser",
        "cards": [{
            "ticker": "NVDA",
            "scanner_type": "flash",
            "direction": "bullish",
            "setup": "floor bounce",
            "captured_timestamp": datetime(2026, 7, 28, 14, 30, tzinfo=timezone.utc).isoformat(),
            "spot": 100,
            "target": 101,
            "invalidation": 99,
        }],
    }
    first = app.ingest(payload)
    app.runtime.drain_for_tests()
    second = app.ingest(payload)
    assert first["accepted"] is True
    assert second["duplicate_batch"] is True
    assert len(app.db.rows("signal_episodes")) == 1
