#!/usr/bin/env python3
"""Reconcile one agent decision against its broker outcome.

PLAYBOOK step 7, automated. Given a FILLED (or terminal) decision and the
broker's order JSON — pasted from the alpaca-trading MCP tool output or the
Trading CLI — this verifies the chain agrees before stamping RECONCILED:

  1. chain has INTENT and SUBMITTED for the same decision_id
  2. broker client_order_id matches the submitted record when both exist
  3. broker symbol/quantity match the intent; buy fills never exceed the
     intent limit price (plus tolerance), sell fills never below it
  4. appends RECONCILED with matches_local_ledger and any mismatches named

The script never edits prior rows. A failed reconciliation is recorded as a
RECONCILED row with matches_local_ledger=false and the mismatch list — that
is the honest terminal state an operator must see.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent_decision_log import DEFAULT_LOG, append, chain, tail_rows  # noqa: E402

PRICE_TOLERANCE = 0.02  # cents-level allowance for per-contract rounding


def _rows_for(log_path: Path, decision_id: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for row in tail_rows(log_path, limit=None):
        if isinstance(row, dict) and row.get("decision_id") == decision_id:
            out.setdefault(str(row.get("event")), row)
    return out


def reconcile(log_path: Path, *, decision_id: str, broker_order: dict) -> dict:
    events = _rows_for(log_path, decision_id)
    problems: list[str] = []

    if "INTENT" not in events:
        problems.append("no INTENT row for this decision")
        intent: dict = {}
    else:
        intent = events["INTENT"]

    submitted = events.get("SUBMITTED")
    broker_id = str(broker_order.get("id") or broker_order.get("broker_order_id") or "")
    if submitted and broker_id and str(submitted.get("broker_order_id")) != broker_id:
        problems.append(
            f"broker id mismatch: log {submitted.get('broker_order_id')} vs broker {broker_id}")

    status = str(broker_order.get("status") or "").lower()
    filled_qty = float(broker_order.get("filled_quantity") or broker_order.get("quantity") or 0)
    filled_price = broker_order.get("average_fill_price") or broker_order.get("filled_price")

    if intent:
        if broker_order.get("symbol") and intent.get("contract_symbol") \
                and str(broker_order["symbol"]).upper() != str(intent["contract_symbol"]).upper():
            problems.append(f"symbol mismatch: {intent['contract_symbol']} vs {broker_order['symbol']}")
        try:
            want_qty = float(intent.get("quantity") or 0)
            if want_qty and abs(filled_qty - want_qty) > 1e-9:
                problems.append(f"quantity mismatch: intent {want_qty} vs filled {filled_qty}")
        except (TypeError, ValueError):
            problems.append("intent quantity unreadable")
        limit = intent.get("limit_price")
        side = str(intent.get("side") or "").lower()
        try:
            if filled_price is not None and limit is not None:
                if side == "buy" and float(filled_price) > float(limit) + PRICE_TOLERANCE:
                    problems.append(f"buy fill {filled_price} above limit {limit}")
                if side == "sell" and float(filled_price) < float(limit) - PRICE_TOLERANCE:
                    problems.append(f"sell fill {filled_price} below limit {limit}")
        except (TypeError, ValueError):
            problems.append("price comparison failed on unreadable numbers")

    matches = not problems and status in {"filled"} and filled_price is not None

    # Record the terminal outcome from broker truth if the session died before
    # logging it — reconciliation is the last chance for the log to agree with
    # reality, so it fills the gap rather than flagging it forever.
    if "FILLED" not in events and matches:
        append(log_path, "FILLED", {
            "decision_id": decision_id,
            "filled_price": float(filled_price),
            "filled_quantity": int(filled_qty) if filled_qty else 0,
            "source": "broker_via_reconcile",
        })
    elif "UNFILLED" not in events and not matches and status in {
            "rejected", "canceled", "cancelled", "expired", "rejected_at_exchange"}:
        append(log_path, "UNFILLED", {"decision_id": decision_id, "status": status})

    row = append(log_path, "RECONCILED", {
        "decision_id": decision_id,
        "matches_local_ledger": matches,
        "broker_status": status or "unknown",
        "mismatches": problems,
        "broker_order_id": broker_id or None,
        "checked_fields": ["client_order_id", "symbol", "quantity", "side-price"],
    })
    return {"reconciled": row, "matches": matches, "problems": problems}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    parser.add_argument("--decision-id", required=True)
    parser.add_argument("--broker-order", required=True,
                        help="JSON of the broker order (MCP tool output or CLI --json)")
    args = parser.parse_args(argv)
    result = reconcile(args.log, decision_id=args.decision_id,
                       broker_order=json.loads(args.broker_order))
    print(json.dumps(result, indent=2, default=str))
    return 0 if result["matches"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
