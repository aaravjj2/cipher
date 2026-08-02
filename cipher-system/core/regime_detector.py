"""
Adaptive Volatility Regime Detection.

Classifies market conditions into regimes:
- Low Vol: Quiet markets, mean reversion strategies work well
- High Vol: Turbulent markets, momentum/breakout strategies favored
- Transitioning: Regime change in progress, reduce position sizes
- Normal: Baseline conditions, balanced approach

Uses realized volatility, IV rank, and volatility momentum to classify.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import math

sys.path.insert(0, str(Path(__file__).parent.parent / "cipher-system" / "core"))


def calculate_realized_volatility(prices: List[float], window: int = 20) -> float:
    """Calculate realized volatility from price series.
    
    Args:
        prices: List of closing prices (oldest first)
        window: Lookback window in bars
    
    Returns:
        Annualized volatility as decimal (e.g., 0.25 for 25%)
    """
    if len(prices) < window + 1:
        return 0.0
    
    # Calculate log returns
    returns = []
    for i in range(1, len(prices)):
        if prices[i - 1] > 0:
            returns.append(math.log(prices[i] / prices[i - 1]))
    
    if len(returns) < window:
        return 0.0
    
    # Use most recent window
    recent_returns = returns[-window:]
    
    # Standard deviation
    mean = sum(recent_returns) / len(recent_returns)
    variance = sum((r - mean) ** 2 for r in recent_returns) / len(recent_returns)
    std_dev = math.sqrt(variance)
    
    # Annualize (assuming daily data, 252 trading days)
    annualized = std_dev * math.sqrt(252)
    return annualized


def calculate_iv_rank(iv_current: float, iv_history: List[float]) -> float:
    """Calculate IV rank (percentile of current IV in historical range).
    
    Args:
        iv_current: Current implied volatility
        iv_history: Historical IV values
    
    Returns:
        IV rank as 0-1 (0 = lowest, 1 = highest)
    """
    if not iv_history or len(iv_history) < 10:
        return 0.5  # Neutral if insufficient data
    
    sorted_iv = sorted(iv_history)
    rank = sum(1 for iv in sorted_iv if iv <= iv_current) / len(sorted_iv)
    return max(0.0, min(1.0, rank))


def calculate_volatility_momentum(rv_current: float, rv_history: List[float]) -> float:
    """Calculate volatility momentum (rate of change in realized vol).
    
    Args:
        rv_current: Current realized volatility
        rv_history: Historical realized vol values
    
    Returns:
        Momentum score -1 to 1 (negative = decreasing, positive = increasing)
    """
    if not rv_history or len(rv_history) < 5:
        return 0.0
    
    # Compare current to average of recent history
    recent_avg = sum(rv_history[-5:]) / len(rv_history[-5:])
    
    if recent_avg == 0:
        return 0.0
    
    # Rate of change, scaled to [-1, 1]
    roc = (rv_current - recent_avg) / recent_avg
    
    # Clamp and scale
    return max(-1.0, min(1.0, roc * 2.0))


def detect_regime(
    prices: List[float],
    iv_current: float = 0.0,
    iv_history: Optional[List[float]] = None,
    rv_history: Optional[List[float]] = None,
) -> Dict:
    """Detect current volatility regime.
    
    Args:
        prices: Recent price series (oldest first)
        iv_current: Current implied volatility
        iv_history: Historical IV values
        rv_history: Historical realized vol values
    
    Returns:
        Dict with regime classification and metrics
    """
    # Calculate realized volatility
    rv_current = calculate_realized_volatility(prices)
    
    # Calculate IV rank
    iv_rank = calculate_iv_rank(iv_current, iv_history or [])
    
    # Calculate vol momentum
    vol_momentum = calculate_volatility_momentum(rv_current, rv_history or [])
    
    # Classify regime based on metrics
    # High vol: RV > 30% OR IV rank > 0.8
    # Low vol: RV < 15% AND IV rank < 0.3
    # Transitioning: |vol momentum| > 0.5
    # Normal: everything else
    
    if rv_current > 0.30 or iv_rank > 0.8:
        regime = "high_vol"
        confidence = 0.7 + (0.3 * iv_rank)
    elif rv_current < 0.15 and iv_rank < 0.3:
        regime = "low_vol"
        confidence = 0.7 + (0.3 * (1 - iv_rank))
    elif abs(vol_momentum) > 0.5:
        regime = "transitioning"
        confidence = 0.5 + (0.3 * abs(vol_momentum))
    else:
        regime = "normal"
        confidence = 0.6
    
    # Strategy recommendations
    recommendations = {
        "high_vol": {
            "preferred_strategies": ["breakout", "momentum", "vol_risk_premium"],
            "avoid_strategies": ["mean_reversion", "theta_harvest"],
            "position_size_multiplier": 0.7,  # Reduce size in high vol
            "widen_stops": True,
        },
        "low_vol": {
            "preferred_strategies": ["mean_reversion", "theta_harvest", "iron_condor"],
            "avoid_strategies": ["breakout", "momentum"],
            "position_size_multiplier": 1.2,  # Can increase size in low vol
            "widen_stops": False,
        },
        "transitioning": {
            "preferred_strategies": ["vol_regime_switch", "straddle"],
            "avoid_strategies": ["directional", "theta_harvest"],
            "position_size_multiplier": 0.5,  # Significantly reduce during transitions
            "widen_stops": True,
        },
        "normal": {
            "preferred_strategies": ["balanced"],
            "avoid_strategies": [],
            "position_size_multiplier": 1.0,
            "widen_stops": False,
        },
    }
    
    return {
        "regime": regime,
        "confidence": round(confidence, 3),
        "metrics": {
            "realized_volatility": round(rv_current, 4),
            "iv_rank": round(iv_rank, 3),
            "vol_momentum": round(vol_momentum, 3),
        },
        "recommendations": recommendations.get(regime, recommendations["normal"]),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def format_regime_report(regime_data: Dict) -> str:
    """Generate human-readable regime report."""
    regime = regime_data.get("regime", "unknown")
    confidence = regime_data.get("confidence", 0)
    metrics = regime_data.get("metrics", {})
    recs = regime_data.get("recommendations", {})
    
    lines = [
        "=" * 70,
        "VOLATILITY REGIME ANALYSIS",
        "=" * 70,
        "",
        f"Current Regime: {regime.upper()}",
        f"Confidence: {confidence:.1%}",
        "",
        "METRICS",
        "-" * 40,
        f"Realized Volatility: {metrics.get('realized_volatility', 0):.2%}",
        f"IV Rank: {metrics.get('iv_rank', 0):.1%}",
        f"Vol Momentum: {metrics.get('vol_momentum', 0):+.2f}",
        "",
        "RECOMMENDATIONS",
        "-" * 40,
        f"Position Size: {recs.get('position_size_multiplier', 1.0):.1%} of normal",
        f"Widen Stops: {'Yes' if recs.get('widen_stops') else 'No'}",
        "",
        "Preferred Strategies:",
    ]
    
    for strat in recs.get("preferred_strategies", []):
        lines.append(f"  ✓ {strat}")
    
    lines.append("")
    lines.append("Avoid Strategies:")
    for strat in recs.get("avoid_strategies", []):
        lines.append(f"  ✗ {strat}")
    
    lines.append("=" * 70)
    return "\n".join(lines)


# CLI test
if __name__ == "__main__":
    import urllib.request
    
    ticker = sys.argv[1] if len(sys.argv) > 1 else "SPY"
    
    print(f"Regime Detection for {ticker}")
    print("Fetching data from local core...")
    
    try:
        # Fetch historical bars for volatility calculation
        req = urllib.request.Request(
            f"http://127.0.0.1:8282/api/bars?ticker={ticker}&timeframe=1D&limit=60",
            headers={"Accept": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            bars_data = json.loads(resp.read().decode())
        
        bars = bars_data.get("bars", [])
        if not bars:
            print("No bar data returned")
            sys.exit(1)
        
        # Extract close prices
        prices = [bar.get("close", 0) for bar in bars if bar.get("close")]
        
        # Detect regime (without IV data for now)
        regime_data = detect_regime(prices)
        report = format_regime_report(regime_data)
        print(report)
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
