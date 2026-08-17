"""Bounded specialist tools exposed to the Cipher ADK agents."""
from __future__ import annotations

from typing import Any

from .client import CipherCoreClient


RESEARCH_NOTICE = (
    "Research-only evidence. No result is a recommendation or an authorization "
    "to deploy capital; human review remains required."
)
MAX_FLOW_PRINTS = 30
MAX_BARS = 240
MAX_GEX_SNAPSHOTS = 30


def _client() -> CipherCoreClient:
    return CipherCoreClient()


def _symbol(value: str) -> str:
    symbol = str(value or "").strip().upper()
    if not symbol or len(symbol) > 12 or not symbol.replace(".", "").replace("-", "").isalnum():
        raise ValueError("symbol must be a short ticker such as SPY or NVDA")
    return symbol


def _pick(payload: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {key: payload.get(key) for key in keys if key in payload}


def get_market_structure(symbol: str) -> dict[str, Any]:
    """Read current price, GEX/VEX structure, key levels, and session context.

    Args:
        symbol: Equity or ETF ticker, for example SPY or NVDA.
    """
    ticker = _symbol(symbol)
    payload = _client().get("/api/night-vision", {"symbol": ticker})
    return {
        "specialist": "market_structure",
        "ticker": ticker,
        "as_of": payload.get("as_of"),
        "feed": payload.get("feed"),
        "quote": payload.get("quote"),
        "exposure_summary": payload.get("summary"),
        "peak_exposure": payload.get("peak"),
        "key_levels": payload.get("levels"),
        "session_levels": payload.get("session_levels"),
        "coverage": payload.get("coverage"),
        "formula": payload.get("formula"),
        "caveat": payload.get("caveat"),
        "omitted": "The full strike-by-expiration grid is omitted from agent context.",
        "research_notice": RESEARCH_NOTICE,
    }


def get_options_flow(symbol: str, minimum_premium: float = 20_000) -> dict[str, Any]:
    """Read recent option prints with quote-relative side inference.

    Args:
        symbol: Equity or ETF ticker.
        minimum_premium: Minimum print premium in dollars, from 0 to 5,000,000.
    """
    ticker = _symbol(symbol)
    premium = max(0.0, min(float(minimum_premium), 5_000_000.0))
    payload = _client().get(
        "/api/flow",
        {"symbol": ticker, "min": premium, "fresh": "1"},
    )
    prints = payload.get("prints") if isinstance(payload.get("prints"), list) else []
    return {
        "specialist": "options_flow",
        "ticker": ticker,
        "as_of": payload.get("as_of"),
        "feed": payload.get("feed"),
        "observed_count": payload.get("count", len(prints)),
        "returned_count": min(len(prints), MAX_FLOW_PRINTS),
        "prints": prints[:MAX_FLOW_PRINTS],
        "errors": (payload.get("errors") or [])[:10],
        "caveat": payload.get("caveat")
        or "Side is inferred from trade versus bid/ask; it is not exchange-reported intent.",
        "research_notice": RESEARCH_NOTICE,
    }


def get_historical_evidence(
    symbol: str,
    timeframe: str = "5Min",
    bar_limit: int = 120,
) -> dict[str, Any]:
    """Read bounded OHLCV history plus the catalog of captured GEX snapshots.

    Args:
        symbol: Equity or ETF ticker.
        timeframe: Cipher bar timeframe such as 1Min, 5Min, 15Min, 1Hour, or 1Day.
        bar_limit: Number of recent bars, from 10 to 240.
    """
    ticker = _symbol(symbol)
    allowed_timeframes = {"1Min", "5Min", "15Min", "1Hour", "1Day"}
    if timeframe not in allowed_timeframes:
        raise ValueError(f"timeframe must be one of {sorted(allowed_timeframes)}")
    limit = max(10, min(int(bar_limit), MAX_BARS))
    bars = _client().get(
        "/api/bars",
        {"symbol": ticker, "timeframe": timeframe, "limit": limit},
    )
    gex = _client().get(
        "/api/gex-replay",
        {"action": "catalog", "ticker": ticker, "limit": MAX_GEX_SNAPSHOTS},
    )
    return {
        "specialist": "historical_evidence",
        "ticker": ticker,
        "bars": _pick(bars, ("ticker", "timeframe", "feed", "as_of", "bars", "caveat")),
        "gex_capture_catalog": _pick(
            gex,
            ("ticker", "count", "snapshots", "coverage", "caveat", "as_of"),
        ),
        "research_notice": RESEARCH_NOTICE,
    }


def get_strategy_validation(family: str = "") -> dict[str, Any]:
    """Read strategy metadata, evidence clocks, and prospective standing.

    Args:
        family: Optional strategy-family filter applied after retrieval.
    """
    catalog = _client().get("/api/strategies", {"action": "list"})
    standing = _client().get("/api/standing")
    evidence = _client().get("/api/evidence-status")
    strategies = catalog.get("strategies") if isinstance(catalog.get("strategies"), list) else []
    wanted = str(family or "").strip().lower()
    if wanted:
        strategies = [
            row for row in strategies
            if str((row or {}).get("family") or "").strip().lower() == wanted
        ]
    projected = [
        _pick(
            row,
            (
                "strategy_id",
                "name",
                "family",
                "evaluable",
                "blocked_reason",
                "data_requirement",
                "bar_timeframe",
            ),
        )
        for row in strategies[:100]
        if isinstance(row, dict)
    ]
    return {
        "specialist": "strategy_validation",
        "catalog_summary": catalog.get("summary"),
        "evaluation_standard": catalog.get("standard"),
        "strategies": projected,
        "prospective_standing": standing,
        "evidence_status": evidence,
        "research_notice": RESEARCH_NOTICE,
    }


def get_risk_and_governance_review() -> dict[str, Any]:
    """Read governance, research readiness, and the enforced human-review boundary."""
    governance = _client().get("/api/governance")
    research = _client().get("/api/research-status")
    health = _client().get("/api/health")
    return {
        "specialist": "risk_adversarial",
        "service": _pick(
            health,
            (
                "status",
                "service",
                "market_data_configured",
                "default_options_feed",
                "default_stock_feed",
                "read_only",
                "as_of",
            ),
        ),
        "governance": governance,
        "research_status": research,
        "required_conclusion": (
            "Surface conflicts, missing inputs, stale evidence, and unsupported claims. "
            "The final disposition is human review, never autonomous capital deployment."
        ),
        "research_notice": RESEARCH_NOTICE,
    }


TOOL_NAMES = frozenset(
    {
        get_market_structure.__name__,
        get_options_flow.__name__,
        get_historical_evidence.__name__,
        get_strategy_validation.__name__,
        get_risk_and_governance_review.__name__,
    }
)
