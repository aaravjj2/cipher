"""Prospective cost-aware v3 experiment; the four frozen v2 runtimes stay unchanged."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import threading

from .cohorts import CohortRuntime
from .config import ExperimentConfig
from .contract_selector import select_debit_spread
from .fill_simulator import simulate_spread_entry, simulate_spread_exit
from .models import sha256_id


COHORT_ID = 'cost_aware_v3'


def configuration(base):
    root = base.runtime_root / 'cohorts' / COHORT_ID
    return replace(base, runtime_root=root, database_path=root / 'paper.sqlite',
                   server=replace(base.server, port=base.server.port + 4,
                                  control_token_path=root / 'state' / 'control.token'),
                   experiment=ExperimentConfig(cohort_id=COHORT_ID, version='v3',
                       registry_strategy_id='autopilot.v3.cost_aware',
                       maximum_round_trip_stop_fraction=1 / 3))


def economics(spread, cfg, now):
    """Executable entry and immediate exit, including the frozen fees/slippage.

    Use the already-registered v2 cost experiment's one-third limit, not an
    optimized threshold fitted to losing trades. Sizes must cover both sides.
    """
    long, short = spread.long_leg.quote, spread.short_leg.quote
    if long is None or short is None:
        raise ValueError('missing_quote')
    for quote in (long, short):
        if quote.symbol not in (spread.long_leg.contract.symbol, spread.short_leg.contract.symbol):
            raise ValueError('quote_identity_mismatch')
        if any(isinstance(size, bool) or not isinstance(size, int) or size < cfg.portfolio.quantity_per_trade
               for size in (quote.bid_size, quote.ask_size)):
            raise ValueError('unavailable_executable_size')
    if long.symbol != spread.long_leg.contract.symbol or short.symbol != spread.short_leg.contract.symbol:
        raise ValueError('quote_identity_mismatch')
    args = (long, short, cfg.simulation, cfg.contract, cfg.portfolio.quantity_per_trade,
            cfg.market_data.quote_maximum_age_seconds, now)
    opening = simulate_spread_entry(*args, width=spread.width)
    closing = simulate_spread_exit(*args)
    multiplier = cfg.portfolio.quantity_per_trade * 100
    debit = opening['fill_price'] * multiplier
    liquidation = closing['fill_price'] * multiplier
    cost = debit - liquidation
    stop = debit * cfg.exit.stop_loss_pct / 100
    if not all(math.isfinite(v) for v in (debit, liquidation, cost, stop)) or stop <= 0 or cost < 0:
        raise ValueError('invalid_execution_economics')
    limit = stop * cfg.experiment.maximum_round_trip_stop_fraction
    reason = 'already_beyond_stop' if cost >= stop else 'round_trip_cost_exceeds_budget' if cost > limit else None
    return {'entry_debit_usd': debit, 'immediate_liquidation_usd': liquidation,
            'round_trip_cost_usd': cost, 'stop_budget_usd': stop,
            'maximum_cost_usd': limit, 'cost_to_stop_ratio': cost / stop,
            'reason': reason}


class CostAwareRuntime(CohortRuntime):
    def __init__(self, cfg, db, **kwargs):
        if (cfg.experiment.cohort_id != COHORT_ID or cfg.experiment.version != 'v3'
                or cfg.execution.backend != 'simulated' or cfg.instrument.model != 'debit_spread'
                or cfg.portfolio.quantity_per_trade != 1
                or cfg.experiment.maximum_round_trip_stop_fraction != 1 / 3
                or not math.isfinite(cfg.exit.stop_loss_pct) or not 0 < cfg.exit.stop_loss_pct < 100):
            raise ValueError('cost-aware v3 requires its frozen simulated debit-spread policy')
        super().__init__(cfg, db, **kwargs)
        self.config_hash = sha256_id('config', {'base_hash': self.config_hash,
            'cost_aware_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
        self._selection = threading.local()
        with db.connect() as conn:
            conn.execute('create table if not exists cost_aware_registration (id integer primary key check(id=1), config_hash text not null, registered_at text not null)')
            conn.execute('insert or ignore into cost_aware_registration values(1,?,?)', (self.config_hash, self.clock().isoformat()))
            row = conn.execute('select config_hash,registered_at from cost_aware_registration where id=1').fetchone()
            if row['config_hash'] != self.config_hash:
                raise ValueError('Frozen cost-aware implementation changed; start a new version')
            self.registered_at = datetime.fromisoformat(row['registered_at'])
            conn.execute('create table if not exists cost_aware_candidates (episode_id text not null, symbol text not null, evaluated_at text not null, evidence_json text not null, primary key(episode_id,symbol))')

    def process_entry_once(self, item):
        if item['card'].captured_at < self.registered_at:
            return self._entry_block(item['episode_id'], item['card'], 'SKIPPED_PRE_REGISTRATION')
        return super().process_entry_once(item)

    def _entry_evidence(self, card, quotes):
        return {**super()._entry_evidence(card, quotes),
                'entry_economics': getattr(self._selection, 'evidence', None)}

    def _process_spread_entry(self, card, episode_id, contracts, quotes):
        now = self.clock()
        _, legs, spreads = select_debit_spread(card, contracts, quotes, self.cfg.contract,
            minimum_width=self.cfg.instrument.minimum_spread_width,
            maximum_width=self.cfg.instrument.maximum_spread_width, now=now)
        self.db.persist_candidates(episode_id, legs)
        evaluated, eligible = [], []
        for spread in spreads:
            detail = {'symbol': spread.symbol, 'structural_rejections': list(spread.rejection_reasons)}
            if spread.accepted:
                try:
                    detail.update(economics(spread, self.cfg, now))
                    if detail['reason'] is None:
                        eligible.append((spread, detail))
                except (ValueError, TypeError) as exc:
                    detail['reason'] = str(exc)
            evaluated.append(detail)
        eligible.sort(key=lambda item: (item[1]['cost_to_stop_ratio'], item[0].ranking_score,
                                        item[0].long_leg.contract.expiration, item[0].symbol))
        selected = eligible[0] if eligible else None
        with self.db.connect() as conn:
            for detail in evaluated:
                detail['selected'] = bool(selected and detail['symbol'] == selected[0].symbol)
                conn.execute('insert or replace into cost_aware_candidates values(?,?,?,?)',
                             (episode_id, detail['symbol'], now.isoformat(), json.dumps(detail, allow_nan=False)))
        if not selected:
            return self._entry_block(episode_id, card, 'SKIPPED_EXECUTION_ECONOMICS')
        spread, detail = selected
        check = self._current_entry_check(card, quotes.get(card.ticker))
        if check:
            return self._entry_block(episode_id, card, check)
        self._selection.evidence = {**detail, 'policy': 'cost_aware_v3',
                                    'evaluated_candidates': len(evaluated), 'eligible_candidates': len(eligible)}
        try:
            # Reuse the existing transactional order/position writer and fresh
            # fill validation, restricting it to the chosen exact two legs.
            return super()._process_spread_entry(card, episode_id,
                [spread.long_leg.contract, spread.short_leg.contract], quotes)
        finally:
            del self._selection.evidence
