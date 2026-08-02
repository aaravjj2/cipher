"""Signal computation module for Cipher research terminal.

Pure computation functions that derive actionable signals from GEX/VEX profiles.
All functions are free of HTTP/cache dependencies where possible.

Signals:
- delta_gex_momentum: rate of change in net GEX across strikes
- vex_gex_divergence: when VEX and GEX point in different directions
- gex_vacuum: strikes with abnormally low |GEX| (thin dealer liquidity)
- gamma_squeeze_probability: likelihood of a gamma-driven move
- term_structure: near vs far expiration GEX comparison
- cluster_collision: when multiple setup kinds overlap at similar strikes
- flow_gex_confluence: agreement between option flow direction and GEX structure

GEX is a public-OI heuristic, not verified dealer positioning.
"""
from __future__ import annotations

import math
from typing import Iterable


def delta_gex_momentum(
    profile: list[dict],
    prev_profile: list[dict] | None = None,
) -> dict:
    """Compute momentum of net GEX changes across strikes.

    If prev_profile is provided, computes actual deltas.
    Otherwise, uses the current profile shape to infer directional bias.

    Returns: score [-100, 100], direction, and per-strike breakdown.
    """
    if not profile:
        return {"score": None, "direction": None, "strikes": []}

    if prev_profile:
        prev_by_strike = {p["strike"]: p for p in prev_profile}
        deltas = []
        for p in profile:
            prev = prev_by_strike.get(p["strike"])
            if prev:
                delta = (p.get("net") or 0) - (prev.get("net") or 0)
                deltas.append({"strike": p["strike"], "delta": delta, "abs_delta": abs(delta)})
            else:
                deltas.append({"strike": p["strike"], "delta": 0.0, "abs_delta": 0.0})

        if not deltas:
            return {"score": None, "direction": None, "strikes": []}

        total_delta = sum(d["delta"] for d in deltas)
        max_delta = max((d["abs_delta"] for d in deltas), default=1.0) or 1.0
        ref = max(sum(abs(p.get("net") or 0) for p in profile) * 0.1, 1.0)
        score = max(-100.0, min(100.0, total_delta / ref * 100))
    else:
        # Infer from profile shape: positive GEX below spot = stabilizing
        # Negative GEX above spot = destabilizing
        strikes_sorted = sorted(profile, key=lambda p: p["strike"])
        mid_idx = len(strikes_sorted) // 2
        below = strikes_sorted[:mid_idx]
        above = strikes_sorted[mid_idx:]

        below_net = sum(p.get("net") or 0 for p in below)
        above_net = sum(p.get("net") or 0 for p in above)

        # Positive below + negative above = stabilizing (positive score)
        score = max(-100.0, min(100.0, (below_net + above_net) / max(sum(abs(p.get("net") or 0) for p in profile) * 0.2, 1.0) * 50))
        deltas = [{"strike": p["strike"], "delta": p.get("net", 0), "abs_delta": abs(p.get("net", 0))} for p in profile]

    if score > 10:
        direction = "stabilizing"
    elif score < -10:
        direction = "destabilizing"
    else:
        direction = "neutral"

    return {
        "score": round(score, 2),
        "direction": direction,
        "strikes": sorted(deltas, key=lambda d: d["strike"]),
    }


def vex_gex_divergence(profile: list[dict]) -> dict:
    """Detect when VEX and GEX point in different directions.

    VEX (vanna exposure) measures sensitivity to volatility changes.
    GEX (gamma exposure) measures dealer hedging pressure.
    When they diverge, it signals potential regime change.

    Returns: divergence_score [0, 100], direction, and interpretation.
    """
    if not profile:
        return {"score": None, "direction": None, "interpretation": None}

    total_gex = sum(p.get("net") or 0 for p in profile)
    total_vex = sum(p.get("vex") or p.get("net_vex") or 0 for p in profile)

    # Normalize both to [-1, 1] range
    abs_gex_sum = sum(abs(p.get("net") or 0) for p in profile) or 1.0
    abs_vex_sum = sum(abs(p.get("vex") or p.get("net_vex") or 0) for p in profile) or 1.0

    gex_sign = total_gex / abs_gex_sum
    vex_sign = total_vex / abs_vex_sum

    # Divergence: when signs differ
    divergence = abs(gex_sign - vex_sign)
    score = min(100.0, divergence * 50)

    if gex_sign > 0.1 and vex_sign < -0.1:
        direction = "gex_positive_vex_negative"
        interpretation = (
            "Dealer gamma is stabilizing but vanna suggests vol-driven selling pressure. "
            "Potential for sharp move if vol spikes."
        )
    elif gex_sign < -0.1 and vex_sign > 0.1:
        direction = "gex_negative_vex_positive"
        interpretation = (
            "Dealer gamma is destabilizing but vanna suggests vol-driven buying. "
            "Possible mean-reversion setup."
        )
    elif abs(gex_sign) < 0.1 and abs(vex_sign) > 0.2:
        direction = "gex_neutral_vex_strong"
        interpretation = "Gamma is flat but vanna is directional — vol-sensitive regime."
    else:
        direction = "aligned"
        interpretation = "GEX and VEX are broadly aligned — consistent regime."

    return {
        "score": round(score, 2),
        "direction": direction,
        "interpretation": interpretation,
        "gex_normalized": round(gex_sign, 3),
        "vex_normalized": round(vex_sign, 3),
    }


def gex_vacuum(
    profile: list[dict],
    spot: float,
    *,
    threshold_pct: float = 0.15,
    window_pct: float = 0.05,
) -> dict:
    """Detect GEX vacuums — strikes with abnormally low |GEX|.

    A vacuum is a region where dealer liquidity is thin, meaning price can
    move through quickly. Useful for identifying breakout/runway zones.

    Returns: vacuum zones with start/end strikes and depth.
    """
    if not profile or not spot:
        return {"zones": [], "count": 0}

    sorted_profile = sorted(profile, key=lambda p: p["strike"])
    peak_abs = max((p.get("abs") or abs(p.get("net") or 0) for p in sorted_profile), default=1.0) or 1.0
    threshold = peak_abs * threshold_pct

    # Find contiguous zones below threshold within the window
    in_window = [
        p for p in sorted_profile
        if abs(p["strike"] - spot) / spot <= window_pct * 2
    ]

    zones = []
    current_zone = None

    for p in in_window:
        net_abs = p.get("abs") or abs(p.get("net") or 0)
        if net_abs < threshold:
            if current_zone is None:
                current_zone = {
                    "start": p["strike"],
                    "end": p["strike"],
                    "strikes": [p["strike"]],
                    "avg_abs": net_abs,
                    "n": 1,
                }
            else:
                current_zone["end"] = p["strike"]
                current_zone["strikes"].append(p["strike"])
                current_zone["avg_abs"] = (current_zone["avg_abs"] * current_zone["n"] + net_abs) / (current_zone["n"] + 1)
                current_zone["n"] += 1
        else:
            if current_zone and current_zone["n"] >= 2:
                current_zone["depth_pct"] = round(
                    (current_zone["end"] - current_zone["start"]) / spot * 100, 3
                )
                current_zone["avg_abs"] = round(current_zone["avg_abs"], 2)
                zones.append(current_zone)
            current_zone = None

    # Don't forget trailing zone
    if current_zone and current_zone["n"] >= 2:
        current_zone["depth_pct"] = round(
            (current_zone["end"] - current_zone["start"]) / spot * 100, 3
        )
        current_zone["avg_abs"] = round(current_zone["avg_abs"], 2)
        zones.append(current_zone)

    return {
        "zones": zones,
        "count": len(zones),
        "spot": spot,
        "threshold": round(threshold, 2),
        "peak_abs": round(peak_abs, 2),
    }


def gamma_squeeze_probability(
    profile: list[dict],
    spot: float,
    *,
    call_oi_total: float | None = None,
    put_oi_total: float | None = None,
) -> dict:
    """Estimate gamma squeeze probability from GEX profile structure.

    A gamma squeeze requires:
    1. Large call OI concentrated above spot (dealers must hedge by buying)
    2. Positive net GEX (dealers are long gamma → buying on rallies)
    3. Price near a major call wall

    Returns: probability [0, 100], contributing factors.
    """
    if not profile or not spot:
        return {"score": None, "factors": []}

    sorted_profile = sorted(profile, key=lambda p: p["strike"])
    factors = []

    # Factor 1: Call concentration above spot
    calls_above = [p for p in sorted_profile if p["strike"] > spot * 1.005]
    calls_below = [p for p in sorted_profile if p["strike"] < spot * 0.995]

    call_strength_above = sum(p.get("call") or 0 for p in calls_above)
    total_abs = sum(abs(p.get("net") or 0) for p in sorted_profile) or 1.0

    call_concentration = call_strength_above / total_abs
    if call_concentration > 0.3:
        factors.append({"factor": "call_concentration_above", "weight": 30, "value": round(call_concentration, 3)})
    elif call_concentration > 0.15:
        factors.append({"factor": "call_concentration_above", "weight": 15, "value": round(call_concentration, 3)})

    # Factor 2: Net GEX polarity
    total_net = sum(p.get("net") or 0 for p in sorted_profile)
    net_ratio = total_net / total_abs
    if net_ratio > 0.3:
        factors.append({"factor": "positive_net_gex", "weight": 25, "value": round(net_ratio, 3)})
    elif net_ratio > 0.1:
        factors.append({"factor": "positive_net_gex", "weight": 10, "value": round(net_ratio, 3)})

    # Factor 3: Proximity to call wall
    if calls_above:
        nearest_call = min(calls_above, key=lambda p: p["strike"])
        dist_pct = (nearest_call["strike"] - spot) / spot
        if dist_pct < 0.02:
            factors.append({"factor": "near_call_wall", "weight": 25, "value": round(dist_pct * 100, 2)})
        elif dist_pct < 0.05:
            factors.append({"factor": "approaching_call_wall", "weight": 15, "value": round(dist_pct * 100, 2)})

    # Factor 4: Put/call OI ratio (if available)
    if call_oi_total and put_oi_total and call_oi_total > 0:
        pc_ratio = put_oi_total / call_oi_total
        if pc_ratio < 0.5:
            factors.append({"factor": "low_put_call_ratio", "weight": 20, "value": round(pc_ratio, 3)})

    score = min(100.0, sum(f["weight"] for f in factors))

    return {
        "score": round(score, 1),
        "factors": factors,
        "spot": spot,
        "caveat": (
            "Gamma squeeze probability is a structural heuristic from GEX profile shape. "
            "Not a prediction. GEX is a public-OI heuristic."
        ),
    }


def term_structure(
    profile_by_exp: dict[str, list[dict]],
    spot: float,
) -> dict:
    """Compare GEX structure across expirations.

    Near-term vs far-term GEX alignment reveals whether dealer positioning
    is consistent or conflicting across time horizons.

    Returns: structure classification and per-expiration summary.
    """
    if not profile_by_exp or not spot:
        return {"classification": None, "expirations": []}

    summaries = []
    for exp, cells in sorted(profile_by_exp.items()):
        if not cells:
            continue
        total_net = sum(c.get("net") or 0 for c in cells)
        total_abs = sum(abs(c.get("net") or 0) for c in cells) or 1.0
        peak_strike = max(cells, key=lambda c: abs(c.get("net") or 0))["strike"] if cells else None
        summaries.append({
            "expiration": exp,
            "total_net_gex": total_net,
            "total_abs_gex": total_abs,
            "polarity": "positive" if total_net > 0 else ("negative" if total_net < 0 else "neutral"),
            "peak_strike": peak_strike,
            "peak_distance_pct": round((peak_strike - spot) / spot * 100, 2) if peak_strike else None,
        })

    if len(summaries) < 2:
        return {"classification": "single_expiration", "expirations": summaries}

    # Check alignment
    polarities = [s["polarity"] for s in summaries]
    all_same = len(set(polarities)) == 1

    peak_strikes = [s["peak_strike"] for s in summaries if s["peak_strike"]]
    peak_spread = 0.0
    if len(peak_strikes) >= 2:
        peak_spread = (max(peak_strikes) - min(peak_strikes)) / spot * 100

    if all_same and peak_spread < 2.0:
        classification = "aligned"
    elif all_same:
        classification = "same_polarity_shifted_peaks"
    elif not all_same and peak_spread < 2.0:
        classification = "conflicting_polarity"
    else:
        classification = "divergent"

    return {
        "classification": classification,
        "expirations": summaries,
        "peak_spread_pct": round(peak_spread, 2),
    }


def cluster_collision(setups: list[dict], *, tolerance_pct: float = 0.005) -> list[dict]:
    """Detect when multiple setup kinds overlap at similar strikes.

    When a quad cluster, golden, and call wall all converge near the same
    strike, that level is extra-significant.

    Returns: collision groups with combined strength.
    """
    if not setups:
        return []

    # Sort by center strike
    sorted_setups = sorted(setups, key=lambda s: s.get("center") or s.get("strike") or 0)

    collisions = []
    used = set()

    for i, s in enumerate(sorted_setups):
        if i in used:
            continue
        center = s.get("center") or s.get("strike") or 0
        if not center:
            continue
        group = [s]
        used.add(i)

        for j in range(i + 1, len(sorted_setups)):
            if j in used:
                continue
            other = sorted_setups[j]
            other_center = other.get("center") or other.get("strike") or 0
            if not other_center:
                continue
            if abs(other_center - center) / center <= tolerance_pct:
                group.append(other)
                used.add(j)

        if len(group) >= 2:
            kinds = list({g.get("kind") for g in group})
            total_strength = sum(g.get("strength") or 0 for g in group)
            collisions.append({
                "center": center,
                "kinds": kinds,
                "n_overlapping": len(group),
                "total_strength": round(total_strength, 2),
                "setups": group,
            })

    collisions.sort(key=lambda c: c["n_overlapping"], reverse=True)
    return collisions


def flow_gex_confluence(
    flow_direction: str | None,
    profile: list[dict],
    spot: float,
) -> dict:
    """Measure agreement between option flow direction and GEX structure.

    When flow is bullish (buy calls) AND GEX shows positive structure above spot,
    that's confluence. When they disagree, the setup is weaker.

    Returns: confluence_score [-100, 100], interpretation.
    """
    if not profile or not spot or not flow_direction:
        return {"score": None, "interpretation": None}

    # Determine GEX bias
    above = [p for p in profile if p["strike"] > spot * 1.005]
    below = [p for p in profile if p["strike"] < spot * 0.995]

    above_net = sum(p.get("net") or 0 for p in above)
    below_net = sum(p.get("net") or 0 for p in below)

    if above_net > 0 and below_net < 0:
        gex_bias = "bullish"  # positive above = ceiling, negative below = floor
    elif above_net < 0 and below_net > 0:
        gex_bias = "bearish"
    else:
        gex_bias = "neutral"

    flow = flow_direction.lower().strip()
    if flow in ("bullish", "buy", "calls", "long"):
        flow_bias = "bullish"
    elif flow in ("bearish", "sell", "puts", "short"):
        flow_bias = "bearish"
    else:
        flow_bias = "neutral"

    # Score
    if gex_bias == flow_bias and gex_bias != "neutral":
        score = 80
        interpretation = "Strong confluence — flow and GEX structure aligned."
    elif gex_bias == "neutral" or flow_bias == "neutral":
        score = 20
        interpretation = "Partial signal — one side is neutral."
    elif gex_bias != flow_bias:
        score = -60
        interpretation = "Divergence — flow direction conflicts with GEX structure."
    else:
        score = 0
        interpretation = "Unclear."

    return {
        "score": score,
        "flow_bias": flow_bias,
        "gex_bias": gex_bias,
        "interpretation": interpretation,
    }
