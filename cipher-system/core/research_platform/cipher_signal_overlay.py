"""Prospective Flash, Agentic, and Cluster overlays for recent reversal research.

The normalized browser capture has only a short live history.  This module
therefore creates immutable, after-close research observations; it does not
backfill 2025, alter the base strategy, promote a candidate, or authorize any
execution.  Historical CSV retries are globally deduplicated by ``signal_id``
and only the first regular-session observation defines an episode.
"""
from __future__ import annotations

import csv
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

import pandas as pd

from .hashing import sha256_file, stable_id

NY = ZoneInfo("America/New_York")
SCAN_TYPES = ("flash", "flash_agentic", "cluster")
DIRECTIONS = {"BULLISH", "BEARISH"}


@dataclass(frozen=True)
class SignalOverlayPolicy:
    name: str
    rule: str
    description: str

    @property
    def policy_id(self) -> str:
        return stable_id("cipher_signal_overlay_policy", asdict(self), length=24)


def default_signal_overlay_policies() -> tuple[SignalOverlayPolicy, ...]:
    """Return the fixed, non-adaptive policy family."""

    return (
        SignalOverlayPolicy(
            "baseline_reversal",
            "retain_all",
            "Keep the complete frozen reversal basket without signal filtering.",
        ),
        SignalOverlayPolicy(
            "agentic_conflict_avoidance",
            "drop_latest_agentic_bearish",
            "Remove a symbol only when its latest eligible Agentic episode is bearish.",
        ),
        SignalOverlayPolicy(
            "flash_conflict_avoidance",
            "drop_latest_flash_bearish",
            "Remove a symbol only when its latest eligible Flash episode is bearish.",
        ),
        SignalOverlayPolicy(
            "cluster_conflict_avoidance",
            "drop_latest_cluster_bearish",
            "Remove a symbol only when its latest eligible Cluster episode is bearish.",
        ),
        SignalOverlayPolicy(
            "all_source_consensus",
            "retain_bullish_without_bearish",
            "Keep a symbol only when at least one covered source is bullish and no covered source is bearish.",
        ),
        SignalOverlayPolicy(
            "bearish_pressure_confirmation",
            "retain_flash_or_agentic_bearish",
            "Keep a long reversal candidate only when Flash or Agentic records bearish pressure.",
        ),
    )


def _bool(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _timestamp(value: Any) -> pd.Timestamp | None:
    parsed = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(parsed):
        return None
    return pd.Timestamp(parsed)


def _regular_session(timestamp: pd.Timestamp) -> tuple[str, bool]:
    local = timestamp.to_pydatetime().astimezone(NY)
    minute = local.hour * 60 + local.minute
    return local.date().isoformat(), bool(local.weekday() < 5 and 570 <= minute <= 960)


def signal_source_files(capture_root: str | Path) -> list[Path]:
    root = Path(capture_root)
    files: list[Path] = []
    for scan_type in SCAN_TYPES:
        files.extend(sorted(root.glob(f"{scan_type}-signals-v2-*.csv")))
    return sorted(set(files))


def signal_file_manifest(capture_root: str | Path) -> list[dict[str, Any]]:
    return [
        {
            "name": path.name,
            "size": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in signal_source_files(capture_root)
    ]


def load_signal_episodes(capture_root: str | Path) -> list[dict[str, Any]]:
    """Load globally deduplicated episodes from normalized signal CSVs.

    Duplicate imports can place the same signal ID in more than one daily file.
    The earliest timestamp and its point-in-time fields are canonical.  Later
    duplicates may extend ``last_seen_at`` and ``seen_count`` but cannot rewrite
    the original direction, setup, score, or geometry.
    """

    episodes: dict[str, dict[str, Any]] = {}
    for path in signal_source_files(capture_root):
        with path.open(newline="", encoding="utf-8") as handle:
            for raw in csv.DictReader(handle):
                signal_id = str(raw.get("signal_id") or "").strip()
                scan_type = str(raw.get("scan_type") or "").strip().lower()
                first_seen = _timestamp(raw.get("first_seen_at") or raw.get("card_timestamp") or raw.get("received_at"))
                last_seen = _timestamp(raw.get("last_seen_at") or raw.get("first_seen_at") or raw.get("received_at"))
                if not signal_id or scan_type not in SCAN_TYPES or first_seen is None:
                    continue
                market_session, regular_hours = _regular_session(first_seen)
                try:
                    raw_card = json.loads(str(raw.get("raw_json") or "{}"))
                except (TypeError, ValueError, json.JSONDecodeError):
                    raw_card = {}
                if not isinstance(raw_card, dict):
                    raw_card = {}
                rank_value = _float(raw_card.get("rank"))
                episode = {
                    "signal_id": signal_id,
                    "signal_signature": str(raw.get("signal_signature") or ""),
                    "scan_type": scan_type,
                    "ticker": str(raw.get("ticker") or "").upper().strip(),
                    "direction": str(raw.get("direction") or "").upper().strip(),
                    "setup_family": str(raw.get("setup_family") or "").strip().lower(),
                    "score": _float(raw.get("score")),
                    "strength": _float(raw.get("strength")),
                    "rank": int(rank_value) if rank_value is not None else None,
                    "declared_dte": raw_card.get("dte"),
                    "declared_expiration": (
                        raw_card.get("expiration")
                        or raw_card.get("expiry")
                        or raw_card.get("expiration_date")
                    ),
                    "spot": _float(raw.get("spot")),
                    "target": _float(raw.get("target")),
                    "invalidation": _float(raw.get("invalidation")),
                    "geometry_valid": _bool(raw.get("geometry_valid")),
                    "actionable": _bool(raw.get("actionable")),
                    "first_seen_at": first_seen.isoformat(),
                    "last_seen_at": (last_seen or first_seen).isoformat(),
                    "seen_count": int(float(raw.get("seen_count") or 1)),
                    "market_session": market_session,
                    "regular_hours": regular_hours,
                    "source_file": path.name,
                }
                existing = episodes.get(signal_id)
                if existing is None or episode["first_seen_at"] < existing["first_seen_at"]:
                    if existing is not None:
                        episode["last_seen_at"] = max(episode["last_seen_at"], existing["last_seen_at"])
                        episode["seen_count"] = max(episode["seen_count"], existing["seen_count"])
                    episodes[signal_id] = episode
                else:
                    existing["last_seen_at"] = max(existing["last_seen_at"], episode["last_seen_at"])
                    existing["seen_count"] = max(existing["seen_count"], episode["seen_count"])
    return sorted(episodes.values(), key=lambda row: (row["first_seen_at"], row["signal_id"]))


def eligible_episode(episode: Mapping[str, Any]) -> bool:
    if not episode.get("regular_hours"):
        return False
    if not episode.get("ticker") or episode.get("direction") not in DIRECTIONS:
        return False
    if not bool(episode.get("geometry_valid")):
        return False
    if episode.get("scan_type") in {"flash", "flash_agentic"}:
        return bool(episode.get("actionable"))
    return episode.get("scan_type") == "cluster"


def capture_inventory(episodes: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    eligible = [row for row in episodes if eligible_episode(row)]
    sessions = sorted({str(row["market_session"]) for row in episodes})
    by_source: dict[str, Any] = {}
    for source in SCAN_TYPES:
        source_rows = [row for row in episodes if row.get("scan_type") == source]
        source_eligible = [row for row in eligible if row.get("scan_type") == source]
        source_sessions = sorted({str(row["market_session"]) for row in source_rows})
        by_source[source] = {
            "episodes": len(source_rows),
            "eligible_regular_session_episodes": len(source_eligible),
            "tickers": len({str(row.get("ticker")) for row in source_rows if row.get("ticker")}),
            "sessions": len(source_sessions),
            "first_session": source_sessions[0] if source_sessions else None,
            "last_session": source_sessions[-1] if source_sessions else None,
        }
    return {
        "episodes": len(episodes),
        "eligible_regular_session_episodes": len(eligible),
        "sessions": len(sessions),
        "first_session": sessions[0] if sessions else None,
        "last_session": sessions[-1] if sessions else None,
        "by_source": by_source,
        "historical_backtest_eligible": False,
        "historical_backtest_blocker": "Normalized capture begins in late July 2026 and cannot support a 2025 backtest.",
    }


def session_signal_features(
    episodes: Sequence[Mapping[str, Any]],
    *,
    market_session: str,
    symbols: Iterable[str],
) -> dict[str, dict[str, Any]]:
    requested = sorted({str(symbol).upper().strip() for symbol in symbols if str(symbol).strip()})
    selected = [
        row
        for row in episodes
        if eligible_episode(row)
        and str(row.get("market_session")) == market_session
        and str(row.get("ticker")) in requested
    ]
    features: dict[str, dict[str, Any]] = {}
    for symbol in requested:
        symbol_rows = [row for row in selected if row.get("ticker") == symbol]
        source_features: dict[str, Any] = {}
        for source in SCAN_TYPES:
            rows = sorted(
                [row for row in symbol_rows if row.get("scan_type") == source],
                key=lambda row: (str(row.get("first_seen_at")), str(row.get("signal_id"))),
            )
            latest = rows[-1] if rows else None
            source_features[source] = {
                "covered": bool(rows),
                "episode_count": len(rows),
                "bullish_count": sum(row.get("direction") == "BULLISH" for row in rows),
                "bearish_count": sum(row.get("direction") == "BEARISH" for row in rows),
                "latest_direction": latest.get("direction") if latest else None,
                "latest_setup_family": latest.get("setup_family") if latest else None,
                "latest_first_seen_at": latest.get("first_seen_at") if latest else None,
                "latest_score": latest.get("score") if latest else None,
                "latest_strength": latest.get("strength") if latest else None,
                "maximum_score": max((row["score"] for row in rows if row.get("score") is not None), default=None),
                "maximum_strength": max((row["strength"] for row in rows if row.get("strength") is not None), default=None),
                "signal_ids": [str(row.get("signal_id")) for row in rows],
            }
        features[symbol] = {
            "symbol": symbol,
            "sources": source_features,
            "covered_sources": sum(source_features[source]["covered"] for source in SCAN_TYPES),
        }
    return features


def _latest(features: Mapping[str, Any], source: str) -> str | None:
    return ((features.get("sources") or {}).get(source) or {}).get("latest_direction")


def apply_signal_overlay_policy(
    symbols: Sequence[str],
    features: Mapping[str, Mapping[str, Any]],
    policy: SignalOverlayPolicy,
) -> dict[str, Any]:
    retained: list[str] = []
    decisions: list[dict[str, Any]] = []
    for symbol in sorted({str(value).upper() for value in symbols}):
        row = features.get(symbol) or {"sources": {}}
        flash = _latest(row, "flash")
        agentic = _latest(row, "flash_agentic")
        cluster = _latest(row, "cluster")
        directions = [value for value in (flash, agentic, cluster) if value in DIRECTIONS]
        keep = True
        reason = "baseline_retained"
        if policy.rule == "drop_latest_agentic_bearish":
            keep = agentic != "BEARISH"
            reason = "agentic_bearish_conflict" if not keep else "no_agentic_bearish_conflict"
        elif policy.rule == "drop_latest_flash_bearish":
            keep = flash != "BEARISH"
            reason = "flash_bearish_conflict" if not keep else "no_flash_bearish_conflict"
        elif policy.rule == "drop_latest_cluster_bearish":
            keep = cluster != "BEARISH"
            reason = "cluster_bearish_conflict" if not keep else "no_cluster_bearish_conflict"
        elif policy.rule == "retain_bullish_without_bearish":
            keep = "BULLISH" in directions and "BEARISH" not in directions
            reason = "bullish_no_bearish_consensus" if keep else "consensus_not_established"
        elif policy.rule == "retain_flash_or_agentic_bearish":
            keep = flash == "BEARISH" or agentic == "BEARISH"
            reason = "bearish_pressure_present" if keep else "bearish_pressure_absent"
        elif policy.rule != "retain_all":
            raise ValueError(f"unknown overlay policy rule: {policy.rule}")
        if keep:
            retained.append(symbol)
        decisions.append(
            {
                "symbol": symbol,
                "retained": keep,
                "reason": reason,
                "latest_directions": {
                    "flash": flash,
                    "flash_agentic": agentic,
                    "cluster": cluster,
                },
            }
        )
    fallback_to_spy = not retained
    symbol_weights = (
        {"SPY": 1.0}
        if fallback_to_spy
        else {symbol: 1.0 / len(retained) for symbol in retained}
    )
    return {
        "policy_id": policy.policy_id,
        "policy_name": policy.name,
        "policy_rule": policy.rule,
        "retained_symbols": retained,
        "dropped_symbols": sorted(set(symbols) - set(retained)),
        "fallback_to_spy": fallback_to_spy,
        "symbol_weights": symbol_weights,
        "decisions": decisions,
    }


def baseline_symbols_from_recent_snapshot(snapshot: Mapping[str, Any]) -> list[str]:
    leader = dict(snapshot.get("leader") or {})
    symbols: set[str] = set()
    for component in leader.get("current_selected_components") or []:
        for item in component.get("active_symbols") or []:
            symbol = str(item.get("symbol") or "").upper().strip()
            if symbol:
                symbols.add(symbol)
    return sorted(symbols)


def build_signal_overlay_snapshot(
    *,
    recent_snapshot: Mapping[str, Any],
    episodes: Sequence[Mapping[str, Any]],
    file_manifest: Sequence[Mapping[str, Any]],
    created_at: datetime,
) -> dict[str, Any]:
    market_session = str(recent_snapshot.get("market_session") or "")
    baseline_symbols = baseline_symbols_from_recent_snapshot(recent_snapshot)
    if not market_session:
        raise RuntimeError("recent snapshot has no market session")
    if not baseline_symbols:
        raise RuntimeError("recent snapshot has no active baseline symbols")
    features = session_signal_features(episodes, market_session=market_session, symbols=baseline_symbols)
    policies = default_signal_overlay_policies()
    decisions = [apply_signal_overlay_policy(baseline_symbols, features, policy) for policy in policies]
    observations = [
        {
            "observation_type": "cipher_signal_overlay",
            "observation_name": row["policy_name"],
            "observation_id": row["policy_id"],
            "symbol_weights": row["symbol_weights"],
            "selection": {
                "baseline_symbols": baseline_symbols,
                "retained_symbols": row["retained_symbols"],
                "dropped_symbols": row["dropped_symbols"],
                "fallback_to_spy": row["fallback_to_spy"],
            },
            "metadata": {
                "policy_rule": row["policy_rule"],
                "decisions": row["decisions"],
            },
        }
        for row in decisions
    ]
    payload = {
        "schema_version": 1,
        "created_at": created_at.isoformat(),
        "market_session": market_session,
        "source_recent_snapshot_id": recent_snapshot.get("snapshot_id"),
        "source_recent_snapshot": {
            "market_session": market_session,
            "dataset": recent_snapshot.get("dataset"),
            "leader": recent_snapshot.get("leader"),
        },
        "baseline_symbols": baseline_symbols,
        "capture_inventory": capture_inventory(episodes),
        "capture_file_manifest": list(file_manifest),
        "session_features": features,
        "policy_family": {
            "count": len(policies),
            "hash": stable_id("cipher_signal_overlay_policy_family", [asdict(policy) for policy in policies], length=64),
            "policies": [{**asdict(policy), "policy_id": policy.policy_id} for policy in policies],
            "outcome_adaptive": False,
        },
        "policy_decisions": decisions,
        "observations": observations,
        "research_role": "prospective_signal_context_overlay_only_no_historical_backfill",
        "automatic_promotion": False,
        "paper_or_live_execution": False,
        "execution_authority": False,
    }
    identity = {key: value for key, value in payload.items() if key != "created_at"}
    payload["snapshot_id"] = stable_id("cipher_signal_overlay_snapshot", identity, length=64)
    return payload


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_immutable_signal_overlay_snapshot(
    snapshot: Mapping[str, Any],
    *,
    root: str | Path,
    updated_at: datetime,
) -> dict[str, Any]:
    root_path = Path(root)
    snapshots = root_path / "snapshots"
    conflicts = root_path / "conflicts"
    session = str(snapshot.get("market_session") or "")
    if not session:
        raise RuntimeError("overlay snapshot has no market session")
    canonical = snapshots / f"{session}.json"
    conflict_path: Path | None = None
    if canonical.is_file():
        existing = json.loads(canonical.read_text(encoding="utf-8"))
        if existing.get("snapshot_id") == snapshot.get("snapshot_id"):
            status = "existing_immutable_snapshot"
            canonical_payload = existing
        else:
            conflict_path = conflicts / f"{session}_{snapshot.get('snapshot_id')}.json"
            if not conflict_path.is_file():
                _write_json_atomic(conflict_path, snapshot)
            status = "immutable_conflict_preserved"
            canonical_payload = existing
    else:
        _write_json_atomic(canonical, snapshot)
        status = "created_immutable_snapshot"
        canonical_payload = dict(snapshot)
    latest = {
        "schema_version": 1,
        "updated_at": updated_at.isoformat(),
        "status": status,
        "market_session": session,
        "snapshot_id": canonical_payload.get("snapshot_id"),
        "snapshot_path": str(canonical),
        "conflict_path": str(conflict_path) if conflict_path else None,
        "automatic_promotion": False,
        "paper_or_live_execution": False,
        "execution_authority": False,
    }
    _write_json_atomic(root_path / "latest.json", latest)
    return latest
