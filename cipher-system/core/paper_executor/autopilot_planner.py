"""Point-in-time planning for Cipher's autonomous *paper* workflow.

Premarket observations create a ranked watch plan. By default they never create
positions: a separate regular-session pass must observe fresh evidence in the
same direction before submission to the simulated executor. An explicit opt-in
premarket-entry mode (``premarket_entry_allowed``) may submit the ranked plan
candidates from the fresh premarket setup alone; every candidate still re-passes
the strict evidence contract and the executor re-applies its own gates. Model
features are advisory until a registered walk-forward experiment promotes them.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
from datetime import datetime, time, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from core import ai_synthesizer
from core.evidence_contract import SignalRecord
from core.exchange_calendar import is_session

# Trained model inference (lazy-loaded on first use)
_ALPHA_MODEL = None
_ALPHA_MODEL_PATH = Path(__file__).resolve().parents[2] / "data" / "models" / "cipher_option_alpha_v1.pkl"


def _get_alpha_model():
    """Lazy-load the trained Options Alpha model for candidate scoring."""
    global _ALPHA_MODEL
    if _ALPHA_MODEL is not None:
        return _ALPHA_MODEL
    if not _ALPHA_MODEL_PATH.is_file():
        return None
    try:
        from core.models.option_alpha_model import OptionAlphaModel
        _ALPHA_MODEL = OptionAlphaModel.load(_ALPHA_MODEL_PATH)
        return _ALPHA_MODEL
    except Exception:
        return None


NEW_YORK = ZoneInfo("America/New_York")
SCHEMA_VERSION = 1
DEFAULT_REGISTRY = Path(__file__).resolve().parents[2] / "data" / "governance" / "research_registry.sqlite"


class AutopilotPhase(str, Enum):
    CLOSED = "closed"
    PREMARKET_DISCOVERY = "premarket_discovery"
    OPENING_WAIT = "opening_wait"
    ENTRY_CONFIRMATION = "entry_confirmation"
    MONITOR_ONLY = "monitor_only"
    FORCE_CLOSE = "force_close"


def phase_at(now: datetime) -> AutopilotPhase:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    local = now.astimezone(NEW_YORK)
    if not is_session(local.date()):
        return AutopilotPhase.CLOSED
    clock = local.time().replace(tzinfo=None)
    if time(4, 0) <= clock < time(9, 30):
        return AutopilotPhase.PREMARKET_DISCOVERY
    if time(9, 30) <= clock < time(9, 35):
        return AutopilotPhase.OPENING_WAIT
    if time(9, 35) <= clock < time(11, 30):
        return AutopilotPhase.ENTRY_CONFIRMATION
    if time(11, 30) <= clock < time(15, 45):
        return AutopilotPhase.MONITOR_ONLY
    if time(15, 45) <= clock < time(16, 5):
        return AutopilotPhase.FORCE_CLOSE
    return AutopilotPhase.CLOSED


def _parse_stamp(value: Any) -> datetime | None:
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if stamp.tzinfo is None:
        return None
    return stamp.astimezone(timezone.utc)


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _stable_id(prefix: str, payload: Any) -> str:
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    return f"{prefix}_{digest[:24]}"


def sentiment_context(
    tickers: Iterable[str],
    *,
    as_of: datetime,
    registry: Path = DEFAULT_REGISTRY,
    maximum_age_hours: float = 36.0,
) -> dict[str, dict[str, Any]]:
    """Read only news that was available by ``as_of``; never backfill future text."""
    symbols = sorted({str(ticker).upper() for ticker in tickers if ticker})
    result = {
        ticker: {
            "status": "unavailable", "score": None, "events": 0,
            "newest_available_at": None, "model_ids": [],
            "directional_authority": False,
        }
        for ticker in symbols
    }
    if not symbols or not registry.is_file():
        return result
    try:
        with sqlite3.connect(registry) as db:
            rows = db.execute(
                "select available_at, payload_json from news_events where available_at <= ? order by available_at desc",
                (as_of.astimezone(timezone.utc).isoformat(),),
            ).fetchall()
    except (sqlite3.Error, OSError):
        return result

    weighted: dict[str, list[tuple[float, float, str, str]]] = {ticker: [] for ticker in symbols}
    for available_at, raw in rows:
        try:
            payload = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            continue
        event_symbols = {str(value).upper() for value in payload.get("symbols") or []}
        matched = event_symbols.intersection(weighted)
        if not matched:
            continue
        available = _parse_stamp(available_at)
        positive = _finite(payload.get("positive_probability"))
        negative = _finite(payload.get("negative_probability"))
        if available is None or positive is None or negative is None:
            continue
        age_hours = max(0.0, (as_of.astimezone(timezone.utc) - available).total_seconds() / 3600.0)
        # Half-life weighting is descriptive context only. It is deliberately not
        # added to the setup rank until a leakage-safe experiment validates it.
        weight = 0.5 ** (age_hours / 12.0)
        model_id = str(payload.get("sentiment_model_id") or "unknown")
        for ticker in matched:
            weighted[ticker].append((positive - negative, weight, available.isoformat(), model_id))

    for ticker, values in weighted.items():
        if not values:
            continue
        newest = max(value[2] for value in values)
        newest_stamp = _parse_stamp(newest)
        newest_age = (
            (as_of.astimezone(timezone.utc) - newest_stamp).total_seconds() / 3600.0
            if newest_stamp else math.inf
        )
        denominator = sum(value[1] for value in values)
        score = sum(value[0] * value[1] for value in values) / denominator if denominator else None
        result[ticker] = {
            "status": "current" if newest_age <= maximum_age_hours else "stale",
            "score": round(score, 6) if score is not None else None,
            "events": len(values),
            "newest_available_at": newest,
            "age_hours": round(newest_age, 3),
            "model_ids": sorted({value[3] for value in values}),
            "directional_authority": False,
            "caveat": "FinBERT headline context is advisory and cannot authorize an entry.",
        }
    return result


def build_premarket_plan(
    scan: dict[str, Any],
    *,
    now: datetime,
    sentiment: dict[str, dict[str, Any]] | None = None,
    maximum_candidates: int = 8,
    minimum_score: float = 60.0,
    minimum_reward_risk: float = 1.5,
    premarket_entry_allowed: bool = False,
) -> dict[str, Any]:
    """Select evidence-qualified names while retaining every rejection reason."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    sentiment = sentiment or {}
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for raw in scan.get("top") or []:
        ticker = str(raw.get("ticker") or "").upper()
        evidence = raw.get("evidence_snapshot") or {}
        coverage = evidence.get("coverage") or {}
        reasons: list[str] = []
        score = _finite(raw.get("score"))
        reward_risk = _finite(raw.get("reward_risk"))
        if raw.get("rank_eligible") is not True:
            reasons.append("rank_ineligible")
        if raw.get("geometry_valid") is not True or raw.get("actionable") is not True:
            reasons.append("invalid_or_nonactionable_geometry")
        if coverage.get("status") != "sufficient":
            reasons.append("options_coverage_not_sufficient")
        if evidence.get("feed") != "opra":
            reasons.append("options_feed_not_opra")
        if evidence.get("freshness", {}).get("status") != "current":
            reasons.append("evidence_not_current")
        if evidence.get("session", {}).get("phase") != "premarket":
            reasons.append("not_premarket_evidence")
        if not evidence.get("snapshot_id"):
            reasons.append("evidence_id_missing")
        if score is None or score < minimum_score:
            reasons.append("score_below_plan_floor")
        if reward_risk is None or reward_risk < minimum_reward_risk:
            reasons.append("reward_risk_below_plan_floor")
        if raw.get("direction") not in {"BULLISH", "BEARISH"}:
            reasons.append("direction_unknown")
        if reasons:
            rejected.append({"ticker": ticker or "UNKNOWN", "reasons": sorted(set(reasons))})
            continue
        accepted.append({
            "candidate_id": _stable_id("candidate", {
                "ticker": ticker,
                "direction": raw["direction"],
                "snapshot_id": evidence["snapshot_id"],
            }),
            "ticker": ticker,
            "direction": raw["direction"],
            "setup_type": raw.get("setup_type"),
            "score": score,
            "spot": _finite(raw.get("spot")),
            "target": _finite(raw.get("target")),
            "invalidation": _finite(raw.get("invalidation")),
            "reward_risk": reward_risk,
            "evidence_snapshot_id": evidence["snapshot_id"],
            "evidence_contract": raw.get("evidence_contract"),
            "evidence_event_at": evidence.get("event_at"),
            "sentiment": sentiment.get(ticker, {
                "status": "unavailable", "score": None, "events": 0,
                "directional_authority": False,
            }),
            "state": "WATCHING_FOR_RTH_CONFIRMATION",
        })
    accepted.sort(key=lambda row: (-float(row["score"]), -float(row["reward_risk"]), row["ticker"]))
    accepted = accepted[: max(1, min(maximum_candidates, 20))]
    try:
        accepted = ai_synthesizer.evaluate_autopilot_candidates(accepted, market_context=scan)
    except Exception:
        pass
    # Score candidates with trained Options Alpha model (advisory only)
    alpha_model = _get_alpha_model()
    if alpha_model is not None:
        try:
            import pandas as pd
            for candidate in accepted:
                features = {col: 0.0 for col in alpha_model.feature_names}
                # Map candidate fields to model features where available
                if candidate.get("score") is not None:
                    features["moneyness_pct"] = 0.0  # No strike data in scan candidates
                if candidate.get("direction") == "BULLISH":
                    features["actual_side_long"] = 1.0
                elif candidate.get("direction") == "BEARISH":
                    features["actual_side_short"] = 1.0
                features_df = pd.DataFrame([features])
                prob = float(alpha_model.predict_edge_probability(features_df)[0])
                candidate["ml_edge_probability"] = round(prob, 4)
                candidate["ml_model_id"] = alpha_model.model_id
                candidate["ml_model_version"] = alpha_model.version
        except Exception:
            pass
    identity = {
        "schema_version": SCHEMA_VERSION,
        "market_date": now.astimezone(NEW_YORK).date().isoformat(),
        "scan_as_of": scan.get("as_of"),
        "candidates": [row["candidate_id"] for row in accepted],
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "plan_id": _stable_id("premarket_plan", identity),
        "created_at": now.astimezone(timezone.utc).isoformat(),
        "market_date": identity["market_date"],
        "phase": AutopilotPhase.PREMARKET_DISCOVERY.value,
        "state": "WATCHLIST_ONLY",
        "entry_policy": {
            "earliest_entry_et": "04:00" if premarket_entry_allowed else "09:35",
            "latest_entry_et": "11:30",
            "required_confirmation": (
                "premarket Cipher setup with fresh OPRA evidence (premarket-entry mode "
                "enabled); regular-session Cipher confirmation may still merge"
                if premarket_entry_allowed
                else (
                    "fresh RTH Cipher confirmation scan in the same direction; "
                    "secondary scanner cards may merge but never replace the primary Cipher signal"
                )
            ),
            "premarket_entry_allowed": bool(premarket_entry_allowed),
        },
        "candidates": accepted,
        "rejected": rejected,
        "source_scan": {
            "as_of": scan.get("as_of"),
            "strategy": scan.get("strategy"),
            "scanned": scan.get("scanned"),
            "qualified": scan.get("qualified"),
            "evidence_snapshot_ids": scan.get("evidence_snapshot_ids") or [],
        },
        "model_policy": {
            "finbert": "advisory_only",
            "openrouter_model": os.environ.get("CIPHER_AI_MODEL", "openai/gpt-4o-mini"),
            "ai_multi_factor": "active_advisory",
            "fingpt": "not_enabled",
            "custom_model": "not_trained",
            "model_may_authorize_entry": False,
        },
        "read_only": True,
        "paper_execution_capability": True,
        "live_execution_capability": False,
    }


def confirmation_payload(
    plan: dict[str, Any],
    scan: dict[str, Any] | list[dict[str, Any]] | None = None,
    *,
    now: datetime,
    scans: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return only cards that satisfy the registered RTH confirmation contract.

    ``scan`` may be a single scan payload (legacy callers) or a list of scans
    from several strategies. When multiple scans are supplied the first scan is
    the primary source (the regular-session Cipher scan); later scans may merge
    cards only for tickers the primary scan did not confirm, so the Flash-Agentic
    bottleneck is removed without letting secondary signals replace the primary.
    """
    if scans is not None:
        scan = scans
    if scan is None:
        raise ValueError("confirmation_payload requires at least one scan")
    multi = isinstance(scan, list)
    scan_list = list(scan) if multi else [scan]
    if not scan_list:
        raise ValueError("confirmation_payload requires at least one scan")
    if phase_at(now) != AutopilotPhase.ENTRY_CONFIRMATION:
        return _confirmation_result(
            plan, scan_list[0], now, [],
            [{"ticker": "*", "reasons": ["outside_entry_confirmation_window"]}],
        )
    planned = {row["ticker"]: row for row in plan.get("candidates") or []}
    primary_tickers: set[str] = set()
    confirmed_by_ticker: dict[str, dict[str, Any]] = {}
    rejected: list[dict[str, Any]] = []
    for index, scan_payload in enumerate(scan_list):
        strategy = str(scan_payload.get("strategy") or "").lower()
        strategy = strategy if strategy else ("cipher" if multi else "flash_agentic")
        for card in scan_payload.get("top") or []:
            ticker = str(card.get("ticker") or "").upper()
            candidate = planned.get(ticker)
            if not candidate:
                continue
            evidence = card.get("evidence_snapshot") or {}
            reasons: list[str] = []
            state = str(card.get("agent_state") or card.get("state") or "").lower()
            if card.get("direction") != candidate.get("direction"):
                reasons.append("direction_changed")
            # A triggered agent state is required only for secondary scanner
            # sources (Flash/Flash-Agentic) and for legacy single scans with no
            # explicit primary strategy. The primary Cipher scan is the
            # regular-session setup surface itself; it does not need a trigger
            # flag, and autopilot entries must not depend on Flash-Agentic state.
            # Legacy single-scan confirmation (no explicit strategy) keeps the
            # triggered-state gate; an explicit cipher scan is the primary
            # regular-session confirmation and never depends on a trigger flag.
            is_primary = (multi and index == 0) or (not multi and strategy == "cipher")
            if not is_primary and state != "triggered":
                reasons.append("setup_not_triggered")
            if card.get("rank_eligible") is not True or card.get("actionable") is not True:
                reasons.append("card_not_actionable")
            if card.get("geometry_valid") is not True:
                reasons.append("geometry_invalid")
            if evidence.get("freshness", {}).get("status") != "current":
                reasons.append("evidence_not_current")
            if evidence.get("session", {}).get("phase") != "regular":
                reasons.append("not_regular_session_evidence")
            if evidence.get("coverage", {}).get("status") != "sufficient":
                reasons.append("options_coverage_not_sufficient")
            if not evidence.get("snapshot_id"):
                reasons.append("confirmation_evidence_id_missing")
            if reasons:
                rejected.append({"ticker": ticker, "reasons": sorted(set(reasons))})
                continue
            # Primary scan always wins; secondary scans merge only unseen tickers.
            if index > 0 and ticker in confirmed_by_ticker:
                continue
            confirmed = {
                **card,
                "confirmation_strategy": strategy,
                "autopilot": {
                    "plan_id": plan.get("plan_id"),
                    "candidate_id": candidate.get("candidate_id"),
                    "premarket_evidence_snapshot_id": candidate.get("evidence_snapshot_id"),
                    "confirmation_evidence_snapshot_id": evidence.get("snapshot_id"),
                    "sentiment": candidate.get("sentiment"),
                    "sentiment_directional_authority": False,
                    "paper_only": True,
                },
            }
            try:
                confirmed["signal_record"] = SignalRecord.from_mapping({
                    **confirmed,
                    "strategy": strategy,
                    "signal_at": evidence.get("event_at"),
                    "available_at": evidence.get("captured_at") or evidence.get("event_at"),
                    "evidence_snapshot_ids": [candidate.get("evidence_snapshot_id"), evidence.get("snapshot_id")],
                    "decision": "accepted",
                    "metadata": {"plan_id": plan.get("plan_id")},
                }).to_dict()
            except (TypeError, ValueError):
                confirmed["signal_record_error"] = "incomplete_signal_contract"
            confirmed_by_ticker[ticker] = confirmed
            if index == 0:
                primary_tickers.add(ticker)
    return _confirmation_result(
        plan, scan_list[0], now,
        [confirmed_by_ticker[ticker] for ticker in confirmed_by_ticker],
        rejected,
        confirmation_sources=[
            row.get("confirmation_strategy") for row in confirmed_by_ticker.values()
        ],
        scan_types=list(dict.fromkeys(str(scan_payload.get("strategy") or "cipher") for scan_payload in scan_list)),
    )


def premarket_payload(
    plan: dict[str, Any],
    scan: dict[str, Any],
    *,
    now: datetime,
) -> dict[str, Any]:
    """Return premarket plan candidates as executor cards without a regular-session scan.

    Opt-in premarket-entry mode: the autopilot may enter from the fresh premarket
    setup alone. It never admits a name that failed ``build_premarket_plan``; each
    candidate is re-verified against the same evidence gates (OPRA feed, current
    freshness, premarket session phase, sufficient coverage, actionable geometry,
    matching direction) before submission, and the payload is paper-only. The
    executor still applies its own setup allowlist, ticker allowlist, portfolio
    limits, contract selection, and synthetic fills on top of this payload.
    """
    planned = {row["ticker"]: row for row in plan.get("candidates") or []}
    confirmed_by_ticker: dict[str, dict[str, Any]] = {}
    rejected: list[dict[str, Any]] = []
    for raw in scan.get("top") or []:
        ticker = str(raw.get("ticker") or "").upper()
        candidate = planned.get(ticker)
        if not candidate:
            continue
        evidence = raw.get("evidence_snapshot") or {}
        coverage = evidence.get("coverage") or {}
        reasons: list[str] = []
        if raw.get("direction") != candidate.get("direction"):
            reasons.append("direction_changed")
        if raw.get("rank_eligible") is not True or raw.get("actionable") is not True:
            reasons.append("card_not_actionable")
        if raw.get("geometry_valid") is not True:
            reasons.append("geometry_invalid")
        if evidence.get("feed") != "opra":
            reasons.append("options_feed_not_opra")
        if evidence.get("freshness", {}).get("status") != "current":
            reasons.append("evidence_not_current")
        if evidence.get("session", {}).get("phase") != "premarket":
            reasons.append("not_premarket_evidence")
        if coverage.get("status") != "sufficient":
            reasons.append("options_coverage_not_sufficient")
        if not evidence.get("snapshot_id"):
            reasons.append("evidence_id_missing")
        if reasons:
            rejected.append({"ticker": ticker, "reasons": sorted(set(reasons))})
            continue
        confirmed = {
            **raw,
            "confirmation_strategy": "premarket_plan",
            "premarket_entry": True,
            "autopilot": {
                "plan_id": plan.get("plan_id"),
                "candidate_id": candidate.get("candidate_id"),
                "premarket_evidence_snapshot_id": evidence.get("snapshot_id"),
                "confirmation_evidence_snapshot_id": None,
                "sentiment": candidate.get("sentiment"),
                "sentiment_directional_authority": False,
                "paper_only": True,
            },
        }
        try:
            confirmed["signal_record"] = SignalRecord.from_mapping({
                **confirmed,
                "strategy": "cipher",
                "signal_at": evidence.get("event_at"),
                "available_at": evidence.get("captured_at") or evidence.get("event_at"),
                "evidence_snapshot_ids": [evidence.get("snapshot_id")],
                "decision": "accepted",
                "metadata": {"plan_id": plan.get("plan_id"), "entry_mode": "premarket"},
            }).to_dict()
        except (TypeError, ValueError):
            confirmed["signal_record_error"] = "incomplete_signal_contract"
        confirmed_by_ticker[ticker] = confirmed
    return {
        "source": "cipher_premarket_autopilot",
        "scan_type": "cipher",
        "captured_at": now.astimezone(timezone.utc).isoformat(),
        "plan_id": plan.get("plan_id"),
        "cards": [confirmed_by_ticker[ticker] for ticker in confirmed_by_ticker],
        "rejected": rejected,
        "confirmation_sources": ["premarket_plan"],
        "scan_types": [str(scan.get("strategy") or "cipher")],
        "premarket_entry": True,
        "source_scan_as_of": scan.get("as_of"),
        "paper_only": True,
        "live_execution_capability": False,
    }


def _confirmation_result(
    plan: dict[str, Any], scan: dict[str, Any], now: datetime,
    confirmed: list[dict[str, Any]], rejected: list[dict[str, Any]],
    *,
    confirmation_sources: list[str] | None = None,
    scan_types: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "source": "cipher_premarket_autopilot",
        "scan_type": "cipher",
        "captured_at": now.astimezone(timezone.utc).isoformat(),
        "plan_id": plan.get("plan_id"),
        "cards": confirmed,
        "rejected": rejected,
        "confirmation_sources": confirmation_sources or [],
        "scan_types": scan_types or [],
        "source_scan_as_of": scan.get("as_of"),
        "paper_only": True,
        "live_execution_capability": False,
    }
