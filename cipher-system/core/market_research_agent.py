"""Scheduled, read-only market research synthesis.

This module ranks *research candidates*, never orders or recommendations.  It consumes the
same scanner evidence as the UI, keeps observed fields separate from derived judgements,
and refuses to call sparse/invalid rows high-confidence.  A failed symbol remains a failed
symbol; partial provider failures do not erase the rest of the report.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from typing import Callable, Iterable

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "research_agent"
REPORT_DIR = DATA_DIR / "reports"
LATEST_FILE = DATA_DIR / "latest.json"

UNIVERSE_GROUPS: dict[str, tuple[str, ...]] = {
    "index": ("SPY", "QQQ", "IWM"),
    "large_liquid": ("AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "AMD", "NFLX"),
    "semiconductors": ("MU", "SNDK", "TSM", "ARM", "SMCI", "LRCX", "AMAT"),
    "tactical_liquid": ("PLTR", "COIN", "MSTR", "HOOD", "RBLX", "CRWD"),
}
DEFAULT_GROUPS = ("index", "large_liquid", "semiconductors", "tactical_liquid")


def universe(groups: Iterable[str] = DEFAULT_GROUPS) -> list[str]:
    """Return a stable de-duplicated configured universe."""
    seen: set[str] = set()
    result: list[str] = []
    for group in groups:
        for symbol in UNIVERSE_GROUPS.get(str(group), ()):
            if symbol not in seen:
                seen.add(symbol)
                result.append(symbol)
    return result


def _number(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if value == value else None


def _coverage(card: dict) -> dict:
    cells = _number(card.get("coverage_cells"))
    contracts = _number(card.get("contracts"))
    known = cells is not None and contracts is not None
    sufficient = bool(known and cells >= 8 and contracts >= 20)
    return {
        "status": "sufficient" if sufficient else ("limited" if known else "unknown"),
        "calculated_cells": int(cells) if cells is not None else None,
        "contracts": int(contracts) if contracts is not None else None,
        "minimums": {"calculated_cells": 8, "contracts": 20},
    }


def _candidate(card: dict, horizon: str, rank: int) -> dict:
    score = _number(card.get("score"))
    rr = _number(card.get("reward_risk"))
    coverage = _coverage(card)
    direction = card.get("direction") if card.get("direction") in {"BULLISH", "BEARISH"} else "NEUTRAL"
    geometry = card.get("geometry_valid") is True
    actionable = card.get("actionable") is True
    eligible = bool(coverage["status"] == "sufficient" and geometry and direction != "NEUTRAL" and score is not None)
    confidence = "insufficient"
    if eligible:
        confidence = "higher" if score >= 75 and actionable and (rr is None or rr >= 1) else "developing"
    blockers = list(card.get("validation_errors") or [])
    if coverage["status"] != "sufficient":
        blockers.append(f"options_coverage_{coverage['status']}")
    if not geometry:
        blockers.append("invalid_or_incomplete_level_geometry")

    if eligible and direction == "BULLISH":
        template = "defined-risk call debit spread research"
    elif eligible and direction == "BEARISH":
        template = "defined-risk put debit spread research"
    else:
        template = "wait / collect missing evidence"

    return {
        "rank": rank,
        "ticker": str(card.get("ticker") or "").upper(),
        "horizon": horizon,
        "observed": {
            "spot": _number(card.get("spot")),
            "day_change_pct": _number(card.get("day_change_pct")),
            "feed": card.get("feed"),
            "scanner_as_of": card.get("scanner_as_of"),
            "supports": card.get("supports") or [],
            "resistances": card.get("resistances") or [],
            "target": _number(card.get("target")),
            "invalidation": _number(card.get("invalidation")),
            "coverage": coverage,
        },
        "derived": {
            "ranking_score": score,
            "direction": direction,
            "setup_type": card.get("setup_type"),
            "reward_risk": rr,
            "confidence": confidence,
            "eligible_for_deeper_review": eligible,
            "research_template": template,
            "thesis": card.get("read") or card.get("reason"),
            "blockers": sorted(set(blockers)),
        },
        "disclaimer": "Research candidate only; not a recommendation or order instruction.",
    }


def _rows(scan: dict, horizon: str, limit: int) -> list[dict]:
    as_of = scan.get("as_of")
    cards = []
    for raw in scan.get("top") or []:
        card = dict(raw)
        card["scanner_as_of"] = as_of
        cards.append(card)
    # The scanner supplies rank, but re-sort defensively so fixtures and persisted reports
    # have one deterministic contract.
    cards.sort(key=lambda row: (-float(row.get("score") or 0), str(row.get("ticker") or "")))
    return [_candidate(card, horizon, idx) for idx, card in enumerate(cards[:limit], 1)]


def build_report(
    *,
    intraday_scan: dict | None,
    weekly_scan: dict | None,
    selected_universe: list[str],
    errors: list[dict] | None = None,
    generated_at: str | None = None,
    candidate_limit: int = 10,
    discovery: dict | None = None,
) -> dict:
    generated_at = generated_at or datetime.now(timezone.utc).isoformat()
    intraday = _rows(intraday_scan or {}, "intraday", candidate_limit)
    weekly = _rows(weekly_scan or {}, "weekly", candidate_limit)
    source_times = sorted({
        value for value in ((intraday_scan or {}).get("as_of"), (weekly_scan or {}).get("as_of")) if value
    })
    return {
        "schema_version": 1,
        "generated_at": generated_at,
        "session_timezone": "America/New_York",
        "universe": selected_universe,
        "discovery": discovery or {"status": "NOT_CONFIGURED", "symbols": []},
        "universe_groups": {name: list(symbols) for name, symbols in UNIVERSE_GROUPS.items()},
        "source_timestamps": source_times,
        "candidates": {"intraday": intraday, "weekly": weekly},
        "scan_summary": {
            "intraday": {key: (intraday_scan or {}).get(key) for key in ("scanned", "qualified", "actionable", "failed", "elapsed_ms")},
            "weekly": {key: (weekly_scan or {}).get(key) for key in ("scanned", "qualified", "actionable", "failed", "elapsed_ms")},
        },
        "errors": list(errors or []),
        "method": (
            "Cipher scanner ranks public-market structure. Confidence is a data-quality label, "
            "not a win probability. Options coverage and valid price geometry gate deeper review."
        ),
        "execution_boundary": {
            "read_only": True,
            "paper_only": True,
            "live_order_authority": False,
            "allowed_output": "research candidates and defined-risk templates",
        },
    }


def run(
    scan_fn: Callable[..., dict],
    *,
    groups: Iterable[str] = DEFAULT_GROUPS,
    candidate_limit: int = 10,
    discovery_fn: Callable[[], dict] | None = None,
    discovery_limit: int = 45,
) -> dict:
    selected = universe(groups)
    discovery = {"status": "NOT_CONFIGURED", "symbols": []}
    if discovery_fn is not None:
        try:
            discovery = discovery_fn()
            for symbol in list(discovery.get("symbols") or [])[:discovery_limit]:
                symbol = str(symbol).upper()
                if symbol and symbol not in selected:
                    selected.append(symbol)
        except Exception as exc:
            discovery = {"status": "UNAVAILABLE", "symbols": [], "error": str(exc)}
    scans: dict[str, dict | None] = {"intraday": None, "weekly": None}
    errors: list[dict] = []
    for horizon, mode in (("intraday", "short"), ("weekly", "long")):
        try:
            scans[horizon] = scan_fn(mode=mode, universe=selected, limit=max(candidate_limit * 2, 20))
        except Exception as exc:  # one horizon should not erase a usable other horizon
            errors.append({"scope": horizon, "error": str(exc)})
    return build_report(
        intraday_scan=scans["intraday"], weekly_scan=scans["weekly"],
        selected_universe=selected, errors=errors, candidate_limit=candidate_limit,
        discovery=discovery,
    )


def save(report: dict, data_dir: Path = DATA_DIR) -> Path:
    report_dir = data_dir / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    safe_stamp = re.sub(r"[^0-9]", "", str(report.get("generated_at") or ""))[:14] or "unknown"
    target = report_dir / f"research-{safe_stamp}.json"
    latest = data_dir / "latest.json"
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    for path in (target, latest):
        temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
        temporary.write_text(encoded, encoding="utf-8")
        os.replace(temporary, path)
    return target


def latest(data_dir: Path = DATA_DIR) -> dict:
    path = data_dir / "latest.json"
    if not path.exists():
        return {
            "generated_at": None, "candidates": {"intraday": [], "weekly": []},
            "errors": [], "available": False, "read_only": True,
            "message": "No scheduled research report has been captured yet.",
        }
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["available"] = True
    return payload


def history(data_dir: Path = DATA_DIR, limit: int = 30) -> list[dict]:
    rows = []
    for path in sorted((data_dir / "reports").glob("research-*.json"), reverse=True)[:max(1, min(limit, 100))]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        rows.append({
            "id": path.stem, "generated_at": payload.get("generated_at"),
            "intraday_candidates": len((payload.get("candidates") or {}).get("intraday") or []),
            "weekly_candidates": len((payload.get("candidates") or {}).get("weekly") or []),
            "errors": len(payload.get("errors") or []),
        })
    return rows
