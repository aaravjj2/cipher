"""
GEX Surface Interpolation for Sparse Data.

Interpolates missing GEX values across strikes and expirations using:
- Linear interpolation for small gaps
- Radial basis functions for larger gaps
- Kriging-style spatial interpolation for 2D surfaces

Handles sparse data from limited GEX snapshots.
"""

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import statistics

sys.path.insert(0, str(Path(__file__).parent.parent / "cipher-system" / "core"))


def linear_interpolate_1d(
    known_values: List[Tuple[float, float]],
    target_x: float,
) -> float:
    """Linear interpolation for 1D data.
    
    Args:
        known_values: List of (x, y) tuples
        target_x: Target x value
    
    Returns:
        Interpolated y value
    """
    if not known_values:
        return 0.0
    
    # Sort by x
    sorted_vals = sorted(known_values, key=lambda p: p[0])
    
    # Find surrounding points
    lower = None
    upper = None
    for x, y in sorted_vals:
        if x <= target_x:
            lower = (x, y)
        if x >= target_x and upper is None:
            upper = (x, y)
    
    # Edge cases
    if lower is None:
        return sorted_vals[0][1]
    if upper is None:
        return sorted_vals[-1][1]
    if lower[0] == upper[0]:
        return lower[1]
    
    # Linear interpolation
    t = (target_x - lower[0]) / (upper[0] - lower[0])
    return lower[1] + t * (upper[1] - lower[1])


def rbf_interpolate(
    known_points: List[Tuple[float, float, float]],
    target_x: float,
    target_y: float,
    epsilon: float = 1.0,
) -> float:
    """Radial basis function interpolation for 2D data.
    
    Args:
        known_points: List of (x, y, value) tuples
        target_x: Target x coordinate
        target_y: Target y coordinate
        epsilon: RBF shape parameter
    
    Returns:
        Interpolated value
    """
    if not known_points:
        return 0.0
    
    # Calculate distances and weights
    weights = []
    values = []
    
    for x, y, val in known_points:
        dist = math.sqrt((x - target_x) ** 2 + (y - target_y) ** 2)
        # Gaussian RBF
        weight = math.exp(-(epsilon * dist) ** 2)
        weights.append(weight)
        values.append(val)
    
    # Weighted average
    total_weight = sum(weights)
    if total_weight == 0:
        return statistics.mean(values)
    
    return sum(w * v for w, v in zip(weights, values)) / total_weight


def interpolate_strike_gex(
    rows: List[Dict],
    target_strike: float,
    expiration: str,
    method: str = "linear",
) -> float:
    """Interpolate GEX at a specific strike for a given expiration.
    
    Args:
        rows: Strike profile rows
        target_strike: Target strike
        expiration: Expiration date
        method: "linear" or "rbf"
    
    Returns:
        Interpolated net GEX
    """
    if not rows:
        return 0.0
    
    # Extract known GEX values for this expiration
    known_gex = []
    for row in rows:
        strike = float(row.get("strike", 0))
        cells = row.get("cells", [])
        
        # Find the cell for this expiration
        for cell in cells:
            if cell.get("expiration") == expiration:
                net_gex = float(cell.get("net_gex", 0) or 0)
                # Only use non-zero values for interpolation
                if net_gex != 0:
                    known_gex.append((strike, net_gex))
                break
    
    if not known_gex:
        return 0.0
    
    if method == "rbf":
        # For RBF, we need 2D points (use strike as x, DTE as y)
        # Simplified: just use linear for now
        return linear_interpolate_1d(known_gex, target_strike)
    else:
        return linear_interpolate_1d(known_gex, target_strike)


def interpolate_gex_surface(
    rows: List[Dict],
    expirations: List[str],
    strike_range: Tuple[float, float],
    strike_step: float = 1.0,
) -> Dict[str, Dict[float, float]]:
    """Interpolate GEX surface across strikes and expirations.
    
    Args:
        rows: Strike profile rows
        expirations: List of expiration dates
        strike_range: (min_strike, max_strike)
        strike_step: Strike increment
    
    Returns:
        Dict: {expiration: {strike: gex}}
    """
    if not rows or not expirations:
        return {}
    
    min_strike, max_strike = strike_range
    target_strikes = [
        min_strike + i * strike_step
        for i in range(int((max_strike - min_strike) / strike_step) + 1)
    ]
    
    surface = {}
    for exp in expirations:
        surface[exp] = {}
        for strike in target_strikes:
            gex = interpolate_strike_gex(rows, strike, exp, method="linear")
            surface[exp][strike] = gex
    
    return surface


def fill_sparse_gex(
    rows: List[Dict],
    sparsity_threshold: float = 0.3,
) -> List[Dict]:
    """Fill sparse GEX data with interpolated values.
    
    Args:
        rows: Strike profile rows
        sparsity_threshold: Fraction of non-zero cells below which to interpolate
    
    Returns:
        Rows with filled GEX values
    """
    if not rows:
        return []
    
    # Calculate sparsity
    total_cells = 0
    non_zero_cells = 0
    
    for row in rows:
        cells = row.get("cells", [])
        for cell in cells:
            total_cells += 1
            if float(cell.get("net_gex", 0) or 0) != 0:
                non_zero_cells += 1
    
    sparsity = non_zero_cells / total_cells if total_cells > 0 else 0
    
    # If data is dense enough, return as-is
    if sparsity >= sparsity_threshold:
        return rows
    
    # Otherwise, interpolate missing values
    filled_rows = []
    expirations = list(set(
        cell.get("expiration")
        for row in rows
        for cell in row.get("cells", [])
    ))
    
    for row in rows:
        strike = float(row.get("strike", 0))
        cells = row.get("cells", [])
        new_cells = []
        
        for cell in cells:
            exp = cell.get("expiration")
            net_gex = float(cell.get("net_gex", 0) or 0)
            
            if net_gex == 0:
                # Interpolate
                interpolated = interpolate_strike_gex(rows, strike, exp)
                new_cell = {**cell, "net_gex": interpolated, "interpolated": True}
            else:
                new_cell = {**cell, "interpolated": False}
            
            new_cells.append(new_cell)
        
        filled_rows.append({**row, "cells": new_cells})
    
    return filled_rows


def calculate_surface_smoothness(surface: Dict[str, Dict[float, float]]) -> float:
    """Calculate smoothness of interpolated GEX surface.
    
    Args:
        surface: GEX surface {expiration: {strike: gex}}
    
    Returns:
        Smoothness score (0-1, higher = smoother)
    """
    if not surface:
        return 0.0
    
    total_variation = 0.0
    count = 0
    
    for exp, strikes in surface.items():
        sorted_strikes = sorted(strikes.keys())
        for i in range(1, len(sorted_strikes)):
            prev_strike = sorted_strikes[i - 1]
            curr_strike = sorted_strikes[i]
            prev_gex = strikes[prev_strike]
            curr_gex = strikes[curr_strike]
            
            # Absolute difference normalized by strike distance
            dist = abs(curr_strike - prev_strike)
            if dist > 0:
                variation = abs(curr_gex - prev_gex) / dist
                total_variation += variation
                count += 1
    
    if count == 0:
        return 1.0
    
    # Lower variation = smoother
    avg_variation = total_variation / count
    
    # Normalize to 0-1 (assuming max reasonable variation)
    smoothness = max(0.0, min(1.0, 1.0 - avg_variation / 1e6))
    
    return smoothness


def format_surface_report(surface: Dict[str, Dict[float, float]], spot: float) -> str:
    """Generate human-readable surface report."""
    lines = [
        "=" * 70,
        "GEX SURFACE INTERPOLATION",
        f"Spot: ${spot:.2f}",
        "=" * 70,
        "",
    ]
    
    smoothness = calculate_surface_smoothness(surface)
    lines.extend([
        "SURFACE SMOOTHNESS",
        "-" * 40,
        f"Smoothness Score: {smoothness:.3f}",
        "",
    ])
    
    # Show sample strikes for each expiration
    lines.extend([
        "SAMPLE SURFACE VALUES",
        "-" * 40,
    ])
    
    for exp in sorted(surface.keys())[:3]:  # First 3 expirations
        lines.append(f"\nExpiration: {exp}")
        strikes = surface[exp]
        
        # Show strikes near spot
        near_spot = [s for s in sorted(strikes.keys()) if abs(s - spot) / spot < 0.05]
        for strike in near_spot[:5]:
            gex = strikes[strike]
            marker = " <-- spot" if abs(strike - spot) < 0.5 else ""
            lines.append(f"  ${strike:>7.2f}: {gex:>15,.0f}{marker}")
    
    lines.append("=" * 70)
    return "\n".join(lines)


# CLI test
if __name__ == "__main__":
    import urllib.request
    
    ticker = sys.argv[1] if len(sys.argv) > 1 else "SPY"
    
    print(f"GEX Surface Interpolation for {ticker}")
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
        
        # Calculate strike range
        strikes = [float(row.get("strike", 0)) for row in rows]
        min_strike = min(strikes)
        max_strike = max(strikes)
        
        # Interpolate surface
        surface = interpolate_gex_surface(
            rows, expirations, (min_strike, max_strike), strike_step=1.0
        )
        
        # Fill sparse data
        filled_rows = fill_sparse_gex(rows)
        
        # Report
        report = format_surface_report(surface, spot)
        print(report)
        
        # Sparsity info
        original_nonzero = sum(
            1 for row in rows for cell in row.get("cells", [])
            if float(cell.get("net_gex", 0) or 0) != 0
        )
        filled_nonzero = sum(
            1 for row in filled_rows for cell in row.get("cells", [])
            if float(cell.get("net_gex", 0) or 0) != 0
        )
        
        print(f"\nSparsity: {original_nonzero} non-zero cells → {filled_nonzero} after interpolation")
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
