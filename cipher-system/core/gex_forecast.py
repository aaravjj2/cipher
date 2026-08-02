"""
GEX Forecasting using fine-tuned TimesFM 2.5.

Predicts future GEX values for strikes based on historical snapshots.
Used by the cluster scanner to anticipate cluster formation/dissolution.

GEX is a public-OI heuristic — not verified dealer positioning.
"""

import os
import sqlite3
import numpy as np
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta

# Lazy-load TimesFM to avoid import overhead when not needed
_tfm = None
_model = None
_config = None

MODEL_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "timesfm_model")
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "gex_history.sqlite")

# Forecast config
CONTEXT_LEN = 4       # Use last 4 snapshots as context
HORIZON_LEN = 2       # Predict 2 steps ahead
MIN_HISTORY = 4       # Minimum snapshots needed for forecast


def _load_model():
    """Lazy-load the fine-tuned TimesFM model."""
    global _tfm, _model, _config
    
    if _tfm is not None:
        return _tfm
    
    try:
        from timesfm import TimesFM_2p5_200M_torch, ForecastConfig
        import torch
        import json
        
        # Load config
        config_path = os.path.join(MODEL_DIR, "finetune_config.json")
        if os.path.exists(config_path):
            with open(config_path) as f:
                _config = json.load(f)
        
        # Load base model
        _tfm = TimesFM_2p5_200M_torch.from_pretrained("google/timesfm-2.5-200m-pytorch")
        
        # Load fine-tuned weights if available
        weights_path = os.path.join(MODEL_DIR, "timesfm_gex_finetuned.pt")
        if os.path.exists(weights_path):
            state_dict = torch.load(weights_path, map_location="cpu")
            _tfm.model.load_state_dict(state_dict)
        
        # Compile for inference
        _tfm.compile(ForecastConfig(max_context=32, max_horizon=128))
        
        return _tfm
    except Exception as e:
        print(f"[gex_forecast] Failed to load TimesFM: {e}")
        return None


def get_gex_history(ticker: str, strike: float, limit: int = 10) -> List[float]:
    """Get recent GEX history for a ticker/strike from the database."""
    if not os.path.exists(DB_PATH):
        return []
    
    try:
        conn = sqlite3.connect(DB_PATH)
        query = """
            SELECT c.net_gex
            FROM gex_strike_cells c
            JOIN gex_snapshots s ON c.snapshot_id = s.id
            WHERE c.ticker = ? AND ABS(c.strike - ?) < 0.01
            ORDER BY c.captured_at DESC
            LIMIT ?
        """
        cursor = conn.execute(query, (ticker, strike, limit))
        rows = cursor.fetchall()
        conn.close()
        
        # Return in chronological order (oldest first)
        return [row[0] for row in reversed(rows)]
    except Exception:
        return []


def forecast_strike_gex(ticker: str, strike: float) -> Optional[Dict]:
    """
    Forecast GEX for a specific strike.
    
    Returns:
        Dict with 'current', 'forecast', 'direction', 'confidence' or None
    """
    history = get_gex_history(ticker, strike, limit=CONTEXT_LEN + 2)
    
    if len(history) < MIN_HISTORY:
        return None
    
    tfm = _load_model()
    if tfm is None:
        return None
    
    # Use last CONTEXT_LEN values as context
    context = np.array(history[-CONTEXT_LEN:], dtype=np.float32)
    
    try:
        point_forecast, quantile_forecast = tfm.forecast(
            horizon=HORIZON_LEN,
            inputs=[context]
        )
        
        forecast_values = point_forecast[0]
        current = float(context[-1])
        predicted = float(forecast_values[0])
        
        # Determine direction
        if predicted > current * 1.05:
            direction = "increasing"
        elif predicted < current * 0.95:
            direction = "decreasing"
        else:
            direction = "stable"
        
        # Confidence based on quantile spread (narrower = more confident)
        q10 = float(quantile_forecast[0, 0, 1])  # 10th percentile
        q90 = float(quantile_forecast[0, 0, 8])  # 90th percentile
        spread = abs(q90 - q10)
        confidence = max(0.0, min(1.0, 1.0 - spread / (abs(current) + 1e-8)))
        
        return {
            "current": current,
            "forecast": predicted,
            "forecast_values": [float(v) for v in forecast_values],
            "direction": direction,
            "confidence": round(confidence, 3),
            "history": [float(v) for v in context],
        }
    except Exception as e:
        print(f"[gex_forecast] Forecast failed for {ticker}@{strike}: {e}")
        return None


def forecast_cluster_strikes(
    ticker: str,
    strikes: List[float],
    spot: float
) -> Dict[str, Dict]:
    """
    Forecast GEX for multiple strikes (e.g., cluster levels).
    
    Returns dict mapping strike -> forecast result.
    """
    results = {}
    for strike in strikes:
        forecast = forecast_strike_gex(ticker, strike)
        if forecast:
            results[str(strike)] = forecast
    return results


def predict_cluster_evolution(
    ticker: str,
    cluster: Dict,
    spot: float
) -> Optional[Dict]:
    """
    Predict how a cluster will evolve based on GEX forecasts.
    
    Args:
        ticker: Stock ticker
        cluster: Cluster dict with 'strikes', 'side', 'strength'
        spot: Current spot price
        
    Returns:
        Dict with 'trend', 'forecast_strength', 'confidence', 'strikes'
    """
    strikes = cluster.get("strikes", [])
    if not strikes:
        return None
    
    forecasts = forecast_cluster_strikes(ticker, strikes, spot)
    
    if not forecasts:
        return None
    
    # Aggregate forecasts
    total_current = 0.0
    total_forecast = 0.0
    confidences = []
    
    for strike_str, fc in forecasts.items():
        total_current += abs(fc["current"])
        total_forecast += abs(fc["forecast"])
        confidences.append(fc["confidence"])
    
    avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0
    
    # Determine trend
    if total_forecast > total_current * 1.1:
        trend = "strengthening"
    elif total_forecast < total_current * 0.9:
        trend = "weakening"
    else:
        trend = "stable"
    
    return {
        "trend": trend,
        "current_strength": round(total_current, 2),
        "forecast_strength": round(total_forecast, 2),
        "confidence": round(avg_confidence, 3),
        "strikes": {k: v["direction"] for k, v in forecasts.items()},
    }


# CLI test
if __name__ == "__main__":
    import sys
    
    ticker = sys.argv[1] if len(sys.argv) > 1 else "NVDA"
    strikes = [200.0, 202.5, 205.0, 210.0, 215.0]
    
    print(f"GEX Forecast for {ticker}")
    print("=" * 60)
    
    for strike in strikes:
        fc = forecast_strike_gex(ticker, strike)
        if fc:
            print(f"  ${strike}: current={fc['current']:.2e}, forecast={fc['forecast']:.2e}, "
                  f"dir={fc['direction']}, conf={fc['confidence']:.2f}")
        else:
            print(f"  ${strike}: insufficient history")
