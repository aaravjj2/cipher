from __future__ import annotations

import json
import queue
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .config import ExecutorConfig
from .alpaca_core_market_data import AlpacaCoreMarketData
from .contract_selector import contracts_from_chain, select_contract, select_debit_spread
from .database import PaperExecutorDatabase
from .episode_tracker import EpisodeTracker
from .fill_simulator import simulate_entry, simulate_exit, simulate_spread_entry, simulate_spread_exit
from .ingestion import normalize_batch
from .models import Direction, Lifecycle, Mode, PaperPosition, Quote, sha256_id
from .policy import eligibility_skip
from .position_manager import exit_reason
from .quote_manager import MarketDataClient, QuoteManager
from .risk_guard import RiskGuard
from .tradier_market_data import TradierMarketData
from .validation import validate_card
from .vm_forwarder import VmForwarder


@dataclass
class WorkerState:
    name: str
    running: bool = False
    last_error: str | None = None
    processed: int = 0
    restarts: int = 0


class RuntimeCoordinator:
    def __init__(
        self,
        cfg: ExecutorConfig,
        db: PaperExecutorDatabase,
        *,
        market_data: MarketDataClient | None = None,
        forwarder: VmForwarder | None = None,
    ):
        self.cfg = cfg
        self.db = db
        self.market_data = market_data or (
            AlpacaCoreMarketData(cfg.market_data)
            if cfg.market_data.provider == "alpaca_core"
            else TradierMarketData(cfg.market_data)
        )
        self.quote_manager = QuoteManager(cfg, self.market_data)
        self.forwarder = forwarder or VmForwarder(db, cfg.runtime_root / "queue" / "vm_pending", cfg.vm_forwarding.endpoint)
        self.episodes = EpisodeTracker(db, cfg.scanner.episode_cooldown_minutes)
        self.risk = RiskGuard(cfg)
        self.mode = cfg.safety.default_start_mode
        self.reconciliation_passed = False
        self.started_at = datetime.now(timezone.utc)
        self.batch_queue: queue.Queue[str] = queue.Queue(maxsize=500)
        self.entry_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=500)
        self.forward_queue: queue.Queue[str] = queue.Queue(maxsize=500)
        self.shutdown_event = threading.Event()
        self.states = {
            "batch": WorkerState("batch"),
            "entry": WorkerState("entry"),
            "monitor": WorkerState("monitor"),
            "forward": WorkerState("forward"),
        }
        self.threads: list[threading.Thread] = []

    def recover(self) -> None:
        self.mode = Mode.SHADOW
        healthy = self.db.integrity_ok()
        with self.db.connect() as db:
            db.execute("update signal_batches set status = 'RECEIVED_RECOVERED' where status = 'PROCESSING'")
        open_positions = self.db.open_positions(include_shadow=True)
        symbols: list[str] = []
        for pos in open_positions:
            symbols.extend(self._symbols_for_position_row(pos))
        self.quote_manager.subscribe(symbols)
        for item_id in self.db.due_forward_items(datetime.now(timezone.utc).isoformat()):
            try:
                self.forward_queue.put_nowait(item_id)
            except queue.Full:
                break
        self.reconciliation_passed = healthy
        self.db.insert_system_event(
            "RECOVERED",
            {
                "database_healthy": healthy,
                "open_positions": len(open_positions),
                "subscriptions": sorted(set(symbols)),
                "effective_mode": self.mode.value,
            },
        )

    def start(self) -> None:
        self.recover()
        for name, target in (
            ("batch", self._batch_loop),
            ("entry", self._entry_loop),
            ("monitor", self._monitor_loop),
            ("forward", self._forward_loop),
        ):
            thread = threading.Thread(target=self._guarded_loop, args=(name, target), name=f"paper-executor-{name}", daemon=True)
            thread.start()
            self.threads.append(thread)

    def stop(self, timeout: float = 5.0) -> None:
        self.shutdown_event.set()
        for thread in self.threads:
            thread.join(timeout=timeout)
        self.db.insert_system_event("SHUTDOWN", {"queues": self.queue_depths()})

    def health(self) -> dict[str, Any]:
        operational = self.db.operational_snapshot()
        worker_errors = [state.last_error for state in self.states.values() if state.last_error]
        return {
            "mode": self.mode.value,
            "reconciliation_passed": self.reconciliation_passed,
            "workers": {name: state.__dict__ for name, state in self.states.items()},
            "queues": self.queue_depths(),
            "quote_manager": {
                "degraded": self.quote_manager.degraded,
                "active_symbols": self.quote_manager.active_symbols,
                "last_error": self.quote_manager.last_error,
                "last_fresh_quote_at": self.quote_manager.last_fresh_quote_at,
            },
            "observability": {
                "configured_mode": self.cfg.safety.default_start_mode.value,
                "effective_mode": self.mode.value,
                "database_integrity_ok": self.db.integrity_ok(),
                "last_batch_at": operational["last_batch_at"],
                "last_episode_at": operational["last_episode_at"],
                "last_market_data_quote_at": self.quote_manager.last_fresh_quote_at,
                "last_mark_at": operational["last_mark_at"],
                "open_shadow_positions": operational["counts"]["open_shadow_positions"],
                "open_paper_positions": operational["counts"]["open_paper_positions"],
                "vm_forward_backlog": operational["counts"]["forward_backlog"],
                "last_worker_exception": worker_errors[-1] if worker_errors else operational["last_worker_exception"],
                "uptime_seconds": round((datetime.now(timezone.utc) - self.started_at).total_seconds(), 3),
            },
        }

    def queue_depths(self) -> dict[str, int]:
        return {
            "batch": self.batch_queue.qsize(),
            "entry": self.entry_queue.qsize(),
            "forward": self.forward_queue.qsize(),
        }

    def enqueue_batch(self, batch_id: str) -> bool:
        try:
            self.batch_queue.put_nowait(batch_id)
            return True
        except queue.Full:
            self.db.update_batch_status(batch_id, "RECEIVED_BACKPRESSURE")
            return False

    def promote_to_paper(self) -> tuple[bool, str]:
        from .promotion_gate import gate_status

        gate = gate_status()
        if not gate["eligible_count"]:
            return False, "no strategy has cleared the FAST_BACKTESTED promotion gate"
        if not self.reconciliation_passed or not self.db.integrity_ok():
            return False, "database reconciliation has not passed"
        if self.quote_manager.degraded:
            return False, "quote feed is degraded"
        if self.cfg.kill_switch_path.exists():
            return False, "kill switch is active"
        if any(not state.running or state.last_error for state in self.states.values()):
            return False, "workers are not healthy"
        self.mode = Mode.PAPER
        return True, "paper"

    def ingest_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        batch = normalize_batch(payload)
        checksum = sha256_id("checksum", batch["raw"])
        accepted = self.db.insert_batch(batch, checksum)
        if not accepted:
            return {"accepted": True, "duplicate_batch": True, "batch_id": batch["batch_id"], "queued": False}
        if self.cfg.vm_forwarding.enabled:
            item_id = self.forwarder.enqueue(batch["batch_id"], batch["raw"])
            try:
                self.forward_queue.put_nowait(item_id)
            except queue.Full:
                pass
        queued = self.enqueue_batch(batch["batch_id"])
        return {"accepted": True, "duplicate_batch": False, "batch_id": batch["batch_id"], "queued": queued}

    def process_batch_once(self, batch_id: str) -> list[dict[str, Any]]:
        row = self.db.batch(batch_id)
        if not row:
            return []
        if row["status"] in {"PROCESSED", "PROCESSING"}:
            return []
        self.db.update_batch_status(batch_id, "PROCESSING")
        payload = json.loads(row["raw_json"])
        batch = normalize_batch(payload)
        results: list[dict[str, Any]] = []
        now = datetime.now(timezone.utc)
        for idx, raw in enumerate(batch["cards"]):
            card_id = sha256_id("card", {"batch": batch_id, "idx": idx, "raw": raw})
            card, reasons = validate_card(raw, self.cfg, now)
            if not card:
                self.db.insert_card(card_id, batch_id, raw, Lifecycle.REJECTED.value, ",".join(reasons))
                results.append({"card_id": card_id, "status": "skipped", "reasons": reasons})
                continue
            normalized = {
                "ticker": card.ticker,
                "scanner_type": card.scanner_type,
                "direction": card.direction.value,
                "setup": card.setup,
                "captured_at": card.captured_at.isoformat(),
                "spot": card.spot,
                "target": card.target,
                "invalidation": card.invalidation,
            }
            skip = eligibility_skip(card, self.cfg, self.cfg.kill_switch_path.exists(), self.quote_manager.degraded)
            if skip:
                self.db.insert_card(card_id, batch_id, raw, Lifecycle.REJECTED.value, skip.value, normalized)
                results.append({"card_id": card_id, "status": "skipped", "reasons": [skip.value]})
                continue
            self.db.insert_card(card_id, batch_id, raw, Lifecycle.VALIDATED.value, None, normalized)
            episode_id, duplicate = self.episodes.record(card, card_id)
            self.db.insert_card(
                card_id,
                batch_id,
                raw,
                Lifecycle.DUPLICATE.value if duplicate else Lifecycle.ELIGIBLE.value,
                "SKIPPED_DUPLICATE" if duplicate else None,
                normalized,
            )
            if not duplicate:
                self.entry_queue.put({"episode_id": episode_id, "card": card, "card_id": card_id, "signal_received_at": row["received_at"]})
            results.append({"card_id": card_id, "episode_id": episode_id, "duplicate_episode": duplicate})
        self.db.update_batch_status(batch_id, "PROCESSED")
        return results

    def process_entry_once(self, item: dict[str, Any]) -> dict[str, Any]:
        card = item["card"]
        episode_id = item["episode_id"]
        if self.mode == Mode.DISABLED:
            return {"status": "skipped", "reason": "SKIPPED_MODE_DISABLED"}
        if self.cfg.kill_switch_path.exists():
            return {"status": "skipped", "reason": "SKIPPED_KILL_SWITCH"}
        if self.quote_manager.degraded:
            return {"status": "skipped", "reason": "SKIPPED_DATA_FEED_DEGRADED"}
        risk_skip = self.risk.entry_skip(
            mode=self.mode,
            kill_switch=False,
            open_positions=[self._position_from_row(row) for row in self.db.open_positions()],
            ticker=card.ticker,
            new_positions_today=0,
            stopped_today=0,
        )
        if risk_skip:
            return {"status": "skipped", "reason": risk_skip.value}
        expirations = [
            exp for exp in self.market_data.expirations(card.ticker)
            if self.cfg.contract.minimum_dte
            <= self._dte(exp, card.captured_at)
            <= self.cfg.contract.maximum_dte
        ]
        if not expirations:
            return {"status": "skipped", "reason": "SKIPPED_NO_CONTRACT"}
        contracts = []
        for expiration in expirations:
            contracts.extend(contracts_from_chain(card.ticker, self.market_data.chain(card.ticker, expiration), card.option_type))
        symbols = [c.symbol for c in contracts]
        quotes = self.quote_manager.refresh(symbols)
        if self.cfg.instrument.model == "debit_spread":
            return self._process_spread_entry(card, episode_id, contracts, quotes)
        selected, candidates = select_contract(
            card,
            contracts,
            quotes,
            self.cfg.contract,
            now=card.captured_at,
        )
        self.db.persist_candidates(episode_id, candidates)
        if not selected or not selected.quote:
            return {"status": "skipped", "reason": "SKIPPED_NO_CONTRACT"}
        entry = simulate_entry(
            selected.quote,
            self.cfg.simulation,
            self.cfg.contract,
            self.cfg.portfolio.quantity_per_trade,
            self.cfg.market_data.quote_maximum_age_seconds,
            card.captured_at,
        )
        position_id = sha256_id("position", {"episode_id": episode_id, "symbol": selected.contract.symbol})
        status = "SHADOW_OPEN" if self.mode == Mode.SHADOW else "OPEN"
        payload = {
            "episode_id": episode_id,
            "mode": self.mode.value,
            "ticker": card.ticker,
            "direction": card.direction.value,
            "contract": selected.contract,
            "entry": entry,
            "opened_at": selected.quote.timestamp.isoformat(),
            "target": card.target,
            "invalidation": card.invalidation,
            "latency": {
                "signal_to_entry_seconds": (datetime.now(timezone.utc) - card.captured_at).total_seconds(),
            },
        }
        created, reason = self.db.create_position_transactional(
            position_id=position_id,
            episode_id=episode_id,
            ticker=card.ticker,
            direction=card.direction.value,
            symbol=selected.contract.symbol,
            quantity=entry.quantity,
            entry_price=entry.fill_price,
            status=status,
            payload=payload,
            max_open_positions=self.cfg.portfolio.maximum_open_positions,
            max_positions_per_ticker=self.cfg.portfolio.maximum_positions_per_ticker,
            max_new_positions_per_day=self.cfg.portfolio.maximum_new_positions_per_day,
            stop_after_daily_losses=self.cfg.portfolio.stop_after_daily_losses,
        )
        if not created:
            return {"status": "skipped", "reason": reason}
        self._record_contract_quote(position_id, episode_id, selected.contract.symbol,
                                    "long_option", selected.quote, selected.quote.timestamp)
        self.quote_manager.subscribe([selected.contract.symbol, card.ticker])
        return {"status": status, "position_id": position_id, "entry_price": entry.fill_price}

    def _process_spread_entry(self, card, episode_id: str, contracts: list, quotes: dict[str, Quote]) -> dict[str, Any]:
        selected, leg_candidates, spread_candidates = select_debit_spread(
            card,
            contracts,
            quotes,
            self.cfg.contract,
            minimum_width=self.cfg.instrument.minimum_spread_width,
            maximum_width=self.cfg.instrument.maximum_spread_width,
            now=card.captured_at,
        )
        self.db.persist_candidates(episode_id, leg_candidates)
        if not selected or not selected.long_leg.quote or not selected.short_leg.quote:
            self.db.insert_system_event("SPREAD_ENTRY_SKIPPED", {
                "episode_id": episode_id,
                "ticker": card.ticker,
                "direction": card.direction.value,
                "setup": card.setup,
                "spread_candidates": len(spread_candidates),
                "accepted_spreads": sum(1 for spread in spread_candidates if spread.accepted),
            })
            return {"status": "skipped", "reason": "SKIPPED_NO_CONTRACT"}
        entry = simulate_spread_entry(
            selected.long_leg.quote,
            selected.short_leg.quote,
            self.cfg.simulation,
            self.cfg.contract,
            self.cfg.portfolio.quantity_per_trade,
            self.cfg.market_data.quote_maximum_age_seconds,
            card.captured_at,
        )
        position_id = sha256_id("position", {"episode_id": episode_id, "symbol": selected.symbol})
        status = "SHADOW_OPEN" if self.mode == Mode.SHADOW else "OPEN"
        payload = {
            "episode_id": episode_id,
            "mode": self.mode.value,
            "instrument_model": "debit_spread",
            "ticker": card.ticker,
            "direction": card.direction.value,
            "long_contract": {
                "symbol": selected.long_leg.contract.symbol,
                "ticker": selected.long_leg.contract.ticker,
                "expiration": selected.long_leg.contract.expiration,
                "strike": selected.long_leg.contract.strike,
                "option_type": selected.long_leg.contract.option_type.value,
            },
            "short_contract": {
                "symbol": selected.short_leg.contract.symbol,
                "ticker": selected.short_leg.contract.ticker,
                "expiration": selected.short_leg.contract.expiration,
                "strike": selected.short_leg.contract.strike,
                "option_type": selected.short_leg.contract.option_type.value,
            },
            "spread": {
                "symbol": selected.symbol,
                "width": selected.width,
                "entry_debit": selected.entry_debit,
                "max_profit": selected.max_profit,
                "structure": "call_debit_spread" if card.direction == Direction.BULLISH else "put_debit_spread",
            },
            "entry": entry,
            "opened_at": selected.long_leg.quote.timestamp.isoformat(),
            "target": card.target,
            "invalidation": card.invalidation,
            "latency": {
                "signal_to_entry_seconds": (datetime.now(timezone.utc) - card.captured_at).total_seconds(),
            },
        }
        created, reason = self.db.create_position_transactional(
            position_id=position_id,
            episode_id=episode_id,
            ticker=card.ticker,
            direction=card.direction.value,
            symbol=selected.symbol,
            quantity=int(entry["quantity"]),
            entry_price=float(entry["fill_price"]),
            status=status,
            payload=payload,
            max_open_positions=self.cfg.portfolio.maximum_open_positions,
            max_positions_per_ticker=self.cfg.portfolio.maximum_positions_per_ticker,
            max_new_positions_per_day=self.cfg.portfolio.maximum_new_positions_per_day,
            stop_after_daily_losses=self.cfg.portfolio.stop_after_daily_losses,
        )
        if not created:
            return {"status": "skipped", "reason": reason}
        self._record_contract_quote(position_id, episode_id, selected.long_leg.contract.symbol,
                                    "spread_long", selected.long_leg.quote, selected.long_leg.quote.timestamp)
        self._record_contract_quote(position_id, episode_id, selected.short_leg.contract.symbol,
                                    "spread_short", selected.short_leg.quote, selected.long_leg.quote.timestamp)
        self.quote_manager.subscribe([selected.long_leg.contract.symbol, selected.short_leg.contract.symbol, card.ticker])
        return {"status": status, "position_id": position_id, "entry_price": entry["fill_price"], "instrument_model": "debit_spread"}

    def monitor_once(self, now: datetime | None = None, recovery_reason: str | None = None) -> list[dict[str, Any]]:
        now = now or datetime.now(timezone.utc)
        results = []
        positions = self.db.open_positions(include_shadow=True)
        if positions:
            symbols = [symbol for p in positions for symbol in self._symbols_for_position_row(p)]
            self.quote_manager.subscribe(symbols)
            self.quote_manager.refresh(symbols)
        for row in positions:
            payload_json = json.loads(row["payload_json"])
            underlying_quote = self.quote_manager.fresh(row["ticker"], now)
            if payload_json.get("instrument_model") == "debit_spread":
                result = self._monitor_spread_position(row, payload_json, underlying_quote, now, recovery_reason)
                results.append(result)
                continue
            option_quote = self.quote_manager.fresh(row["symbol"], now)
            if not option_quote or not underlying_quote:
                self.quote_manager.refresh([row["symbol"], row["ticker"]])
                self.db.insert_system_event("STALE_QUOTE_RECOVERY", {"position_id": row["id"], "symbol": row["symbol"], "ticker": row["ticker"]})
                results.append({"position_id": row["id"], "status": "stale_quote"})
                continue
            position = self._position_from_row(row)
            underlying = underlying_quote.last or underlying_quote.midpoint
            pnl_pct = (option_quote.bid - position.entry_price) / position.entry_price * 100.0
            pnl_dollars = (option_quote.bid - position.entry_price) * 100 * position.quantity
            previous_peak = float(payload_json.get("peak_pnl_pct") or 0)
            peak = max(previous_peak, pnl_pct)
            mfe = max(float(payload_json.get("mfe_pct") or 0), pnl_pct)
            mae = min(float(payload_json.get("mae_pct") or 0), pnl_pct)
            mark = {
                "marked_at": now.isoformat(),
                "position_id": row["id"],
                "bid": option_quote.bid,
                "ask": option_quote.ask,
                "liquidation_value": option_quote.bid * 100 * position.quantity,
                "pnl_pct": pnl_pct,
                "pnl_dollars": pnl_dollars,
                "mfe_pct": mfe,
                "mae_pct": mae,
                "peak_pnl_pct": peak,
                "drawdown_from_peak_pct": peak - pnl_pct,
                "holding_seconds": (now - position.opened_at).total_seconds(),
                "underlying_price": underlying,
                "quote_age_seconds": (now - option_quote.timestamp.astimezone(timezone.utc)).total_seconds(),
                "feed_degraded": self.quote_manager.degraded,
            }
            self._record_contract_quote(row["id"], row.get("episode_id"), row["symbol"],
                                        "long_option", option_quote, now)
            self._record_contract_quote(row["id"], row.get("episode_id"), row["ticker"],
                                        "underlying", underlying_quote, now)
            self.db.insert_mark(row["id"], mark)
            reason = recovery_reason or exit_reason(position, option_quote, underlying, self.cfg.exit, now)
            if reason:
                exit_fill = simulate_exit(
                    option_quote,
                    self.cfg.simulation,
                    self.cfg.contract,
                    position.quantity,
                    self.cfg.market_data.quote_maximum_age_seconds,
                    now,
                )
                closed = self.db.close_position(row["id"], exit_fill.fill_price, reason, {**mark, "exit_fill": exit_fill})
                results.append({"position_id": row["id"], "closed": closed, "exit_reason": reason})
            else:
                results.append({"position_id": row["id"], "status": "marked"})
        return results

    def _monitor_spread_position(self, row: dict[str, Any], payload_json: dict[str, Any], underlying_quote: Quote | None, now: datetime, recovery_reason: str | None) -> dict[str, Any]:
        long_symbol = str((payload_json.get("long_contract") or {}).get("symbol") or "")
        short_symbol = str((payload_json.get("short_contract") or {}).get("symbol") or "")
        long_quote = self.quote_manager.fresh(long_symbol, now)
        short_quote = self.quote_manager.fresh(short_symbol, now)
        if not long_quote or not short_quote or not underlying_quote:
            self.quote_manager.refresh([s for s in (long_symbol, short_symbol, row["ticker"]) if s])
            self.db.insert_system_event("STALE_QUOTE_RECOVERY", {"position_id": row["id"], "symbol": row["symbol"], "ticker": row["ticker"]})
            return {"position_id": row["id"], "status": "stale_quote"}
        position = self._position_from_row(row)
        underlying = underlying_quote.last or underlying_quote.midpoint
        spread_bid = max(0.0, round(long_quote.bid - short_quote.ask, 4))
        spread_ask = max(0.0, round(long_quote.ask - short_quote.bid, 4))
        pnl_pct = (spread_bid - position.entry_price) / position.entry_price * 100.0
        pnl_dollars = (spread_bid - position.entry_price) * 100 * position.quantity
        previous_peak = float(payload_json.get("peak_pnl_pct") or 0)
        peak = max(previous_peak, pnl_pct)
        mfe = max(float(payload_json.get("mfe_pct") or 0), pnl_pct)
        mae = min(float(payload_json.get("mae_pct") or 0), pnl_pct)
        mark = {
            "marked_at": now.isoformat(),
            "position_id": row["id"],
            "bid": spread_bid,
            "ask": spread_ask,
            "liquidation_value": spread_bid * 100 * position.quantity,
            "pnl_pct": pnl_pct,
            "pnl_dollars": pnl_dollars,
            "mfe_pct": mfe,
            "mae_pct": mae,
            "peak_pnl_pct": peak,
            "drawdown_from_peak_pct": peak - pnl_pct,
            "holding_seconds": (now - position.opened_at).total_seconds(),
            "underlying_price": underlying,
            "long_quote": long_quote,
            "short_quote": short_quote,
            "feed_degraded": self.quote_manager.degraded,
        }
        self._record_contract_quote(row["id"], row.get("episode_id"), long_symbol, "spread_long", long_quote, now)
        self._record_contract_quote(row["id"], row.get("episode_id"), short_symbol, "spread_short", short_quote, now)
        self._record_contract_quote(row["id"], row.get("episode_id"), row["ticker"], "underlying", underlying_quote, now)
        self.db.insert_mark(row["id"], mark)
        synthetic_quote = Quote(row["symbol"], spread_bid, spread_ask if spread_ask > spread_bid else spread_bid + 0.01, now)
        reason = recovery_reason or exit_reason(position, synthetic_quote, underlying, self.cfg.exit, now)
        if reason:
            exit_fill = simulate_spread_exit(
                long_quote,
                short_quote,
                self.cfg.simulation,
                self.cfg.contract,
                position.quantity,
                self.cfg.market_data.quote_maximum_age_seconds,
                now,
            )
            closed = self.db.close_position(row["id"], float(exit_fill["fill_price"]), reason, {**mark, "exit_fill": exit_fill})
            return {"position_id": row["id"], "closed": closed, "exit_reason": reason}
        return {"position_id": row["id"], "status": "marked"}

    def drain_for_tests(self, max_steps: int = 100) -> None:
        for _ in range(max_steps):
            did = False
            try:
                batch_id = self.batch_queue.get_nowait()
                self.process_batch_once(batch_id)
                did = True
            except queue.Empty:
                pass
            try:
                item = self.entry_queue.get_nowait()
                self.process_entry_once(item)
                did = True
            except queue.Empty:
                pass
            if not did:
                return

    def _guarded_loop(self, name: str, target) -> None:
        state = self.states[name]
        backoff = 0.25
        while not self.shutdown_event.is_set():
            state.running = True
            try:
                target()
                state.processed += 1
                state.last_error = None
                backoff = 0.25
            except Exception as exc:
                state.last_error = str(exc)
                state.restarts += 1
                self.db.insert_system_event("WORKER_ERROR", {"worker": name, "error": str(exc)})
                time.sleep(min(backoff, 5.0))
                backoff = min(backoff * 2, 5.0)
        state.running = False

    def _batch_loop(self) -> None:
        try:
            batch_id = self.batch_queue.get(timeout=0.25)
        except queue.Empty:
            return
        self.process_batch_once(batch_id)

    def _entry_loop(self) -> None:
        try:
            item = self.entry_queue.get(timeout=0.25)
        except queue.Empty:
            return
        self.process_entry_once(item)

    def _monitor_loop(self) -> None:
        self.monitor_once()
        time.sleep(0.5)

    def _forward_loop(self) -> None:
        try:
            item_id = self.forward_queue.get(timeout=0.25)
        except queue.Empty:
            return
        self.forwarder.attempt(item_id)

    @staticmethod
    def _dte(expiration: str, as_of: datetime | None = None) -> int:
        from .contract_selector import dte

        return dte(expiration, as_of)

    def _record_contract_quote(self, position_id: str, episode_id: str | None, symbol: str,
                               role: str, quote: Quote, captured_at: datetime) -> None:
        self.db.insert_contract_mark(
            position_id=position_id, episode_id=episode_id, symbol=symbol, role=role,
            quote=quote, captured_at=captured_at,
            source=type(self.market_data).__name__,
        )

    @staticmethod
    def _symbols_for_position_row(row: dict[str, Any]) -> list[str]:
        payload = json.loads(row["payload_json"])
        if payload.get("instrument_model") == "debit_spread":
            return [
                str((payload.get("long_contract") or {}).get("symbol") or ""),
                str((payload.get("short_contract") or {}).get("symbol") or ""),
                row["ticker"],
            ]
        return [row["symbol"], row["ticker"]]

    @staticmethod
    def _position_from_row(row: dict[str, Any]) -> PaperPosition:
        payload = json.loads(row["payload_json"])
        opened_at = datetime.fromisoformat(row["opened_at"])
        return PaperPosition(
            id=row["id"],
            ticker=row["ticker"],
            direction=Direction(row["direction"]),
            contract_symbol=row["symbol"],
            quantity=int(row["quantity"]),
            entry_price=float(row["entry_price"]),
            opened_at=opened_at,
            target=float(payload.get("target") or payload.get("target_underlying") or 0),
            invalidation=float(payload.get("invalidation") or 0),
            status=row["status"],
            peak_pnl_pct=float(payload.get("peak_pnl_pct") or 0),
            mfe_pct=float(payload.get("mfe_pct") or 0),
            mae_pct=float(payload.get("mae_pct") or 0),
        )
