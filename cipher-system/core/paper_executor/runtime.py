from __future__ import annotations

import json
import math
import hashlib
from pathlib import Path
import queue
import threading
import time
from dataclasses import dataclass, asdict, replace
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .config import ExecutorConfig, ExitConfig, SimulationConfig
from .alpaca_core_market_data import AlpacaCoreMarketData
from .contract_selector import contracts_from_chain, select_contract, select_debit_spread
from .database import PaperExecutorDatabase
from .episode_tracker import EpisodeTracker
from .fill_simulator import simulate_entry, simulate_exit, simulate_spread_entry, simulate_spread_exit, validate_pair, validate_quote
from .ingestion import normalize_batch
from .models import Direction, Lifecycle, Mode, PaperPosition, Quote, sha256_id
from .models import TradeIntent
from .alpaca_paper_broker import AlpacaPaperBroker, intent_payload
from .policy import eligibility_skip
from .position_manager import exit_reason
from .quote_manager import MarketDataClient, QuoteManager
from .risk_guard import RiskGuard
from .tradier_market_data import TradierMarketData
from .validation import validate_card
from .vm_forwarder import VmForwarder
from .exchange_calendar import closing_reason, session_close, ET


# A simulated-fill rejection must become a classified entry decision, never a
# generic worker exception. This maps fill-simulator ValueError messages to the
# same SkipReason vocabulary used everywhere else on the entry path.
SIMULATION_SKIP_REASONS = {
    "stale quote": "SKIPPED_STALE_QUOTE",
    "invalid quote": "SKIPPED_MARKET_DATA_UNAVAILABLE",
    "invalid spread debit": "SKIPPED_NO_CONTRACT",
    "wide spread": "SKIPPED_WIDE_SPREAD",
    "max cost": "SKIPPED_MAX_COST",
    "debit exceeds width": "SKIPPED_NO_CONTRACT",
}

# After a paper-broker exit submission fails or expires unfilled, hold off
# resubmitting for this many seconds so the 0.5 s monitor loop cannot hammer
# the broker API. The position stays open and continues to be marked.
EXIT_RETRY_BACKOFF_SECONDS = 30.0


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
        broker: Any | None = None,
        clock=None,
    ):
        self.cfg = cfg
        self.db = db
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        implementation = hashlib.sha256()
        for name in ("runtime.py", "contract_selector.py", "fill_simulator.py", "database.py", "position_manager.py", "cohorts.py", "config.py", "exchange_calendar.py", "../exchange_calendar.py", "cohort_evaluation.py", "promotion_gate.py", "quote_manager.py", "risk_guard.py", "validation.py", "policy.py", "episode_tracker.py", "models.py", "alpaca_core_market_data.py"):
            implementation.update((Path(__file__).parent / name).read_bytes())
        self.config_hash = sha256_id("config", {"configuration": asdict(cfg), "implementation_sha256": implementation.hexdigest()})
        self.market_data = market_data or (
            AlpacaCoreMarketData(cfg.market_data)
            if cfg.market_data.provider == "alpaca_core"
            else TradierMarketData(cfg.market_data)
        )
        self.quote_manager = QuoteManager(cfg, self.market_data, clock=self.clock)
        self.forwarder = forwarder or VmForwarder(db, cfg.runtime_root / "queue" / "vm_pending", cfg.vm_forwarding.endpoint)
        self.broker = broker if broker is not None else (
            AlpacaPaperBroker.from_environment() if cfg.execution.backend == "alpaca_paper" else None
        )
        self.broker_readiness: dict[str, Any] = {
            "backend": cfg.execution.backend,
            "ready": cfg.execution.backend == "simulated",
            "paper_only": True,
            "last_error": None,
            "account": None,
            "unknown_positions": [],
            "owned_orphan_positions": [],
        }
        self.episodes = EpisodeTracker(db, cfg.scanner.episode_cooldown_minutes)
        self.risk = RiskGuard(cfg)
        self._exit_retry_after: dict[str, float] = {}
        self._next_due_forward_check = 0.0
        self._next_reconcile_check = 0.0
        self.mode = cfg.safety.default_start_mode
        self.reconciliation_passed = False
        self.database_integrity_ok = False
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
        self.database_integrity_ok = healthy
        with self.db.connect() as db:
            db.execute("update signal_batches set status = 'RECEIVED_RECOVERED' where status = 'PROCESSING'")
            pending_batches = db.execute("select id from signal_batches where status like 'RECEIVED%' order by received_at limit 500").fetchall()
        for batch in pending_batches:
            self.enqueue_batch(batch["id"])
        # A crash after a batch commit but before entry processing must not
        # strand its fresh eligible card in an in-memory queue.
        with self.db.connect() as db:
            queued_cards = db.execute("""select c.id,c.raw_json,u.episode_id,
                exists(select 1 from system_events e where e.event_type='ENTRY_QUEUED'
                       and json_extract(e.payload_json,'$.card_id')=c.id) committed_entry
                from signal_cards c
                join episode_updates u on u.card_id=c.id
                where c.status in ('ELIGIBLE','DUPLICATE')
                  and not exists(select 1 from paper_positions p where p.episode_id=u.episode_id)
                  and not exists(select 1 from system_events e where e.event_type='ENTRY_BLOCKED'
                    and json_extract(e.payload_json,'$.episode_id')=u.episode_id)
                order by c.captured_at desc limit 500""").fetchall()
        restored = set()
        for row in queued_cards:
            if self.cfg.experiment.confirmation_observations > 1 and not row["committed_entry"]:
                continue
            card, _ = validate_card(json.loads(row["raw_json"]), self.cfg, self.clock())
            if card is not None and row["episode_id"] not in restored:
                self.entry_queue.put_nowait({"episode_id": row["episode_id"], "card_id": row["id"], "card": card})
                restored.add(row["episode_id"])
        open_positions = self.db.open_positions(include_shadow=True)
        ended_orphaned_episodes = self.db.end_orphaned_episodes()
        symbols: list[str] = []
        for pos in open_positions:
            symbols.extend(self._symbols_for_position_row(pos))
        self.quote_manager.subscribe(symbols)
        for item_id in self.db.due_forward_items(datetime.now(timezone.utc).isoformat()):
            try:
                self.forward_queue.put_nowait(item_id)
            except queue.Full:
                break
        self.reconciliation_passed = healthy and self._reconcile_broker()
        self.db.insert_system_event(
            "RECOVERED",
            {
                "database_healthy": healthy,
                "open_positions": len(open_positions),
                "ended_orphaned_episodes": ended_orphaned_episodes,
                "subscriptions": sorted(set(symbols)),
                "effective_mode": self.mode.value,
                "execution_backend": self.cfg.execution.backend,
                "broker_reconciliation": self.broker_readiness,
            },
        )

    def start(self) -> None:
        if self.cfg.execution.auto_promote_paper:
            self._next_reconcile_check = time.monotonic() + 60.0
        for name, target in (
            ("batch", self._batch_loop),
            ("entry", self._entry_loop),
            ("monitor", self._monitor_loop),
            ("forward", self._forward_loop),
        ):
            thread = threading.Thread(target=self._guarded_loop, args=(name, target), name=f"paper-executor-{name}", daemon=True)
            thread.start()
            self.threads.append(thread)
        if self.cfg.execution.auto_promote_paper:
            for _ in range(20):
                if all(state.running for state in self.states.values()):
                    break
                time.sleep(0.05)
            ok, reason = self.promote_to_paper()
            self.db.insert_system_event("AUTO_PAPER_PROMOTION", {"ok": ok, "reason": reason})

    def stop(self, timeout: float = 5.0) -> None:
        self.shutdown_event.set()
        for thread in self.threads:
            thread.join(timeout=timeout)
        self.db.insert_system_event("SHUTDOWN", {"queues": self.queue_depths()})

    def health(self) -> dict[str, Any]:
        operational = self.db.operational_snapshot()
        market_date = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
        worker_errors = [state.last_error for state in self.states.values() if state.last_error]
        market_data = (
            self.market_data.status()
            if callable(getattr(self.market_data, "status", None))
            else {
                "provider": type(self.market_data).__name__,
                "provider_session_ready": None,
                "market_data_ready": self.quote_manager.last_fresh_quote_at is not None,
                "last_chain_success_at": None,
                "last_error": None,
            }
        )
        blocker = None
        if self.cfg.kill_switch_path.exists():
            blocker = "SKIPPED_KILL_SWITCH"
        elif market_data.get("last_error"):
            blocker = str(market_data["last_error"].get("reason") or "MARKET_DATA_UNAVAILABLE")
        elif self.quote_manager.degraded and self.quote_manager.active_symbols:
            blocker = "SKIPPED_DATA_FEED_DEGRADED"
        execution_ready = (
            self.database_integrity_ok
            and not worker_errors
            and blocker is None
            and (
                self.cfg.execution.backend == "simulated"
                or (self.mode == Mode.PAPER and self.reconciliation_passed and self.broker_readiness.get("ready"))
            )
        )
        now = self.clock()
        positions = self.db.open_positions()
        pending_exits = [row["id"] for row in positions if json.loads(row["payload_json"]).get("pending_exit")]
        exit_covered = all(all(self.quote_manager.fresh(symbol, now) is not None
                              for symbol in self._symbols_for_position_row(row) if symbol != row["ticker"])
                           for row in positions)
        last_quote = self.quote_manager.last_fresh_quote_at
        observed_fresh = bool(last_quote and -2 <= (now - datetime.fromisoformat(last_quote)).total_seconds() <= self.cfg.market_data.quote_maximum_age_seconds)
        local = now.astimezone(ET)
        window_open = (session_close(local.date()) is not None
                       and (self.cfg.strategy.entry_window_et_start or "09:30") <= local.strftime("%H:%M")
                       < (self.cfg.strategy.entry_window_et_end or "16:00"))
        daily_counts = self.db.session_counts(now)
        capacity = (daily_counts["new_positions"] < self.cfg.portfolio.maximum_new_positions_per_day
                    and daily_counts["stopped_trades"] < self.cfg.portfolio.stop_after_daily_losses
                    and len(positions) < self.cfg.portfolio.maximum_open_positions)
        return {
            "ready": bool(execution_ready),
            "experiment": {**asdict(self.cfg.experiment), "config_hash": self.config_hash},
            "readiness": {
                "process_healthy": self.database_integrity_ok and all(s.running and not s.last_error for s in self.states.values()),
                "data_ready": observed_fresh and not self.quote_manager.degraded,
                "entry_eligible": bool(execution_ready and observed_fresh and window_open and capacity and self.mode == Mode.PAPER),
                "exit_quotes_covered": exit_covered, "pending_exit_positions": pending_exits,
            },
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
            "market_data_readiness": market_data,
            "paper_broker": self.broker_readiness,
            "execution": {
                "backend": self.cfg.execution.backend,
                "portfolio_kind": "cipher_local_paper" if self.cfg.execution.backend == "simulated" else "external_paper_account",
                "external_order_capability": self.cfg.execution.backend == "alpaca_paper",
                "auto_promote_paper": self.cfg.execution.auto_promote_paper,
                "paper_forward_test_authorized": self.cfg.execution.paper_forward_test_authorized,
            },
            "observability": {
                "configured_mode": self.cfg.safety.default_start_mode.value,
                "effective_mode": self.mode.value,
                "database_integrity_ok": self.database_integrity_ok,
                "last_batch_at": operational["last_batch_at"],
                "last_episode_at": operational["last_episode_at"],
                "last_market_data_quote_at": self.quote_manager.last_fresh_quote_at,
                "last_mark_at": operational["last_mark_at"],
                "open_shadow_positions": operational["counts"]["open_shadow_positions"],
                "open_paper_positions": operational["counts"]["open_paper_positions"],
                "vm_forward_backlog": operational["counts"]["forward_backlog"],
                "last_worker_exception": worker_errors[-1] if worker_errors else operational["last_worker_exception"],
                "last_entry_block": operational["last_entry_block"],
                "entry_blocked_reason": blocker,
                "counts": operational["counts"],
                "session": {"market_date": market_date, **self.db.session_snapshot(market_date)},
                "portfolio": self.db.portfolio_snapshot(self.cfg.portfolio.starting_cash),
                "uptime_seconds": round((datetime.now(timezone.utc) - self.started_at).total_seconds(), 3),
            },
        }

    def queue_depths(self) -> dict[str, int]:
        return {
            "batch": self.batch_queue.qsize(),
            "entry": self.entry_queue.qsize(),
            "forward": self.forward_queue.qsize(),
        }

    def _entry_evidence(self, card, quotes):
        return {"cohort_id": self.cfg.experiment.cohort_id, "version": self.cfg.experiment.version,
                "config_hash": self.config_hash, "decision_at": self.clock().isoformat(),
                "execution_assumptions": asdict(self.cfg.simulation), "registry_strategy_id": self.cfg.experiment.registry_strategy_id or card.raw.get("registry_strategy_id"),
                "contract_policy": asdict(self.cfg.contract), "quote_maximum_age_seconds": self.cfg.market_data.quote_maximum_age_seconds,
                "signal": asdict(card), "quotes": [asdict(q) for q in quotes]}

    def _current_entry_check(self, card, underlying=None):
        now = self.clock()
        age = (now - card.captured_at).total_seconds()
        if not -2 <= age <= self.cfg.scanner.maximum_signal_age_seconds:
            return "SKIPPED_STALE_SIGNAL"
        skip = eligibility_skip(replace(card, captured_at=now), self.cfg, self.cfg.kill_switch_path.exists(), False)
        if skip:
            return skip.value
        close = session_close(now.astimezone(ET).date())
        if close is None or now >= close:
            return "SKIPPED_ENTRY_WINDOW"
        if underlying is not None:
            from .fill_simulator import quote_is_fresh
            if not quote_is_fresh(underlying, self.cfg.market_data.quote_maximum_age_seconds, now):
                return "SKIPPED_STALE_QUOTE"
            spot = underlying.last or underlying.midpoint
            if not math.isfinite(spot) or not (card.invalidation < spot < card.target if card.direction == Direction.BULLISH else card.target < spot < card.invalidation):
                return "SKIPPED_INVALID_GEOMETRY"
        return None

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
        explicitly_authorized = self.cfg.execution.paper_forward_test_authorized
        if not gate["eligible_count"] and not explicitly_authorized:
            return False, "no strategy has cleared the FAST_BACKTESTED promotion gate"
        if not self.reconciliation_passed or not self.db.integrity_ok():
            return False, "database reconciliation has not passed"
        if self.cfg.execution.backend == "alpaca_paper" and not self.broker_readiness.get("ready"):
            return False, "Alpaca paper account reconciliation has not passed"
        if self.quote_manager.degraded:
            return False, "quote feed is degraded"
        if self.cfg.kill_switch_path.exists():
            return False, "kill switch is active"
        if any(not state.running or state.last_error for state in self.states.values()):
            return False, "workers are not healthy"
        self.mode = Mode.PAPER
        return True, "paper"

    def _reconcile_broker(self) -> bool:
        if self.cfg.execution.backend == "simulated":
            return True
        if self.broker is None:
            self.broker_readiness.update(ready=False, last_error="Alpaca paper broker is not configured")
            return False
        try:
            account = self.broker.account()
            positions = self.broker.positions()
            recent_orders = self.broker.orders(status="all", limit=20) if callable(getattr(self.broker, "orders", None)) else []
            local_symbols = {str(row["symbol"]) for row in self.db.open_positions(include_shadow=False)}
            broker_symbols = {str(row.get("symbol") or "") for row in positions if row.get("symbol")}
            unknown = sorted(broker_symbols - local_symbols)
            known_owned_symbols = {
                str(row.get("symbol") or "")
                for row in self.db.rows("paper_orders")
                if str(row.get("symbol") or "")
            }
            owned_orphans = sorted(set(unknown).intersection(known_owned_symbols))
            ready = (
                account.get("status") == "ACTIVE"
                and not account.get("trading_blocked")
                and not account.get("account_blocked")
                and not owned_orphans
            )
            self.broker_readiness.update(
                ready=ready,
                last_error=None if ready else "Owned orphan position or blocked paper account",
                account={
                    "status": account.get("status"), "currency": account.get("currency"),
                    "equity": account.get("equity"), "buying_power": account.get("buying_power"),
                    "paper_only": True,
                },
                unknown_positions=unknown,
                owned_orphan_positions=owned_orphans,
                recent_orders=[{
                    key: row.get(key) for key in (
                        "id", "client_order_id", "symbol", "side", "quantity", "limit_price",
                        "status", "filled_quantity", "average_fill_price", "submitted_at", "filled_at",
                    )
                } for row in recent_orders[:20]],
            )
            return ready
        except Exception as exc:
            self.broker_readiness.update(ready=False, last_error=str(exc)[:300])
            return False

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
        now = self.clock()
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
            # A prior refresh failure is not permanent evidence that the next
            # card's feed is unavailable. The entry path below performs its own
            # fail-closed chain and quote refresh, which is also the only path
            # capable of clearing a transient degraded state.
            skip = eligibility_skip(card, self.cfg, self.cfg.kill_switch_path.exists(), False)
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
            confirmed = not duplicate
            if self.cfg.experiment.confirmation_observations > 1:
                with self.db.connect() as conn:
                    traded = conn.execute("select 1 from paper_positions where episode_id=?", (episode_id,)).fetchone()
                    previous = conn.execute("select id from signal_batches where rowid < (select rowid from signal_batches where id=?) order by rowid desc limit ?",
                                            (batch_id, self.cfg.experiment.confirmation_observations - 1)).fetchall()
                    stamps = [card.captured_at]
                    for prior in previous:
                        matching = conn.execute("select captured_at from signal_cards where batch_id=? and ticker=? and direction=? and status in ('ELIGIBLE','DUPLICATE','VALIDATED') order by captured_at desc limit 1",
                                                (prior["id"], card.ticker, card.direction.value)).fetchone()
                        if not matching:
                            break
                        stamp = datetime.fromisoformat(matching["captured_at"])
                        if not 0 < (stamps[-1] - stamp).total_seconds() <= 450:
                            break
                        stamps.append(stamp)
                confirmed = len(stamps) >= self.cfg.experiment.confirmation_observations and not traded
            if confirmed:
                self.db.insert_system_event("ENTRY_QUEUED", {"card_id": card_id, "episode_id": episode_id})
                self.entry_queue.put({"episode_id": episode_id, "card": card, "card_id": card_id, "signal_received_at": row["received_at"]})
            results.append({"card_id": card_id, "episode_id": episode_id, "duplicate_episode": duplicate})
        self.db.update_batch_status(batch_id, "PROCESSED")
        return results

    def process_entry_once(self, item: dict[str, Any]) -> dict[str, Any]:
        card = item["card"]
        episode_id = item["episode_id"]
        check = self._current_entry_check(card)
        if check:
            return self._entry_block(episode_id, card, check)
        if self.mode == Mode.DISABLED:
            return self._entry_block(episode_id, card, "SKIPPED_MODE_DISABLED")
        if self.cfg.kill_switch_path.exists():
            return self._entry_block(episode_id, card, "SKIPPED_KILL_SWITCH")
        if (
            self.cfg.execution.backend == "alpaca_paper"
            and self.mode == Mode.PAPER
            and (not self.reconciliation_passed or not self.broker_readiness.get("ready"))
        ):
            return self._entry_block(episode_id, card, "SKIPPED_BROKER_RECONCILIATION")
        counts = self.db.session_counts(self.clock(), card.ticker)
        if counts["ticker_entries"] >= self.cfg.portfolio.maximum_new_positions_per_ticker_per_day:
            return self._entry_block(episode_id, card, "SKIPPED_TICKER_DAILY_LIMIT")
        risk_skip = self.risk.entry_skip(
            mode=self.mode,
            kill_switch=False,
            open_positions=[self._position_from_row(row) for row in self.db.open_positions()],
            ticker=card.ticker,
            new_positions_today=counts["new_positions"],
            stopped_today=counts["stopped_trades"],
        )
        if risk_skip:
            return self._entry_block(episode_id, card, risk_skip.value)
        try:
            expirations = [
                exp for exp in self.market_data.expirations(card.ticker)
                if self.cfg.contract.minimum_dte
                <= self._dte(exp, self.clock())
                <= self.cfg.contract.maximum_dte
            ]
        except RuntimeError as exc:
            return self._entry_block(episode_id, card, "SKIPPED_MARKET_DATA_UNAVAILABLE", str(exc))
        if not expirations:
            return self._entry_block(episode_id, card, "SKIPPED_NO_CONTRACT")
        contracts = []
        try:
            for expiration in expirations:
                contracts.extend(contracts_from_chain(card.ticker, self.market_data.chain(card.ticker, expiration), card.option_type))
        except RuntimeError as exc:
            return self._entry_block(episode_id, card, "SKIPPED_MARKET_DATA_UNAVAILABLE", str(exc))
        symbols = [c.symbol for c in contracts]
        quotes = self.quote_manager.refresh([*symbols, card.ticker])
        if card.ticker not in quotes:
            return self._entry_block(episode_id, card, "SKIPPED_MARKET_DATA_UNAVAILABLE", "underlying quote missing")
        check = self._current_entry_check(card, quotes.get(card.ticker))
        if check:
            return self._entry_block(episode_id, card, check, "stale quote" if check == "SKIPPED_STALE_QUOTE" else None)
        # Moneyness is determined by the observed execution-time underlying.
        card = replace(card, spot=quotes[card.ticker].last or quotes[card.ticker].midpoint)
        if symbols and not quotes:
            return self._entry_block(
                episode_id, card, "SKIPPED_MARKET_DATA_UNAVAILABLE",
                self.quote_manager.last_error or "No fresh option quotes returned",
            )
        if self.cfg.instrument.model == "debit_spread":
            return self._process_spread_entry(card, episode_id, contracts, quotes)
        selected, candidates = select_contract(
            card,
            contracts,
            quotes,
            self.cfg.contract,
            now=self.clock(),
        )
        self.db.persist_candidates(episode_id, candidates)
        if not selected or not selected.quote:
            return self._entry_block(episode_id, card, "SKIPPED_NO_CONTRACT")
        try:
            entry = simulate_entry(
                selected.quote,
                self.cfg.simulation,
                self.cfg.contract,
                self.cfg.portfolio.quantity_per_trade,
                self.cfg.market_data.quote_maximum_age_seconds,
                self.clock(),
            )
        except ValueError as exc:
            reason = SIMULATION_SKIP_REASONS.get(str(exc), "SKIPPED_MARKET_DATA_UNAVAILABLE")
            return self._entry_block(episode_id, card, reason, str(exc))
        position_id = sha256_id("position", {"episode_id": episode_id, "symbol": selected.contract.symbol})
        status = "SHADOW_OPEN" if self.mode == Mode.SHADOW else "OPEN"
        order_id = sha256_id("paper_order", {"position": position_id, "side": "BUY_TO_OPEN"})
        entry_price = entry.fill_price
        opened_at = self.clock().isoformat()
        broker_order: dict[str, Any] | None = None
        if self.mode == Mode.PAPER and self.cfg.execution.backend == "alpaca_paper":
            intent = TradeIntent(
                decision_id=f"{episode_id}:BUY_TO_OPEN",
                evidence_snapshot_id=str(card.raw.get("evidence_snapshot_id") or "") or None,
                ticker=card.ticker,
                contract_symbol=selected.contract.symbol,
                side="buy",
                quantity=entry.quantity,
                limit_price=round(selected.quote.ask, 2),
                target=card.target,
                invalidation=card.invalidation,
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=self.cfg.execution.order_timeout_seconds),
            )
            self.db.insert_order(
                order_id=order_id, episode_id=episode_id, position_id=position_id,
                side="BUY_TO_OPEN", symbol=selected.contract.symbol, quantity=entry.quantity,
                status="PENDING_SUBMISSION", fill={"intent": intent_payload(intent)},
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            try:
                submitted = self.broker.submit(intent)
                self.db.update_order(order_id, submitted["status"].upper(), {"intent": intent_payload(intent), "broker": submitted})
                broker_order = self.broker.wait_for_terminal(
                    submitted["id"], self.cfg.execution.order_timeout_seconds,
                    self.cfg.execution.poll_interval_seconds,
                )
                self.db.update_order(order_id, broker_order["status"].upper(), {"intent": intent_payload(intent), "broker": broker_order})
            except Exception as exc:
                self.db.update_order(order_id, "SUBMISSION_FAILED", {"intent": intent_payload(intent), "error": str(exc)[:300], "paper_only": True})
                return self._entry_block(episode_id, card, "SKIPPED_PAPER_BROKER_UNAVAILABLE", str(exc))
            if broker_order.get("status") != "filled" or broker_order.get("average_fill_price") is None:
                return self._entry_block(episode_id, card, "SKIPPED_PAPER_ORDER_UNFILLED", broker_order.get("status"))
            entry_price = float(broker_order["average_fill_price"])
            opened_at = str(broker_order.get("filled_at") or datetime.now(timezone.utc).isoformat())
        payload = {
            "episode_id": episode_id,
            "mode": self.mode.value,
            "ticker": card.ticker,
            "direction": card.direction.value,
            "contract": asdict(selected.contract),
            "entry": asdict(entry),
            "entry_evidence": {**self._entry_evidence(card, [selected.quote]), "decision_at": opened_at},
            "exit_policy": asdict(self.cfg.exit),
            "broker_order": broker_order,
            "execution_backend": self.cfg.execution.backend,
            "opened_at": opened_at,
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
            entry_price=entry_price,
            status=status,
            payload=payload,
            max_open_positions=self.cfg.portfolio.maximum_open_positions,
            max_positions_per_ticker=self.cfg.portfolio.maximum_positions_per_ticker,
            max_new_positions_per_day=self.cfg.portfolio.maximum_new_positions_per_day,
            max_new_positions_per_ticker_per_day=self.cfg.portfolio.maximum_new_positions_per_ticker_per_day,
            stop_after_daily_losses=self.cfg.portfolio.stop_after_daily_losses,
            starting_cash=self.cfg.portfolio.starting_cash,
            entry_order=None if broker_order else dict(order_id=order_id, episode_id=episode_id, position_id=position_id, side="BUY_TO_OPEN", symbol=selected.contract.symbol, quantity=entry.quantity, status="SIMULATED_FILLED", fill={"price": entry.fill_price, "basis": "ask_plus_slippage", "paper_only": True}, created_at=opened_at),
        )
        if not created:
            if broker_order and broker_order.get("status") == "filled":
                self.db.insert_system_event("BROKER_ORPHANED_FILL", {"episode_id": episode_id, "position_id": position_id, "broker_order": broker_order})
                self.reconciliation_passed = False
            return self._entry_block(episode_id, card, reason or "SKIPPED_POSITION_CREATE")
        self._record_contract_quote(position_id, episode_id, selected.contract.symbol,
                                    "long_option", selected.quote, selected.quote.timestamp)
        self.quote_manager.subscribe([selected.contract.symbol, card.ticker])
        return {"status": status, "position_id": position_id, "entry_price": entry_price, "execution_backend": self.cfg.execution.backend}

    def _entry_block(self, episode_id: str, card: Any, reason: str, error: str | None = None) -> dict[str, Any]:
        payload = {
            "episode_id": episode_id, "ticker": card.ticker,
            "direction": card.direction.value, "setup": card.setup, "reason": reason,
            "entry_evidence": self._entry_evidence(card, []),
        }
        if error:
            payload["error"] = error[:300]
        self.db.insert_system_event("ENTRY_BLOCKED", payload)
        # A rejected attempt is terminal for this episode. Leaving it open made
        # every later observation a duplicate, so a single transient data
        # failure could suppress valid entries for the rest of the session.
        self.db.end_episode(episode_id)
        return {"status": "skipped", "reason": reason, **({"error": error[:300]} if error else {})}

    def _process_spread_entry(self, card, episode_id: str, contracts: list, quotes: dict[str, Quote]) -> dict[str, Any]:
        selected, leg_candidates, spread_candidates = select_debit_spread(
            card,
            contracts,
            quotes,
            self.cfg.contract,
            minimum_width=self.cfg.instrument.minimum_spread_width,
            maximum_width=self.cfg.instrument.maximum_spread_width,
            now=self.clock(),
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
            return self._entry_block(episode_id, card, "SKIPPED_NO_CONTRACT")
        try:
            entry = simulate_spread_entry(
                selected.long_leg.quote,
                selected.short_leg.quote,
                self.cfg.simulation,
                self.cfg.contract,
                self.cfg.portfolio.quantity_per_trade,
                self.cfg.market_data.quote_maximum_age_seconds,
                self.clock(),
                width=selected.width,
            )
            fraction = self.cfg.experiment.maximum_round_trip_stop_fraction
            if fraction is not None:
                immediate = simulate_spread_exit(selected.long_leg.quote, selected.short_leg.quote, self.cfg.simulation, self.cfg.contract, int(entry["quantity"]), self.cfg.market_data.quote_maximum_age_seconds, self.clock())
                if entry["fill_price"] - immediate["fill_price"] > entry["fill_price"] * self.cfg.exit.stop_loss_pct / 100 * fraction:
                    return self._entry_block(episode_id, card, "SKIPPED_ROUND_TRIP_COST")
        except ValueError as exc:
            reason = SIMULATION_SKIP_REASONS.get(str(exc), "SKIPPED_MARKET_DATA_UNAVAILABLE")
            return self._entry_block(episode_id, card, reason, str(exc))
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
            "entry": {**entry, "long_fill": asdict(entry["long_fill"]), "short_fill": asdict(entry["short_fill"])},
            "entry_evidence": self._entry_evidence(card, [selected.long_leg.quote, selected.short_leg.quote]),
            "opened_at": self.clock().isoformat(),
            "exit_policy": asdict(self.cfg.exit),
            "take_profit_remaining_fraction": self.cfg.experiment.take_profit_remaining_fraction,
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
            max_new_positions_per_ticker_per_day=self.cfg.portfolio.maximum_new_positions_per_ticker_per_day,
            stop_after_daily_losses=self.cfg.portfolio.stop_after_daily_losses,
            starting_cash=self.cfg.portfolio.starting_cash,
            entry_order=dict(order_id=sha256_id("paper_order", {"position": position_id, "side": "DEBIT_OPEN"}), episode_id=episode_id, position_id=position_id, side="DEBIT_OPEN", symbol=selected.symbol, quantity=int(entry["quantity"]), status="SIMULATED_FILLED", fill={"price": entry["fill_price"], "basis": "long_ask_minus_short_bid_plus_slippage", "paper_only": True}, created_at=payload["opened_at"]),
        )
        if not created:
            return self._entry_block(episode_id, card, reason or "SKIPPED_POSITION_CREATE")
        self._record_contract_quote(position_id, episode_id, selected.long_leg.contract.symbol,
                                    "spread_long", selected.long_leg.quote, selected.long_leg.quote.timestamp)
        self._record_contract_quote(position_id, episode_id, selected.short_leg.contract.symbol,
                                    "spread_short", selected.short_leg.quote, selected.long_leg.quote.timestamp)
        self.quote_manager.subscribe([selected.long_leg.contract.symbol, selected.short_leg.contract.symbol, card.ticker])
        return {"status": status, "position_id": position_id, "entry_price": entry["fill_price"], "instrument_model": "debit_spread"}

    def monitor_once(self, now: datetime | None = None, recovery_reason: str | None = None) -> list[dict[str, Any]]:
        now = now or self.clock()
        results = []
        positions = self.db.open_positions(include_shadow=True)
        if positions:
            symbols = [symbol for p in positions for symbol in self._symbols_for_position_row(p)]
            self.quote_manager.subscribe(symbols)
            self.quote_manager.refresh(symbols)
        for row in positions:
            payload_json = json.loads(row["payload_json"])
            exit_cfg = ExitConfig(**payload_json.get("exit_policy", asdict(self.cfg.exit)))
            simulation = SimulationConfig(**payload_json.get("entry_evidence", {}).get("execution_assumptions", asdict(self.cfg.simulation)))
            underlying_quote = self.quote_manager.fresh(row["ticker"], now)
            if payload_json.get("instrument_model") == "debit_spread":
                result = self._monitor_spread_position(row, payload_json, underlying_quote, now, recovery_reason)
                results.append(result)
                continue
            option_quote = self.quote_manager.fresh(row["symbol"], now)
            if not option_quote:
                position = self._position_from_row(row)
                timed = closing_reason(position.opened_at, now, exit_cfg.force_close_time_et, exit_cfg.allow_overnight)
                if (now - position.opened_at).total_seconds() >= exit_cfg.maximum_hold_minutes * 60:
                    timed = timed or "maximum_holding_time"
                results.append(self._pending_exit(row, (payload_json.get("pending_exit") or {}).get("reason") or recovery_reason or timed, "stale_quote", now))
                continue
            position = self._position_from_row(row)
            try:
                liquidation_fill = simulate_exit(option_quote, simulation, self.cfg.contract, position.quantity, self.cfg.market_data.quote_maximum_age_seconds, now)
            except ValueError as exc:
                timed = closing_reason(position.opened_at, now, exit_cfg.force_close_time_et, exit_cfg.allow_overnight)
                if (now - position.opened_at).total_seconds() >= exit_cfg.maximum_hold_minutes * 60:
                    timed = timed or "maximum_holding_time"
                results.append(self._pending_exit(row, (payload_json.get("pending_exit") or {}).get("reason") or recovery_reason or timed, str(exc), now))
                continue
            underlying = (underlying_quote.last or underlying_quote.midpoint) if underlying_quote else None
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
                "liquidation_value": liquidation_fill.fill_price * 100 * position.quantity,
                "liquidation_basis": "validated_exit_fill_after_costs",
                "pnl_pct": pnl_pct,
                "pnl_dollars": pnl_dollars,
                "mfe_pct": mfe,
                "mae_pct": mae,
                "peak_pnl_pct": peak,
                "drawdown_from_peak_pct": peak - pnl_pct,
                "holding_seconds": (now - position.opened_at).total_seconds(),
                "underlying_price": underlying,
                "quote_age_seconds": max(0.0, (now - option_quote.timestamp.astimezone(timezone.utc)).total_seconds()),
                "feed_degraded": self.quote_manager.degraded,
            }
            self._record_contract_quote(row["id"], row.get("episode_id"), row["symbol"],
                                        "long_option", option_quote, now)
            self._record_contract_quote(row["id"], row.get("episode_id"), row["ticker"],
                                        "underlying", underlying_quote, now)
            self.db.insert_mark(row["id"], mark)
            pending = payload_json.get("pending_exit") or {}
            reason = pending.get("reason") or recovery_reason or exit_reason(position, option_quote, underlying, exit_cfg, now)
            if reason:
                if pending.get("retry_at") and now < datetime.fromisoformat(pending["retry_at"]):
                    results.append({"position_id": row["id"], "status": "exit_backoff", "exit_reason": reason})
                    continue
                if self.mode == Mode.PAPER and self.cfg.execution.backend == "alpaca_paper":
                    retry_key = f"{row['id']}:{reason}"
                    ready_at = self._exit_retry_after.get(retry_key)
                    if ready_at is not None and time.monotonic() < ready_at:
                        results.append({"position_id": row["id"], "closed": False, "exit_reason": reason, "status": "broker_exit_backoff"})
                        continue
                    broker_fill = self._paper_broker_exit(row, option_quote, reason, now)
                    if broker_fill is None:
                        self._exit_retry_after[retry_key] = time.monotonic() + EXIT_RETRY_BACKOFF_SECONDS
                        results.append({"position_id": row["id"], "closed": False, "exit_reason": reason, "status": "broker_exit_unfilled"})
                        continue
                    self._exit_retry_after.pop(retry_key, None)
                    closed = self.db.close_position(row["id"], broker_fill, reason, {**mark, "execution_backend": "alpaca_paper", "broker_fill_price": broker_fill})
                    if closed:
                        self._complete_position(row)
                    results.append({"position_id": row["id"], "closed": closed, "exit_reason": reason, "execution_backend": "alpaca_paper"})
                    continue
                try:
                    exit_fill = simulate_exit(option_quote, simulation, self.cfg.contract, position.quantity, self.cfg.market_data.quote_maximum_age_seconds, now)
                except ValueError as exc:
                    results.append(self._pending_exit(row, reason, str(exc), now))
                    continue
                order = dict(order_id=sha256_id("paper_order", {"position": row["id"], "side": "SELL_TO_CLOSE"}),
                             episode_id=row.get("episode_id"), position_id=row["id"], side="SELL_TO_CLOSE",
                             symbol=row["symbol"], quantity=position.quantity, status="SIMULATED_FILLED",
                             fill={"price": exit_fill.fill_price, "basis": "bid_minus_slippage", "paper_only": True}, created_at=now.isoformat())
                closed = self.db.close_position(row["id"], exit_fill.fill_price, reason, {**mark, "exit_fill": exit_fill}, exit_order=order)
                if closed:
                    self._complete_position(row)
                results.append({"position_id": row["id"], "closed": closed, "exit_reason": reason})
            else:
                results.append({"position_id": row["id"], "status": "marked"})
        return results

    def _paper_broker_exit(self, row: dict[str, Any], option_quote: Quote, reason: str, now: datetime) -> float | None:
        order_id = sha256_id("paper_order", {"position": row["id"], "side": "SELL_TO_CLOSE", "reason": reason})
        intent = TradeIntent(
            decision_id=f"{row['id']}:SELL_TO_CLOSE:{reason}", evidence_snapshot_id=None,
            ticker=row["ticker"], contract_symbol=row["symbol"], side="sell",
            quantity=int(row["quantity"]), limit_price=round(option_quote.bid, 2),
            target=None, invalidation=None,
            expires_at=now + timedelta(seconds=self.cfg.execution.order_timeout_seconds),
        )
        self.db.insert_order(
            order_id=order_id, episode_id=row.get("episode_id"), position_id=row["id"],
            side="SELL_TO_CLOSE", symbol=row["symbol"], quantity=int(row["quantity"]),
            status="PENDING_SUBMISSION", fill={"intent": intent_payload(intent)}, created_at=now.isoformat(),
        )
        try:
            submitted = self.broker.submit(intent)
            self.db.update_order(order_id, submitted["status"].upper(), {"intent": intent_payload(intent), "broker": submitted})
            final = self.broker.wait_for_terminal(
                submitted["id"], self.cfg.execution.order_timeout_seconds,
                self.cfg.execution.poll_interval_seconds,
            )
            self.db.update_order(order_id, final["status"].upper(), {"intent": intent_payload(intent), "broker": final})
        except Exception as exc:
            self.db.update_order(order_id, "SUBMISSION_FAILED", {"intent": intent_payload(intent), "error": str(exc)[:300], "paper_only": True})
            self.db.insert_system_event("PAPER_BROKER_EXIT_FAILED", {"position_id": row["id"], "reason": reason, "error": str(exc)[:300]})
            return None
        value = final.get("average_fill_price")
        return float(value) if final.get("status") == "filled" and value is not None else None

    def _monitor_spread_position(self, row: dict[str, Any], payload_json: dict[str, Any], underlying_quote: Quote | None, now: datetime, recovery_reason: str | None) -> dict[str, Any]:
        exit_cfg = ExitConfig(**payload_json.get("exit_policy", asdict(self.cfg.exit)))
        pending = payload_json.get("pending_exit") or {}
        position = self._position_from_row(row)
        timed = closing_reason(position.opened_at, now, exit_cfg.force_close_time_et, exit_cfg.allow_overnight, (payload_json.get("long_contract") or {}).get("expiration"))
        if not timed and (now - position.opened_at).total_seconds() >= exit_cfg.maximum_hold_minutes * 60:
            timed = "maximum_holding_time"
        recovery_reason = pending.get("reason") or recovery_reason or timed
        long_symbol = str((payload_json.get("long_contract") or {}).get("symbol") or "")
        short_symbol = str((payload_json.get("short_contract") or {}).get("symbol") or "")
        long_quote = self.quote_manager.fresh(long_symbol, now)
        short_quote = self.quote_manager.fresh(short_symbol, now)
        if not long_quote or not short_quote:
            return self._pending_exit(row, recovery_reason, "stale_quote", now)
        try:
            validate_pair(long_quote, short_quote)
            # Invalid prices cannot become marks or trigger a fabricated stop.
            validate_quote(long_quote, position.quantity, "sell", self.cfg.market_data.quote_maximum_age_seconds, now)
            validate_quote(short_quote, position.quantity, "buy", self.cfg.market_data.quote_maximum_age_seconds, now)
        except ValueError as exc:
            return self._pending_exit(row, recovery_reason, str(exc), now)
        position = self._position_from_row(row)
        underlying = (underlying_quote.last or underlying_quote.midpoint) if underlying_quote else None
        simulation = SimulationConfig(**payload_json.get("entry_evidence", {}).get("execution_assumptions", asdict(self.cfg.simulation)))
        liquidation_fill = simulate_spread_exit(long_quote, short_quote, simulation, self.cfg.contract, position.quantity, self.cfg.market_data.quote_maximum_age_seconds, now)
        spread_bid = round(long_quote.bid - short_quote.ask, 4)
        spread_ask = round(long_quote.ask - short_quote.bid, 4)
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
            "liquidation_value": liquidation_fill["fill_price"] * 100 * position.quantity,
            "liquidation_basis": "validated_exit_fill_after_costs",
            "pnl_pct": pnl_pct,
            "pnl_dollars": pnl_dollars,
            "mfe_pct": mfe,
            "mae_pct": mae,
            "peak_pnl_pct": peak,
            "drawdown_from_peak_pct": peak - pnl_pct,
            "holding_seconds": (now - position.opened_at).total_seconds(),
            "underlying_price": underlying,
            "long_quote_age_seconds": max(0.0, (now - long_quote.timestamp.astimezone(timezone.utc)).total_seconds()),
            "short_quote_age_seconds": max(0.0, (now - short_quote.timestamp.astimezone(timezone.utc)).total_seconds()),
            "feed_degraded": self.quote_manager.degraded,
        }
        self._record_contract_quote(row["id"], row.get("episode_id"), long_symbol, "spread_long", long_quote, now)
        self._record_contract_quote(row["id"], row.get("episode_id"), short_symbol, "spread_short", short_quote, now)
        self._record_contract_quote(row["id"], row.get("episode_id"), row["ticker"], "underlying", underlying_quote, now)
        self.db.insert_mark(row["id"], mark)
        synthetic_quote = Quote(row["symbol"], spread_bid, spread_ask if spread_ask > spread_bid else spread_bid + 0.01, now)
        fraction = payload_json.get("take_profit_remaining_fraction")
        if fraction is not None:
            target_pct = (float(payload_json["spread"]["width"]) - position.entry_price) * fraction / position.entry_price * 100
            exit_cfg = replace(exit_cfg, take_profit_pct=target_pct)
        reason = recovery_reason or exit_reason(position, synthetic_quote, underlying, exit_cfg, now)
        if reason:
            if pending.get("retry_at") and now < datetime.fromisoformat(pending["retry_at"]):
                return {"position_id": row["id"], "status": "exit_backoff", "exit_reason": reason}
            try:
                simulation = SimulationConfig(**payload_json.get("entry_evidence", {}).get("execution_assumptions", asdict(self.cfg.simulation)))
                exit_fill = simulate_spread_exit(long_quote, short_quote, simulation, self.cfg.contract, position.quantity, self.cfg.market_data.quote_maximum_age_seconds, now)
            except ValueError as exc:
                return self._pending_exit(row, reason, str(exc), now)
            order = dict(order_id=sha256_id("paper_order", {"position": row["id"], "side": "DEBIT_CLOSE"}), episode_id=row.get("episode_id"), position_id=row["id"], side="DEBIT_CLOSE", symbol=row["symbol"], quantity=position.quantity, status="SIMULATED_FILLED", fill={"price": exit_fill["fill_price"], "basis": "long_bid_minus_short_ask_minus_slippage", "paper_only": True}, created_at=now.isoformat())
            closed = self.db.close_position(row["id"], float(exit_fill["fill_price"]), reason, {**mark, "exit_fill": {**exit_fill, "long_fill": asdict(exit_fill["long_fill"]), "short_fill": asdict(exit_fill["short_fill"])}, "pending_exit": None}, exit_order=order)
            if closed:
                self._complete_position(row)
            return {"position_id": row["id"], "closed": closed, "exit_reason": reason}
        return {"position_id": row["id"], "status": "marked"}

    def _pending_exit(self, row, reason, error, now):
        if reason:
            payload = json.loads(row["payload_json"])
            previous = payload.get("pending_exit") or {}
            if previous.get("retry_at") and now < datetime.fromisoformat(previous["retry_at"]):
                return {"position_id": row["id"], "status": "exit_backoff", "exit_reason": reason}
            attempts = previous.get("attempts", 0) + 1
            pending = {"reason": reason, "error": error, "attempts": attempts, "since": previous.get("since", now.isoformat()), "retry_at": (now + timedelta(seconds=min(60, 2 ** min(attempts, 6)))).isoformat()}
            with self.db.connect() as conn:
                conn.execute("update paper_positions set payload_json=json_set(payload_json, '$.pending_exit', json(?)) where id=? and status in ('OPEN','SHADOW_OPEN')", (json.dumps(pending), row["id"]))
            if attempts == 1 or attempts % 10 == 0:
                self.db.insert_system_event("EXIT_PENDING", {"position_id": row["id"], **pending})
        return {"position_id": row["id"], "status": "exit_pending" if reason else error, "exit_reason": reason}

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
                worked = target()
                if worked is not False:
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
            return False
        self.process_batch_once(batch_id)
        return True

    def _entry_loop(self) -> None:
        try:
            item = self.entry_queue.get(timeout=0.25)
        except queue.Empty:
            return False
        self.process_entry_once(item)
        return True

    def _monitor_loop(self) -> None:
        worked = bool(self.monitor_once())
        if self.cfg.execution.auto_promote_paper and time.monotonic() >= self._next_reconcile_check:
            self._next_reconcile_check = time.monotonic() + 60.0
            self.reconciliation_passed = self.database_integrity_ok and self._reconcile_broker()
            if not self.reconciliation_passed:
                self.db.insert_system_event("BROKER_RECONCILIATION_FAILED", {
                    "reason": self.broker_readiness.get("last_error") or "reconciliation failed",
                })
            if not self.reconciliation_passed and not self.db.open_positions(include_shadow=False):
                self.mode = Mode.SHADOW
            if self.mode != Mode.PAPER:
                ok, reason = self.promote_to_paper()
                self.db.insert_system_event("AUTO_PAPER_PROMOTION", {"ok": ok, "reason": reason, "retry": True})
            worked = True
        time.sleep(0.5)
        return worked

    def _forward_loop(self) -> None:
        try:
            item_id = self.forward_queue.get(timeout=0.25)
        except queue.Empty:
            # Retry scheduling must not depend on a restart: periodically requeue
            # database items whose backoff has elapsed so a transient network
            # failure cannot strand a forward until the next process start.
            if time.monotonic() >= self._next_due_forward_check:
                self._next_due_forward_check = time.monotonic() + 60.0
                for item_id in self.db.due_forward_items(datetime.now(timezone.utc).isoformat())[:50]:
                    try:
                        self.forward_queue.put_nowait(item_id)
                    except queue.Full:
                        break
            return False
        self.forwarder.attempt(item_id)
        return True

    def _complete_position(self, row: dict[str, Any]) -> None:
        """Close the parent lifecycle and release every quote dependency."""
        if row.get("episode_id"):
            self.db.end_episode(str(row["episode_id"]))
        self.quote_manager.unsubscribe(self._symbols_for_position_row(row))

    @staticmethod
    def _dte(expiration: str, as_of: datetime | None = None) -> int:
        from .contract_selector import dte

        return dte(expiration, as_of)

    def _record_contract_quote(self, position_id: str, episode_id: str | None, symbol: str,
                               role: str, quote: Quote, captured_at: datetime) -> None:
        if quote is None:
            return
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
