"""
Cluster Decay and Half-Life Modeling.

Models how GEX clusters weaken over time based on:
- Time to expiration (faster decay as expiration approaches)
- Cluster strength (stronger clusters persist longer)
- Market regime (high vol accelerates decay)
- Volume/OI changes (declining OI = weakening cluster)

Half-life = time for cluster strength to reduce by 50%
"""

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
import statistics

sys.path.insert(0, str(Path(__file__).parent.parent / "cipher-system" / "core"))


def calculate_base_half_life(dte: int, cluster_kind: str) -> float:
    """Calculate base half-life in hours based on DTE and cluster type.
    
    Args:
        dte: Days to expiration
        cluster_kind: Type of cluster (quad, triple, battle, etc.)
    
    Returns:
        Half-life in hours
    """
    # Base half-life by DTE
    if dte <= 0:
        # 0DTE: very short half-life
        base_hours = 2.0
    elif dte <= 2:
        # 1-2 DTE: short half-life
        base_hours = 6.0
    elif dte <= 5:
        # 3-5 DTE: medium half-life
        base_hours = 12.0
    elif dte <= 10:
        # 6-10 DTE: longer half-life
        base_hours = 24.0
    else:
        # 10+ DTE: long half-life
        base_hours = 48.0
    
    # Adjust by cluster kind (stronger clusters persist longer)
    kind_multipliers = {
        "quad": 1.5,      # Strongest
        "triple": 1.3,
        "battle": 1.1,
        "golden": 1.0,
        "call_wall": 0.9,
        "put_floor": 0.9,
    }
    
    multiplier = kind_multipliers.get(cluster_kind.lower(), 1.0)
    return base_hours * multiplier


def calculate_decay_rate(half_life_hours: float) -> float:
    """Calculate exponential decay rate from half-life.
    
    Args:
        half_life_hours: Half-life in hours
    
    Returns:
        Decay rate (lambda) for exponential decay
    """
    if half_life_hours <= 0:
        return 1.0
    
    # Exponential decay: N(t) = N0 * e^(-lambda * t)
    # At half-life: N0/2 = N0 * e^(-lambda * t_half)
    # lambda = ln(2) / t_half
    return math.log(2) / half_life_hours


def predict_cluster_strength(
    initial_strength: float,
    hours_elapsed: float,
    half_life_hours: float,
    regime_factor: float = 1.0,
) -> float:
    """Predict cluster strength after time has elapsed.
    
    Args:
        initial_strength: Initial cluster strength
        hours_elapsed: Hours since cluster formation
        half_life_hours: Cluster half-life
        regime_factor: Regime adjustment (high vol = faster decay)
    
    Returns:
        Predicted current strength
    """
    decay_rate = calculate_decay_rate(half_life_hours)
    
    # Adjust decay rate by regime
    adjusted_decay = decay_rate * regime_factor
    
    # Exponential decay
    current_strength = initial_strength * math.exp(-adjusted_decay * hours_elapsed)
    
    return max(0.0, current_strength)


def estimate_cluster_age(
    current_strength: float,
    initial_strength: float,
    half_life_hours: float,
) -> float:
    """Estimate how old a cluster is based on current strength.
    
    Args:
        current_strength: Current cluster strength
        initial_strength: Estimated initial strength
        half_life_hours: Cluster half-life
    
    Returns:
        Estimated age in hours
    """
    if current_strength <= 0 or initial_strength <= 0:
        return float("inf")
    
    if current_strength >= initial_strength:
        return 0.0
    
    decay_rate = calculate_decay_rate(half_life_hours)
    
    # current = initial * e^(-decay * age)
    # age = -ln(current/initial) / decay
    age_hours = -math.log(current_strength / initial_strength) / decay_rate
    
    return max(0.0, age_hours)


def calculate_regime_decay_factor(regime: str) -> float:
    """Calculate decay acceleration factor based on market regime.
    
    Args:
        regime: Market regime (high_vol, low_vol, normal, transitioning)
    
    Returns:
        Factor to multiply decay rate (1.0 = normal)
    """
    factors = {
        "high_vol": 1.5,        # High vol accelerates decay
        "low_vol": 0.7,         # Low vol slows decay
        "transitioning": 2.0,   # Transitions accelerate decay significantly
        "normal": 1.0,
    }
    return factors.get(regime, 1.0)


def analyze_cluster_lifecycle(
    cluster: Dict,
    spot: float,
    regime: str = "normal",
    hours_since_capture: Optional[float] = None,
) -> Dict:
    """Analyze cluster lifecycle stage and predict future behavior.
    
    Args:
        cluster: Cluster data with strength, kind, strikes, etc.
        spot: Current spot price
        regime: Current market regime
        hours_since_capture: Hours since GEX snapshot (if known)
    
    Returns:
        Dict with lifecycle analysis and predictions
    """
    # Extract cluster info
    strength = float(cluster.get("strength", 0))
    kind = cluster.get("kind", "unknown")
    strikes = cluster.get("strikes", [])
    
    if not strikes:
        return {"error": "No strikes in cluster"}
    
    # Calculate average strike for DTE estimation
    avg_strike = sum(strikes) / len(strikes)
    
    # Estimate DTE (simplified - would need expiration data for accuracy)
    # For now, assume 3 DTE as default
    estimated_dte = 3
    
    # Calculate base half-life
    base_half_life = calculate_base_half_life(estimated_dte, kind)
    
    # Adjust for regime
    regime_factor = calculate_regime_decay_factor(regime)
    adjusted_half_life = base_half_life / regime_factor
    
    # If we know hours since capture, predict current strength
    if hours_since_capture is not None:
        # Assume initial strength was 20% higher
        estimated_initial = strength * 1.2
        predicted_strength = predict_cluster_strength(
            estimated_initial, hours_since_capture, adjusted_half_life, regime_factor
        )
        strength_decay_pct = 1.0 - (predicted_strength / estimated_initial)
    else:
        predicted_strength = strength
        strength_decay_pct = 0.0
    
    # Estimate cluster age (assuming initial strength ~2x current)
    estimated_initial = strength * 2.0
    estimated_age = estimate_cluster_age(strength, estimated_initial, adjusted_half_life)
    
    # Predict future strength at key timepoints
    predictions = {}
    for hours_ahead in [1, 2, 4, 8]:
        future_strength = predict_cluster_strength(
            strength, hours_ahead, adjusted_half_life, regime_factor
        )
        predictions[f"{hours_ahead}h_ahead"] = round(future_strength, 2)
    
    # Classify lifecycle stage
    if estimated_age < adjusted_half_life * 0.3:
        stage = "forming"
    elif estimated_age < adjusted_half_life * 0.7:
        stage = "mature"
    elif estimated_age < adjusted_half_life * 1.5:
        stage = "decaying"
    else:
        stage = "dissolving"
    
    # Confidence in cluster persistence
    persistence_confidence = max(0.0, min(1.0, 1.0 - (estimated_age / (adjusted_half_life * 2))))
    
    return {
        "cluster_kind": kind,
        "current_strength": round(strength, 2),
        "predicted_strength": round(predicted_strength, 2),
        "strength_decay_pct": round(strength_decay_pct * 100, 1),
        "half_life_hours": round(adjusted_half_life, 1),
        "estimated_age_hours": round(estimated_age, 1),
        "lifecycle_stage": stage,
        "persistence_confidence": round(persistence_confidence, 3),
        "regime_factor": regime_factor,
        "future_predictions": predictions,
        "recommendation": _get_lifecycle_recommendation(stage, persistence_confidence),
    }


def _get_lifecycle_recommendation(stage: str, confidence: float) -> str:
    """Get trading recommendation based on lifecycle stage."""
    if stage == "forming" and confidence > 0.7:
        return "Cluster forming - monitor for confirmation"
    elif stage == "mature" and confidence > 0.5:
        return "Cluster mature - reliable support/resistance"
    elif stage == "decaying":
        return "Cluster decaying - reduce reliance"
    elif stage == "dissolving":
        return "Cluster dissolving - do not use"
    else:
        return "Monitor cluster development"


def format_lifecycle_report(analysis: Dict) -> str:
    """Generate human-readable lifecycle report."""
    lines = [
        "=" * 70,
        "CLUSTER LIFECYCLE ANALYSIS",
        "=" * 70,
        "",
        f"Cluster Type: {analysis.get('cluster_kind', 'unknown')}",
        f"Lifecycle Stage: {analysis.get('lifecycle_stage', 'unknown').upper()}",
        f"Persistence Confidence: {analysis.get('persistence_confidence', 0):.1%}",
        "",
        "STRENGTH ANALYSIS",
        "-" * 40,
        f"Current Strength: {analysis.get('current_strength', 0):.2f}",
        f"Predicted Strength: {analysis.get('predicted_strength', 0):.2f}",
        f"Strength Decay: {analysis.get('strength_decay_pct', 0):.1f}%",
        "",
        "TIMING",
        "-" * 40,
        f"Half-Life: {analysis.get('half_life_hours', 0):.1f} hours",
        f"Estimated Age: {analysis.get('estimated_age_hours', 0):.1f} hours",
        f"Regime Factor: {analysis.get('regime_factor', 1.0):.2f}x",
        "",
        "FUTURE PREDICTIONS",
        "-" * 40,
    ]
    
    predictions = analysis.get("future_predictions", {})
    for key, value in predictions.items():
        lines.append(f"{key}: {value:.2f}")
    
    lines.extend([
        "",
        "RECOMMENDATION",
        "-" * 40,
        analysis.get("recommendation", "No recommendation"),
        "=" * 70,
    ])
    
    return "\n".join(lines)


# CLI test
if __name__ == "__main__":
    import urllib.request
    
    ticker = sys.argv[1] if len(sys.argv) > 1 else "NVDA"
    
    print(f"Cluster Lifecycle Analysis for {ticker}")
    print("Fetching data from local core...")
    
    try:
        # Fetch matrix data
        req = urllib.request.Request(
            f"http://127.0.0.1:8282/api/matrix?ticker={ticker}&expirations=6&depth=0.06",
            headers={"Accept": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())
        
        rows = data.get("rows", [])
        expirations = data.get("expirations", [])
        spot = (data.get("quote") or {}).get("price_context", 0)
        
        if not rows or not spot:
            print("No data returned")
            sys.exit(1)
        
        # Import scanner functions
        from scanner import _strike_profile, _detect_cluster_zones
        
        # Detect clusters
        profile = _strike_profile(rows, expiration_index=0, expirations=expirations)
        zones = _detect_cluster_zones(profile, spot)
        
        if not zones:
            print("No clusters detected")
            sys.exit(0)
        
        # Analyze first cluster
        cluster = zones[0]
        analysis = analyze_cluster_lifecycle(cluster, spot, regime="normal")
        report = format_lifecycle_report(analysis)
        print(report)
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
