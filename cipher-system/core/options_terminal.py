"""Normalized conventional option chain and read-only multi-leg payoff analysis."""
from __future__ import annotations

from datetime import date, datetime, timezone
import math
from typing import Any, Sequence

GREEKS = ("delta", "gamma", "theta", "vega", "rho")


def _age_seconds(value: str | None, now: datetime) -> float | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0.0, (now.astimezone(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds())


def _contract(row: dict, spot: float, now: datetime) -> dict:
    bid, ask = row.get("bid"), row.get("ask")
    mid = row.get("mid")
    spread = (ask - bid) if bid is not None and ask is not None and ask >= bid else None
    spread_pct = spread / mid * 100 if spread is not None and mid and mid > 0 else None
    strike = float(row["strike"])
    kind = row["type"]
    distance_pct = (strike / spot - 1) * 100 if spot else None
    intrinsic = max(0.0, spot - strike) if kind == "call" else max(0.0, strike - spot)
    if abs(strike / spot - 1) <= 0.0025:
        moneyness = "ATM"
    elif (kind == "call" and strike < spot) or (kind == "put" and strike > spot):
        moneyness = "ITM"
    else:
        moneyness = "OTM"
    quote_age = _age_seconds(row.get("quote_time"), now)
    flags = []
    if bid is None or ask is None or ask < bid:
        flags.append("NO_EXECUTABLE_MARKET")
    if spread_pct is not None and spread_pct > 15:
        flags.append("WIDE_SPREAD")
    if row.get("open_interest") is None:
        flags.append("OI_UNKNOWN")
    elif row["open_interest"] < 100:
        flags.append("LOW_OI")
    if row.get("volume") is None:
        flags.append("VOLUME_UNKNOWN")
    elif row["volume"] < 10:
        flags.append("LOW_VOLUME")
    if quote_age is None or quote_age > 120:
        flags.append("STALE_QUOTE")
    return {
        **row, "buy_price": ask, "sell_price": bid, "spread": spread,
        "spread_pct": spread_pct, "quote_age_seconds": quote_age,
        "moneyness": moneyness, "distance_pct": distance_pct,
        "intrinsic": intrinsic, "extrinsic": mid - intrinsic if mid is not None else None,
        "liquidity_flags": flags,
        # Unknown OI/volume cannot truthfully earn a positive liquidity verdict.
        "liquid": None if any(x in flags for x in ("OI_UNKNOWN", "VOLUME_UNKNOWN")) else not any(x in flags for x in (
            "NO_EXECUTABLE_MARKET", "WIDE_SPREAD", "LOW_OI", "LOW_VOLUME", "STALE_QUOTE"
        )),
    }


def chain_view(ticker: str, spot_quote: dict, rows: Sequence[dict], *,
               expiration_limit: int = 6, now: datetime | None = None) -> dict:
    moment = now or datetime.now(timezone.utc)
    spot = float(spot_quote["price_context"])
    today = moment.date()
    expirations = sorted({r.get("expiry") for r in rows if r.get("expiry") and r["expiry"] >= today.isoformat()})[:max(1, min(expiration_limit, 12))]
    contracts = [_contract(r, spot, moment) for r in rows if r.get("expiry") in expirations]
    grouped = []
    term = []
    for expiry in expirations:
        subset = [r for r in contracts if r["expiry"] == expiry]
        by_strike = {}
        for row in subset:
            by_strike.setdefault(row["strike"], {"strike": row["strike"], "call": None, "put": None})[row["type"]] = row
        atm = min(subset, key=lambda r: abs(r["strike"] - spot), default=None)
        atm_strike = atm["strike"] if atm else None
        atm_call = next((r for r in subset if r["type"] == "call" and r["strike"] == atm_strike), None)
        atm_put = next((r for r in subset if r["type"] == "put" and r["strike"] == atm_strike), None)
        expected = (
            atm_call["mid"] + atm_put["mid"]
            if atm_call and atm_put and atm_call.get("mid") is not None and atm_put.get("mid") is not None else None
        )
        call25 = min((r for r in subset if r["type"] == "call" and r.get("delta") is not None), key=lambda r: abs(r["delta"] - .25), default=None)
        put25 = min((r for r in subset if r["type"] == "put" and r.get("delta") is not None), key=lambda r: abs(r["delta"] + .25), default=None)
        skew = (
            put25["iv"] - call25["iv"] if put25 and call25 and put25.get("iv") is not None and call25.get("iv") is not None else None
        )
        dte = (date.fromisoformat(expiry) - today).days
        grouped.append({"expiration": expiry, "dte": dte, "expected_move": expected,
                        "expected_move_pct": expected / spot * 100 if expected is not None else None,
                        "put_call_25d_skew": skew, "rows": [by_strike[k] for k in sorted(by_strike)]})
        term.append({"expiration": expiry, "dte": dte, "atm_strike": atm_strike,
                     "atm_iv": (atm_call or atm_put or {}).get("iv"), "expected_move": expected})
    newest = max((r.get("quote_time") for r in contracts if r.get("quote_time")), default=None)
    return {
        "ticker": ticker.upper(), "spot": spot, "spot_as_of": spot_quote.get("as_of"),
        "generated_at": moment.isoformat(), "as_of": newest,
        "feed": contracts[0].get("feed") if contracts else None,
        "expirations": grouped, "term_structure": term,
        "iv_rank": None, "iv_percentile": None,
        "iv_history_status": "UNAVAILABLE_INSUFFICIENT_HISTORY",
        "open_interest_caveat": "OI carries its provider date and may be prior-session data; missing OI stays unknown.",
        "read_only": True,
    }


def analyze_structure(ticker: str, spot: float, legs: Sequence[dict]) -> dict[str, Any]:
    if not 1 <= len(legs) <= 8:
        raise ValueError("structure requires 1 to 8 legs")
    normalized = []
    entry_cash = 0.0
    greeks = {name: 0.0 for name in GREEKS}
    unknown_greeks = set()
    expiries = set()
    for raw in legs:
        side = str(raw.get("side") or "").lower()
        if side not in {"buy", "sell"}:
            raise ValueError("each leg side must be buy or sell")
        quantity = int(raw.get("quantity") or 0)
        if quantity < 1 or quantity > 10_000:
            raise ValueError("leg quantity must be 1..10000")
        kind = str(raw.get("type") or "").lower()
        is_stock = kind == "stock"
        price = raw.get("entry_price", spot) if is_stock else raw.get("ask") if side == "buy" else raw.get("bid")
        if price is None or float(price) < 0:
            raise ValueError("each leg needs an executable bid/ask")
        sign = 1 if side == "buy" else -1
        multiplier = 1 if is_stock else 100
        entry_cash -= sign * quantity * float(price) * multiplier
        leg = {**raw, "type": kind, "side": side, "quantity": quantity, "entry_price": float(price), "sign": sign, "multiplier": multiplier}
        normalized.append(leg)
        if not is_stock:
            if kind not in {"call", "put"}:
                raise ValueError("option leg type must be call or put")
            expiries.add(str(raw["expiration"]))
        for name in GREEKS:
            value = (1.0 if name == "delta" else 0.0) if is_stock else raw.get(name)
            if value is None:
                unknown_greeks.add(name)
            else:
                greeks[name] += sign * quantity * float(value) * multiplier
    # Stock has no expiry and may hedge one option expiry. A stock-only
    # position is also a well-defined terminal payoff.
    same_expiry = len(expiries) <= 1
    strikes = [float(x["strike"]) for x in normalized if x["type"] != "stock"]
    lo, hi = max(.01, min(strikes + [spot]) * .5), max(strikes + [spot]) * 1.5
    points = []
    if same_expiry:
        for i in range(401):
            terminal = lo + (hi - lo) * i / 400
            pnl = entry_cash
            for leg in normalized:
                if leg["type"] == "stock":
                    value = terminal
                else:
                    value = max(0, terminal - float(leg["strike"])) if leg["type"] == "call" else max(0, float(leg["strike"]) - terminal)
                pnl += leg["sign"] * leg["quantity"] * value * leg["multiplier"]
            points.append({"underlying": terminal, "pnl": pnl})
    def terminal_pnl(terminal: float) -> float:
        result = entry_cash
        for leg in normalized:
            if leg["type"] == "stock":
                value = terminal
            else:
                value = max(0, terminal - float(leg["strike"])) if leg["type"] == "call" else max(0, float(leg["strike"]) - terminal)
            result += leg["sign"] * leg["quantity"] * value * leg["multiplier"]
        return result

    # Expiry payoff is piecewise linear, so extrema occur at zero, a strike, or
    # infinity. Sampling the chart grid here used to miss true maxima and naked-call risk.
    critical_pnls = [terminal_pnl(value) for value in sorted({0.0, *strikes})] if same_expiry else []
    max_profit = max(critical_pnls, default=None)
    max_loss = min(critical_pnls, default=None)
    breakevens = []
    for a, b in zip(points, points[1:]):
        if abs(a["pnl"]) < 1e-9:
            breakevens.append(a["underlying"])
        elif a["pnl"] * b["pnl"] < 0:
            breakevens.append(a["underlying"] + (b["underlying"] - a["underlying"]) * (-a["pnl"]) / (b["pnl"] - a["pnl"]))
    upside_slope = sum(leg["sign"] * leg["quantity"] * leg["multiplier"] for leg in normalized if leg["type"] in {"stock", "call"}) if same_expiry else 0.0
    unbounded_up = same_expiry and upside_slope > 1e-9
    unbounded_down = same_expiry and upside_slope < -1e-9
    liquidity_warnings = sorted({flag for leg in normalized for flag in (leg.get("liquidity_flags") or [])})
    short_options = [leg for leg in normalized if leg["type"] in {"call", "put"} and leg["side"] == "sell"]
    risk_per_structure = None if unbounded_down or not same_expiry or max_loss is None else abs(min(0.0, max_loss))
    return {
        "ticker": ticker.upper(), "spot": spot, "legs": normalized,
        "net_debit": round(max(0.0, -entry_cash), 2), "net_credit": round(max(0.0, entry_cash), 2),
        "max_profit": None if unbounded_up or not same_expiry else round(max_profit, 2),
        "max_loss": None if unbounded_down or not same_expiry else round(max_loss, 2),
        "max_profit_unbounded": unbounded_up, "max_loss_unbounded": unbounded_down,
        "breakevens": [round(value, 4) for value in breakevens], "aggregate_greeks": {k: None if k in unknown_greeks else v for k, v in greeks.items()},
        "risk_per_structure": None if risk_per_structure is None else round(risk_per_structure, 2),
        "liquidity_warnings": liquidity_warnings,
        "payoff": points[::8], "same_expiration": same_expiry,
        "calendar_caveat": None if same_expiry else "Calendar/diagonal terminal payoff requires a future-volatility model; max P/L and breakevens are intentionally unavailable.",
        "assignment_warning": bool(short_options),
        "ex_dividend_warning": any(leg["type"] == "call" for leg in short_options),
        "research_only": True, "execution_capability": False,
    }
