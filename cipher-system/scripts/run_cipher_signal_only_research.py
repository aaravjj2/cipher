#!/usr/bin/env python3
"""Analyze only Cipher Flash, Agentic, and Cluster captures.

The primary observation is the latest eligible state for one source/ticker/session.
Signals are evaluated only from the next market open to later market opens. This
is descriptive prospective research; it cannot promote strategies or submit orders.
"""
from __future__ import annotations

import json
import math
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.research_platform.cipher_signal_overlay import (  # noqa: E402
    SCAN_TYPES,
    capture_inventory,
    eligible_episode,
    load_signal_episodes,
    signal_file_manifest,
)
from core.research_platform.hashing import stable_id  # noqa: E402

REGISTRY = ROOT / "data" / "governance" / "research_registry.sqlite"
CAPTURE_ROOT = ROOT / "data" / "browser_ingest"
OUTPUT_ROOT = ROOT / "data" / "governance" / "cipher_signal_only"
OUTPUT = OUTPUT_ROOT / "latest_signal_research.json"
PREFIX = "alpaca_broad_daily_recent_2024_"
FALLBACK_NAME = "alpaca_broad_daily_2024_2026_ytd_holdout_v1"
HORIZONS = (1, 5, 21)
NY = ZoneInfo("America/New_York")


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    temporary.replace(path)


def latest_dataset() -> dict[str, Any]:
    with sqlite3.connect(f"file:{REGISTRY.as_posix()}?mode=ro", uri=True, timeout=30) as db:
        row = db.execute(
            """
            select d.dataset_id, d.name, d.payload_json, r.uri, r.checksum
            from datasets d
            join dataset_raw_objects l on l.dataset_id=d.dataset_id
            join raw_objects r on r.raw_object_id=l.raw_object_id
            where d.frozen=1 and d.quality_passed=1
              and (d.name like ? or d.name = ?)
            order by case when d.name like ? then 0 else 1 end, d.created_at desc
            limit 1
            """,
            (f"{PREFIX}%", FALLBACK_NAME, f"{PREFIX}%"),
        ).fetchone()
    if not row:
        raise RuntimeError("no canonical recent daily dataset is registered")
    uri = str(row[3])
    if not uri.startswith("file://"):
        raise RuntimeError("canonical daily dataset is not local")
    path = Path(uri.removeprefix("file://"))
    if not path.is_file():
        raise RuntimeError(f"canonical daily dataset is unavailable: {path}")
    payload = json.loads(row[2]) if row[2] else {}
    quality = payload.get("quality_checks") or {}
    return {
        "dataset_id": str(row[0]),
        "dataset_name": str(row[1]),
        "path": str(path),
        "checksum": str(row[4]),
        "latest_session": str(quality.get("observed_end") or ""),
    }


def daily_latest_states(episodes: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    states: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in episodes:
        if not eligible_episode(row):
            continue
        key = (str(row["market_session"]), str(row["ticker"]), str(row["scan_type"]))
        previous = states.get(key)
        if previous is None or str(row["first_seen_at"]) > str(previous["first_seen_at"]):
            states[key] = dict(row)
    return sorted(states.values(), key=lambda row: (row["market_session"], row["ticker"], row["scan_type"]))


def agreement_context(states: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in states:
        grouped[(str(row["market_session"]), str(row["ticker"]))].append(row)
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for key, rows in grouped.items():
        rows = sorted(rows, key=lambda row: (str(row["first_seen_at"]), str(row["scan_type"])))
        directions = [str(row["direction"]) for row in rows]
        if len(rows) == 1:
            status = "single_source"
        elif len(set(directions)) == 1:
            status = "all_agree_bullish" if directions[0] == "BULLISH" else "all_agree_bearish"
        else:
            status = "mixed_conflict"
        first = rows[0]
        last = rows[-1]
        lag_minutes = (
            pd.Timestamp(last["first_seen_at"]) - pd.Timestamp(first["first_seen_at"])
        ).total_seconds() / 60.0
        result[key] = {
            "agreement_status": status,
            "covered_sources": len(rows),
            "first_source": first["scan_type"],
            "last_source": last["scan_type"],
            "cross_source_lag_minutes": lag_minutes,
            "directions": {str(row["scan_type"]): str(row["direction"]) for row in rows},
        }
    return result


def score_bucket(value: Any) -> str:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return "unscored"
    if not math.isfinite(score):
        return "unscored"
    if score < 60:
        return "below_60"
    if score < 70:
        return "60_69"
    if score < 80:
        return "70_79"
    if score < 90:
        return "80_89"
    return "90_100"


def time_bucket(value: Any) -> str:
    timestamp = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(timestamp):
        return "unknown"
    local = pd.Timestamp(timestamp).to_pydatetime().astimezone(NY)
    minute = local.hour * 60 + local.minute
    if minute < 630:
        return "open_0930_1029"
    if minute < 720:
        return "morning_1030_1159"
    if minute < 840:
        return "midday_1200_1359"
    if minute < 930:
        return "afternoon_1400_1529"
    return "close_1530_1600"


def score_states(states: list[dict[str, Any]], opens: pd.DataFrame) -> list[dict[str, Any]]:
    contexts = agreement_context(states)
    results: list[dict[str, Any]] = []
    opens = opens.copy().sort_index()
    for row in states:
        session = pd.Timestamp(row["market_session"])
        future = pd.DatetimeIndex(opens.index[opens.index > session]).sort_values().unique()
        symbol = str(row["ticker"])
        context = contexts[(str(row["market_session"]), symbol)]
        for horizon in HORIZONS:
            needed = horizon + 1
            base = {
                "observation_id": stable_id(
                    "cipher_signal_daily_state",
                    {
                        "market_session": row["market_session"],
                        "ticker": symbol,
                        "scan_type": row["scan_type"],
                        "signal_id": row["signal_id"],
                        "horizon": horizon,
                    },
                    length=24,
                ),
                "market_session": row["market_session"],
                "ticker": symbol,
                "source": row["scan_type"],
                "direction": row["direction"],
                "setup_family": row.get("setup_family") or "unknown",
                "score": row.get("score"),
                "score_bucket": score_bucket(row.get("score")),
                "strength": row.get("strength"),
                "first_seen_at": row["first_seen_at"],
                "time_bucket": time_bucket(row["first_seen_at"]),
                "horizon_sessions": horizon,
                **context,
            }
            if len(future) < needed:
                results.append({**base, "status": "pending_future_opens", "available_future_opens": len(future)})
                continue
            if symbol not in opens.columns or "SPY" not in opens.columns:
                results.append({**base, "status": "unscorable_symbol_not_in_daily_panel"})
                continue
            entry_session = pd.Timestamp(future[0])
            exit_session = pd.Timestamp(future[horizon])
            entry = opens.at[entry_session, symbol]
            exit_value = opens.at[exit_session, symbol]
            spy_entry = opens.at[entry_session, "SPY"]
            spy_exit = opens.at[exit_session, "SPY"]
            if any(pd.isna(value) or float(value) <= 0 for value in (entry, exit_value, spy_entry, spy_exit)):
                results.append({**base, "status": "unscorable_missing_open"})
                continue
            raw_return = float(exit_value / entry - 1.0)
            spy_return = float(spy_exit / spy_entry - 1.0)
            sign = 1.0 if row["direction"] == "BULLISH" else -1.0
            directional = sign * raw_return
            directional_excess = sign * (raw_return - spy_return)
            results.append(
                {
                    **base,
                    "status": "matured",
                    "entry_session": entry_session.date().isoformat(),
                    "exit_session": exit_session.date().isoformat(),
                    "raw_underlying_return_pct": raw_return * 100.0,
                    "spy_return_pct": spy_return * 100.0,
                    "directional_return_pct": directional * 100.0,
                    "directional_excess_vs_spy_pct": directional_excess * 100.0,
                    "direction_correct": directional > 0,
                }
            )
    return results


def summarize(rows: list[dict[str, Any]], group_fields: tuple[str, ...]) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("status") != "matured":
            continue
        groups[tuple(row.get(field) for field in group_fields)].append(row)
    output: list[dict[str, Any]] = []
    for key, group in groups.items():
        directional = [float(row["directional_return_pct"]) for row in group]
        excess = [float(row["directional_excess_vs_spy_pct"]) for row in group]
        record = {field: value for field, value in zip(group_fields, key)}
        record.update(
            {
                "observations": len(group),
                "directional_accuracy": sum(bool(row["direction_correct"]) for row in group) / len(group),
                "average_directional_return_pct": mean(directional),
                "median_directional_return_pct": median(directional),
                "average_directional_excess_vs_spy_pct": mean(excess),
            }
        )
        output.append(record)
    return sorted(output, key=lambda row: tuple(str(row.get(field)) for field in group_fields))


def pair_agreement(states: list[dict[str, Any]]) -> list[dict[str, Any]]:
    contexts = agreement_context(states)
    by_key: dict[tuple[str, str], dict[str, str]] = defaultdict(dict)
    for row in states:
        by_key[(str(row["market_session"]), str(row["ticker"]))][str(row["scan_type"])] = str(row["direction"])
    output: list[dict[str, Any]] = []
    pairs = (("flash", "flash_agentic"), ("flash", "cluster"), ("flash_agentic", "cluster"))
    for left, right in pairs:
        comparable = [directions for directions in by_key.values() if left in directions and right in directions]
        agree = sum(row[left] == row[right] for row in comparable)
        output.append(
            {
                "left_source": left,
                "right_source": right,
                "comparable_daily_ticker_states": len(comparable),
                "agreement_count": agree,
                "conflict_count": len(comparable) - agree,
                "agreement_fraction": agree / len(comparable) if comparable else None,
            }
        )
    _ = contexts
    return output


def freshness(episodes: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for source in SCAN_TYPES:
        rows = [row for row in episodes if row.get("scan_type") == source]
        latest = max(rows, key=lambda row: str(row.get("first_seen_at")), default=None)
        if latest is None:
            result[source] = {"status": "missing"}
            continue
        timestamp = pd.Timestamp(latest["first_seen_at"])
        age_minutes = (pd.Timestamp(now) - timestamp).total_seconds() / 60.0
        result[source] = {
            "status": "available",
            "last_received_at": latest["first_seen_at"],
            "age_minutes": age_minutes,
            "ticker": latest.get("ticker"),
            "direction": latest.get("direction"),
            "setup_family": latest.get("setup_family"),
        }
    return result


def main() -> int:
    now = datetime.now(timezone.utc)
    dataset = latest_dataset()
    episodes = load_signal_episodes(CAPTURE_ROOT)
    states = daily_latest_states(episodes)
    frame = pd.read_parquet(dataset["path"], columns=["timestamp", "ticker", "open"])
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True).dt.tz_convert(None).dt.normalize()
    opens = frame.pivot(index="timestamp", columns="ticker", values="open").sort_index()
    scored = score_states(states, opens)
    matured = [row for row in scored if row.get("status") == "matured"]
    pending = [row for row in scored if row.get("status") == "pending_future_opens"]
    unscorable = [row for row in scored if str(row.get("status", "")).startswith("unscorable")]

    source_counts = Counter(row["scan_type"] for row in states)
    setup_counts: dict[str, Counter[str]] = {
        source: Counter(str(row.get("setup_family") or "unknown") for row in states if row["scan_type"] == source)
        for source in SCAN_TYPES
    }
    direction_counts: dict[str, Counter[str]] = {
        source: Counter(str(row["direction"]) for row in states if row["scan_type"] == source)
        for source in SCAN_TYPES
    }
    first_source_counts = Counter(
        context["first_source"] for context in agreement_context(states).values() if context["covered_sources"] >= 2
    )

    payload = {
        "schema_version": 1,
        "created_at": now.isoformat(),
        "status": "completed",
        "mode": "flash_agentic_cluster_only",
        "active_sources": list(SCAN_TYPES),
        "dataset": dataset,
        "capture_manifest": signal_file_manifest(CAPTURE_ROOT),
        "capture_inventory": capture_inventory(episodes),
        "freshness": freshness(episodes, now),
        "daily_latest_states": {
            "count": len(states),
            "by_source": dict(source_counts),
            "directions_by_source": {source: dict(direction_counts[source]) for source in SCAN_TYPES},
            "setup_families_by_source": {source: dict(setup_counts[source].most_common()) for source in SCAN_TYPES},
        },
        "cross_source": {
            "pair_agreement": pair_agreement(states),
            "first_source_when_multiple_covered": dict(first_source_counts),
            "agreement_state_counts": dict(Counter(
                context["agreement_status"] for context in agreement_context(states).values()
            )),
        },
        "forward_scoring": {
            "observation_definition": "latest_eligible_source_ticker_state_per_market_session",
            "entry_rule": "next_session_open",
            "exit_horizons_sessions": list(HORIZONS),
            "matured_observations": len(matured),
            "pending_observations": len(pending),
            "unscorable_observations": len(unscorable),
            "available_symbol_fraction": (
                len({row["ticker"] for row in matured}) / len({row["ticker"] for row in states})
                if states else None
            ),
            "by_source_horizon": summarize(scored, ("source", "horizon_sessions")),
            "by_source_direction_horizon": summarize(scored, ("source", "direction", "horizon_sessions")),
            "by_source_setup_horizon": summarize(scored, ("source", "setup_family", "horizon_sessions")),
            "by_source_score_bucket_horizon": summarize(scored, ("source", "score_bucket", "horizon_sessions")),
            "by_agreement_state_horizon": summarize(scored, ("agreement_status", "horizon_sessions")),
            "by_first_source_horizon": summarize(scored, ("first_source", "horizon_sessions")),
        },
        "scored_observations": scored,
        "research_limits": {
            "capture_start": capture_inventory(episodes).get("first_session"),
            "cannot_support_2025_backtest": True,
            "short_history": True,
            "multiple_daily_episodes_reduced_to_latest_state": True,
            "results_descriptive_not_confirmatory": True,
        },
        "automatic_promotion": False,
        "paper_or_live_execution": False,
        "execution_authority": False,
    }
    payload["report_id"] = stable_id(
        "cipher_signal_only_research",
        {
            "dataset_checksum": dataset["checksum"],
            "capture_manifest": payload["capture_manifest"],
            "active_sources": payload["active_sources"],
            "observation_definition": payload["forward_scoring"]["observation_definition"],
        },
        length=64,
    )
    _atomic_json(OUTPUT, payload)
    print(
        json.dumps(
            {
                "status": payload["status"],
                "mode": payload["mode"],
                "capture_sessions": payload["capture_inventory"]["sessions"],
                "daily_latest_states": len(states),
                "matured_observations": len(matured),
                "pending_observations": len(pending),
                "unscorable_observations": len(unscorable),
                "freshness": payload["freshness"],
                "by_source_horizon": payload["forward_scoring"]["by_source_horizon"],
                "output": str(OUTPUT),
                "automatic_promotion": False,
                "execution_authority": False,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
