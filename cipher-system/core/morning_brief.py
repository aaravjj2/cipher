"""Small, failure-tolerant morning review assembled from existing Cipher evidence."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError, as_completed
from pathlib import Path
import sqlite3
from typing import Callable

from core import (
    alerts,
    holdings,
    paper_portfolio_api,
    prospective_fronttest_api,
    scan_history,
)

GEX_DB = Path(__file__).resolve().parents[1] / "data" / "gex_history.sqlite"
ALERT_STATE = Path(__file__).resolve().parents[1] / "data" / "alerts" / "market_alert_state.json"
BRIEF_MARKET_BUDGET_SECONDS = 2.0
_MARKET_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="cipher-brief")


def _gex_change(ticker: str) -> dict:
    if not GEX_DB.exists():
        return {"ticker": ticker, "available": False}
    with sqlite3.connect(f"file:{GEX_DB}?mode=ro", uri=True, timeout=1.0) as db:
        rows = db.execute(
            "select id,captured_at from gex_snapshots where ticker=? order by captured_at desc limit 2",
            (ticker.upper(),),
        ).fetchall()
        values = []
        for snapshot_id, captured_at in rows:
            net = db.execute(
                "select sum(net_gex) from gex_strike_cells where snapshot_id=? and net_gex is not null",
                (snapshot_id,),
            ).fetchone()[0]
            values.append({"captured_at": captured_at, "net_gex": net})
    current = values[0] if values else None
    prior = values[1] if len(values) > 1 else None
    return {
        "ticker": ticker.upper(), "available": bool(current and current["net_gex"] is not None),
        "current": current, "prior": prior,
        "change": (
            float(current["net_gex"]) - float(prior["net_gex"])
            if current and prior and current["net_gex"] is not None and prior["net_gex"] is not None else None
        ),
        "caveat": "Public-OI GEX heuristic, not verified dealer positioning.",
    }


def _alert_states() -> dict:
    try:
        states = json.loads(ALERT_STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        states = {}
    configured = alerts.list_rules()
    for rule in configured.get("rules", []):
        rule["evaluation"] = states.get(rule["id"])
    return configured


def build(*, ticker: str, quote_fn: Callable[[str], dict], flow_fn: Callable[..., dict],
          status_payload: dict) -> dict:
    market_by_symbol = {}
    errors = []
    symbols = ("SPY", "QQQ", "IWM")
    pending = {_MARKET_EXECUTOR.submit(quote_fn, symbol): symbol for symbol in symbols}
    try:
        for future in as_completed(pending, timeout=BRIEF_MARKET_BUDGET_SECONDS):
            symbol = pending[future]
            try:
                q = future.result()
                market_by_symbol[symbol] = {
                    "ticker": symbol, "price": q.get("price_context"),
                    "day_change_pct": q.get("day_change_pct"), "as_of": q.get("as_of"),
                    "feed": q.get("feed"), "availability": q.get("availability"),
                }
            except Exception as exc:
                errors.append({"section": "market", "ticker": symbol, "error": str(exc)})
    except FutureTimeoutError:
        pass
    for future, symbol in pending.items():
        if symbol not in market_by_symbol:
            future.cancel()
            market_by_symbol[symbol] = {
                "ticker": symbol, "price": None, "day_change_pct": None,
                "as_of": None, "feed": "unavailable",
                "availability": {"status": "refreshing", "reason": "refresh_pending"},
            }
    market = [market_by_symbol[symbol] for symbol in symbols]
    try:
        flow = flow_fn(ticker, None, min_premium=100_000)
        significant_flow = {
            "ticker": ticker.upper(), "as_of": flow.get("as_of"),
            "source": flow.get("source"), "session_date": flow.get("session_date"),
            "prints": (flow.get("prints") or [])[:8], "caveat": flow.get("caveat"),
            "freshness": flow.get("freshness"),
            "availability": flow.get("availability") or {"status": "available"},
            "coverage": flow.get("coverage"),
        }
    except Exception as exc:
        errors.append({"section": "flow", "ticker": ticker.upper(), "error": str(exc)})
        significant_flow = {
            "ticker": ticker.upper(), "prints": [],
            "availability": {"status": "unavailable", "reason": "provider_error"},
            "freshness": {"status": "unknown", "age_seconds": None},
        }
    try:
        paper = paper_portfolio_api.snapshot()
    except Exception as exc:
        errors.append({"section": "paper_portfolios", "error": str(exc)})
        paper = {"portfolios": [], "combined_realized_pnl": None}
    try:
        prospective = prospective_fronttest_api.snapshot(recent_limit=100)
    except Exception as exc:
        errors.append({"section": "prospective_fronttests", "error": str(exc)})
        prospective = {
            "as_of": None,
            "programs": [],
            "signals": [],
            "observations": [],
            "latest_coverage": {
                "run_id": None, "observed": 0, "fresh": 0, "partial": 0,
                "stale": 0, "missing": 0, "signals_opened": 0,
            },
        }
    scans = scan_history.list_scans(limit=5)
    try:
        manual_holdings = holdings.holdings_status(quote_fn=quote_fn)
    except Exception as exc:
        errors.append({"section": "holdings", "error": str(exc)})
        manual_holdings = {"positions": [], "unresolved": []}
    coverage = prospective.get("latest_coverage") or {}
    latest_run_id = coverage.get("run_id")
    latest_observations = [
        row for row in prospective.get("observations", [])
        if row.get("run_id") == latest_run_id
    ]
    open_signals = [
        row for row in prospective.get("signals", []) if row.get("status") == "OPEN"
    ][:12]
    void_signals = [
        row for row in prospective.get("signals", []) if row.get("status") == "VOID"
    ]
    all_exceptions = status_payload.get("exceptions", []) + errors
    attention = []
    for item in all_exceptions:
        name = item.get("name") or item.get("section") or "data input"
        state = item.get("state") or "error"
        age = item.get("age_seconds")
        state_detail = f"Input state is {state}"
        if isinstance(age, (int, float)):
            state_detail += f" ({int(age)}s old)"
        caveat = item.get("detail")
        detail = item.get("error") or (
            f"{state_detail}. {caveat}" if caveat else f"{state_detail}."
        )
        attention.append({
            "severity": "error" if state in {"unavailable", "error"} else "warning",
            "kind": "data_exception", "title": str(name).replace("_", " ").title(),
            "detail": detail, "ticker": item.get("ticker"),
        })
    nonfresh = (
        int(coverage.get("partial") or 0)
        + int(coverage.get("stale") or 0)
        + int(coverage.get("missing") or 0)
    )
    if latest_run_id is not None and nonfresh:
        attention.append({
            "severity": "warning", "kind": "prospective_coverage",
            "title": "Prospective coverage is incomplete",
            "detail": (
                f"Run {latest_run_id}: {coverage.get('partial', 0)} partial, "
                f"{coverage.get('stale', 0)} stale, {coverage.get('missing', 0)} missing."
            ),
        })
    if void_signals:
        attention.append({
            "severity": "warning", "kind": "integrity_exclusion",
            "title": f"{len(void_signals)} prospective signal{'s' if len(void_signals) != 1 else ''} excluded",
            "detail": "Preserved in the audit ledger but excluded from eligible results.",
        })
    return {
        "generated_at": status_payload["generated_at"], "ticker": ticker.upper(),
        "session": status_payload["session"], "freshness": status_payload,
        "market": market, "recent_scans": scans,
        "significant_flow": significant_flow,
        "gex_change": _gex_change(ticker),
        "alerts": _alert_states(),
        "holdings": manual_holdings,
        "paper_portfolios": {
            "as_of": paper.get("as_of"),
            "combined_equity": paper.get("combined_equity"),
            "combined_marked_equity": paper.get("combined_marked_equity"),
            "combined_liquidation_equity": paper.get("combined_liquidation_equity"),
            "combined_unrealized_pnl_mid": paper.get("combined_unrealized_pnl_mid"),
            "daily_realized_pnl": paper.get("daily_realized_pnl"),
            "combined_realized_pnl": paper.get("combined_realized_pnl"),
            "portfolios": [{k: row.get(k) for k in (
                "portfolio_id", "strategy", "realized_equity", "realized_pnl",
                "marked_equity", "liquidation_equity", "unrealized_pnl_mid",
                "closed_trades", "wins", "open_positions", "risk_state",
            )} for row in paper.get("portfolios", [])],
        },
        "prospective_fronttests": {
            "as_of": prospective.get("as_of"),
            "latest_coverage": coverage,
            "programs": [{k: row.get(k) for k in (
                "program_id", "name", "kind", "effective_status", "minimum_sample",
                "eligible_signals", "open_signals", "closed_signals", "void_signals",
                "wins", "sample_progress", "closed_option_pnl",
            )} for row in prospective.get("programs", [])],
            "open_signals": [{k: row.get(k) for k in (
                "signal_id", "program_id", "ticker", "setup_id", "direction",
                "signal_bar_at", "underlying_entry", "target", "deadline_at",
                "option_selection_status",
            )} for row in open_signals],
            "latest_observations": [{k: row.get(k) for k in (
                "program_id", "ticker", "observed_at", "latest_bar_at",
                "coverage_status", "decision", "reason",
            )} for row in latest_observations],
            "paper_only": True,
            "execution_capability": False,
        },
        "attention": attention,
        "exceptions": all_exceptions,
        "read_only": True,
    }
