#!/usr/bin/env python3
"""Build a causal-context postmortem for every modeled Tier A Cluster trade.

The report preserves the original trade record and separates information that
was available at the Cluster signal from evidence that appeared later. Flash,
Agentic, news, and market context are explanatory only; they never create or
execute an order.

News is primarily headline metadata. Attribution labels therefore describe the
best supported context, not proven causality.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from bisect import bisect_left
from collections import Counter, defaultdict
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from core.research_platform.hashing import stable_id  # noqa: E402
from export_tier_a_trade_log import rows as exported_trade_rows  # noqa: E402
from run_cipher_cluster_individual_analysis import (  # noqa: E402
    first_number,
    prepare_daily_bars,
    prepare_minute_bars,
)
from run_cipher_cluster_strategy import (  # noqa: E402
    event_is_company_specific,
    finite,
    load_news_events,
    nested,
)
from run_cipher_complete_observations import (  # noqa: E402
    STOCK_BARS_URL,
    STOCK_DAILY_CACHE,
    STOCK_MINUTE_CACHE,
    atomic_json,
    load_or_fetch_daily_bars,
    load_or_fetch_partitioned_minute_bars,
    provider_headers,
)

UTC = timezone.utc
NY = ZoneInfo("America/New_York")
GOV = ROOT / "data" / "governance" / "cipher_strategy"
INDEPENDENT_REPORT = ROOT / "data" / "governance" / "cipher_signal_only" / "latest_independent_signal_analysis.json"
OUTPUT = GOV / "latest_trade_postmortems.json"
CSV_OUTPUT = GOV / "latest_trade_postmortems.csv"
ARCHIVE_ROOT = GOV / "trade_postmortems"


def parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def safe_float(value: Any) -> float | None:
    number = finite(value)
    return number if number is not None and math.isfinite(number) else None


def pct_label(value: float | None) -> str:
    if value is None:
        return "unavailable"
    if value > 0.25:
        return "positive"
    if value < -0.25:
        return "negative"
    return "flat"


def relationship(direction: str, cluster_direction: str = "BULLISH") -> str:
    normalized = str(direction or "").upper()
    if normalized not in {"BULLISH", "BEARISH"}:
        return "neutral_or_unavailable"
    return "same_direction" if normalized == cluster_direction else "opposite_direction"


def episode_time(row: Mapping[str, Any]) -> datetime | None:
    return parse_time(row.get("first_seen_at"))


def compact_episode(row: Mapping[str, Any] | None, *, signal_at: datetime) -> dict[str, Any]:
    if not row:
        return {
            "available": False,
            "direction": None,
            "relationship_to_cluster": "unavailable",
            "score": None,
            "setup_family": None,
            "observed_at": None,
            "lag_minutes_from_cluster": None,
        }
    observed = episode_time(row)
    lag = (observed - signal_at).total_seconds() / 60.0 if observed else None
    return {
        "available": True,
        "direction": row.get("direction"),
        "relationship_to_cluster": relationship(str(row.get("direction") or "")),
        "score": safe_float(row.get("score")),
        "rank": safe_float(row.get("rank")),
        "setup_family": row.get("setup_family"),
        "actionable": bool(row.get("actionable")),
        "geometry_valid": bool(row.get("geometry_valid")),
        "observed_at": observed.isoformat() if observed else None,
        "lag_minutes_from_cluster": lag,
        "signal_id": row.get("signal_id"),
    }


def source_timeline_context(
    records: Sequence[Mapping[str, Any]],
    *,
    ticker: str,
    session: str,
    signal_at: datetime,
) -> dict[str, Any]:
    day = [
        row
        for row in records
        if str(row.get("ticker") or "").upper() == ticker.upper()
        and str(row.get("market_session") or "") == session
        and bool(row.get("regular_hours"))
        and episode_time(row) is not None
    ]
    day.sort(key=lambda row: episode_time(row) or datetime.min.replace(tzinfo=UTC))
    before = [row for row in day if (episode_time(row) or signal_at) <= signal_at]
    after = [row for row in day if (episode_time(row) or signal_at) > signal_at]
    latest_before = before[-1] if before else None
    first_after = after[0] if after else None
    terminal = day[-1] if day else None
    return {
        "ticker_day_coverage": bool(day),
        "ticker_day_episode_count": len(day),
        "available_at_cluster_signal": compact_episode(latest_before, signal_at=signal_at),
        "first_post_cluster_signal": compact_episode(first_after, signal_at=signal_at),
        "terminal_same_day_state": compact_episode(terminal, signal_at=signal_at),
        "temporal_boundary": (
            "Only available_at_cluster_signal may be treated as contemporaneous entry context. "
            "Post-signal and terminal states are post-entry observations."
        ),
    }


def event_key(event: Mapping[str, Any]) -> str:
    return str(event.get("title") or "").casefold().strip()


def dedupe_events(events: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_title: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for event in events:
        key = event_key(event)
        if not key:
            continue
        item = dict(event)
        if key not in by_title:
            order.append(key)
            by_title[key] = item
            continue
        prior = by_title[key]
        prior_url = str(prior.get("source_url") or "")
        item_url = str(item.get("source_url") or "")
        if not prior_url and item_url:
            by_title[key] = item
            continue
        if not isinstance(prior.get("company_specific"), bool) and isinstance(item.get("company_specific"), bool):
            by_title[key] = item
    return [by_title[key] for key in order]


def event_net(event: Mapping[str, Any]) -> float | None:
    positive = safe_float(event.get("positive_probability"))
    negative = safe_float(event.get("negative_probability"))
    if positive is None or negative is None:
        return None
    return positive - negative


def event_magnitude(event: Mapping[str, Any]) -> float:
    value = event_net(event)
    return abs(value) if value is not None else 0.0


def summarize_event_group(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    values = [value for event in events if (value := event_net(event)) is not None]
    leading = max(events, key=lambda event: (event_magnitude(event), str(event.get("publication_time") or "")), default=None)
    average = mean(values) if values else None
    return {
        "count": len(events),
        "average_sentiment_net": average,
        "sentiment_direction": pct_label(average),
        "high_magnitude_count": sum(bool(event.get("high_magnitude")) for event in events),
        "leading_event": dict(leading) if leading else None,
        "events": [dict(event) for event in events[:8]],
    }


def trade_event_context(
    *,
    ticker: str,
    signal_at: datetime,
    hold_end: datetime,
    ticker_events: Sequence[Mapping[str, Any]],
    market_events: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    pre_start = signal_at - timedelta(hours=24)
    ticker_unique = dedupe_events(ticker_events)
    company = [event for event in ticker_unique if event_is_company_specific(event, ticker)]
    cross_tagged = [event for event in ticker_unique if not event_is_company_specific(event, ticker)]

    def between(event: Mapping[str, Any], start: datetime, end: datetime, *, include_end: bool = True) -> bool:
        published = parse_time(event.get("publication_time"))
        if published is None:
            return False
        return start <= published <= end if include_end else start <= published < end

    pre_company = [event for event in company if between(event, pre_start, signal_at)]
    during_company = [event for event in company if between(event, signal_at, hold_end, include_end=True)]
    market_unique = dedupe_events([*market_events, *cross_tagged])
    pre_market = [event for event in market_unique if between(event, pre_start, signal_at)]
    during_market = [event for event in market_unique if between(event, signal_at, hold_end, include_end=True)]
    return {
        "pre_entry_window": {
            "start": pre_start.isoformat(),
            "end": signal_at.isoformat(),
            "company_specific": summarize_event_group(pre_company),
            "market_or_cross_tagged": summarize_event_group(pre_market),
        },
        "during_holding_window": {
            "start": signal_at.isoformat(),
            "end": hold_end.isoformat(),
            "company_specific": summarize_event_group(during_company),
            "market_or_cross_tagged": summarize_event_group(during_market),
        },
        "data_limit": "Headline metadata only for most events; attribution is contextual and not proof of causality.",
    }


def prepared_independent_records() -> dict[str, list[dict[str, Any]]]:
    payload = read_json(INDEPENDENT_REPORT)
    sources = payload.get("sources") if isinstance(payload.get("sources"), dict) else {}
    output: dict[str, list[dict[str, Any]]] = {}
    for source in ("flash", "flash_agentic"):
        section = sources.get(source) if isinstance(sources.get(source), dict) else {}
        episodes = section.get("episodes") if isinstance(section.get("episodes"), dict) else {}
        records = episodes.get("records") if isinstance(episodes.get("records"), list) else []
        output[source] = [dict(row) for row in records if isinstance(row, dict)]
    return output


def spy_context(
    *,
    signal_at: datetime,
    signal_session: str,
    mark_session: str,
    minute_bars: Mapping[tuple[str, str], Mapping[str, Any]],
    daily_bars: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    series = minute_bars.get((signal_session, "SPY")) or {}
    times = list(series.get("times") or [])
    rows = list(series.get("rows") or [])
    index = bisect_left(times, signal_at)
    entry_row = rows[index] if index < len(rows) else None
    entry_price = first_number(entry_row or {}, "vw", "c", "o")
    mark_row = next(
        (row for row in daily_bars.get("SPY", []) if str(row.get("session") or "") == mark_session),
        None,
    )
    mark_price = first_number(mark_row or {}, "c", "vw")
    return_pct = (
        (mark_price / entry_price - 1.0) * 100.0
        if entry_price not in {None, 0} and mark_price is not None
        else None
    )
    return {
        "entry_at": entry_row.get("timestamp").isoformat() if entry_row and entry_row.get("timestamp") is not None else None,
        "entry_price": entry_price,
        "mark_session": mark_session,
        "mark_price": mark_price,
        "return_pct": return_pct,
        "basis": "SPY first one-minute bar at or after Cluster signal to SPY mark-session daily close",
    }


def fill_quality(trade: Mapping[str, Any]) -> dict[str, Any]:
    debit = safe_float(trade.get("modeled_entry_debit"))
    long_time = parse_time(trade.get("long_entry_time_et"))
    short_time = parse_time(trade.get("short_entry_time_et"))
    if debit is None:
        label = "spread_fill_unavailable"
        gap = None
    elif long_time is None or short_time is None:
        label = "one_leg_time_unavailable"
        gap = None
    else:
        gap = abs((long_time - short_time).total_seconds()) / 60.0
        if gap <= 1.0:
            label = "near_simultaneous_modeled_legs"
        elif gap <= 5.0:
            label = "moderately_asynchronous_modeled_legs"
        else:
            label = "asynchronous_modeled_legs"
    return {
        "label": label,
        "leg_entry_time_gap_minutes": gap,
        "modeled_entry_debit": debit,
        "historical_bid_ask_available": False,
        "simultaneous_fill_proven": False,
    }


def outcome_class(trade: Mapping[str, Any]) -> str:
    underlying = safe_float(trade.get("underlying_directional_return_pct"))
    spread = safe_float(trade.get("debit_spread_return_pct"))
    target_hit = bool(trade.get("target_hit"))
    mfe = safe_float(trade.get("maximum_favorable_move_pct"))
    if target_hit and spread is not None and spread > 0 and underlying is not None and underlying > 0:
        return "clean_followthrough"
    if target_hit and (spread is None or spread <= 0):
        return "target_hit_but_spread_gave_back_or_unfilled"
    if underlying is not None and underlying > 0 and spread is not None and spread <= 0:
        return "underlying_up_but_spread_failed"
    if underlying is not None and underlying <= 0 and mfe is not None and mfe >= 2.0:
        return "early_favorable_move_then_reversal"
    if underlying is not None and underlying > 0:
        return "partial_underlying_followthrough"
    return "failed_directional_followthrough"


def attribution_class(
    *,
    underlying_return: float | None,
    excess_vs_spy: float | None,
    event_context: Mapping[str, Any],
) -> tuple[str, str]:
    realized = pct_label(underlying_return)
    during = nested(event_context, "during_holding_window", "company_specific") or {}
    pre = nested(event_context, "pre_entry_window", "company_specific") or {}
    during_count = int(during.get("count") or 0)
    pre_count = int(pre.get("count") or 0)
    during_sentiment = str(during.get("sentiment_direction") or "unavailable")
    pre_sentiment = str(pre.get("sentiment_direction") or "unavailable")
    excess = excess_vs_spy

    if realized == "positive" and during_count and during_sentiment == "positive":
        return "company_positive_catalyst_supported", "moderate_context_not_causal"
    if realized == "negative" and during_count and during_sentiment == "negative":
        return "company_negative_catalyst_supported", "moderate_context_not_causal"
    if realized == "positive" and during_count and during_sentiment == "negative":
        return "rose_despite_negative_company_context", "low"
    if realized == "negative" and during_count and during_sentiment == "positive":
        return "fell_despite_positive_company_context", "low"
    if pre_count and pre_sentiment == "negative" and realized == "positive":
        return "bullish_trade_overcame_negative_pre_entry_context", "low"
    if pre_count and pre_sentiment == "positive" and realized == "negative":
        return "bullish_trade_failed_despite_positive_pre_entry_context", "low"
    if not during_count and not pre_count:
        if excess is not None and abs(excess) <= 1.0:
            return "market_beta_dominated_or_unresolved", "low"
        if excess is not None and excess > 1.0:
            return "idiosyncratic_strength_without_identified_company_catalyst", "low"
        if excess is not None and excess < -1.0:
            return "idiosyncratic_weakness_without_identified_company_catalyst", "low"
        return "unresolved_no_company_specific_headline", "low"
    return "mixed_company_and_market_context", "low"


def uncertainty(
    *,
    trade: Mapping[str, Any],
    flash: Mapping[str, Any],
    agentic: Mapping[str, Any],
    event_context: Mapping[str, Any],
    fill: Mapping[str, Any],
) -> dict[str, Any]:
    reasons: list[str] = ["headline_metadata_does_not_prove_causality"]
    pre_company = int(nested(event_context, "pre_entry_window", "company_specific", "count") or 0)
    during_company = int(nested(event_context, "during_holding_window", "company_specific", "count") or 0)
    contemporaneous_sources = sum(
        bool(nested(source, "available_at_cluster_signal", "available"))
        for source in (flash, agentic)
    )
    if pre_company + during_company == 0:
        reasons.append("no_company_specific_headline_in_observed_window")
    if contemporaneous_sources == 0:
        reasons.append("no_flash_or_agentic_state_available_at_cluster_signal")
    if str(trade.get("status") or "").startswith("pending"):
        reasons.append("expiry_outcome_not_final")
    if fill.get("label") != "near_simultaneous_modeled_legs":
        reasons.append(str(fill.get("label") or "spread_fill_uncertain"))
    if safe_float(trade.get("debit_spread_return_pct")) is None:
        reasons.append("spread_return_unavailable")
    evidence_classes = sum(
        (
            pre_company + during_company > 0,
            contemporaneous_sources > 0,
            fill.get("label") == "near_simultaneous_modeled_legs",
            str(trade.get("status") or "") == "matured_at_expiry",
        )
    )
    if evidence_classes >= 3:
        level = "reduced_but_not_removed"
    elif evidence_classes >= 1:
        level = "medium"
    else:
        level = "high"
    return {"level": level, "reasons": reasons, "evidence_classes_available": evidence_classes}


def row_postmortem(
    trade: Mapping[str, Any],
    *,
    independent: Mapping[str, Sequence[Mapping[str, Any]]],
    news_by_ticker: Mapping[str, Sequence[Mapping[str, Any]]],
    spy_minute: Mapping[tuple[str, str], Mapping[str, Any]],
    spy_daily: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    ticker = str(trade.get("ticker") or "").upper()
    session = str(trade.get("market_session") or "")
    signal_at = parse_time(trade.get("signal_time_et"))
    if signal_at is None:
        raise RuntimeError(f"trade {trade.get('trade_no')} missing signal time")
    mark_session = str(trade.get("mark_session") or session)
    hold_end = datetime.combine(date.fromisoformat(mark_session), dt_time(16, 0), tzinfo=NY).astimezone(UTC)
    flash = source_timeline_context(
        independent.get("flash", []), ticker=ticker, session=session, signal_at=signal_at
    )
    agentic = source_timeline_context(
        independent.get("flash_agentic", []), ticker=ticker, session=session, signal_at=signal_at
    )
    market_events = [
        *news_by_ticker.get("SPY", []),
        *news_by_ticker.get("QQQ", []),
    ]
    events = trade_event_context(
        ticker=ticker,
        signal_at=signal_at,
        hold_end=hold_end,
        ticker_events=news_by_ticker.get(ticker, []),
        market_events=market_events,
    )
    spy = spy_context(
        signal_at=signal_at,
        signal_session=session,
        mark_session=mark_session,
        minute_bars=spy_minute,
        daily_bars=spy_daily,
    )
    underlying = safe_float(trade.get("underlying_directional_return_pct"))
    spy_return = safe_float(spy.get("return_pct"))
    excess = underlying - spy_return if underlying is not None and spy_return is not None else None
    attribution, causal_confidence = attribution_class(
        underlying_return=underlying,
        excess_vs_spy=excess,
        event_context=events,
    )
    fill = fill_quality(trade)
    uncertainty_result = uncertainty(
        trade=trade,
        flash=flash,
        agentic=agentic,
        event_context=events,
        fill=fill,
    )
    result = {
        **dict(trade),
        "cluster_direction": "BULLISH",
        "flash_context": flash,
        "agentic_context": agentic,
        "event_context": events,
        "spy_benchmark": spy,
        "excess_return_vs_spy_pct": excess,
        "realized_move_direction": pct_label(underlying),
        "outcome_class": outcome_class(trade),
        "attribution_class": attribution,
        "causal_confidence": causal_confidence,
        "uncertainty": uncertainty_result,
        "fill_quality": fill,
        "automatic_promotion": False,
        "execution_authority": False,
    }
    result["postmortem_id"] = stable_id(
        "cipher_cluster_trade_postmortem",
        {
            "signal_id": trade.get("signal_id"),
            "status": trade.get("status"),
            "mark_session": mark_session,
            "attribution_class": attribution,
            "company_event_titles": [
                nested(events, "pre_entry_window", "company_specific", "leading_event", "title"),
                nested(events, "during_holding_window", "company_specific", "leading_event", "title"),
            ],
        },
        length=64,
    )
    return result


def metric(values: Iterable[Any]) -> dict[str, Any]:
    numbers = [value for raw in values if (value := safe_float(raw)) is not None]
    return {
        "available": len(numbers),
        "average": mean(numbers) if numbers else None,
        "median": median(numbers) if numbers else None,
        "positive_fraction": sum(value > 0 for value in numbers) / len(numbers) if numbers else None,
    }


def grouped_summary(rows: Sequence[Mapping[str, Any]], key: str) -> list[dict[str, Any]]:
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        value: Any = row
        for part in key.split("."):
            value = value.get(part) if isinstance(value, Mapping) else None
        groups[str(value or "unavailable")].append(row)
    output: list[dict[str, Any]] = []
    for label, group in sorted(groups.items()):
        output.append(
            {
                "group": label,
                "trades": len(group),
                "matured": sum(str(row.get("status")) == "matured_at_expiry" for row in group),
                "target_hit_fraction": sum(bool(row.get("target_hit")) for row in group) / len(group),
                "underlying_return_pct": metric(row.get("underlying_directional_return_pct") for row in group),
                "spread_return_pct": metric(row.get("debit_spread_return_pct") for row in group),
            }
        )
    return output


def summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "trades": len(rows),
        "unique_tickers": len({str(row.get("ticker")) for row in rows}),
        "sessions": sorted({str(row.get("market_session")) for row in rows}),
        "matured_at_expiry": sum(str(row.get("status")) == "matured_at_expiry" for row in rows),
        "pending_expiry": sum(str(row.get("status")).startswith("pending") for row in rows),
        "target_hit_fraction": sum(bool(row.get("target_hit")) for row in rows) / len(rows) if rows else None,
        "underlying_return_pct": metric(row.get("underlying_directional_return_pct") for row in rows),
        "spread_return_pct": metric(row.get("debit_spread_return_pct") for row in rows),
        "attribution_counts": dict(Counter(str(row.get("attribution_class")) for row in rows)),
        "outcome_counts": dict(Counter(str(row.get("outcome_class")) for row in rows)),
        "uncertainty_counts": dict(Counter(str(nested(row, "uncertainty", "level")) for row in rows)),
        "flash_available_at_signal": sum(bool(nested(row, "flash_context", "available_at_cluster_signal", "available")) for row in rows),
        "agentic_available_at_signal": sum(bool(nested(row, "agentic_context", "available_at_cluster_signal", "available")) for row in rows),
        "company_headline_pre_entry": sum(int(nested(row, "event_context", "pre_entry_window", "company_specific", "count") or 0) > 0 for row in rows),
        "company_headline_during_hold": sum(int(nested(row, "event_context", "during_holding_window", "company_specific", "count") or 0) > 0 for row in rows),
        "by_attribution": grouped_summary(rows, "attribution_class"),
        "by_outcome": grouped_summary(rows, "outcome_class"),
        "by_flash_at_signal": grouped_summary(rows, "flash_context.available_at_cluster_signal.relationship_to_cluster"),
        "by_agentic_at_signal": grouped_summary(rows, "agentic_context.available_at_cluster_signal.relationship_to_cluster"),
        "by_pre_entry_company_sentiment": grouped_summary(rows, "event_context.pre_entry_window.company_specific.sentiment_direction"),
        "by_holding_company_sentiment": grouped_summary(rows, "event_context.during_holding_window.company_specific.sentiment_direction"),
    }


def source_text(event: Mapping[str, Any] | None) -> str | None:
    if not event:
        return None
    parts = [
        str(event.get("title") or "").strip(),
        str(event.get("publisher") or "").strip(),
        str(event.get("source_url") or "").strip(),
    ]
    return " | ".join(part for part in parts if part) or None


def csv_row(row: Mapping[str, Any]) -> dict[str, Any]:
    pre_lead = nested(row, "event_context", "pre_entry_window", "company_specific", "leading_event")
    hold_lead = nested(row, "event_context", "during_holding_window", "company_specific", "leading_event")
    market_lead = nested(row, "event_context", "during_holding_window", "market_or_cross_tagged", "leading_event")
    return {
        "Trade #": row.get("trade_no"),
        "Session": row.get("market_session"),
        "Ticker": row.get("ticker"),
        "Signal Time (ET)": row.get("signal_time_et"),
        "Cluster Expiry": row.get("cluster_expiration"),
        "Status": row.get("status"),
        "Research Priority": row.get("research_priority"),
        "Underlying Return (%)": row.get("underlying_directional_return_pct"),
        "SPY Benchmark Return (%)": nested(row, "spy_benchmark", "return_pct"),
        "Excess vs SPY (%)": row.get("excess_return_vs_spy_pct"),
        "Target Hit": row.get("target_hit"),
        "MFE (%)": row.get("maximum_favorable_move_pct"),
        "MAE (%)": row.get("maximum_adverse_move_pct"),
        "Debit Spread Return (%)": row.get("debit_spread_return_pct"),
        "Outcome Class": row.get("outcome_class"),
        "Attribution Class": row.get("attribution_class"),
        "Causal Confidence": row.get("causal_confidence"),
        "Uncertainty": nested(row, "uncertainty", "level"),
        "Uncertainty Reasons": " | ".join(nested(row, "uncertainty", "reasons") or []),
        "Flash Available at Signal": nested(row, "flash_context", "available_at_cluster_signal", "available"),
        "Flash Direction at Signal": nested(row, "flash_context", "available_at_cluster_signal", "direction"),
        "Flash Relation at Signal": nested(row, "flash_context", "available_at_cluster_signal", "relationship_to_cluster"),
        "Flash Score at Signal": nested(row, "flash_context", "available_at_cluster_signal", "score"),
        "Flash First Post-Signal Relation": nested(row, "flash_context", "first_post_cluster_signal", "relationship_to_cluster"),
        "Flash First Post-Signal Lag (min)": nested(row, "flash_context", "first_post_cluster_signal", "lag_minutes_from_cluster"),
        "Agentic Available at Signal": nested(row, "agentic_context", "available_at_cluster_signal", "available"),
        "Agentic Direction at Signal": nested(row, "agentic_context", "available_at_cluster_signal", "direction"),
        "Agentic Relation at Signal": nested(row, "agentic_context", "available_at_cluster_signal", "relationship_to_cluster"),
        "Agentic Score at Signal": nested(row, "agentic_context", "available_at_cluster_signal", "score"),
        "Agentic First Post-Signal Relation": nested(row, "agentic_context", "first_post_cluster_signal", "relationship_to_cluster"),
        "Agentic First Post-Signal Lag (min)": nested(row, "agentic_context", "first_post_cluster_signal", "lag_minutes_from_cluster"),
        "Pre-Entry Company Headlines": nested(row, "event_context", "pre_entry_window", "company_specific", "count"),
        "Pre-Entry Company Sentiment": nested(row, "event_context", "pre_entry_window", "company_specific", "sentiment_direction"),
        "Pre-Entry Leading Source": source_text(pre_lead),
        "Holding Company Headlines": nested(row, "event_context", "during_holding_window", "company_specific", "count"),
        "Holding Company Sentiment": nested(row, "event_context", "during_holding_window", "company_specific", "sentiment_direction"),
        "Holding Leading Source": source_text(hold_lead),
        "Market Context Source": source_text(market_lead),
        "Fill Quality": nested(row, "fill_quality", "label"),
        "Leg Time Gap (min)": nested(row, "fill_quality", "leg_entry_time_gap_minutes"),
        "Long Contract": row.get("long_atm_symbol"),
        "Short Contract": row.get("short_target_symbol"),
        "Modeled Entry Debit ($)": row.get("modeled_entry_debit"),
        "Postmortem ID": row.get("postmortem_id"),
    }


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    values = [csv_row(row) for row in rows]
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(values[0]) if values else [])
        if values:
            writer.writeheader()
            writer.writerows(values)
    temporary.replace(path)


def load_spy_bars(trades: Sequence[Mapping[str, Any]], *, force: bool, workers: int) -> tuple[dict, dict]:
    sessions = sorted({str(row.get("market_session")) for row in trades if row.get("market_session")})
    requirements = {session: {"SPY"} for session in sessions}
    headers = provider_headers()
    minute_raw = load_or_fetch_partitioned_minute_bars(
        requirements,
        root=STOCK_MINUTE_CACHE,
        url=STOCK_BARS_URL,
        headers=headers,
        stock=True,
        workers=max(1, min(workers, 4)),
        force=force,
    )
    first = min(date.fromisoformat(session) for session in sessions)
    last_mark = max(
        date.fromisoformat(str(row.get("mark_session") or row.get("market_session")))
        for row in trades
    )
    daily_raw = load_or_fetch_daily_bars(
        ["SPY"],
        root=STOCK_DAILY_CACHE,
        url=STOCK_BARS_URL,
        headers=headers,
        stock=True,
        start=datetime.combine(first, dt_time(0, 0), tzinfo=UTC),
        end=datetime.combine(last_mark + timedelta(days=1), dt_time(0, 0), tzinfo=UTC),
        workers=1,
        force=force,
    )
    return prepare_minute_bars(minute_raw), prepare_daily_bars(daily_raw)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force-provider-refresh", action="store_true")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    created_at = datetime.now(UTC)
    trades = exported_trade_rows()
    independent = prepared_independent_records()
    news_by_ticker, news_status = load_news_events(through=created_at, lookback_days=30)
    spy_minute, spy_daily = load_spy_bars(
        trades,
        force=args.force_provider_refresh,
        workers=args.workers,
    )
    postmortems = [
        row_postmortem(
            trade,
            independent=independent,
            news_by_ticker=news_by_ticker,
            spy_minute=spy_minute,
            spy_daily=spy_daily,
        )
        for trade in trades
    ]
    payload = {
        "schema_version": 1,
        "created_at": created_at.isoformat(),
        "status": "completed",
        "mode": "every_modeled_tier_a_cluster_trade_causal_context_postmortem",
        "source_trade_export": str(ROOT / "scripts" / "export_tier_a_trade_log.py"),
        "source_cluster_report": str(ROOT / "data" / "governance" / "cipher_signal_only" / "latest_cluster_individual_analysis.json"),
        "source_independent_report": str(INDEPENDENT_REPORT),
        "source_news_registry": str(ROOT / "data" / "governance" / "research_registry.sqlite"),
        "news_registry_status": news_status,
        "summary": summary(postmortems),
        "records": postmortems,
        "methodology": {
            "entry_source_boundary": "Flash/Agentic latest state at or before Cluster signal only",
            "post_entry_source_boundary": "First later same-day Flash/Agentic state is reported separately",
            "company_event_window_pre_entry": "24 hours before Cluster signal through signal time",
            "company_event_window_holding": "after Cluster signal through mark-session 4:00 PM ET",
            "market_benchmark": "SPY signal-minute to mark-session daily close",
            "news_role": "retrospective explanatory context and risk flag only",
            "news_availability_boundary": "Publication time is historical context; this run does not prove Cipher received the headline at the original signal time.",
            "causality_claimed": False,
            "historical_bid_ask_available": False,
            "simultaneous_option_fill_proven": False,
        },
        "automatic_promotion": False,
        "paper_or_live_execution": False,
        "execution_authority": False,
    }
    payload["report_id"] = stable_id(
        "cipher_every_trade_postmortem_report",
        {
            "created_at": payload["created_at"],
            "postmortem_ids": [row.get("postmortem_id") for row in postmortems],
        },
        length=64,
    )
    archive = ARCHIVE_ROOT / f"{payload['report_id']}.json"
    atomic_json(archive, payload)
    atomic_json(OUTPUT, payload)
    write_csv(CSV_OUTPUT, postmortems)
    print(
        json.dumps(
            {
                "status": "completed",
                "report_id": payload["report_id"],
                "json": str(OUTPUT),
                "csv": str(CSV_OUTPUT),
                "trades": len(postmortems),
                "unique_tickers": payload["summary"]["unique_tickers"],
                "matured": payload["summary"]["matured_at_expiry"],
                "pending": payload["summary"]["pending_expiry"],
                "attribution_counts": payload["summary"]["attribution_counts"],
                "execution_authority": False,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
