#!/usr/bin/env python3
"""Deterministic pre-trade gate for the Options Alpha agent.

The agent session may reason about anything, but it may only place an order
after this script answers PASS. Every check is a plain fact about local state
-- no model judgement is involved -- so a PASS is reproducible and an
otherwise-identical rerun cannot disagree.

Checks, in order:
  1. kill switch absent          (runtime STOP_PAPER_EXECUTOR sentinel)
  2. US regular session          (Mon-Fri, 09:30-16:00 America/New_York)
  3. portfolio limits            (open positions < maximum; ticker not held;
                                  daily new-position budget remaining)
  4. no duplicate pending intent (decision log has no INTENT for this id)

Exit codes: 0 = PASS, 2 = BLOCKED. Either way the JSON verdict on stdout is
exactly what the agent should append to its decision log on BLOCKED.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

KILL_SWITCH = Path(
    "/home/aarav/Aarav/cipher/runtime/data/paper_runtime/STOP_PAPER_EXECUTOR"
)
LEDGER = Path(
    "/home/aarav/Aarav/cipher/runtime/data/paper_runtime/data/paper_trades/autopilot_shadow.sqlite"
)
DECISION_LOG = Path("/home/aarav/Aarav/cipher/runtime/data/agent_decision_log.jsonl")

NEW_YORK = ZoneInfo("America/New_York")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.paper_executor.config import load_config  # noqa: E402
from core.exchange_calendar import is_session  # noqa: E402

_POLICY = load_config(Path(__file__).resolve().parents[1] / "config" / "paper_autopilot_shadow.yaml")
MAX_OPEN_POSITIONS = _POLICY.portfolio.maximum_open_positions
MAX_POSITIONS_PER_TICKER = _POLICY.portfolio.maximum_positions_per_ticker
MAX_NEW_POSITIONS_PER_DAY = _POLICY.portfolio.maximum_new_positions_per_day
MAX_CONTRACT_COST_USD = _POLICY.contract.maximum_contract_cost

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent_decision_log import has_event  # noqa: E402


def _session_is_open(now: datetime | None = None) -> tuple[bool, str]:
    """US equity regular session, computed locally from the ET wall clock.

    Half-days are treated as full sessions: a false positive only widens the
    window in which the portfolio gates still apply.
    """
    local = (now or datetime.now(datetime.now().astimezone().tzinfo)).astimezone(NEW_YORK)
    if not is_session(local.date()):
        return False, f"{local:%A} — market closed"
    minutes = local.hour * 60 + local.minute
    if minutes < 9 * 60 + 30:
        return False, f"pre-open ({local:%H:%M} ET)"
    if minutes >= 16 * 60:
        return False, f"after close ({local:%H:%M} ET)"
    return True, f"regular session ({local:%H:%M} ET)"


def _portfolio_state(ticker: str) -> dict:
    db = sqlite3.connect(f"file:{LEDGER}?mode=ro", uri=True, timeout=5)
    try:
        open_positions = db.execute(
            "select ticker from paper_positions where status in ('OPEN','SHADOW_OPEN')"
        ).fetchall()
        today = datetime.now(timezone.utc).date().isoformat()
        opened_today = db.execute(
            "select count(*) from paper_positions where substr(opened_at,1,10) = ?",
            (today,),
        ).fetchone()[0]
    finally:
        db.close()
    tickers_held = {row[0].upper() for row in open_positions}
    return {
        "open_count": len(open_positions),
        "ticker_already_held": ticker.upper() in tickers_held,
        "opened_today": opened_today,
    }


def evaluate(*, decision_id: str, ticker: str, limit_price: float | None = None,
             quantity: int = 1) -> dict:
    checks: list[dict] = []
    blocked_reason = None

    if KILL_SWITCH.exists():
        blocked_reason = "SKIPPED_KILL_SWITCH"
        checks.append({"check": "kill_switch", "ok": False})
    else:
        checks.append({"check": "kill_switch", "ok": True})

    # Contract cost cap mirrors the executor's maximum_contract_cost: a
    # single contract must not exceed the per-trade budget. Checked on the
    # limit price the agent actually intends to pay.
    cost = None
    if limit_price is not None:
        try:
            cost = round(float(limit_price) * 100 * int(quantity), 2)
            cost_ok = 0 < cost <= MAX_CONTRACT_COST_USD
        except (TypeError, ValueError):
            cost_ok = False
        checks.append({"check": "contract_cost", "ok": cost_ok,
                       "detail": f"${cost} <= ${MAX_CONTRACT_COST_USD:.0f}"})
        if not cost_ok and not blocked_reason:
            blocked_reason = "SKIPPED_MAX_COST"

    is_open, session_note = _session_is_open()
    checks.append({"check": "market_open", "ok": is_open, "detail": session_note})
    if not is_open and not blocked_reason:
        blocked_reason = f"BLOCKED_MARKET_CLOSED ({session_note})"

    state = _portfolio_state(ticker)
    checks.append({"check": "max_open_positions", "ok": state["open_count"] < MAX_OPEN_POSITIONS,
                   "detail": f"{state['open_count']}/{MAX_OPEN_POSITIONS}"})
    checks.append({"check": "ticker_position_limit", "ok": not state["ticker_already_held"]})
    checks.append({"check": "daily_new_positions", "ok": state["opened_today"] < MAX_NEW_POSITIONS_PER_DAY,
                   "detail": f"{state['opened_today']}/{MAX_NEW_POSITIONS_PER_DAY}"})
    if state["open_count"] >= MAX_OPEN_POSITIONS and not blocked_reason:
        blocked_reason = "SKIPPED_MAX_POSITIONS"
    if state["ticker_already_held"] and not blocked_reason:
        blocked_reason = "SKIPPED_POSITION_EXISTS"
    if state["opened_today"] >= MAX_NEW_POSITIONS_PER_DAY and not blocked_reason:
        blocked_reason = "SKIPPED_DAILY_LIMIT"

    duplicate = has_event(DECISION_LOG, decision_id, "INTENT")
    checks.append({"check": "no_duplicate_intent", "ok": not duplicate})
    if duplicate and not blocked_reason:
        blocked_reason = "BLOCKED_DUPLICATE_INTENT"

    verdict = {
        "verdict": "PASS" if blocked_reason is None else "BLOCKED",
        "reason": blocked_reason,
        "decision_id": decision_id,
        "ticker": ticker.upper(),
        "checks": checks,
        "paper_only": True,
        "live_execution_capability": False,
    }
    return verdict


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--decision-id", required=True)
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--limit-price", type=float, default=None,
                        help="intended limit per contract; enables the cost cap check")
    parser.add_argument("--quantity", type=int, default=1)
    args = parser.parse_args(argv)
    verdict = evaluate(decision_id=args.decision_id, ticker=args.ticker,
                       limit_price=args.limit_price, quantity=args.quantity)
    print(json.dumps(verdict, indent=2))
    return 0 if verdict["verdict"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
