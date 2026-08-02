"""
Smart Money Divergence Detection.

Detects when large trades (smart money) disagree with GEX structure:
- Bullish divergence: Large call buying despite negative GEX (bearish structure)
- Bearish divergence: Large put buying despite positive GEX (bullish structure)
- Neutral: Trades align with GEX structure

Smart money often positions ahead of moves, so divergences can be leading indicators.
"""

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import statistics

sys.path.insert(0, str(Path(__file__).parent.parent / "cipher-system" / "core"))


def calculate_volume_percentile(
    volume: float,
    all_volumes: List[float],
) -> float:
    """Calculate volume percentile.
    
    Args:
        volume: Target volume
        all_volumes: All volume values for comparison
    
    Returns:
        Percentile 0-1
    """
    if not all_volumes:
        return 0.5
    
    sorted_vols = sorted(all_volumes)
    rank = sum(1 for v in sorted_vols if v <= volume) / len(sorted_vols)
    return max(0.0, min(1.0, rank))


def detect_large_trades(
    rows: List[Dict],
    volume_threshold_percentile: float = 0.9,
) -> List[Dict]:
    """Detect strikes with unusually large volume.
    
    Args:
        rows: Strike profile rows
        volume_threshold_percentile: Volume percentile to consider "large"
    
    Returns:
        List of large trade strikes
    """
    if not rows:
        return []
    
    # Collect all volumes
    all_volumes = []
    for row in rows:
        cells = row.get("cells", [])
        for cell in cells:
            vol = float(cell.get("volume", 0) or 0)
            if vol > 0:
                all_volumes.append(vol)
    
    if not all_volumes:
        return []
    
    # Calculate threshold
    sorted_vols = sorted(all_volumes)
    threshold_idx = int(len(sorted_vols) * volume_threshold_percentile)
    volume_threshold = sorted_vols[min(threshold_idx, len(sorted_vols) - 1)]
    
    # Find large trades
    large_trades = []
    for row in rows:
        strike = float(row.get("strike", 0))
        cells = row.get("cells", [])
        
        for cell in cells:
            vol = float(cell.get("volume", 0) or 0)
            if vol >= volume_threshold:
                # Determine trade direction
                call_oi = float(cell.get("call_oi", 0) or 0)
                put_oi = float(cell.get("put_oi", 0) or 0)
                call_gex = float(cell.get("call_gex", 0) or 0)
                put_gex = float(cell.get("put_gex", 0) or 0)
                
                # Infer direction from OI changes (simplified)
                # High volume + high call OI = likely call buying
                # High volume + high put OI = likely put buying
                if call_oi > put_oi * 1.5:
                    direction = "call_buying"
                elif put_oi > call_oi * 1.5:
                    direction = "put_buying"
                else:
                    direction = "mixed"
                
                large_trades.append({
                    "strike": strike,
                    "expiration": cell.get("expiration"),
                    "volume": vol,
                    "volume_percentile": calculate_volume_percentile(vol, all_volumes),
                    "call_oi": call_oi,
                    "put_oi": put_oi,
                    "call_gex": call_gex,
                    "put_gex": put_gex,
                    "net_gex": float(cell.get("net_gex", 0) or 0),
                    "direction": direction,
                })
    
    # Sort by volume (descending)
    large_trades.sort(key=lambda x: -x["volume"])
    return large_trades


def analyze_divergence(
    large_trades: List[Dict],
    spot: float,
) -> Dict:
    """Analyze divergence between smart money and GEX structure.
    
    Args:
        large_trades: List of large trade data
        spot: Current spot price
    
    Returns:
        Dict with divergence analysis
    """
    if not large_trades:
        return {"divergence": "none", "confidence": 0.0}
    
    # Categorize trades by direction and position relative to spot
    bullish_trades = []  # Call buying above spot OR put buying below spot
    bearish_trades = []  # Put buying above spot OR call buying below spot
    
    for trade in large_trades:
        strike = trade["strike"]
        direction = trade["direction"]
        
        if direction == "call_buying":
            if strike > spot:
                bullish_trades.append(trade)
            else:
                bearish_trades.append(trade)
        elif direction == "put_buying":
            if strike < spot:
                bearish_trades.append(trade)
            else:
                bullish_trades.append(trade)
    
    # Analyze GEX structure
    bullish_gex = sum(t["net_gex"] for t in bullish_trades if t["net_gex"] > 0)
    bearish_gex = sum(abs(t["net_gex"]) for t in bearish_trades if t["net_gex"] < 0)
    
    # Detect divergences
    # Bullish divergence: Smart money buying calls despite negative GEX
    # Bearish divergence: Smart money buying puts despite positive GEX
    
    divergences = []
    
    for trade in bullish_trades:
        if trade["net_gex"] < 0:  # Negative GEX = bearish structure
            divergences.append({
                "type": "bullish_divergence",
                "strike": trade["strike"],
                "volume": trade["volume"],
                "gex": trade["net_gex"],
                "interpretation": "Smart money buying calls despite bearish GEX structure",
            })
    
    for trade in bearish_trades:
        if trade["net_gex"] > 0:  # Positive GEX = bullish structure
            divergences.append({
                "type": "bearish_divergence",
                "strike": trade["strike"],
                "volume": trade["volume"],
                "gex": trade["net_gex"],
                "interpretation": "Smart money buying puts despite bullish GEX structure",
            })
    
    # Overall divergence score
    if not divergences:
        overall_divergence = "none"
        confidence = 0.0
    elif len(divergences) == 1:
        overall_divergence = divergences[0]["type"]
        confidence = min(1.0, divergences[0]["volume"] / 10000)
    else:
        # Multiple divergences
        bullish_count = sum(1 for d in divergences if d["type"] == "bullish_divergence")
        bearish_count = sum(1 for d in divergences if d["type"] == "bearish_divergence")
        
        if bullish_count > bearish_count:
            overall_divergence = "bullish_divergence"
        elif bearish_count > bullish_count:
            overall_divergence = "bearish_divergence"
        else:
            overall_divergence = "mixed_divergence"
        
        confidence = min(1.0, len(divergences) / 5)
    
    return {
        "divergence": overall_divergence,
        "confidence": round(confidence, 3),
        "divergence_count": len(divergences),
        "divergences": divergences[:5],  # Top 5
        "bullish_trades": len(bullish_trades),
        "bearish_trades": len(bearish_trades),
        "interpretation": _interpret_divergence(overall_divergence, confidence),
    }


def _interpret_divergence(divergence: str, confidence: float) -> str:
    """Generate human-readable interpretation."""
    if divergence == "none":
        return "Smart money aligns with GEX structure"
    elif divergence == "bullish_divergence":
        if confidence > 0.7:
            return "Strong bullish divergence - smart money positioned for upside"
        else:
            return "Moderate bullish divergence - some smart money buying calls"
    elif divergence == "bearish_divergence":
        if confidence > 0.7:
            return "Strong bearish divergence - smart money positioned for downside"
        else:
            return "Moderate bearish divergence - some smart money buying puts"
    else:
        return "Mixed signals - smart money divided on direction"


def format_divergence_report(divergence_data: Dict, spot: float) -> str:
    """Generate human-readable divergence report."""
    lines = [
        "=" * 70,
        "SMART MONEY DIVERGENCE ANALYSIS",
        f"Spot: ${spot:.2f}",
        "=" * 70,
        "",
        "OVERALL DIVERGENCE",
        "-" * 40,
        f"Divergence: {divergence_data.get('divergence', 'none').upper()}",
        f"Confidence: {divergence_data.get('confidence', 0):.1%}",
        f"Interpretation: {divergence_data.get('interpretation', 'N/A')}",
        "",
        "TRADE SUMMARY",
        "-" * 40,
        f"Bullish Trades: {divergence_data.get('bullish_trades', 0)}",
        f"Bearish Trades: {divergence_data.get('bearish_trades', 0)}",
        f"Divergence Count: {divergence_data.get('divergence_count', 0)}",
    ]
    
    divergences = divergence_data.get("divergences", [])
    if divergences:
        lines.extend([
            "",
            "KEY DIVERGENCES",
            "-" * 40,
        ])
        
        for div in divergences[:3]:
            lines.append(
                f"  ${div['strike']:.2f}: {div['type']} "
                f"(Vol: {div['volume']:,.0f}, GEX: {div['gex']:,.0f})"
            )
            lines.append(f"    → {div['interpretation']}")
    
    lines.append("=" * 70)
    return "\n".join(lines)


# CLI test
if __name__ == "__main__":
    import urllib.request
    
    ticker = sys.argv[1] if len(sys.argv) > 1 else "SPY"
    
    print(f"Smart Money Divergence Analysis for {ticker}")
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
        
        # Detect large trades
        large_trades = detect_large_trades(rows, volume_threshold_percentile=0.9)
        
        # Analyze divergence
        divergence_data = analyze_divergence(large_trades, spot)
        
        # Report
        report = format_divergence_report(divergence_data, spot)
        print(report)
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
