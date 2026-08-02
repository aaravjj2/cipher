from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.paper_executor.config import ContractConfig, ExecutorConfig, InstrumentConfig, MarketDataConfig, ScannerConfig, VmForwardingConfig
from core.paper_executor.database import PaperExecutorDatabase
from core.paper_executor.models import Mode, Quote
from core.paper_executor.runtime import RuntimeCoordinator


class MockMarketData:
    def __init__(self):
        self.now = datetime(2026, 7, 28, 14, 30, tzinfo=timezone.utc)
        self.option_symbol = "NVDA260730C00100000"
        self.short_option_symbol = "NVDA260730C00105000"
        self.option_bid = 1.00
        self.option_ask = 1.10
        self.short_option_bid = 0.37
        self.short_option_ask = 0.39
        self.underlying = 100.0
        self.quote_fail = False
        self.calls = []

    def expirations(self, ticker):
        self.calls.append(("expirations", ticker))
        return ["2026-07-30"]

    def chain(self, ticker, expiration):
        self.calls.append(("chain", ticker, expiration))
        return [{
            "symbol": self.option_symbol,
            "expiration_date": expiration,
            "strike": 100,
            "option_type": "call",
            "active": True,
        }, {
            "symbol": self.short_option_symbol,
            "expiration_date": expiration,
            "strike": 105,
            "option_type": "call",
            "active": True,
        }]

    def quotes(self, symbols):
        self.calls.append(("quotes", tuple(symbols)))
        if self.quote_fail:
            raise RuntimeError("feed down")
        out = {}
        for symbol in symbols:
            if symbol == self.option_symbol:
                out[symbol] = Quote(symbol, self.option_bid, self.option_ask, self.now, volume=50, open_interest=500)
            elif symbol == self.short_option_symbol:
                out[symbol] = Quote(symbol, self.short_option_bid, self.short_option_ask, self.now, volume=50, open_interest=500)
            elif symbol == "NVDA":
                out[symbol] = Quote(symbol, self.underlying - 0.01, self.underlying + 0.01, self.now, last=self.underlying)
        return out


def signal(now):
    return {
        "batch_id": "b1",
        "source": "access_obsidian_browser",
        "cards": [{
            "ticker": "NVDA",
            "scanner_type": "flash",
            "direction": "bullish",
            "setup": "floor bounce",
            "captured_timestamp": now.isoformat(),
            "spot": 100,
            "target": 101,
            "invalidation": 99,
        }],
    }


def runtime(tmp_path, md=None, *, vm_enabled=True):
    cfg = ExecutorConfig(
        runtime_root=tmp_path,
        database_path=tmp_path / "paper.sqlite",
        market_data=MarketDataConfig(quote_maximum_age_seconds=10),
        contract=ContractConfig(minimum_dte=0, allow_0dte=True),
        scanner=ScannerConfig(maximum_signal_age_seconds=999999999),
        vm_forwarding=VmForwardingConfig(enabled=vm_enabled),
    )
    db = PaperExecutorDatabase(cfg.database_path)
    rt = RuntimeCoordinator(cfg, db, market_data=md or MockMarketData())
    rt.recover()
    return rt


def open_shadow_position(rt, md):
    res = rt.ingest_payload(signal(md.now))
    assert res["queued"] is True
    rt.drain_for_tests()
    positions = rt.db.rows("paper_positions")
    assert len(positions) == 1
    assert positions[0]["status"] == "SHADOW_OPEN"
    return positions[0]


def test_runtime_end_to_end_shadow_take_profit_and_restart_no_duplicate(tmp_path):
    md = MockMarketData()
    rt = runtime(tmp_path, md)
    open_shadow_position(rt, md)
    md.option_bid = 1.35
    md.option_ask = 1.45
    md.now += timedelta(seconds=1)
    result = rt.monitor_once(md.now)
    assert result[0]["closed"] is True
    assert result[0]["exit_reason"] == "option_take_profit"
    assert rt.db.rows("paper_positions")[0]["status"] == "CLOSED"
    rt2 = runtime(tmp_path, md)
    assert rt2.mode == Mode.SHADOW
    duplicate = rt2.ingest_payload(signal(md.now))
    assert duplicate["duplicate_batch"] is True
    rt2.drain_for_tests()
    assert len(rt2.db.rows("paper_positions")) == 1


def test_runtime_stop_loss_exit(tmp_path):
    md = MockMarketData()
    rt = runtime(tmp_path, md)
    open_shadow_position(rt, md)
    md.option_bid = 0.80
    md.option_ask = 0.90
    assert rt.monitor_once(md.now)[0]["exit_reason"] == "option_stop_loss"


def test_runtime_underlying_invalidation_exit(tmp_path):
    md = MockMarketData()
    rt = runtime(tmp_path, md)
    open_shadow_position(rt, md)
    md.option_bid = 1.05
    md.option_ask = 1.15
    md.underlying = 98.75
    assert rt.monitor_once(md.now)[0]["exit_reason"] == "underlying_invalidation"


def test_runtime_underlying_target_exit(tmp_path):
    md = MockMarketData()
    rt = runtime(tmp_path, md)
    open_shadow_position(rt, md)
    md.option_bid = 1.05
    md.option_ask = 1.15
    md.underlying = 101.25
    assert rt.monitor_once(md.now)[0]["exit_reason"] == "underlying_target"


def test_runtime_max_hold_exit(tmp_path):
    md = MockMarketData()
    rt = runtime(tmp_path, md)
    open_shadow_position(rt, md)
    md.option_bid = 1.05
    md.option_ask = 1.15
    later = md.now + timedelta(minutes=46)
    md.now = later
    assert rt.monitor_once(later)[0]["exit_reason"] == "maximum_holding_time"


def test_runtime_force_close_exit(tmp_path):
    md = MockMarketData()
    rt = runtime(tmp_path, md)
    open_shadow_position(rt, md)
    md.option_bid = 1.05
    md.option_ask = 1.15
    force_time = datetime(2026, 7, 28, 19, 46, tzinfo=timezone.utc)
    md.now = force_time
    assert rt.monitor_once(force_time)[0]["exit_reason"] == "maximum_holding_time"


def test_runtime_stale_feed_does_not_fill_exit(tmp_path):
    md = MockMarketData()
    rt = runtime(tmp_path, md)
    open_shadow_position(rt, md)
    stale_now = md.now + timedelta(seconds=30)
    md.quote_fail = True
    result = rt.monitor_once(stale_now)
    assert result[0]["status"] == "stale_quote"
    assert rt.db.rows("paper_positions")[0]["status"] == "SHADOW_OPEN"


def test_runtime_reconnect_and_resubscribe(tmp_path):
    md = MockMarketData()
    rt = runtime(tmp_path, md)
    open_shadow_position(rt, md)
    assert md.option_symbol in rt.quote_manager.active_symbols
    md.quote_fail = True
    rt.quote_manager.refresh([md.option_symbol])
    assert rt.quote_manager.degraded
    md.quote_fail = False
    rt.quote_manager.reconnect_once()
    assert rt.quote_manager.last_error is None


def test_recovery_resets_processing_batch_to_recovered(tmp_path):
    md = MockMarketData()
    rt = runtime(tmp_path, md)
    response = rt.ingest_payload(signal(md.now))
    rt.db.update_batch_status(response["batch_id"], "PROCESSING")
    recovered = runtime(tmp_path, md)
    assert recovered.db.batch(response["batch_id"])["status"] == "RECEIVED_RECOVERED"


def test_recovery_requeues_pending_forward_items(tmp_path):
    md = MockMarketData()
    rt = runtime(tmp_path, md)
    response = rt.ingest_payload(signal(md.now))
    item_id = rt.db.rows("forward_queue")[0]["id"]
    assert response["queued"] is True
    recovered = runtime(tmp_path, md)
    assert recovered.forward_queue.get_nowait() == item_id


def test_health_exposes_deployment_observability(tmp_path):
    md = MockMarketData()
    rt = runtime(tmp_path, md, vm_enabled=False)
    open_shadow_position(rt, md)
    payload = rt.health()
    obs = payload["observability"]
    assert obs["configured_mode"] == "shadow"
    assert obs["effective_mode"] == "shadow"
    assert obs["database_integrity_ok"] is True
    assert obs["last_batch_at"] is not None
    assert obs["last_episode_at"] is not None
    assert obs["last_market_data_quote_at"] is not None
    assert obs["open_shadow_positions"] == 1
    assert obs["open_paper_positions"] == 0
    assert obs["vm_forward_backlog"] == 0
    assert obs["uptime_seconds"] >= 0


def test_crash_during_position_creation_does_not_duplicate_episode_position(tmp_path):
    md = MockMarketData()
    rt = runtime(tmp_path, md)
    open_shadow_position(rt, md)
    episode_id = rt.db.rows("signal_episodes")[0]["id"]
    created, reason = rt.db.create_position_transactional(
        position_id="duplicate-position",
        episode_id=episode_id,
        ticker="NVDA",
        direction="bullish",
        symbol=md.option_symbol,
        quantity=1,
        entry_price=1.11,
        status="SHADOW_OPEN",
        payload={"opened_at": md.now.isoformat(), "target": 98, "invalidation": 101},
        max_open_positions=2,
        max_positions_per_ticker=1,
        max_new_positions_per_day=5,
        stop_after_daily_losses=2,
    )
    assert created is False
    assert reason == "SKIPPED_DUPLICATE"
    assert len(rt.db.rows("paper_positions")) == 1


def test_crash_during_position_close_is_idempotent(tmp_path):
    md = MockMarketData()
    rt = runtime(tmp_path, md)
    pos = open_shadow_position(rt, md)
    md.option_bid = 1.35
    md.option_ask = 1.45
    first = rt.monitor_once(md.now)[0]
    second = rt.db.close_position(pos["id"], 1.20, "option_take_profit", {})
    assert first["closed"] is True
    assert second is False


def test_runtime_shadow_debit_spread_entry_and_take_profit(tmp_path):
    md = MockMarketData()
    cfg = ExecutorConfig(
        runtime_root=tmp_path,
        database_path=tmp_path / "paper.sqlite",
        market_data=MarketDataConfig(quote_maximum_age_seconds=10),
        contract=ContractConfig(minimum_dte=0, allow_0dte=True),
        scanner=ScannerConfig(maximum_signal_age_seconds=999999999),
        instrument=InstrumentConfig(model="debit_spread"),
        vm_forwarding=VmForwardingConfig(enabled=False),
    )
    rt = RuntimeCoordinator(cfg, PaperExecutorDatabase(cfg.database_path), market_data=md)
    rt.recover()
    open_shadow_position(rt, md)
    position = rt.db.rows("paper_positions")[0]
    assert "/" in position["symbol"]
    assert '"instrument_model": "debit_spread"' in position["payload_json"]
    md.option_bid = 1.60
    md.option_ask = 1.70
    md.short_option_bid = 0.28
    md.short_option_ask = 0.30
    md.now += timedelta(seconds=1)
    result = rt.monitor_once(md.now)
    assert result[0]["closed"] is True
    assert result[0]["exit_reason"] == "option_take_profit"
