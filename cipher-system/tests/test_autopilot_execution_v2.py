from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import pytest

from core.paper_executor.config import ContractConfig, SimulationConfig, InstrumentConfig
from core.paper_executor.fill_simulator import simulate_spread_entry, simulate_spread_exit, simulate_entry
from core.paper_executor.alpaca_core_market_data import AlpacaCoreMarketData
from core.paper_executor.exchange_calendar import session_close
from core.paper_executor.runtime import RuntimeCoordinator
from core.paper_executor.database import PaperExecutorDatabase
from core.paper_executor.models import Quote
from test_paper_executor_runtime import MockMarketData, runtime, signal


def test_pair_all_in_cap_width_sizes_and_clocks():
    now = datetime(2026, 9, 9, 15, tzinfo=timezone.utc)
    long = Quote("long", 9.9, 10, now)
    short = Quote("short", 5, 5.1, now)
    args = (SimulationConfig(), ContractConfig(maximum_contract_cost=500), 1, 30, now)
    with pytest.raises(ValueError, match="max cost"):
        simulate_spread_entry(long, short, *args, width=10)
    with pytest.raises(ValueError, match="exceeds width"):
        simulate_spread_entry(long, replace(short, bid=6, ask=6.1), *args, width=4)
    with pytest.raises(ValueError, match="size"):
        simulate_spread_entry(replace(long, ask_size=0), short, *args)
    with pytest.raises(ValueError, match="asynchronous"):
        simulate_spread_entry(long, replace(short, timestamp=now-timedelta(seconds=6)), *args)
    with pytest.raises(ValueError, match="stale"):
        simulate_entry(replace(long, timestamp=now+timedelta(seconds=3)), *args)
    with pytest.raises(ValueError, match="invalid quote"):
        simulate_entry(replace(long, ask=float("nan")), *args)


def test_wide_spread_exit_retains_negative_liquidation():
    now = datetime(2026, 9, 9, 15, tzinfo=timezone.utc)
    long, short = Quote("long", 1, 2, now), Quote("short", 1.5, 2.5, now)
    fill = simulate_spread_exit(long, short, SimulationConfig(), ContractConfig(), 1, 30, now)
    assert fill["fill_price"] < -1.5


def test_missing_timestamp_and_zero_size_are_preserved():
    assert AlpacaCoreMarketData._quote({"symbol": "NVDA", "bid": 1, "ask": 2}) is None
    row = {"symbol": "NVDA", "bid": 1, "ask": 2, "quote_time": "2026-09-09T15:00:00Z", "ask_size": 0}
    assert AlpacaCoreMarketData._quote(row).ask_size == 0
    assert AlpacaCoreMarketData._quote({**row, "quote_time": "bad"}) is None


def test_execution_clock_rechecks_queue_and_underlying(tmp_path):
    md = MockMarketData()
    rt = runtime(tmp_path, md)
    rt.cfg = replace(rt.cfg, scanner=replace(rt.cfg.scanner, maximum_signal_age_seconds=120))
    result = rt.ingest_payload(signal(md.now))
    rt.process_batch_once(result["batch_id"])
    md.now += timedelta(minutes=3)
    assert rt.process_entry_once(rt.entry_queue.get_nowait())["reason"] == "SKIPPED_STALE_SIGNAL"
    assert rt.db.rows("paper_positions") == []


def test_spread_exits_without_underlying_and_persists_pending_through_restart(tmp_path):
    md = MockMarketData()
    base = runtime(tmp_path, md)
    cfg = replace(base.cfg, instrument=InstrumentConfig(model="debit_spread"))
    rt = RuntimeCoordinator(cfg, base.db, market_data=md, clock=lambda: md.now)
    rt.recover()
    rt.ingest_payload(signal(md.now)); rt.drain_for_tests()
    position = rt.db.rows("paper_positions")[0]
    payload = json.loads(position["payload_json"])
    assert isinstance(payload["entry"]["long_fill"], dict)
    assert payload["entry_evidence"]["decision_at"] <= position["opened_at"]
    md.now += timedelta(minutes=46)
    md.quote_fail = True
    assert rt.monitor_once(md.now)[0]["status"] == "exit_pending"
    recovered = RuntimeCoordinator(cfg, base.db, market_data=md, clock=lambda: md.now)
    recovered.recover()
    md.quote_fail = False
    md.now += timedelta(seconds=65)
    original = md.quotes
    md.quotes = lambda symbols: {k: q for k, q in original(symbols).items() if k != "NVDA"}
    outcome = recovered.monitor_once(md.now)[0]
    assert outcome["closed"] is True
    assert outcome["exit_reason"] == "maximum_holding_time"
    assert len(rt.db.rows("paper_orders")) == 2
    assert recovered.db.session_counts(md.now)["stopped_trades"] == 1
    from core.paper_executor.cohort_evaluation import replay_ledger
    replay = replay_ledger(cfg.database_path)
    assert replay["gaps"] == []
    assert len(replay["decisions"]) == 2
    assert replay["all_covered_decisions_match"] is True


def test_invalid_long_quote_cannot_create_mark_or_fake_exit(tmp_path):
    md = MockMarketData()
    rt = runtime(tmp_path, md)
    rt.ingest_payload(signal(md.now))
    rt.drain_for_tests()
    before = len(rt.db.rows("paper_marks"))
    md.now += timedelta(minutes=46)
    md.option_bid = float("nan")
    assert rt.monitor_once(md.now)[0]["status"] == "exit_pending"
    assert len(rt.db.rows("paper_marks")) == before
    assert len(rt.db.open_positions(include_shadow=True)) == 1


@pytest.mark.parametrize("cost", [float("nan"), float("inf"), -0.1])
def test_invalid_cost_assumptions_rejected(cost):
    with pytest.raises(ValueError):
        SimulationConfig(fee_per_contract=cost)


def test_2026_exchange_close_schedule():
    # https://www.nyse.com/markets/hours-calendars
    assert session_close(datetime(2026, 11, 27).date()).hour == 13
    assert session_close(datetime(2026, 12, 24).date()).hour == 13
    assert session_close(datetime(2026, 7, 2).date()).hour == 16
    assert session_close(datetime(2026, 7, 3).date()) is None
    assert session_close(datetime(2027, 12, 31).date()).hour == 16
