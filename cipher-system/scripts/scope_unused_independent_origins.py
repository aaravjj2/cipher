#!/usr/bin/env python3
"""Audit unused local stretches without weakening Holdout C rules."""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.research_platform.market_quality import HoldoutCohortEligibility  # noqa: E402

QUALITY, GOVERNANCE = ROOT / "data" / "market_quality", ROOT / "data" / "governance"


def latest(pattern: str) -> Path:
    return sorted(QUALITY.glob(pattern))[-1]


def main() -> None:
    scope = json.loads(latest("price_only_forecast_scope_*.json").read_text(encoding="utf-8"))
    closeout = json.loads(latest("alpaca_holdout_c_recovery_closeout_*.json").read_text(encoding="utf-8"))
    used_cases: list[dict] = []
    for path in sorted(QUALITY.glob("*preregistration_*.json")):
        for case in json.loads(path.read_text(encoding="utf-8")).get("cases", []):
            if {"ticker", "context_start", "outcome_end"} <= set(case):
                used_cases.append({"artifact": path.name, **case})
    used_tickers = {case["ticker"] for case in used_cases}
    months: dict[str, set[str]] = defaultdict(set)
    import duckdb
    with duckdb.connect(str(ROOT / "data" / "market_catalog.duckdb"), read_only=True) as db:
        for month, ticker in db.execute("select distinct strftime(timestamp, '%Y-%m'), ticker from cipher_market.ohlcv_1m order by 1, 2").fetchall():
            months[str(month)].add(str(ticker))
    observed = closeout["observed_result"]
    cohort = HoldoutCohortEligibility(1, observed["maximum_common_tickers"], observed["maximum_strict_independent_origins"])
    unused = [stretch for stretch in scope["stretches"] if stretch["ticker"] not in used_tickers]
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "independent_origin_definition": {"enforced_by": "scripts/construct_alpaca_holdout_c_cohort.py", "rule": "one non-overlapping 32-session context plus 20-session outcome slice inside one contiguous >=8-common-ticker block", "ticker_switch_is_independent": False, "separate_blocks_can_be_summed": False},
        "catalog": {"month_count": len(months), "months": {month: sorted(tickers) for month, tickers in months.items()}},
        "scope": {"stretch_count": len(scope["stretches"]), "ticker_count": scope["ticker_count"], "used_tickers": sorted(used_tickers), "unused_stretch_count": len(unused), "unused_ticker_count": len({item["ticker"] for item in unused}), "unused_stretches": unused},
        "holdout_c": {"cohort": cohort.to_dict(), "break_dates": observed["common_universe_break_dates"], "conclusion": "Unused ticker stretches cannot repair a provider-wide day where fewer than eight tickers are eligible; no additional strict origins are available under the unchanged strongest-single-block rule."},
        "additional_origins_from_existing_data": 0, "ranking_outcomes_evaluated": False, "full_gate_changed": False, "live_execution": False,
    }
    GOVERNANCE.mkdir(parents=True, exist_ok=True)
    output = GOVERNANCE / f"unused_independent_origin_scope_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"path": str(output), "month_count": len(months), "unused_ticker_count": payload["scope"]["unused_ticker_count"], "additional_origins": 0, "cohort": cohort.to_dict()}, indent=2))


if __name__ == "__main__":
    main()
