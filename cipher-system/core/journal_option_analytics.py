"""Exact-contract option mark excursions for manual journal records.

All values are marked from captured Tradier bid/ask/trade context.  They are not
broker fills, and a missing contract/window remains unavailable.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_STREAM_DB = Path(__file__).resolve().parents[1] / "data" / "tradier_stream.sqlite"


def _marks(row) -> dict:
    bid, ask, trade = row[1], row[2], row[3]
    mid = (bid + ask) / 2 if bid is not None and ask is not None and ask >= bid else None
    return {"bid": bid, "mid": mid, "ask": ask, "trade": trade}


def analyze(entry: dict, db_path: Path = DEFAULT_STREAM_DB) -> dict:
    legs = entry.get("legs") or []
    if not legs:
        return {"status": "NO_OPTION_LEGS", "legs": [], "caveat": "Underlying excursion is shown separately."}
    if not entry.get("entry_at"):
        return {"status": "UNAVAILABLE_NO_ENTRY_TIME", "legs": [], "caveat": "Exact mark replay requires an entry time."}
    if not db_path.exists():
        return {"status": "UNAVAILABLE_NO_CAPTURE_DATABASE", "legs": [], "caveat": "No captured option marks are available."}
    end = entry.get("exit_at") or datetime.now(timezone.utc).isoformat()
    output = []
    with sqlite3.connect(db_path) as db:
        for leg in legs:
            symbol = leg["contract_symbol"]
            rows = list(db.execute(
                """select provider_ts,bid,ask,price from tradier_option_timesales
                   where symbol=? and provider_ts>=? and provider_ts<=? order by provider_ts""",
                (symbol, entry["entry_at"], end),
            ))
            if not rows:
                output.append({"contract_symbol": symbol, "status": "NO_CAPTURED_MARKS", "events": 0})
                continue
            first = _marks(rows[0])
            explicit = leg.get("entry_mark")
            if explicit is not None:
                basis, basis_source = float(explicit), str(leg.get("entry_mark_type") or "manual")
            elif leg["side"] == "buy":
                basis, basis_source = first.get("ask") or first.get("mid") or first.get("trade"), "first_captured_ask_or_fallback"
            else:
                basis, basis_source = first.get("bid") or first.get("mid") or first.get("trade"), "first_captured_bid_or_fallback"
            if basis is None or basis <= 0:
                output.append({"contract_symbol": symbol, "status": "NO_ENTRY_MARK", "events": len(rows)})
                continue
            excursions = {}
            sign = 1 if leg["side"] == "buy" else -1
            quantity, multiplier = int(leg["quantity"]), int(leg["multiplier"])
            for kind in ("bid", "mid", "ask", "trade"):
                values = [_marks(row)[kind] for row in rows]
                values = [float(value) for value in values if value is not None]
                returns = [sign * (value - basis) / basis * 100 for value in values]
                dollars = [sign * (value - basis) * quantity * multiplier for value in values]
                excursions[kind] = {
                    "observations": len(values),
                    "mfe_pct": round(max(returns), 4) if returns else None,
                    "mae_pct": round(min(returns), 4) if returns else None,
                    "mfe_dollars": round(max(dollars), 2) if dollars else None,
                    "mae_dollars": round(min(dollars), 2) if dollars else None,
                }
            output.append({
                "contract_symbol": symbol, "side": leg["side"], "quantity": quantity,
                "multiplier": multiplier, "entry_mark": basis, "entry_mark_source": basis_source,
                "status": "CALCULATED_FROM_CAPTURED_MARKS", "events": len(rows),
                "first_mark_at": rows[0][0], "last_mark_at": rows[-1][0],
                "excursions": excursions,
            })
    calculated = sum(row.get("status") == "CALCULATED_FROM_CAPTURED_MARKS" for row in output)
    return {
        "status": "CALCULATED" if calculated == len(legs) else "PARTIAL" if calculated else "UNAVAILABLE",
        "legs": output, "coverage": {"requested_legs": len(legs), "calculated_legs": calculated},
        "caveat": "Captured bid/mid/ask/trade marks are simulated valuation paths, not actual fills. Missing windows are not interpolated.",
    }
