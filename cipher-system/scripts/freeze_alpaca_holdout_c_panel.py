#!/usr/bin/env python3
"""Freeze the pre-outcome Alpaca SIP recovery panel after three pilot checks."""
from __future__ import annotations
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PILOT = ROOT / "data" / "market_quality" / "alpaca_holdout_c_pilot"
GOV = ROOT / "data" / "governance"
PANEL = ["SPY", "QQQ", "IWM", "XLF", "XLE", "AAPL", "MSFT", "NVDA", "GE"]
PERIODS = {"2017-01-01..2017-03-31", "2018-01-01..2018-03-31", "2019-01-01..2019-03-31"}

def main() -> None:
    evidence = []
    for path in sorted(PILOT.glob("pilot_report_*.json")):
        item = json.loads(path.read_text(encoding="utf-8"))
        if item.get("schema_version") != 2 or item.get("period") not in PERIODS:
            continue
        if not set(PANEL).issubset(item.get("symbols", [])):
            continue
        complete = item["complete_session_counts"]
        if all(complete.get(symbol) == item["regular_sessions_requested"] for symbol in PANEL) and item["ohlc_integrity_failures"] == 0:
            evidence.append({"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "period": item["period"],
                             "regular_sessions": item["regular_sessions_requested"]})
    present = {row["period"] for row in evidence}
    if present != PERIODS:
        raise SystemExit(f"cannot freeze panel; incomplete pilot evidence: {sorted(present)}")
    payload = {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(), "provider": "Alpaca SIP", "feed": "sip",
               "holdout_period": "2017-01 through 2019-12", "panel": PANEL,
               "selection_basis": "fixed before any ranking/outcome evaluation: broad index ETFs, sector ETFs, liquid technology names, and GE corporate-action representation",
               "pilot_evidence": evidence, "minimum_common_tickers_required": 8, "minimum_strict_independent_origins_required": 12,
               "price_only_only": True, "volume_features_or_evaluation": False, "full_volume_reconciled_gate_changed": False,
               "ranking_outcomes_evaluated": False, "live_execution": False}
    GOV.mkdir(parents=True, exist_ok=True)
    output = GOV / f"holdout_c_alpaca_panel_freeze_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(output)

if __name__ == "__main__":
    main()
