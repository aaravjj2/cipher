"""Six isolated, automatic option shadow portfolios for the supplied studies.

This is a market-data consumer and local simulator. It has no broker adapter and
cannot transmit an order. Prospective signals are accepted only from the latest
closed source bar, fills cross the observed spread with additional slippage, and
missed signals are never backfilled.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
from typing import Callable, Mapping, Sequence

from core import obsidian_signal_studies as studies
from core import structural_fib_v6 as v6
from core.structural_fib_bars import Bar, NY, split_sessions

DEFAULT_DB = Path("/home/aarav/Aarav/cipher/runtime/data/fronttest_portfolios/fronttest.sqlite")


@dataclass(frozen=True, slots=True)
class PortfolioSpec:
    portfolio_id: str
    strategy: str
    symbol: str
    setup_ids: tuple[str, ...]
    timeframe_minutes: int
    starting_cash: float
    risk_fraction: float
    min_dte: int
    target_dte: int
    max_dte: int
    target_moneyness: float
    maximum_spread_pct: float = 15.0
    minimum_open_interest: int = 100
    minimum_volume: int = 10
    maximum_new_positions_per_day: int = 3
    stop_after_daily_losses: int = 2
    entry_start_et: time = time(9, 35)
    entry_cutoff_et: time = time(11, 30)
    direction_flip_cooldown_minutes: int = 15
    force_close_et: time = time(15, 45)
    # Disabled portfolios stop receiving signals and are excluded from the
    # daily digest; they remain in the registry/status so the UI can show the
    # turned-off state instead of pretending they never existed.
    enabled: bool = True


SPECS = (
    PortfolioSpec("v6_nvda_p05", "V6 PUT 0.5->1", "NVDA", ("P05",), 5, 100_000, .10, 10, 14, 21, .98),
    # C05 was being emitted by the V6 study but no portfolio subscribed to it;
    # registered 2026-08-18 so the call 0.5->1 setup gets a paper account too.
    PortfolioSpec("v6_nvda_c05", "V6 CALL 0.5->1", "NVDA", ("C05",), 5, 100_000, .10, 10, 14, 21, 1.00),
    PortfolioSpec("v6_nvda_c1", "V6 CALL 1->2", "NVDA", ("C1",), 5, 100_000, .075, 10, 14, 21, 1.00),
    PortfolioSpec("v6_nvda_p1", "V6 PUT 1->2", "NVDA", ("P1",), 5, 100_000, .05, 10, 14, 21, .98),
    # QQQ systems are turned off as of 2026-08-18; flip enabled=True to restart them.
    PortfolioSpec("qqq_validated", "QQQ VALIDATED 0.5->1", "QQQ", ("validated_bull", "validated_bear"), 1, 100_000, .05, 1, 3, 7, 1.00, enabled=False),
    PortfolioSpec("qqq_early", "QQQ EARLY pivot->0.5", "QQQ", ("early_bull", "early_bear"), 1, 100_000, .02, 1, 3, 7, 1.00, enabled=False),
    PortfolioSpec("mu_pm_liquidity", "MU PM break/sweep 15m", "MU", ("bull_break", "bear_break", "top_sweep", "bottom_sweep"), 5, 100_000, .02, 1, 3, 7, 1.00),
)

# Portfolios the pass actually processes; disabled ones stay in SPECS for the
# registry and status surface but receive no signals and no digest rows.
ACTIVE_SPECS = tuple(spec for spec in SPECS if spec.enabled)


def connect(path: Path = DEFAULT_DB) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript("""
      pragma journal_mode=WAL;
      pragma foreign_keys=on;
      create table if not exists portfolios (
        portfolio_id text primary key, strategy text not null, symbol text not null,
        starting_cash real not null, risk_fraction real not null,
        config_json text not null, created_at text not null
      );
      create table if not exists signals (
        signal_id text primary key, portfolio_id text not null references portfolios(portfolio_id),
        symbol text not null, setup_id text not null, direction text not null,
        signal_at text not null, detected_at text not null, payload_json text not null,
        disposition text not null default 'DETECTED', skip_reason text
      );
      create table if not exists positions (
        position_id text primary key, portfolio_id text not null references portfolios(portfolio_id),
        signal_id text not null unique references signals(signal_id), status text not null,
        contract text not null, option_type text not null, expiration text not null,
        strike real not null, quantity integer not null, allocated_capital real not null,
        entry_at text not null, entry_bid real not null, entry_ask real not null,
        entry_fill real not null, underlying_entry real not null,
        last_mark_at text, last_bid real, last_ask real,
        exit_at text, exit_bid real, exit_ask real, exit_fill real,
        exit_reason text, pnl real, return_pct real,
        structure text not null default 'long_option',
        short_contract text, short_strike real,
        short_entry_bid real, short_entry_ask real, short_entry_fill real,
        short_last_bid real, short_last_ask real,
        short_exit_bid real, short_exit_ask real, short_exit_fill real
      );
      create unique index if not exists ux_fronttest_one_open
        on positions(portfolio_id) where status='OPEN';
      create table if not exists runs (
        run_id integer primary key autoincrement, started_at text not null,
        completed_at text, status text not null, summary_json text, error text
      );
      create table if not exists events (
        event_id integer primary key autoincrement, recorded_at text not null,
        portfolio_id text, event_type text not null, payload_json text not null
      );
      create table if not exists signal_outcomes (
        signal_id text primary key references signals(signal_id),
        portfolio_id text not null references portfolios(portfolio_id),
        symbol text not null, status text not null default 'TRACKING',
        outcome text, evaluated_through text, resolved_at text,
        entry_underlying real, exit_underlying real, target real, stop real,
        bars_observed integer not null default 0,
        mfe_pct real, mae_pct real,
        methodology text not null,
        created_at text not null, updated_at text not null
      );
      create index if not exists ix_signal_outcomes_portfolio_status
        on signal_outcomes(portfolio_id,status);
    """)
    signal_columns = {row[1] for row in db.execute("pragma table_info(signals)")}
    if "disposition" not in signal_columns:
        db.execute("alter table signals add column disposition text not null default 'DETECTED'")
    if "skip_reason" not in signal_columns:
        db.execute("alter table signals add column skip_reason text")
    position_columns = {row[1] for row in db.execute("pragma table_info(positions)")}
    position_migrations = {
        "structure": "text not null default 'long_option'",
        "short_contract": "text", "short_strike": "real",
        "short_entry_bid": "real", "short_entry_ask": "real", "short_entry_fill": "real",
        "short_last_bid": "real", "short_last_ask": "real",
        "short_exit_bid": "real", "short_exit_ask": "real", "short_exit_fill": "real",
    }
    for column, declaration in position_migrations.items():
        if column not in position_columns:
            db.execute(f"alter table positions add column {column} {declaration}")
    now = datetime.now(timezone.utc).isoformat()
    for spec in SPECS:
        db.execute(
            "insert or ignore into portfolios values (?,?,?,?,?,?,?)",
            (spec.portfolio_id, spec.strategy, spec.symbol, spec.starting_cash,
             spec.risk_fraction, json.dumps(asdict(spec), default=str, sort_keys=True), now),
        )
    db.commit()
    return db


TERMINAL_OUTCOMES = {
    "TARGET", "INVALIDATED", "SESSION_EXPIRED",
    "HORIZON_FAVORABLE", "HORIZON_ADVERSE", "HORIZON_FLAT",
}


def _signal_price(payload: Mapping[str, object]) -> float | None:
    for key in ("signal_price", "signal_close", "underlying_entry"):
        try:
            value = float(payload.get(key))
        except (TypeError, ValueError):
            continue
        if math.isfinite(value) and value > 0:
            return value
    return None


def _score_signal_path(
    *, portfolio_id: str, direction: str, signal_at: datetime,
    payload: Mapping[str, object], bars: Sequence[Bar], now_et: datetime,
) -> dict:
    """Score the observable underlying path after a signal without inventing fills.

    This is intentionally a counterfactual *signal* ledger.  It does not select
    an option contract or claim option-premium P/L for a portfolio-blocked setup.
    """
    entry = _signal_price(payload)
    try:
        target = float(payload.get("target"))
    except (TypeError, ValueError):
        target = None
    try:
        stop = float(payload.get("stop"))
    except (TypeError, ValueError):
        stop = None
    relevant = sorted(
        (bar for bar in bars if bar.t > signal_at and bar.t.date() == signal_at.date()),
        key=lambda bar: bar.t,
    )
    methodology = (
        "underlying_path_only;v6_target_touch_then_confirmed_close_invalidation"
        if portfolio_id.startswith("v6_") else
        "underlying_path_only;conservative_invalidation_before_target"
    )
    if portfolio_id == "mu_pm_liquidity":
        methodology = "underlying_path_only;fixed_15m_directional_close"

    result = {
        "status": "TRACKING", "outcome": None,
        "evaluated_through": relevant[-1].t.isoformat() if relevant else None,
        "resolved_at": None, "entry_underlying": entry,
        "exit_underlying": relevant[-1].c if relevant else None,
        "target": target, "stop": stop, "bars_observed": len(relevant),
        "mfe_pct": None, "mae_pct": None, "methodology": methodology,
    }
    if entry is not None and relevant:
        if direction == "long":
            favorable = max(bar.h - entry for bar in relevant)
            adverse = max(entry - bar.l for bar in relevant)
        else:
            favorable = max(entry - bar.l for bar in relevant)
            adverse = max(bar.h - entry for bar in relevant)
        result["mfe_pct"] = max(0.0, favorable / entry * 100.0)
        result["mae_pct"] = max(0.0, adverse / entry * 100.0)

    if portfolio_id == "mu_pm_liquidity":
        horizon = signal_at + timedelta(minutes=int(payload.get("max_hold_minutes", 15)))
        completed = [bar for bar in relevant if bar.t >= horizon]
        if completed and entry is not None:
            exit_bar = completed[0]
            signed = (exit_bar.c / entry - 1.0) * (1 if direction == "long" else -1)
            result.update({
                "status": "RESOLVED",
                "outcome": "HORIZON_FAVORABLE" if signed > 0 else ("HORIZON_ADVERSE" if signed < 0 else "HORIZON_FLAT"),
                "resolved_at": exit_bar.t.isoformat(), "exit_underlying": exit_bar.c,
            })
            return result
    elif target is not None and stop is not None:
        for bar in relevant:
            target_hit = (direction == "long" and bar.h >= target) or (direction == "short" and bar.l <= target)
            invalidated = (
                ((direction == "long" and bar.c < stop) or (direction == "short" and bar.c > stop))
                if portfolio_id.startswith("v6_") else
                ((direction == "long" and bar.l < stop) or (direction == "short" and bar.h > stop))
            )
            # Match the active simulator: V6 checks its price-touch target first;
            # one-minute pivot studies use the conservative stop-first ordering.
            if portfolio_id.startswith("v6_"):
                outcome = "TARGET" if target_hit else ("INVALIDATED" if invalidated else None)
            else:
                outcome = "INVALIDATED" if invalidated else ("TARGET" if target_hit else None)
            if outcome:
                result.update({
                    "status": "RESOLVED", "outcome": outcome,
                    "resolved_at": bar.t.isoformat(), "exit_underlying": target if outcome == "TARGET" else bar.c,
                })
                return result

    session_finished = bool(relevant and relevant[-1].t.time() >= time(15, 55))
    day_finished = now_et.date() > signal_at.date()
    if session_finished or day_finished:
        result.update({
            "status": "RESOLVED", "outcome": "SESSION_EXPIRED",
            "resolved_at": relevant[-1].t.isoformat() if relevant else now_et.isoformat(),
        })
    elif not relevant:
        result["status"] = "AWAITING_BARS"
    return result


def update_signal_outcomes(
    db: sqlite3.Connection, bars_by_symbol: Mapping[str, Sequence[Bar]], *, now_et: datetime,
) -> dict[str, int]:
    """Create/update the counterfactual path ledger for every detected signal."""
    rows = db.execute(
        """select s.signal_id,s.portfolio_id,s.symbol,s.direction,s.signal_at,s.payload_json,
                  o.status as outcome_status
             from signals s left join signal_outcomes o on o.signal_id=s.signal_id
            where o.signal_id is null or o.status not in ('RESOLVED')
            order by s.signal_at"""
    ).fetchall()
    created = updated = resolved = 0
    stamp = datetime.now(timezone.utc).isoformat()
    for row in rows:
        try:
            signal_at = datetime.fromisoformat(row["signal_at"]).astimezone(NY)
            payload = json.loads(row["payload_json"] or "{}")
        except (ValueError, TypeError, json.JSONDecodeError):
            continue
        outcome = _score_signal_path(
            portfolio_id=row["portfolio_id"], direction=row["direction"],
            signal_at=signal_at, payload=payload,
            bars=bars_by_symbol.get(row["symbol"], ()), now_et=now_et,
        )
        existed = row["outcome_status"] is not None
        db.execute(
            """insert into signal_outcomes(
                   signal_id,portfolio_id,symbol,status,outcome,evaluated_through,resolved_at,
                   entry_underlying,exit_underlying,target,stop,bars_observed,mfe_pct,mae_pct,
                   methodology,created_at,updated_at
               ) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               on conflict(signal_id) do update set
                   status=excluded.status,outcome=excluded.outcome,
                   evaluated_through=excluded.evaluated_through,resolved_at=excluded.resolved_at,
                   entry_underlying=excluded.entry_underlying,exit_underlying=excluded.exit_underlying,
                   target=excluded.target,stop=excluded.stop,bars_observed=excluded.bars_observed,
                   mfe_pct=excluded.mfe_pct,mae_pct=excluded.mae_pct,
                   methodology=excluded.methodology,updated_at=excluded.updated_at""",
            (row["signal_id"], row["portfolio_id"], row["symbol"], outcome["status"],
             outcome["outcome"], outcome["evaluated_through"], outcome["resolved_at"],
             outcome["entry_underlying"], outcome["exit_underlying"], outcome["target"],
             outcome["stop"], outcome["bars_observed"], outcome["mfe_pct"], outcome["mae_pct"],
             outcome["methodology"], stamp, stamp),
        )
        created += int(not existed)
        updated += int(existed)
        resolved += int(outcome["status"] == "RESOLVED")
    return {"created": created, "updated": updated, "resolved": resolved}


def _stable_id(*parts: object) -> str:
    raw = "|".join(map(str, parts))
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _latest_regular(bars: Sequence[Bar]) -> Bar | None:
    regular = [b for b in bars if time(9, 30) <= b.t.time() < time(16)]
    return regular[-1] if regular else None


def detect_signals(bars_by_symbol: Mapping[str, Sequence[Bar]]) -> list[dict]:
    """Return signals on the latest closed bar only; never historical backfill."""
    out: list[dict] = []
    nvda = list(bars_by_symbol.get("NVDA", ()))
    latest = _latest_regular(nvda)
    if latest:
        result = v6.run_symbol("NVDA", nvda)
        nvda_specs = [spec for spec in ACTIVE_SPECS if spec.symbol == "NVDA"]
        for row in result["signals"]:
            if row["day"] == latest.t.date().isoformat() and row["signal_time"] == latest.t.strftime("%H:%M"):
                for spec in nvda_specs:
                    if row["setup_id"] in spec.setup_ids:
                        out.append({**row, "portfolio_id": spec.portfolio_id,
                                    "signal_at": latest.t.isoformat(), "symbol": "NVDA"})

    qqq = list(bars_by_symbol.get("QQQ", ()))
    latest = _latest_regular(qqq)
    if latest:
        report = studies.qqq_wave_study(qqq)
        for spec in ACTIVE_SPECS:
            if spec.symbol != "QQQ":
                continue
            if any(setup.startswith("validated_") for setup in spec.setup_ids):
                rows = report["raw_validated_signal_records"]
            elif any(setup.startswith("early_") for setup in spec.setup_ids):
                rows = report["raw_early_signal_records"]
            else:
                continue
            for row in rows:
                if datetime.fromisoformat(row["signal_at"]) == latest.t and row["setup_id"] in spec.setup_ids:
                    payload = {**row, "portfolio_id": spec.portfolio_id}
                    if "early_" in spec.setup_ids[0]:
                        sign = 1 if row["direction"] == "long" else -1
                        payload["target"] = row["anchor"] + sign * .5 * row["pm_range"]
                        payload["stop"] = row["anchor"]
                    out.append(payload)

    mu = list(bars_by_symbol.get("MU", ()))
    latest = _latest_regular(mu)
    mu_spec = next((spec for spec in ACTIVE_SPECS if spec.symbol == "MU"), None)
    if latest and mu_spec:
        report = studies.mu_premarket_study(mu)
        for row in report["raw_signal_records"]:
            if datetime.fromisoformat(row["signal_at"]) == latest.t:
                out.append({**row, "portfolio_id": mu_spec.portfolio_id,
                            "max_hold_minutes": 15})
    return out


class AlpacaOptionQuotes:
    """Read-only Alpaca stock/option snapshot adapter."""

    def stock(self, symbol: str) -> float:
        from core import app
        value = app.quote(symbol).get("price_context")
        if value is None:
            raise RuntimeError(f"no stock price for {symbol}")
        return float(value)

    def chain(self, spec: PortfolioSpec, today: date) -> list[dict]:
        from core import app
        start = (today + timedelta(days=spec.min_dte)).isoformat()
        end = (today + timedelta(days=spec.max_dte)).isoformat()
        return app.option_chain(spec.symbol, "opra", max_pages=8,
                                expiration_gte=start, expiration_lte=end)

    def quotes(self, symbols: Sequence[str]) -> dict[str, dict]:
        if not symbols:
            return {}
        from core import app
        raw = app.alpaca("/v1beta1/options/snapshots", {
            "symbols": ",".join(sorted(set(symbols))), "feed": app.resolve_options_feed("opra")
        })
        out = {}
        for symbol, snapshot in (raw.get("snapshots") or {}).items():
            q = snapshot.get("latestQuote") or {}
            bid = q.get("bp", q.get("bid_price")); ask = q.get("ap", q.get("ask_price"))
            if bid is not None and ask is not None:
                out[symbol] = {"bid": float(bid), "ask": float(ask),
                               "timestamp": q.get("t", q.get("timestamp"))}
        return out


def _equity(db: sqlite3.Connection, spec: PortfolioSpec) -> float:
    pnl = db.execute(
        "select coalesce(sum(pnl),0) from positions where portfolio_id=? and status='CLOSED'",
        (spec.portfolio_id,),
    ).fetchone()[0]
    return spec.starting_cash + float(pnl or 0)


def _select_contract(
    spec: PortfolioSpec, rows: Sequence[dict], spot: float, today: date,
    required_type: str,
) -> dict | None:
    usable = []
    for row in rows:
        kind = str(row.get("type") or row.get("option_type") or "").lower()
        if kind != required_type:
            continue
        expiration = row.get("expiration") or row.get("expiration_date") or row.get("expiry")
        try:
            dte = (date.fromisoformat(str(expiration)) - today).days
            bid, ask = float(row.get("bid")), float(row.get("ask"))
            strike = float(row.get("strike"))
        except (TypeError, ValueError):
            continue
        if not spec.min_dte <= dte <= spec.max_dte or bid <= 0 or ask <= bid:
            continue
        mid = (bid + ask) / 2
        spread = (ask - bid) / mid * 100 if mid else math.inf
        oi = row.get("open_interest"); volume = row.get("volume")
        if spread > spec.maximum_spread_pct:
            continue
        if oi is not None and float(oi) < spec.minimum_open_interest:
            continue
        if volume is not None and float(volume) < spec.minimum_volume:
            continue
        score = (abs(dte - spec.target_dte), abs(strike / spot - spec.target_moneyness), spread)
        usable.append((score, row))
    return min(usable, key=lambda x: x[0])[1] if usable else None


def _fill(side: str, bid: float, ask: float) -> float:
    return ask * 1.005 + .01 if side == "entry" else max(0.0, bid * .995 - .01)


def _spread_candidate(
    spec: PortfolioSpec, rows: Sequence[dict], *, long_contract: dict,
    today: date, required_type: str, allocation: float,
) -> tuple[dict, float] | None:
    """Find a same-expiry vertical whose conservative debit fits allocation.

    The long leg remains the liquidity-qualified contract selected above.  The
    short leg must be farther out of the money and independently pass the same
    spread/OI/volume checks.  Snapshot quotes rank candidates, but both legs are
    re-quoted before a position is recorded.
    """
    expiration = str(long_contract.get("expiration") or long_contract.get("expiration_date") or long_contract.get("expiry"))
    long_strike = float(long_contract["strike"])
    long_ask = float(long_contract["ask"])
    candidates: list[tuple[tuple[float, float], dict, float]] = []
    for row in rows:
        kind = str(row.get("type") or row.get("option_type") or "").lower()
        row_expiration = str(row.get("expiration") or row.get("expiration_date") or row.get("expiry"))
        try:
            strike = float(row.get("strike")); bid = float(row.get("bid")); ask = float(row.get("ask"))
            dte = (date.fromisoformat(row_expiration) - today).days
        except (TypeError, ValueError):
            continue
        farther_otm = strike > long_strike if required_type == "call" else strike < long_strike
        if kind != required_type or row_expiration != expiration or not farther_otm:
            continue
        if not spec.min_dte <= dte <= spec.max_dte or bid <= 0 or ask <= bid:
            continue
        mid = (bid + ask) / 2
        if not mid or (ask - bid) / mid * 100 > spec.maximum_spread_pct:
            continue
        if row.get("open_interest") is not None and float(row["open_interest"]) < spec.minimum_open_interest:
            continue
        if row.get("volume") is not None and float(row["volume"]) < spec.minimum_volume:
            continue
        estimated_debit = _fill("entry", 0.0, long_ask) - _fill("exit", bid, ask)
        width = abs(strike - long_strike)
        if 0 < estimated_debit < width and estimated_debit * 100 <= allocation:
            candidates.append(((abs(width - max(1.0, long_strike * .01)), estimated_debit), row, estimated_debit))
    if not candidates:
        return None
    _, row, debit = min(candidates, key=lambda item: item[0])
    return row, debit


def _entry_block_reason(db: sqlite3.Connection, spec: PortfolioSpec, signal: Mapping[str, object], moment: datetime) -> str | None:
    """Apply identical session and loss controls before any market-data request."""
    clock = moment.time().replace(tzinfo=None)
    if clock < spec.entry_start_et or clock >= spec.entry_cutoff_et:
        return "ENTRY_WINDOW_CLOSED"
    day = moment.date().isoformat()
    losses = int(db.execute(
        """select count(*) from positions where portfolio_id=? and status='CLOSED'
             and substr(exit_at,1,10)=? and pnl<0""", (spec.portfolio_id, day),
    ).fetchone()[0])
    if losses >= spec.stop_after_daily_losses:
        return "DAILY_LOSS_LOCKOUT"
    last = db.execute(
        """select p.exit_at,s.direction from positions p join signals s on s.signal_id=p.signal_id
             where p.portfolio_id=? and p.status='CLOSED' and p.exit_at is not null
             order by p.exit_at desc limit 1""", (spec.portfolio_id,),
    ).fetchone()
    if last and str(last["direction"]) != str(signal.get("direction")):
        try:
            exit_at = datetime.fromisoformat(last["exit_at"]).astimezone(NY)
        except (TypeError, ValueError):
            exit_at = None
        if exit_at and moment < exit_at + timedelta(minutes=spec.direction_flip_cooldown_minutes):
            return "DIRECTION_FLIP_COOLDOWN"
    return None


def _exit_reason(row: sqlite3.Row, payload: dict, bars: Sequence[Bar], now_et: datetime) -> str | None:
    opened = datetime.fromisoformat(row["entry_at"]).astimezone(NY)
    relevant = [b for b in bars if b.t > opened and b.t.date() == opened.date()]
    direction = payload["direction"]
    if row["portfolio_id"] == "mu_pm_liquidity":
        if now_et >= opened + timedelta(minutes=int(payload.get("max_hold_minutes", 15))):
            return "fixed_15m"
    else:
        target, stop = float(payload["target"]), float(payload["stop"])
        for bar in relevant:
            if row["portfolio_id"].startswith("v6_"):
                if (direction == "long" and bar.h >= target) or (direction == "short" and bar.l <= target):
                    return "underlying_target"
                if (direction == "long" and bar.c < stop) or (direction == "short" and bar.c > stop):
                    return "confirmed_invalidation"
            else:
                # One-minute OHLC cannot sequence a same-bar target and pivot
                # failure, so the non-flattering interpretation scores failure.
                if (direction == "long" and bar.l < stop) or (direction == "short" and bar.h > stop):
                    return "pivot_invalidation"
                if (direction == "long" and bar.h >= target) or (direction == "short" and bar.l <= target):
                    return "underlying_target"
    spec = next(x for x in SPECS if x.portfolio_id == row["portfolio_id"])
    return "forced_close" if now_et.time() >= spec.force_close_et else None


def run_pass(
    bars_by_symbol: Mapping[str, Sequence[Bar]], *, db_path: Path = DEFAULT_DB,
    market: AlpacaOptionQuotes | None = None, now: datetime | None = None,
) -> dict:
    moment = (now or datetime.now(timezone.utc)).astimezone(NY)
    provider = market or AlpacaOptionQuotes()
    db = connect(db_path)
    run_id = db.execute("insert into runs(started_at,status) values (?,'RUNNING')",
                        (moment.astimezone(timezone.utc).isoformat(),)).lastrowid
    opened = closed = 0
    errors: list[dict] = []
    try:
        open_rows = db.execute(
            "select p.*,s.payload_json from positions p join signals s on s.signal_id=p.signal_id where p.status='OPEN'"
        ).fetchall()
        quote_symbols = [row["contract"] for row in open_rows]
        quote_symbols.extend(row["short_contract"] for row in open_rows if row["short_contract"])
        quotes = provider.quotes(quote_symbols)
        for row in open_rows:
            try:
                quote = quotes.get(row["contract"])
                short_quote = quotes.get(row["short_contract"]) if row["short_contract"] else None
                reason = _exit_reason(row, json.loads(row["payload_json"]),
                                      bars_by_symbol[row["portfolio_id"].split("_")[1].upper()] if row["portfolio_id"].startswith("v6_") else bars_by_symbol[next(x.symbol for x in SPECS if x.portfolio_id == row["portfolio_id"])], moment)
                missing_leg = not quote or (row["short_contract"] and not short_quote)
                if missing_leg:
                    stale_spread_available = (
                        row["structure"] == "debit_spread" and row["last_bid"] is not None
                        and row["last_ask"] is not None and row["short_last_bid"] is not None
                        and row["short_last_ask"] is not None
                    )
                    if reason == "forced_close" and row["last_bid"] is not None and row["last_ask"] is not None and (not row["short_contract"] or stale_spread_available):
                        quote = {"bid": float(row["last_bid"]), "ask": float(row["last_ask"])}
                        short_quote = (
                            {"bid": float(row["short_last_bid"]), "ask": float(row["short_last_ask"])}
                            if row["short_contract"] else None
                        )
                        reason = "forced_close_stale_mark"
                    else:
                        errors.append({"portfolio_id": row["portfolio_id"], "error": "missing option mark"})
                        continue
                db.execute(
                    """update positions set last_mark_at=?,last_bid=?,last_ask=?,
                              short_last_bid=?,short_last_ask=? where position_id=?""",
                    (moment.isoformat(), quote["bid"], quote["ask"],
                     short_quote["bid"] if short_quote else None,
                     short_quote["ask"] if short_quote else None, row["position_id"]),
                )
                if reason:
                    long_exit = _fill("exit", quote["bid"], quote["ask"])
                    short_exit = _fill("entry", short_quote["bid"], short_quote["ask"]) if short_quote else 0.0
                    exit_fill = max(0.0, long_exit - short_exit)
                    pnl = row["quantity"] * 100 * (exit_fill - row["entry_fill"])
                    ret = (exit_fill / row["entry_fill"] - 1) * 100
                    db.execute(
                        """update positions set status='CLOSED',exit_at=?,exit_bid=?,exit_ask=?,exit_fill=?,
                                  short_exit_bid=?,short_exit_ask=?,short_exit_fill=?,
                                  exit_reason=?,pnl=?,return_pct=? where position_id=?""",
                        (moment.isoformat(), quote["bid"], quote["ask"], exit_fill,
                         short_quote["bid"] if short_quote else None,
                         short_quote["ask"] if short_quote else None,
                         short_exit if short_quote else None,
                         reason, pnl, ret, row["position_id"]),
                    )
                    closed += 1
            except Exception as exc:
                errors.append({"portfolio_id": row["portfolio_id"], "error": f"{type(exc).__name__}: {exc}"})

        for signal in detect_signals(bars_by_symbol):
            spec = next(x for x in SPECS if x.portfolio_id == signal["portfolio_id"])
            try:
                signal_id = _stable_id(spec.portfolio_id, signal["setup_id"], signal["signal_at"])
                if db.execute("select 1 from signals where signal_id=?", (signal_id,)).fetchone():
                    continue
                db.execute("insert into signals(signal_id,portfolio_id,symbol,setup_id,direction,signal_at,detected_at,payload_json) values (?,?,?,?,?,?,?,?)", (
                    signal_id, spec.portfolio_id, spec.symbol, signal["setup_id"], signal["direction"],
                    signal["signal_at"], moment.isoformat(), json.dumps(signal, sort_keys=True),
                ))
                if db.execute("select 1 from positions where portfolio_id=? and status='OPEN'", (spec.portfolio_id,)).fetchone():
                    db.execute("update signals set disposition='SKIPPED',skip_reason='OPEN_POSITION' where signal_id=?", (signal_id,))
                    continue
                block_reason = _entry_block_reason(db, spec, signal, moment)
                if block_reason:
                    db.execute("update signals set disposition='SKIPPED',skip_reason=? where signal_id=?", (block_reason, signal_id))
                    continue
                today_count = db.execute(
                    "select count(*) from positions where portfolio_id=? and substr(entry_at,1,10)=?",
                    (spec.portfolio_id, moment.date().isoformat()),
                ).fetchone()[0]
                if today_count >= spec.maximum_new_positions_per_day:
                    db.execute("update signals set disposition='SKIPPED',skip_reason='DAILY_LIMIT' where signal_id=?", (signal_id,))
                    continue
                spot = provider.stock(spec.symbol)
                option_type = "call" if signal["direction"] == "long" else "put"
                chain = provider.chain(spec, moment.date())
                contract = _select_contract(spec, chain, spot, moment.date(), option_type)
                if not contract:
                    db.execute("update signals set disposition='SKIPPED',skip_reason='NO_ELIGIBLE_CONTRACT' where signal_id=?", (signal_id,))
                    errors.append({"portfolio_id": spec.portfolio_id, "error": "no eligible contract"})
                    continue
                symbol = str(contract["symbol"])
                quote = provider.quotes([symbol]).get(symbol)
                if not quote:
                    db.execute("update signals set disposition='SKIPPED',skip_reason='MISSING_ENTRY_QUOTE' where signal_id=?", (signal_id,))
                    errors.append({"portfolio_id": spec.portfolio_id, "error": "missing entry quote"})
                    continue
                long_fill = _fill("entry", quote["bid"], quote["ask"])
                allocation = _equity(db, spec) * spec.risk_fraction
                structure = "long_option"
                short_contract = None
                short_quote = None
                short_fill = None
                fill = long_fill
                quantity = int(allocation // (fill * 100))
                if quantity < 1:
                    spread = _spread_candidate(
                        spec, chain, long_contract=contract, today=moment.date(),
                        required_type=option_type, allocation=allocation,
                    )
                    if spread:
                        short_contract, _ = spread
                        short_symbol = str(short_contract["symbol"])
                        short_quote = provider.quotes([short_symbol]).get(short_symbol)
                        if short_quote:
                            short_fill = _fill("exit", short_quote["bid"], short_quote["ask"])
                            fill = long_fill - short_fill
                            width = abs(float(short_contract["strike"]) - float(contract["strike"]))
                            if fill > 0 and fill < width:
                                quantity = int(allocation // (fill * 100))
                                structure = "debit_spread"
                    if quantity < 1:
                        db.execute("update signals set disposition='SKIPPED',skip_reason='CONTRACT_EXCEEDS_ALLOCATION' where signal_id=?", (signal_id,))
                        continue
                expiration = contract.get("expiration") or contract.get("expiration_date") or contract.get("expiry")
                position_id = "position_" + signal_id
                db.execute(
                    """insert into positions(
                           position_id,portfolio_id,signal_id,status,contract,option_type,expiration,
                           strike,quantity,allocated_capital,entry_at,entry_bid,entry_ask,entry_fill,
                           underlying_entry,last_mark_at,last_bid,last_ask,structure,short_contract,
                           short_strike,short_entry_bid,short_entry_ask,short_entry_fill,
                           short_last_bid,short_last_ask
                       ) values (?,?,?,'OPEN',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (position_id, spec.portfolio_id, signal_id, symbol, option_type, str(expiration),
                     float(contract["strike"]), quantity, quantity * fill * 100, moment.isoformat(),
                     quote["bid"], quote["ask"], fill, spot, moment.isoformat(), quote["bid"], quote["ask"],
                     structure, str(short_contract["symbol"]) if short_contract else None,
                     float(short_contract["strike"]) if short_contract else None,
                     short_quote["bid"] if short_quote else None,
                     short_quote["ask"] if short_quote else None, short_fill,
                     short_quote["bid"] if short_quote else None,
                     short_quote["ask"] if short_quote else None),
                )
                db.execute("update signals set disposition='OPENED' where signal_id=?", (signal_id,))
                opened += 1
            except Exception as exc:
                db.execute("update signals set disposition='ERROR',skip_reason=? where signal_id=?",
                           (f"{type(exc).__name__}: {exc}", signal_id))
                errors.append({"portfolio_id": spec.portfolio_id, "error": f"{type(exc).__name__}: {exc}"})
        opportunity_updates = update_signal_outcomes(db, bars_by_symbol, now_et=moment)
        db.commit()
        summary = portfolio_status(db)
        db.execute("update runs set completed_at=?,status=?,summary_json=? where run_id=?",
                   (datetime.now(timezone.utc).isoformat(), "DEGRADED" if errors else "OK",
                    json.dumps(summary, sort_keys=True), run_id))
        db.commit()
        return {"paper_only": True, "opened": opened, "closed": closed,
                "errors": errors, "opportunity_updates": opportunity_updates,
                "portfolios": summary}
    except Exception as exc:
        db.execute("update runs set completed_at=?,status='ERROR',error=? where run_id=?",
                   (datetime.now(timezone.utc).isoformat(), f"{type(exc).__name__}: {exc}", run_id))
        db.commit()
        raise
    finally:
        db.close()


def portfolio_status(db: sqlite3.Connection) -> list[dict]:
    out = []
    for spec in SPECS:
        closed = db.execute("select count(*),coalesce(sum(pnl),0),sum(case when pnl>0 then 1 else 0 end) from positions where portfolio_id=? and status='CLOSED'", (spec.portfolio_id,)).fetchone()
        open_count = db.execute("select count(*) from positions where portfolio_id=? and status='OPEN'", (spec.portfolio_id,)).fetchone()[0]
        equity = spec.starting_cash + float(closed[1] or 0)
        out.append({
            "portfolio_id": spec.portfolio_id, "strategy": spec.strategy,
            "starting_cash": spec.starting_cash, "realized_equity": equity,
            "realized_pnl": equity - spec.starting_cash, "closed_trades": int(closed[0]),
            "wins": int(closed[2] or 0), "open_positions": int(open_count),
            "risk_fraction": spec.risk_fraction, "enabled": spec.enabled,
        })
    return out
