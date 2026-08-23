from datetime import timedelta

from core.paper_executor.config import ContractConfig, ExecutionConfig, ExecutorConfig, MarketDataConfig, ScannerConfig, VmForwardingConfig
from core.paper_executor.database import PaperExecutorDatabase
from core.paper_executor.models import Mode
from core.paper_executor.runtime import RuntimeCoordinator

from test_paper_executor_runtime import MockMarketData, signal


class FilledBroker:
    def __init__(self):
        self.intents = []

    def account(self):
        return {"status": "ACTIVE", "currency": "USD", "equity": 100000.0, "buying_power": 100000.0, "trading_blocked": False, "account_blocked": False, "paper_only": True}

    def positions(self):
        return []

    def submit(self, intent):
        self.intents.append(intent)
        return {"id": f"order-{len(self.intents)}", "client_order_id": intent.client_order_id, "symbol": intent.contract_symbol, "status": "accepted", "average_fill_price": None, "paper_only": True}

    def wait_for_terminal(self, broker_order_id, timeout_seconds, poll_interval_seconds):
        intent = self.intents[-1]
        price = 1.10 if intent.side == "buy" else 1.35
        return {"id": broker_order_id, "client_order_id": intent.client_order_id, "symbol": intent.contract_symbol, "status": "filled", "average_fill_price": price, "filled_at": intent.expires_at.isoformat(), "paper_only": True}


class FailedBroker(FilledBroker):
    def submit(self, intent):
        raise RuntimeError("paper provider unavailable")


def make_runtime(tmp_path, broker):
    md = MockMarketData()
    cfg = ExecutorConfig(
        runtime_root=tmp_path, database_path=tmp_path / "paper.sqlite",
        market_data=MarketDataConfig(quote_maximum_age_seconds=10),
        contract=ContractConfig(minimum_dte=0, allow_0dte=True),
        scanner=ScannerConfig(maximum_signal_age_seconds=999999999),
        vm_forwarding=VmForwardingConfig(enabled=False),
        execution=ExecutionConfig(backend="alpaca_paper", order_timeout_seconds=1, poll_interval_seconds=0.001),
    )
    db = PaperExecutorDatabase(cfg.database_path)
    runtime = RuntimeCoordinator(cfg, db, market_data=md, broker=broker)
    runtime.recover()
    runtime.mode = Mode.PAPER
    return runtime, md


def test_alpaca_paper_fill_uses_broker_price_and_persists_intent_first(tmp_path):
    broker = FilledBroker()
    runtime, md = make_runtime(tmp_path, broker)
    runtime.ingest_payload(signal(md.now))
    runtime.drain_for_tests()
    position = runtime.db.rows("paper_positions")[0]
    order = runtime.db.rows("paper_orders")[0]
    assert position["status"] == "OPEN"
    assert position["entry_price"] == 1.10
    assert order["status"] == "FILLED"
    assert len(broker.intents) == 1
    assert broker.intents[0].client_order_id.startswith("cipher-")


def test_alpaca_paper_failure_creates_no_position_and_is_a_classified_block(tmp_path):
    runtime, md = make_runtime(tmp_path, FailedBroker())
    runtime.ingest_payload(signal(md.now))
    runtime.drain_for_tests()
    assert runtime.db.rows("paper_positions") == []
    assert runtime.db.rows("paper_orders")[0]["status"] == "SUBMISSION_FAILED"
    block = runtime.db.operational_snapshot()["last_entry_block"]
    assert block["reason"] == "SKIPPED_PAPER_BROKER_UNAVAILABLE"


def test_alpaca_paper_exit_closes_only_after_broker_fill(tmp_path):
    broker = FilledBroker()
    runtime, md = make_runtime(tmp_path, broker)
    runtime.ingest_payload(signal(md.now))
    runtime.drain_for_tests()
    md.option_bid = 1.35
    md.option_ask = 1.45
    md.now += timedelta(seconds=1)
    result = runtime.monitor_once(md.now)[0]
    assert result["closed"] is True
    assert result["execution_backend"] == "alpaca_paper"
    assert runtime.db.rows("paper_positions")[0]["exit_price"] == 1.35
    assert [intent.side for intent in broker.intents] == ["buy", "sell"]


def test_unknown_broker_position_blocks_reconciliation(tmp_path):
    broker = FilledBroker()
    broker.positions = lambda: [{"symbol": "AAPL", "quantity": 1}]
    runtime, _ = make_runtime(tmp_path, broker)
    assert runtime.reconciliation_passed is False
    assert runtime.health()["paper_broker"]["unknown_positions"] == ["AAPL"]


def test_explicit_forward_test_authorization_can_promote_only_paper_backend(tmp_path):
    broker = FilledBroker()
    runtime, _ = make_runtime(tmp_path, broker)
    runtime.cfg = ExecutorConfig(
        runtime_root=runtime.cfg.runtime_root,
        database_path=runtime.cfg.database_path,
        market_data=runtime.cfg.market_data,
        contract=runtime.cfg.contract,
        scanner=runtime.cfg.scanner,
        vm_forwarding=runtime.cfg.vm_forwarding,
        execution=ExecutionConfig(
            backend="alpaca_paper", paper_forward_test_authorized=True,
        ),
    )
    for state in runtime.states.values():
        state.running = True
    ok, reason = runtime.promote_to_paper()
    assert (ok, reason, runtime.mode) == (True, "paper", Mode.PAPER)
