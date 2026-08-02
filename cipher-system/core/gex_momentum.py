"""
GEX Momentum Indicators.

Calculates velocity (rate of change) and acceleration (change in velocity)
of GEX values at strike levels. These indicators help predict cluster
formation and dissolution.

GEX is a public-OI heuristic — not verified dealer positioning.
"""

import os
import sqlite3
import numpy as np
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "gex_history.sqlite"


def get_gex_timeseries(
    ticker: str,
    strike: float,
    limit: int = 20,
    db_path: Path = DB_PATH,
) -> List[Tuple[str, float]]:
    """Get time-ordered GEX history for a ticker/strike.
    
    Returns list of (timestamp, net_gex) tuples, oldest first.
    """
    if not db_path.exists():
        return []
    
    try:
        conn = sqlite3.connect(db_path)
        query = """
            SELECT c.captured_at, c.net_gex
            FROM gex_strike_cells c
            WHERE c.ticker = ? AND ABS(c.strike - ?) < 0.01
            ORDER BY c.captured_at ASC
            LIMIT ?
        """
        cursor = conn.execute(query, (ticker, strike, limit))
        rows = [(row[0], row[1] or 0.0) for row in cursor.fetchall()]
        conn.close()
        return rows
    except Exception:
        return []


def calculate_momentum(values: List[float]) -> Dict[str, float]:
    """Calculate momentum indicators for a time series.
    
    Returns:
        velocity: Rate of change (last - first) / n
        acceleration: Change in velocity (recent vs older)
        trend: 'increasing', 'decreasing', or 'stable'
        momentum_score: Combined score (-1 to 1)
    """
    if len(values) < 2:
        return {"velocity": 0.0, "acceleration": 0.0, "trend": "unknown", "momentum_score": 0.0}
    
    n = len(values)
    
    # Velocity: linear regression slope (simplified)
    # Using simple (last - first) / n for efficiency
    velocity = (values[-1] - values[0]) / n
    
    # Acceleration: compare recent velocity to older velocity
    if n >= 4:
        mid = n // 2
        recent_vel = (values[-1] - values[mid]) / (n - mid)
        older_vel = (values[mid] - values[0]) / mid
        acceleration = (recent_vel - older_vel) / (n / 2)
    else:
        acceleration = 0.0
    
    # Trend determination
    abs_range = max(values) - min(values)
    mean_val = np.mean(values)
    relative_change = abs_range / (abs(mean_val) + 1e-8)
    
    if velocity > 0 and relative_change > 0.05:
        trend = "increasing"
    elif velocity < 0 and relative_change > 0.05:
        trend = "decreasing"
    else:
        trend = "stable"
    
    # Momentum score: normalized combination of velocity and acceleration
    # Scale by the magnitude of GEX values
    scale = max(abs(mean_val), 1e6)  # At least 1M for scaling
    norm_vel = np.clip(velocity / scale, -1, 1)
    norm_acc = np.clip(acceleration / scale, -1, 1)
    momentum_score = 0.7 * norm_vel + 0.3 * norm_acc
    
    return {
        "velocity": float(velocity),
        "acceleration": float(acceleration),
        "trend": trend,
        "momentum_score": float(np.clip(momentum_score, -1, 1)),
        "n_points": n,
    }


def calculate_strike_momentum(
    ticker: str,
    strikes: List[float],
    spot: float,
    limit: int = 10,
) -> Dict[str, Dict]:
    """Calculate momentum for multiple strikes.
    
    Returns dict mapping strike -> momentum indicators.
    """
    results = {}
    
    for strike in strikes:
        timeseries = get_gex_timeseries(ticker, strike, limit=limit)
        if len(timeseries) >= 2:
            values = [v for _, v in timeseries]
            momentum = calculate_momentum(values)
            momentum["latest_gex"] = values[-1]
            momentum["latest_time"] = timeseries[-1][0]
            results[str(strike)] = momentum
    
    return results


def calculate_cluster_momentum(
    ticker: str,
    cluster: Dict,
    spot: float,
    limit: int = 10,
) -> Optional[Dict]:
    """Calculate momentum for a cluster's strikes.
    
    Aggregates momentum across all cluster strikes to determine
    if the cluster is strengthening or weakening.
    
    Returns:
        Dict with cluster-level momentum indicators.
    """
    strikes = cluster.get("strikes", [])
    if not strikes:
        return None
    
    strike_momentum = calculate_strike_momentum(ticker, strikes, spot, limit)
    
    if not strike_momentum:
        return None
    
    # Aggregate metrics
    velocities = [m["velocity"] for m in strike_momentum.values()]
    accelerations = [m["acceleration"] for m in strike_momentum.values()]
    momentum_scores = [m["momentum_score"] for m in strike_momentum.values()]
    
    avg_velocity = np.mean(velocities)
    avg_acceleration = np.mean(accelerations)
    avg_momentum = np.mean(momentum_scores)
    
    # Cluster trend
    # Positive momentum for negative GEX (put-heavy) = bearish strengthening
    # Positive momentum for positive GEX (call-heavy) = bullish strengthening
    net_gex = cluster.get("net_gex", 0)
    side = cluster.get("side", "above")
    
    if side == "below":
        # Downside cluster: negative GEX is typical
        # If GEX becomes more negative → strengthening
        # If GEX becomes less negative → weakening
        if avg_velocity < 0:
            cluster_trend = "strengthening"
        elif avg_velocity > 0:
            cluster_trend = "weakening"
        else:
            cluster_trend = "stable"
    else:
        # Upside cluster: positive GEX is typical
        if avg_velocity > 0:
            cluster_trend = "strengthening"
        elif avg_velocity < 0:
            cluster_trend = "weakening"
        else:
            cluster_trend = "stable"
    
    return {
        "cluster_trend": cluster_trend,
        "avg_velocity": float(avg_velocity),
        "avg_acceleration": float(avg_acceleration),
        "avg_momentum_score": float(avg_momentum),
        "strike_count": len(strike_momentum),
        "strikes": {
            k: {
                "velocity": v["velocity"],
                "trend": v["trend"],
                "latest_gex": v["latest_gex"],
            }
            for k, v in strike_momentum.items()
        },
    }


def predict_cluster_lifecycle(
    momentum: Dict,
    strength: float,
) -> str:
    """Predict cluster lifecycle stage based on momentum.
    
    Returns one of:
        - 'forming': Cluster is building strength
        - 'mature': Cluster at peak strength
        - 'decaying': Cluster losing strength
        - 'dissolving': Cluster about to disappear
    """
    if not momentum:
        return "unknown"
    
    trend = momentum.get("cluster_trend", "stable")
    mom_score = momentum.get("avg_momentum_score", 0)
    accel = momentum.get("avg_acceleration", 0)
    
    # Lifecycle logic
    if trend == "strengthening" and accel > 0:
        return "forming"
    elif trend == "strengthening" and accel <= 0:
        return "mature"
    elif trend == "weakening" and abs(mom_score) < 0.3:
        return "dissolving"
    elif trend == "weakening":
        return "decaying"
    else:
        return "mature"


# CLI test
if __name__ == "__main__":
    import sys
    
    ticker = sys.argv[1] if len(sys.argv) > 1 else "QQQ"
    strikes = [667.0, 670.0, 672.0, 675.0, 680.0]
    
    print(f"GEX Momentum for {ticker}")
    print("=" * 60)
    
    for strike in strikes:
        ts = get_gex_timeseries(ticker, strike, limit=10)
        if len(ts) >= 2:
            values = [v for _, v in ts]
            mom = calculate_momentum(values)
            print(f"  ${strike}:")
            print(f"    GEX series: {[f'{v:.2e}' for v in values[:5]]}...")
            print(f"    Velocity: {mom['velocity']:.2e}")
            print(f"    Acceleration: {mom['acceleration']:.2e}")
            print(f"    Trend: {mom['trend']}")
            print(f"    Momentum score: {mom['momentum_score']:.3f}")
        else:
            print(f"  ${strike}: insufficient data ({len(ts)} points)")
