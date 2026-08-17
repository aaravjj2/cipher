#!/usr/bin/env python3
"""Validate the curated scanner universe against Alpaca's active asset catalog.

Market-cap tiers remain the curated input; this job only confirms that a symbol
is still an active US asset carrying Alpaca's ``has_options`` attribute.  It
does not silently invent market-cap classifications for newly listed names.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "core"
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

import app  # noqa: E402

UNIVERSE = ROOT / "data" / "optionable_universe_by_cap.json"


def refresh_payload(payload: dict, assets: list[dict], *, as_of: str) -> dict:
    active = {
        str(row.get("symbol") or "").upper()
        for row in assets
        if row.get("status") == "active" and "has_options" in (row.get("attributes") or [])
    }
    tiers = payload.get("sorted_tickers") or {}
    validated = {}
    removed = []
    for tier, symbols in tiers.items():
        kept = []
        for raw in symbols or []:
            symbol = str(raw).upper()
            if symbol in active:
                kept.append(symbol)
            else:
                removed.append({"ticker": symbol, "prior_tier": tier, "reason": "not_active_has_options"})
        validated[tier] = kept
    result = dict(payload)
    result["as_of"] = as_of
    result["sorted_tickers"] = validated
    result["count"] = sum(len(rows) for rows in validated.values())
    result["counts"] = {tier: len(rows) for tier, rows in validated.items()}
    result["validation"] = {
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "provider": "alpaca_assets",
        "criterion": "status=active and attributes includes has_options",
        "prior_count": sum(len(rows or []) for rows in tiers.values()),
        "validated_count": result["count"],
        "removed": removed,
        "addition_policy": "validation only; new symbols require cap-tier classification",
    }
    return result


def main() -> int:
    payload = json.loads(UNIVERSE.read_text(encoding="utf-8"))
    assets = app.alpaca(
        "/v2/assets", {"status": "active", "asset_class": "us_equity"},
        base="https://paper-api.alpaca.markets",
    )
    today = datetime.now(timezone.utc).date().isoformat()
    result = refresh_payload(payload, assets, as_of=today)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=UNIVERSE.parent, delete=False) as handle:
        json.dump(result, handle, indent=2, sort_keys=False)
        handle.write("\n")
        temp = Path(handle.name)
    os.replace(temp, UNIVERSE)
    print(json.dumps({
        "as_of": today, "prior_count": result["validation"]["prior_count"],
        "validated_count": result["count"], "removed": result["validation"]["removed"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
