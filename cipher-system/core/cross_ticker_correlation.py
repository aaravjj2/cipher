"""
Cross-Ticker Cluster Correlation Analysis.

Analyzes cluster behavior across related tickers:
- Sector correlation: Do tech stocks cluster at similar levels?
- Index correlation: Do SPY/QQQ/IWM clusters align?
- Leading indicators: Does one ticker's cluster predict another's move?

Helps identify systemic vs idiosyncratic cluster behavior.
"""

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import statistics

sys.path.insert(0, str(Path(__file__).parent.parent / "cipher-system" / "core"))


def fetch_ticker_clusters(ticker: str) -> Dict:
    """Fetch clusters for a ticker from local core.
    
    Args:
        ticker: Ticker symbol
    
    Returns:
        Dict with ticker data and clusters
    """
    import urllib.request
    
    try:
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
            return {"ticker": ticker, "clusters": [], "spot": 0}
        
        # Import scanner functions
        from scanner import _strike_profile, _detect_cluster_zones
        
        # Detect clusters
        profile = _strike_profile(rows, expiration_index=0, expirations=expirations)
        zones = _detect_cluster_zones(profile, spot)
        
        return {
            "ticker": ticker,
            "clusters": zones,
            "spot": spot,
            "rows": rows,
            "expirations": expirations,
        }
    
    except Exception as e:
        return {"ticker": ticker, "clusters": [], "spot": 0, "error": str(e)}


def calculate_cluster_similarity(
    clusters1: List[Dict],
    clusters2: List[Dict],
    spot1: float,
    spot2: float,
) -> float:
    """Calculate similarity between two sets of clusters.
    
    Args:
        clusters1: First ticker's clusters
        clusters2: Second ticker's clusters
        spot1: First ticker's spot price
        spot2: Second ticker's spot price
    
    Returns:
        Similarity score 0-1
    """
    if not clusters1 or not clusters2:
        return 0.0
    
    # Compare cluster kinds
    kinds1 = {c.get("kind", "") for c in clusters1}
    kinds2 = {c.get("kind", "") for c in clusters2}
    
    kind_overlap = len(kinds1 & kinds2) / max(len(kinds1 | kinds2), 1)
    
    # Compare relative positions (distance from spot as %)
    positions1 = []
    for c in clusters1:
        strikes = c.get("strikes", [])
        if strikes:
            avg_strike = sum(strikes) / len(strikes)
            rel_pos = (avg_strike - spot1) / spot1
            positions1.append(rel_pos)
    
    positions2 = []
    for c in clusters2:
        strikes = c.get("strikes", [])
        if strikes:
            avg_strike = sum(strikes) / len(strikes)
            rel_pos = (avg_strike - spot2) / spot2
            positions2.append(rel_pos)
    
    # Compare position distributions
    if positions1 and positions2:
        avg_pos1 = statistics.mean(positions1)
        avg_pos2 = statistics.mean(positions2)
        position_similarity = 1.0 - min(1.0, abs(avg_pos1 - avg_pos2) * 10)
    else:
        position_similarity = 0.0
    
    # Weighted combination
    similarity = 0.5 * kind_overlap + 0.5 * position_similarity
    return max(0.0, min(1.0, similarity))


def analyze_ticker_group(tickers: List[str]) -> Dict:
    """Analyze clusters across a group of related tickers.
    
    Args:
        tickers: List of ticker symbols
    
    Returns:
        Dict with cross-ticker analysis
    """
    if not tickers:
        return {"error": "No tickers provided"}
    
    # Fetch clusters for all tickers
    ticker_data = {}
    for ticker in tickers:
        data = fetch_ticker_clusters(ticker)
        if data.get("spot", 0) > 0:
            ticker_data[ticker] = data
    
    if len(ticker_data) < 2:
        return {"error": "Insufficient data for correlation analysis"}
    
    # Calculate pairwise similarities
    similarities = []
    ticker_list = list(ticker_data.keys())
    
    for i in range(len(ticker_list)):
        for j in range(i + 1, len(ticker_list)):
            t1 = ticker_list[i]
            t2 = ticker_list[j]
            
            sim = calculate_cluster_similarity(
                ticker_data[t1]["clusters"],
                ticker_data[t2]["clusters"],
                ticker_data[t1]["spot"],
                ticker_data[t2]["spot"],
            )
            
            similarities.append({
                "ticker1": t1,
                "ticker2": t2,
                "similarity": round(sim, 3),
                "clusters1": len(ticker_data[t1]["clusters"]),
                "clusters2": len(ticker_data[t2]["clusters"]),
            })
    
    # Sort by similarity (descending)
    similarities.sort(key=lambda x: -x["similarity"])
    
    # Calculate average similarity
    avg_similarity = (
        sum(s["similarity"] for s in similarities) / len(similarities)
        if similarities else 0.0
    )
    
    # Identify highly correlated pairs
    high_corr_pairs = [s for s in similarities if s["similarity"] > 0.7]
    
    # Identify cluster consensus (clusters appearing in multiple tickers)
    cluster_kinds = {}
    for ticker, data in ticker_data.items():
        for cluster in data["clusters"]:
            kind = cluster.get("kind", "")
            if kind not in cluster_kinds:
                cluster_kinds[kind] = []
            cluster_kinds[kind].append(ticker)
    
    consensus_clusters = {
        kind: tickers
        for kind, tickers in cluster_kinds.items()
        if len(tickers) >= 2
    }
    
    return {
        "tickers_analyzed": list(ticker_data.keys()),
        "pairwise_similarities": similarities[:10],  # Top 10
        "average_similarity": round(avg_similarity, 3),
        "high_correlation_pairs": high_corr_pairs,
        "consensus_clusters": consensus_clusters,
        "interpretation": _interpret_correlation(avg_similarity, consensus_clusters),
    }


def _interpret_correlation(avg_similarity: float, consensus: Dict) -> str:
    """Generate human-readable interpretation."""
    if avg_similarity > 0.7:
        return "High correlation - tickers showing similar cluster behavior"
    elif avg_similarity > 0.4:
        return "Moderate correlation - some cluster alignment"
    else:
        return "Low correlation - tickers showing independent cluster behavior"


def format_correlation_report(correlation_data: Dict) -> str:
    """Generate human-readable correlation report."""
    lines = [
        "=" * 70,
        "CROSS-TICKER CLUSTER CORRELATION",
        "=" * 70,
        "",
        "TICKERS ANALYZED",
        "-" * 40,
        ", ".join(correlation_data.get("tickers_analyzed", [])),
        "",
        "CORRELATION SUMMARY",
        "-" * 40,
        f"Average Similarity: {correlation_data.get('average_similarity', 0):.3f}",
        f"Interpretation: {correlation_data.get('interpretation', 'N/A')}",
        "",
        "TOP CORRELATED PAIRS",
        "-" * 40,
    ]
    
    pairs = correlation_data.get("pairwise_similarities", [])
    for pair in pairs[:5]:
        lines.append(
            f"  {pair['ticker1']} ↔ {pair['ticker2']}: "
            f"{pair['similarity']:.3f} "
            f"({pair['clusters1']} vs {pair['clusters2']} clusters)"
        )
    
    consensus = correlation_data.get("consensus_clusters", {})
    if consensus:
        lines.extend([
            "",
            "CONSENSUS CLUSTERS",
            "-" * 40,
        ])
        
        for kind, tickers in consensus.items():
            lines.append(f"  {kind}: {', '.join(tickers)}")
    
    lines.append("=" * 70)
    return "\n".join(lines)


# CLI test
if __name__ == "__main__":
    # Default to major indices/ETFs
    default_tickers = ["SPY", "QQQ", "IWM"]
    
    if len(sys.argv) > 1:
        tickers = sys.argv[1:]
    else:
        tickers = default_tickers
    
    print(f"Cross-Ticker Correlation Analysis")
    print(f"Tickers: {', '.join(tickers)}")
    print("Fetching data from local core...")
    
    try:
        correlation_data = analyze_ticker_group(tickers)
        report = format_correlation_report(correlation_data)
        print(report)
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
