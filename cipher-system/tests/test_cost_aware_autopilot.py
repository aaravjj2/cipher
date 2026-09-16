from dataclasses import replace
from datetime import timedelta
import json
from types import SimpleNamespace

import pytest

from core.paper_executor.cohorts import SharedObservations, configurations
from core.paper_executor.config import InstrumentConfig
from core.paper_executor.cost_aware import CostAwareRuntime, configuration, economics
from core.paper_executor.database import PaperExecutorDatabase
from core.paper_executor.models import Quote
from test_paper_executor_runtime import MockMarketData, runtime, signal


class SizedMarketData(MockMarketData):
    size = 10

    def __init__(self):
        super().__init__()
        self.option_bid, self.option_ask = 4.00, 4.01
        self.short_option_bid, self.short_option_ask = 1.00, 1.01

    def quotes(self, symbols):
        return {key: replace(q, bid_size=self.size, ask_size=self.size)
                for key, q in super().quotes(symbols).items()}


def build(tmp_path, md):
    base = replace(runtime(tmp_path, md).cfg, instrument=InstrumentConfig(model='debit_spread'))
    cfg = configuration(base)
    view = SharedObservations(md, tmp_path / 'observations.sqlite').view()
    rt = CostAwareRuntime(cfg, PaperExecutorDatabase(cfg.database_path), market_data=view, clock=lambda: md.now)
    rt.recover()
    return rt, base


def ingest(rt, md):
    rt.ingest_payload(signal(md.now))
    rt.drain_for_tests()
    return rt.db.rows('paper_positions')


def test_isolated_registration_evidence_and_restart(tmp_path):
    md = SizedMarketData()
    rt, base = build(tmp_path, md)
    originals = configurations(base)
    assert rt.cfg.database_path not in [c.database_path for c in originals]
    assert rt.cfg.server.port not in [c.server.port for c in originals]
    positions = ingest(rt, md)
    assert len(positions) == 1
    evidence = json.loads(positions[0]['payload_json'])['entry_evidence']['entry_economics']
    assert evidence['cost_to_stop_ratio'] <= 1 / 3
    assert evidence['selected'] is True
    registered = rt.registered_at
    md.now += timedelta(seconds=1)
    restarted, _ = build(tmp_path, md)
    assert restarted.registered_at == registered
    assert restarted.config_hash == rt.config_hash
    assert len(ingest(restarted, md)) == 1


@pytest.mark.parametrize('size', [None, 0, True])
def test_unknown_or_inadequate_size_never_fills(tmp_path, size):
    md = SizedMarketData()
    md.size = size
    rt, _ = build(tmp_path, md)
    assert ingest(rt, md) == []


def test_excessive_cost_never_fills(tmp_path):
    md = SizedMarketData()
    md.option_bid, md.option_ask = 1, 1.1
    md.short_option_bid, md.short_option_ask = .37, .39
    rt, _ = build(tmp_path, md)
    assert ingest(rt, md) == []
    with rt.db.connect() as conn:
        details = [json.loads(row[0]) for row in conn.execute('select evidence_json from cost_aware_candidates')]
    assert any(d.get('reason') == 'already_beyond_stop' for d in details)


def test_pre_registration_signal_never_fills(tmp_path):
    md = SizedMarketData()
    rt, _ = build(tmp_path, md)
    md.now -= timedelta(seconds=1)
    assert ingest(rt, md) == []


def test_changed_policy_requires_new_version(tmp_path):
    md = SizedMarketData()
    rt, _ = build(tmp_path, md)
    changed = replace(rt.cfg, exit=replace(rt.cfg.exit, stop_loss_pct=16))
    with pytest.raises(ValueError, match='Frozen cost-aware'):
        CostAwareRuntime(changed, rt.db, market_data=rt.market_data, clock=lambda: md.now)


def test_searches_past_structural_winner_with_excessive_cost(tmp_path):
    class Alternatives(SizedMarketData):
        expensive = 'NVDA260730C00101000'

        def chain(self, ticker, expiration):
            return super().chain(ticker, expiration) + [{
                'symbol': self.expensive, 'expiration_date': expiration,
                'strike': 101, 'option_type': 'call', 'active': True}]

        def quotes(self, symbols):
            result = super().quotes(symbols)
            if self.expensive in symbols:
                result[self.expensive] = Quote(self.expensive, 3.6, 3.61, self.now,
                    bid_size=10, ask_size=10, volume=50, open_interest=500)
            return result

    md = Alternatives()
    rt, _ = build(tmp_path, md)
    assert len(ingest(rt, md)) == 1
    with rt.db.connect() as conn:
        details = [json.loads(row[0]) for row in conn.execute('select evidence_json from cost_aware_candidates')]
    assert any(md.expensive in d['symbol'] and d.get('reason') == 'already_beyond_stop' for d in details)
    selected = [d for d in details if d['selected']]
    assert len(selected) == 1
    assert md.short_option_symbol in selected[0]['symbol']


@pytest.mark.parametrize('failure', ['missing', 'stale', 'asynchronous', 'identity'])
def test_invalid_quote_pair_never_fills(tmp_path, failure):
    class BadQuotes(SizedMarketData):
        def quotes(self, symbols):
            result = super().quotes(symbols)
            if self.short_option_symbol in result:
                quote = result[self.short_option_symbol]
                if failure == 'missing':
                    del result[self.short_option_symbol]
                elif failure == 'identity':
                    result[self.short_option_symbol] = replace(quote, symbol=self.option_symbol)
                else:
                    result[self.short_option_symbol] = replace(quote,
                        timestamp=self.now - timedelta(seconds=20 if failure == 'stale' else 6))
            return result

    md = BadQuotes()
    rt, _ = build(tmp_path, md)
    assert ingest(rt, md) == []


def test_round_trip_cost_includes_four_contract_fees(tmp_path):
    md = SizedMarketData()
    rt, _ = build(tmp_path, md)
    quotes = md.quotes([md.option_symbol, md.short_option_symbol])
    def leg(symbol):
        return SimpleNamespace(contract=SimpleNamespace(symbol=symbol), quote=quotes[symbol])
    spread = SimpleNamespace(long_leg=leg(md.option_symbol), short_leg=leg(md.short_option_symbol), width=5)
    before = economics(spread, rt.cfg, md.now)
    charged = replace(rt.cfg, simulation=replace(rt.cfg.simulation, fee_per_contract=.65))
    after = economics(spread, charged, md.now)
    assert after['round_trip_cost_usd'] - before['round_trip_cost_usd'] == pytest.approx(2.60)
    assert after['entry_debit_usd'] - before['entry_debit_usd'] == pytest.approx(1.30)
