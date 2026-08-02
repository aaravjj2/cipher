"""
Flow Imbalance Scoring and Directional Bias Detection.

Analyzes call/put ratios, volume imbalances, and OI shifts to detect:
- Bullish flow: Call volume > Put volume, call OI increasing
- Bearish flow: Put volume > Call volume, put OI increasing
- Neutral flow: Balanced activity

Imbalances at key strikes can indicate directional expectations.
"""

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import statistics

sys.path.insert(0, str(Path(__file__).parent.parent / "cipher-system" / "core"))


def calculate_call_put_ratio(rows: List[Dict], strike: float, window: float = 2.0) -> Dict:
    """Calculate call/put ratio at a specific strike.
    
    Args:
        rows: Strike profile rows (each row has strike + cells array)
        strike: Target strike
        window: Strike window (±window%)
    
    Returns:
        Dict with call/put ratios for OI, volume, and GEX
    """
    if not rows or strike <= 0:
        return {}
    
    # Filter rows near the strike
    lower = strike * (1 - window / 100)
    upper = strike * (1 + window / 100)
    
    call_oi = 0
    put_oi = 0
    call_vol = 0  # Volume is combined, not split by call/put
    put_vol = 0
    call_gex = 0
    put_gex = 0
    
    for row in rows:
        row_strike = float(row.get("strike", 0))
        if lower <= row_strike <= upper:
            # Each row has cells (one per expiration)
            cells = row.get("cells", [])
            for cell in cells:
                call_oi += float(cell.get("call_oi", 0) or 0)
                put_oi += float(cell.get("put_oi", 0) or 0)
                call_gex += abs(float(cell.get("call_gex", 0) or 0))
                put_gex += abs(float(cell.get("put_gex", 0) or 0))
                # Volume is not split by call/put in the data
                vol = float(cell.get("volume", 0) or 0)
                call_vol += vol / 2  # Approximate split
                put_vol += vol / 2
    
    # Calculate ratios (avoid division by zero)
    cp_ratio_oi = call_oi / put_oi if put_oi > 0 else (2.0 if call_oi > 0 else 1.0)
    cp_ratio_vol = call_vol / put_vol if put_vol > 0 else (2.0 if call_vol > 0 else 1.0)
    cp_ratio_gex = call_gex / put_gex if put_gex > 0 else (2.0 if call_gex > 0 else 1.0)
    
    return {
        "strike": strike,
        "call_oi": call_oi,
        "put_oi": put_oi,
        "call_volume": call_vol,
        "put_volume": put_vol,
        "call_gex": call_gex,
        "put_gex": put_gex,
        "cp_ratio_oi": round(cp_ratio_oi, 3),
        "cp_ratio_vol": round(cp_ratio_vol, 3),
        "cp_ratio_gex": round(cp_ratio_gex, 3),
    }


def calculate_flow_imbalance_score(cp_ratio: float) -> float:
    """Convert call/put ratio to imbalance score.
    
    Args:
        cp_ratio: Call/put ratio (1.0 = neutral, >1 = bullish, <1 = bearish)
    
    Returns:
        Imbalance score -1 to 1 (negative = bearish, positive = bullish)
    """
    if cp_ratio <= 0:
        return 0.0
    
    # Log scale to handle extreme ratios
    log_ratio = math.log(cp_ratio)
    
    # Scale to [-1, 1] range
    # cp_ratio = 0.5 -> score = -1
    # cp_ratio = 1.0 -> score = 0
    # cp_ratio = 2.0 -> score = +1
    score = max(-1.0, min(1.0, log_ratio / math.log(2)))
    
    return score


def analyze_strike_flow(rows: List[Dict], spot: float) -> Dict:
    """Analyze flow imbalance across multiple strikes around spot.
    
    Args:
        rows: Strike profile rows
        spot: Current spot price
    
    Returns:
        Dict with flow analysis at key strikes
    """
    if not rows or not spot:
        return {"error": "Insufficient data"}
    
    # Analyze at key levels
    key_strikes = [
        spot * 0.98,  # 2% below
        spot * 0.99,  # 1% below
        spot,         # ATM
        spot * 1.01,  # 1% above
        spot * 1.02,  # 2% above
    ]
    
    strike_flows = []
    for strike in key_strikes:
        cp_data = calculate_call_put_ratio(rows, strike)
        if cp_data:
            # Calculate imbalance scores
            oi_imbalance = calculate_flow_imbalance_score(cp_data["cp_ratio_oi"])
            vol_imbalance = calculate_flow_imbalance_score(cp_data["cp_ratio_vol"])
            gex_imbalance = calculate_flow_imbalance_score(cp_data["cp_ratio_gex"])
            
            # Weighted composite (volume most responsive, OI most stable)
            composite = (
                0.3 * oi_imbalance +
                0.4 * vol_imbalance +
                0.3 * gex_imbalance
            )
            
            strike_flows.append({
                "strike": round(strike, 2),
                "dist_from_spot_pct": round((strike - spot) / spot * 100, 2),
                "cp_ratio_oi": cp_data["cp_ratio_oi"],
                "cp_ratio_vol": cp_data["cp_ratio_vol"],
                "oi_imbalance": round(oi_imbalance, 3),
                "vol_imbalance": round(vol_imbalance, 3),
                "gex_imbalance": round(gex_imbalance, 3),
                "composite_imbalance": round(composite, 3),
            })
    
    # Overall flow bias (average across strikes)
    if strike_flows:
        avg_composite = sum(sf["composite_imbalance"] for sf in strike_flows) / len(strike_flows)
    else:
        avg_composite = 0.0
    
    # Classify overall bias
    if avg_composite > 0.3:
        bias = "bullish"
        confidence = min(1.0, avg_composite)
    elif avg_composite < -0.3:
        bias = "bearish"
        confidence = min(1.0, abs(avg_composite))
    else:
        bias = "neutral"
        confidence = 1.0 - abs(avg_composite)
    
    return {
        "spot": spot,
        "strike_flows": strike_flows,
        "overall_bias": bias,
        "overall_imbalance": round(avg_composite, 3),
        "confidence": round(confidence, 3),
        "interpretation": _interpret_flow_bias(bias, avg_composite),
    }


def _interpret_flow_bias(bias: str, imbalance: float) -> str:
    """Generate human-readable interpretation of flow bias."""
    if bias == "bullish":
        if imbalance > 0.6:
            return "Strong bullish flow - calls dominating"
        else:
            return "Moderate bullish flow - call activity elevated"
    elif bias == "bearish":
        if imbalance < -0.6:
            return "Strong bearish flow - puts dominating"
        else:
            return "Moderate bearish flow - put activity elevated"
    else:
        return "Neutral flow - balanced call/put activity"


def detect_unusual_flow(strike_flows: List[Dict], threshold: float = 0.5) -> List[Dict]:
    """Detect strikes with unusual flow imbalances.
    
    Args:
        strike_flows: List of strike flow data
        threshold: Imbalance threshold to flag as unusual
    
    Returns:
        List of unusual flow strikes
    """
    unusual = []
    for sf in strike_flows:
        abs_imbalance = abs(sf["composite_imbalance"])
        if abs_imbalance > threshold:
            unusual.append({
                **sf,
                "unusual_level": "extreme" if abs_imbalance > 0.7 else "moderate",
            })
    
    # Sort by absolute imbalance (descending)
    unusual.sort(key=lambda x: -abs(x["composite_imbalance"]))
    return unusual


def format_flow_report(flow_data: Dict) -> str:
    """Generate human-readable flow report."""
    lines = [
        "=" * 70,
        "FLOW IMBALANCE ANALYSIS",
        f"Spot: ${flow_data.get('spot', 0):.2f}",
        "=" * 70,
        "",
        "OVERALL FLOW BIAS",
        "-" * 40,
        f"Bias: {flow_data.get('overall_bias', 'unknown').upper()}",
        f"Imbalance Score: {flow_data.get('overall_imbalance', 0):+.3f}",
        f"Confidence: {flow_data.get('confidence', 0):.1%}",
        f"Interpretation: {flow_data.get('interpretation', 'N/A')}",
        "",
        "STRIKE-BY-STRIKE FLOW",
        "-" * 40,
    ]
    
    strike_flows = flow_data.get("strike_flows", [])
    for sf in strike_flows:
        dist = sf["dist_from_spot_pct"]
        imbalance = sf["composite_imbalance"]
        direction = "↑" if imbalance > 0 else "↓" if imbalance < 0 else "→"
        lines.append(
            f"  ${sf['strike']:>7.2f} ({dist:+.1f}%): "
            f"C/P OI {sf['cp_ratio_oi']:.2f}, Vol {sf['cp_ratio_vol']:.2f} | "
            f"Imbalance {direction} {imbalance:+.3f}"
        )
    
    # Unusual flow
    unusual = detect_unusual_flow(strike_flows)
    if unusual:
        lines.extend([
            "",
            "UNUSUAL FLOW DETECTED",
            "-" * 40,
        ])
        for uf in unusual[:3]:
            lines.append(
                f"  ${uf['strike']}: {uf['unusual_level']} "
                f"({uf['composite_imbalance']:+.3f})"
            )
    
    lines.append("=" * 70)
    return "\n".join(lines)


# CLI test
if __name__ == "__main__":
    import urllib.request
    
    ticker = sys.argv[1] if len(sys.argv) > 1 else "SPY"
    
    print(f"Flow Imbalance Analysis for {ticker}")
    print("Fetching data from local core...")
    
    try:
        # Fetch matrix data
        req = urllib.request.Request(
            f"http://127.0.0.1:8282/api/matrix?ticker={ticker}&expirations=3&depth=0.03",
            headers={"Accept": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())
        
        rows = data.get("rows", [])
        spot = (data.get("quote") or {}).get("price_context", 0)
        
        if not rows or not spot:
            print("No data returned")
            sys.exit(1)
        
        # Analyze flow
        flow_data = analyze_strike_flow(rows, spot)
        report = format_flow_report(flow_data)
        print(report)
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
