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


# Two hours, in years. Floors same-day time-to-expiry so Black-Scholes stays
# solvable after the 16:00 ET close without pretending the contract is long-dated.
_MIN_T_YEARS = 2.0 / (24 * 365)


def _years_to_expiry(expiry):
    """Years until the expiry's 20:00 UTC settlement.

    Same-day expiries are floored rather than rejected. Returning None once the
    close has passed zeroed every 0DTE cell in the grid: measured against the real
    product across 10 tickers, 886 of 1,801 same-day cells were zero on our side
    and non-zero on theirs, with none the other way round. The real product keeps
    showing same-day exposure after the bell, and it is real — those contracts hold
    open interest until settlement.

    Only genuinely past dates return None.
    """
    try:
        expiry_date = datetime.fromisoformat(expiry).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None
    now = datetime.now(timezone.utc)
    years = (expiry_date + timedelta(hours=20) - now).total_seconds() / (365.25 * 86400)
    if years > 0:
        return years
    # Past 20:00 UTC on the expiry date itself — floor instead of dropping.
    return _MIN_T_YEARS if expiry_date.date() >= now.date() else None


# A mid at or below this is the exchange's minimum tick on a worthless option. It
# carries no volatility information: inverting Black-Scholes on it is
# ill-conditioned and returns whatever the solver's ceiling allows.
MIN_INFORMATIVE_MID = 0.01


def iv_is_ill_conditioned(contract):
    """True when this contract's gamma rests on a quote that carries no vol signal.

    A mid at or below the minimum tick means the option is worth essentially
    nothing, and inverting Black-Scholes on it is ill-conditioned: a half-cent of
    quote noise moves implied vol by hundreds of points, and the solved gamma with
    it. These cells are not wrong on average — against the real product their
    median ratio is 0.973 — but they are individually noisy, disagreeing by a
    median 35% where cells with real quotes on both legs agree to 3.8%.

    Substituting a neighbouring strike's IV, or the same strike's other leg, both
    measured worse (see resolve_iv). So the value is kept and the weakness is
    flagged, the same way gamma_modeled and oi_from_volume are.
    """
    mid = number(contract.get("mid"))
    return mid is not None and 0 < mid <= MIN_INFORMATIVE_MID



def _solve_own_iv(contract, spot):
    """IV from this contract alone — feed value, else inverted from its own mid.

    Not memoised on the contract. Chain contracts are cached and reused across
    requests at different spots, so caching a solved IV on the dict leaks a stale
    value into a later call — measured as a jump from 1.33/0.34/0.09% to
    8.07/1.64/0.76% median error on the 4-7 / 8-30 / 31+ DTE buckets.
    """

    iv = number(contract.get("iv"))
    if iv is not None:
        if iv > 5:
            iv /= 100.0
        if iv > 0:
            return iv
    mid = number(contract.get("mid"))
    strike = number(contract.get("strike"))
    if mid is None or mid <= 0 or strike is None or not spot or spot <= 0 or strike <= 0:
        return None
    years = _years_to_expiry(contract.get("expiry"))
    if years is None:
        return None
    import greeks

    return greeks.implied_vol(mid, spot, strike, years, contract.get("type") == "call")



def resolve_iv(contract, spot, strike_iv=None):
    """Contract IV, solved from the mid price when the feed omits it.

    Alpaca supplies `impliedVolatility` on exactly the same contracts it supplies
    `greeks.gamma` for — measured on AAPL 2026-08-07, both were present on 0/166
    same-day contracts and 52/124, 66/126, 81/166 on the next three expirations. So
    the IV-based model_gamma() fallback below could never actually fire on a contract
    that needed it. A `mid` price, by contrast, is quoted for ~100% of contracts, so
    inverting Black-Scholes on the mid recovers usable IV for the whole chain.
    Validated against Alpaca's own gamma where both exist (n=1068 AAPL contracts):
    correlation 0.9998, median ratio 1.003.

    IV comes from this contract's own quote and nothing else. Two substitutions
    were tried against the real product and BOTH measured worse, so `strike_iv` is
    accepted and ignored:

      * Borrowing from neighbouring strikes degraded the 4-7 / 8-30 / 31+ DTE
        buckets from 0.82 / 0.37 / 0.04% to 2.18 / 0.70 / 0.43% median error.
      * Borrowing the other leg of the SAME strike, on put-call parity grounds,
        degraded them from 1.33 / 0.34 / 0.09% to 8.28 / 1.60 / 0.76%. Parity fixes
        one IV per strike for European options; American equity options carry
        early-exercise, dividend and borrow effects that make the call and put legs
        genuinely price at different implied vols, and the data says so.

    See MIN_INFORMATIVE_MID for what is still imperfect and why it is disclosed
    rather than patched.
    """
    return _solve_own_iv(contract, spot)


def model_gamma(contract, spot, strike_iv=None):
    """Black-Scholes gamma estimate when the chain lacks a greek gamma.

    Returns None when inputs are insufficient (no IV/mid, expired, etc.).
    """
    strike, expiry = number(contract.get("strike")), contract.get("expiry")
    if strike is None or not expiry or spot <= 0 or strike <= 0:
        return None
    iv = resolve_iv(contract, spot, strike_iv)
    if iv is None or iv <= 0:
        return None
    years = _years_to_expiry(expiry)
    if years is None:
        return None
    d1 = (math.log(spot / strike) + (0.045 + 0.5 * iv * iv) * years) / (iv * math.sqrt(years))
    normal_pdf = math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi)
    return normal_pdf / (spot * iv * math.sqrt(years))


def contract_size(contract):
    """Contracts outstanding to weight exposure by: open interest, else session volume.

    Alpaca returns `open_interest: null` for a minority of listed contracts (40 of 420
    AAPL matrix cells on 2026-08-07). Those rendered as blank cells while the real
    product showed a number, so it evidently falls back to same-day volume there.
    Tested against the real grid on exactly those cells: several reproduce to the
    dollar (277.5 Aug-12 -264 vs -264; 287.5 Aug-10 -7,200 vs -7,181) with a median
    real/computed ratio of 0.925.

    Volume is a PROXY for open interest, not the same quantity — it counts a day's
    trades rather than outstanding contracts, and is used only when OI is genuinely
    absent. Callers that need to distinguish the two should check `open_interest`
    themselves; `oi_is_proxy()` reports which path was taken.
    """
    oi = number(contract.get("open_interest"))
    if oi is not None:
        return oi
    return number(contract.get("volume"))


def oi_is_proxy(contract):
    """True when contract_size() had to substitute volume for absent open interest."""
    return number(contract.get("open_interest")) is None and number(contract.get("volume")) is not None


def gex(contract, spot, strike_iv=None):
    """Dollar-gamma * OI heuristic. Missing OI falls back to volume (see contract_size);
    missing both remains unknown, not zero. Not dealer-verified."""
    gamma = number(contract.get("gamma"))
    size = contract_size(contract)
    if gamma is None:
        gamma = model_gamma(contract, spot, strike_iv)
    if gamma is None or size is None:
        return None
    magnitude = gamma * size * 100 * spot * spot * 0.01
    return magnitude if contract["type"] == "call" else -magnitude


def model_vanna(contract, spot, strike_iv=None):
    """Black-Scholes vanna estimate.

    Vanna = d(delta)/d(sigma) = -phi(d1) * d2 / sigma.
    """
    strike, expiry = number(contract.get("strike")), contract.get("expiry")
    if strike is None or not expiry or spot <= 0 or strike <= 0:
        return None
    # Same IV-coverage gap as model_gamma() — see resolve_iv().
    iv = resolve_iv(contract, spot, strike_iv)
    if iv is None or iv <= 0:
        return None
    years = _years_to_expiry(expiry)
    if years is None:
        return None
    root = iv * math.sqrt(years)
    d1 = (math.log(spot / strike) + (0.045 + 0.5 * iv * iv) * years) / root
    d2 = d1 - root
    normal_pdf = math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi)
    return -normal_pdf * d2 / iv


def vex(contract, spot, strike_iv=None):
    """Vanna * OI heuristic. Same sign convention as gex."""
    vanna, size = model_vanna(contract, spot, strike_iv), contract_size(contract)
    if vanna is None or size is None:
        return None
    magnitude = vanna * size * 100 * spot * 0.01
    return magnitude if contract["type"] == "call" else -magnitude


def _side_cells(cells, side):
    """Cells whose `side` exposure is genuinely measured.

    `cell["available"]` is the AND of both sides, so it is false for a strike/expiration
    where calls are unlisted but puts are measured -- 363 of SPY's 1,044 cells on
    2026-08-12. Row inclusion used that strict flag while the sums added any non-null
    value, so those one-sided measurements counted for rows that survived the test and
    were discarded entirely for rows that did not. Each side is now selected on its own
    availability flag, with a fallback for callers that build rows without the per-side
    flags.
    """
    field = f"{side}_gex"
    flag = f"{side}_gex_available"
    out = []
    for cell in cells:
        value = cell.get(field)
        if value is None:
            continue
        if flag in cell:
            if not cell[flag]:
                continue
        elif not cell.get("available", True):
            continue
        out.append(value)
    return out


def profile_summary(rows, spot=None):
    """Derive key GEX profile levels from matrix rows.

    Returns global_max_strike, call_wall_strike, put_wall_strike, gamma_flip_level, plus
    the full set of sign changes behind that flip level.

    The flip is the crossing **nearest spot**. It used to be whichever crossing came first
    scanning up from the lowest strike, which is arbitrary whenever the net profile changes
    sign more than once -- and it usually does. SPY on 2026-08-12 had 13 crossings spanning
    740.99 to 773.63 against a spot of 772.68: the reported level was 740.99, the lowest,
    4.1% away from spot, while the crossing that describes dealer positioning around the
    current price was 773.63. Nearest-spot is also the stable choice; recomputing over
    different subsets of well-covered expirations moved it only between 772.26 and 773.63,
    while first-from-the-bottom moved with the noise.

    `spot` is optional so existing callers keep working. Without it the crossing nearest the
    dominant (largest absolute net) strike is used, which is the best available proxy, and
    `gamma_flip_reference` records which rule was applied.
    """
    profile = []
    for row in rows:
        calls = _side_cells(row["cells"], "call")
        puts = _side_cells(row["cells"], "put")
        if not calls and not puts:
            continue
        call = sum(calls)
        put = sum(puts)
        profile.append({
            "strike": row["strike"],
            "call": call,
            "put": put,
            "net": call + put,
            "cells": len(calls) + len(puts),
        })
    if not profile:
        return {
            "global_max_strike": None,
            "call_wall_strike": None,
            "put_wall_strike": None,
            "gamma_flip_level": None,
            "gamma_flip_candidates": [],
            "gamma_flip_reference": None,
        }
    profile.sort(key=lambda item: item["strike"])
    global_max = max(profile, key=lambda item: abs(item["net"]))
    call_wall = max(profile, key=lambda item: item["call"])
    put_wall = min(profile, key=lambda item: item["put"])

    crossings = []
    for left, right in zip(profile, profile[1:]):
        if left["net"] == 0.0:
            crossings.append(left["strike"])
            continue
        if left["net"] * right["net"] < 0:
            span = right["net"] - left["net"]
            crossings.append(left["strike"] + (-left["net"] / span) * (right["strike"] - left["strike"]))
    if profile[-1]["net"] == 0.0:
        crossings.append(profile[-1]["strike"])

    reference = number(spot)
    rule = "nearest_spot"
    if reference is None:
        reference = global_max["strike"]
        rule = "nearest_dominant_strike"
    flip = min(crossings, key=lambda value: abs(value - reference)) if crossings else None

    return {
        "global_max_strike": global_max["strike"],
        "call_wall_strike": call_wall["strike"] if call_wall["call"] > 0 else None,
        "put_wall_strike": put_wall["strike"] if put_wall["put"] < 0 else None,
        "gamma_flip_level": flip,
        # Every sign change, so a reader can see whether the profile has one clean flip or
        # oscillates. A long list means the net profile is noisy and the single level is
        # weak evidence.
        "gamma_flip_candidates": [round(value, 4) for value in sorted(crossings)],
        "gamma_flip_reference": rule,
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
