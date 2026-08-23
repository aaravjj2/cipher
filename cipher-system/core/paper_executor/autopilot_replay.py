"""Replay the current paper-autopilot decision contract against recorded scans.

This module deliberately replays decisions, not broker activity.  It requires the
same evidence/session fields used by ``autopilot_planner`` and keeps unresolved
paths unknown when the archive has no later observation.  Historical option marks
are not synthesized, so this report never claims option P/L.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .autopilot_planner import build_premarket_plan, confirmation_payload

NEW_YORK = ZoneInfo("America/New_York")
ENTRY_START = time(9, 35)
ENTRY_END = time(11, 30)
DEFAULT_MAX_HOLD_MINUTES = 45


def _stamp(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _phase(card: dict[str, Any]) -> str | None:
    evidence = card.get("evidence_snapshot") or {}
    session = evidence.get("session") or {}
    value = session.get("phase")
    return str(value).lower() if value else None


def _current_session_cards(payload: dict[str, Any], phase: str) -> bool:
    cards = payload.get("top") or []
    return bool(cards) and all(isinstance(card, dict) and _phase(card) == phase for card in cards)


def load_scan_history(root: Path) -> list[tuple[datetime, dict[str, Any], Path]]:
    """Load scan responses and ignore malformed/non-object artifacts."""
    rows: list[tuple[datetime, dict[str, Any], Path]] = []
    for path in sorted(root.rglob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        captured = _stamp(payload.get("as_of") or payload.get("captured_at"))
        if captured is not None:
            rows.append((captured, payload, path))
    return sorted(rows, key=lambda row: row[0])


def _day_coverage(rows: list[tuple[datetime, dict[str, Any], Path]]) -> list[dict[str, Any]]:
    dates = sorted({stamp.astimezone(NEW_YORK).date().isoformat() for stamp, _, _ in rows})
    result: list[dict[str, Any]] = []
    for market_date in dates:
        day_rows = [row for row in rows if row[0].astimezone(NEW_YORK).date().isoformat() == market_date]
        premarket = [
            row for row in day_rows
            if row[1].get("strategy") == "cipher" and _current_session_cards(row[1], "premarket")
        ]
        regular = [
            row for row in day_rows
            if row[1].get("strategy") == "flash_agentic"
            and _current_session_cards(row[1], "regular")
            and ENTRY_START <= row[0].astimezone(NEW_YORK).time().replace(tzinfo=None) <= ENTRY_END
        ]
        if premarket and regular:
            status = "exact_current_contract"
        elif premarket:
            status = "premarket_only"
        else:
            status = "legacy_or_missing_premarket_contract"
        result.append({
            "market_date": market_date,
            "status": status,
            "premarket_scans": len(premarket),
            "rth_confirmation_scans": len(regular),
        })
    return result


def _first_outcome(
    entry_time: datetime,
    entry: dict[str, Any],
    path: list[tuple[datetime, dict[str, Any], Path]],
    max_hold_minutes: int,
) -> dict[str, Any]:
    direction = str(entry.get("direction") or "").upper()
    target = _number(entry.get("target"))
    invalidation = _number(entry.get("invalidation"))
    deadline = entry_time + timedelta(minutes=max_hold_minutes)
    observations: list[tuple[datetime, float, str]] = []
    outcome: str | None = None
    exit_time: datetime | None = None
    exit_spot: float | None = None
    for observed_at, card, source in path:
        if not entry_time < observed_at <= deadline or _number(card.get("spot")) is None:
            continue
        spot = float(card["spot"])
        observations.append((observed_at, spot, str(source)))
        # Match position_manager priority: invalidation is checked before target
        # when a sparse snapshot appears to cross both levels.
        if invalidation is not None and (
            (direction == "BULLISH" and spot <= invalidation)
            or (direction == "BEARISH" and spot >= invalidation)
        ):
            outcome = "invalidation_hit"
        elif target is not None and (
            (direction == "BULLISH" and spot >= target)
            or (direction == "BEARISH" and spot <= target)
        ):
            outcome = "target_hit"
        if outcome:
            exit_time, exit_spot = observed_at, spot
            break
    if outcome is None:
        if observations:
            outcome = "unresolved_capture_path"
            exit_time, exit_spot, _ = observations[-1]
        else:
            outcome = "unresolved_no_future_snapshot"
    entry_spot = _number(entry.get("spot"))
    directional_move_pct = None
    if entry_spot and exit_spot is not None:
        move = exit_spot - entry_spot if direction == "BULLISH" else entry_spot - exit_spot
        directional_move_pct = round(move / entry_spot * 100, 4)
    return {
        "ticker": entry.get("ticker"),
        "direction": direction,
        "entry_time": entry_time.isoformat(),
        "entry_spot": entry_spot,
        "target": target,
        "invalidation": invalidation,
        "exit_time": exit_time.isoformat() if exit_time else None,
        "exit_spot": exit_spot,
        "outcome": outcome,
        "win": True if outcome == "target_hit" else False if outcome == "invalidation_hit" else None,
        "directional_move_pct": directional_move_pct,
        "option_pnl": None,
        "option_mark_status": "unavailable",
        "path_observations": len(observations),
        "source_scan": entry.get("_source_scan"),
    }


def _number(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def replay_day(
    rows: list[tuple[datetime, dict[str, Any], Path]],
    market_date: str,
    *,
    max_hold_minutes: int = DEFAULT_MAX_HOLD_MINUTES,
) -> dict[str, Any] | None:
    day_rows = [row for row in rows if row[0].astimezone(NEW_YORK).date().isoformat() == market_date]
    premarket = [
        row for row in day_rows
        if row[1].get("strategy") == "cipher" and _current_session_cards(row[1], "premarket")
    ]
    regular_confirmation = [
        row for row in day_rows
        if row[1].get("strategy") == "flash_agentic"
        and _current_session_cards(row[1], "regular")
        and ENTRY_START <= row[0].astimezone(NEW_YORK).time().replace(tzinfo=None) <= ENTRY_END
    ]
    if not premarket or not regular_confirmation:
        return None
    plan_time, plan_scan, plan_path = sorted(premarket, key=lambda row: row[0])[-1]
    plan = build_premarket_plan(plan_scan, now=plan_time, sentiment={})
    confirmations: list[tuple[datetime, dict[str, Any], Path]] = []
    for captured_at, scan, path in sorted(regular_confirmation, key=lambda row: row[0]):
        payload = confirmation_payload(plan, scan, now=captured_at)
        for card in payload.get("cards") or []:
            confirmations.append((captured_at, card, path))

    # The executor's episode/deduplication policy means repeated confirmations
    # for one ticker are one candidate entry, not multiple trades.
    unique_entries: list[tuple[datetime, dict[str, Any], Path]] = []
    seen_tickers: set[str] = set()
    for captured_at, card, path in confirmations:
        ticker = str(card.get("ticker") or "").upper()
        if not ticker or ticker in seen_tickers:
            continue
        seen_tickers.add(ticker)
        unique_entries.append((captured_at, card, path))

    paths: defaultdict[str, list[tuple[datetime, dict[str, Any], Path]]] = defaultdict(list)
    for captured_at, scan, path in day_rows:
        if captured_at <= plan_time:
            continue
        for card in scan.get("top") or []:
            if isinstance(card, dict) and _phase(card) == "regular" and card.get("ticker"):
                paths[str(card["ticker"]).upper()].append((captured_at, card, path))

    trades = []
    for captured_at, card, path in unique_entries:
        enriched = dict(card)
        enriched["_source_scan"] = path.name
        trades.append(_first_outcome(captured_at, enriched, paths[str(card["ticker"]).upper()], max_hold_minutes))

    outcomes = Counter(trade["outcome"] for trade in trades)
    return {
        "market_date": market_date,
        "mode": "exact_current_autopilot_signal_replay",
        "plan_source": plan_path.name,
        "plan_id": plan.get("plan_id"),
        "plan_candidates": len(plan.get("candidates") or []),
        "confirmation_events": len(confirmations),
        "unique_confirmations": len(unique_entries),
        "target_hits": outcomes.get("target_hit", 0),
        "invalidation_hits": outcomes.get("invalidation_hit", 0),
        "unresolved": outcomes.get("unresolved_capture_path", 0) + outcomes.get("unresolved_no_future_snapshot", 0),
        "option_pnl_available": False,
        "trades": trades,
    }


def replay_history(
    root: Path,
    *,
    dates: list[str] | None = None,
    max_hold_minutes: int = DEFAULT_MAX_HOLD_MINUTES,
) -> dict[str, Any]:
    rows = load_scan_history(root)
    coverage = _day_coverage(rows)
    requested = set(dates or [])
    exact_dates = [row["market_date"] for row in coverage if row["status"] == "exact_current_contract"]
    if requested:
        exact_dates = [day for day in exact_dates if day in requested]
    days = [replay_day(rows, day, max_hold_minutes=max_hold_minutes) for day in exact_dates]
    days = [day for day in days if day is not None]
    trades = [trade for day in days for trade in day["trades"]]
    target_hits = sum(trade["outcome"] == "target_hit" for trade in trades)
    invalidation_hits = sum(trade["outcome"] == "invalidation_hit" for trade in trades)
    unresolved = len(trades) - target_hits - invalidation_hits
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "capture_root": str(root),
        "mode": "exact_current_autopilot_signal_replay",
        "coverage": {
            "days_present": len(coverage),
            "exact_days": exact_dates,
            "day_status": coverage,
        },
        "summary": {
            "replayed_days": len(days),
            "confirmation_events": sum(day["confirmation_events"] for day in days),
            "unique_confirmations": len(trades),
            "target_hits": target_hits,
            "invalidation_hits": invalidation_hits,
            "unresolved": unresolved,
            "wins": target_hits,
            "losses": invalidation_hits,
            "win_rate_on_resolved_pct": round(target_hits / (target_hits + invalidation_hits) * 100, 2)
            if target_hits + invalidation_hits else None,
            "option_pnl_available": False,
        },
        "days": days,
        "trades": trades,
        "caveats": [
            "Only scans carrying the current evidence/session contract are replayed as exact autopilot decisions.",
            "Repeated confirmations for one ticker are deduplicated to one candidate entry, matching the executor episode policy.",
            "Underlying target/invalidation outcomes use sparse regular-session snapshots; intrabar touches between snapshots remain unknown.",
            "Historical option contract selection, bid/ask fills, option take-profit, and option stop-loss are unavailable, so option P/L is not calculated.",
            "This is a paper/read-only research replay and does not place or route orders.",
        ],
    }


def write_report(report: dict[str, Any], out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "autopilot_replay_2026-08-19.json"
    md_path = out_dir / "autopilot_replay_2026-08-19.md"
    json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    summary = report["summary"]
    lines = [
        "# Paper Autopilot Historical Replay",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "## Result",
        "",
        f"- Exact-contract replay days: **{', '.join(report['coverage']['exact_days']) or 'none'}**",
        f"- Unique confirmations: **{summary['unique_confirmations']}**",
        f"- Resolved wins/losses: **{summary['wins']} / {summary['losses']}**",
        f"- Resolved win rate: **{summary['win_rate_on_resolved_pct']}%**",
        f"- Unresolved: **{summary['unresolved']}**",
        "- Option P/L: **not calculated** (historical option marks unavailable)",
        "",
        "## Day coverage",
        "",
        "| Date | Status | Premarket scans | RTH confirmation scans |",
        "|---|---|---:|---:|",
    ]
    for row in report["coverage"]["day_status"]:
        lines.append(f"| {row['market_date']} | {row['status']} | {row['premarket_scans']} | {row['rth_confirmation_scans']} |")
    lines += ["", "## Replayed entries", "", "| Date | Ticker | Entry | Outcome | Directional move % |", "|---|---|---|---|---:|"]
    for trade in report["trades"]:
        lines.append(
            f"| {next((day['market_date'] for day in report['days'] if trade in day['trades']), '')} | "
            f"{trade['ticker']} | {trade['entry_time']} | {trade['outcome']} | {trade['directional_move_pct']} |"
        )
    lines += ["", "## Caveats", ""]
    lines.extend(f"- {caveat}" for caveat in report["caveats"])
    lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--date", action="append", dest="dates")
    parser.add_argument("--max-hold-minutes", type=int, default=DEFAULT_MAX_HOLD_MINUTES)
    parser.add_argument("--out-dir", type=Path)
    args = parser.parse_args(argv)
    report = replay_history(args.root, dates=args.dates, max_hold_minutes=args.max_hold_minutes)
    if args.out_dir:
        report["paths"] = write_report(report, args.out_dir)
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
