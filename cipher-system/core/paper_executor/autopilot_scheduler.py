"""One-cycle premarket-to-close scheduler for Cipher's paper-only autopilot."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .autopilot_planner import (
    AutopilotPhase,
    build_premarket_plan,
    confirmation_payload,
    phase_at,
    sentiment_context,
)
from .local_scan_scheduler import request_json, scanner_url


CORE_URL = "http://127.0.0.1:8282"
EXECUTOR_URL = "http://127.0.0.1:8787/api/scanner-ingest"
ROOT = Path(__file__).resolve().parents[2]
STATE_DIR = ROOT / "data" / "paper_runtime" / "autopilot"
PLAN_PATH = STATE_DIR / "premarket_plan.json"
STATUS_PATH = STATE_DIR / "status.json"

FOUNDATION_UNIVERSE = (
    "SPY", "QQQ", "IWM", "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META",
    "TSLA", "AVGO", "AMD", "MU", "SNDK", "NFLX", "PLTR", "COIN", "IBIT",
)


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True, allow_nan=False, default=str)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _load_plan(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _reason_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    return dict(sorted(Counter(
        str(reason) for row in rows for reason in (row.get("reasons") or [row.get("reason")]) if reason
    ).items()))


def _append_cycle_audit(status_path: Path, status: dict[str, Any], now: datetime) -> Path:
    """Append a compact, replay-safe decision trace for every scheduler cycle."""
    market_date = now.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    path = status_path.parent / "cycles" / f"{market_date}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    trace = {
        "cycle_id": hashlib.sha256(json.dumps(status, sort_keys=True, default=str).encode()).hexdigest()[:24],
        **status,
    }
    line = json.dumps(trace, sort_keys=True, allow_nan=False, default=str) + "\n"
    descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, line.encode("utf-8")); os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return path


def discovery_universe(core_url: str, *, limit: int = 30) -> list[str]:
    symbols = list(FOUNDATION_UNIVERSE)
    try:
        discovered = request_json(f"{core_url.rstrip('/')}/api/finviz-discovery?limit={limit}")
        symbols.extend(discovered.get("symbols") or [])
    except Exception:
        # Finviz is delayed supplemental discovery. Its absence must not suppress
        # the liquid Alpaca-validated core universe.
        pass
    seen: set[str] = set()
    unique: list[str] = []
    for raw in symbols:
        symbol = str(raw).upper()
        if symbol and symbol not in seen:
            seen.add(symbol)
            unique.append(symbol)
    return unique


def run_cycle(
    *,
    now: datetime,
    core_url: str = CORE_URL,
    executor_url: str = EXECUTOR_URL,
    plan_path: Path = PLAN_PATH,
    status_path: Path = STATUS_PATH,
    force_phase: AutopilotPhase | None = None,
) -> dict[str, Any]:
    phase = force_phase or phase_at(now)
    status: dict[str, Any] = {
        "as_of": now.astimezone(timezone.utc).isoformat(),
        "phase": phase.value,
        "paper_only": True,
        "live_execution_capability": False,
        "action": "noop",
    }
    if phase == AutopilotPhase.PREMARKET_DISCOVERY:
        universe = discovery_universe(core_url)
        scan = request_json(scanner_url(core_url, "cipher", universe, workers=1), timeout=900)
        sentiment = sentiment_context(universe, as_of=now)
        plan = build_premarket_plan(scan, now=now, sentiment=sentiment)
        _atomic_json(plan_path, plan)
        status.update({
            "action": "premarket_plan_saved",
            "plan_id": plan["plan_id"],
            "universe_size": len(universe),
            "candidates": len(plan["candidates"]),
            "rejected": len(plan["rejected"]),
            "premarket_entries": 0,
            "candidate_tickers": [row["ticker"] for row in plan["candidates"]],
            "rejection_reason_counts": _reason_counts(plan["rejected"]),
        })
    elif phase == AutopilotPhase.ENTRY_CONFIRMATION:
        plan = _load_plan(plan_path)
        market_date = now.astimezone(ZoneInfo("America/New_York")).date().isoformat()
        if not plan or plan.get("market_date") != market_date:
            status.update({"action": "blocked", "reason": "current_premarket_plan_missing"})
        else:
            tickers = [row["ticker"] for row in plan.get("candidates") or []]
            if not tickers:
                status.update({"action": "no_candidates", "plan_id": plan.get("plan_id")})
            else:
                scan = request_json(scanner_url(core_url, "flash_agentic", tickers, workers=1), timeout=600)
                payload = confirmation_payload(plan, scan, now=now)
                if payload["cards"]:
                    accepted = request_json(executor_url, payload=payload, timeout=30)
                    status.update({
                        "action": "paper_confirmations_submitted",
                        "plan_id": plan.get("plan_id"),
                        "confirmed": len(payload["cards"]),
                        "rejected": len(payload["rejected"]),
                        "batch_id": accepted.get("batch_id"),
                        "confirmed_tickers": [row.get("ticker") for row in payload["cards"]],
                        "rejection_reason_counts": _reason_counts(payload["rejected"]),
                    })
                else:
                    status.update({
                        "action": "no_confirmed_entries",
                        "plan_id": plan.get("plan_id"),
                        "confirmed": 0,
                        "rejected": payload["rejected"],
                        "rejection_reason_counts": _reason_counts(payload["rejected"]),
                    })
    elif phase == AutopilotPhase.OPENING_WAIT:
        status.update({"action": "wait_for_confirmed_0935_bar", "entries_allowed": False})
    elif phase in {AutopilotPhase.MONITOR_ONLY, AutopilotPhase.FORCE_CLOSE}:
        # The continuously running executor marks positions and applies stop, target,
        # maximum-hold, and 15:45 ET exits. This scheduler never duplicates that state.
        status.update({"action": "executor_monitoring", "new_entries_allowed": False})
    else:
        status.update({"action": "market_closed", "new_entries_allowed": False})
    audit_path = _append_cycle_audit(status_path, status, now)
    status["audit_path"] = str(audit_path)
    _atomic_json(status_path, status)
    return status


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core-url", default=CORE_URL)
    parser.add_argument("--executor-url", default=EXECUTOR_URL)
    parser.add_argument("--plan-path", type=Path, default=PLAN_PATH)
    parser.add_argument("--status-path", type=Path, default=STATUS_PATH)
    parser.add_argument("--force-phase", choices=[phase.value for phase in AutopilotPhase])
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    phase = AutopilotPhase(args.force_phase) if args.force_phase else None
    result = run_cycle(
        now=datetime.now(timezone.utc), core_url=args.core_url, executor_url=args.executor_url,
        plan_path=args.plan_path, status_path=args.status_path, force_phase=phase,
    )
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("action") != "blocked" else 2


if __name__ == "__main__":
    raise SystemExit(main())
