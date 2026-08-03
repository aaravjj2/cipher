#!/usr/bin/env python3
"""Screen pre-registered price-only factors on the current-era panel.

This is the deterministic adapter path used when RD-Agent's LLM configuration
is unavailable.  It preserves the same candidate artifact boundary and makes
the development/OOS decision without searching until a positive result appears.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.research_platform.artifact_store import ArtifactStore
from core.research_platform.factors import FactorCandidate, FactorResearchService
from core.research_platform.qlib_price_only import load_price_only_daily, write_qlib_panel
from core.research_platform.registry import ResearchRegistry


DATA = ROOT / "data" / "normalized" / "alpaca_sip_holdout_c_1m"
OUT = ROOT / "data" / "market_quality"
SYMBOLS = ("SPY", "QQQ", "IWM", "XLF", "XLE", "AAPL", "MSFT", "NVDA", "GE")
CANDIDATES = (
    ("momentum_20", "pct_change(close, 20)", "20-session momentum predicts the next return.", "positive"),
    ("reversal_5", "-pct_change(close, 5)", "Short-horizon reversal predicts the next return.", "positive"),
    ("trend_10_30", "ema(close, 10) / ema(close, 30) - 1", "A fast/slow price trend predicts the next return.", "positive"),
    ("volatility_adjusted_return", "pct_change(close, 5) / rolling_std(pct_change(close, 1), 20)", "Recent return scaled by recent price volatility predicts the next return.", "positive"),
)


def _files() -> list[Path]:
    return sorted(DATA.glob("year=*/month=*/*.parquet"))


def _rank_ic(values: np.ndarray, outcomes: np.ndarray) -> float | None:
    mask = np.isfinite(values) & np.isfinite(outcomes)
    if mask.sum() < 3 or np.std(values[mask]) == 0 or np.std(outcomes[mask]) == 0:
        return None
    return float(np.corrcoef(np.argsort(np.argsort(values[mask])), np.argsort(np.argsort(outcomes[mask])))[0, 1])


def _evaluate(rows: list[dict], compiled, cutoff: date) -> dict:
    by_symbol: dict[str, list[dict]] = {}
    for row in rows:
        by_symbol.setdefault(row["instrument"], []).append(row)
    records: list[dict] = []
    for symbol, series in by_symbol.items():
        series.sort(key=lambda item: item["datetime"])
        close = np.asarray([float(item["close"]) for item in series], dtype=float)
        factor = compiled.evaluate({"open": np.asarray([item["open"] for item in series]), "high": np.asarray([item["high"] for item in series]), "low": np.asarray([item["low"] for item in series]), "close": close})
        for index in range(len(series) - 20):
            if series[index]["datetime"] >= cutoff:
                continue
            for horizon in (5, 20):
                end = index + horizon
                if end >= len(series):
                    continue
                if series[end]["datetime"] >= cutoff:
                    continue
                outcome = close[end] / close[index] - 1.0
                if np.isfinite(factor[index]) and np.isfinite(outcome):
                    records.append({"symbol": symbol, "date": str(series[index]["datetime"]), "factor": float(factor[index]), "outcome": float(outcome), "horizon": horizon})
    result: dict[str, dict] = {}
    for horizon in (5, 20):
        subset = [item for item in records if item["horizon"] == horizon]
        if not subset:
            result[str(horizon)] = {"n": 0, "rank_ic": None, "positive_direction_accuracy": None}
            continue
        values = np.asarray([item["factor"] for item in subset])
        outcomes = np.asarray([item["outcome"] for item in subset])
        result[str(horizon)] = {"n": len(subset), "rank_ic": _rank_ic(values, outcomes), "positive_direction_accuracy": float(np.mean((values > 0) == (outcomes > 0)))}
    return result


def main() -> int:
    files = _files()
    rows = load_price_only_daily(files, SYMBOLS)
    qlib_path = OUT / "current_era_price_only_qlib_panel.parquet"
    write_qlib_panel(rows, qlib_path)
    registry = ResearchRegistry(ROOT / "data" / "governance" / "price_only_factor_screen.sqlite")
    artifacts = ArtifactStore(ROOT / "data" / "artifacts" / "price_only_factor_screen")
    service = FactorResearchService(registry, artifacts)
    reports = []
    for name, expression, hypothesis, direction in CANDIDATES:
        candidate = FactorCandidate(name=name, version="2026-08-03", expression=expression, hypothesis=hypothesis, expected_direction=direction, availability_lag_seconds=0, missing_value_policy="drop_until_warm", metadata={"data_scope": "price_only", "volume_used": False, "source": "pre_registered_deterministic_screen"})
        compiled, spec, artifact = service.register_candidate(candidate)
        reports.append({"candidate": candidate.to_dict(), "feature_id": spec.feature_id, "artifact_id": artifact.artifact_id, "development": _evaluate(rows, compiled, date(2025, 1, 1)), "oos_untouched_2025": None})
    for report, (name, expression, hypothesis, direction) in zip(reports, CANDIDATES):
        candidate = FactorCandidate(name=name, version="2026-08-03", expression=expression, hypothesis=hypothesis, expected_direction=direction, availability_lag_seconds=0, missing_value_policy="drop_until_warm", metadata={"data_scope": "price_only", "volume_used": False})
        compiled = service.compiler.compile(candidate)
        report["oos_untouched_2025"] = _evaluate_oos(rows, compiled, date(2025, 1, 1))
    report = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_scope": "price_only",
        "symbols": list(SYMBOLS),
        "rows": len(rows),
        "qlib_panel": str(qlib_path),
        "development_period": "2023-01-01..2024-12-31",
        "oos_period": "2025-01-01..2025-12-31",
        "candidates": reports,
        "rdagent": {"importable": True, "llm_loop_executed": False, "blocked_reason": "No configured model endpoint was available for an LLM-backed RD-Agent run; deterministic screen used."},
        "promotion": {"candidate_found": False, "paper_eligible": False, "reason": "No candidate is promoted from factor screening; OOS and full walk-forward strategy evidence are still required."},
        "volume_policy": "Volume-sensitive research remains blocked; volume was not selected or exported.",
    }
    path = OUT / f"price_only_factor_screen_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(path), "qlib_panel": str(qlib_path), "rows": len(rows), "candidates": len(reports)}, indent=2))
    return 0


def _evaluate_oos(rows: list[dict], compiled, cutoff: date) -> dict:
    by_symbol: dict[str, list[dict]] = {}
    for row in rows:
        by_symbol.setdefault(row["instrument"], []).append(row)
    values: dict[int, list[float]] = {5: [], 20: []}
    outcomes: dict[int, list[float]] = {5: [], 20: []}
    for symbol, series in by_symbol.items():
        series.sort(key=lambda item: item["datetime"])
        close = np.asarray([float(item["close"]) for item in series])
        factor = compiled.evaluate({"open": np.asarray([item["open"] for item in series]), "high": np.asarray([item["high"] for item in series]), "low": np.asarray([item["low"] for item in series]), "close": close})
        for index in range(len(series) - 20):
            if series[index]["datetime"] < cutoff or index + 20 >= len(series):
                continue
            for horizon in (5, 20):
                if index + horizon >= len(series) or not np.isfinite(factor[index]):
                    continue
                values[horizon].append(float(factor[index]))
                outcomes[horizon].append(float(close[index + horizon] / close[index] - 1.0))
    return {
        str(horizon): {
            "n": len(values[horizon]),
            "rank_ic": _rank_ic(np.asarray(values[horizon]), np.asarray(outcomes[horizon])) if values[horizon] else None,
            "positive_direction_accuracy": float(np.mean((np.asarray(values[horizon]) > 0) == (np.asarray(outcomes[horizon]) > 0))) if values[horizon] else None,
        }
        for horizon in (5, 20)
    }


if __name__ == "__main__":
    raise SystemExit(main())
