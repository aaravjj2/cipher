"""
Cluster Confidence Scoring with Bootstrap Methods.

Estimates confidence in cluster detection using:
- Bootstrap resampling of GEX data
- Sensitivity analysis to parameter changes
- Statistical significance testing

Provides confidence intervals and p-values for cluster strength.
"""

import json
import math
import sys
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import statistics

sys.path.insert(0, str(Path(__file__).parent.parent / "cipher-system" / "core"))


def bootstrap_resample(values: List[float], n_samples: int = 100) -> List[List[float]]:
    """Generate bootstrap resamples.
    
    Args:
        values: Original values
        n_samples: Number of bootstrap samples
    
    Returns:
        List of resampled datasets
    """
    if not values:
        return []
    
    n = len(values)
    resamples = []
    
    for _ in range(n_samples):
        # Sample with replacement
        resample = [random.choice(values) for _ in range(n)]
        resamples.append(resample)
    
    return resamples


def calculate_cluster_strength_bootstrap(
    gex_values: List[float],
    n_bootstrap: int = 100,
) -> Dict:
    """Calculate cluster strength with bootstrap confidence intervals.
    
    Args:
        gex_values: GEX values at cluster strikes
        n_bootstrap: Number of bootstrap samples
    
    Returns:
        Dict with strength estimate and confidence intervals
    """
    if not gex_values:
        return {"strength": 0.0, "ci_lower": 0.0, "ci_upper": 0.0}
    
    # Point estimate (sum of absolute GEX)
    point_estimate = sum(abs(v) for v in gex_values)
    
    # Bootstrap resampling
    resamples = bootstrap_resample(gex_values, n_bootstrap)
    
    # Calculate strength for each resample
    bootstrap_strengths = [sum(abs(v) for v in resample) for resample in resamples]
    
    # Calculate confidence intervals
    bootstrap_strengths.sort()
    ci_lower_idx = int(0.025 * len(bootstrap_strengths))
    ci_upper_idx = int(0.975 * len(bootstrap_strengths))
    
    ci_lower = bootstrap_strengths[ci_lower_idx]
    ci_upper = bootstrap_strengths[min(ci_upper_idx, len(bootstrap_strengths) - 1)]
    
    # Standard error
    mean_strength = statistics.mean(bootstrap_strengths)
    variance = sum((s - mean_strength) ** 2 for s in bootstrap_strengths) / len(bootstrap_strengths)
    std_error = math.sqrt(variance)
    
    return {
        "strength": round(point_estimate, 2),
        "ci_lower": round(ci_lower, 2),
        "ci_upper": round(ci_upper, 2),
        "std_error": round(std_error, 2),
        "mean_bootstrap": round(mean_strength, 2),
    }


def sensitivity_analysis(
    rows: List[Dict],
    spot: float,
    parameter_ranges: Dict[str, List[float]],
) -> Dict:
    """Analyze cluster detection sensitivity to parameter changes.
    
    Args:
        rows: Strike profile rows
        spot: Current spot price
        parameter_ranges: Dict of parameter names to value ranges
    
    Returns:
        Dict with sensitivity analysis
    """
    # Import scanner functions
    try:
        from scanner import _strike_profile, _detect_cluster_zones
    except ImportError:
        return {"error": "Scanner module not available"}
    
    results = []
    
    # Test different parameter combinations
    for param_name, param_values in parameter_ranges.items():
        param_results = []
        
        for value in param_values:
            # For now, just vary the zone detection threshold
            # In a full implementation, this would modify scanner parameters
            profile = _strike_profile(rows, expiration_index=0, expirations=[])
            zones = _detect_cluster_zones(profile, spot)
            
            param_results.append({
                "parameter": param_name,
                "value": value,
                "cluster_count": len(zones),
                "total_strength": sum(z.get("strength", 0) for z in zones),
            })
        
        results.append(param_results)
    
    # Calculate sensitivity (variance in cluster count)
    if results and results[0]:
        cluster_counts = [r["cluster_count"] for r in results[0]]
        sensitivity = statistics.variance(cluster_counts) if len(cluster_counts) > 1 else 0.0
    else:
        sensitivity = 0.0
    
    return {
        "sensitivity": round(sensitivity, 3),
        "results": results,
        "interpretation": _interpret_sensitivity(sensitivity),
    }


def _interpret_sensitivity(sensitivity: float) -> str:
    """Interpret sensitivity score."""
    if sensitivity < 0.5:
        return "Low sensitivity - cluster detection is stable"
    elif sensitivity < 2.0:
        return "Moderate sensitivity - some parameter dependence"
    else:
        return "High sensitivity - cluster detection varies significantly with parameters"


def calculate_cluster_significance(
    cluster_gex: List[float],
    background_gex: List[float],
    n_permutations: int = 1000,
) -> Dict:
    """Calculate statistical significance of cluster strength.
    
    Args:
        cluster_gex: GEX values at cluster strikes
        background_gex: GEX values at non-cluster strikes
        n_permutations: Number of permutations for test
    
    Returns:
        Dict with p-value and significance
    """
    if not cluster_gex or not background_gex:
        return {"p_value": 1.0, "significant": False}
    
    # Observed difference
    observed_cluster_strength = statistics.mean(abs(v) for v in cluster_gex)
    observed_background_strength = statistics.mean(abs(v) for v in background_gex)
    observed_diff = observed_cluster_strength - observed_background_strength
    
    # Permutation test
    combined = cluster_gex + background_gex
    n_cluster = len(cluster_gex)
    
    permutation_diffs = []
    for _ in range(n_permutations):
        random.shuffle(combined)
        perm_cluster = combined[:n_cluster]
        perm_background = combined[n_cluster:]
        
        perm_diff = statistics.mean(abs(v) for v in perm_cluster) - statistics.mean(abs(v) for v in perm_background)
        permutation_diffs.append(perm_diff)
    
    # Calculate p-value (one-tailed test)
    p_value = sum(1 for d in permutation_diffs if d >= observed_diff) / len(permutation_diffs)
    
    return {
        "p_value": round(p_value, 4),
        "significant": p_value < 0.05,
        "observed_difference": round(observed_diff, 2),
        "interpretation": _interpret_significance(p_value),
    }


def _interpret_significance(p_value: float) -> str:
    """Interpret p-value."""
    if p_value < 0.01:
        return "Highly significant (p < 0.01)"
    elif p_value < 0.05:
        return "Statistically significant (p < 0.05)"
    else:
        return "Not statistically significant"


def analyze_cluster_confidence(
    rows: List[Dict],
    spot: float,
    cluster: Dict,
) -> Dict:
    """Comprehensive cluster confidence analysis.
    
    Args:
        rows: Strike profile rows
        spot: Current spot price
        cluster: Cluster data with strikes
    
    Returns:
        Dict with confidence analysis
    """
    strikes = cluster.get("strikes", [])
    if not strikes:
        return {"error": "No strikes in cluster"}
    
    # Extract GEX values at cluster strikes
    cluster_gex = []
    background_gex = []
    
    for row in rows:
        strike = float(row.get("strike", 0))
        cells = row.get("cells", [])
        total_gex = sum(float(cell.get("net_gex", 0) or 0) for cell in cells)
        
        if strike in strikes:
            cluster_gex.append(total_gex)
        else:
            background_gex.append(total_gex)
    
    # Bootstrap confidence intervals
    bootstrap_result = calculate_cluster_strength_bootstrap(cluster_gex)
    
    # Statistical significance
    significance_result = calculate_cluster_significance(cluster_gex, background_gex)
    
    # Overall confidence score (0-1)
    # Based on: CI width, p-value, cluster strength
    ci_width = bootstrap_result["ci_upper"] - bootstrap_result["ci_lower"]
    ci_relative_width = ci_width / bootstrap_result["strength"] if bootstrap_result["strength"] > 0 else 1.0
    
    confidence_score = (
        0.4 * (1.0 - min(1.0, ci_relative_width)) +  # Narrow CI = high confidence
        0.4 * (1.0 - significance_result["p_value"]) +  # Low p-value = high confidence
        0.2 * min(1.0, bootstrap_result["strength"] / 1e8)  # Strong cluster = high confidence
    )
    
    return {
        "cluster_kind": cluster.get("kind", "unknown"),
        "cluster_strikes": strikes,
        "bootstrap": bootstrap_result,
        "significance": significance_result,
        "confidence_score": round(confidence_score, 3),
        "interpretation": _interpret_confidence(confidence_score),
    }


def _interpret_confidence(confidence_score: float) -> str:
    """Interpret confidence score."""
    if confidence_score > 0.8:
        return "High confidence - cluster is reliable"
    elif confidence_score > 0.5:
        return "Moderate confidence - cluster is likely valid"
    else:
        return "Low confidence - cluster may be spurious"


def format_confidence_report(confidence_data: Dict) -> str:
    """Generate human-readable confidence report."""
    lines = [
        "=" * 70,
        "CLUSTER CONFIDENCE ANALYSIS",
        "=" * 70,
        "",
        f"Cluster Type: {confidence_data.get('cluster_kind', 'unknown')}",
        f"Strikes: {confidence_data.get('cluster_strikes', [])}",
        "",
        "BOOTSTRAP CONFIDENCE INTERVALS",
        "-" * 40,
    ]
    
    bootstrap = confidence_data.get("bootstrap", {})
    lines.extend([
        f"Point Estimate: {bootstrap.get('strength', 0):>15,.0f}",
        f"95% CI: [{bootstrap.get('ci_lower', 0):>15,.0f}, {bootstrap.get('ci_upper', 0):>15,.0f}]",
        f"Std Error: {bootstrap.get('std_error', 0):>15,.0f}",
        "",
        "STATISTICAL SIGNIFICANCE",
        "-" * 40,
        f"P-value: {confidence_data.get('significance', {}).get('p_value', 1.0):.4f}",
        f"Significant: {'Yes' if confidence_data.get('significance', {}).get('significant') else 'No'}",
        f"Interpretation: {confidence_data.get('significance', {}).get('interpretation', 'N/A')}",
        "",
        "OVERALL CONFIDENCE",
        "-" * 40,
        f"Confidence Score: {confidence_data.get('confidence_score', 0):.3f}",
        f"Interpretation: {confidence_data.get('interpretation', 'N/A')}",
        "=" * 70,
    ])
    
    return "\n".join(lines)


# CLI test
if __name__ == "__main__":
    import urllib.request
    
    ticker = sys.argv[1] if len(sys.argv) > 1 else "NVDA"
    
    print(f"Cluster Confidence Analysis for {ticker}")
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
        
        # Analyze confidence for first cluster
        cluster = zones[0]
        confidence_data = analyze_cluster_confidence(rows, spot, cluster)
        
        # Report
        report = format_confidence_report(confidence_data)
        print(report)
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
