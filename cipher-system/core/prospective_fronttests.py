"""Prospective shadow monitoring for frozen niche rules and weekly radars.

The module records only signals observable after registration, option entries at
observed asks, marks at observed bid/ask, and exits at observed bids.  It never
backfills a missed signal and contains no account, broker, or order interface.

GEX is a public-OI heuristic, not verified dealer positioning. Missing GEX,
open interest, gamma, quotes, or contracts remains missing and blocks the
corresponding observation.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, Sequence
from zoneinfo import ZoneInfo

import pandas as pd

from core.evidence_contract import SignalRecord


NY = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "prospective_fronttests" / "prospective_fronttests.sqlite"
DEFAULT_GEX_DB = ROOT / "data" / "gex_history.sqlite"
WEEK_START = date(2026, 8, 17)
WEEK_END = date(2026, 8, 21)
MAX_OPTION_SPREAD_PCT = 12.0


@dataclass(frozen=True, slots=True)
class RadarSpec:
    ticker: str
    pivot: float
    upside_targets: tuple[float, ...]
    downside_targets: tuple[float, ...]
    bullish_strikes: tuple[float, ...]
    bearish_strikes: tuple[float, ...]
    bullish_only: bool = False


RADAR_SPECS = (
    RadarSpec("SPY", 779.82, (787.40, 798.26), (768.21, 759.62), (790, 795), (770, 765)),
    RadarSpec("AAPL", 309.21, (317.37, 324.04), (300.71, 293.87), (315, 325), (300, 295)),
    RadarSpec("AMZN", 262.31, (270.91, 278.73), (256.94, 250.19), (280, 300), (250, 240)),
    RadarSpec("NFLX", 78.53, (81.03, 83.54), (76.09, 72.99), (79, 82), (76, 74)),
    RadarSpec("META", 602.14, (614.82, 628.65), (588.60, 577.37), (610, 620), (590, 580)),
    RadarSpec("GOOGL", 352.53, (358.50, 365.39), (347.25, 340.82), (360, 365), (345, 335)),
    RadarSpec("MU", 986.42, (), (), (1000, 1020), (), True),
    RadarSpec("TSLA", 341.27, (), (), (360,), (), True),
    RadarSpec("NBIS", 277.95, (), (), (280, 300), (), True),
    RadarSpec("ARTW", 3.18, (), (), (), (), True),
)

TSLA_RULE = {
    "approach_pct": 0.005,
    "reclaim_pct": 0.0,
    "impulse_pct": 0.003,
    "minimum_wick_fraction": 0.35,
    "minimum_gex_balance": 0.25,
    "maximum_wall_move_pct": 0.0025,
    "stop_buffer_pct": 0.0025,
    "reward_risk": 1.5,
    "maximum_hold_minutes": 60,
    "one_signal_per_day": True,
    "historical_status": "DESCRIPTIVE_ONLY_AUDIT_CLUSTER",
}


class Market(Protocol):
    def chain(self, ticker: str, start: date, end: date) -> Sequence[Mapping[str, Any]]: ...
    def quotes(self, symbols: Sequence[str]) -> Mapping[str, Mapping[str, Any]]: ...
    def stock(self, ticker: str) -> float: ...


def _stable_id(*parts: object) -> str:
    return hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:32]


def _configuration_hash(configuration_json: str) -> str:
    """Fingerprint the exact frozen rule, independent of database row identity."""
    return hashlib.sha256(configuration_json.encode("utf-8")).hexdigest()


def connect(path: Path = DEFAULT_DB) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript("""
      pragma journal_mode=WAL;
      pragma foreign_keys=on;
      create table if not exists programs(
        program_id text primary key, name text not null, kind text not null,
        starts_at text not null, ends_at text, configuration_json text not null,
        minimum_sample integer not null, status text not null,
        created_at text not null, execution_authority integer not null default 0
      );
      create table if not exists signals(
        signal_id text primary key, program_id text not null references programs(program_id),
        ticker text not null, setup_id text not null, direction text not null,
        signal_bar_at text not null, available_at text not null,
        underlying_entry real not null, target real, stop real, deadline_at text not null,
        status text not null, outcome text, exit_at text, underlying_exit real,
        gross_underlying_return_pct real, target_hits_json text not null default '[]',
        payload_json text not null, updated_at text not null
      );
      create index if not exists ix_prospective_signals_status on signals(status,deadline_at);
      create table if not exists option_legs(
        leg_id text primary key, signal_id text not null references signals(signal_id),
        selector text not null, contract text not null, option_type text not null,
        expiration text not null, strike real not null, status text not null,
        entry_at text not null, entry_bid real not null, entry_ask real not null,
        entry_fill real not null, entry_spread_pct real not null,
        last_mark_at text, last_bid real, last_ask real,
        exit_at text, exit_bid real, exit_ask real, exit_fill real,
        pnl_per_contract real, return_on_debit_pct real,
        unique(signal_id,contract)
      );
      create table if not exists events(
        event_id integer primary key autoincrement, recorded_at text not null,
        program_id text, signal_id text, event_type text not null, payload_json text not null
      );
      create table if not exists runs(
        run_id integer primary key autoincrement, started_at text not null,
        completed_at text, status text not null, summary_json text, error text
      );
      create table if not exists observations(
        observation_id text primary key, run_id integer not null references runs(run_id),
        program_id text not null references programs(program_id), ticker text not null,
        observed_at text not null, latest_bar_at text, bar_age_seconds real,
        bars_available integer not null, coverage_status text not null,
        decision text not null, reason text not null, payload_json text not null,
        unique(run_id,program_id,ticker)
      );
      create index if not exists ix_observations_program_time
        on observations(program_id,observed_at desc);
    """)
    now = datetime.now(timezone.utc).isoformat()
    registrations = (
        (
            "tsla_stable_wall_rejection_v1", "TSLA stable-wall rejection v1", "ticker_rule",
            "2026-08-17T13:30:00+00:00", None, TSLA_RULE, 20,
        ),
        (
            "spartan_weekly_radar_2026_08_17", "Spartan conditional radar — Aug 17, 2026",
            "weekly_radar", "2026-08-17T13:30:00+00:00", "2026-08-21T20:00:00+00:00",
            {"week_start": WEEK_START.isoformat(), "week_end": WEEK_END.isoformat(),
             "specs": [asdict(spec) for spec in RADAR_SPECS]}, 1,
        ),
    )
    for program_id, name, kind, starts_at, ends_at, configuration, minimum_sample in registrations:
        db.execute(
            """insert into programs values (?,?,?,?,?,?,?,'REGISTERED',?,0)
               on conflict(program_id) do nothing""",
            (program_id, name, kind, starts_at, ends_at,
             json.dumps(configuration, sort_keys=True), minimum_sample, now),
        )
    # A weekly level is not an executable target if price already passed it before
    # the first observable signal close. Preserve any early records for audit, but
    # quarantine them from both the sample and option P/L instead of deleting history.
    invalid = db.execute(
        """select signal_id,program_id,ticker,direction,underlying_entry,target
             from signals where program_id like 'spartan_%' and status!='VOID'
              and target is not null and (
                (direction='long' and target<=underlying_entry) or
                (direction='short' and target>=underlying_entry)
              )"""
    ).fetchall()
    for row in invalid:
        db.execute(
            """update signals set status='VOID',outcome='TARGET_ALREADY_PASSED_AT_SIGNAL',
                      updated_at=? where signal_id=?""", (now, row["signal_id"]),
        )
        db.execute("update option_legs set status='VOID' where signal_id=?", (row["signal_id"],))
        _event(
            db, "SIGNAL_VOIDED",
            {"reason": "TARGET_ALREADY_PASSED_AT_SIGNAL", "underlying_entry": row["underlying_entry"],
             "target": row["target"], "direction": row["direction"]},
            program_id=row["program_id"], signal_id=row["signal_id"],
        )
    db.commit()
    return db


def _normalise_bars(rows: Iterable[Mapping[str, Any]], now: datetime) -> pd.DataFrame:
    values = []
    for row in rows:
        values.append({
            "timestamp": row.get("time") or row.get("t"),
            "open": row.get("open", row.get("o")), "high": row.get("high", row.get("h")),
            "low": row.get("low", row.get("l")), "close": row.get("close", row.get("c")),
            "volume": row.get("volume", row.get("v")),
        })
    frame = pd.DataFrame(values)
    if frame.empty:
        return frame
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    current = pd.Timestamp(now.astimezone(timezone.utc))
    # Alpaca timestamps are interval starts. A signal candle must be closed.
    frame = frame.loc[frame["timestamp"] + pd.Timedelta(minutes=5) <= current].copy()
    local = frame["timestamp"].dt.tz_convert(NY)
    frame["date"] = local.dt.strftime("%Y-%m-%d")
    minute = local.dt.hour * 60 + local.dt.minute
    frame = frame.loc[local.dt.weekday.lt(5) & minute.between(9 * 60 + 30, 15 * 60 + 55)]
    return frame.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)


def latest_tsla_gex(
    signal_bar_at: datetime, db_path: Path = DEFAULT_GEX_DB,
) -> dict[str, Any] | None:
    if not db_path.is_file():
        return None
    db = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        rows = db.execute(
            """select id,captured_at,spot,call_wall_strike,put_wall_strike
                 from gex_snapshots where ticker='TSLA' and captured_at<=?
                  and substr(captured_at,1,10)=? order by captured_at desc limit 2""",
            (signal_bar_at.astimezone(timezone.utc).isoformat(), signal_bar_at.date().isoformat()),
        ).fetchall()
        if not rows:
            return None
        latest = rows[0]
        age = signal_bar_at.astimezone(timezone.utc) - datetime.fromisoformat(latest["captured_at"])
        if age > timedelta(minutes=75):
            return None
        aggregate = db.execute(
            """select count(*) as listed,
                      sum(case when available=1 then 1 else 0 end) as available,
                      sum(case when available=1 then net_gex end) as net_gex,
                      sum(case when available=1 then abs(call_gex)+abs(put_gex) end) as abs_gex,
                      sum(case when available=1 then call_oi+put_oi end) as total_oi
                 from gex_strike_cells where snapshot_id=?""",
            (latest["id"],),
        ).fetchone()
        if not aggregate or not aggregate["abs_gex"] or not aggregate["listed"]:
            return None
        previous = rows[1] if len(rows) > 1 else None
        def move(name: str) -> float | None:
            if not previous or latest[name] is None or previous[name] is None or not latest["spot"]:
                return None
            return (float(latest[name]) - float(previous[name])) / float(latest["spot"])
        return {
            "snapshot_id": str(latest["id"]), "captured_at": latest["captured_at"],
            "call_wall": float(latest["call_wall_strike"]),
            "put_wall": float(latest["put_wall_strike"]),
            "call_wall_move": move("call_wall_strike"), "put_wall_move": move("put_wall_strike"),
            "gex_balance": float(aggregate["net_gex"]) / float(aggregate["abs_gex"]),
            "available_rate": float(aggregate["available"]) / float(aggregate["listed"]),
            "total_oi": float(aggregate["total_oi"] or 0),
        }
    finally:
        db.close()


def detect_tsla(
    bars: Sequence[Mapping[str, Any]], *, now: datetime, gex_db: Path = DEFAULT_GEX_DB,
    diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    diag = diagnostics if diagnostics is not None else {}
    def reject(reason: str) -> None:
        diag["decision"] = "NO_SIGNAL"
        diag["reason"] = reason

    frame = _normalise_bars(bars, now)
    diag["bars_available"] = len(frame)
    if frame.empty:
        reject("NO_CLOSED_RTH_BAR")
        return None
    bar = frame.iloc[-1]
    bar_end = bar["timestamp"].to_pydatetime() + timedelta(minutes=5)
    age = now.astimezone(timezone.utc) - bar_end
    diag["latest_bar_at"] = bar["timestamp"].isoformat()
    diag["bar_age_seconds"] = max(0.0, age.total_seconds())
    if age > timedelta(minutes=3):
        reject("BETWEEN_SIGNAL_WINDOWS" if age <= timedelta(minutes=6) else "STALE_CLOSED_BAR")
        diag["coverage_status"] = "FRESH" if age <= timedelta(minutes=6) else "STALE"
        return None
    gex = latest_tsla_gex(bar["timestamp"].to_pydatetime(), gex_db)
    if not gex:
        reject("GEX_UNAVAILABLE_OR_STALE")
        return None
    diag["gex_snapshot_id"] = gex["snapshot_id"]
    if gex["available_rate"] < 0.60:
        reject("GEX_COVERAGE_BELOW_60_PERCENT")
        return None
    if gex["total_oi"] < 1_000:
        reject("GEX_OPEN_INTEREST_BELOW_1000")
        return None
    day = frame.loc[frame["date"] == bar["date"]]
    session_open = float(day.iloc[0]["open"])
    candle_range = float(bar["high"] - bar["low"])
    if candle_range <= 0:
        reject("ZERO_RANGE_SIGNAL_BAR")
        return None
    upper_wick = (float(bar["high"]) - max(float(bar["open"]), float(bar["close"]))) / candle_range
    lower_wick = (min(float(bar["open"]), float(bar["close"])) - float(bar["low"])) / candle_range
    call, put = gex["call_wall"], gex["put_wall"]
    stable_call = gex["call_wall_move"] is not None and abs(gex["call_wall_move"]) <= TSLA_RULE["maximum_wall_move_pct"]
    stable_put = gex["put_wall_move"] is not None and abs(gex["put_wall_move"]) <= TSLA_RULE["maximum_wall_move_pct"]
    short = (
        float(bar["high"]) >= call * (1 - TSLA_RULE["approach_pct"])
        and float(bar["high"]) <= call * 1.0075 and float(bar["close"]) <= call
        and float(bar["close"]) < float(bar["open"])
        and float(bar["high"]) / session_open - 1 >= TSLA_RULE["impulse_pct"]
        and upper_wick >= TSLA_RULE["minimum_wick_fraction"] and stable_call
    )
    long = (
        float(bar["low"]) <= put * (1 + TSLA_RULE["approach_pct"])
        and float(bar["low"]) >= put * 0.9925 and float(bar["close"]) >= put
        and float(bar["close"]) > float(bar["open"])
        and float(bar["low"]) / session_open - 1 <= -TSLA_RULE["impulse_pct"]
        and lower_wick >= TSLA_RULE["minimum_wick_fraction"] and stable_put
    )
    if gex["gex_balance"] < TSLA_RULE["minimum_gex_balance"]:
        reject("GEX_BALANCE_BELOW_THRESHOLD")
        return None
    if not (short or long):
        reject("PRICE_RULE_NOT_QUALIFIED")
        return None
    direction = "short" if short else "long"
    wall = call if short else put
    entry = float(bar["close"])
    stop = wall * (1.0025 if short else 0.9975)
    risk = (stop - entry) if short else (entry - stop)
    if risk <= 0:
        reject("INVALID_RISK_GEOMETRY")
        return None
    target = entry - 1.5 * risk if short else entry + 1.5 * risk
    diag["decision"] = "SIGNAL_CANDIDATE"
    diag["reason"] = "QUALIFIED"
    return {
        "program_id": "tsla_stable_wall_rejection_v1", "ticker": "TSLA",
        "setup_id": "call_wall_rejection" if short else "put_wall_bounce",
        "direction": direction, "signal_bar_at": bar["timestamp"].isoformat(),
        "available_at": bar_end.isoformat(), "signal_price": entry,
        "target": target, "stop": stop,
        "deadline_at": (bar_end + timedelta(minutes=60)).isoformat(),
        "feature_snapshot_ids": [gex["snapshot_id"]], "gex": gex,
    }


def detect_radar(
    bars_by_ticker: Mapping[str, Sequence[Mapping[str, Any]]], *, now: datetime,
    diagnostics: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    output = []
    diag = diagnostics if diagnostics is not None else {}
    for spec in RADAR_SPECS:
        frame = _normalise_bars(bars_by_ticker.get(spec.ticker, ()), now)
        if frame.empty:
            diag[spec.ticker] = {"decision": "NO_SIGNAL", "reason": "NO_CLOSED_RTH_BAR"}
            continue
        frame = frame.loc[frame["date"].between(WEEK_START.isoformat(), WEEK_END.isoformat())]
        if frame.empty:
            diag[spec.ticker] = {"decision": "NO_SIGNAL", "reason": "OUTSIDE_PROGRAM_WINDOW"}
            continue
        bar = frame.iloc[-1]
        bar_end = bar["timestamp"].to_pydatetime() + timedelta(minutes=5)
        if now.astimezone(timezone.utc) - bar_end > timedelta(minutes=3):
            age = now.astimezone(timezone.utc) - bar_end
            diag[spec.ticker] = {
                "decision": "NO_SIGNAL",
                "reason": "BETWEEN_SIGNAL_WINDOWS" if age <= timedelta(minutes=6) else "STALE_CLOSED_BAR",
                "coverage_status": "FRESH" if age <= timedelta(minutes=6) else "STALE",
            }
            continue
        first_week_bar = len(frame) == 1
        previous_close = float(frame.iloc[-2]["close"]) if not first_week_bar else None
        directions = []
        if float(bar["close"]) >= spec.pivot and (first_week_bar or previous_close < spec.pivot):
            directions.append(("long", "pivot_hold", spec.upside_targets, spec.bullish_strikes))
        if not spec.bullish_only and float(bar["close"]) < spec.pivot and (first_week_bar or previous_close >= spec.pivot):
            directions.append(("short", "pivot_failure", spec.downside_targets, spec.bearish_strikes))
        for direction, setup, targets, strikes in directions:
            entry = float(bar["close"])
            if targets and (
                (direction == "long" and targets[0] <= entry)
                or (direction == "short" and targets[0] >= entry)
            ):
                diag[spec.ticker] = {
                    "decision": "NO_SIGNAL", "reason": "TARGET_ALREADY_PASSED_AT_SIGNAL",
                    "entry": entry, "first_target": targets[0],
                }
                continue
            diag[spec.ticker] = {"decision": "SIGNAL_CANDIDATE", "reason": "QUALIFIED"}
            output.append({
                "program_id": "spartan_weekly_radar_2026_08_17", "ticker": spec.ticker,
                "setup_id": setup, "direction": direction,
                "signal_bar_at": bar["timestamp"].isoformat(), "available_at": bar_end.isoformat(),
                "signal_price": entry, "target": targets[0] if targets else None,
                "stop": None,
                "deadline_at": datetime.combine(WEEK_END, time(15, 55), NY).astimezone(timezone.utc).isoformat(),
                "pivot": spec.pivot, "targets": list(targets), "requested_strikes": list(strikes),
                "requested_expiration": WEEK_END.isoformat(),
            })
    return output


def _contract_fields(row: Mapping[str, Any]) -> tuple[str, str, str, float, float, float] | None:
    symbol = str(row.get("symbol") or row.get("contract") or "")
    option_type = str(row.get("type") or row.get("option_type") or "").lower()
    expiration = str(row.get("expiration") or row.get("expiration_date") or row.get("expiry") or "")
    try:
        strike, bid, ask = float(row.get("strike")), float(row.get("bid")), float(row.get("ask"))
    except (TypeError, ValueError):
        return None
    if not symbol or option_type not in {"call", "put"} or bid <= 0 or ask <= bid:
        return None
    return symbol, option_type, expiration, strike, bid, ask


def _choose_contracts(signal: Mapping[str, Any], market: Market, today: date) -> list[dict[str, Any]]:
    ticker = str(signal["ticker"])
    direction = str(signal["direction"])
    desired_type = "call" if direction == "long" else "put"
    radar = signal["program_id"].startswith("spartan_")
    start = WEEK_END if radar else today
    end = WEEK_END if radar else today + timedelta(days=7)
    rows = market.chain(ticker, start, end)
    usable = []
    requested = {float(value) for value in signal.get("requested_strikes") or ()}
    spot = float(signal["signal_price"])
    for row in rows:
        fields = _contract_fields(row)
        if not fields:
            continue
        symbol, option_type, expiration, strike, bid, ask = fields
        if option_type != desired_type or not start <= date.fromisoformat(expiration) <= end:
            continue
        if radar and strike not in requested:
            continue
        mid = (bid + ask) / 2
        spread = (ask - bid) / mid * 100
        # Apply the same executable-liquidity ceiling to research cohorts and
        # portfolio simulations. Requested newsletter strikes are preserved in
        # the signal payload when rejected; a wide market is not a usable fill.
        if spread > MAX_OPTION_SPREAD_PCT:
            continue
        oi, volume = row.get("open_interest"), row.get("volume")
        if oi is not None and float(oi) < 100:
            continue
        if volume is not None and float(volume) < 10:
            continue
        score = (abs((date.fromisoformat(expiration) - (WEEK_END if radar else today)).days), abs(strike - spot), spread)
        usable.append((score, {"symbol": symbol, "option_type": option_type, "expiration": expiration,
                               "strike": strike, "bid": bid, "ask": ask, "spread_pct": spread}))
    if radar:
        return [row for _, row in sorted(usable, key=lambda item: item[0])]
    return [min(usable, key=lambda item: item[0])[1]] if usable else []


def _fill(side: str, bid: float, ask: float) -> float:
    return ask * 1.005 + 0.01 if side == "entry" else max(0.0, bid * 0.995 - 0.01)


def _event(db: sqlite3.Connection, event_type: str, payload: Mapping[str, Any], *, program_id: str | None = None, signal_id: str | None = None) -> None:
    db.execute(
        "insert into events(recorded_at,program_id,signal_id,event_type,payload_json) values (?,?,?,?,?)",
        (datetime.now(timezone.utc).isoformat(), program_id, signal_id, event_type, json.dumps(dict(payload), sort_keys=True)),
    )


def _bar_coverage(rows: Sequence[Mapping[str, Any]], now: datetime) -> dict[str, Any]:
    frame = _normalise_bars(rows, now)
    if frame.empty:
        return {"bars_available": 0, "coverage_status": "MISSING", "decision": "NO_SIGNAL",
                "reason": "NO_CLOSED_RTH_BAR", "latest_bar_at": None, "bar_age_seconds": None}
    latest = frame.iloc[-1]["timestamp"].to_pydatetime()
    age = max(0.0, (now.astimezone(timezone.utc) - (latest + timedelta(minutes=5))).total_seconds())
    feed_current = age <= 360
    signal_current = age <= 180
    reason = "NO_PIVOT_CROSS" if signal_current else "BETWEEN_SIGNAL_WINDOWS" if feed_current else "STALE_CLOSED_BAR"
    return {"bars_available": len(frame), "coverage_status": "FRESH" if feed_current else "STALE",
            "decision": "NO_SIGNAL", "reason": reason,
            "latest_bar_at": pd.Timestamp(latest).isoformat(), "bar_age_seconds": age}


def _record_observation(
    db: sqlite3.Connection, *, run_id: int, program_id: str, ticker: str,
    now: datetime, details: Mapping[str, Any],
) -> None:
    payload = dict(details)
    reason = str(payload.get("reason") or "UNKNOWN")
    if reason.startswith("GEX_"):
        default_coverage = "PARTIAL"
    elif reason == "STALE_CLOSED_BAR":
        default_coverage = "STALE"
    elif reason in {"NO_CLOSED_RTH_BAR", "PROVIDER_ERROR"}:
        default_coverage = "MISSING"
    else:
        default_coverage = "FRESH"
    coverage = str(payload.get("coverage_status") or default_coverage)
    db.execute(
        """insert into observations values (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (_stable_id(run_id, program_id, ticker), run_id, program_id, ticker,
         now.astimezone(timezone.utc).isoformat(), payload.get("latest_bar_at"),
         payload.get("bar_age_seconds"), int(payload.get("bars_available") or 0), coverage,
         str(payload.get("decision") or "NO_SIGNAL"), reason,
         json.dumps(payload, sort_keys=True)),
    )


def _insert_signal(db: sqlite3.Connection, signal: Mapping[str, Any], market: Market, now: datetime) -> bool:
    signal_day = str(signal["signal_bar_at"])[:10]
    if signal["program_id"] == "tsla_stable_wall_rejection_v1" and db.execute(
        "select 1 from signals where program_id=? and ticker=? and substr(signal_bar_at,1,10)=?",
        (signal["program_id"], signal["ticker"], signal_day),
    ).fetchone():
        return False
    # Weekly radar records only the first crossing in each direction per name.
    if signal["program_id"].startswith("spartan_") and db.execute(
        "select 1 from signals where program_id=? and ticker=? and direction=?",
        (signal["program_id"], signal["ticker"], signal["direction"]),
    ).fetchone():
        return False
    signal_id = _stable_id(signal["program_id"], signal["ticker"], signal["setup_id"], signal["signal_bar_at"])
    if db.execute("select 1 from signals where signal_id=?", (signal_id,)).fetchone():
        return False
    contracts = _choose_contracts(signal, market, now.astimezone(NY).date())
    payload = {**dict(signal), "contract_candidates": contracts,
               "option_selection_status": "SELECTED" if contracts else "NO_ELIGIBLE_OBSERVED_CONTRACT"}
    configuration = db.execute(
        "select configuration_json from programs where program_id=?", (signal["program_id"],)
    ).fetchone()
    if configuration:
        payload["configuration_sha256"] = _configuration_hash(configuration["configuration_json"])
    try:
        payload["signal_record"] = SignalRecord.from_mapping({
            **signal,
            "signal_id": signal_id,
            "strategy": signal["program_id"],
            "evidence_snapshot_ids": signal.get("feature_snapshot_ids") or (),
            "decision": "candidate",
            "configuration_sha256": payload.get("configuration_sha256"),
        }).to_dict()
    except (TypeError, ValueError):
        # A malformed/partial observation remains in the legacy ledger with its
        # original rejection semantics; never invent a timestamp or identity.
        payload["signal_record_error"] = "incomplete_signal_contract"
    db.execute(
        """insert into signals values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (signal_id, signal["program_id"], signal["ticker"], signal["setup_id"], signal["direction"],
         signal["signal_bar_at"], signal["available_at"], float(signal["signal_price"]),
         signal.get("target"), signal.get("stop"), signal["deadline_at"], "OPEN", None, None, None,
         None, "[]", json.dumps(payload, sort_keys=True), now.astimezone(timezone.utc).isoformat()),
    )
    for index, contract in enumerate(contracts):
        fill = _fill("entry", contract["bid"], contract["ask"])
        leg_id = _stable_id(signal_id, contract["symbol"])
        db.execute(
            """insert into option_legs(
                 leg_id,signal_id,selector,contract,option_type,expiration,strike,status,
                 entry_at,entry_bid,entry_ask,entry_fill,entry_spread_pct,
                 last_mark_at,last_bid,last_ask
               ) values (?,?,?,?,?,?,?,'OPEN',?,?,?,?,?,?,?,?)""",
            (leg_id, signal_id, f"requested_{index + 1}" if signal["program_id"].startswith("spartan_") else "atm_nearest",
             contract["symbol"], contract["option_type"], contract["expiration"], contract["strike"],
             now.astimezone(timezone.utc).isoformat(), contract["bid"], contract["ask"], fill,
             contract["spread_pct"], now.astimezone(timezone.utc).isoformat(), contract["bid"], contract["ask"]),
        )
    _event(db, "SIGNAL_OPENED", payload, program_id=signal["program_id"], signal_id=signal_id)
    return True


def _update_signals(
    db: sqlite3.Connection, bars_by_ticker: Mapping[str, Sequence[Mapping[str, Any]]],
    market: Market, now: datetime,
) -> dict[str, int]:
    rows = db.execute("select * from signals where status='OPEN'").fetchall()
    leg_rows = db.execute(
        "select * from option_legs where status='OPEN' and signal_id in (select signal_id from signals where status='OPEN')"
    ).fetchall()
    quotes = market.quotes([row["contract"] for row in leg_rows])
    marked = closed = 0
    for leg in leg_rows:
        quote = quotes.get(leg["contract"])
        if quote and float(quote.get("bid") or 0) > 0 and float(quote.get("ask") or 0) >= float(quote["bid"]):
            db.execute(
                "update option_legs set last_mark_at=?,last_bid=?,last_ask=? where leg_id=?",
                (quote.get("timestamp") or now.astimezone(timezone.utc).isoformat(), quote["bid"], quote["ask"], leg["leg_id"]),
            )
            marked += 1
    for row in rows:
        frame = _normalise_bars(bars_by_ticker.get(row["ticker"], ()), now)
        signal_at = pd.Timestamp(row["signal_bar_at"])
        future = frame.loc[frame["timestamp"] > signal_at]
        payload = json.loads(row["payload_json"])
        target_hits = json.loads(row["target_hits_json"] or "[]")
        targets = payload.get("targets") or ([row["target"]] if row["target"] is not None else [])
        direction = 1 if row["direction"] == "long" else -1
        for target in targets:
            if target in target_hits:
                continue
            touched = bool((future["high"] >= target).any()) if direction > 0 else bool((future["low"] <= target).any())
            if touched:
                target_hits.append(target)
                _event(db, "TARGET_TOUCHED", {"target": target}, program_id=row["program_id"], signal_id=row["signal_id"])
        terminal = None
        terminal_price = None
        terminal_at = None
        if row["program_id"] == "tsla_stable_wall_rejection_v1":
            for bar in future.itertuples(index=False):
                stop_hit = (bar.low <= row["stop"]) if direction > 0 else (bar.high >= row["stop"])
                target_hit = (bar.high >= row["target"]) if direction > 0 else (bar.low <= row["target"])
                if stop_hit:  # conservative when both occur in one 5-minute bar
                    terminal, terminal_price, terminal_at = "STOP", float(row["stop"]), bar.timestamp
                    break
                if target_hit:
                    terminal, terminal_price, terminal_at = "TARGET", float(row["target"]), bar.timestamp
                    break
        deadline = datetime.fromisoformat(row["deadline_at"])
        if terminal is None and now.astimezone(timezone.utc) >= deadline:
            terminal = "TIME_EXIT" if row["program_id"] == "tsla_stable_wall_rejection_v1" else "WEEK_END"
            terminal_price = float(future.iloc[-1]["close"]) if not future.empty else float(row["underlying_entry"])
            terminal_at = future.iloc[-1]["timestamp"] if not future.empty else pd.Timestamp(now)
        if terminal is not None:
            gross = direction * (float(terminal_price) / float(row["underlying_entry"]) - 1) * 100
            db.execute(
                """update signals set status='CLOSED',outcome=?,exit_at=?,underlying_exit=?,
                     gross_underlying_return_pct=?,target_hits_json=?,updated_at=? where signal_id=?""",
                (terminal, pd.Timestamp(terminal_at).isoformat(), terminal_price, gross,
                 json.dumps(target_hits), now.astimezone(timezone.utc).isoformat(), row["signal_id"]),
            )
            for leg in db.execute("select * from option_legs where signal_id=? and status='OPEN'", (row["signal_id"],)).fetchall():
                quote = quotes.get(leg["contract"])
                bid = float(quote["bid"]) if quote else float(leg["last_bid"] or 0)
                ask = float(quote["ask"]) if quote else float(leg["last_ask"] or bid)
                if bid <= 0:
                    continue
                fill = _fill("exit", bid, ask)
                pnl = (fill - float(leg["entry_fill"])) * 100 - 1.30
                db.execute(
                    """update option_legs set status='CLOSED',exit_at=?,exit_bid=?,exit_ask=?,exit_fill=?,
                         pnl_per_contract=?,return_on_debit_pct=? where leg_id=?""",
                    (now.astimezone(timezone.utc).isoformat(), bid, ask, fill, pnl,
                     pnl / (float(leg["entry_fill"]) * 100 + .65) * 100, leg["leg_id"]),
                )
            _event(db, "SIGNAL_CLOSED", {"outcome": terminal, "underlying_return_pct": gross},
                   program_id=row["program_id"], signal_id=row["signal_id"])
            closed += 1
        else:
            db.execute(
                "update signals set target_hits_json=?,updated_at=? where signal_id=?",
                (json.dumps(target_hits), now.astimezone(timezone.utc).isoformat(), row["signal_id"]),
            )
    return {"marked_legs": marked, "closed_signals": closed}


def run_once(
    bars_by_ticker: Mapping[str, Sequence[Mapping[str, Any]]], *, market: Market,
    db_path: Path = DEFAULT_DB, gex_db: Path = DEFAULT_GEX_DB,
    now: datetime | None = None, bar_errors: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    moment = now or datetime.now(timezone.utc)
    db = connect(db_path)
    run_id = db.execute("insert into runs(started_at,status) values (?,'RUNNING')", (moment.astimezone(timezone.utc).isoformat(),)).lastrowid
    opened = 0
    errors: list[str] = []
    try:
        errors_by_ticker = dict(bar_errors or {})
        radar_diag: dict[str, dict[str, Any]] = {}
        candidates = detect_radar(bars_by_ticker, now=moment, diagnostics=radar_diag)
        tsla_diag: dict[str, Any] = {}
        try:
            tsla = detect_tsla(
                bars_by_ticker.get("TSLA", ()), now=moment, gex_db=gex_db,
                diagnostics=tsla_diag,
            )
            if tsla:
                candidates.append(tsla)
        except Exception as exc:  # GEX failure must not suppress weekly radar monitoring
            errors.append(f"TSLA_GEX: {type(exc).__name__}: {exc}")
        inserted: set[tuple[str, str]] = set()
        candidate_keys = {(str(row["program_id"]), str(row["ticker"])) for row in candidates}
        for signal in candidates:
            try:
                did_insert = _insert_signal(db, signal, market, moment)
                opened += int(did_insert)
                if did_insert:
                    inserted.add((str(signal["program_id"]), str(signal["ticker"])))
            except Exception as exc:
                errors.append(f"{signal['ticker']}:{signal['setup_id']}: {type(exc).__name__}: {exc}")
        for spec in RADAR_SPECS:
            key = ("spartan_weekly_radar_2026_08_17", spec.ticker)
            if spec.ticker in errors_by_ticker:
                detail = {"bars_available": 0, "coverage_status": "MISSING", "decision": "NO_SIGNAL",
                          "reason": "PROVIDER_ERROR", "provider_error": errors_by_ticker[spec.ticker]}
            else:
                detail = _bar_coverage(bars_by_ticker.get(spec.ticker, ()), moment)
                detail.update(radar_diag.get(spec.ticker, {}))
                if key in candidate_keys:
                    detail["decision"] = "SIGNAL_OPENED" if key in inserted else "SIGNAL_ALREADY_RECORDED"
                    detail["reason"] = "QUALIFIED" if key in inserted else "DEDUPLICATED"
            _record_observation(db, run_id=run_id, program_id=key[0], ticker=spec.ticker,
                                now=moment, details=detail)
        if "TSLA" in errors_by_ticker:
            tsla_diag = {"bars_available": 0, "coverage_status": "MISSING", "decision": "NO_SIGNAL",
                         "reason": "PROVIDER_ERROR", "provider_error": errors_by_ticker["TSLA"]}
        tsla_key = ("tsla_stable_wall_rejection_v1", "TSLA")
        if tsla_key in candidate_keys:
            tsla_diag["decision"] = "SIGNAL_OPENED" if tsla_key in inserted else "SIGNAL_ALREADY_RECORDED"
            tsla_diag["reason"] = "QUALIFIED" if tsla_key in inserted else "DEDUPLICATED"
        _record_observation(db, run_id=run_id, program_id=tsla_key[0], ticker="TSLA",
                            now=moment, details=tsla_diag)
        updates = _update_signals(db, bars_by_ticker, market, moment)
        summary = status(db)
        result = {"paper_only": True, "execution_authority": False, "opened_signals": opened,
                  **updates, "errors": errors, "observation_rows": len(RADAR_SPECS) + 1,
                  "programs": summary}
        db.execute(
            "update runs set completed_at=?,status=?,summary_json=? where run_id=?",
            (datetime.now(timezone.utc).isoformat(), "DEGRADED" if errors else "OK",
             json.dumps(result, sort_keys=True), run_id),
        )
        db.commit()
        return result
    except Exception as exc:
        db.execute("update runs set completed_at=?,status='ERROR',error=? where run_id=?",
                   (datetime.now(timezone.utc).isoformat(), f"{type(exc).__name__}: {exc}", run_id))
        db.commit()
        raise
    finally:
        db.close()


def status(db: sqlite3.Connection) -> list[dict[str, Any]]:
    output = []
    for program in db.execute("select * from programs order by program_id").fetchall():
        aggregate = db.execute(
            """select count(*) signals,
                      sum(case when status!='VOID' then 1 else 0 end) eligible_signals,
                      sum(case when status='OPEN' then 1 else 0 end) open_signals,
                      sum(case when status='CLOSED' then 1 else 0 end) closed_signals,
                      sum(case when status='VOID' then 1 else 0 end) void_signals,
                      sum(case when gross_underlying_return_pct>0 then 1 else 0 end) wins,
                      avg(gross_underlying_return_pct) avg_return
                 from signals where program_id=?""", (program["program_id"],),
        ).fetchone()
        options = db.execute(
            """select count(*) legs,sum(case when status='OPEN' then 1 else 0 end) open_legs,
                      sum(case when status='VOID' then 1 else 0 end) void_legs,
                      sum(case when status='CLOSED' then pnl_per_contract else 0 end) pnl
                 from option_legs where signal_id in (select signal_id from signals where program_id=?)""",
            (program["program_id"],),
        ).fetchone()
        output.append({
            "program_id": program["program_id"], "name": program["name"], "kind": program["kind"],
            "configuration_sha256": _configuration_hash(program["configuration_json"]),
            "status": program["status"], "starts_at": program["starts_at"], "ends_at": program["ends_at"],
            "minimum_sample": program["minimum_sample"], "signals": int(aggregate["signals"] or 0),
            "eligible_signals": int(aggregate["eligible_signals"] or 0),
            "open_signals": int(aggregate["open_signals"] or 0), "closed_signals": int(aggregate["closed_signals"] or 0),
            "void_signals": int(aggregate["void_signals"] or 0),
            "wins": int(aggregate["wins"] or 0), "average_underlying_return_pct": aggregate["avg_return"],
            "option_legs": int(options["legs"] or 0), "open_option_legs": int(options["open_legs"] or 0),
            "void_option_legs": int(options["void_legs"] or 0),
            "closed_option_pnl": float(options["pnl"] or 0), "execution_authority": False,
        })
    return output


class AlpacaReadOnlyMarket:
    def stock(self, ticker: str) -> float:
        from core import app
        value = app.quote(ticker).get("price_context")
        if value is None:
            raise RuntimeError(f"no stock quote for {ticker}")
        return float(value)

    def chain(self, ticker: str, start: date, end: date) -> Sequence[Mapping[str, Any]]:
        from core import app
        return app.option_chain(ticker, "opra", max_pages=36,
                                expiration_gte=start.isoformat(), expiration_lte=end.isoformat())

    def quotes(self, symbols: Sequence[str]) -> Mapping[str, Mapping[str, Any]]:
        if not symbols:
            return {}
        from core import app
        raw = app.alpaca("/v1beta1/options/snapshots", {
            "symbols": ",".join(sorted(set(symbols))), "feed": app.resolve_options_feed("opra")
        })
        output = {}
        for symbol, snapshot in (raw.get("snapshots") or {}).items():
            quote = snapshot.get("latestQuote") or {}
            bid = quote.get("bp", quote.get("bid_price")); ask = quote.get("ap", quote.get("ask_price"))
            if bid is not None and ask is not None:
                output[symbol] = {"bid": float(bid), "ask": float(ask),
                                  "timestamp": quote.get("t", quote.get("timestamp"))}
        return output
