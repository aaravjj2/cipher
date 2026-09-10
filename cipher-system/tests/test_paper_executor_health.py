from datetime import datetime, timedelta, timezone

from core.paper_executor.config import ExecutorConfig, ScannerConfig
from core.paper_executor.service import PaperExecutorApp, make_handler
from test_paper_executor_runtime import MockMarketData, open_shadow_position, runtime


def test_runtime_health_exposes_readiness_blocker_and_complete_counts(tmp_path):
    md = MockMarketData()
    rt = runtime(tmp_path, md, vm_enabled=False)
    open_shadow_position(rt, md)
    health = rt.health()

    assert health["market_data_readiness"]["market_data_ready"] is True
    assert health["observability"]["entry_blocked_reason"] is None
    assert health["observability"]["counts"]["contract_candidates"] >= 1
    assert health["observability"]["counts"]["paper_orders"] == 1
    assert health["observability"]["counts"]["open_shadow_positions"] == 1
    assert health["execution"]["portfolio_kind"] == "cipher_local_paper"
    assert health["execution"]["external_order_capability"] is False
    assert health["observability"]["portfolio"]["external_order_capability"] is False
    assert health["observability"]["portfolio"]["cash_balance"] == 4_889.0


def test_runtime_health_reports_recovery_integrity_without_rescanning_database(tmp_path, monkeypatch):
    rt = runtime(tmp_path, MockMarketData(), vm_enabled=False)
    monkeypatch.setattr(rt.db, "integrity_ok", lambda: (_ for _ in ()).throw(AssertionError("unexpected full scan")))
    assert rt.health()["observability"]["database_integrity_ok"] is True


def test_market_data_failure_is_a_recorded_skip_not_a_worker_crash(tmp_path):
    md = MockMarketData()
    md.expirations = lambda _ticker: (_ for _ in ()).throw(RuntimeError("HTTP 401 authentication failed"))
    rt = runtime(tmp_path, md, vm_enabled=False)
    response = rt.ingest_payload({
        "batch_id": "data-failure",
        "source": "access_obsidian_browser",
        "cards": [{
            "ticker": "NVDA", "scanner_type": "flash", "direction": "bullish",
            "setup": "floor bounce", "captured_timestamp": md.now.isoformat(),
            "spot": 100, "target": 101, "invalidation": 99,
        }],
    })
    rt.drain_for_tests()
    health = rt.health()

    assert response["queued"] is True
    assert health["observability"]["counts"]["entry_blocks"] == 1
    assert health["observability"]["last_entry_block"]["reason"] == "SKIPPED_MARKET_DATA_UNAVAILABLE"
    assert health["observability"]["counts"]["paper_orders"] == 0
    assert health["observability"]["open_shadow_positions"] == 0
    assert not [row for row in rt.db.rows("system_events") if row["event_type"] == "WORKER_ERROR"]


def test_local_probe_returns_only_read_only_aggregate_metadata(tmp_path):
    cfg = ExecutorConfig(
        runtime_root=tmp_path, database_path=tmp_path / "paper.sqlite",
        scanner=ScannerConfig(maximum_signal_age_seconds=999999999),
    )
    app = PaperExecutorApp(cfg)
    app.runtime.market_data = MockMarketData()
    result = app.market_data_probe("nvda")
    assert result == {
        "ok": True, "ticker": "NVDA", "feed": "opra", "expirations": 1,
        "contracts": 2, "paper_only": True, "live_execution_capability": False,
    }


def test_disconnected_health_client_does_not_raise_or_log_a_traceback(tmp_path):
    app = PaperExecutorApp(ExecutorConfig(
        runtime_root=tmp_path, database_path=tmp_path / "paper.sqlite",
    ))
    handler = object.__new__(make_handler(app))
    handler.headers = {}
    handler.send_response = lambda _code: None
    handler.send_header = lambda *_args: None
    handler.end_headers = lambda: None

    class DisconnectedClient:
        def write(self, _body):
            raise BrokenPipeError("client timed out")

    handler.wfile = DisconnectedClient()
    handler._send(200, {"status": "ok"})
