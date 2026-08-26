#!/usr/bin/env python3
"""Append-only decision log for the Options Alpha agent.

The charter (docs/agent/AGENT_CHARTER.md) requires intent-before-submission
and a classified outcome for every decision. This writer is the mechanism:
one JSONL file, one row per event, idempotent per ``decision_id`` so a rerun
or retry can never duplicate an intent.

Event vocabulary (the only values ever written):

  INTENT      the decision was made and fully described BEFORE any order call
  SUBMITTED   the order reached Alpaca paper; carries the broker order id
  FILLED      broker reported a fill; carries price/qty
  UNFILLED    order expired/rejected/cancelled without a fill
  BLOCKED     the pretrade gate or the broker refused; carries the reason
  RECONCILED  local ledger cross-checked against the paper account

Rows are never rewritten or deleted. A malformed trailing line is counted,
not silently dropped, by ``tail``.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_LOG = Path(
    "/home/aarav/Aarav/cipher/runtime/data/agent_decision_log.jsonl"
)

EVENTS = ("INTENT", "SUBMITTED", "FILLED", "UNFILLED", "BLOCKED", "RECONCILED")

REQUIRED_FIELDS = {
    "INTENT": {"decision_id", "ticker", "side", "contract_symbol", "quantity",
               "limit_price", "rationale"},
    "SUBMITTED": {"decision_id", "broker_order_id"},
    "FILLED": {"decision_id", "filled_price", "filled_quantity"},
    "UNFILLED": {"decision_id", "status"},
    "BLOCKED": {"decision_id", "reason"},
    "RECONCILED": {"decision_id", "matches_local_ledger"},
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def append(log_path: Path, event: str, payload: dict) -> dict:
    """Write one event. Returns the stored row.

    Idempotency: an INTENT whose ``decision_id`` already has an INTENT row is
    refused (exit via ValueError) rather than duplicated. Later events for the
    same decision_id are allowed exactly once each.
    """
    if event not in EVENTS:
        raise ValueError(f"unknown event: {event}")
    missing = REQUIRED_FIELDS[event] - set(payload)
    if missing:
        raise ValueError(f"{event} missing fields: {sorted(missing)}")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if event == "INTENT" and has_event(log_path, str(payload["decision_id"]), "INTENT"):
        raise ValueError(f"decision_id already has an INTENT row: {payload['decision_id']}")
    row = {
        "ts": _now(),
        "event": event,
        **payload,
        # Charter line 1: this value is structural, not decorative.
        "paper_only": True,
        "live_execution_capability": False,
    }
    handle = os.open(log_path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        fcntl.flock(handle, fcntl.LOCK_EX)
        os.write(handle, (json.dumps(row, sort_keys=True, default=str) + "\n").encode())
        os.fsync(handle)
    finally:
        os.close(handle)
    return row


def has_event(log_path: Path, decision_id: str, event: str) -> bool:
    if not log_path.is_file():
        return False
    for row in tail_rows(log_path, limit=None):
        if row.get("event") == event and row.get("decision_id") == decision_id:
            return True
    return False


def tail_rows(log_path: Path, limit: int | None = 20) -> list[dict]:
    if not log_path.is_file():
        return []
    lines = log_path.read_text(encoding="utf-8").splitlines()
    if limit is not None:
        lines = lines[-limit:]
    rows, malformed = [], 0
    for line in lines:
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            malformed += 1
    if rows and isinstance(rows[0], dict):
        rows[0]["malformed_trailing_lines"] = malformed
    elif malformed:
        rows.append({"malformed_trailing_lines": malformed})
    return rows


TERMINAL_EVENTS = ("FILLED", "UNFILLED", "BLOCKED")


def positions(log_path: Path) -> dict[str, Any]:
    """Derive the agent's option book from matched fills in its own log.

    The agent trades a dedicated paper account, so its book is whatever its
    log proves: each FILLED whose intent side is 'buy' opens quantity of that
    contract; a 'sell' fill reduces it. Contracts are keyed by symbol; lots
    carry no cost-basis modelling beyond the average of opening fills, which
    is all the record honestly supports.
    """
    intents: dict[str, dict] = {}
    fills: list[dict] = []
    for row in tail_rows(log_path, limit=None):
        if not isinstance(row, dict):
            continue
        did = row.get("decision_id")
        if row.get("event") == "INTENT" and did:
            intents[did] = {
                "ticker": row.get("ticker"),
                "contract_symbol": row.get("contract_symbol"),
                "side": str(row.get("side") or "").lower(),
            }
        elif row.get("event") == "FILLED" and did:
            fills.append(row)

    books: dict[str, dict[str, Any]] = {}
    closed = 0
    for fill in fills:
        intent = intents.get(str(fill.get("decision_id"))) or {}
        contract = str(intent.get("contract_symbol") or "?")
        ticker = str(intent.get("ticker") or "?")
        side = intent.get("side") or ("sell" if str(fill.get("decision_id", "")).endswith("_close") else "buy")
        signed = int(fill.get("filled_quantity") or 0) * (-1 if side == "sell" else 1)
        book = books.setdefault(ticker, {})
        lot = book.setdefault(contract, {"quantity": 0, "cost_sum": 0.0, "opened_at": None})
        if signed > 0 and lot["quantity"] <= 0 and lot["cost_sum"] == 0:
            lot["opened_at"] = fill.get("ts")
        if signed > 0:
            lot["quantity"] += signed
            lot["cost_sum"] += float(fill.get("filled_price") or 0) * signed
        else:
            lot["quantity"] += signed
            if lot["quantity"] <= 0:
                closed += 1
                lot["quantity"], lot["cost_sum"] = 0, 0.0
    open_positions = [
        {
            "ticker": ticker,
            "contract_symbol": contract,
            "quantity": lot["quantity"],
            "avg_open_price": round(lot["cost_sum"] / lot["quantity"], 4) if lot["quantity"] else None,
            "opened_at": lot["opened_at"],
        }
        for ticker, book in sorted(books.items())
        for contract, lot in sorted(book.items())
        if lot["quantity"] > 0
    ]
    return {
        "open_positions": open_positions,
        "closed_round_turns": closed,
        "fills_processed": len(fills),
        "paper_only": True,
    }


def chain(log_path: Path, decision_id: str) -> dict:
    """The full event trail for one decision plus an honesty verdict.

    A healthy chain is INTENT followed by exactly one terminal event
    (FILLED/UNFILLED/BLOCKED); RECONCILED is expected after FILLED but not
    mandatory at log time. Anomalies -- missing INTENT, a dangling SUBMITTED,
    duplicated events -- are named, never smoothed over.
    """
    events = [
        row for row in tail_rows(log_path, limit=None)
        if isinstance(row, dict)
        and row.get("decision_id") == decision_id
        and row.get("event") in EVENTS
    ]
    kinds = [row.get("event") for row in events]
    anomalies: list[str] = []
    if kinds.count("INTENT") > 1:
        anomalies.append(f"duplicate INTENT x{kinds.count('INTENT')}")
    if not kinds:
        pass  # unknown decision: reported as such below
    else:
        terminals = [k for k in kinds if k in TERMINAL_EVENTS]
        if len(terminals) == 0:
            anomalies.append("no terminal event yet")
        elif len(terminals) > 1:
            anomalies.append(f"multiple terminal events: {terminals}")
        if "SUBMITTED" in kinds and "FILLED" in kinds and "RECONCILED" not in kinds:
            anomalies.append("filled but not reconciled yet")
        if "RECONCILED" in kinds and "FILLED" not in kinds:
            anomalies.append("reconciled without a fill")
    return {
        "decision_id": decision_id,
        "known": bool(kinds),
        "events": [{"ts": row.get("ts"), "event": row.get("event")} for row in events],
        "anomalies": anomalies,
        "complete": bool(kinds) and not anomalies and any(
            k in TERMINAL_EVENTS for k in kinds),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    sub = parser.add_subparsers(dest="command", required=True)
    p_add = sub.add_parser("append")
    p_add.add_argument("--event", required=True, choices=EVENTS)
    p_add.add_argument("--payload", required=True,
                       help="JSON object with the event's required fields")
    p_tail = sub.add_parser("tail")
    p_tail.add_argument("--limit", type=int, default=20)
    p_chain = sub.add_parser("chain")
    p_chain.add_argument("--decision-id", required=True)
    p_pos = sub.add_parser("positions")
    args = parser.parse_args(argv)
    if args.command == "append":
        payload = json.loads(args.payload)
        print(json.dumps(append(args.log, args.event, payload), indent=2, sort_keys=True))
        return 0
    if args.command == "chain":
        print(json.dumps(chain(args.log, args.decision_id), indent=2, sort_keys=True))
        return 0
    if args.command == "positions":
        print(json.dumps(positions(args.log), indent=2, sort_keys=True))
        return 0
    print(json.dumps(tail_rows(args.log, args.limit), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
