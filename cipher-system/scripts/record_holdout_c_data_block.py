#!/usr/bin/env python3
"""Synchronize the research registry with the evidence-only Holdout C block."""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "data" / "governance" / "research_status_registry.json"
QUALITY = ROOT / "data" / "market_quality"

def main() -> None:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    closeout = sorted(QUALITY.glob("alpaca_holdout_c_recovery_closeout_*.json"))[-1]
    sources = sorted(QUALITY.glob("holdout_c_alternate_source_entitlements_*.json"))[-1]
    artifact_names = [f"market_quality/{closeout.name}", f"market_quality/{sources.name}"]
    for component in registry["components"]:
        if component["name"] == "momentum_20":
            component["reason"] = "positive comparator in prior data; Holdout C remains unavailable because every accessible single-source recovery failed frozen continuity/origin requirements"
            component["conditions_before_reconsideration"] = "a single qualifying historical source must first produce a pre-registered Holdout C with >=8 common tickers and >=12 strict-independent origins"
            component["supporting_artifacts"] = sorted(set(component["supporting_artifacts"] + artifact_names))
    registry["promotion_eligible"] = False
    registry["live_execution"] = False
    registry["updated_at"] = datetime.now(timezone.utc).isoformat()
    REGISTRY.write_text(json.dumps(registry, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(REGISTRY)

if __name__ == "__main__":
    main()
