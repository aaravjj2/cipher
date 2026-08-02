"""
Strategy Signal Aggregation Engine.

Combines multiple signals into composite scores:
- Cluster signals (strength, persistence, momentum)
- Flow signals (call/put imbalance, smart money divergence)
- Regime signals (volatility regime, decay rate)
- Technical signals (multi-timeframe alignment, confidence)

Provides unified scoring for strategy decisions.
"""

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import statistics

sys.path.insert(0, str(Path(__file__).parent.parent / "cipher-system" / "core"))


def normalize_score(value: float, min_val: float, max_val: float) -> float:
    """Normalize value to 0-1 range.
    
    Args:
        value: Raw value
        min_val: Expected minimum
        max_val: Expected maximum
    
    Returns:
        Normalized value 0-1
    """
    if max_val == min_val:
        return 0.5
    
    normalized = (value - min_val) / (max_val - min_val)
    return max(0.0, min(1.0, normalized))


def aggregate_cluster_signals(
    cluster: Dict,
    confidence_data: Dict,
    decay_data: Dict,
) -> Dict:
    """Aggregate cluster-related signals.
    
    Args:
        cluster: Cluster data
        confidence_data: Confidence analysis
        decay_data: Decay/lifecycle analysis
    
    Returns:
        Dict with aggregated cluster signals
    """
    # Cluster strength (normalized)
    strength = float(cluster.get("strength", 0))
    strength_norm = normalize_score(strength, 0, 1e8)
    
    # Confidence score
    confidence = float(confidence_data.get("confidence_score", 0.5))
    
    # Lifecycle stage (forming=1, mature=0.8, decaying=0.4, dissolving=0)
    lifecycle_stage = decay_data.get("lifecycle_stage", "mature")
    stage_scores = {
        "forming": 1.0,
        "mature": 0.8,
        "decaying": 0.4,
        "dissolving": 0.0,
    }
    lifecycle_score = stage_scores.get(lifecycle_stage, 0.5)
    
    # Persistence (from multi-expiration analysis)
    persistence = float(cluster.get("persistence_ratio", 0.5))
    
    # Weighted combination
    cluster_signal = (
        0.35 * strength_norm +
        0.30 * confidence +
        0.20 * lifecycle_score +
        0.15 * persistence
    )
    
    return {
        "cluster_signal": round(cluster_signal, 3),
        "components": {
            "strength_norm": round(strength_norm, 3),
            "confidence": round(confidence, 3),
            "lifecycle_score": round(lifecycle_score, 3),
            "persistence": round(persistence, 3),
        },
    }


def aggregate_flow_signals(
    flow_data: Dict,
    divergence_data: Dict,
) -> Dict:
    """Aggregate flow-related signals.
    
    Args:
        flow_data: Flow imbalance data
        divergence_data: Smart money divergence data
    
    Returns:
        Dict with aggregated flow signals
    """
    # Flow imbalance (directional bias)
    imbalance = float(flow_data.get("overall_imbalance", 0))
    # Convert from [-1, 1] to [0, 1]
    flow_bias = (imbalance + 1.0) / 2.0
    
    # Smart money divergence (can be contrarian indicator)
    divergence = divergence_data.get("divergence", "none")
    divergence_confidence = float(divergence_data.get("confidence", 0))
    
    # Divergence scoring
    if divergence == "bullish_divergence":
        divergence_score = 0.5 + 0.5 * divergence_confidence  # Bullish
    elif divergence == "bearish_divergence":
        divergence_score = 0.5 - 0.5 * divergence_confidence  # Bearish
    else:
        divergence_score = 0.5  # Neutral
    
    # Flow signal (combination)
    flow_signal = 0.6 * flow_bias + 0.4 * divergence_score
    
    return {
        "flow_signal": round(flow_signal, 3),
        "direction": "bullish" if flow_signal > 0.6 else "bearish" if flow_signal < 0.4 else "neutral",
        "components": {
            "flow_bias": round(flow_bias, 3),
            "divergence_score": round(divergence_score, 3),
        },
    }


def aggregate_regime_signals(
    regime_data: Dict,
) -> Dict:
    """Aggregate regime-related signals.
    
    Args:
        regime_data: Regime detection data
    
    Returns:
        Dict with aggregated regime signals
    """
    regime = regime_data.get("regime", "normal")
    confidence = float(regime_data.get("confidence", 0.5))
    
    # Regime scoring (how favorable for trading)
    regime_scores = {
        "low_vol": 0.8,      # Good for theta strategies
        "normal": 0.7,       # Baseline
        "high_vol": 0.5,     # Risky but opportunity
        "transitioning": 0.3,  # Avoid
    }
    
    regime_score = regime_scores.get(regime, 0.5)
    
    # Weight by confidence
    regime_signal = regime_score * confidence + 0.5 * (1 - confidence)
    
    return {
        "regime_signal": round(regime_signal, 3),
        "regime": regime,
        "regime_confidence": round(confidence, 3),
    }


def calculate_composite_score(
    cluster_signals: Dict,
    flow_signals: Dict,
    regime_signals: Dict,
    weights: Optional[Dict[str, float]] = None,
) -> Dict:
    """Calculate composite score from all signal categories.
    
    Args:
        cluster_signals: Aggregated cluster signals
        flow_signals: Aggregated flow signals
        regime_signals: Aggregated regime signals
        weights: Category weights (default: equal weight)
    
    Returns:
        Dict with composite score and breakdown
    """
    if weights is None:
        weights = {
            "cluster": 0.50,  # Clusters are primary signal
            "flow": 0.30,     # Flow is secondary
            "regime": 0.20,   # Regime is context
        }
    
    cluster_signal = float(cluster_signals.get("cluster_signal", 0.5))
    flow_signal = float(flow_signals.get("flow_signal", 0.5))
    regime_signal = float(regime_signals.get("regime_signal", 0.5))
    
    composite = (
        weights["cluster"] * cluster_signal +
        weights["flow"] * flow_signal +
        weights["regime"] * regime_signal
    )
    
    # Convert to 0-100 scale for display
    composite_score = composite * 100
    
    # Generate recommendation
    if composite_score >= 70:
        recommendation = "STRONG SIGNAL - High conviction setup"
    elif composite_score >= 55:
        recommendation = "MODERATE SIGNAL - Consider position"
    elif composite_score >= 45:
        recommendation = "WEAK SIGNAL - Monitor only"
    else:
        recommendation = "NO SIGNAL - Avoid trading"
    
    return {
        "composite_score": round(composite_score, 1),
        "recommendation": recommendation,
        "breakdown": {
            "cluster_signal": round(cluster_signal * 100, 1),
            "flow_signal": round(flow_signal * 100, 1),
            "regime_signal": round(regime_signal * 100, 1),
        },
        "weights": weights,
    }


def generate_full_analysis(ticker: str) -> Dict:
    """Generate complete signal aggregation analysis for a ticker.
    
    Args:
        ticker: Ticker symbol
    
    Returns:
        Dict with full analysis
    """
    import urllib.request
    
    try:
        # Fetch matrix data
        req = urllib.request.Request(
            f"http://127.0.0.1:8282/api/matrix?ticker={ticker}&expirations=6&depth=0.06",
            headers={"Accept": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            matrix_data = json.loads(resp.read().decode())
        
        rows = matrix_data.get("rows", [])
        expirations = matrix_data.get("expirations", [])
        spot = (matrix_data.get("quote") or {}).get("price_context", 0)
        
        if not rows or not spot:
            return {"error": "No data available"}
        
        # Import analysis modules
        from scanner import _strike_profile, _detect_cluster_zones
        from flow_imbalance import analyze_strike_flow
        from regime_detector import detect_regime, calculate_realized_volatility
        
        # Fetch bars for volatility
        req = urllib.request.Request(
            f"http://127.0.0.1:8282/api/bars?ticker={ticker}&timeframe=1D&limit=30",
            headers={"Accept": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            bars_data = json.loads(resp.read().decode())
        
        bars = bars_data.get("bars", [])
        prices = [bar.get("close", 0) for bar in bars if bar.get("close")]
        
        # Detect clusters
        profile = _strike_profile(rows, expiration_index=0, expirations=expirations)
        zones = _detect_cluster_zones(profile, spot)
        
        # Analyze flow
        flow_data = analyze_strike_flow(rows, spot)
        
        # Detect regime
        regime_data = detect_regime(prices)
        
        # For now, use placeholder data for confidence and decay
        # (Would integrate full modules in production)
        confidence_data = {"confidence_score": 0.6}
        decay_data = {"lifecycle_stage": "mature"}
        
        # Aggregate signals
        if zones:
            cluster = zones[0]  # Best cluster
            cluster_signals = aggregate_cluster_signals(cluster, confidence_data, decay_data)
        else:
            cluster_signals = {"cluster_signal": 0.0, "components": {}}
        
        # Smart money divergence (placeholder)
        divergence_data = {"divergence": "none", "confidence": 0.0}
        flow_signals = aggregate_flow_signals(flow_data, divergence_data)
        
        regime_signals = aggregate_regime_signals(regime_data)
        
        # Composite score
        composite = calculate_composite_score(cluster_signals, flow_signals, regime_signals)
        
        return {
            "ticker": ticker,
            "spot": spot,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "cluster_count": len(zones),
            "cluster_signals": cluster_signals,
            "flow_signals": flow_signals,
            "regime_signals": regime_signals,
            "composite": composite,
        }
    
    except Exception as e:
        return {"error": str(e)}


def format_composite_report(analysis: Dict) -> str:
    """Generate human-readable composite report."""
    if "error" in analysis:
        return f"Error: {analysis['error']}"
    
    lines = [
        "=" * 70,
        "STRATEGY SIGNAL AGGREGATION",
        f"Ticker: {analysis.get('ticker', 'N/A')}",
        f"Spot: ${analysis.get('spot', 0):.2f}",
        f"Time: {analysis.get('timestamp', '?')[:19]}",
        "=" * 70,
        "",
        "COMPOSITE SCORE",
        "-" * 40,
        f"Score: {analysis.get('composite', {}).get('composite_score', 0):.1f} / 100",
        f"Recommendation: {analysis.get('composite', {}).get('recommendation', 'N/A')}",
        "",
        "SIGNAL BREAKDOWN",
        "-" * 40,
    ]
    
    breakdown = analysis.get("composite", {}).get("breakdown", {})
    weights = analysis.get("composite", {}).get("weights", {})
    
    lines.extend([
        f"Cluster Signal: {breakdown.get('cluster_signal', 0):.1f} (weight: {weights.get('cluster', 0):.0%})",
        f"Flow Signal: {breakdown.get('flow_signal', 0):.1f} (weight: {weights.get('flow', 0):.0%})",
        f"Regime Signal: {breakdown.get('regime_signal', 0):.1f} (weight: {weights.get('regime', 0):.0%})",
        "",
        "DETAILED SIGNALS",
        "-" * 40,
    ])
    
    # Cluster signals
    cluster_signals = analysis.get("cluster_signals", {})
    lines.append(f"Cluster Signal: {cluster_signals.get('cluster_signal', 0):.3f}")
    for key, val in cluster_signals.get("components", {}).items():
        lines.append(f"  - {key}: {val:.3f}")
    
    # Flow signals
    flow_signals = analysis.get("flow_signals", {})
    lines.append(f"\nFlow Signal: {flow_signals.get('flow_signal', 0):.3f}")
    lines.append(f"  Direction: {flow_signals.get('direction', 'unknown')}")
    
    # Regime signals
    regime_signals = analysis.get("regime_signals", {})
    lines.append(f"\nRegime Signal: {regime_signals.get('regime_signal', 0):.3f}")
    lines.append(f"  Regime: {regime_signals.get('regime', 'unknown')}")
    lines.append(f"  Confidence: {regime_signals.get('regime_confidence', 0):.1%}")
    
    lines.append("=" * 70)
    return "\n".join(lines)


# CLI test
if __name__ == "__main__":
    ticker = sys.argv[1] if len(sys.argv) > 1 else "SPY"
    
    print(f"Strategy Signal Aggregation for {ticker}")
    print("Analyzing signals...")
    
    try:
        analysis = generate_full_analysis(ticker)
        report = format_composite_report(analysis)
        print(report)
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
