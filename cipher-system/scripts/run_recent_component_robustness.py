#!/usr/bin/env python3
"""Read-only concentration audit for the strongest recent component."""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
for path in (ROOT, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import run_recent_regime_research as recent  # noqa: E402
from core.research_platform.regime_allocator import equity_curve_to_returns  # noqa: E402
from core.research_platform.strategy_research_loop import (  # noqa: E402
    CanonicalPanel,
    StrategyCandidate,
    StrategyResearchPolicy,
    load_canonical_daily_panel,
    run_candidate_backtest,
)

OUTPUT = ROOT / "data" / "governance" / "recent_component_robustness.json"
CACHE = ROOT / "data" / "cache" / "recent_component_robustness"
COMPONENT_ID = "candidate_450ab714a604e63bc221ccfb"

EQUITIES = frozenset({"AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "JPM", "XOM", "UNH", "CAT", "COST", "WMT"})
REMOVAL_GROUPS = {
    "mega_cap_technology": frozenset({"AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL"}),
    "current_cat_meta": frozenset({"CAT", "META"}),
    "real_assets": frozenset({"GLD", "SLV", "USO", "VNQ", "XLE", "XLB", "XLRE"}),
    "rates_and_credit": frozenset({"TLT", "IEF", "HYG", "LQD"}),
    "single_equities": EQUITIES,
}
CATEGORY = {
    **{symbol: "single_equity" for symbol in EQUITIES},
    **{symbol: "broad_index" for symbol in ("SPY", "QQQ", "IWM", "DIA")},
    **{symbol: "rates_credit" for symbol in ("TLT", "IEF", "HYG", "LQD")},
    **{symbol: "real_asset" for symbol in ("GLD", "SLV", "USO", "VNQ")},
    **{symbol: "international" for symbol in ("EEM", "EFA")},
}


def make_policy() -> StrategyResearchPolicy:
    return StrategyResearchPolicy(
        batch_size=1,
        maximum_total_candidates=1,
        maximum_generation=0,
        maximum_adaptive_children_per_cycle=1,
        slippage_bps_per_side=25.0,
        minimum_sessions=300,
        minimum_trades=1,
        minimum_profit_factor=0.0,
        maximum_drawdown_pct=100.0,
        maximum_holm_adjusted_p_value=1.0,
        walk_forward_folds=4,
        random_seed=20252602,
    )


def make_panel(loaded: Any, frame: pd.DataFrame, end: str, role: str) -> CanonicalPanel:
    return CanonicalPanel(
        dataset_id=loaded.dataset_id,
        dataset_name=loaded.dataset_name,
        frame=frame.copy(),
        raw_object_count=loaded.raw_object_count,
        source_paths=loaded.source_paths,
        lineage_hash=loaded.lineage_hash,
        research_role=role,
        evaluation_start=recent.COMPONENT_START,
        evaluation_end=end,
    )


def metrics(output: Any, end: str) -> dict[str, Any]:
    values = equity_curve_to_returns(
        [point.timestamp for point in output.equity_curve],
        [point.equity for point in output.equity_curve],
    )
    return {
        "2025": recent.period_metrics(values, recent.ROLLING_START, "2025-12-31"),
        "2026_ytd": recent.period_metrics(values, recent.RECENT_YEAR_START, end),
        "combined": recent.period_metrics(values, recent.ROLLING_START, end),
    }


def contribution(output: Any, end: str) -> dict[str, Any]:
    by_symbol: dict[str, float] = defaultdict(float)
    by_category: dict[str, float] = defaultdict(float)
    count = 0
    for item in output.trades:
        entry = pd.Timestamp(item.entry_time)
        if entry < pd.Timestamp(recent.ROLLING_START) or entry > pd.Timestamp(end):
            continue
        value = float(item.return_pct or 0.0)
        symbol = str(item.symbol)
        by_symbol[symbol] += value
        by_category[CATEGORY.get(symbol, "sector_or_industry")] += value
        count += 1
    ranked = sorted(by_symbol.items(), key=lambda pair: pair[1], reverse=True)
    positive = sum(max(0.0, value) for value in by_symbol.values())
    first_share = max(0.0, ranked[0][1]) / positive if ranked and positive > 0 else None
    first_three = sum(max(0.0, value) for _, value in ranked[:3]) / positive if positive > 0 else None
    return {
        "method": "sum_of_normalized_return_percentages_not_capital_pnl",
        "observations": count,
        "by_symbol": dict(sorted(by_symbol.items())),
        "by_category": dict(sorted(by_category.items())),
        "top_symbol": ranked[0][0] if ranked else None,
        "top_symbol_positive_share": first_share,
        "top_three_positive_share": first_three,
        "concentration_flag": bool(first_share is not None and first_share > 0.35),
    }


def main() -> int:
    rows, pool_hash = recent.candidate_pool()
    row = next(item for item in rows if item["candidate_id"] == COMPONENT_ID)
    candidate = StrategyCandidate(
        family=row["family"],
        parameters=row["parameters"],
        parent_candidate_id=row.get("parent_candidate_id"),
        hypothesis="Recent component robustness audit.",
        candidate_id=row["candidate_id"],
    )
    dataset_id = recent.resolve_recent_dataset_id()
    loaded = load_canonical_daily_panel(recent.REGISTRY, dataset_id, cache_root=CACHE / dataset_id)
    frame = loaded.frame.copy()
    end = pd.to_datetime(frame["date"]).max().tz_localize(None).date().isoformat()
    symbols = sorted(frame["ticker"].unique().tolist())

    baseline = run_candidate_backtest(make_panel(loaded, frame, end, "recent_component_baseline"), candidate, make_policy())
    leave_one = []
    for symbol in symbols:
        reduced = frame[frame["ticker"] != symbol]
        result = run_candidate_backtest(make_panel(loaded, reduced, end, "recent_component_leave_one_out"), candidate, make_policy())
        view = metrics(result.output, end)
        leave_one.append(
            {
                "excluded_symbol": symbol,
                "return_2025_pct": view["2025"]["total_return_pct"],
                "return_2026_ytd_pct": view["2026_ytd"]["total_return_pct"],
                "drawdown_2025_pct": view["2025"]["maximum_drawdown_pct"],
                "drawdown_2026_ytd_pct": view["2026_ytd"]["maximum_drawdown_pct"],
            }
        )

    group_results = []
    for name, excluded in REMOVAL_GROUPS.items():
        reduced = frame[~frame["ticker"].isin(sorted(excluded))]
        result = run_candidate_backtest(make_panel(loaded, reduced, end, f"recent_component_without_{name}"), candidate, make_policy())
        group_results.append(
            {
                "excluded_group": name,
                "excluded_symbols": sorted(excluded & set(symbols)),
                "remaining_symbol_count": int(reduced["ticker"].nunique()),
                "metrics": metrics(result.output, end),
            }
        )

    returns_2025 = [float(item["return_2025_pct"]) for item in leave_one]
    returns_2026 = [float(item["return_2026_ytd_pct"]) for item in leave_one]
    summary = {
        "leave_one_symbol_out_tests": len(leave_one),
        "positive_2025_fraction": sum(value > 0 for value in returns_2025) / len(returns_2025),
        "positive_2026_fraction": sum(value > 0 for value in returns_2026) / len(returns_2026),
        "worst_2025_return_pct": min(returns_2025),
        "worst_2026_ytd_return_pct": min(returns_2026),
        "group_removal_tests": len(group_results),
    }
    summary["leave_one_symbol_out_passed"] = bool(
        summary["positive_2025_fraction"] >= 0.90
        and summary["positive_2026_fraction"] >= 0.90
        and summary["worst_2025_return_pct"] > 0
        and summary["worst_2026_ytd_return_pct"] > 0
    )
    concentration = contribution(baseline.output, end)
    summary.update(
        {
            "top_symbol": concentration["top_symbol"],
            "top_symbol_positive_share": concentration["top_symbol_positive_share"],
            "concentration_flag": concentration["concentration_flag"],
            "allowed_claim": "recent_component_concentration_audit_only",
        }
    )

    payload = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "completed",
        "dataset": {
            "dataset_id": loaded.dataset_id,
            "dataset_name": loaded.dataset_name,
            "lineage_hash": loaded.lineage_hash,
            "evaluation_end": end,
        },
        "candidate_pool_hash": pool_hash,
        "component": candidate.to_dict(),
        "slippage_bps_per_side": 25.0,
        "baseline_metrics": metrics(baseline.output, end),
        "leave_one_symbol_out": leave_one,
        "group_removals": group_results,
        "contribution_concentration": concentration,
        "summary": summary,
        "automatic_promotion": False,
        "paper_or_live_execution": False,
        "execution_authority": False,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUTPUT.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    temporary.replace(OUTPUT)
    print(json.dumps({"status": "completed", **summary, "output": str(OUTPUT), "automatic_promotion": False, "execution_authority": False}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
