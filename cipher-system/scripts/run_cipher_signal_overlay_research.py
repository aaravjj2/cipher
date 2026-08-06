#!/usr/bin/env python3
"""Freeze and evaluate Flash/Agentic/Cluster overlays on the recent basket."""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.research_platform.cipher_signal_overlay import (  # noqa: E402
    build_signal_overlay_snapshot,
    load_signal_episodes,
    signal_file_manifest,
    write_immutable_signal_overlay_snapshot,
)
from core.research_platform.recent_regime_prospective_evaluation import (  # noqa: E402
    evaluate_prospective_snapshots,
)

NY = ZoneInfo("America/New_York")
REGISTRY = ROOT / "data" / "governance" / "research_registry.sqlite"
CAPTURE_ROOT = ROOT / "data" / "browser_ingest"
RECENT_ROOT = ROOT / "data" / "governance" / "recent_regime_prospective"
OVERLAY_ROOT = ROOT / "data" / "governance" / "cipher_signal_overlay"
OUTPUT = ROOT / "data" / "governance" / "cipher_signal_overlay_research.json"
PREFIX = "alpaca_broad_daily_recent_2024_"
FALLBACK_NAME = "alpaca_broad_daily_2024_2026_ytd_holdout_v1"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
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
        raise RuntimeError("no canonical recent-regime dataset is registered")
    uri = str(row[3])
    if not uri.startswith("file://"):
        raise RuntimeError("recent-regime dataset is not a local canonical file")
    path = Path(uri.removeprefix("file://"))
    if not path.is_file():
        raise RuntimeError(f"recent-regime dataset path is unavailable: {path}")
    payload = json.loads(row[2]) if row[2] else {}
    quality = payload.get("quality_checks") or {}
    return {
        "dataset_id": str(row[0]),
        "dataset_name": str(row[1]),
        "path": str(path),
        "checksum": str(row[4]),
        "latest_session": str(quality.get("observed_end") or ""),
    }


def latest_recent_snapshot() -> tuple[Path, dict[str, Any]]:
    paths = sorted((RECENT_ROOT / "snapshots").glob("*.json"))
    if not paths:
        raise RuntimeError("no immutable recent-regime snapshot is available")
    path = paths[-1]
    payload = _read_json(path)
    if not payload:
        raise RuntimeError(f"recent-regime snapshot is invalid: {path}")
    return path, payload


def legacy_flash_agentic_evidence() -> dict[str, Any]:
    path = ROOT / "data" / "flash_agentic" / "flash_agentic.sqlite"
    if not path.is_file():
        return {"status": "unavailable"}
    with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=30) as db:
        row = db.execute(
            """
            select count(*),
                   avg(case when pnl_pct > 0 then 1.0 else 0.0 end),
                   avg(pnl_pct),
                   sum(pnl_pct),
                   min(opened_at),
                   max(coalesce(closed_at, opened_at))
            from flash_trades where status='closed'
            """
        ).fetchone()
    return {
        "status": "available_small_incompatible_sample",
        "closed_trades": int(row[0] or 0),
        "win_rate": float(row[1]) if row[1] is not None else None,
        "average_normalized_pnl_pct": float(row[2]) if row[2] is not None else None,
        "sum_normalized_pnl_pct": float(row[3]) if row[3] is not None else None,
        "first_opened_at": row[4],
        "last_closed_at": row[5],
        "usage": "context_only_not_merged_with_browser_episode_schema",
    }


def cluster_forward_evidence() -> dict[str, Any]:
    snapshots = sorted(
        (ROOT / "data" / "research_snapshots").glob("cluster_kronos_forward_*/sqlite_snapshot_*.sqlite"),
        key=lambda path: path.stat().st_mtime,
    )
    if not snapshots:
        return {"status": "unavailable"}
    path = snapshots[-1]
    with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=30) as db:
        overall = db.execute(
            """
            select count(*), avg(cluster_direction_positive),
                   avg(cluster_directional_return_pct),
                   avg(case when kronos_correct is not null then kronos_correct end)
            from outcomes
            """
        ).fetchone()
        groups = db.execute(
            """
            select p.evaluation_group, count(*), avg(o.cluster_direction_positive),
                   avg(o.cluster_directional_return_pct),
                   avg(case when o.kronos_correct is not null then o.kronos_correct end)
            from predictions p join outcomes o on o.prediction_id=p.id
            group by p.evaluation_group order by p.evaluation_group
            """
        ).fetchall()
    return {
        "status": "available_small_frozen_forward_sample",
        "path": str(path),
        "outcomes": int(overall[0] or 0),
        "cluster_directional_accuracy": float(overall[1]) if overall[1] is not None else None,
        "average_cluster_directional_return_pct": float(overall[2]) if overall[2] is not None else None,
        "kronos_accuracy": float(overall[3]) if overall[3] is not None else None,
        "by_evaluation_group": [
            {
                "group": row[0],
                "outcomes": int(row[1]),
                "cluster_directional_accuracy": float(row[2]) if row[2] is not None else None,
                "average_cluster_directional_return_pct": float(row[3]) if row[3] is not None else None,
                "kronos_accuracy": float(row[4]) if row[4] is not None else None,
            }
            for row in groups
        ],
        "usage": "context_only_supports_agreement_filter_research_not_standalone_alpha",
    }


def main() -> int:
    created_at = datetime.now(timezone.utc)
    current_et = created_at.astimezone(NY)
    dataset = latest_dataset()
    recent_path, recent_snapshot = latest_recent_snapshot()
    recent_session = str(recent_snapshot.get("market_session") or "")
    if recent_session != dataset["latest_session"]:
        raise RuntimeError(
            f"recent snapshot/data session mismatch: snapshot={recent_session} dataset={dataset['latest_session']}"
        )
    if current_et.date().isoformat() == recent_session and current_et.hour < 16:
        raise RuntimeError("signal overlay may be frozen only after the regular market close")

    episodes = load_signal_episodes(CAPTURE_ROOT)
    manifest = signal_file_manifest(CAPTURE_ROOT)
    overlay = build_signal_overlay_snapshot(
        recent_snapshot=recent_snapshot,
        episodes=episodes,
        file_manifest=manifest,
        created_at=created_at,
    )
    inventory = overlay["capture_inventory"]
    target_session_episodes = sum(
        str(row.get("market_session")) == recent_session and bool(row.get("regular_hours"))
        for row in episodes
    )
    if target_session_episodes <= 0:
        raise RuntimeError(f"normalized signal capture has no regular-session episodes for {recent_session}")
    inventory["target_session"] = recent_session
    inventory["target_session_regular_hours_episodes"] = target_session_episodes
    snapshot_status = write_immutable_signal_overlay_snapshot(
        overlay,
        root=OVERLAY_ROOT,
        updated_at=created_at,
    )

    frame = pd.read_parquet(dataset["path"], columns=["timestamp", "ticker", "open"])
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True).dt.tz_convert(None).dt.normalize()
    opens = frame.pivot(index="timestamp", columns="ticker", values="open").sort_index()
    evaluation = evaluate_prospective_snapshots(
        opens=opens,
        snapshot_paths=sorted((OVERLAY_ROOT / "snapshots").glob("*.json")),
        root=OVERLAY_ROOT,
        dataset=dataset,
        evaluated_at=created_at,
    )

    policy_baskets = {
        row["policy_name"]: {
            "retained_symbols": row["retained_symbols"],
            "dropped_symbols": row["dropped_symbols"],
            "fallback_to_spy": row["fallback_to_spy"],
            "symbol_weights": row["symbol_weights"],
        }
        for row in overlay["policy_decisions"]
    }
    payload = {
        "schema_version": 1,
        "created_at": created_at.isoformat(),
        "status": "completed",
        "dataset": dataset,
        "source_recent_snapshot": {
            "path": str(recent_path),
            "snapshot_id": recent_snapshot.get("snapshot_id"),
            "market_session": recent_session,
        },
        "capture_inventory": inventory,
        "policy_family": overlay["policy_family"],
        "baseline_symbols": overlay["baseline_symbols"],
        "session_features": overlay["session_features"],
        "current_policy_baskets": policy_baskets,
        "snapshot_status": snapshot_status,
        "prospective_evaluation": evaluation,
        "legacy_evidence": {
            "flash_agentic_simulation": legacy_flash_agentic_evidence(),
            "cluster_kronos_forward": cluster_forward_evidence(),
        },
        "assessment": {
            "can_help": True,
            "best_current_role": "prospective_confirmation_and_risk_context_for_the_reversal_component",
            "not_supported": [
                "2025_backfill",
                "standalone_alpha_claim",
                "automatic_strategy_selection",
                "promotion_or_execution_authority",
            ],
            "reason": (
                "Normalized Flash, Agentic, and Cluster history begins in late July 2026. "
                "The policy family is therefore frozen prospectively and evaluated only with future session opens."
            ),
        },
        "research_role": "prospective_cipher_signal_overlay_only_not_independent_historical_validation",
        "automatic_promotion": False,
        "paper_or_live_execution": False,
        "execution_authority": False,
    }
    _write_json(OUTPUT, payload)
    print(
        json.dumps(
            {
                "status": payload["status"],
                "market_session": recent_session,
                "capture_sessions": inventory["sessions"],
                "episodes": inventory["episodes"],
                "eligible_episodes": inventory["eligible_regular_session_episodes"],
                "baseline_symbols": payload["baseline_symbols"],
                "policies": payload["policy_family"]["count"],
                "current_policy_baskets": policy_baskets,
                "snapshot_status": snapshot_status["status"],
                "matured_observations": evaluation["matured_observations"],
                "pending_observations": evaluation["pending_observations"],
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
