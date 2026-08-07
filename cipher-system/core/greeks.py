"""Black-Scholes gamma, with implied volatility solved from the option's mid price.

Why this exists: Alpaca's option snapshots only carry `greeks`/`impliedVolatility` for
a subset of contracts — measured on AAPL 2026-08-07, gamma was present for 0 of 166
same-day (0DTE) contracts and only 52 of 124, 66 of 126, 81 of 166 on the next three
expirations. Since GEX = gamma x OI x ..., every contract missing gamma silently
contributed exactly 0 exposure, blanking the nearest expiration column outright and
thinning every other one. A `mid` price, by contrast, was available for 100% of
contracts, so implied vol can be recovered numerically and gamma computed in closed
form. The real product clearly does the same — it renders a populated 0DTE column.

Research-only: this reconstructs gamma under textbook Black-Scholes assumptions
(European exercise, no dividends, flat rate). It is not broker-grade risk output.
"""
from __future__ import annotations

import math

# Flat short-rate assumption. Gamma is very insensitive to r at these tenors.
RISK_FREE_RATE = 0.04
# Time-to-expiry floor, in years. Gamma -> infinity as T -> 0, so same-day contracts
# are floored at ~2 market hours to keep values finite and comparable.
MIN_T_YEARS = 2.0 / (24.0 * 365.0)
_IV_LO, _IV_HI = 0.01, 5.0
# Ceiling raised from 5.0 (500%) because same-day far-OTM calls genuinely price
# above it. NVDA 0DTE at spot 223.5 quotes the 300 strike at the $0.005 minimum
# tick; no volatility under 500% reproduces that, so the solver returned None and
# the cell rendered as zero. Measured against the real product, 311 same-day cells
# were zero on our side and non-zero on theirs, all of this shape. At 10.0 they
# solve at 6.2-7.9 implied. Raising further changes nothing — 10.0, 20.0 and 50.0
# give identical results, so every affected contract converges below 10.0 — and it
# cannot affect ordinary contracts, which already solved well under 5.0.
_IV_HI = 10.0
_MAX_ITERS = 60


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _d1(spot: float, strike: float, t: float, sigma: float, r: float = RISK_FREE_RATE) -> float:
    return (math.log(spot / strike) + (r + 0.5 * sigma * sigma) * t) / (sigma * math.sqrt(t))


def bs_price(spot: float, strike: float, t: float, sigma: float, is_call: bool,
             r: float = RISK_FREE_RATE) -> float:
    d1 = _d1(spot, strike, t, sigma, r)
    d2 = d1 - sigma * math.sqrt(t)
    disc = math.exp(-r * t)
    if is_call:
        return spot * _norm_cdf(d1) - strike * disc * _norm_cdf(d2)
    return strike * disc * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def implied_vol(price: float, spot: float, strike: float, t: float, is_call: bool,
                r: float = RISK_FREE_RATE) -> float | None:
    """Solve implied volatility by bisection. Returns None if the price is outside
    the no-arbitrage band (where no positive sigma reproduces it)."""
    if price is None or price <= 0 or spot <= 0 or strike <= 0 or t <= 0:
        return None
    # Intrinsic value bound — below this, no volatility can produce the price.
    disc = math.exp(-r * t)
    intrinsic = max(0.0, (spot - strike * disc) if is_call else (strike * disc - spot))
    if price < intrinsic - 1e-6:
        return None
    upper_bound = spot if is_call else strike * disc
    if price >= upper_bound:
        return None
    lo, hi = _IV_LO, _IV_HI
    try:
        if bs_price(spot, strike, t, hi, is_call, r) < price:
            return None
        for _ in range(_MAX_ITERS):
            mid = 0.5 * (lo + hi)
            if bs_price(spot, strike, t, mid, is_call, r) < price:
                lo = mid
            else:
                hi = mid
            if hi - lo < 1e-6:
                break
    except (ValueError, OverflowError, ZeroDivisionError):
        return None
    return 0.5 * (lo + hi)


def gamma(spot: float, strike: float, t: float, sigma: float,
          r: float = RISK_FREE_RATE) -> float | None:
    """Black-Scholes gamma (identical for calls and puts)."""
    if not spot or not strike or not sigma or sigma <= 0:
        return None
    t = max(float(t), MIN_T_YEARS)
    try:
        d1 = _d1(spot, strike, t, sigma, r)
        return _norm_pdf(d1) / (spot * sigma * math.sqrt(t))
    except (ValueError, OverflowError, ZeroDivisionError):
        return None


def years_to_expiry(expiry_iso: str, today_iso: str) -> float:
    """Calendar days to expiry in years, floored so same-day stays finite."""
    from datetime import date

    try:
        y1, m1, d1_ = (int(x) for x in str(expiry_iso)[:10].split("-"))
        y2, m2, d2_ = (int(x) for x in str(today_iso)[:10].split("-"))
        days = (date(y1, m1, d1_) - date(y2, m2, d2_)).days
    except (ValueError, TypeError):
        return MIN_T_YEARS
    return max(days / 365.0, MIN_T_YEARS)


def gamma_from_mid(mid: float, spot: float, strike: float, expiry_iso: str,
                   today_iso: str, is_call: bool) -> float | None:
    """Full fallback path: mid price -> implied vol -> gamma."""
    t = years_to_expiry(expiry_iso, today_iso)
    sigma = implied_vol(mid, spot, strike, t, is_call)
    if sigma is None:
        return None
    return gamma(spot, strike, t, sigma)
