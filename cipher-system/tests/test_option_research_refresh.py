from __future__ import annotations

import json
from pathlib import Path

from core.research_platform.option_research_refresh import refresh_option_research, summarize_option_report


def row(policy: str, model: str, *, pnl: float, risk_pct: float, profit_factor: float, trades: int = 40) -> dict:
    return {
        "policy": policy,
        "execution_model": model,
        "total_pnl_dollars": pnl,
        "pnl_on_deployed_risk_pct": risk_pct,
        "profit_factor": profit_factor,
        "trades": trades,
        "max_drawdown_dollars": -100.0,
    }


def test_option_summary_distinguishes_degradation_from_severe_survival():
    report = {
        "analysis_start": "2026-01-26",
        "analysis_end": "2026-07-24",
        "holdout_months": ["2026-04", "2026-05", "2026-06", "2026-07"],
        "candidate_variants": 10296,
        "outcome_rows_loaded": 1000,
        "selection_policies": ["permissive"],
        "aggregate_results": [
            row("permissive", "base", pnl=400.0, risk_pct=7.0, profit_factor=1.3),
            row("permissive", "worse", pnl=100.0, risk_pct=2.0, profit_factor=1.1),
            row("permissive", "severe", pnl=-500.0, risk_pct=-9.0, profit_factor=0.7),
        ],
    }
    summary = summarize_option_report(report)
    assert summary["candidate_variants"] == 10296
    assert summary["degradation_survivor_count"] == 1
    assert summary["severe_survivor_count"] == 0
    assert summary["promotion_eligible"] is False
    assert summary["allowed_claim"] == "one_or_more_policies_survived_base_and_worse_but_not_required_severe_execution"


def test_option_summary_requires_minimum_trade_count():
    report = {
        "aggregate_results": [
            row("strict", "base", pnl=100.0, risk_pct=5.0, profit_factor=2.0, trades=5),
            row("strict", "worse", pnl=80.0, risk_pct=4.0, profit_factor=1.8, trades=5),
            row("strict", "severe", pnl=40.0, risk_pct=2.0, profit_factor=1.2, trades=5),
        ]
    }
    summary = summarize_option_report(report)
    assert summary["degradation_survivor_count"] == 0
    assert summary["severe_survivor_count"] == 0


def test_option_refresh_blocks_cleanly_when_inputs_are_missing(tmp_path: Path):
    status_path = tmp_path / "status.json"
    payload = refresh_option_research(
        system_root=tmp_path,
        state_path=tmp_path / "state.json",
        status_path=status_path,
    )
    assert payload["status"] == "blocked_missing_inputs"
    assert payload["execution_authority"] is False
    assert status_path.is_file()
    saved = json.loads(status_path.read_text(encoding="utf-8"))
    assert saved["status"] == "blocked_missing_inputs"
