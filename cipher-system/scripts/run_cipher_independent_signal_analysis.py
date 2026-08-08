#!/usr/bin/env python3
"""Analyze Flash and Agentic independently with no cross-source logic.

Each source receives its own episode inventory, terminal ticker/session states,
next-open 1/5/21-session outcomes, ticker tables, setup tables, score buckets,
and timing diagnostics. The two sources are never merged into a vote,
confirmation, veto, lead-lag, or combined candidate rule.

Read-only research only. No account or order endpoints.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, time as dt_time, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from core.research_platform.cipher_signal_overlay import (  # noqa: E402
    eligible_episode,
    load_signal_episodes,
    signal_file_manifest,
)
from core.research_platform.hashing import stable_id  # noqa: E402
from run_cipher_complete_observations import (  # noqa: E402
    STOCK_BARS_URL,
    STOCK_DAILY_CACHE,
    atomic_json,
    finite,
    latest_completed_market_session,
    load_or_fetch_daily_bars,
    provider_headers,
    provider_open_matrix,
    signal_time_bucket,
    utc_timestamp,
)
from run_cipher_signal_only_research import latest_dataset, score_states  # noqa: E402

UTC = timezone.utc
CAPTURE_ROOT = ROOT / "data" / "browser_ingest"
GOV = ROOT / "data" / "governance" / "cipher_signal_only"
OUTPUT = GOV / "latest_independent_signal_analysis.json"
SOURCES = ("flash", "flash_agentic")


def latest_states(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    selected: dict[tuple[str, str], Mapping[str, Any]] = {}
    for row in rows:
        if not eligible_episode(row):
            continue
        key = (str(row.get("market_session")), str(row.get("ticker")))
        existing = selected.get(key)
        if existing is None or str(row.get("first_seen_at")) > str(existing.get("first_seen_at")):
            selected[key] = row
    return [dict(row) for _, row in sorted(selected.items())]


def duration_minutes(row: Mapping[str, Any]) -> float | None:
    first = utc_timestamp(row.get("first_seen_at"))
    last = utc_timestamp(row.get("last_seen_at"))
    if first is None or last is None:
        return None
    return max((last - first).total_seconds() / 60.0, 0.0)


def score_bucket(value: Any) -> str:
    score = finite(value)
    if score is None:
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


def summarize(
    rows: Sequence[Mapping[str, Any]],
    fields: Sequence[str],
) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[tuple(row.get(field) for field in fields)].append(row)
    output: list[dict[str, Any]] = []
    for key, group in groups.items():
        returns = [finite(row.get("directional_return_pct")) for row in group]
        excess = [finite(row.get("directional_excess_vs_spy_pct")) for row in group]
        returns = [value for value in returns if value is not None]
        excess = [value for value in excess if value is not None]
        record = {field: value for field, value in zip(fields, key)}
        record.update(
            {
                "observations": len(group),
                "market_sessions": len({str(row.get("market_session")) for row in group}),
                "tickers": len({str(row.get("ticker")) for row in group}),
                "directional_return_available": len(returns),
                "directional_accuracy": sum(value > 0 for value in returns) / len(returns) if returns else None,
                "average_directional_return_pct": mean(returns) if returns else None,
                "median_directional_return_pct": median(returns) if returns else None,
                "average_directional_excess_vs_spy_pct": mean(excess) if excess else None,
                "median_directional_excess_vs_spy_pct": median(excess) if excess else None,
            }
        )
        output.append(record)
    return sorted(output, key=lambda row: tuple(str(row.get(field)) for field in fields))


def source_payload(
    source: str,
    source_episodes: Sequence[Mapping[str, Any]],
    scored: Sequence[Mapping[str, Any]],
    latest_session: str,
) -> dict[str, Any]:
    states = latest_states(source_episodes)
    enriched_episodes = [
        {
            **dict(row),
            "episode_duration_minutes": duration_minutes(row),
            "signal_time_bucket": signal_time_bucket(row.get("first_seen_at")),
            "score_bucket": score_bucket(row.get("score")),
        }
        for row in source_episodes
    ]
    enriched_scored = [
        {
            **dict(row),
            "signal_time_bucket": signal_time_bucket(row.get("first_seen_at")),
            "score_bucket": score_bucket(row.get("score")),
        }
        for row in scored
    ]
    matured = [row for row in enriched_scored if row.get("status") == "matured"]
    return {
        "source": source,
        "source_boundary": {
            "uses_other_signal_sources": False,
            "cross_source_confirmation": False,
            "cross_source_veto": False,
            "cross_source_ranking": False,
        },
        "latest_completed_market_session": latest_session,
        "episodes": {
            "total": len(source_episodes),
            "eligible_regular_session": sum(eligible_episode(row) for row in source_episodes),
            "unique_tickers": len({str(row.get("ticker")) for row in source_episodes if row.get("ticker")}),
            "market_sessions": len({str(row.get("market_session")) for row in source_episodes}),
            "directions": dict(Counter(str(row.get("direction")) for row in source_episodes)),
            "setups": dict(Counter(str(row.get("setup_family")) for row in source_episodes)),
            "score_buckets": dict(Counter(score_bucket(row.get("score")) for row in source_episodes)),
            "records": enriched_episodes,
        },
        "terminal_states": {
            "definition": "latest eligible state for this source, ticker, and market session only",
            "count": len(states),
            "records": states,
        },
        "forward_scoring": {
            "definition": "next-session-open entry; 1/5/21 completed-session exits",
            "records": enriched_scored,
            "status_counts": dict(Counter(str(row.get("status")) for row in enriched_scored)),
            "matured": len(matured),
            "pending": sum(row.get("status") == "pending_future_opens" for row in enriched_scored),
            "unscorable": sum(str(row.get("status", "")).startswith("unscorable") for row in enriched_scored),
            "by_direction_horizon": summarize(matured, ("direction", "horizon_sessions")),
            "by_setup_horizon": summarize(matured, ("setup_family", "horizon_sessions")),
            "by_score_bucket_horizon": summarize(matured, ("score_bucket", "horizon_sessions")),
            "by_time_bucket_horizon": summarize(matured, ("signal_time_bucket", "horizon_sessions")),
            "by_ticker_horizon": summarize(matured, ("ticker", "horizon_sessions")),
            "by_session_horizon": summarize(matured, ("market_session", "horizon_sessions")),
        },
        "automatic_promotion": False,
        "execution_authority": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--force-provider-refresh", action="store_true")
    args = parser.parse_args()

    created_at = datetime.now(UTC)
    episodes = load_signal_episodes(CAPTURE_ROOT)
    selected = [row for row in episodes if str(row.get("scan_type")) in SOURCES]
    if not selected:
        raise RuntimeError("no Flash or Agentic episodes found")
    states_by_source = {source: latest_states([row for row in selected if row.get("scan_type") == source]) for source in SOURCES}
    tickers = sorted({str(row.get("ticker")) for rows in states_by_source.values() for row in rows} | {"SPY"})
    first_session = min(str(row.get("market_session")) for row in selected)
    bars_start = datetime.combine(date.fromisoformat(first_session), dt_time(0, 0), tzinfo=UTC)
    headers = provider_headers()
    stock_daily = load_or_fetch_daily_bars(
        tickers,
        root=STOCK_DAILY_CACHE,
        url=STOCK_BARS_URL,
        headers=headers,
        stock=True,
        start=bars_start,
        end=created_at,
        workers=max(1, min(args.workers, 4)),
        force=args.force_provider_refresh,
    )
    fallback = str(latest_dataset().get("latest_session") or max(str(row.get("market_session")) for row in selected))
    latest_session = latest_completed_market_session(stock_daily, now=created_at, fallback=fallback)
    opens = provider_open_matrix(stock_daily)

    sources: dict[str, Any] = {}
    for source in SOURCES:
        source_episodes = [row for row in selected if row.get("scan_type") == source]
        scored = score_states(states_by_source[source], opens)
        sources[source] = source_payload(source, source_episodes, scored, latest_session)

    payload = {
        "schema_version": 1,
        "created_at": created_at.isoformat(),
        "status": "completed",
        "mode": "independent_flash_and_agentic_analysis",
        "source_boundary": {
            "sources_analyzed_separately": list(SOURCES),
            "combined_votes": False,
            "cross_source_confirmation": False,
            "cross_source_veto": False,
            "lead_lag_ranking": False,
        },
        "capture_manifest": signal_file_manifest(CAPTURE_ROOT),
        "latest_completed_market_session": latest_session,
        "sources": sources,
        "automatic_promotion": False,
        "paper_or_live_execution": False,
        "execution_authority": False,
    }
    payload["report_id"] = stable_id(
        "cipher_independent_signal_analysis",
        {
            "capture_manifest": payload["capture_manifest"],
            "latest_completed_market_session": latest_session,
            "source_counts": {source: sources[source]["episodes"]["total"] for source in SOURCES},
        },
        length=64,
    )
    atomic_json(OUTPUT, payload)
    print(
        json.dumps(
            {
                "status": "completed",
                "mode": payload["mode"],
                "output": str(OUTPUT),
                "latest_completed_market_session": latest_session,
                "sources": {
                    source: {
                        "episodes": sources[source]["episodes"]["total"],
                        "terminal_states": sources[source]["terminal_states"]["count"],
                        "matured": sources[source]["forward_scoring"]["matured"],
                        "pending": sources[source]["forward_scoring"]["pending"],
                    }
                    for source in SOURCES
                },
                "combined_votes": False,
                "execution_authority": False,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
