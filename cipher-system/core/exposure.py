"""Pure computation layer for GEX/VEX exposure, contract parsing, and scoring helpers.

Every function in this module is free of HTTP, cache, and side-effect dependencies.
They can be imported and tested in isolation without a running server or network.

GEX formula (canonical — do not change silently):
    call_gex =  gamma * OI * 100 * spot**2 * 0.01
    put_gex  = -gamma * OI * 100 * spot**2 * 0.01
    net_gex  = call_gex + put_gex

Missing gamma or open interest is unknown — not zero.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone

OCC = re.compile(r"^([A-Z.]+)(\d{6})([CP])(\d{8})$")

# Matrix / Night Vision expiration depth.
# Cap keeps payload size bounded; UI presets: 1 Exp=1, Compact=5, Full=12, Leap=36.
MAX_MATRIX_EXPIRATIONS = 36
DEFAULT_MATRIX_EXPIRATIONS = 12


def number(value):
    """Coerce to float, treating None and empty string as None."""
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def parse_contract(symbol):
    """Parse an OCC option symbol into expiry, strike, and type."""
    match = OCC.match(symbol.upper())
    if not match:
        return {"symbol": symbol.upper(), "expiry": None, "strike": None, "type": None}
    _, raw_date, kind, raw_strike = match.groups()
    return {
        "symbol": symbol.upper(),
        "expiry": f"20{raw_date[:2]}-{raw_date[2:4]}-{raw_date[4:]}",
        "strike": int(raw_strike) / 1000,
        "type": "call" if kind == "C" else "put",
    }


def model_gamma(contract, spot):
    """Black-Scholes gamma estimate when the chain lacks a greek gamma.

    Returns None when inputs are insufficient (no IV, expired, etc.).
    """
    iv, strike, expiry = number(contract.get("iv")), number(contract.get("strike")), contract.get("expiry")
    if iv is None or strike is None or not expiry or spot <= 0 or strike <= 0:
        return None
    if iv > 5:
        iv /= 100.0
    if iv <= 0:
        return None
    try:
        expiry_at = datetime.fromisoformat(expiry).replace(tzinfo=timezone.utc) + timedelta(hours=20)
    except ValueError:
        return None
    years = (expiry_at - datetime.now(timezone.utc)).total_seconds() / (365.25 * 86400)
    if years <= 0:
        return None
    d1 = (math.log(spot / strike) + (0.045 + 0.5 * iv * iv) * years) / (iv * math.sqrt(years))
    normal_pdf = math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi)
    return normal_pdf / (spot * iv * math.sqrt(years))


def gex(contract, spot):
    """Dollar-gamma * OI heuristic. Missing OI remains unknown (not dealer-verified)."""
    gamma, oi = number(contract.get("gamma")), number(contract.get("open_interest"))
    if gamma is None:
        gamma = model_gamma(contract, spot)
    if gamma is None or oi is None:
        return None
    magnitude = gamma * oi * 100 * spot * spot * 0.01
    return magnitude if contract["type"] == "call" else -magnitude


def model_vanna(contract, spot):
    """Black-Scholes vanna estimate.

    Vanna = d(delta)/d(sigma) = -phi(d1) * d2 / sigma.
    """
    iv, strike, expiry = number(contract.get("iv")), number(contract.get("strike")), contract.get("expiry")
    if iv is None or strike is None or not expiry or spot <= 0 or strike <= 0:
        return None
    if iv > 5:
        iv /= 100.0
    if iv <= 0:
        return None
    try:
        expiry_at = datetime.fromisoformat(expiry).replace(tzinfo=timezone.utc) + timedelta(hours=20)
    except ValueError:
        return None
    years = (expiry_at - datetime.now(timezone.utc)).total_seconds() / (365.25 * 86400)
    if years <= 0:
        return None
    root = iv * math.sqrt(years)
    d1 = (math.log(spot / strike) + (0.045 + 0.5 * iv * iv) * years) / root
    d2 = d1 - root
    normal_pdf = math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi)
    return -normal_pdf * d2 / iv


def vex(contract, spot):
    """Vanna * OI heuristic. Same sign convention as gex."""
    vanna, oi = model_vanna(contract, spot), number(contract.get("open_interest"))
    if vanna is None or oi is None:
        return None
    magnitude = vanna * oi * 100 * spot * 0.01
    return magnitude if contract["type"] == "call" else -magnitude


def profile_summary(rows):
    """Derive key GEX profile levels from matrix rows.

    Returns global_max_strike, call_wall_strike, put_wall_strike, gamma_flip_level.
    """
    profile = []
    for row in rows:
        observed = any(cell["available"] for cell in row["cells"])
        if not observed:
            continue
        call = sum(cell["call_gex"] for cell in row["cells"])
        put = sum(cell["put_gex"] for cell in row["cells"])
        profile.append({"strike": row["strike"], "call": call, "put": put, "net": call + put})
    if not profile:
        return {
            "global_max_strike": None,
            "call_wall_strike": None,
            "put_wall_strike": None,
            "gamma_flip_level": None,
        }
    profile.sort(key=lambda item: item["strike"])
    global_max = max(profile, key=lambda item: abs(item["net"]))
    call_wall = max(profile, key=lambda item: item["call"])
    put_wall = min(profile, key=lambda item: item["put"])
    flip = None
    for left, right in zip(profile, profile[1:]):
        if left["net"] == 0:
            flip = left["strike"]
            break
        if left["net"] * right["net"] < 0:
            span = right["net"] - left["net"]
            flip = left["strike"] + (-left["net"] / span) * (right["strike"] - left["strike"])
            break
    return {
        "global_max_strike": global_max["strike"],
        "call_wall_strike": call_wall["strike"] if call_wall["call"] > 0 else None,
        "put_wall_strike": put_wall["strike"] if put_wall["put"] < 0 else None,
        "gamma_flip_level": flip,
    }


def classify_aggressor(last, bid, ask, near_threshold_ratio=0.15):
    """Infer the aggressor side from trade price vs bid/ask.

    Returns 'buy', 'sell', or 'unknown'.
    """
    if last is None or last <= 0 or bid is None or ask is None or bid <= 0 or ask <= 0:
        return "unknown"
    spread = ask - bid
    if spread <= 0:
        if last >= ask:
            return "buy"
        if last <= bid:
            return "sell"
        return "unknown"
    tolerance = spread * near_threshold_ratio
    if last >= ask - tolerance:
        return "buy"
    if last <= bid + tolerance:
        return "sell"
    dist_to_ask = abs(last - ask)
    dist_to_bid = abs(last - bid)
    if dist_to_ask < dist_to_bid:
        return "buy"
    if dist_to_bid < dist_to_ask:
        return "sell"
    mid = (bid + ask) / 2.0
    if abs(last - mid) < 1e-9:
        return "unknown"
    return "buy" if last > mid else "sell"


def premium_tier(premium):
    """Classify option premium into Spyglass tier buckets."""
    if premium is None:
        return "unknown"
    if premium < 20_000:
        return "below"
    if premium < 50_000:
        return "small"
    if premium < 150_000:
        return "medium"
    if premium < 500_000:
        return "large"
    return "whale"


def _depth_is_full_chain(depth) -> bool:
    """UI 'All' and explicit all/full/chain -> every listed strike (commercial CSV parity)."""
    if depth is None:
        return False
    if isinstance(depth, str):
        raw = depth.strip().lower()
        if raw in {"all", "full", "chain", "entire"}:
            return True
        if raw.endswith("%"):
            try:
                return float(raw.rstrip("%")) >= 25.0
            except ValueError:
                return False
    try:
        value = float(depth)
    except (TypeError, ValueError):
        return False
    return value >= 0.25


def _depth_to_points(spot, depth):
    """Accept absolute points or a percent string/value (e.g. 0.06 or '6%').

    Full-chain depth returns +inf so strike filters keep every listed strike.
    """
    if _depth_is_full_chain(depth):
        return float("inf")
    if isinstance(depth, str) and depth.strip().endswith("%"):
        pct = float(depth.strip().rstrip("%")) / 100.0
        return max(0.5, spot * pct)
    value = float(depth)
    if value <= 1.0:
        return max(0.5, spot * value)
    return value


def _clamp_expiration_count(expiration_count) -> int:
    return max(1, min(int(expiration_count), MAX_MATRIX_EXPIRATIONS))


def _matrix_chain_pages(expiration_count: int) -> int:
    """Scale snapshot pages with requested expiration depth so Leap can see LEAPs."""
    n = max(1, int(expiration_count))
    if n <= 6:
        return 12
    if n <= 12:
        return 16
    if n <= 18:
        return 20
    return 24


def _matrix_oi_horizon_days(expiration_count: int) -> int:
    """Calendar days of OI metadata to pull for the requested column depth."""
    n = max(1, int(expiration_count))
    return max(120, 40 * n)
