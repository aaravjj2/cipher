from pathlib import Path

from core import market_research_agent as agent


def card(ticker="MU", score=82, cells=20, contracts=100, geometry=True):
    return {
        "ticker": ticker, "spot": 125.0, "score": score, "direction": "BULLISH",
        "setup_type": "CIPHER MODEL", "target": 130.0, "invalidation": 122.0,
        "reward_risk": 1.66, "geometry_valid": geometry, "actionable": True,
        "coverage_cells": cells, "contracts": contracts, "feed": "opra",
        "supports": [122.0], "resistances": [130.0], "read": "Support below, target above.",
    }


def test_report_separates_observed_and_derived_and_never_grants_order_authority():
    report = agent.build_report(
        intraday_scan={"as_of": "2026-08-14T14:00:00Z", "top": [card()]},
        weekly_scan={"as_of": "2026-08-14T14:01:00Z", "top": [card("NVDA", 76)]},
        selected_universe=["MU", "NVDA"], generated_at="2026-08-14T14:02:00Z",
    )
    item = report["candidates"]["intraday"][0]
    assert item["observed"]["coverage"]["status"] == "sufficient"
    assert item["derived"]["confidence"] == "higher"
    assert "debit spread research" in item["derived"]["research_template"]
    assert report["execution_boundary"]["live_order_authority"] is False


def test_sparse_or_invalid_evidence_is_not_high_confidence():
    report = agent.build_report(
        intraday_scan={"top": [card(cells=None, contracts=None), card("SNDK", geometry=False)]},
        weekly_scan=None, selected_universe=["MU", "SNDK"],
    )
    for item in report["candidates"]["intraday"]:
        assert item["derived"]["confidence"] == "insufficient"
        assert item["derived"]["eligible_for_deeper_review"] is False
        assert item["derived"]["research_template"].startswith("wait")


def test_one_horizon_can_fail_without_erasing_the_other():
    def scan_fn(*, mode, **_kwargs):
        if mode == "short":
            raise RuntimeError("provider timeout")
        return {"as_of": "later", "top": [card("AAPL")]}

    report = agent.run(scan_fn, groups=["large_liquid"], candidate_limit=3)
    assert report["candidates"]["intraday"] == []
    assert report["candidates"]["weekly"][0]["ticker"] == "AAPL"
    assert report["errors"][0]["scope"] == "intraday"


def test_atomic_save_latest_and_history(tmp_path: Path):
    report = agent.build_report(
        intraday_scan={"top": [card()]}, weekly_scan={"top": []},
        selected_universe=["MU"], generated_at="2026-08-14T14:02:03Z",
    )
    target = agent.save(report, tmp_path)
    assert target.exists()
    assert agent.latest(tmp_path)["available"] is True
    assert agent.history(tmp_path)[0]["intraday_candidates"] == 1
    assert not list(tmp_path.rglob("*.tmp"))
