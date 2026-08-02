#!/usr/bin/env python3
"""Write an evidence-backed decision record for local research tooling.

This is deliberately a usage report, not a dependency wish list.  A tool is
only marked active when it has produced a local artifact or is already used by
the research code.  Deferred tools must retain their gating prerequisite.
"""

from __future__ import annotations

import importlib.metadata
import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def version(package: str) -> str | None:
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return None


def newest(directory: Path, pattern: str) -> str | None:
    matches = sorted(directory.glob(pattern))
    return str(matches[-1]) if matches else None


def main() -> None:
    generated_at = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    market_quality = DATA / "market_quality"
    report = {
        "generated_at_utc": generated_at,
        "scope": "local research tooling only; no order submission or paper execution",
        "active_tools": [
            {
                "tool": "DuckDB",
                "version": version("duckdb"),
                "usage": "Local market-data catalog and deterministic scope/audit queries.",
                "evidence": [
                    "scripts/scope_price_only_forecast_gate.py",
                    "scripts/audit_holdout_c_symbol_coverage.py",
                    "core/research_platform/local_market_catalog.py",
                ],
            },
            {
                "tool": "yfinance",
                "version": version("yfinance"),
                "usage": "Existing corporate-action and reconciliation context only.",
                "restriction": "Not an accepted replacement for the qualified historical minute-bar source.",
            },
            {
                "tool": "hurst",
                "version": version("hurst"),
                "usage": "Descriptive Hurst-exponent context for the already frozen Holdout C price series.",
                "evidence": [newest(market_quality, "holdout_c_hurst_context_*.json")],
                "restriction": "Cannot influence data-source choice, membership, origins, ranking outcomes, promotion, or execution.",
            },
        ],
        "deferred_tools": [
            {
                "tools": ["quantstats", "alphalens", "pyfolio", "honest-signals"],
                "reason": "No strategy has cleared the unchanged Holdout C cohort gate; performance and calibration reports would be invalid or non-actionable before then.",
                "prerequisite": "At least 8 common eligible tickers and 12 strict independent origins from one qualified source.",
            },
            {
                "tools": ["vectorbt", "backtesting.py", "Manifold-BT", "hftbacktest"],
                "reason": "Backtesting engines cannot repair an inadequate frozen evaluation cohort. hftbacktest is additionally mismatched to the current minute-bar, non-order-book dataset.",
                "prerequisite": "Qualified evaluation data and a pre-registered strategy specification.",
            },
            {
                "tools": ["Qlib", "FinRL", "ml-quant-trading"],
                "reason": "Model/factor frameworks are not a substitute for a valid provenance-controlled training and holdout dataset.",
                "prerequisite": "Operational data pipeline and a valid independent holdout before model selection.",
            },
            {
                "tools": ["tsfresh", "pmdarima"],
                "reason": "Feature engineering and classical forecast selection remain blocked by the same inadequate independent evaluation cohort.",
                "prerequisite": "Sufficient pre-registered forecast windows under the price-only gate.",
            },
            {
                "tools": ["edgartools"],
                "reason": "Fundamental data would create a new feature family requiring timestamp/provenance controls and preregistration; it is outside the frozen price-only study.",
                "prerequisite": "Separate fundamentals research protocol.",
            },
        ],
        "not_adopted": [
            {
                "tools": ["ArcticDB", "Marketstore", "PyStore", "kdb+"],
                "reason": "The existing local DuckDB catalog meets current analytical access needs. Introducing a second storage system would duplicate data and increase operational surface without unlocking qualified history.",
            },
            {
                "tools": ["TectonicDB"],
                "reason": "Designed for tick/order-book streaming, which is not the current minute-bar research workload.",
            },
            {
                "tools": ["AkShare", "pandas-datareader", "findatapy"],
                "reason": "Additional data wrappers would not meet the single-source, schema, and provenance requirements for the frozen Holdout C cohort without a separate source qualification audit.",
            },
            {
                "tools": ["Prophet"],
                "reason": "Not selected for intraday equity price forecasting; no validated use case has been established.",
            },
        ],
        "blocker_evidence": {
            "holdout_c_recovery": newest(market_quality, "alpaca_holdout_c_recovery_closeout_*.json"),
            "alternate_source_entitlements": newest(market_quality, "holdout_c_alternate_source_entitlements_*.json"),
        },
        "non_negotiable_controls": [
            "The full gate, including volume reconciliation, is unchanged for volume-sensitive work.",
            "The price-only gate is only for price forecasting and excludes volume features and volume-based evaluation.",
            "No live or paper order execution is enabled by this tooling record.",
        ],
    }
    output_dir = DATA / "governance"
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"research_tooling_usage_{generated_at}.json"
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
