"""
Dynamic Strike Zone Classification.

Adapts strike zone sizes based on:
- Volatility (high vol = wider zones)
- Price level (higher prices = wider absolute zones)
- Regime (different zones for different market conditions)

Replaces fixed zone sizes with adaptive, context-aware classification.
"""

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import statistics

sys.path.insert(0, str(Path(__file__).parent.parent / "cipher-system" / "core"))


def calculate_atr(prices: List[float], period: int = 14) -> float:
    """Calculate Average True Range (ATR).
    
    Args:
        prices: List of prices (high, low, close interleaved or just close)
        period: ATR period
    
    Returns:
        ATR value
    """
    if len(prices) < period + 1:
        return 0.0
    
    # Calculate true range
    true_ranges = []
    for i in range(1, len(prices)):
        high = prices[i]
        low = prices[i - 1]
        prev_close = prices[i - 1]
        
        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close)
        )
        true_ranges.append(tr)
    
    if len(true_ranges) < period:
        return statistics.mean(true_ranges) if true_ranges else 0.0
    
    # Average over period
    return statistics.mean(true_ranges[-period:])


def calculate_volatility_adjusted_zone(
    spot: float,
    volatility: float,
    base_zone_pct: float = 0.01,
) -> float:
    """Calculate volatility-adjusted zone size.
    
    Args:
        spot: Current spot price
        volatility: Realized volatility (annualized)
        base_zone_pct: Base zone size as fraction of spot
    
    Returns:
        Zone size in price units
    """
    # Adjust zone size by volatility
    # Higher vol = wider zones
    vol_multiplier = 1.0 + (volatility - 0.20) * 2.0  # 20% vol = baseline
    
    # Clamp multiplier
    vol_multiplier = max(0.5, min(3.0, vol_multiplier))
    
    # Calculate zone size
    zone_size = spot * base_zone_pct * vol_multiplier
    
    return zone_size


def classify_strike_zone(
    strike: float,
    spot: float,
    zone_size: float,
) -> str:
    """Classify a strike into a zone relative to spot.
    
    Args:
        strike: Strike price
        spot: Spot price
        zone_size: Zone size in price units
    
    Returns:
        Zone classification (e.g., "atm", "near_otm", "otm", "far_otm")
    """
    if spot <= 0 or zone_size <= 0:
        return "unknown"
    
    distance = abs(strike - spot)
    zones_away = distance / zone_size
    
    if zones_away < 0.5:
        return "atm"  # At-the-money
    elif zones_away < 1.5:
        return "near_otm"  # Near OTM
    elif zones_away < 3.0:
        return "otm"  # OTM
    else:
        return "far_otm"  # Far OTM


def calculate_adaptive_zones(
    spot: float,
    volatility: float,
    num_zones: int = 5,
) -> List[Dict]:
    """Calculate adaptive zone boundaries.
    
    Args:
        spot: Current spot price
        volatility: Realized volatility
        num_zones: Number of zones on each side
    
    Returns:
        List of zone boundaries
    """
    if spot <= 0:
        return []
    
    # Base zone size (1% of spot)
    base_zone_pct = 0.01
    
    # Adjust for volatility
    zone_size = calculate_volatility_adjusted_zone(spot, volatility, base_zone_pct)
    
    zones = []
    
    # Build zones above spot
    for i in range(num_zones):
        lower = spot + i * zone_size
        upper = spot + (i + 1) * zone_size
        
        zones.append({
            "side": "above",
            "zone_index": i,
            "lower": round(lower, 2),
            "upper": round(upper, 2),
            "classification": classify_strike_zone((lower + upper) / 2, spot, zone_size),
            "distance_from_spot_pct": round(((lower + upper) / 2 - spot) / spot * 100, 2),
        })
    
    # Build zones below spot
    for i in range(num_zones):
        upper = spot - i * zone_size
        lower = spot - (i + 1) * zone_size
        
        zones.append({
            "side": "below",
            "zone_index": i,
            "lower": round(lower, 2),
            "upper": round(upper, 2),
            "classification": classify_strike_zone((lower + upper) / 2, spot, zone_size),
            "distance_from_spot_pct": round(((lower + upper) / 2 - spot) / spot * 100, 2),
        })
    
    return zones


def analyze_strike_distribution(
    rows: List[Dict],
    spot: float,
    volatility: float,
) -> Dict:
    """Analyze GEX distribution across adaptive zones.
    
    Args:
        rows: Strike profile rows
        spot: Current spot price
        volatility: Realized volatility
    
    Returns:
        Dict with zone analysis
    """
    if not rows or spot <= 0:
        return {"error": "Insufficient data"}
    
    # Calculate adaptive zones
    zones = calculate_adaptive_zones(spot, volatility, num_zones=5)
    
    # Aggregate GEX by zone
    zone_gex = {}
    for zone in zones:
        zone_key = f"{zone['side']}_{zone['zone_index']}"
        zone_gex[zone_key] = {
            "zone": zone,
            "total_gex": 0.0,
            "strike_count": 0,
            "strikes": [],
        }
    
    # Distribute strikes into zones
    for row in rows:
        strike = float(row.get("strike", 0))
        cells = row.get("cells", [])
        
        # Calculate total GEX across all expirations
        total_gex = sum(float(cell.get("net_gex", 0) or 0) for cell in cells)
        
        # Find which zone this strike belongs to
        for zone in zones:
            if zone["lower"] <= strike < zone["upper"]:
                zone_key = f"{zone['side']}_{zone['zone_index']}"
                zone_gex[zone_key]["total_gex"] += total_gex
                zone_gex[zone_key]["strike_count"] += 1
                zone_gex[zone_key]["strikes"].append(strike)
                break
    
    # Calculate zone importance (absolute GEX)
    for zone_key, data in zone_gex.items():
        data["abs_gex"] = abs(data["total_gex"])
    
    # Sort zones by importance
    sorted_zones = sorted(
        zone_gex.values(),
        key=lambda x: -x["abs_gex"]
    )
    
    # Find dominant zone
    dominant_zone = sorted_zones[0] if sorted_zones else None
    
    return {
        "spot": spot,
        "volatility": volatility,
        "zone_size": round(zones[0]["upper"] - zones[0]["lower"], 2) if zones else 0,
        "zones": sorted_zones[:10],  # Top 10 zones
        "dominant_zone": dominant_zone["zone"] if dominant_zone else None,
        "dominant_zone_gex": dominant_zone["total_gex"] if dominant_zone else 0,
    }


def format_zone_report(zone_data: Dict) -> str:
    """Generate human-readable zone report."""
    lines = [
        "=" * 70,
        "DYNAMIC STRIKE ZONE ANALYSIS",
        f"Spot: ${zone_data.get('spot', 0):.2f}",
        f"Volatility: {zone_data.get('volatility', 0):.2%}",
        f"Zone Size: ${zone_data.get('zone_size', 0):.2f}",
        "=" * 70,
        "",
        "TOP ZONES BY GEX IMPORTANCE",
        "-" * 40,
    ]
    
    zones = zone_data.get("zones", [])
    for zone_data_item in zones[:5]:
        zone = zone_data_item["zone"]
        total_gex = zone_data_item["total_gex"]
        strike_count = zone_data_item["strike_count"]
        
        lines.append(
            f"  {zone['side'].upper()} Zone {zone['zone_index']} "
            f"(${zone['lower']:.2f} - ${zone['upper']:.2f}):"
        )
        lines.append(f"    Classification: {zone['classification']}")
        lines.append(f"    Distance from spot: {zone['distance_from_spot_pct']:+.2f}%")
        lines.append(f"    Total GEX: {total_gex:>15,.0f}")
        lines.append(f"    Strike count: {strike_count}")
        lines.append("")
    
    dominant = zone_data.get("dominant_zone")
    if dominant:
        lines.extend([
            "DOMINANT ZONE",
            "-" * 40,
            f"  {dominant['side'].upper()} Zone {dominant['zone_index']}",
            f"  Range: ${dominant['lower']:.2f} - ${dominant['upper']:.2f}",
            f"  Classification: {dominant['classification']}",
            f"  GEX: {zone_data.get('dominant_zone_gex', 0):>15,.0f}",
        ])
    
    lines.append("=" * 70)
    return "\n".join(lines)


# CLI test
if __name__ == "__main__":
    import urllib.request
    
    ticker = sys.argv[1] if len(sys.argv) > 1 else "SPY"
    
    print(f"Dynamic Strike Zone Analysis for {ticker}")
    print("Fetching data from local core...")
    
    try:
        # Fetch matrix data and bars for volatility
        req = urllib.request.Request(
            f"http://127.0.0.1:8282/api/matrix?ticker={ticker}&expirations=3&depth=0.06",
            headers={"Accept": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            matrix_data = json.loads(resp.read().decode())
        
        rows = matrix_data.get("rows", [])
        spot = (matrix_data.get("quote") or {}).get("price_context", 0)
        
        if not rows or not spot:
            print("No data returned")
            sys.exit(1)
        
        # Fetch bars for volatility calculation
        req = urllib.request.Request(
            f"http://127.0.0.1:8282/api/bars?ticker={ticker}&timeframe=1D&limit=30",
            headers={"Accept": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            bars_data = json.loads(resp.read().decode())
        
        bars = bars_data.get("bars", [])
        prices = [bar.get("close", 0) for bar in bars if bar.get("close")]
        
        # Calculate volatility
        from regime_detector import calculate_realized_volatility
        volatility = calculate_realized_volatility(prices)
        
        # Analyze zones
        zone_data = analyze_strike_distribution(rows, spot, volatility)
        
        # Report
        report = format_zone_report(zone_data)
        print(report)
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
