"""Prospective Cluster + Kronos recorder using read-only Tradier market data.

The recorder freezes the first model prediction for each Cluster capture/ticker,
scores every group later from completed five-minute bars, and never calls broker
account or order endpoints.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import time
from collections import Counter
from datetime import datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from core.kronos_research import load_predictor, set_research_seed
from core.paper_executor.capture_files import parse_capture_time, read_payload
from core.paper_executor.cluster_forward_test import observation_cards
from core.paper_executor.config import MarketDataConfig
from core.paper_executor.tradier_market_data import TradierMarketData

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows only
    fcntl = None


ROOT = Path(__file__).resolve().parents[1]
ET = ZoneInfo("America/New_York")
DEFAULT_RUNTIME_ROOT = Path("/home/aarav/Aarav/cipher-system/CipherCapture")
DEFAULT_CAPTURE_ROOT = ROOT / "data" / "browser_ingest" / "raw_windows" / "device-windows"
DEFAULT_REGISTRATION = ROOT / "config" / "cluster_kronos_forward_preregistered.json"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def canonical_config_id(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def load_registration(path: Path = DEFAULT_REGISTRATION) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Cluster Kronos preregistration must contain a JSON object")
    required = {
        "registered_at",
        "model_id",
        "tokenizer_id",
        "data_provider",
        "session_filter",
        "timeframe",
        "context_bars",
        "prediction_bars",
        "sample_count",
        "seed",
        "maximum_rank",
        "minimum_absolute_prediction_pct",
        "maximum_generation_delay_minutes",
        "minimum_scored_sample",
        "score_rule",
        "decision_rule",
    }
    missing = sorted(required - set(payload))
    if missing:
        raise ValueError(f"Cluster Kronos preregistration is missing: {', '.join(missing)}")
    if payload["timeframe"] != "5min":
        raise ValueError("only the preregistered five-minute timeframe is supported")
    payload = dict(payload)
    payload["config_id"] = canonical_config_id(payload)
    payload["source_file"] = str(path.resolve())
    return payload


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_tradier_token() -> str:
    token = os.environ.get("TRADIER_PRODUCTION_TOKEN")
    if token:
        return token
    for path in (ROOT / ".env", ROOT / "app" / ".env"):
        token = parse_env_file(path).get("TRADIER_PRODUCTION_TOKEN")
        if token:
            return token
    raise RuntimeError("TRADIER_PRODUCTION_TOKEN is not configured")


def ensure_schema(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as db:
        db.executescript(
            """
            create table if not exists processed_files (
                path text primary key,
                processed_at text not null,
                result_json text not null
            );

            create table if not exists predictions (
                id text primary key,
                config_id text not null,
                capture_file text not null,
                captured_at text not null,
                generated_at text not null,
                ticker text not null,
                rank integer,
                cluster_direction text not null,
                setup text,
                spot real not null,
                target real,
                strength real,
                context_end_at text,
                reference_close real not null,
                pred_close real,
                pred_return_pct real,
                kronos_direction text,
                evaluation_group text not null,
                prospective_eligible integer not null,
                generation_delay_seconds real not null,
                status text not null,
                payload_json text not null
            );
            create index if not exists idx_cluster_kronos_due
                on predictions(status, captured_at);
            create index if not exists idx_cluster_kronos_group
                on predictions(config_id, prospective_eligible, evaluation_group);

            create table if not exists outcomes (
                prediction_id text primary key references predictions(id),
                scored_at text not null,
                horizon_end_at text not null,
                actual_close real not null,
                actual_return_pct real not null,
                cluster_directional_return_pct real not null,
                cluster_direction_positive integer not null,
                kronos_correct integer,
                payload_json text not null
            );
            """
        )


def db_path_for(runtime_root: Path) -> Path:
    return runtime_root / "data" / "cluster_kronos_forward" / "cluster_kronos_forward.sqlite"


def report_dir_for(runtime_root: Path) -> Path:
    return runtime_root / "data" / "cluster_kronos_forward"


def iter_cluster_files(capture_root: Path) -> list[Path]:
    by_name: dict[str, Path] = {}
    for folder_name in ("uploaded", "ready"):
        folder = capture_root / folder_name
        if not folder.is_dir():
            continue
        for path in folder.glob("cluster_*.json"):
            by_name.setdefault(path.name, path)
    return sorted(by_name.values(), key=lambda path: path.stat().st_mtime)


def parse_tradier_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ET)
    return parsed.astimezone(ET)


def fetch_bars(
    client: TradierMarketData,
    ticker: str,
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    rows = client.timesales(
        ticker,
        start.astimezone(ET).strftime("%Y-%m-%d %H:%M"),
        end.astimezone(ET).strftime("%Y-%m-%d %H:%M"),
        interval="5min",
        session_filter="open",
    )
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("time"):
            continue
        try:
            timestamp = parse_tradier_time(str(row["time"]))
            op = float(row["open"])
            hi = float(row["high"])
            lo = float(row["low"])
            close = float(row["close"])
            volume = float(row.get("volume") or 0.0)
        except (KeyError, TypeError, ValueError):
            continue
        if timestamp < start.astimezone(ET) or timestamp > end.astimezone(ET):
            continue
        out.append(
            {
                "timestamp": timestamp,
                "open": op,
                "high": hi,
                "low": lo,
                "close": close,
                "volume": volume,
                "amount": volume * ((op + hi + lo + close) / 4.0),
            }
        )
    unique = {row["timestamp"]: row for row in out}
    return [unique[key] for key in sorted(unique)]


def normalize_regular_session_bar(timestamp: datetime) -> datetime:
    current = timestamp.astimezone(ET)
    while True:
        if current.weekday() >= 5:
            current = (current + timedelta(days=1)).replace(hour=9, minute=30, second=0, microsecond=0)
            continue
        if current.time() < dt_time(9, 30):
            return current.replace(hour=9, minute=30, second=0, microsecond=0)
        if current.time() >= dt_time(16, 0):
            current = (current + timedelta(days=1)).replace(hour=9, minute=30, second=0, microsecond=0)
            continue
        return current


def first_full_bar_after(timestamp: datetime) -> datetime:
    current = timestamp.astimezone(ET).replace(second=0, microsecond=0)
    next_minute = (current.minute // 5 + 1) * 5
    if next_minute >= 60:
        current = (current + timedelta(hours=1)).replace(minute=0)
    else:
        current = current.replace(minute=next_minute)
    return normalize_regular_session_bar(current)


def next_session_bar(timestamp: datetime) -> datetime:
    return normalize_regular_session_bar(timestamp.astimezone(ET) + timedelta(minutes=5))


def future_timestamps(decision_time: datetime, count: int) -> list[datetime]:
    if int(count) <= 0:
        return []
    out = [first_full_bar_after(decision_time)]
    while len(out) < int(count):
        out.append(next_session_bar(out[-1]))
    return out


def forecast_from_bars(
    predictor,
    bars: list[dict[str, Any]],
    registration: dict[str, Any],
    decision_time: datetime,
) -> dict[str, Any]:
    context_count = int(registration["context_bars"])
    if len(bars) < context_count:
        return {
            "available": False,
            "reason": "insufficient_context",
            "context_rows": len(bars),
        }
    import pandas as pd

    context = bars[-context_count:]
    x_df = pd.DataFrame(
        [
            {key: row[key] for key in ("open", "high", "low", "close", "volume", "amount")}
            for row in context
        ]
    )
    x_ts = pd.Series([row["timestamp"].replace(tzinfo=None) for row in context])
    future = future_timestamps(decision_time, int(registration["prediction_bars"]))
    y_ts = pd.Series([timestamp.replace(tzinfo=None) for timestamp in future])
    predicted = predictor.predict(
        df=x_df,
        x_timestamp=x_ts,
        y_timestamp=y_ts,
        pred_len=len(future),
        T=1.0,
        top_p=0.9,
        sample_count=int(registration["sample_count"]),
        verbose=False,
    )
    reference = float(context[-1]["close"])
    pred_close = float(predicted["close"].iloc[-1])
    pred_return = (pred_close / reference - 1.0) * 100.0 if reference else 0.0
    direction = "bullish" if pred_return > 0 else "bearish" if pred_return < 0 else "flat"
    return {
        "available": True,
        "context_rows": len(context),
        "context_start_at": context[0]["timestamp"].isoformat(),
        "context_end_at": context[-1]["timestamp"].isoformat(),
        "reference_close": round(reference, 6),
        "pred_close": round(pred_close, 6),
        "pred_return_pct": round(pred_return, 6),
        "direction": direction,
        "forecast_end_at": future[-1].isoformat(),
    }


def prediction_id(config_id: str, captured_at: datetime, ticker: str, rank: int | None, target: float | None) -> str:
    raw = "|".join(
        [
            config_id,
            captured_at.astimezone(timezone.utc).isoformat(),
            ticker.upper(),
            str(rank or ""),
            str(round(float(target or 0.0), 4)),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def already_processed(db_path: Path, path: Path) -> bool:
    with sqlite3.connect(db_path) as db:
        return db.execute("select 1 from processed_files where path = ?", (str(path.resolve()),)).fetchone() is not None


def process_capture_file(
    path: Path,
    *,
    db_path: Path,
    client: TradierMarketData,
    predictor,
    registration: dict[str, Any],
) -> dict[str, Any]:
    payload = read_payload(path)
    captured_at = parse_capture_time(payload, path)
    if captured_at is None:
        raise ValueError("capture timestamp is missing")
    captured_et = captured_at.astimezone(ET)
    observations = observation_cards(payload, path)
    maximum_rank = int(registration["maximum_rank"])
    observations = [
        obs
        for obs in observations
        if (obs.rank is not None and obs.rank <= maximum_rank) or "quad" in obs.setup.lower()
    ]
    inserted = duplicates = unavailable = 0
    errors: list[dict[str, str]] = []
    for obs in observations:
        ident = prediction_id(registration["config_id"], captured_at, obs.ticker, obs.rank, obs.target)
        with sqlite3.connect(db_path) as db:
            if db.execute("select 1 from predictions where id = ?", (ident,)).fetchone():
                duplicates += 1
                continue
        try:
            context_start = captured_et - timedelta(days=7)
            bars = fetch_bars(client, obs.ticker, context_start, captured_et)
            bars = [
                row
                for row in bars
                if row["timestamp"] + timedelta(minutes=5) <= captured_et
            ]
            forecast = forecast_from_bars(predictor, bars, registration, captured_et)
            reference_close = float(forecast.get("reference_close") or obs.spot)
            generated_at = utcnow()
            delay_seconds = max(0.0, (generated_at - captured_at.astimezone(timezone.utc)).total_seconds())
            registered_at = datetime.fromisoformat(str(registration["registered_at"]).replace("Z", "+00:00"))
            prospective_eligible = bool(
                captured_at.astimezone(timezone.utc) >= registered_at.astimezone(timezone.utc)
                and delay_seconds <= float(registration["maximum_generation_delay_minutes"]) * 60.0
            )
            pred_return = forecast.get("pred_return_pct")
            threshold = float(registration["minimum_absolute_prediction_pct"])
            if not forecast.get("available"):
                evaluation_group = "unavailable"
                unavailable += 1
            else:
                agrees = abs(float(pred_return)) >= threshold and forecast.get("direction") == obs.direction
                evaluation_group = "agreed" if agrees else "disagreed"
            row = {
                "id": ident,
                "config_id": registration["config_id"],
                "capture_file": str(path.resolve()),
                "captured_at": captured_at.astimezone(timezone.utc).isoformat(),
                "generated_at": generated_at.isoformat(),
                "ticker": obs.ticker,
                "rank": obs.rank,
                "cluster_direction": obs.direction,
                "setup": obs.setup,
                "spot": obs.spot,
                "target": obs.target,
                "strength": obs.strength,
                "context_end_at": forecast.get("context_end_at"),
                "reference_close": reference_close,
                "pred_close": forecast.get("pred_close"),
                "pred_return_pct": pred_return,
                "kronos_direction": forecast.get("direction"),
                "evaluation_group": evaluation_group,
                "prospective_eligible": prospective_eligible,
                "generation_delay_seconds": round(delay_seconds, 3),
                "status": "pending",
                "forecast": forecast,
                "pre_registration": registration,
                "caveat": "Context-only prospective research; no orders or sizing changes.",
            }
            with sqlite3.connect(db_path) as db:
                cursor = db.execute(
                    """
                    insert or ignore into predictions (
                        id, config_id, capture_file, captured_at, generated_at, ticker,
                        rank, cluster_direction, setup, spot, target, strength,
                        context_end_at, reference_close, pred_close, pred_return_pct,
                        kronos_direction, evaluation_group, prospective_eligible,
                        generation_delay_seconds, status, payload_json
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["id"], row["config_id"], row["capture_file"], row["captured_at"],
                        row["generated_at"], row["ticker"], row["rank"], row["cluster_direction"],
                        row["setup"], row["spot"], row["target"], row["strength"],
                        row["context_end_at"], row["reference_close"], row["pred_close"],
                        row["pred_return_pct"], row["kronos_direction"], row["evaluation_group"],
                        1 if row["prospective_eligible"] else 0, row["generation_delay_seconds"],
                        row["status"], json.dumps(row, separators=(",", ":"), default=str),
                    ),
                )
                if cursor.rowcount == 1:
                    inserted += 1
                else:
                    duplicates += 1
        except Exception as exc:
            errors.append({"ticker": obs.ticker, "error": str(exc)})
    result = {
        "file": str(path),
        "captured_at": captured_at.isoformat(),
        "observations": len(observations),
        "inserted": inserted,
        "duplicates": duplicates,
        "unavailable": unavailable,
        "errors": errors,
    }
    with sqlite3.connect(db_path) as db:
        db.execute(
            "insert or ignore into processed_files(path, processed_at, result_json) values (?, ?, ?)",
            (str(path.resolve()), utcnow().isoformat(), json.dumps(result, separators=(",", ":"))),
        )
    return result


def completed_future_bars(
    client: TradierMarketData,
    ticker: str,
    captured_at: datetime,
    now: datetime,
) -> list[dict[str, Any]]:
    start = captured_at.astimezone(ET)
    rows = fetch_bars(client, ticker, start, now.astimezone(ET))
    first_bar = first_full_bar_after(start)
    return [
        row
        for row in rows
        if row["timestamp"] >= first_bar
        and row["timestamp"] + timedelta(minutes=5) <= now.astimezone(ET)
    ]


def score_pending(
    *,
    db_path: Path,
    client: TradierMarketData,
    registration: dict[str, Any],
) -> dict[str, Any]:
    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        pending = db.execute("select * from predictions where status = 'pending' order by captured_at").fetchall()
    scored = 0
    waiting = 0
    errors: list[dict[str, str]] = []
    required = int(registration["prediction_bars"])
    now = utcnow()
    for row in pending:
        try:
            captured_at = datetime.fromisoformat(str(row["captured_at"]))
            expected_complete = future_timestamps(captured_at, required)[-1] + timedelta(minutes=5)
            if now.astimezone(ET) < expected_complete.astimezone(ET):
                waiting += 1
                continue
            bars = completed_future_bars(client, str(row["ticker"]), captured_at, now)
            if len(bars) < required:
                waiting += 1
                continue
            horizon = bars[:required]
            actual_close = float(horizon[-1]["close"])
            reference = float(row["reference_close"])
            actual_return = (actual_close / reference - 1.0) * 100.0 if reference else 0.0
            cluster_return = actual_return if row["cluster_direction"] == "bullish" else -actual_return
            pred_return = row["pred_return_pct"]
            kronos_correct = None
            if pred_return is not None:
                kronos_correct = (float(pred_return) > 0 and actual_return > 0) or (
                    float(pred_return) < 0 and actual_return < 0
                )
            outcome = {
                "prediction_id": row["id"],
                "scored_at": now.isoformat(),
                "horizon_end_at": horizon[-1]["timestamp"].astimezone(timezone.utc).isoformat(),
                "actual_close": round(actual_close, 6),
                "actual_return_pct": round(actual_return, 6),
                "cluster_directional_return_pct": round(cluster_return, 6),
                "cluster_direction_positive": cluster_return > 0,
                "kronos_correct": kronos_correct,
                "bars_scored": required,
            }
            with sqlite3.connect(db_path) as db:
                cursor = db.execute(
                    """
                    insert or ignore into outcomes (
                        prediction_id, scored_at, horizon_end_at, actual_close,
                        actual_return_pct, cluster_directional_return_pct,
                        cluster_direction_positive, kronos_correct, payload_json
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["id"], outcome["scored_at"], outcome["horizon_end_at"],
                        outcome["actual_close"], outcome["actual_return_pct"],
                        outcome["cluster_directional_return_pct"],
                        1 if outcome["cluster_direction_positive"] else 0,
                        None if kronos_correct is None else (1 if kronos_correct else 0),
                        json.dumps(outcome, separators=(",", ":"), default=str),
                    ),
                )
                if cursor.rowcount == 1:
                    db.execute("update predictions set status = 'scored' where id = ?", (row["id"],))
                    scored += 1
        except Exception as exc:
            errors.append({"id": str(row["id"]), "ticker": str(row["ticker"]), "error": str(exc)})
    return {"pending_seen": len(pending), "scored": scored, "waiting": waiting, "errors": errors}


def summarize_rows(rows: list[sqlite3.Row]) -> dict[str, Any]:
    if not rows:
        return {
            "n": 0,
            "cluster_win_rate": None,
            "average_cluster_directional_return_pct": None,
            "kronos_direction_accuracy": None,
        }
    directional = [float(row["cluster_directional_return_pct"]) for row in rows]
    correct = [int(row["kronos_correct"]) for row in rows if row["kronos_correct"] is not None]
    return {
        "n": len(rows),
        "cluster_win_rate": round(sum(value > 0 for value in directional) / len(directional), 4),
        "average_cluster_directional_return_pct": round(sum(directional) / len(directional), 6),
        "total_cluster_directional_return_points": round(sum(directional), 6),
        "kronos_direction_accuracy": round(sum(correct) / len(correct), 4) if correct else None,
        "kronos_scored_n": len(correct),
    }


def write_report(runtime_root: Path, db_path: Path, registration: dict[str, Any]) -> dict[str, Any]:
    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        all_predictions = db.execute(
            "select status, prospective_eligible, evaluation_group from predictions where config_id = ?",
            (registration["config_id"],),
        ).fetchall()
        scored = db.execute(
            """
            select p.evaluation_group, p.prospective_eligible,
                   o.cluster_directional_return_pct, o.kronos_correct
            from predictions p join outcomes o on o.prediction_id = p.id
            where p.config_id = ?
            """,
            (registration["config_id"],),
        ).fetchall()
    status_counts = Counter(str(row["status"]) for row in all_predictions)
    prospective = [row for row in scored if int(row["prospective_eligible"] or 0) == 1]
    audit_only = [row for row in scored if int(row["prospective_eligible"] or 0) == 0]
    groups: dict[str, list[sqlite3.Row]] = {}
    for row in prospective:
        groups.setdefault(str(row["evaluation_group"]), []).append(row)
    minimum = int(registration["minimum_scored_sample"])
    report = {
        "generated_at": utcnow().isoformat(),
        "mode": "cluster_kronos_preregistered_prospective",
        "pre_registration": registration,
        "status_counts": dict(status_counts),
        "prospective": summarize_rows(prospective),
        "audit_only": summarize_rows(audit_only),
        "by_evaluation_group": {name: summarize_rows(items) for name, items in sorted(groups.items())},
        "progress": {
            "scored": len(prospective),
            "minimum_required": minimum,
            "remaining": max(0, minimum - len(prospective)),
            "minimum_reached": len(prospective) >= minimum,
        },
        "deployment_rule": registration["decision_rule"],
    }
    report_dir = report_dir_for(runtime_root)
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / "latest_cluster_kronos_forward.json"
    md_path = report_dir / "latest_cluster_kronos_forward.md"
    json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    lines = [
        "# Cluster Kronos Prospective Forward Test",
        "",
        f"- Generated: {report['generated_at']}",
        f"- Configuration: `{registration['config_id']}`",
        f"- Prospective scored: {len(prospective)}/{minimum}",
        f"- Minimum reached: {report['progress']['minimum_reached']}",
        f"- Status counts: {dict(status_counts)}",
        "",
        "## Prospective Results",
        "",
        "| Group | N | Cluster win rate | Avg directional return | Kronos direction accuracy |",
        "|---|---:|---:|---:|---:|",
    ]
    for group in ("agreed", "disagreed", "unavailable"):
        summary = report["by_evaluation_group"].get(group)
        if not summary:
            continue
        win_rate = "" if summary["cluster_win_rate"] is None else f"{summary['cluster_win_rate'] * 100:.1f}%"
        avg_return = "" if summary["average_cluster_directional_return_pct"] is None else f"{summary['average_cluster_directional_return_pct']:.4f}%"
        accuracy = "" if summary["kronos_direction_accuracy"] is None else f"{summary['kronos_direction_accuracy'] * 100:.1f}%"
        lines.append(f"| {group} | {summary['n']} | {win_rate} | {avg_return} | {accuracy} |")
    lines.extend(
        [
            "",
            "## Locked Rule",
            "",
            registration["decision_rule"],
            "",
            "This is read-only research and does not authorize orders or sizing changes.",
        ]
    )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    report["json_path"] = str(json_path)
    report["markdown_path"] = str(md_path)
    return report


def seed_existing(capture_root: Path, db_path: Path) -> dict[str, Any]:
    files = iter_cluster_files(capture_root)
    inserted = 0
    with sqlite3.connect(db_path) as db:
        for path in files:
            cursor = db.execute(
                "insert or ignore into processed_files(path, processed_at, result_json) values (?, ?, ?)",
                (str(path.resolve()), utcnow().isoformat(), json.dumps({"seeded": True})),
            )
            inserted += max(cursor.rowcount, 0)
    return {"files": len(files), "seeded": inserted}


def process_once(
    *,
    runtime_root: Path,
    capture_root: Path,
    client: TradierMarketData,
    predictor,
    registration: dict[str, Any],
) -> dict[str, Any]:
    db_path = db_path_for(runtime_root)
    ensure_schema(db_path)
    processed = []
    for path in iter_cluster_files(capture_root):
        if already_processed(db_path, path):
            continue
        try:
            processed.append(
                process_capture_file(
                    path,
                    db_path=db_path,
                    client=client,
                    predictor=predictor,
                    registration=registration,
                )
            )
        except Exception as exc:
            result = {"file": str(path), "error": str(exc)}
            with sqlite3.connect(db_path) as db:
                db.execute(
                    "insert or ignore into processed_files(path, processed_at, result_json) values (?, ?, ?)",
                    (str(path.resolve()), utcnow().isoformat(), json.dumps(result)),
                )
            processed.append(result)
    scoring = score_pending(db_path=db_path, client=client, registration=registration)
    report = write_report(runtime_root, db_path, registration)
    return {"processed": processed, "scoring": scoring, "report": report}


def acquire_lock(runtime_root: Path):
    path = runtime_root / "state" / "cluster_kronos_forward.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    if fcntl is None:
        return handle
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()).encode("ascii"))
    handle.flush()
    return handle


def main() -> int:
    parser = argparse.ArgumentParser(description="Prospective Cluster + Kronos context recorder.")
    parser.add_argument("--root", default=str(DEFAULT_RUNTIME_ROOT))
    parser.add_argument("--capture-root", default=str(DEFAULT_CAPTURE_ROOT))
    parser.add_argument("--registration", default=str(DEFAULT_REGISTRATION))
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval-seconds", type=float, default=60.0)
    parser.add_argument("--seed-existing", action="store_true")
    parser.add_argument("--process-latest-only", action="store_true")
    parser.add_argument("--score-only", action="store_true")
    args = parser.parse_args()

    runtime_root = Path(args.root)
    capture_root = Path(args.capture_root)
    registration = load_registration(Path(args.registration))
    set_research_seed(int(registration["seed"]))
    client = TradierMarketData(MarketDataConfig(), token=load_tradier_token())
    db_path = db_path_for(runtime_root)
    ensure_schema(db_path)

    if args.score_only:
        result = {
            "scoring": score_pending(db_path=db_path, client=client, registration=registration),
            "report": write_report(runtime_root, db_path, registration),
        }
        print(json.dumps(result, indent=2, default=str))
        return 0

    predictor = load_predictor(
        model_id=str(registration["model_id"]),
        tokenizer_id=str(registration["tokenizer_id"]),
        device="cpu",
        max_context=max(64, int(registration["context_bars"])),
    )

    if args.process_latest_only:
        files = iter_cluster_files(capture_root)
        if not files:
            print(json.dumps({"status": "no_cluster_files"}, indent=2))
            return 0
        result = process_capture_file(
            files[-1],
            db_path=db_path,
            client=client,
            predictor=predictor,
            registration=registration,
        )
        output = {
            "processed": result,
            "scoring": score_pending(db_path=db_path, client=client, registration=registration),
            "report": write_report(runtime_root, db_path, registration),
        }
        print(json.dumps(output, indent=2, default=str))
        return 0

    if args.seed_existing:
        print(json.dumps({"seed": seed_existing(capture_root, db_path)}, indent=2))

    lock_handle = acquire_lock(runtime_root) if args.watch else None
    if args.watch and lock_handle is None:
        print(json.dumps({"status": "already_running"}, indent=2))
        return 0

    while True:
        print(
            json.dumps(
                process_once(
                    runtime_root=runtime_root,
                    capture_root=capture_root,
                    client=client,
                    predictor=predictor,
                    registration=registration,
                ),
                indent=2,
                default=str,
            ),
            flush=True,
        )
        if not args.watch:
            return 0
        time.sleep(max(30.0, float(args.interval_seconds)))


if __name__ == "__main__":
    raise SystemExit(main())
