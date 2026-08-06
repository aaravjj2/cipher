#!/usr/bin/env python3
"""Build detailed ticker and rule diagnostics for Flash, Agentic, and Cluster.

This report is descriptive and outcome-informed. It ranks current evidence but
cannot validate, promote, paper trade, or submit orders.
"""
from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.research_platform.cipher_signal_overlay import (  # noqa: E402
    eligible_episode,
    load_signal_episodes,
)
from core.research_platform.hashing import stable_id  # noqa: E402

SOURCE_REPORT = ROOT / "data" / "governance" / "cipher_signal_only" / "latest_signal_research.json"
OUTPUT = ROOT / "data" / "governance" / "cipher_signal_only" / "latest_ticker_strategy_specifics.json"
CAPTURE_ROOT = ROOT / "data" / "browser_ingest"
NY = ZoneInfo("America/New_York")


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    temporary.replace(path)


def read_report() -> dict[str, Any]:
    payload = json.loads(SOURCE_REPORT.read_text(encoding="utf-8"))
    if payload.get("mode") != "flash_agentic_cluster_only":
        raise RuntimeError("source report is not the three-feed-only report")
    return payload


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


def group_summary(frame: pd.DataFrame, fields: list[str], *, minimum: int = 1) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    output: list[dict[str, Any]] = []
    group_key: str | list[str] = fields[0] if len(fields) == 1 else fields
    for key, group in frame.groupby(group_key, dropna=False):
        if len(group) < minimum:
            continue
        values = key if isinstance(key, tuple) else (key,)
        sessions = group["market_session"].astype(str).nunique()
        tickers = group["ticker"].astype(str).nunique()
        session_means = group.groupby("market_session")["directional_return_pct"].mean()
        record = {
            field: (value.item() if hasattr(value, "item") else value)
            for field, value in zip(fields, values)
        }
        record.update(
            {
                "observations": int(len(group)),
                "sessions": int(sessions),
                "tickers": int(tickers),
                "directional_accuracy": float(group["direction_correct"].mean()),
                "average_directional_return_pct": float(group["directional_return_pct"].mean()),
                "median_directional_return_pct": float(group["directional_return_pct"].median()),
                "average_directional_excess_vs_spy_pct": float(group["directional_excess_vs_spy_pct"].mean()),
                "positive_session_fraction": float((session_means > 0).mean()),
                "zero_return_prior_shrunk_average_pct": float(
                    group["directional_return_pct"].sum() / (len(group) + 6)
                ),
            }
        )
        output.append(record)
    return sorted(
        output,
        key=lambda row: (
            -float(row["zero_return_prior_shrunk_average_pct"]),
            -int(row["observations"]),
            tuple(str(row.get(field)) for field in fields),
        ),
    )


def candidate_rule_rows(frame: pd.DataFrame) -> pd.DataFrame:
    output: list[dict[str, Any]] = []
    structural = {
        "follow_majority_vote",
        "follow_unanimous_multi_source",
        "follow_two_source_agreement",
        "follow_first_source",
        "follow_latest_source",
        "follow_flash_bullish",
        "follow_agentic_bullish",
        "follow_cluster_bullish",
        "follow_cluster_when_confirmed",
        "follow_agentic_when_confirmed",
    }
    keys = ["market_session", "ticker", "horizon_sessions"]
    for key, group in frame.groupby(keys):
        market_session, ticker, horizon = key
        by_source = {str(row.source): row for row in group.itertuples()}
        directions = {source: str(row.direction) for source, row in by_source.items()}
        ordered = group.sort_values(["first_seen_at", "source"])
        raw_return = float(group.iloc[0]["raw_underlying_return_pct"])
        spy_return = float(group.iloc[0]["spy_return_pct"])

        def add(rule_name: str, direction: str | None) -> None:
            if direction not in {"BULLISH", "BEARISH"}:
                return
            sign = 1.0 if direction == "BULLISH" else -1.0
            output.append(
                {
                    "market_session": str(market_session),
                    "ticker": str(ticker),
                    "horizon_sessions": int(horizon),
                    "rule_name": rule_name,
                    "rule_origin": (
                        "predefined_structural"
                        if rule_name in structural
                        else "post_hoc_diagnostic_frozen_after_2026_08_05"
                    ),
                    "direction": direction,
                    "covered_sources": len(by_source),
                    "source_directions": directions,
                    "directional_return_pct": sign * raw_return,
                    "directional_excess_vs_spy_pct": sign * (raw_return - spy_return),
                    "direction_correct": sign * raw_return > 0,
                }
            )

        votes = sum(1 if direction == "BULLISH" else -1 for direction in directions.values())
        if votes:
            add("follow_majority_vote", "BULLISH" if votes > 0 else "BEARISH")
        if len(by_source) >= 2 and len(set(directions.values())) == 1:
            add("follow_unanimous_multi_source", next(iter(directions.values())))
        bullish_votes = sum(direction == "BULLISH" for direction in directions.values())
        bearish_votes = sum(direction == "BEARISH" for direction in directions.values())
        if max(bullish_votes, bearish_votes) >= 2:
            add("follow_two_source_agreement", "BULLISH" if bullish_votes >= 2 else "BEARISH")
        add("follow_first_source", str(ordered.iloc[0]["direction"]))
        add("follow_latest_source", str(ordered.iloc[-1]["direction"]))

        flash = directions.get("flash")
        agentic = directions.get("flash_agentic")
        cluster = directions.get("cluster")
        if flash == "BULLISH":
            add("follow_flash_bullish", "BULLISH")
        if agentic == "BULLISH":
            add("follow_agentic_bullish", "BULLISH")
        if cluster == "BULLISH":
            add("follow_cluster_bullish", "BULLISH")
        if cluster and any(directions.get(source) == cluster for source in ("flash", "flash_agentic")):
            add("follow_cluster_when_confirmed", cluster)
        if agentic and any(directions.get(source) == agentic for source in ("flash", "cluster")):
            add("follow_agentic_when_confirmed", agentic)
        if flash == "BEARISH":
            add("fade_flash_bearish", "BULLISH")

    return pd.DataFrame(output)


def leave_one_ticker_out(frame: pd.DataFrame, rule_name: str, horizon: int = 1) -> list[dict[str, Any]]:
    selected = frame[(frame["rule_name"] == rule_name) & (frame["horizon_sessions"] == horizon)]
    if selected.empty:
        return []
    output: list[dict[str, Any]] = []
    exclusions: list[str | None] = [None, *sorted(selected["ticker"].astype(str).unique())]
    for excluded in exclusions:
        group = selected if excluded is None else selected[selected["ticker"] != excluded]
        if group.empty:
            continue
        session_means = group.groupby("market_session")["directional_return_pct"].mean()
        output.append(
            {
                "excluded_ticker": excluded,
                "observations": int(len(group)),
                "sessions": int(group["market_session"].nunique()),
                "directional_accuracy": float(group["direction_correct"].mean()),
                "average_directional_return_pct": float(group["directional_return_pct"].mean()),
                "average_directional_excess_vs_spy_pct": float(group["directional_excess_vs_spy_pct"].mean()),
                "positive_session_fraction": float((session_means > 0).mean()),
            }
        )
    return output


def latest_session_snapshot() -> dict[str, Any]:
    episodes = [row for row in load_signal_episodes(CAPTURE_ROOT) if eligible_episode(row)]
    if not episodes:
        return {"market_session": None, "states": [], "multi_source": []}
    market_session = max(str(row["market_session"]) for row in episodes)
    selected = [row for row in episodes if str(row["market_session"]) == market_session]
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for row in selected:
        key = (str(row["scan_type"]), str(row["ticker"]))
        previous = latest.get(key)
        if previous is None or str(row["first_seen_at"]) > str(previous["first_seen_at"]):
            latest[key] = row
    states = sorted(latest.values(), key=lambda row: (str(row["scan_type"]), str(row["ticker"])))
    by_ticker: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in states:
        by_ticker[str(row["ticker"])].append(row)
    multi_source = []
    for ticker, rows in sorted(by_ticker.items()):
        if len(rows) < 2:
            continue
        directions = {str(row["scan_type"]): str(row["direction"]) for row in rows}
        bullish = sum(value == "BULLISH" for value in directions.values())
        bearish = sum(value == "BEARISH" for value in directions.values())
        multi_source.append(
            {
                "ticker": ticker,
                "directions": directions,
                "covered_sources": len(rows),
                "agreement_status": (
                    "all_agree_bullish"
                    if bearish == 0
                    else "all_agree_bearish"
                    if bullish == 0
                    else "mixed_conflict"
                ),
                "majority_direction": "BULLISH" if bullish > bearish else "BEARISH" if bearish > bullish else None,
            }
        )
    return {
        "market_session": market_session,
        "states": [
            {
                "source": row["scan_type"],
                "ticker": row["ticker"],
                "direction": row["direction"],
                "setup_family": row.get("setup_family"),
                "score": row.get("score"),
                "strength": row.get("strength"),
                "first_seen_at": row.get("first_seen_at"),
            }
            for row in states
        ],
        "multi_source": multi_source,
    }


def main() -> int:
    source = read_report()
    frame = pd.DataFrame(source.get("scored_observations") or [])
    matured = frame[frame["status"] == "matured"].copy()
    matured["time_bucket"] = matured["first_seen_at"].map(time_bucket)
    candidate_rows = candidate_rule_rows(matured)

    source_ticker = group_summary(
        matured,
        ["source", "ticker", "horizon_sessions"],
        minimum=2,
    )
    source_ticker_setup = group_summary(
        matured,
        ["source", "ticker", "setup_family", "direction", "horizon_sessions"],
        minimum=2,
    )
    source_direction = group_summary(
        matured,
        ["source", "direction", "horizon_sessions"],
    )
    timing = group_summary(
        matured,
        ["source", "time_bucket", "horizon_sessions"],
        minimum=3,
    )
    rules = group_summary(candidate_rows, ["rule_name", "rule_origin", "horizon_sessions"])
    majority_tickers = group_summary(
        candidate_rows[candidate_rows["rule_name"] == "follow_majority_vote"],
        ["ticker", "horizon_sessions"],
        minimum=2,
    )

    payload = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "completed",
        "mode": "flash_agentic_cluster_only",
        "source_report_id": source.get("report_id"),
        "ticker_analysis": {
            "by_source_ticker_horizon": source_ticker,
            "by_source_ticker_setup_direction_horizon": source_ticker_setup,
            "deduplicated_majority_vote_by_ticker_horizon": majority_tickers,
        },
        "direction_analysis": source_direction,
        "timing_analysis": timing,
        "candidate_rule_analysis": {
            "rules": rules,
            "flash_bullish_leave_one_ticker_out": leave_one_ticker_out(
                candidate_rows, "follow_flash_bullish", 1
            ),
            "interpretation_boundary": {
                "predefined_structural": "Descriptive structural combinations not selected from outcome ranking.",
                "post_hoc_diagnostic_frozen_after_2026_08_05": (
                    "Generated after observing the initial sample and eligible only for future prospective tracking."
                ),
            },
        },
        "latest_session_snapshot": latest_session_snapshot(),
        "limits": {
            "capture_sessions": source.get("capture_inventory", {}).get("sessions"),
            "matured_source_observations": source.get("forward_scoring", {}).get("matured_observations"),
            "ticker_day_dependence_deduplicated_in_candidate_rules": True,
            "small_sample": True,
            "results_descriptive_not_confirmatory": True,
            "historical_rule_ranking_outcome_informed": True,
        },
        "automatic_promotion": False,
        "paper_or_live_execution": False,
        "execution_authority": False,
    }
    payload["report_id"] = stable_id(
        "cipher_signal_ticker_strategy_specifics",
        {
            "source_report_id": payload["source_report_id"],
            "candidate_rule_names": sorted(candidate_rows["rule_name"].unique()) if not candidate_rows.empty else [],
            "latest_market_session": payload["latest_session_snapshot"]["market_session"],
        },
        length=64,
    )
    atomic_json(OUTPUT, payload)
    one_session_rules = [
        row for row in rules if int(row.get("horizon_sessions") or 0) == 1
    ]
    print(
        json.dumps(
            {
                "status": payload["status"],
                "ticker_groups": len(source_ticker),
                "ticker_setup_groups": len(source_ticker_setup),
                "candidate_rules": len(one_session_rules),
                "top_one_session_rules": one_session_rules[:8],
                "latest_session": payload["latest_session_snapshot"]["market_session"],
                "output": str(OUTPUT),
                "automatic_promotion": False,
                "execution_authority": False,
            },
            indent=2,
            sort_keys=True,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
