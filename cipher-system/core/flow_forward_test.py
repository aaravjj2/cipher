"""Forward-test ledger for flow-cluster + Kronos research signals.

This is read-only research infrastructure.  It captures candidate signals from
historical/live LSE option-flow rows, optionally confirms them with Kronos
OHLCV forecasts, and later scores outcomes from local stock bars.

Signals are not orders.  No broker/trading endpoints are used here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parent
import sys

if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from flow_cluster_backtest import (
    DEFAULT_DB as FLOW_DB,
    STOCK_DATA_ROOT,
    build_flow_snapshot,
    download_lse_flow,
    load_stock_bars,
    next_calendar_day,
    number,
    simulate_level_trade,
)
from kronos_research import (
    DEFAULT_MODEL_ID,
    DEFAULT_TOKENIZER_ID,
    kronos_forecast_signal,
    load_local_ohlcv_rows,
    load_predictor,
    set_research_seed,
)

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
FORWARD_DIR = DATA_DIR / "forward_tests"
DEFAULT_FORWARD_DB = DATA_DIR / "flow_forward_test.sqlite"
SCAN_DIR = DATA_DIR / "accessobsidian_scans"
BROWSER_CAPTURE_ROOT = DATA_DIR / "browser_ingest" / "raw_windows" / "device-windows"
DEFAULT_PREREGISTRATION = ROOT / "config" / "kronos_forward_preregistered.json"

DEFAULT_WATCHLIST = ["AMD", "AMZN", "COIN", "GOOGL", "NVDA"]


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def canonical_config_id(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def load_preregistration(path: Path = DEFAULT_PREREGISTRATION) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Kronos preregistration must contain a JSON object")
    required = {
        "model_id",
        "tokenizer_id",
        "timeframe",
        "lookback",
        "pred_bars",
        "sample_count",
        "seed",
        "horizon_sessions",
        "minimum_flow_premium",
        "maximum_dte",
        "minimum_flow_prints",
        "latest_scan_ticker_limit",
        "minimum_absolute_prediction_pct",
        "stop_pct",
        "minimum_target_distance_pct",
        "maximum_target_distance_pct",
        "primary_only",
        "minimum_scored_sample",
    }
    missing = sorted(required - set(payload))
    if missing:
        raise ValueError(f"Kronos preregistration is missing fields: {', '.join(missing)}")
    if payload["timeframe"] not in {"1m", "5m", "15m"}:
        raise ValueError("unsupported preregistered Kronos timeframe")
    payload = dict(payload)
    payload["config_id"] = canonical_config_id(payload)
    payload["source_file"] = str(path.resolve())
    return payload


def ensure_schema(db_path: Path = DEFAULT_FORWARD_DB) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as db:
        db.executescript(
            """
            create table if not exists forward_signals (
                id text primary key,
                created_at text not null,
                as_of text not null,
                ticker text not null,
                horizon integer not null,
                setup_rank integer not null,
                is_primary integer not null,
                kind text,
                side text,
                direction text,
                spot real,
                level real,
                target_distance_pct real,
                stop_pct real,
                min_abs_kronos_pred_return_pct real,
                kronos_available integer,
                kronos_direction text,
                kronos_pred_return_pct real,
                kronos_agrees integer,
                config_id text,
                evaluation_group text,
                eligible_for_scoring integer,
                status text not null,
                signal_json text not null
            );

            create index if not exists idx_forward_signals_due
                on forward_signals(status, as_of, horizon);
            create index if not exists idx_forward_signals_ticker
                on forward_signals(ticker, as_of);

            create table if not exists forward_outcomes (
                signal_id text primary key references forward_signals(id),
                scored_at text not null,
                exit_reason text,
                trade_return_pct real,
                entry real,
                exit real,
                target real,
                stop real,
                payload_json text not null
            );
            """
        )
        columns = {row[1] for row in db.execute("pragma table_info(forward_signals)")}
        migrations = {
            "config_id": "alter table forward_signals add column config_id text",
            "evaluation_group": "alter table forward_signals add column evaluation_group text",
            "eligible_for_scoring": "alter table forward_signals add column eligible_for_scoring integer",
        }
        for name, sql in migrations.items():
            if name not in columns:
                db.execute(sql)


def parse_tickers(raw_values: list[str] | None, *, default: list[str] | None = None) -> list[str]:
    out = []
    seen = set()
    for raw in raw_values or []:
        for part in str(raw).replace(";", ",").split(","):
            ticker = part.upper().strip()
            if ticker and ticker not in seen:
                seen.add(ticker)
                out.append(ticker)
    return out or list(default or DEFAULT_WATCHLIST)


def latest_scan_tickers(scan_dir: Path = SCAN_DIR, limit: int = 20) -> list[str]:
    summaries = sorted(scan_dir.glob("20*/20*/summary.json"), reverse=True)
    for summary in summaries:
        cluster = summary.parent / "cluster.json"
        if not cluster.is_file():
            continue
        try:
            rows = json.loads(cluster.read_text(encoding="utf-8")).get("rows") or []
        except (OSError, json.JSONDecodeError):
            continue
        tickers = []
        seen = set()
        for row in rows:
            ticker = str(row.get("ticker") or "").upper()
            if ticker and ticker not in seen:
                seen.add(ticker)
                tickers.append(ticker)
            if len(tickers) >= int(limit):
                break
        if tickers:
            return tickers

    captures = []
    for folder_name in ("uploaded", "ready"):
        folder = BROWSER_CAPTURE_ROOT / folder_name
        if folder.is_dir():
            captures.extend(folder.glob("cluster_*.json"))
    for capture in sorted(captures, key=lambda path: path.stat().st_mtime, reverse=True):
        try:
            payload = json.loads(capture.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        cards = [card for card in payload.get("cards") or [] if isinstance(card, dict)]
        cards.sort(key=lambda card: (int(number(card.get("rank")) or 999999), str(card.get("ticker") or "")))
        tickers = []
        seen = set()
        for card in cards:
            ticker = str(card.get("ticker") or "").upper().strip()
            if ticker and ticker not in seen:
                seen.add(ticker)
                tickers.append(ticker)
            if len(tickers) >= int(limit):
                break
        if tickers:
            return tickers
    return []


def signal_id(row: dict) -> str:
    return "|".join(
        [
            str(row["as_of"])[:10],
            str(row["ticker"]).upper(),
            str(row["horizon"]),
            str(row.get("setup_rank", 0)),
            str(row.get("kind") or "unknown"),
            f"{float(row.get('level') or 0.0):.4f}",
            f"k{float(row.get('min_abs_kronos_pred_return_pct') or 0.0):.4f}",
            str(row.get("config_id") or "legacy"),
        ]
    )


def make_signal_rows(
    snapshot: dict,
    *,
    horizon: int,
    stop_pct: float,
    predictor,
    kronos_timeframe: str,
    kronos_lookback: int,
    kronos_pred_bars: int,
    kronos_sample_count: int,
    min_abs_kronos_pred_return_pct: float,
    min_target_distance_pct: float = 0.3,
    max_target_distance_pct: float = 5.0,
    primary_only: bool = True,
    pre_registration: dict | None = None,
) -> list[dict]:
    ticker = snapshot["ticker"].upper()
    as_of = snapshot["as_of"][:10]
    spot = number(snapshot.get("spot"))
    if not spot:
        return []
    kronos = kronos_forecast_signal(
        predictor,
        ticker,
        as_of,
        timeframe=kronos_timeframe,
        horizon_days=int(horizon),
        lookback=int(kronos_lookback),
        max_pred_bars=int(kronos_pred_bars),
        sample_count=int(kronos_sample_count),
    )
    pred_ret = number(kronos.get("pred_return_pct"))
    registration = dict(pre_registration or {})
    config_id = str(registration.get("config_id") or "legacy")
    rows = []
    for setup_rank, setup in enumerate(snapshot.get("setups") or []):
        if primary_only and setup_rank != 0:
            continue
        level = number(setup.get("center") or setup.get("high") or setup.get("low"))
        if level is None:
            continue
        direction = "long" if level >= spot else "short"
        target_distance_pct = abs(level - spot) / spot * 100.0 if spot else None
        distance_ok = (
            target_distance_pct is not None
            and target_distance_pct >= float(min_target_distance_pct)
            and target_distance_pct <= float(max_target_distance_pct)
        )
        kronos_agrees = bool(
            kronos.get("available")
            and pred_ret is not None
            and abs(pred_ret) >= float(min_abs_kronos_pred_return_pct)
            and ((direction == "long" and pred_ret > 0) or (direction == "short" and pred_ret < 0))
        )
        if not kronos.get("available"):
            evaluation_group = "unavailable"
        elif kronos_agrees:
            evaluation_group = "agreed"
        else:
            evaluation_group = "disagreed"
        eligible_for_scoring = bool(distance_ok)
        status = "pending" if eligible_for_scoring else "excluded"
        exclusion_reasons = []
        if not distance_ok:
            exclusion_reasons.append("target_distance_out_of_range")
        row = {
            "created_at": utcnow(),
            "as_of": as_of,
            "ticker": ticker,
            "horizon": int(horizon),
            "setup_rank": int(setup_rank),
            "is_primary": setup_rank == 0,
            "kind": f"flow_{setup.get('kind') or 'unknown'}",
            "side": setup.get("side"),
            "direction": direction,
            "spot": round(spot, 4),
            "level": round(level, 4),
            "target_distance_pct": round(target_distance_pct, 4) if target_distance_pct is not None else None,
            "min_target_distance_pct": float(min_target_distance_pct),
            "max_target_distance_pct": float(max_target_distance_pct),
            "stop_pct": float(stop_pct),
            "min_abs_kronos_pred_return_pct": float(min_abs_kronos_pred_return_pct),
            "kronos": kronos,
            "kronos_available": bool(kronos.get("available")),
            "kronos_direction": kronos.get("direction"),
            "kronos_pred_return_pct": pred_ret,
            "kronos_agrees": kronos_agrees,
            "distance_ok": distance_ok,
            "config_id": config_id,
            "evaluation_group": evaluation_group,
            "eligible_for_scoring": eligible_for_scoring,
            "pre_registration": registration,
            "exclusion_reasons": exclusion_reasons,
            "reject_reasons": exclusion_reasons,
            "status": status,
            "caveat": "Prospective context-only signal. Every distance-eligible candidate is scored regardless of Kronos agreement. No orders or broker execution.",
        }
        row["id"] = signal_id(row)
        rows.append(row)
    return rows


def store_signals(rows: list[dict], db_path: Path = DEFAULT_FORWARD_DB) -> dict:
    """Persist the first prediction only; duplicate captures never rewrite it."""
    ensure_schema(db_path)
    inserted = 0
    duplicates = 0
    with sqlite3.connect(db_path) as db:
        for row in rows:
            cursor = db.execute(
                """
                insert or ignore into forward_signals (
                    id, created_at, as_of, ticker, horizon, setup_rank, is_primary,
                    kind, side, direction, spot, level, target_distance_pct, stop_pct,
                    min_abs_kronos_pred_return_pct, kronos_available, kronos_direction,
                    kronos_pred_return_pct, kronos_agrees, config_id, evaluation_group,
                    eligible_for_scoring, status, signal_json
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    row["created_at"],
                    row["as_of"],
                    row["ticker"],
                    row["horizon"],
                    row["setup_rank"],
                    1 if row["is_primary"] else 0,
                    row["kind"],
                    row["side"],
                    row["direction"],
                    row["spot"],
                    row["level"],
                    row["target_distance_pct"],
                    row["stop_pct"],
                    row["min_abs_kronos_pred_return_pct"],
                    1 if row["kronos_available"] else 0,
                    row["kronos_direction"],
                    row["kronos_pred_return_pct"],
                    1 if row["kronos_agrees"] else 0,
                    row.get("config_id"),
                    row.get("evaluation_group"),
                    1 if row.get("eligible_for_scoring") else 0,
                    row["status"],
                    json.dumps(row, separators=(",", ":"), default=str),
                ),
            )
            if cursor.rowcount == 1:
                inserted += 1
            else:
                duplicates += 1
    return {
        "attempted": len(rows),
        "inserted": inserted,
        "duplicates_preserved": duplicates,
        "db_path": str(db_path),
    }


def capture_forward(
    tickers: list[str],
    *,
    as_of: str | None = None,
    download: bool = True,
    flow_db: Path = FLOW_DB,
    db_path: Path = DEFAULT_FORWARD_DB,
    min_premium: float = 25_000.0,
    max_dte: int = 45,
    min_prints: int = 20,
    horizon: int = 2,
    stop_pct: float = 0.03,
    min_abs_kronos_pred_return_pct: float = 0.3,
    min_target_distance_pct: float = 0.3,
    max_target_distance_pct: float = 5.0,
    kronos_timeframe: str = "5m",
    kronos_lookback: int = 128,
    kronos_pred_bars: int = 32,
    kronos_sample_count: int = 1,
    seed: int | None = 42,
    primary_only: bool = True,
    model_id: str = DEFAULT_MODEL_ID,
    tokenizer_id: str = DEFAULT_TOKENIZER_ID,
    pre_registration: dict | None = None,
) -> dict:
    as_of = (as_of or date.today().isoformat())[:10]
    set_research_seed(seed)
    download_result = None
    if download:
        download_result = download_lse_flow(
            tickers,
            start=as_of,
            end=as_of,
            db_path=flow_db,
            min_premium=min_premium,
            max_dte=max_dte,
            sleep_ms=200,
        )
    predictor = load_predictor(model_id=model_id, tokenizer_id=tokenizer_id)
    all_rows = []
    skipped = []
    for ticker in tickers:
        snapshot = build_flow_snapshot(flow_db, ticker, as_of, min_prints=min_prints, write=True)
        if not snapshot or not snapshot.get("setups"):
            skipped.append({"ticker": ticker, "reason": "no_flow_snapshot_or_setups"})
            continue
        all_rows.extend(
            make_signal_rows(
                snapshot,
                horizon=horizon,
                stop_pct=stop_pct,
                predictor=predictor,
                kronos_timeframe=kronos_timeframe,
                kronos_lookback=kronos_lookback,
                kronos_pred_bars=kronos_pred_bars,
                kronos_sample_count=kronos_sample_count,
                min_abs_kronos_pred_return_pct=min_abs_kronos_pred_return_pct,
                min_target_distance_pct=min_target_distance_pct,
                max_target_distance_pct=max_target_distance_pct,
                primary_only=primary_only,
                pre_registration=pre_registration,
            )
        )
    store = store_signals(all_rows, db_path=db_path)
    eligible = [r for r in all_rows if r.get("eligible_for_scoring")]
    agreed = [r for r in eligible if r.get("evaluation_group") == "agreed"]
    disagreed = [r for r in eligible if r.get("evaluation_group") == "disagreed"]
    unavailable = [r for r in eligible if r.get("evaluation_group") == "unavailable"]
    excluded = [r for r in all_rows if not r.get("eligible_for_scoring")]
    payload = {
        "as_of": as_of,
        "created_at": utcnow(),
        "mode": "flow_kronos_forward_capture",
        "tickers": tickers,
        "download_result": download_result,
        "signals_n": len(all_rows),
        "eligible_n": len(eligible),
        "agreed_n": len(agreed),
        "disagreed_n": len(disagreed),
        "unavailable_n": len(unavailable),
        "excluded_n": len(excluded),
        "skipped": skipped,
        "agreed": agreed,
        "disagreed": disagreed,
        "unavailable": unavailable,
        "excluded": excluded,
        "settings": {
            "horizon": int(horizon),
            "stop_pct": float(stop_pct),
            "min_abs_kronos_pred_return_pct": float(min_abs_kronos_pred_return_pct),
            "min_target_distance_pct": float(min_target_distance_pct),
            "max_target_distance_pct": float(max_target_distance_pct),
            "kronos_timeframe": kronos_timeframe,
            "kronos_lookback": int(kronos_lookback),
            "kronos_pred_bars": int(kronos_pred_bars),
            "kronos_sample_count": int(kronos_sample_count),
            "model_id": model_id,
            "tokenizer_id": tokenizer_id,
            "primary_only": bool(primary_only),
        },
        "pre_registration": pre_registration or {},
        "store": store,
        "caveat": "Prospective context-only capture. Kronos does not gate entries or sizing; all distance-eligible groups are scored. No order execution.",
    }
    FORWARD_DIR.mkdir(parents=True, exist_ok=True)
    out = FORWARD_DIR / f"flow_kronos_forward_{as_of}_{stamp()}.json"
    out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    payload["path"] = str(out)
    return payload


def local_daily_bars(ticker: str, start: str, end: str, timeframe: str = "5m") -> list[dict]:
    start_day = start[:10]
    end_day = end[:10]
    by_day: dict[str, dict] = {}
    for row in load_local_ohlcv_rows(ticker, timeframe):
        day = row["timestamp"].date().isoformat()
        if day < start_day or day > end_day:
            continue
        current = by_day.get(day)
        if current is None:
            by_day[day] = {
                "time": day,
                "open": row["open"],
                "high": row["high"],
                "low": row["low"],
                "close": row["close"],
                "volume": row.get("volume") or 0.0,
            }
            continue
        current["high"] = max(current["high"], row["high"])
        current["low"] = min(current["low"], row["low"])
        current["close"] = row["close"]
        current["volume"] += row.get("volume") or 0.0
    return [by_day[day] for day in sorted(by_day)]


def open_signals(db_path: Path = DEFAULT_FORWARD_DB) -> list[dict]:
    ensure_schema(db_path)
    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute(
            """
            select * from forward_signals
            where status in ('pending', 'open', 'rejected')
            order by as_of, ticker, horizon
            """
        ).fetchall()
    out = []
    for row in rows:
        try:
            signal = json.loads(row["signal_json"])
        except Exception:
            signal = dict(row)
        eligible = signal.get("eligible_for_scoring")
        if eligible is None:
            eligible = bool(signal.get("distance_ok"))
        if eligible:
            out.append(signal)
    return out


def summarize_values(values: list[float]) -> dict:
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value < 0]
    loss_sum = -sum(losses)
    profit_factor = None
    if values:
        profit_factor = 999.0 if loss_sum <= 0 and wins else (sum(wins) / loss_sum if loss_sum else None)
    return {
        "n": len(values),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(values), 4) if values else None,
        "avg_return_pct": round(sum(values) / len(values), 4) if values else None,
        "total_return_points": round(sum(values), 4) if values else 0.0,
        "profit_factor": round(profit_factor, 4) if profit_factor is not None else None,
    }


def grouped_outcome_summary(rows: list[dict]) -> dict:
    groups: dict[str, list[float]] = {}
    for row in rows:
        group = str(row.get("evaluation_group") or "legacy")
        outcome = row.get("outcome") or {}
        value = number(outcome.get("trade_return_pct"))
        if value is not None:
            groups.setdefault(group, []).append(value)
    return {name: summarize_values(values) for name, values in sorted(groups.items())}


def score_open(
    *,
    db_path: Path = DEFAULT_FORWARD_DB,
    local_timeframe: str = "5m",
    local_stock_root: Path = STOCK_DATA_ROOT,
) -> dict:
    ensure_schema(db_path)
    signals = open_signals(db_path)
    scored = []
    pending = []
    for sig in signals:
        start_day = next_calendar_day(sig["as_of"], 1)
        end_day = next_calendar_day(sig["as_of"], int(sig["horizon"]) + 10)
        bars, bar_source = load_stock_bars(
            sig["ticker"],
            start_day,
            end_day,
            provider="local",
            local_timeframe=local_timeframe,
            local_root=local_stock_root,
        )
        if not bars:
            bars = local_daily_bars(sig["ticker"], start_day, end_day, timeframe=local_timeframe)
            bar_source = "historical_bars_sqlite" if bars else bar_source
        eval_bars = bars[: int(sig["horizon"])]
        if len(eval_bars) < int(sig["horizon"]):
            pending.append({"id": sig["id"], "ticker": sig["ticker"], "as_of": sig["as_of"], "bars": len(eval_bars)})
            continue
        trade = simulate_level_trade(
            spot=number(sig.get("spot")),
            level=number(sig.get("level")),
            bars=eval_bars,
            stop_pct=float(sig.get("stop_pct") or 0.03),
        )
        if trade.get("trade_return_pct") is None:
            pending.append({"id": sig["id"], "ticker": sig["ticker"], "as_of": sig["as_of"], "reason": trade.get("skip_reason")})
            continue
        payload = {**sig, "outcome": trade, "bar_source": bar_source, "scored_at": utcnow()}
        with sqlite3.connect(db_path) as db:
            db.execute(
                """
                insert or ignore into forward_outcomes (
                    signal_id, scored_at, exit_reason, trade_return_pct, entry, exit, target, stop, payload_json
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sig["id"],
                    payload["scored_at"],
                    trade.get("exit_reason"),
                    trade.get("trade_return_pct"),
                    trade.get("entry"),
                    trade.get("exit"),
                    trade.get("target"),
                    trade.get("stop"),
                    json.dumps(payload, separators=(",", ":"), default=str),
                ),
            )
            db.execute("update forward_signals set status = 'scored' where id = ?", (sig["id"],))
        scored.append(payload)
    values = [float(r["outcome"]["trade_return_pct"]) for r in scored]
    payload = {
        "scored_at": utcnow(),
        "mode": "flow_kronos_forward_score",
        "scored_n": len(scored),
        "pending_n": len(pending),
        "summary": summarize_values(values),
        "by_evaluation_group": grouped_outcome_summary(scored),
        "scored": scored,
        "pending": pending,
        "db_path": str(db_path),
    }
    FORWARD_DIR.mkdir(parents=True, exist_ok=True)
    out = FORWARD_DIR / f"flow_kronos_score_{stamp()}.json"
    out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    payload["path"] = str(out)
    return payload


def prospective_report(
    db_path: Path = DEFAULT_FORWARD_DB,
    preregistration_path: Path = DEFAULT_PREREGISTRATION,
) -> dict:
    ensure_schema(db_path)
    registration = load_preregistration(preregistration_path)
    config_id = registration["config_id"]
    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute(
            """
            select
                s.id, s.created_at, s.as_of, s.ticker, s.direction, s.level,
                s.config_id, s.evaluation_group, s.eligible_for_scoring, s.status,
                o.trade_return_pct, o.payload_json
            from forward_signals s
            left join forward_outcomes o on o.signal_id = s.id
            where s.config_id = ?
            order by s.created_at, s.ticker
            """,
            (config_id,),
        ).fetchall()
    scored_rows = []
    status_counts: dict[str, int] = {}
    for row in rows:
        status = str(row["status"] or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
        if row["trade_return_pct"] is None:
            continue
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            payload = {}
        payload.setdefault("evaluation_group", row["evaluation_group"] or "legacy")
        payload.setdefault("outcome", {})["trade_return_pct"] = float(row["trade_return_pct"])
        scored_rows.append(payload)
    values = [float(row["outcome"]["trade_return_pct"]) for row in scored_rows]
    minimum = int(registration["minimum_scored_sample"])
    scored_n = len(scored_rows)
    payload = {
        "generated_at": utcnow(),
        "mode": "kronos_preregistered_prospective_report",
        "db_path": str(db_path),
        "pre_registration": registration,
        "status_counts": status_counts,
        "overall": summarize_values(values),
        "by_evaluation_group": grouped_outcome_summary(scored_rows),
        "progress": {
            "scored": scored_n,
            "minimum_required": minimum,
            "remaining": max(0, minimum - scored_n),
            "minimum_reached": scored_n >= minimum,
        },
        "deployment_rule": registration["decision_rule"],
    }
    FORWARD_DIR.mkdir(parents=True, exist_ok=True)
    latest_json = FORWARD_DIR / "latest_kronos_prospective_report.json"
    latest_md = FORWARD_DIR / "latest_kronos_prospective_report.md"
    latest_json.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    lines = [
        "# Kronos Preregistered Prospective Report",
        "",
        f"- Generated: {payload['generated_at']}",
        f"- Configuration: `{config_id}`",
        f"- Scored: {scored_n}/{minimum}",
        f"- Minimum reached: {payload['progress']['minimum_reached']}",
        f"- Status counts: {status_counts}",
        "",
        "## Results by Evaluation Group",
        "",
        "| Group | N | Win rate | Average return | Total return points | Profit factor |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for group in ("agreed", "disagreed", "unavailable", "legacy"):
        summary = payload["by_evaluation_group"].get(group)
        if not summary:
            continue
        win_rate = "" if summary["win_rate"] is None else f"{summary['win_rate'] * 100:.1f}%"
        avg_return = "" if summary["avg_return_pct"] is None else f"{summary['avg_return_pct']:.4f}%"
        pf = "" if summary["profit_factor"] is None else str(summary["profit_factor"])
        lines.append(
            f"| {group} | {summary['n']} | {win_rate} | {avg_return} | "
            f"{summary['total_return_points']:.4f} | {pf} |"
        )
    lines.extend(
        [
            "",
            "## Locked Deployment Rule",
            "",
            registration["decision_rule"],
            "",
            "Kronos remains context-only. This report does not authorize orders or sizing changes.",
        ]
    )
    latest_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    payload["json_path"] = str(latest_json)
    payload["markdown_path"] = str(latest_md)
    return payload


def list_forward(db_path: Path = DEFAULT_FORWARD_DB) -> dict:
    ensure_schema(db_path)
    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        status_counts = [dict(r) for r in db.execute("select status, count(*) as n from forward_signals group by status")]
        recent = [dict(r) for r in db.execute("select id, as_of, ticker, horizon, kind, direction, level, config_id, evaluation_group, eligible_for_scoring, kronos_pred_return_pct, status from forward_signals order by created_at desc limit 50")]
    return {"db_path": str(db_path), "status_counts": status_counts, "recent": recent}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Forward-test flow-cluster + Kronos signals.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    cap = sub.add_parser("capture", help="Capture today's/read-date flow+Kronos forward signals.")
    cap.add_argument("--ticker", action="append", default=[])
    cap.add_argument("--from-latest-scan", action="store_true", help="Use tickers from the latest captured AccessObsidian cluster scan.")
    cap.add_argument("--scan-limit", type=int, default=20)
    cap.add_argument("--as-of")
    cap.add_argument("--no-download", action="store_true")
    cap.add_argument("--min-premium", type=float, default=25_000.0)
    cap.add_argument("--max-dte", type=int, default=45)
    cap.add_argument("--min-prints", type=int, default=20)
    cap.add_argument("--horizon", type=int, default=2)
    cap.add_argument("--stop-pct", type=float, default=0.03)
    cap.add_argument("--min-abs-kronos-pred-return-pct", type=float, default=0.3)
    cap.add_argument("--min-target-distance-pct", type=float, default=0.3)
    cap.add_argument("--max-target-distance-pct", type=float, default=5.0)
    cap.add_argument("--kronos-timeframe", choices=("1m", "5m", "15m"), default="5m")
    cap.add_argument("--kronos-lookback", type=int, default=128)
    cap.add_argument("--kronos-pred-bars", type=int, default=32)
    cap.add_argument("--kronos-sample-count", type=int, default=1)
    cap.add_argument("--seed", type=int, default=42)
    cap.add_argument("--all-setups", action="store_true")
    cap.add_argument("--db", default=str(DEFAULT_FORWARD_DB))
    cap.add_argument("--flow-db", default=str(FLOW_DB))
    cap.add_argument("--preregistration", default=str(DEFAULT_PREREGISTRATION))

    score = sub.add_parser("score", help="Score open signals when enough local future bars exist.")
    score.add_argument("--db", default=str(DEFAULT_FORWARD_DB))
    score.add_argument("--local-timeframe", choices=("1m", "5m", "15m"), default="5m")
    score.add_argument("--local-stock-root", default=str(STOCK_DATA_ROOT))
    score.add_argument("--preregistration", default=str(DEFAULT_PREREGISTRATION))

    report = sub.add_parser("report", help="Write the cumulative preregistered prospective report.")
    report.add_argument("--db", default=str(DEFAULT_FORWARD_DB))
    report.add_argument("--preregistration", default=str(DEFAULT_PREREGISTRATION))

    ls = sub.add_parser("list", help="List forward-test ledger state.")
    ls.add_argument("--db", default=str(DEFAULT_FORWARD_DB))

    args = parser.parse_args(argv)
    if args.cmd == "capture":
        registration = load_preregistration(Path(args.preregistration))
        scan_limit = int(registration["latest_scan_ticker_limit"])
        default_tickers = latest_scan_tickers(limit=scan_limit) if args.from_latest_scan else DEFAULT_WATCHLIST
        payload = capture_forward(
            parse_tickers(args.ticker, default=default_tickers),
            as_of=args.as_of,
            download=not args.no_download,
            flow_db=Path(args.flow_db),
            db_path=Path(args.db),
            min_premium=float(registration["minimum_flow_premium"]),
            max_dte=int(registration["maximum_dte"]),
            min_prints=int(registration["minimum_flow_prints"]),
            horizon=int(registration["horizon_sessions"]),
            stop_pct=float(registration["stop_pct"]),
            min_abs_kronos_pred_return_pct=float(registration["minimum_absolute_prediction_pct"]),
            min_target_distance_pct=float(registration["minimum_target_distance_pct"]),
            max_target_distance_pct=float(registration["maximum_target_distance_pct"]),
            kronos_timeframe=str(registration["timeframe"]),
            kronos_lookback=int(registration["lookback"]),
            kronos_pred_bars=int(registration["pred_bars"]),
            kronos_sample_count=int(registration["sample_count"]),
            seed=int(registration["seed"]),
            primary_only=bool(registration["primary_only"]),
            model_id=str(registration["model_id"]),
            tokenizer_id=str(registration["tokenizer_id"]),
            pre_registration=registration,
        )
    elif args.cmd == "score":
        registration = load_preregistration(Path(args.preregistration))
        payload = score_open(
            db_path=Path(args.db),
            local_timeframe=str(registration["timeframe"]),
            local_stock_root=Path(args.local_stock_root),
        )
    elif args.cmd == "report":
        payload = prospective_report(
            db_path=Path(args.db),
            preregistration_path=Path(args.preregistration),
        )
    else:
        payload = list_forward(db_path=Path(args.db))
    print(json.dumps(payload, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
