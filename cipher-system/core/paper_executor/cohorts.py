"""Isolated paper experiments consuming one immutable market observation tape."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
import json
import secrets
from pathlib import Path
import sqlite3
import threading
import time
from zoneinfo import ZoneInfo

from .models import Quote
from .runtime import RuntimeCoordinator


COHORTS = ("baseline", "confirmation", "cost", "exit")


def configurations(base):
    """Same cash and risk policy, one frozen experimental delta per candidate."""
    from .config import ExperimentConfig

    if base.execution.backend != "simulated":
        raise ValueError("Autopilot comparisons require simulated execution")
    result = []
    for index, name in enumerate(COHORTS):
        root = base.runtime_root if index == 0 else base.runtime_root / "cohorts" / name
        version = base.experiment.version if base.experiment.cohort_id != "legacy" else "v2"
        experiment = ExperimentConfig(
            cohort_id=name, version=version,
            registry_strategy_id=f"autopilot.{version}.{name}",
            confirmation_observations=2 if name == "confirmation" else 1,
            maximum_round_trip_stop_fraction=1 / 3 if name == "cost" else None,
            take_profit_remaining_fraction=0.5 if name == "exit" else None,
        )
        result.append(replace(
            base, runtime_root=root,
            database_path=base.database_path if index == 0 else root / "paper.sqlite",
            server=replace(base.server, port=base.server.port + index,
                           control_token_path=root / "state" / "control.token"),
            experiment=experiment,
        ))
    return result


class SharedObservations:
    """One provider fetch per snapshot and request, also retained across restarts.

    Each entry batch has its own snapshot. Monitors share five-second snapshots.
    Failures are frozen too, so provider recovery cannot favor a later cohort.
    """

    def __init__(self, provider, path: Path):
        self.provider = provider
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with self._connect() as db:
            db.execute("pragma journal_mode=WAL")
            db.execute("""create table if not exists observations (
                observation_id text primary key, snapshot_id text not null,
                method text not null, request_json text not null,
                response_json text, error text, observed_at text not null)""")

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    def fetch(self, snapshot, method, args):
        request = json.dumps(args, sort_keys=True)
        key = hashlib.sha256(f"{snapshot}|{method}|{request}".encode()).hexdigest()
        with self.lock:
            with self._connect() as db:
                row = db.execute("select response_json,error from observations where observation_id=?", (key,)).fetchone()
                if row is None:
                    try:
                        value = getattr(self.provider, method)(*args)
                        if method == "quotes":
                            value = {symbol: asdict(quote) for symbol, quote in value.items()}
                        response, error = json.dumps(value, default=str), None
                    except Exception as exc:
                        response, error = None, f"{type(exc).__name__}: market observation unavailable"
                    db.execute("insert into observations values(?,?,?,?,?,?,?)", (
                        key, snapshot, method, request, response, error,
                        datetime.now(timezone.utc).isoformat(),
                    ))
                    row = (response, error)
            if row[1]:
                raise RuntimeError(row[1])
            value = json.loads(row[0])
            if method == "quotes":
                return {symbol: Quote(**{**fields, "timestamp": datetime.fromisoformat(fields["timestamp"])})
                        for symbol, fields in value.items()}
            return value

    def view(self):
        return ObservationView(self)


class ObservationView:
    def __init__(self, shared):
        self.shared = shared
        self.local = threading.local()

    @contextmanager
    def scope(self, snapshot):
        previous = getattr(self.local, "snapshot", None)
        self.local.snapshot = snapshot
        try:
            yield
        finally:
            self.local.snapshot = previous

    def _fetch(self, method, *args):
        snapshot = getattr(self.local, "snapshot", None) or f"monitor:{int(time.time() // 5)}"
        return self.shared.fetch(snapshot, method, args)

    def expirations(self, ticker):
        return self._fetch("expirations", ticker)

    def chain(self, ticker, expiration):
        return self._fetch("chain", ticker, expiration)

    def quotes(self, symbols):
        # Cache per symbol so overlapping requests from portfolios with
        # different positions receive identical quotes in the same snapshot.
        snapshot = getattr(self.local, "snapshot", None) or f"monitor:{int(time.time() // 5)}"
        symbols = sorted(set(symbols))
        shared = self.shared
        with shared.lock:
            existing, missing = {}, []
            with shared._connect() as db:
                for symbol in symbols:
                    key = hashlib.sha256(f"{snapshot}|quote|{symbol}".encode()).hexdigest()
                    row = db.execute("select response_json,error from observations where observation_id=?", (key,)).fetchone()
                    if row is None:
                        missing.append((symbol, key))
                    elif row[1]:
                        continue
                    elif row[0]:
                        raw = json.loads(row[0])
                        existing[symbol] = Quote(**{**raw, "timestamp": datetime.fromisoformat(raw["timestamp"])})
                if missing:
                    quotes = shared.fetch(snapshot, "quotes", ([symbol for symbol, _ in missing],))
                    for symbol, key in missing:
                        quote = quotes.get(symbol)
                        db.execute("insert or ignore into observations values(?,?,?,?,?,?,?)", (
                            key, snapshot, "quote", json.dumps(symbol),
                            json.dumps(asdict(quote), default=str) if quote else None,
                            None if quote else "missing_quote", datetime.now(timezone.utc).isoformat(),
                        ))
                        if quote is not None:
                            existing[symbol] = quote
            return existing

    def status(self):
        provider = self.shared.provider
        return provider.status() if callable(getattr(provider, "status", None)) else {}


class CohortRuntime(RuntimeCoordinator):
    def process_entry_once(self, item):
        card = item["card"]
        # Repeated observations of the same ticker within a scanner batch share
        # a snapshot across all portfolios, independent of thread scheduling.
        snapshot = f"entry:{card.captured_at.isoformat()}:{card.ticker}"
        with self.market_data.scope(snapshot):
            return super().process_entry_once(item)


class CohortGroup:
    def __init__(self, apps):
        self.apps = apps
        self._summary_lock = threading.Lock()
        self._summary_at = 0.0
        self._summary = []
        self._maintenance_at = 0.0
        self.primary_path = apps[0].cfg.runtime_root / "cohorts" / "primary.json"
        try:
            self.primary = json.loads(self.primary_path.read_text())
        except FileNotFoundError:
            self.primary = {"cohort_id": "baseline", "session": None, "reason": "initial_baseline"}
        self.inbox_path = apps[0].cfg.runtime_root / "cohorts" / "inbox.sqlite"
        self.inbox_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.inbox_path) as db:
            db.execute("create table if not exists inbox (id text primary key, payload text not null, delivered integer not null default 0)")
        for app in apps:
            manifest = app.cfg.runtime_root / "experiments" / f"{app.cfg.experiment.cohort_id}-{app.cfg.experiment.version}.json"
            frozen = {"configuration": asdict(app.cfg), "config_hash": app.runtime.config_hash}
            frozen = json.loads(json.dumps(frozen, default=str))
            manifest.parent.mkdir(parents=True, exist_ok=True)
            if manifest.exists():
                prior = json.loads(manifest.read_text())
                if prior.get("config_hash") != frozen["config_hash"]:
                    raise ValueError(f"Frozen experiment changed: {app.cfg.experiment.cohort_id}; increment version")
            else:
                with manifest.open("x") as handle:
                    json.dump({**frozen, "registered_at": datetime.now(timezone.utc).isoformat()}, handle, indent=2)
            app.experiment_registered_at = json.loads(manifest.read_text())["registered_at"]
            token = app.cfg.server.control_token_path
            token.parent.mkdir(parents=True, exist_ok=True)
            if not token.exists():
                with token.open("x") as handle:
                    token.chmod(0o600)
                    handle.write(secrets.token_urlsafe(32))

    def ingest(self, payload):
        from .ingestion import batch_id
        identifier = batch_id(payload)
        with sqlite3.connect(self.inbox_path) as db:
            db.execute("insert or ignore into inbox(id,payload) values(?,?)", (identifier, json.dumps(payload)))
            recorded = json.loads(db.execute("select payload from inbox where id=?", (identifier,)).fetchone()[0])
        if recorded != payload:
            raise ValueError("batch ID reused with different observations")
        results = {}
        for app in self.apps:
            name = app.cfg.experiment.cohort_id
            try:
                results[name] = app.runtime.ingest_payload(deepcopy(payload))
            except Exception as exc:
                results[name] = {"accepted": False, "error": type(exc).__name__}
        if all(result.get("accepted") for result in results.values()):
            with sqlite3.connect(self.inbox_path) as db:
                db.execute("update inbox set delivered=1 where id=?", (identifier,))
        return {**results["baseline"], "cohorts": results}

    def recover(self):
        with sqlite3.connect(self.inbox_path) as db:
            rows = db.execute("select payload from inbox where delivered=0 order by rowid").fetchall()
        for row in rows:
            self.ingest(json.loads(row[0]))

    def summary(self):
        from .cohort_evaluation import evaluate_cohort, observation_activity

        with self._summary_lock:
            if time.monotonic() - self._summary_at < 60 and self._summary:
                return deepcopy(self._summary)
            rows = []
            for app in self.apps:
                try:
                    evaluation = evaluate_cohort(
                        app.cfg.database_path,
                        baseline_path=self.apps[0].cfg.database_path if app is not self.apps[0] else None,
                    )
                    health = app.runtime.health()
                    key = f"{app.cfg.experiment.cohort_id}/{app.cfg.experiment.version}/{app.runtime.config_hash}"
                    current = deepcopy(evaluation["per_version"].get(key))
                    if current is not None:
                        blockers = current["promotion_blockers"]
                        reconciled = (health.get("reconciliation_passed") and health.get("ready")
                                      and health.get("readiness", {}).get("process_healthy")
                                      and not health.get("readiness", {}).get("pending_exit_positions"))
                        if reconciled:
                            blockers = [b for b in blockers if b != "operational_reconciliation_required"]
                        current["promotion_blockers"] = blockers
                        current["promotion_eligible"] = not blockers
                        evaluation["promotion_blockers"] = blockers
                    else:
                        evaluation["promotion_blockers"] = ["no_prospective_trades"]
                    evaluation["current"] = current or {"trades": 0, "pnl_usd": 0, "win_rate_pct": None,
                                                          "promotion_eligible": False, "promotion_blockers": ["no_prospective_trades"]}
                    evaluation["activity"] = observation_activity(app.cfg.database_path, app.experiment_registered_at)
                    rows.append({"cohort_id": app.cfg.experiment.cohort_id,
                                 "version": app.cfg.experiment.version,
                                 "primary": self.primary["cohort_id"] == app.cfg.experiment.cohort_id,
                                 "port": app.cfg.server.port,
                                 "health": health, "evaluation": evaluation})
                except Exception as exc:
                    rows.append({"cohort_id": app.cfg.experiment.cohort_id,
                                 "version": app.cfg.experiment.version,
                                 "error": type(exc).__name__})
            self._summary, self._summary_at = rows, time.monotonic()
            return deepcopy(rows)

    def maintain(self, now=None):
        """Select the primary paper portfolio only before a session's entries.

        Comparison portfolios keep collecting evidence after a primary change.
        Existing positions remain managed by their original portfolio/policy.
        """
        if now is None and time.monotonic() - self._maintenance_at < 60:
            return
        self._maintenance_at = time.monotonic()
        now = now or datetime.now(timezone.utc)
        local = now.astimezone(ZoneInfo("America/New_York"))
        from core.exchange_calendar import is_session
        if not is_session(local.date()) or not "09:00" <= local.strftime("%H:%M") < "09:45":
            return
        day = local.date().isoformat()
        if self.primary.get("session") == day:
            return
        rows = self.summary()
        eligible = [r for r in rows if r["cohort_id"] != "baseline" and r.get("evaluation", {}).get("current", {}).get("promotion_eligible")]
        selected = max(eligible, key=lambda r: (r["evaluation"]["current"]["expectancy_usd"], r["cohort_id"]), default=None)
        choice = selected["cohort_id"] if selected else "baseline"
        self.primary = {"cohort_id": choice, "session": day, "decided_at": now.isoformat(),
                        "reason": "validated_candidate" if selected else "candidate_gates_not_met"}
        temporary = self.primary_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.primary, indent=2))
        temporary.replace(self.primary_path)
        self.apps[0].db.insert_system_event("PRIMARY_PAPER_PORTFOLIO", self.primary)
        self._summary_at = 0
