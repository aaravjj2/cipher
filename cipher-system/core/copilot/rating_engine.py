"""Expert Rating Engine - multi-horizon fundamental + positioning analysis with 1-10 verdict.

No guardrails. No hedging. This produces an actual investment rating with reasoning.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from core.copilot import tools
from core.copilot.providers import chat, available_providers, any_provider_configured
from core.copilot.tools import bounded_json


SYSTEM_PROMPT = """You are a senior equity analyst producing a formal investment rating (1-10).

Rating scale:
10 = Exceptional: Deep moat, secular tailwind, mispriced, high conviction
9  = Excellent: Strong compounder, clear catalyst path, favorable risk/reward
8  = Strong: Quality business, reasonable price, identifiable edge
7  = Good: Solid fundamentals, decent setup, minor concerns
6  = Fair: Average business, fair price, no clear edge
5  = Mediocre: Flawed but not broken, better opportunities exist
4  = Weak: Deteriorating fundamentals, price ignores risks
3  = Poor: Structural issues, high probability of permanent capital loss
2  = Dangerous: Fraud risk, balance sheet crisis, or terminal decline
1  = Toxic: Near-certain capital destruction

You have access to all Cipher tools: live quotes, GEX/VEX positioning, IV structure,
scanner setups, earnings forecasts, backtest evidence, technicals, headlines, earnings dates,
backtest evidence, and paper account history.

For each rating you MUST provide:
1. The rating (1-10)
2. Current thesis (1-2 sentences)
3. 3-month outlook with specific catalysts
4. 1+ year structural view
4. Key risks (specific, not generic)
5. Position sizing guidance (relative to portfolio)
6. Option strategy if applicable (specific strikes/expirations)
7. Confidence level (1-5)

Be precise. Use numbers from tools. No hedging language."""


def build_research_payload(ticker: str) -> dict[str, Any]:
    """Gather all available data for a ticker across tools."""
    payload = {"ticker": ticker.upper(), "as_of": datetime.now(timezone.utc).isoformat()}

    # Core market data
    for tool_name in ("get_quote", "get_technicals", "get_exposure_term_structure",
                      "get_iv_structure", "get_earnings_dates", "get_headlines",
                      "scan_strategies", "get_backtest_evidence"):
        try:
            payload[tool_name] = tools.dispatch(tool_name, {"ticker": ticker.upper()})
        except Exception:
            payload[tool_name] = {"error": "tool failed"}

    return payload


def deterministic_rating(ticker: str, research: dict) -> dict:
    """Deterministic rating based on quantitative rules - no LLM required."""
    score = 5  # Start neutral
    reasons = []

    # Technical trend
    tech = research.get("get_technicals", {})
    if not tech.get("error"):
        trend = tech.get("trend")
        if trend == "up":
            score += 1
            reasons.append("uptrend intact")
        elif trend == "down":
            score -= 1
            reasons.append("downtrend")

    # GEX regime
    exposure = research.get("get_exposure_term_structure", {})
    if not exposure.get("error"):
        near = (exposure.get("buckets") or {}).get("0-14d") or {}
        if near.get("net_gex_musd", 0) > 0:
            score += 1
            reasons.append("positive near-term GEX")
        elif near.get("net_gex_musd", 0) < 0:
            score -= 1
            reasons.append("negative near-term GEX")

    # Scanner consensus
    scan = research.get("scan_strategies", {})
    if not scan.get("error"):
        consensus = scan.get("consensus", "")
        if "BULLISH" in consensus:
            score += 1
            reasons.append("scanner bullish")
        elif "BEARISH" in consensus:
            score -= 1
            reasons.append("scanner bearish")

    # Earnings timing
    earn = research.get("get_earnings_dates", {})
    if not earn.get("error") and earn.get("upcoming_earnings"):
        reasons.append("earnings catalyst approaching")

    # IV term structure
    iv = research.get("get_iv_structure", {})
    if not iv.get("error") and iv.get("expirations"):
        near_iv = iv["expirations"][0].get("atm_iv", 0)
        if near_iv > 0.8:
            reasons.append("elevated IV - premium selling opportunity")
        elif near_iv < 0.3:
            reasons.append("compressed IV - premium buying opportunity")

    # Clamp score
    rating = max(1, min(10, score))

    return {
        "rating": rating,
        "current_thesis": f"Deterministic score {rating}/10: {', '.join(reasons) if reasons else 'neutral setup'}",
        "three_month_outlook": "Rules-based outlook: monitor gamma flip, earnings catalyst, and IV mean-reversion.",
        "one_year_plus_view": "Long-term view depends on fundamental re-rating; position sizing should reflect conviction.",
        "key_risks": [
            "Model risk: deterministic rules cannot capture all nuances",
            "Data lag: IV and GEX from captures up to 2h old",
            "Earnings binary risk: single event can invalidate positioning"
        ],
        "position_sizing": "1-2% of portfolio per trade, max 5% aggregate",
        "option_strategy": "none" if rating < 7 else "debit spread toward regime direction",
        "confidence": 3,
        "reasoning": f"Deterministic rating engine applied {len(reasons)} rules: {', '.join(reasons) if reasons else 'no clear signals'}. This is a rules-based assessment - LLM providers unavailable."
    }


def synthesize_rating(ticker: str, research: dict) -> dict:
    """Synthesize research into a formal rating using LLM with deterministic fallback."""
    if not any_provider_configured():
        return deterministic_rating(ticker, research)

    prompt = f"""Analyze {ticker} and produce a formal 1-10 investment rating.

RESEARCH DATA:
{json.dumps(research, default=str)[:50000]}

Produce JSON with exactly these keys:
- rating: integer 1-10
- current_thesis: string
- three_month_outlook: string
- one_year_plus_view: string
- key_risks: array of strings
- position_sizing: string (e.g., "2-3% of portfolio, max 5%")
- option_strategy: string (specific strikes/expirations or "none")
- confidence: integer 1-5
- reasoning: string (2-3 paragraphs synthesizing the data)"""

    providers_list = available_providers()
    for provider in providers_list:
        try:
            result = chat(provider, [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt}
            ], tools.to_openai_specs())

            text = result.get("text", "")
            import re
            json_match = re.search(r'\{.*\}', text, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
        except Exception:
            continue

    # Deterministic fallback when all LLM providers fail
    return deterministic_rating(ticker, research)


def rate_ticker(ticker: str) -> dict:
    """Main entry: rate a ticker 1-10 with full reasoning."""
    research = build_research_payload(ticker)
    return synthesize_rating(ticker, research)


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("usage: python -m core.copilot.rating_engine TICKER")
        sys.exit(1)
    result = rate_ticker(sys.argv[1])
    print(json.dumps(result, indent=2, default=str))