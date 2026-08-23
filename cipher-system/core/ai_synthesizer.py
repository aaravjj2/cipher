"""Financial AI Synthesis Engine for Cipher.

Provides high-reliability financial reasoning and synthesis over scanner cards,
GEX distributions, institutional flow sweeps, and shadow portfolios.
Uses OpenRouter (e.g. GPT-4o-mini, Claude, DeepSeek) with deterministic
fallbacks when offline or unconfigured.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

logger = logging.getLogger("cipher.ai_synthesizer")

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "data" / "cache" / "ai_synthesis"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_MODEL = os.environ.get("CIPHER_AI_MODEL") or "openai/gpt-4o-mini"
FALLBACK_MODELS = ["openai/gpt-4o-mini", "deepseek/deepseek-chat", "anthropic/claude-3-haiku"]
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

_CACHE_LOCK = threading.Lock()


def _env_key(name: str) -> str | None:
    for env_file in [ROOT / ".env", ROOT.parent / ".env"]:
        if env_file.is_file():
            try:
                for line in env_file.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    if key.strip() == name:
                        val = value.strip().strip('"').strip("'")
                        if val:
                            return val
            except Exception:
                pass
    return os.environ.get(name) or None


def get_openrouter_api_key() -> str | None:
    return _env_key("OPENROUTER_API_KEY")


def get_available_models() -> list[dict[str, Any]]:
    has_or = bool(get_openrouter_api_key())
    return [
        {
            "id": "openai/gpt-4o-mini",
            "name": "GPT-4o Mini (Fast Financial Synthesis)",
            "provider": "OpenRouter",
            "status": "ready" if has_or else "missing_api_key",
            "default": True,
        },
        {
            "id": "anthropic/claude-sonnet-5",
            "name": "Claude Sonnet 5 (Deep Market Reasoning)",
            "provider": "OpenRouter",
            "status": "ready" if has_or else "missing_api_key",
            "default": False,
        },
        {
            "id": "deepseek/deepseek-chat",
            "name": "DeepSeek V3 (Quantitative Multi-Factor)",
            "provider": "OpenRouter",
            "status": "ready" if has_or else "missing_api_key",
            "default": False,
        },
        {
            "id": "ProsusAI/finbert",
            "name": "FinBERT (Local Financial Sentiment)",
            "provider": "HuggingFace Local",
            "status": "ready",
            "default": False,
        },
    ]


def _call_openrouter(
    messages: list[dict[str, str]],
    *,
    model: str,
    max_tokens: int = 1500,
    temperature: float = 0.2,
    response_format: dict[str, str] | None = None,
) -> str | None:
    key = get_openrouter_api_key()
    if not key:
        return None

    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if response_format:
        payload["response_format"] = response_format

    req = urllib.request.Request(
        OPENROUTER_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://cipher-terminal.local",
            "X-Title": "Cipher Options Research Terminal",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=15.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            choices = data.get("choices") or []
            if choices and choices[0].get("message"):
                return choices[0]["message"].get("content")
    except Exception as exc:
        logger.warning("OpenRouter call failed for model %s: %s", model, exc)
        return None


def _cache_key(prefix: str, data: Any, hour_bucket: str) -> str:
    serialized = json.dumps(data, sort_keys=True, default=str)
    digest = hashlib.sha256(f"{hour_bucket}:{serialized}".encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}.json"


def _read_cache(filename: str) -> dict[str, Any] | None:
    path = CACHE_DIR / filename
    if not path.is_file():
        return None
    try:
        with _CACHE_LOCK:
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_cache(filename: str, payload: dict[str, Any]) -> None:
    path = CACHE_DIR / filename
    try:
        with _CACHE_LOCK:
            path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning("Failed writing AI cache %s: %s", path, exc)


def _deterministic_morning_synthesis(brief: dict[str, Any]) -> dict[str, Any]:
    """Fallback deterministic financial synthesizer when LLM API is offline."""
    market = brief.get("market") or []
    ticker = brief.get("ticker", "SPY")
    flow = brief.get("significant_flow", {})
    prints = flow.get("prints") or []
    gex = brief.get("gex_change") or {}
    prospective = brief.get("prospective_fronttests") or {}
    signals = prospective.get("open_signals") or []
    attention = brief.get("attention") or []

    # Calculate index changes
    changes = {m.get("ticker"): m.get("day_change_pct") for m in market if m.get("ticker")}
    spy_chg = changes.get("SPY")
    qqq_chg = changes.get("QQQ")

    regime = "Balanced Rotation"
    if spy_chg is not None:
        if spy_chg > 0.5:
            regime = "Broad Bullish Momentum"
        elif spy_chg < -0.5:
            regime = "Risk-Off Defensive Expansion"
        elif abs(spy_chg) <= 0.2:
            regime = "Gamma Pinning / Range Compression"

    whale_insights = []
    total_flow_prem = 0.0
    bullish_prem = 0.0
    bearish_prem = 0.0
    for p in prints:
        prem = float(p.get("premium") or 0)
        side = str(p.get("side") or "").upper()
        total_flow_prem += prem
        if "BID" in side or "SELL" in side or "PUT" in str(p.get("contract")):
            bearish_prem += prem
        else:
            bullish_prem += prem
        if prem >= 250_000:
            whale_insights.append(
                f"{p.get('ticker', ticker)} ${prem:,.0f} {p.get('contract', '')} {side} execution"
            )

    setups = []
    for s in signals[:4]:
        setups.append({
            "ticker": s.get("ticker"),
            "direction": str(s.get("direction", "")).upper(),
            "setup": s.get("setup_id", "Cipher Quantitative"),
            "entry_level": s.get("underlying_entry"),
            "target": s.get("target"),
            "status": s.get("option_selection_status", "Active"),
        })

    headline = (
        f"{ticker} Session Overview: {regime} with {len(prints)} institutional flow prints "
        f"and {len(signals)} active prospective signals."
    )

    return {
        "headline": headline,
        "market_regime": regime,
        "gamma_structure_analysis": (
            f"Net GEX on {ticker} is {gex.get('current', {}).get('net_gex', 'neutral')} "
            f"(change: {gex.get('change', 'flat')}). {gex.get('caveat', '')}"
        ),
        "whale_flow_insights": whale_insights or ["No single institutional prints above $250k threshold recorded."],
        "actionable_paper_setups": setups,
        "risk_and_integrity_flags": [item.get("title") for item in attention if item.get("title")] or ["All data streams healthy."],
        "model_used": "Deterministic Financial Rule Synthesizer",
        "provider": "Local Cipher Engine",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "is_ai_generated": False,
    }


def synthesize_morning_brief(
    brief_payload: dict[str, Any],
    *,
    model: str | None = None,
    force_refresh: bool = False,
    sync_remote: bool = False,
) -> dict[str, Any]:
    """Generate an AI executive morning market brief synthesis."""
    target_model = model or DEFAULT_MODEL
    now = datetime.now(timezone.utc)
    hour_bucket = now.strftime("%Y-%m-%d_%H")
    cache_file = _cache_key(f"brief_{brief_payload.get('ticker', 'ALL')}", brief_payload, hour_bucket)

    if not force_refresh:
        cached = _read_cache(cache_file)
        if cached:
            return cached

    if not sync_remote and not force_refresh:
        # Fast path: return deterministic summary immediately and pre-warm AI in background thread
        fallback = _deterministic_morning_synthesis(brief_payload)

        def _bg_warm():
            try:
                synthesize_morning_brief(brief_payload, model=target_model, force_refresh=True, sync_remote=True)
            except Exception:
                pass

        threading.Thread(target=_bg_warm, daemon=True).start()
        return fallback

    # Prepare compact prompt context
    market_summary = [
        f"{m.get('ticker')}: price={m.get('price')} chg={m.get('day_change_pct')}%"
        for m in (brief_payload.get("market") or [])
    ]
    flow_summary = [
        f"{p.get('ticker')} {p.get('contract')} side={p.get('side')} prem=${p.get('premium', 0):,.0f}"
        for p in (brief_payload.get("significant_flow", {}).get("prints") or [])[:6]
    ]
    signals_summary = [
        f"{s.get('ticker')} {s.get('direction')} setup={s.get('setup_id')} entry={s.get('underlying_entry')} target={s.get('target')}"
        for s in (brief_payload.get("prospective_fronttests", {}).get("open_signals") or [])[:5]
    ]
    gex_data = brief_payload.get("gex_change") or {}
    attention_data = [a.get("title") for a in (brief_payload.get("attention") or [])]

    system_prompt = (
        "You are Cipher's Senior Options & Derivatives Quantitative Strategist. "
        "You provide institutional-grade, actionable morning market analysis for a professional options desk. "
        "Your style is concise, mathematically disciplined, and focused on gamma regimes, flow positioning, and risk boundaries. "
        "Strictly return a valid JSON object matching the requested schema with no markdown code blocks."
    )

    user_prompt = f"""
Analyze the following morning session evidence:
- Primary Ticker: {brief_payload.get('ticker', 'SPY')}
- Market Benchmarks: {', '.join(market_summary)}
- Institutional Option Flow Sweeps: {json.dumps(flow_summary)}
- Net GEX Positioning: Current Net={gex_data.get('current', {}).get('net_gex')} Prior Net={gex_data.get('prior', {}).get('net_gex')} Change={gex_data.get('change')}
- Active Shadow Signals: {json.dumps(signals_summary)}
- System / Data Alerts: {json.dumps(attention_data)}

Output a JSON object with exactly these keys:
{{
  "headline": "<1-2 punchy sentences summarizing market sentiment and primary directional thesis>",
  "market_regime": "<Name of current gamma & volatility regime, e.g. Positive Gamma Compression / Volatility Expansion>",
  "gamma_structure_analysis": "<Analysis of GEX positioning, dealer delta hedging implications, and key inflection strikes>",
  "whale_flow_insights": ["<Bullet 1 on notable block/sweep positioning>", "<Bullet 2 on institutional sentiment>"],
  "actionable_paper_setups": [
    {{
      "ticker": "<symbol>",
      "direction": "<BULLISH|BEARISH>",
      "setup": "<setup name>",
      "entry_level": "<price or level>",
      "target": "<price or target>",
      "thesis": "<1 sentence reasoning>",
      "invalidation": "<key stop or invalidation level>"
    }}
  ],
  "risk_and_integrity_flags": ["<Key risks, macroeconomic catalysts, or data caveats to monitor>"]
}}
"""

    raw_response = None
    chosen_model = target_model
    for candidate_model in [target_model] + [m for m in FALLBACK_MODELS if m != target_model]:
        raw_response = _call_openrouter(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            model=candidate_model,
            max_tokens=1500,
            temperature=0.2,
        )
        if raw_response:
            chosen_model = candidate_model
            break

    if raw_response:
        try:
            cleaned = raw_response.strip()
            if cleaned.startswith("```json"):
                cleaned = cleaned[7:]
            if cleaned.startswith("```"):
                cleaned = cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            parsed = json.loads(cleaned.strip())
            parsed["model_used"] = chosen_model
            parsed["provider"] = "OpenRouter AI"
            parsed["generated_at"] = now.isoformat()
            parsed["is_ai_generated"] = True
            _write_cache(cache_file, parsed)
            return parsed
        except Exception as exc:
            logger.warning("Failed parsing AI JSON response: %s; raw was: %s", exc, raw_response[:200])

    fallback = _deterministic_morning_synthesis(brief_payload)
    _write_cache(cache_file, fallback)
    return fallback


def evaluate_autopilot_candidates(
    candidates: Sequence[Mapping[str, Any]],
    *,
    market_context: Mapping[str, Any] | None = None,
    model: str | None = None,
    sync_remote: bool = False,
) -> list[dict[str, Any]]:
    """Evaluate premarket candidate setups using financial AI reasoning."""
    if not candidates:
        return []

    target_model = model or DEFAULT_MODEL
    now = datetime.now(timezone.utc)
    results: list[dict[str, Any]] = []

    compact_candidates = [
        {
            "ticker": str(c.get("ticker")),
            "direction": str(c.get("direction")),
            "score": c.get("score"),
            "reward_risk": c.get("reward_risk"),
            "spot": c.get("spot"),
            "target": c.get("target"),
            "invalidation": c.get("invalidation"),
            "setup_type": c.get("setup_type"),
            "sentiment": c.get("sentiment"),
        }
        for c in candidates
    ]

    ai_by_ticker = {}
    if sync_remote:
        system_prompt = (
            "You are Cipher's Autonomous Strategy Evaluator. "
            "Review candidate setups and assign institutional probability, key catalyst risks, "
            "and multi-factor reasoning. Return a JSON list of objects matching the schema."
        )

        user_prompt = f"""
Review these premarket watch candidates:
{json.dumps(compact_candidates, indent=2)}

Output a JSON array where each element contains:
{{
  "ticker": "<symbol>",
  "ai_thesis": "<Concise 1-sentence technical & structural thesis for RTH confirmation>",
  "regime_confluence_score": <Integer 1-100 estimating alignment with volatility and market structure>,
  "catalyst_risk": "<Low|Medium|High>",
  "recommended_contract_type": "<e.g. 0-1 DTE Defined Debit Spread or ATM Call/Put>"
}}
"""

        raw_response = _call_openrouter(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            model=target_model,
            max_tokens=1500,
        )

        if raw_response:
            try:
                cleaned = raw_response.strip()
                if cleaned.startswith("```json"):
                    cleaned = cleaned[7:]
                if cleaned.startswith("```"):
                    cleaned = cleaned[3:]
                if cleaned.endswith("```"):
                    cleaned = cleaned[:-3]
                parsed_list = json.loads(cleaned.strip())
                if isinstance(parsed_list, list):
                    for item in parsed_list:
                        if isinstance(item, dict) and item.get("ticker"):
                            ai_by_ticker[str(item["ticker"]).upper()] = item
            except Exception as exc:
                logger.warning("Failed parsing autopilot candidates AI response: %s", exc)

    for c in candidates:
        row = dict(c)
        ticker = str(row.get("ticker", "")).upper()
        ai_eval = ai_by_ticker.get(ticker)
        if ai_eval:
            row["ai_evaluation"] = {
                "thesis": ai_eval.get("ai_thesis"),
                "regime_confluence_score": ai_eval.get("regime_confluence_score", int(row.get("score") or 70)),
                "catalyst_risk": ai_eval.get("catalyst_risk", "Medium"),
                "recommended_structure": ai_eval.get("recommended_contract_type", "Defined Debit Spread"),
                "model": target_model,
                "evaluated_at": now.isoformat(),
            }
        else:
            score = float(row.get("score") or 70)
            direction = str(row.get("direction") or "BULLISH").upper()
            row["ai_evaluation"] = {
                "thesis": f"Quantitative {row.get('setup_type', 'setup')} indicating {direction} expansion towards {row.get('target')}.",
                "regime_confluence_score": int(min(100, max(1, score))),
                "catalyst_risk": "Medium",
                "recommended_structure": "Defined-Risk Debit Spread",
                "model": "Deterministic Financial Engine",
                "evaluated_at": now.isoformat(),
            }
        results.append(row)

    return results
