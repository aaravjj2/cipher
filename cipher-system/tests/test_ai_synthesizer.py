from __future__ import annotations

import json
from pathlib import Path
from core import ai_synthesizer


def test_get_available_models_returns_configured_providers() -> None:
    models = ai_synthesizer.get_available_models()
    assert len(models) >= 3
    ids = [m["id"] for m in models]
    assert "openai/gpt-4o-mini" in ids
    assert "ProsusAI/finbert" in ids
    assert any(m["default"] is True for m in models)


def test_deterministic_morning_synthesis_handles_missing_fields() -> None:
    minimal_payload = {
        "ticker": "SPY",
        "market": [],
        "significant_flow": {},
        "gex_change": {},
        "prospective_fronttests": {},
        "attention": [],
    }
    result = ai_synthesizer._deterministic_morning_synthesis(minimal_payload)
    assert "headline" in result
    assert "market_regime" in result
    assert "gamma_structure_analysis" in result
    assert isinstance(result["whale_flow_insights"], list)
    assert isinstance(result["actionable_paper_setups"], list)
    assert isinstance(result["risk_and_integrity_flags"], list)
    assert result["is_ai_generated"] is False


def test_synthesize_morning_brief_caches_result(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(ai_synthesizer, "CACHE_DIR", tmp_path)

    payload = {
        "ticker": "QQQ",
        "market": [{"ticker": "QQQ", "price": 480.0, "day_change_pct": 0.8}],
        "significant_flow": {"prints": []},
        "gex_change": {"current": {"net_gex": 5000000}, "prior": {"net_gex": 4000000}, "change": 1000000},
        "prospective_fronttests": {"open_signals": []},
        "attention": [],
    }
    res1 = ai_synthesizer.synthesize_morning_brief(payload, sync_remote=False)
    assert res1 is not None
    assert "headline" in res1

    # Second call should hit the cache
    res2 = ai_synthesizer.synthesize_morning_brief(payload, sync_remote=False)
    assert res2["headline"] == res1["headline"]


def test_evaluate_autopilot_candidates_enriches_cards() -> None:
    candidates = [
        {
            "ticker": "NVDA",
            "direction": "BULLISH",
            "score": 85.0,
            "reward_risk": 2.2,
            "spot": 130.0,
            "target": 135.0,
            "invalidation": 127.0,
            "setup_type": "CIPHER_MODEL_V6",
            "sentiment": {"status": "available", "score": 0.8},
        }
    ]
    evaluated = ai_synthesizer.evaluate_autopilot_candidates(candidates, sync_remote=False)
    assert len(evaluated) == 1
    assert "ai_evaluation" in evaluated[0]
    eval_data = evaluated[0]["ai_evaluation"]
    assert eval_data["regime_confluence_score"] >= 80
    assert "thesis" in eval_data
    assert "recommended_structure" in eval_data
