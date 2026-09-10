"""Append-only prospective prediction log for the daily earnings radar.

Phase 2 of the earnings re-validation requires that every daily radar outcome
per ticker — including NO_TRADE / gate-blocked decisions — is recorded before
any outcome is known. A model that only logs its wins cannot be re-validated:
the no-trade days and the refused entries are exactly the observations a future
chronological revalidation needs.

Shape: one JSON object per line in an append-only JSONL file, keyed idempotently
by (date, ticker). Re-running the radar the same morning must not duplicate
lines, so the writer scans existing keys first and appends only what is missing.
The file is never rewritten or reordered; a partially written trailing line (a
crash mid-append) is reported as malformed rather than silently dropped.

`entry_attempted` is answered from the paper book as of log time: True only when
a paper_positions row already exists for this (symbol, report_date). The digest
runs radar before paper-enter, so a same-morning FIRST entry shows False here —
the authoritative entry record is paper_portfolio.sqlite, and the two are meant
to be read together. Stating radar-time truth plainly beats backfilling a line
that was written before the attempt happened.

Writes go to runtime/data/ by default, the same ROOT the radar JSON artifact and
the research corpus already use. Research and paper simulation only; nothing
here touches order routing.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

try:  # stdlib on 3.9+; kept importable on odd builds without tz data
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover - fallback keeps the writer alive
    _ET = timezone.utc

DEFAULT_LOG_PATH = Path("/home/aarav/Aarav/cipher/runtime/data/earnings_prospective_log.jsonl")

#: Fields every prospective record must carry. Kept explicit so a card shape
#: change breaks loudly here instead of quietly thinning the log.
REQUIRED_FIELDS = (
    "date",
    "ticker",
    "model_version",
    "predicted_direction",
    "predicted_confidence",
    "predicted_gap_magnitude_pct",
    "gate_status",
    "calendar_confirmation",
    "entry_attempted",
)


def eastern_date(now: Optional[datetime] = None) -> str:
    """The radar's calendar date in the exchange timezone the digest runs on."""
    moment = now or datetime.now(timezone.utc)
    return moment.astimezone(_ET).strftime("%Y-%m-%d")


def attempted_entries(
    pairs: Sequence[Tuple[str, str]],
    db_path: Optional[str] = None,
) -> set[Tuple[str, str]]:
    """Which (symbol, report_date) pairs already hold a paper position.

    Read-only: a missing database means nothing was attempted, never an error —
    the radar must keep logging on a fresh host where the paper book does not
    exist yet.
    """
    if not pairs:
        return set()
    path = Path(db_path) if db_path else None
    if path is not None and not path.exists():
        return set()
    if path is None:
        from .paper_portfolio import PAPER_DB_PATH

        path = Path(PAPER_DB_PATH)
        if not path.exists():
            return set()
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            rows = conn.execute("SELECT symbol, report_date FROM paper_positions").fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return set()
    known = {(str(sym).upper(), str(rep)) for sym, rep in rows}
    return {(str(sym).upper(), str(rep)) for sym, rep in pairs} & known


def build_record(
    card: Mapping[str, Any],
    *,
    run_date: str,
    model_version: str,
    gate_status: str,
    entry_attempted: bool,
    logged_at_utc: Optional[str] = None,
) -> Dict[str, Any]:
    """One prospective log line from one radar trade card."""
    return {
        "date": run_date,
        "ticker": str(card.get("symbol", "")).upper(),
        "model_version": model_version,
        "predicted_direction": card.get("direction_bias"),
        "predicted_confidence": card.get("confidence"),
        "raw_prob_day5_up": card.get("prob_day5_up"),
        "raw_direction": card.get("raw_direction"),
        "forecast_status": card.get("forecast_status", "UNVALIDATED"),
        "feature_method": card.get("feature_method"),
        "model_artifact_sha256": card.get("model_artifact_sha256"),
        "forward_baseline": card.get("forward_baseline"),
        "last_mature_report_date": card.get("last_mature_report_date"),
        "predicted_gap_magnitude_pct": card.get("expected_gap_pct"),
        "gate_status": gate_status,
        "calendar_confirmation": card.get("earnings_date_confirmation"),
        "entry_attempted": bool(entry_attempted),
        # Provenance extras: cheap to store, and the scheduled date is what makes
        # entry_attempted checkable against the paper book later.
        "scheduled_date": card.get("scheduled_date"),
        "days_until": card.get("days_until"),
        "recommended_strategy": card.get("recommended_strategy"),
        "logged_at_utc": logged_at_utc or datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def existing_keys(path: Path) -> Tuple[set[Tuple[str, str]], int]:
    """Read the (date, ticker) keys already logged, and any unreadable lines.

    Returns (keys, malformed_count). A malformed line is counted, not discarded:
    the file is append-only, so repairing it is a human decision, not a side
    effect of a scan.
    """
    keys: set[Tuple[str, str]] = set()
    malformed = 0
    if not path.exists():
        return keys, malformed
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
                key = (str(payload["date"]), str(payload["ticker"]))
            except (json.JSONDecodeError, KeyError, TypeError):
                malformed += 1
                continue
            keys.add(key)
    return keys, malformed


def append_records(
    records: Iterable[Mapping[str, Any]],
    path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Append records whose (date, ticker) key is not present yet. Idempotent.

    The file itself is never rewritten: existing bytes stay exactly where they
    were, so the log remains a faithful append-only history even across reruns.
    """
    target = Path(path) if path else DEFAULT_LOG_PATH
    seen, malformed = existing_keys(target)
    written = 0
    skipped = 0
    if target.parent and not target.parent.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        for record in records:
            key = (str(record.get("date")), str(record.get("ticker")))
            if key in seen:
                skipped += 1
                continue
            missing = [field for field in REQUIRED_FIELDS if field not in record]
            if missing:
                raise ValueError(
                    f"prospective record for {key} is missing required fields: {missing}"
                )
            handle.write(json.dumps(record, sort_keys=True, default=str) + "\n")
            seen.add(key)
            written += 1
    return {
        "path": str(target),
        "written": written,
        "skipped_existing": skipped,
        "malformed_lines": malformed,
    }


def resolve_model_context() -> Dict[str, str]:
    """Model version and strategy gate as the trained artifacts state them.

    Missing artifacts degrade to explicit UNAVAILABLE markers rather than a
    plausible-looking default: a log line that cannot say which model produced
    it must say so on its face.
    """
    context = {"model_version": "UNAVAILABLE", "gate_status": "UNAVAILABLE_MODEL_ARTIFACTS"}
    try:
        from .model import load_trained_models

        results = (load_trained_models() or {}).get("results", {}) or {}
        context["model_version"] = results.get("model_version") or context["model_version"]
        gate = results.get("strategy_gate") or {}
        context["gate_status"] = gate.get("status") or context["gate_status"]
    except Exception:  # noqa: BLE001 - logging must survive model-loading failures
        pass
    return context


def log_radar_cards(
    cards: List[Mapping[str, Any]],
    *,
    run_date: Optional[str] = None,
    model_version: Optional[str] = None,
    gate_status: Optional[str] = None,
    path: Optional[Path] = None,
    paper_db_path: Optional[str] = None,
    now_utc: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Log every radar card — including NO_TRADE cards — idempotently.

    Called from the radar step of the daily digest, so tomorrow's 08:15 ET run
    records each day's predictions with zero manual steps.
    """
    date_value = run_date or eastern_date(now_utc)
    context = resolve_model_context()
    version = model_version or context["model_version"]
    gate = gate_status or context["gate_status"]

    pairs = [
        (str(card.get("symbol", "")).upper(), str(card.get("scheduled_date", "")))
        for card in cards
    ]
    attempted = attempted_entries(pairs, db_path=paper_db_path)

    stamp = (now_utc or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    records = [
        build_record(
            card,
            run_date=date_value,
            model_version=version,
            gate_status=gate,
            entry_attempted=(card_key in attempted),
            logged_at_utc=stamp,
        )
        for card, card_key in zip(cards, pairs)
    ]
    result = append_records(records, path=path)
    result.update({"run_date": date_value, "cards": len(cards)})
    return result
