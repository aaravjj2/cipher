"""One-cycle premarket-to-close scheduler for Cipher's paper-only autopilot."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
import hashlib
import json
import os
import tempfile
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .autopilot_planner import (
    AutopilotPhase,
    build_premarket_plan,
    confirmation_payload,
    phase_at,
    premarket_payload,
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


def ensure_executor_market_data(executor_url: str, ticker: str) -> dict[str, Any]:
    """Prove the executor can reach an authenticated OPRA chain before ingest."""
    base = executor_url.split("/api/", 1)[0].rstrip("/")
    result = request_json(f"{base}/api/paper/market-data-probe?ticker={ticker.upper()}", timeout=120)
    if result.get("ok") is False or str(result.get("feed") or "opra").lower() != "opra":
        raise ValueError("executor OPRA market-data probe failed")
    return result


def premarket_entry_enabled(flag: bool | None = None) -> bool:
    """Resolve the opt-in premarket-entry mode (CLI flag wins, else environment)."""
    if flag is not None:
        return bool(flag)
    value = os.environ.get("CIPHER_AUTOPILOT_PREMARKET_ENTRY") or ""
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _server_provider_credentials() -> dict[str, str]:
    key = (
        os.environ.get("ALPACA_ALGO_KEY")
        or os.environ.get("ALPACA_ALGO_PLUS_KEY")
        or os.environ.get("ALPACA_API_KEY")
    )
    secret = (
        os.environ.get("ALPACA_ALGO_SECRET")
        or os.environ.get("ALPACA_ALGO_PLUS_SECRET")
        or os.environ.get("ALPACA_API_SECRET")
    )
    if not key or not secret:
        raise ValueError("server Alpaca market-data credentials are not configured")
    options_feed = str(os.environ.get("ALPACA_DATA_FEED", "opra")).lower()
    stock_feed = str(os.environ.get("ALPACA_STOCK_FEED", "sip")).lower()
    if options_feed not in {"opra", "indicative"} or stock_feed not in {"sip", "iex"}:
        raise ValueError("server Alpaca feed configuration is invalid")
    return {
        "key": key,
        "secret": secret,
        "options_feed": options_feed,
        "stock_feed": stock_feed,
    }


@contextmanager
def service_provider_session(core_url: str):
    """Temporarily expose server-side Alpaca credentials to the hosted core.

    The core intentionally requires an opaque provider session in hosted mode.
    The scheduler creates a guest-scoped in-memory session over loopback, uses
    it for its read-only scan, and disconnects it before returning. Credentials
    never enter the plan, audit trace, executor payload, or browser.
    """
    if not os.environ.get("CIPHER_INTERNAL_PROXY_TOKEN"):
        raise ValueError("internal proxy token is not configured")
    credentials = _server_provider_credentials()
    result = request_json(
        f"{core_url.rstrip('/')}/internal/provider-session",
        payload={"action": "connect", **credentials},
        timeout=30,
    )
    session_id = str(result.get("provider_session_id") or "")
    if not session_id:
        raise ValueError("hosted core did not return a provider session")
    previous = os.environ.get("CIPHER_PROVIDER_SESSION")
    os.environ["CIPHER_PROVIDER_SESSION"] = session_id
    try:
        yield session_id
    finally:
        try:
            request_json(
                f"{core_url.rstrip('/')}/internal/provider-session",
                payload={"action": "disconnect", "provider_session_id": session_id},
                timeout=30,
            )
        except Exception:
            # Cleanup must not turn a completed paper observation into a failed
            # scheduler result; the core session also has bounded expiry.
            pass
        if previous is None:
            os.environ.pop("CIPHER_PROVIDER_SESSION", None)
        else:
            os.environ["CIPHER_PROVIDER_SESSION"] = previous


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
    premarket_entry: bool | None = None,
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
        premarket_entry_mode = premarket_entry_enabled(premarket_entry)
        try:
            with service_provider_session(core_url):
                universe = discovery_universe(core_url)
                scan = request_json(scanner_url(core_url, "cipher", universe, workers=1), timeout=900)
                sentiment = sentiment_context(universe, as_of=now)
                plan = build_premarket_plan(
                    scan, now=now, sentiment=sentiment,
                    premarket_entry_allowed=premarket_entry_mode,
                )
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            # The timer retries at 09:15 ET. Preserve any prior artifact for
            # auditability, but never treat it as today's plan and never turn a
            # transient provider/session failure into a scheduler traceback.
            status.update({
                "action": "premarket_plan_unavailable",
                "reason": "premarket_provider_unavailable",
                "retryable": True,
                "error_type": type(exc).__name__,
            })
        else:
            _atomic_json(plan_path, plan)
            submission: dict[str, Any] = {"premarket_entries": 0}
            if premarket_entry_mode:
                # Opt-in premarket-entry mode: the ranked plan candidates are
                # submitted to the paper executor directly from the fresh
                # premarket setup. The executor re-checks setup/ticker/portfolio
                # gates and still only ever writes to the simulated book.
                try:
                    payload = premarket_payload(plan, scan, now=now)
                    if payload["cards"]:
                        ensure_executor_market_data(executor_url, str(payload["cards"][0]["ticker"]))
                        accepted = request_json(executor_url, payload=payload, timeout=30)
                        submission.update({
                            "premarket_entries": len(payload["cards"]),
                            "premarket_batch_id": accepted.get("batch_id"),
                            "premarket_rejected": len(payload["rejected"]),
                            "premarket_tickers": [row.get("ticker") for row in payload["cards"]],
                            "premarket_rejection_reason_counts": _reason_counts(payload["rejected"]),
                        })
                    else:
                        submission.update({
                            "premarket_entries": 0,
                            "premarket_rejected": payload["rejected"],
                            "premarket_rejection_reason_counts": _reason_counts(payload["rejected"]),
                        })
                except (urllib.error.URLError, TimeoutError, ValueError) as exc:
                    submission.update({
                        "premarket_entries": 0,
                        "premarket_submission_error": type(exc).__name__,
                        "premarket_submission_retryable": True,
                    })
            status.update({
                "action": "premarket_plan_saved",
                "plan_id": plan["plan_id"],
                "universe_size": len(universe),
                "candidates": len(plan["candidates"]),
                "rejected": len(plan["rejected"]),
                "premarket_entry_mode": premarket_entry_mode,
                "candidate_tickers": [row["ticker"] for row in plan["candidates"]],
                "rejection_reason_counts": _reason_counts(plan["rejected"]),
                **submission,
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
                # Primary confirmation is the regular-session Cipher scan; the
                # Flash and Flash-Agentic scans merge only tickers the primary
                # scan did not confirm. Each scan failure is recorded and the
                # cycle continues with whatever strategies were available.
                confirm_strategies = ("cipher", "flash", "flash_agentic")
                scans: list[dict[str, Any]] = []
                scan_errors: list[str] = []
                session_error: dict[str, Any] | None = None
                try:
                    with service_provider_session(core_url):
                        for strategy in confirm_strategies:
                            try:
                                scans.append(request_json(
                                    scanner_url(core_url, strategy, tickers, workers=1), timeout=600
                                ))
                            except (urllib.error.URLError, TimeoutError, ValueError) as exc:
                                scan_errors.append(f"{strategy}:{type(exc).__name__}")
                except (urllib.error.URLError, TimeoutError, ValueError, RuntimeError) as exc:
                    # Symmetric with the premarket branch: a missing proxy token or
                    # a failed provider-session connect is a retryable blocked
                    # cycle, never a scheduler traceback.
                    session_error = {
                        "action": "blocked", "reason": "premarket_provider_unavailable",
                        "retryable": True, "error_type": type(exc).__name__,
                    }
                if session_error is not None:
                    status.update(session_error)
                else:
                    payload = confirmation_payload(plan, scans, now=now)
                    if scan_errors:
                        status["scan_errors"] = scan_errors
                    if payload["cards"]:
                        try:
                            ensure_executor_market_data(executor_url, str(payload["cards"][0]["ticker"]))
                            accepted = request_json(executor_url, payload=payload, timeout=30)
                        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
                            status.update({
                                "action": "blocked", "reason": "executor_market_data_unavailable",
                                "error_type": type(exc).__name__, "confirmed": 0,
                                "rejected": len(payload["rejected"]), "plan_id": plan.get("plan_id"),
                            })
                        else:
                            status.update({
                                "action": "paper_confirmations_submitted",
                                "plan_id": plan.get("plan_id"),
                                "confirmed": len(payload["cards"]),
                                "rejected": len(payload["rejected"]),
                                "batch_id": accepted.get("batch_id"),
                                "confirmed_tickers": [row.get("ticker") for row in payload["cards"]],
                                "rejection_reason_counts": _reason_counts(payload["rejected"]),
                                "confirmation_sources": payload.get("confirmation_sources") or [],
                                "scan_types": payload.get("scan_types") or [],
                            })
                    else:
                        status.update({
                            "action": "no_confirmed_entries",
                            "plan_id": plan.get("plan_id"),
                            "confirmed": 0,
                            "rejected": payload["rejected"],
                            "rejection_reason_counts": _reason_counts(payload["rejected"]),
                            "confirmation_sources": payload.get("confirmation_sources") or [],
                            "scan_types": payload.get("scan_types") or [],
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
    parser.add_argument(
        "--premarket-entry", action="store_true", default=None,
        help="submit ranked plan candidates to the paper executor from the premarket "
        "setup alone (default: CIPHER_AUTOPILOT_PREMARKET_ENTRY env var)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    phase = AutopilotPhase(args.force_phase) if args.force_phase else None
    result = run_cycle(
        now=datetime.now(timezone.utc), core_url=args.core_url, executor_url=args.executor_url,
        plan_path=args.plan_path, status_path=args.status_path, force_phase=phase,
        premarket_entry=args.premarket_entry,
    )
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("action") != "blocked" else 2


if __name__ == "__main__":
    raise SystemExit(main())
