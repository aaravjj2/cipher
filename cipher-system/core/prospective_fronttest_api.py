"""Read-only presentation model for registered prospective fronttests."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.prospective_fronttests import DEFAULT_DB, connect, status as program_status


def _json(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (json.JSONDecodeError, TypeError):
        return fallback


def _leg_mark(item: dict[str, Any], now: datetime) -> None:
    if item.get("status") != "OPEN":
        item.update({"mark_status": "closed", "mark_mid": None, "unrealized_pnl_mid": None,
                     "liquidation_fill": None, "liquidation_pnl": None, "mark_age_seconds": None})
        return
    try:
        bid, ask, entry = float(item["last_bid"]), float(item["last_ask"]), float(item["entry_fill"])
        stamp = datetime.fromisoformat(str(item["last_mark_at"]).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError("naive mark")
    except (TypeError, ValueError):
        item.update({"mark_status": "unavailable", "mark_mid": None, "unrealized_pnl_mid": None,
                     "liquidation_fill": None, "liquidation_pnl": None, "mark_age_seconds": None})
        return
    age = max(0.0, (now - stamp.astimezone(timezone.utc)).total_seconds())
    mid = (bid + ask) / 2
    liquidation = max(0.0, bid * .995 - .01)
    item.update({
        "mark_status": "stale" if age > 120 else "current",
        "mark_age_seconds": round(age, 3), "mark_mid": round(mid, 6),
        "unrealized_pnl_mid": round((mid - entry) * 100 - .65, 2),
        "liquidation_fill": round(liquidation, 6),
        "liquidation_pnl": round((liquidation - entry) * 100 - 1.30, 2),
    })


def snapshot(db_path: Path = DEFAULT_DB, *, recent_limit: int = 50) -> dict[str, Any]:
    """Return the audit ledger without granting mutation or execution authority."""
    if not db_path.exists():
        # Registration is a safe idempotent schema operation. It does not evaluate
        # historical bars and therefore cannot manufacture a prospective signal.
        connect(db_path).close()
    with sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True, timeout=2.0) as db:
        db.row_factory = sqlite3.Row
        programs = program_status(db)
        now = datetime.now(timezone.utc)
        for program in programs:
            starts = datetime.fromisoformat(program["starts_at"])
            ends = datetime.fromisoformat(program["ends_at"]) if program["ends_at"] else None
            if now < starts:
                effective = "REGISTERED"
            elif ends and now > ends:
                effective = "COMPLETED"
            elif program["signals"]:
                effective = "COLLECTING"
            else:
                effective = "MONITORING"
            program["effective_status"] = effective
            program["sample_progress"] = min(
                1.0, program["closed_signals"] / max(1, program["minimum_sample"])
            )
        signals = []
        for row in db.execute(
            "select * from signals order by signal_bar_at desc limit ?", (recent_limit,)
        ):
            item = dict(row)
            item["target_hits"] = _json(item.pop("target_hits_json", None), [])
            payload = _json(item.pop("payload_json", None), {})
            item["option_selection_status"] = payload.get("option_selection_status")
            item["requested_strikes"] = payload.get("requested_strikes", [])
            item["feature_snapshot_ids"] = payload.get("feature_snapshot_ids", [])
            if isinstance(payload.get("signal_record"), dict):
                item["signal_record"] = payload["signal_record"]
                item["canonical_signal_id"] = payload["signal_record"].get("signal_id")
                item["evidence_snapshot_ids"] = payload["signal_record"].get("evidence_snapshot_ids", [])
            if isinstance(payload.get("evidence_contract"), dict):
                item["evidence_contract"] = payload["evidence_contract"]
            signals.append(item)
        legs = [dict(row) for row in db.execute(
            "select * from option_legs order by entry_at desc limit ?", (recent_limit,)
        )]
        for item in legs:
            _leg_mark(item, now)
        program_marks: dict[str, dict[str, float]] = {}
        signal_program = {row["signal_id"]: row["program_id"] for row in signals}
        for item in legs:
            program_id = signal_program.get(item["signal_id"])
            if not program_id or item.get("status") != "OPEN":
                continue
            marks = program_marks.setdefault(program_id, {"mid": 0.0, "liquidation": 0.0})
            marks["mid"] += float(item.get("unrealized_pnl_mid") or 0)
            marks["liquidation"] += float(item.get("liquidation_pnl") or 0)
        for program in programs:
            marks = program_marks.get(program["program_id"], {"mid": 0.0, "liquidation": 0.0})
            program["open_option_mark_pnl"] = round(marks["mid"], 2)
            program["open_option_liquidation_pnl"] = round(marks["liquidation"], 2)
        observations = [dict(row) for row in db.execute(
            """select observation_id,run_id,program_id,ticker,observed_at,latest_bar_at,
                      bar_age_seconds,bars_available,coverage_status,decision,reason
                 from observations order by run_id desc,program_id,ticker limit ?""",
            (recent_limit,),
        )]
        latest_run_id = max((int(row["run_id"]) for row in observations), default=None)
        latest_observations = [row for row in observations if row["run_id"] == latest_run_id]
        coverage_summary = {
            "run_id": latest_run_id,
            "observed": len(latest_observations),
            "fresh": sum(row["coverage_status"] == "FRESH" for row in latest_observations),
            "partial": sum(row["coverage_status"] == "PARTIAL" for row in latest_observations),
            "stale": sum(row["coverage_status"] == "STALE" for row in latest_observations),
            "missing": sum(row["coverage_status"] == "MISSING" for row in latest_observations),
            "signals_opened": sum(row["decision"] == "SIGNAL_OPENED" for row in latest_observations),
        }
        runs = [dict(row) for row in db.execute(
            "select run_id,started_at,completed_at,status,error from runs order by run_id desc limit 20"
        )]
    as_of = max(
        (row.get("completed_at") or row.get("started_at") or "" for row in runs),
        default="",
    ) or datetime.fromtimestamp(db_path.stat().st_mtime, timezone.utc).isoformat()
    return {
        "as_of": as_of,
        "paper_only": True,
        "read_only": True,
        "execution_capability": False,
        "programs": programs,
        "signals": signals,
        "option_legs": legs,
        "observations": observations,
        "latest_coverage": coverage_summary,
        "open_option_mark_pnl": round(sum(float(row.get("unrealized_pnl_mid") or 0) for row in legs if row.get("status") == "OPEN"), 2),
        "open_option_liquidation_pnl": round(sum(float(row.get("liquidation_pnl") or 0) for row in legs if row.get("status") == "OPEN"), 2),
        "option_liquidity_policy": {"maximum_entry_spread_pct": 12.0, "missing_quote_is_unavailable": True},
        "runs": runs,
        "caveat": (
            "Prospective observations only: no signal backfill. Option entries cross the observed ask "
            "with modeled slippage; exits cross the observed bid. Missing quotes, contracts, gamma, "
            "or open interest remain unavailable. GEX is a public-OI heuristic, not verified dealer positioning."
        ),
    }
