#!/usr/bin/env python3
"""Freeze the latest Cluster scan, then build a deterministic research strategy.

The raw browser-ingest scan is already persisted before this script can observe
it. ``--freeze-only`` creates a second immutable decision-input snapshot before
any slower analysis is run. The normal mode then combines:

- Cluster Tier A persistence and target geometry;
- exact option-spread liquidity and payoff constraints;
- standalone Flash and Agentic states as non-gating confidence context;
- locally governed public-event headlines as explanatory risk context; and
- explicit entry, management, and exit values for a paper/manual review plan.

This module has no broker client, no order endpoint, and no execution authority.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "core") not in sys.path:
    sys.path.insert(0, str(ROOT / "core"))

from core.research_platform.hashing import sha256_file, stable_id  # noqa: E402

try:  # Provider failures remain explicit and never block local snapshotting.
    from app import quote  # type: ignore  # noqa: E402
except Exception:  # pragma: no cover - import depends on local runtime config.
    quote = None

UTC = timezone.utc
COMPANY_ALIASES = {
    "BIDU": ("baidu",),
    "BURL": ("burlington",),
    "CRM": ("salesforce", "benioff"),
    "CRWD": ("crowdstrike",),
    "CVNA": ("carvana",),
    "GEV": ("ge vernova", "vernova"),
    "INTC": ("intel",),
    "INTU": ("intuit",),
    "LMND": ("lemonade",),
    "MA": ("mastercard",),
    "RTX": ("rtx", "raytheon"),
    "SIG": ("signet",),
    "UBER": ("uber",),
    "ZS": ("zscaler",),
}
MARKET_CONTEXT_PREFIXES = (
    "market update",
    "stocks settle",
    "stocks close",
    "stock market",
    "futures",
)
CAPTURE_ROOT = ROOT / "data" / "browser_ingest"
SIGNAL_GOV = ROOT / "data" / "governance" / "cipher_signal_only"
STRATEGY_GOV = ROOT / "data" / "governance" / "cipher_strategy"
INPUT_SNAPSHOTS = STRATEGY_GOV / "input_snapshots"
DECISIONS = STRATEGY_GOV / "decisions"
LATEST = STRATEGY_GOV / "latest_strategy_decision.json"
TRADE_REPORT = SIGNAL_GOV / "latest_cluster_trade_candidates.json"
INDEPENDENT_REPORT = SIGNAL_GOV / "latest_independent_signal_analysis.json"
CLUSTER_REPORT = SIGNAL_GOV / "latest_cluster_individual_analysis.json"
NEWS_DB = ROOT / "data" / "governance" / "research_registry.sqlite"


def finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def nested(row: Mapping[str, Any], *keys: str) -> Any:
    value: Any = row
    for key in keys:
        value = value.get(key) if isinstance(value, Mapping) else None
    return value


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    temporary.replace(path)


def immutable_json(path: Path, payload: Mapping[str, Any]) -> None:
    encoded = json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        if path.read_text(encoding="utf-8") != encoded:
            raise RuntimeError(f"immutable snapshot mismatch: {path}")
        return
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(encoded, encoding="utf-8")
    temporary.replace(path)


def latest_nonempty_line(path: Path) -> str:
    with path.open("rb") as handle:
        handle.seek(0, 2)
        position = handle.tell()
        buffer = b""
        while position > 0:
            step = min(position, 65536)
            position -= step
            handle.seek(position)
            buffer = handle.read(step) + buffer
            lines = [line for line in buffer.splitlines() if line.strip()]
            if lines:
                return lines[-1].decode("utf-8")
    raise RuntimeError(f"no non-empty records in {path}")


def freeze_latest_cluster_scan(
    *,
    capture_root: Path = CAPTURE_ROOT,
    snapshot_root: Path = INPUT_SNAPSHOTS,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    paths = sorted(capture_root.glob("cluster-scans-v2-*.jsonl"))
    if not paths:
        raise RuntimeError(f"no Cluster scan JSONL files under {capture_root}")
    source = paths[-1]
    raw_line = latest_nonempty_line(source)
    try:
        record = json.loads(raw_line)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"latest Cluster scan is invalid JSON: {source}") from exc
    if not isinstance(record, dict):
        raise RuntimeError("latest Cluster scan record is not an object")
    observed = observed_at or datetime.now(UTC)
    line_sha = hashlib.sha256(raw_line.encode("utf-8")).hexdigest()
    source_sha = sha256_file(source)
    captured_at = str(record.get("client_timestamp") or record.get("received_at") or observed.isoformat())
    session = captured_at[:10] if len(captured_at) >= 10 else observed.date().isoformat()
    snapshot = {
        "schema_version": 1,
        "snapshot_stage": "raw_cluster_scan_frozen_before_strategy_analysis",
        "observed_at": observed.isoformat(),
        "captured_at": captured_at,
        "source_path": str(source),
        "source_file_sha256": source_sha,
        "source_line_sha256": line_sha,
        "source_record": record,
        "record_count": record.get("records"),
        "scan_type": record.get("scan_type"),
        "execution_authority": False,
    }
    snapshot_id = stable_id(
        "cipher_cluster_strategy_input_snapshot",
        {
            "source_file_sha256": source_sha,
            "source_line_sha256": line_sha,
            "captured_at": captured_at,
        },
        length=64,
    )
    snapshot["snapshot_id"] = snapshot_id
    path = snapshot_root / session / f"cluster_input_{snapshot_id}.json"
    if path.is_file():
        existing = read_json(path)
        if existing.get("source_line_sha256") != line_sha or existing.get("source_file_sha256") != source_sha:
            raise RuntimeError(f"immutable snapshot identity mismatch: {path}")
        return {"path": str(path), "snapshot": existing}
    immutable_json(path, snapshot)
    return {"path": str(path), "snapshot": snapshot}


def business_sessions_after(start: str, expiry: str) -> int | None:
    try:
        current = date.fromisoformat(start)
        end = date.fromisoformat(expiry)
    except (TypeError, ValueError):
        return None
    count = 0
    current += timedelta(days=1)
    while current <= end:
        if current.weekday() < 5:
            count += 1
        current += timedelta(days=1)
    return count


def latest_source_states(independent: Mapping[str, Any], session: str) -> dict[str, dict[str, dict[str, Any]]]:
    output: dict[str, dict[str, dict[str, Any]]] = {"flash": {}, "flash_agentic": {}}
    sources = independent.get("sources") if isinstance(independent.get("sources"), Mapping) else {}
    for source_name in output:
        source = sources.get(source_name) if isinstance(sources.get(source_name), Mapping) else {}
        terminal = source.get("terminal_states") if isinstance(source.get("terminal_states"), Mapping) else {}
        records = terminal.get("records") if isinstance(terminal.get("records"), list) else []
        for row in records:
            if not isinstance(row, Mapping) or str(row.get("market_session") or "") != session:
                continue
            ticker = str(row.get("ticker") or "").upper()
            if ticker:
                output[source_name][ticker] = dict(row)
    return output


def source_modifier(state: Mapping[str, Any] | None, *, expected_direction: str, source: str) -> dict[str, Any]:
    if not state:
        return {
            "source": source,
            "coverage": "not_available_for_ticker_session",
            "modifier_points": 0,
            "is_trade_gate": False,
        }
    direction = str(state.get("direction") or "")
    score = finite(state.get("score"))
    same = direction == expected_direction
    opposite = direction in {"BULLISH", "BEARISH"} and not same
    points = 0
    if source == "flash_agentic":
        if same and score is not None and score >= 70:
            points = 6
        elif same and score is not None and score >= 60:
            points = 3
        elif opposite and score is not None and score >= 70:
            points = -8
    else:
        if same and score is not None and score >= 80:
            points = 4
        elif same and score is not None and score >= 60:
            points = 2
        elif opposite and score is not None and score >= 80:
            points = -5
    return {
        "source": source,
        "coverage": "available",
        "direction": direction or None,
        "score": score,
        "setup_family": state.get("setup_family"),
        "target": state.get("target"),
        "invalidation": state.get("invalidation"),
        "same_direction": same,
        "opposite_direction": opposite,
        "modifier_points": points,
        "is_trade_gate": False,
    }


def load_news_events(*, through: datetime, lookback_days: int = 7) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    by_ticker: dict[str, list[dict[str, Any]]] = {}
    if not NEWS_DB.is_file():
        return by_ticker, {"status": "news_registry_unavailable", "latest_available_at": None}
    cutoff = through - timedelta(days=max(1, lookback_days))
    latest_available: datetime | None = None
    with sqlite3.connect(NEWS_DB) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute(
            """
            select source, publication_time, received_at, available_at, symbols_json, payload_json
            from news_events
            where available_at <= ? and publication_time >= ?
            order by publication_time desc
            """,
            (through.isoformat(), cutoff.isoformat()),
        ).fetchall()
    for row in rows:
        try:
            payload = json.loads(row["payload_json"])
            symbols = json.loads(row["symbols_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        available_at = str(row["available_at"] or "")
        try:
            available_dt = datetime.fromisoformat(available_at.replace("Z", "+00:00"))
            if available_dt.tzinfo is None:
                available_dt = available_dt.replace(tzinfo=UTC)
            available_dt = available_dt.astimezone(UTC)
            latest_available = max(latest_available, available_dt) if latest_available else available_dt
        except ValueError:
            available_dt = None
        event = {
            "source": row["source"],
            "title": payload.get("title"),
            "publication_time": row["publication_time"],
            "available_at": available_at,
            "positive_probability": finite(payload.get("positive_probability")),
            "negative_probability": finite(payload.get("negative_probability")),
            "neutral_probability": finite(payload.get("neutral_probability")),
            "high_magnitude": bool(payload.get("high_magnitude")),
            "publisher": nested(payload, "metadata", "publisher"),
            "source_url": nested(payload, "metadata", "source_url"),
            "source_scope": nested(payload, "metadata", "source_scope"),
            "headline_relevance": nested(payload, "metadata", "headline_relevance"),
            "company_specific": nested(payload, "metadata", "company_specific"),
            "provider_related_tickers": nested(payload, "metadata", "provider_related_tickers"),
            "directional_signal_allowed": bool(nested(payload, "metadata", "directional_signal_allowed")),
        }
        for symbol in symbols if isinstance(symbols, list) else []:
            ticker = str(symbol).upper()
            if ticker:
                by_ticker.setdefault(ticker, []).append(event)
    age_hours = (through - latest_available).total_seconds() / 3600.0 if latest_available else None
    return by_ticker, {
        "status": "available" if latest_available else "no_events_in_window",
        "latest_available_at": latest_available.isoformat() if latest_available else None,
        "latest_event_age_hours": age_hours,
        "stale_for_intraday_use": age_hours is None or age_hours > 6.0,
        "role": "explanatory_risk_context_only",
        "directional_trade_gate": False,
    }


def event_is_company_specific(event: Mapping[str, Any], ticker: str) -> bool:
    explicit = event.get("company_specific")
    if isinstance(explicit, bool):
        return explicit
    title = str(event.get("title") or "")
    lowered = title.casefold().strip()
    if lowered.startswith(MARKET_CONTEXT_PREFIXES):
        return False
    if ticker.upper() in {token.strip("()[]{}.,:;").upper() for token in title.split()}:
        return True
    return any(alias in lowered for alias in COMPANY_ALIASES.get(ticker.upper(), ()))


def catalyst_context(
    events: Iterable[Mapping[str, Any]],
    *,
    ticker: str,
    direction: str,
    through: datetime,
) -> dict[str, Any]:
    by_title: dict[str, Mapping[str, Any]] = {}
    title_order: list[str] = []
    for event in events:
        title_key = str(event.get("title") or "").casefold().strip()
        if not title_key:
            continue
        if title_key not in by_title:
            title_order.append(title_key)
            by_title[title_key] = event
            continue
        prior = by_title[title_key]
        if not isinstance(prior.get("company_specific"), bool) and isinstance(event.get("company_specific"), bool):
            by_title[title_key] = event
    deduped = [by_title[key] for key in title_order]
    company_specific = [event for event in deduped if event_is_company_specific(event, ticker)][:5]
    market_context = [event for event in deduped if not event_is_company_specific(event, ticker)][:3]
    nets: list[float] = []
    within_48h = 0
    for event in company_specific:
        positive = finite(event.get("positive_probability"))
        negative = finite(event.get("negative_probability"))
        if positive is not None and negative is not None:
            nets.append(positive - negative)
        try:
            published = datetime.fromisoformat(str(event.get("publication_time")).replace("Z", "+00:00"))
            if published.tzinfo is None:
                published = published.replace(tzinfo=UTC)
            if through - published.astimezone(UTC) <= timedelta(hours=48):
                within_48h += 1
        except ValueError:
            pass
    net = mean(nets) if nets else None
    directional_net = net if direction == "BULLISH" else (-net if net is not None else None)
    if directional_net is None:
        alignment = "unavailable"
        points = 0
    elif directional_net >= 0.20:
        alignment = "headline_sentiment_aligned"
        points = 3
    elif directional_net <= -0.20:
        alignment = "headline_sentiment_conflicting"
        points = -5
    else:
        alignment = "headline_sentiment_mixed_or_neutral"
        points = 0
    return {
        "event_count_7d": len(company_specific),
        "event_count_48h": within_48h,
        "average_sentiment_net": net,
        "directional_alignment": alignment,
        "modifier_points": points,
        "events": company_specific,
        "market_context_events": market_context,
        "is_trade_gate": False,
        "limitation": "Most governed items are headline metadata; causal attribution remains uncertain.",
    }


def spread_checks(candidate: Mapping[str, Any]) -> dict[str, Any]:
    geometry = candidate.get("quote_geometry") if isinstance(candidate.get("quote_geometry"), Mapping) else {}
    width = finite(geometry.get("spread_width"))
    debit = finite(geometry.get("midpoint_debit"))
    natural = finite(geometry.get("natural_debit"))
    long_bid = finite(geometry.get("long_bid"))
    long_ask = finite(geometry.get("long_ask"))
    short_bid = finite(geometry.get("short_bid"))
    short_ask = finite(geometry.get("short_ask"))
    long_quote_width = finite(geometry.get("long_relative_quote_width"))
    short_quote_width = finite(geometry.get("short_relative_quote_width"))
    long_volume = finite(geometry.get("long_volume")) or 0.0
    short_volume = finite(geometry.get("short_volume")) or 0.0
    long_oi = finite(geometry.get("long_open_interest")) or 0.0
    short_oi = finite(geometry.get("short_open_interest")) or 0.0
    target = finite(candidate.get("latest_target"))
    breakeven = finite(geometry.get("reference_breakeven"))

    quote_integrity = all(
        value is not None
        for value in (long_bid, long_ask, short_bid, short_ask)
    ) and bool(long_bid and short_bid and long_ask > long_bid and short_ask > short_bid)
    quote_widths_pass = bool(
        long_quote_width is not None
        and short_quote_width is not None
        and long_quote_width <= 0.15
        and short_quote_width <= 0.25
    )
    depth_pass = bool(
        (long_oi >= 50 or long_volume >= 20)
        and (short_oi >= 50 or short_volume >= 20)
    )
    geometry_pass = bool(width and debit and natural and 0 < debit < width and 0 < natural < width)
    debit_fraction = debit / width if geometry_pass and width else None
    return_on_risk = (width - debit) / debit if geometry_pass and debit else None
    economics_pass = bool(
        geometry_pass
        and debit_fraction is not None
        and debit_fraction <= 0.45
        and return_on_risk is not None
        and return_on_risk >= 1.25
        and breakeven is not None
        and target is not None
        and breakeven < target
    )
    maximum_limit = None
    if geometry_pass and width is not None and debit is not None and natural is not None:
        maximum_limit = min(0.45 * width, debit + 0.25 * max(natural - debit, 0.0))
    return {
        "quote_integrity_pass": quote_integrity,
        "quote_widths_pass": quote_widths_pass,
        "depth_pass": depth_pass,
        "geometry_pass": geometry_pass,
        "economics_pass": economics_pass,
        "long_relative_quote_width": long_quote_width,
        "short_relative_quote_width": short_quote_width,
        "long_volume": long_volume,
        "short_volume": short_volume,
        "long_open_interest": long_oi,
        "short_open_interest": short_oi,
        "spread_width": width,
        "midpoint_debit": debit,
        "natural_debit": natural,
        "debit_fraction_of_width": debit_fraction,
        "maximum_return_on_risk": return_on_risk,
        "breakeven": breakeven,
        "initial_limit_debit": debit,
        "maximum_limit_debit": maximum_limit,
    }


def exit_plan(checks: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, Any]:
    width = finite(checks.get("spread_width"))
    debit = finite(checks.get("midpoint_debit"))
    if width is None or debit is None or width <= debit:
        return {"available": False}
    return {
        "available": True,
        "target_hit_exit_underlying": finite(candidate.get("latest_target")),
        "profit_exit_spread_value": debit + 0.70 * (width - debit),
        "profit_capture_fraction_of_maximum": 0.70,
        "loss_exit_spread_value": 0.50 * debit,
        "loss_fraction_of_original_debit": 0.50,
        "cluster_invalidation_exit": [
            "latest direction is no longer BULLISH",
            "latest research tier is no longer tier_a_cluster_only",
            "rank is above 10",
            "strength is outside 200 through 299",
            "target is at or below live stock price",
        ],
        "carry_overnight_only_if": [
            "Cluster remains bullish Tier A at the final scan",
            "more than 1 percent target room remains",
            "at least two full trading sessions remain before expiration",
            "neither profit nor loss exit has triggered",
        ],
        "expiration_exit": "close no later than the final regular-session scan on the trading day before expiration",
        "execution_authority": False,
    }


def quote_session_context(as_of: Any) -> str:
    try:
        parsed = datetime.fromisoformat(str(as_of).replace("Z", "+00:00"))
    except ValueError:
        return "unknown"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    eastern = parsed.astimezone(ZoneInfo("America/New_York"))
    minute = eastern.hour * 60 + eastern.minute
    return "regular_session" if eastern.weekday() < 5 and 570 <= minute <= 960 else "extended_hours_reference"


def move_attribution(
    candidate: Mapping[str, Any],
    *,
    spy_change_pct: float | None,
    catalysts: Mapping[str, Any],
) -> dict[str, Any]:
    change = finite(nested(candidate, "underlying_quote", "day_change_pct"))
    quote_as_of = nested(candidate, "underlying_quote", "as_of")
    quote_session = quote_session_context(quote_as_of)
    excess = change - spy_change_pct if change is not None and spy_change_pct is not None else None
    if catalysts.get("event_count_48h", 0) and catalysts.get("directional_alignment") == "headline_sentiment_aligned":
        classification = "recent_headline_context_aligns_with_move_but_does_not_prove_causality"
    elif change is not None and spy_change_pct is not None and excess is not None and abs(excess) <= 0.75 and change * spy_change_pct >= 0:
        classification = "mostly_broad_market_or_factor_move"
    elif excess is not None and abs(excess) >= 1.5 and not catalysts.get("event_count_48h", 0):
        classification = "stock_specific_or_technical_move_without_fresh_local_catalyst"
    else:
        classification = "mixed_or_unresolved"
    return {
        "stock_day_change_pct": change,
        "stock_quote_as_of": quote_as_of,
        "stock_quote_session_context": quote_session,
        "spy_day_change_pct": spy_change_pct,
        "excess_move_vs_spy_pct": excess,
        "classification": classification,
        "causality_confirmed": False,
    }


def evaluate_candidate(
    candidate: Mapping[str, Any],
    *,
    session: str,
    source_states: Mapping[str, Mapping[str, Mapping[str, Any]]],
    news_by_ticker: Mapping[str, list[dict[str, Any]]],
    through: datetime,
    spy_change_pct: float | None,
) -> dict[str, Any]:
    ticker = str(candidate.get("ticker") or "").upper()
    direction = str(candidate.get("latest_direction") or "")
    persisted = bool(candidate.get("persisted_tier_a_to_last_capture"))
    tier = str(candidate.get("latest_tier") or "")
    rank = finite(candidate.get("latest_rank"))
    strength = finite(candidate.get("latest_strength"))
    target_room = finite(candidate.get("target_remaining_distance_pct"))
    expiry = str(candidate.get("cluster_expiration") or "")
    sessions_remaining = business_sessions_after(session, expiry)
    checks = spread_checks(candidate)
    target_zone_pass = target_room is not None and 2.0 <= target_room <= 5.0
    cluster_pass = bool(
        persisted
        and direction == "BULLISH"
        and tier == "tier_a_cluster_only"
        and rank is not None
        and 1 <= rank <= 10
        and strength is not None
        and 200 <= strength <= 299
    )
    expiry_pass = sessions_remaining is not None and sessions_remaining >= 4

    flash = source_modifier(source_states.get("flash", {}).get(ticker), expected_direction="BULLISH", source="flash")
    agentic = source_modifier(
        source_states.get("flash_agentic", {}).get(ticker),
        expected_direction="BULLISH",
        source="flash_agentic",
    )
    catalysts = catalyst_context(
        news_by_ticker.get(ticker, []),
        ticker=ticker,
        direction="BULLISH",
        through=through,
    )
    attribution = move_attribution(candidate, spy_change_pct=spy_change_pct, catalysts=catalysts)

    hard_rules = {
        "cluster_tier_and_persistence": cluster_pass,
        "target_room_2_to_5_pct": target_zone_pass,
        "at_least_four_future_business_sessions": expiry_pass,
        "quote_integrity": checks["quote_integrity_pass"],
        "quote_widths": checks["quote_widths_pass"],
        "option_depth": checks["depth_pass"],
        "valid_debit_geometry": checks["geometry_pass"],
        "spread_economics": checks["economics_pass"],
    }
    failed = [name for name, passed in hard_rules.items() if not passed]
    eligible = not failed

    score = 35
    score += 15 if cluster_pass else 0
    score += 15 if target_zone_pass else 0
    score += 10 if expiry_pass else 0
    score += 8 if checks["quote_widths_pass"] else 0
    score += 7 if checks["depth_pass"] else 0
    score += 10 if checks["economics_pass"] else 0
    score += int(flash.get("modifier_points") or 0)
    score += int(agentic.get("modifier_points") or 0)
    score += int(catalysts.get("modifier_points") or 0)

    auxiliary_available = sum(
        overlay.get("coverage") == "available" for overlay in (flash, agentic)
    )
    fresh_catalyst_available = bool(catalysts.get("event_count_48h"))
    if auxiliary_available == 0 and not fresh_catalyst_available:
        uncertainty = "high_auxiliary_evidence_missing"
        evidence_cap = 65
        confidence_cap_reason = "no same-ticker Flash/Agentic state and no fresh governed catalyst"
    elif auxiliary_available >= 1 and fresh_catalyst_available:
        uncertainty = "reduced_but_not_removed"
        evidence_cap = 85
        confidence_cap_reason = "technical overlay and fresh catalyst context both available"
    else:
        uncertainty = "medium"
        evidence_cap = 75
        confidence_cap_reason = "only one auxiliary evidence class is available"
    catalyst_conflict = catalysts.get("directional_alignment") == "headline_sentiment_conflicting"
    if catalyst_conflict:
        evidence_cap = min(evidence_cap, 65)
        uncertainty = "medium_with_company_specific_catalyst_conflict"
        confidence_cap_reason += "; fresh company-specific headline sentiment conflicts with the Cluster direction"
    score = max(0, min(score, evidence_cap))

    if eligible and catalyst_conflict:
        status = "eligible_with_catalyst_conflict_for_manual_review"
    elif eligible:
        status = "eligible_for_paper_or_manual_review"
    elif cluster_pass and target_zone_pass:
        status = "watch_spread_or_expiry_rules_failed"
    elif cluster_pass:
        status = "watch_cluster_valid_but_target_zone_failed"
    else:
        status = "skip_cluster_rules_failed"

    return {
        "ticker": ticker,
        "strategy_status": status,
        "eligible": eligible,
        "failed_hard_rules": failed,
        "hard_rules": hard_rules,
        "confidence_score_capped_at_85": score,
        "confidence_evidence_cap": evidence_cap,
        "confidence_cap_reason": confidence_cap_reason,
        "uncertainty": uncertainty,
        "catalyst_conflict_risk_flag": catalyst_conflict,
        "cluster": {
            "direction": direction,
            "tier": tier,
            "rank": rank,
            "strength": strength,
            "persisted_to_latest_same_day_capture": persisted,
            "target": finite(candidate.get("latest_target")),
            "target_room_pct": target_room,
            "expiration": expiry or None,
            "future_business_sessions_through_expiry": sessions_remaining,
        },
        "structure": {
            "type": "bull_call_debit_spread",
            "long_contract": candidate.get("long_atm_contract"),
            "long_strike": finite(candidate.get("long_strike")),
            "short_contract": candidate.get("short_target_contract"),
            "short_strike": finite(candidate.get("short_strike")),
            "selection_rule": "long nearest live ATM call; short highest liquid call at or below Cluster target",
            **checks,
        },
        "entry_plan": {
            "confirmation": "prior-day persistent name may qualify at 09:45 ET; a newly appearing name requires the next 45-minute scan",
            "initial_limit_debit": checks.get("initial_limit_debit"),
            "maximum_limit_debit": checks.get("maximum_limit_debit"),
            "order_handling": "paper/manual complex spread only; start at midpoint, revise once toward the capped limit, cancel rather than chase",
            "execution_authority": False,
        },
        "exit_plan": exit_plan(checks, candidate),
        "flash_context": flash,
        "agentic_context": agentic,
        "catalyst_context": catalysts,
        "move_attribution": attribution,
        "source_candidate": dict(candidate),
        "automatic_promotion": False,
        "paper_or_live_execution": False,
        "execution_authority": False,
    }


def strategy_sort_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    target_room = finite(nested(row, "cluster", "target_room_pct"))
    target_middle_distance = abs(target_room - 3.5) if target_room is not None else math.inf
    rank = finite(nested(row, "cluster", "rank")) or 999
    return (
        0 if row.get("eligible") else 1,
        -int(row.get("confidence_score_capped_at_85") or 0),
        target_middle_distance,
        rank,
        str(row.get("ticker") or ""),
    )


def market_context() -> dict[str, Any]:
    if quote is None:
        return {"status": "provider_quote_module_unavailable", "spy_day_change_pct": None}
    try:
        payload = quote("SPY")
    except Exception as exc:  # network/provider failure is normal and explicit.
        return {"status": "provider_error", "error": str(exc), "spy_day_change_pct": None}
    return {
        "status": "available",
        "spy_day_change_pct": finite(payload.get("day_change_pct")),
        "spy_quote": payload,
    }


def build_strategy() -> dict[str, Any]:
    frozen = freeze_latest_cluster_scan()
    created_at = datetime.now(UTC)
    trade = read_json(TRADE_REPORT)
    independent = read_json(INDEPENDENT_REPORT)
    cluster = read_json(CLUSTER_REPORT)
    if not trade or not independent or not cluster:
        raise RuntimeError("required Cluster, trade-board, or independent report is missing")
    session = str(trade.get("latest_signal_session") or "")
    board = trade.get("current_trade_board") if isinstance(trade.get("current_trade_board"), Mapping) else {}
    candidates = board.get("candidates") if isinstance(board.get("candidates"), list) else []
    source_states = latest_source_states(independent, session)
    news_by_ticker, news_status = load_news_events(through=created_at)
    market = market_context()
    spy_change = finite(market.get("spy_day_change_pct"))
    evaluated = [
        evaluate_candidate(
            row,
            session=session,
            source_states=source_states,
            news_by_ticker=news_by_ticker,
            through=created_at,
            spy_change_pct=spy_change,
        )
        for row in candidates
        if isinstance(row, Mapping)
    ]
    evaluated.sort(key=strategy_sort_key)
    for index, row in enumerate(evaluated, start=1):
        row["strategy_rank"] = index

    overlay_coverage = {
        "flash_matching_candidate_count": sum(row["flash_context"].get("coverage") == "available" for row in evaluated),
        "agentic_matching_candidate_count": sum(row["agentic_context"].get("coverage") == "available" for row in evaluated),
        "candidate_count": len(evaluated),
        "interpretation": (
            "Flash and Agentic currently cover a much narrower ticker universe than Cluster. "
            "Missing context is neutral, not a negative vote."
        ),
    }
    payload = {
        "schema_version": 1,
        "created_at": created_at.isoformat(),
        "status": "completed",
        "mode": "cluster_primary_deterministic_spread_strategy_with_non_gating_context",
        "input_snapshot": frozen,
        "source_reports": {
            "cluster_trade_board": {"path": str(TRADE_REPORT), "sha256": sha256_file(TRADE_REPORT)},
            "cluster_individual": {"path": str(CLUSTER_REPORT), "sha256": sha256_file(CLUSTER_REPORT)},
            "independent_flash_agentic": {"path": str(INDEPENDENT_REPORT), "sha256": sha256_file(INDEPENDENT_REPORT)},
        },
        "latest_signal_session": session,
        "scan_schedule_et": ["09:45", "10:30", "11:15", "12:00", "12:45", "13:30", "14:15", "15:00", "15:45"],
        "new_entry_cutoff_et": "14:15",
        "strategy_rules": {
            "primary_signal": "Cluster Tier A only",
            "entry_confirmation": "prior-day persistent at 09:45 ET or two consecutive intraday scans for new names",
            "target_room_pct": [2.0, 5.0],
            "minimum_future_business_sessions": 4,
            "long_relative_quote_width_max": 0.15,
            "short_relative_quote_width_max": 0.25,
            "minimum_leg_depth": "each leg must have open interest >=50 or session volume >=20",
            "maximum_debit_fraction_of_width": 0.45,
            "minimum_maximum_return_on_risk": 1.25,
            "profit_capture_fraction_of_maximum": 0.70,
            "loss_exit_fraction_of_debit": 0.50,
            "flash_agentic_role": "non-gating confidence and conflict context",
            "news_role": "explanatory risk context only; never a directional trade gate",
        },
        "market_context": market,
        "news_context_status": news_status,
        "overlay_coverage": overlay_coverage,
        "candidates": evaluated,
        "eligible_candidates": [row for row in evaluated if row.get("eligible")],
        "limits": {
            "only_four_delayed_entries_were_finalized_at_expiry_in_the_source_research": True,
            "current_quotes_are_reference_values_until_refreshed_at_the_scan": True,
            "headline_sentiment_does_not_establish_causality": True,
            "flash_agentic_coverage_is_not_broad_enough_for_a_required_gate": True,
            "confidence_is_capped_at_85": True,
        },
        "automatic_promotion": False,
        "paper_or_live_execution": False,
        "execution_authority": False,
    }
    payload["strategy_id"] = stable_id(
        "cipher_cluster_strategy_decision",
        {
            "created_at": payload["created_at"],
            "input_snapshot_id": nested(frozen, "snapshot", "snapshot_id"),
            "source_reports": payload["source_reports"],
            "candidate_contracts": [
                {
                    "ticker": row.get("ticker"),
                    "long": nested(row, "structure", "long_contract"),
                    "short": nested(row, "structure", "short_contract"),
                    "eligible": row.get("eligible"),
                }
                for row in evaluated
            ],
        },
        length=64,
    )
    decision_path = DECISIONS / session / f"strategy_{payload['strategy_id']}.json"
    immutable_json(decision_path, payload)
    payload["decision_path"] = str(decision_path)
    atomic_json(LATEST, payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--freeze-only", action="store_true")
    args = parser.parse_args()
    if args.freeze_only:
        result = freeze_latest_cluster_scan()
        print(json.dumps({
            "status": "frozen",
            "path": result["path"],
            "snapshot_id": nested(result, "snapshot", "snapshot_id"),
            "execution_authority": False,
        }, indent=2, sort_keys=True))
        return 0
    payload = build_strategy()
    print(json.dumps({
        "status": payload["status"],
        "strategy_id": payload["strategy_id"],
        "latest_signal_session": payload["latest_signal_session"],
        "eligible": [
            {
                "rank": row.get("strategy_rank"),
                "ticker": row.get("ticker"),
                "confidence": row.get("confidence_score_capped_at_85"),
                "uncertainty": row.get("uncertainty"),
                "long": nested(row, "structure", "long_contract"),
                "short": nested(row, "structure", "short_contract"),
                "initial_limit": nested(row, "entry_plan", "initial_limit_debit"),
                "maximum_limit": nested(row, "entry_plan", "maximum_limit_debit"),
            }
            for row in payload["eligible_candidates"]
        ],
        "overlay_coverage": payload["overlay_coverage"],
        "news_context_status": payload["news_context_status"],
        "decision_path": payload["decision_path"],
        "execution_authority": False,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
