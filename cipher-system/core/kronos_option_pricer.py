"""Kronos-backed option spread pricing research.

Kronos forecasts the underlying OHLCV path. This module maps that forecast into
option/spread value with Black-Scholes plus a tiny local calibration layer from
captured option marks. It is research-only and does not call trading endpoints.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CORE_DIR = Path(__file__).resolve().parent
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

import kronos_research
import tradier_stream_capture as tradier


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT_DIR = DATA / "kronos_options"
CALIBRATION_PATH = OUT_DIR / "option_pricer_calibration.json"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def num(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_price(spot: float, strike: float, t_years: float, iv: float, option_type: str, rate: float = 0.045) -> float:
    if t_years <= 0 or iv <= 0 or spot <= 0 or strike <= 0:
        return max(0.0, spot - strike) if option_type == "call" else max(0.0, strike - spot)
    sigma_t = iv * math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (rate + 0.5 * iv * iv) * t_years) / sigma_t
    d2 = d1 - sigma_t
    if option_type == "call":
        return spot * normal_cdf(d1) - strike * math.exp(-rate * t_years) * normal_cdf(d2)
    return strike * math.exp(-rate * t_years) * normal_cdf(-d2) - spot * normal_cdf(-d1)


def occ_symbol(root: str, expiry: str, option_type: str, strike: float) -> str:
    y, m, d = expiry.split("-")
    cp = "C" if option_type.lower().startswith("c") else "P"
    return f"{root.upper()}{y[2:]}{m}{d}{cp}{int(round(float(strike) * 1000)):08d}"


def expiry_close_utc(expiry: str) -> datetime:
    return datetime.fromisoformat(expiry + "T20:00:00+00:00")


def years_to_expiry(expiry: str, as_of: datetime | None = None) -> float:
    as_of = as_of or datetime.now(timezone.utc)
    return max((expiry_close_utc(expiry) - as_of).total_seconds() / (365.0 * 24 * 3600), 0.0)


def tradier_quotes(symbols: list[str], greeks: bool = True) -> dict[str, dict[str, Any]]:
    token, _ = tradier.load_credentials("production")
    url = "https://api.tradier.com/v1/markets/quotes?" + urllib.parse.urlencode({
        "symbols": ",".join(symbols),
        "greeks": "true" if greeks else "false",
    })
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=25) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    quotes = (payload.get("quotes") or {}).get("quote") or []
    if isinstance(quotes, dict):
        quotes = [quotes]
    return {str(row.get("symbol") or "").upper(): row for row in quotes}


def mid(quote: dict[str, Any]) -> float | None:
    bid = num(quote.get("bid"))
    ask = num(quote.get("ask"))
    last = num(quote.get("last"))
    if bid is not None and ask is not None and ask >= bid and ask > 0:
        return (bid + ask) / 2.0
    return last


def iv_from_quote(quote: dict[str, Any]) -> float | None:
    greeks = quote.get("greeks") or {}
    for key in ("mid_iv", "smv_vol", "ask_iv", "bid_iv"):
        value = num(greeks.get(key))
        if value and value > 0:
            return value
    return None


def load_calibration() -> dict[str, Any]:
    if CALIBRATION_PATH.is_file():
        return json.loads(CALIBRATION_PATH.read_text(encoding="utf-8"))
    return {"spread_multiplier": 1.0, "iv_multiplier": 1.0, "samples": 0}


def current_spread_mark(
    ticker: str,
    expiry: str,
    option_type: str,
    long_strike: float,
    short_strike: float,
) -> dict[str, Any]:
    long_symbol = occ_symbol(ticker, expiry, option_type, long_strike)
    short_symbol = occ_symbol(ticker, expiry, option_type, short_strike)
    quotes = tradier_quotes([ticker.upper(), long_symbol, short_symbol], greeks=True)
    long_quote = quotes.get(long_symbol) or {}
    short_quote = quotes.get(short_symbol) or {}
    long_mid = mid(long_quote)
    short_mid = mid(short_quote)
    spot = num((quotes.get(ticker.upper()) or {}).get("last")) or mid(quotes.get(ticker.upper()) or {})
    mark = (long_mid - short_mid) if long_mid is not None and short_mid is not None else None
    return {
        "ticker": ticker.upper(),
        "expiry": expiry,
        "option_type": option_type,
        "long_strike": float(long_strike),
        "short_strike": float(short_strike),
        "long_symbol": long_symbol,
        "short_symbol": short_symbol,
        "spot": spot,
        "mark": mark,
        "long_quote": long_quote,
        "short_quote": short_quote,
        "long_iv": iv_from_quote(long_quote),
        "short_iv": iv_from_quote(short_quote),
    }


def theoretical_spread(mark_payload: dict[str, Any], spot: float, t_years: float, calibration: dict[str, Any]) -> float | None:
    long_iv = mark_payload.get("long_iv")
    short_iv = mark_payload.get("short_iv")
    if not long_iv or not short_iv:
        return None
    iv_mult = float(calibration.get("iv_multiplier") or 1.0)
    spread_mult = float(calibration.get("spread_multiplier") or 1.0)
    long_price = bs_price(
        spot,
        float(mark_payload["long_strike"]),
        t_years,
        float(long_iv) * iv_mult,
        str(mark_payload["option_type"]),
    )
    short_price = bs_price(
        spot,
        float(mark_payload["short_strike"]),
        t_years,
        float(short_iv) * iv_mult,
        str(mark_payload["option_type"]),
    )
    return max((long_price - short_price) * spread_mult, 0.0)


def scenario_grid(mark_payload: dict[str, Any], calibration: dict[str, Any], horizon_days: int) -> list[dict[str, Any]]:
    spot = num(mark_payload.get("spot"))
    current_mark = num(mark_payload.get("mark"))
    if not spot or not current_mark:
        return []
    t_future = max(years_to_expiry(str(mark_payload["expiry"])) - horizon_days / 365.0, 0.0)
    rows = []
    for move_pct in [-3, -2, -1, -0.5, 0, 0.5, 1, 1.5, 2, 3, 5]:
        future_spot = spot * (1 + move_pct / 100.0)
        mark = theoretical_spread(mark_payload, future_spot, t_future, calibration)
        pnl = ((mark - current_mark) / current_mark * 100.0) if mark is not None else None
        rows.append({
            "underlying_move_pct": move_pct,
            "future_spot": round(future_spot, 4),
            "estimated_mark": round(mark, 4) if mark is not None else None,
            "estimated_pnl_pct": round(pnl, 3) if pnl is not None else None,
        })
    return rows


def required_move_for_profit(mark_payload: dict[str, Any], calibration: dict[str, Any], horizon_days: int) -> dict[str, Any]:
    spot = num(mark_payload.get("spot"))
    current_mark = num(mark_payload.get("mark"))
    if not spot or not current_mark:
        return {"available": False, "reason": "missing_spot_or_mark"}
    t_future = max(years_to_expiry(str(mark_payload["expiry"])) - horizon_days / 365.0, 0.0)
    best = None
    for bp in range(-500, 1001, 5):
        move_pct = bp / 100.0
        future_spot = spot * (1 + move_pct / 100.0)
        mark = theoretical_spread(mark_payload, future_spot, t_future, calibration)
        if mark is not None and mark >= current_mark:
            best = {
                "available": True,
                "required_underlying_move_pct": round(move_pct, 3),
                "required_spot": round(future_spot, 4),
                "estimated_mark_at_required_spot": round(mark, 4),
            }
            break
    return best or {"available": False, "reason": "profit_not_reached_in_-5_to_10pct_grid"}


def train_calibration() -> dict[str, Any]:
    """Fit a simple BS-to-market spread multiplier from local captured marks."""
    samples: list[dict[str, Any]] = []
    dbs = [
        DATA / "paper_trades" / "paper_trades.sqlite",
        DATA / "positions" / "position_monitor.sqlite",
    ]
    for db_path in dbs:
        if not db_path.is_file():
            continue
        with sqlite3.connect(db_path) as db:
            tables = {row[0] for row in db.execute("select name from sqlite_master where type='table'")}
            if "paper_marks" in tables:
                for row in db.execute("select payload_json from paper_marks order by captured_at desc limit 100"):
                    payload = json.loads(row[0])
                    lq = payload.get("long_quote") or {}
                    sq = payload.get("short_quote") or {}
                    spot = num(payload.get("spot"))
                    mark = num(payload.get("mark"))
                    expiry = lq.get("expiration_date") or sq.get("expiration_date")
                    option_type = lq.get("option_type") or sq.get("option_type")
                    if not spot or not mark or not expiry or not option_type:
                        continue
                    long_iv = iv_from_quote(lq)
                    short_iv = iv_from_quote(sq)
                    long_strike = num(lq.get("strike"))
                    short_strike = num(sq.get("strike"))
                    if not long_iv or not short_iv or not long_strike or not short_strike:
                        continue
                    t = years_to_expiry(expiry)
                    theo = bs_price(spot, long_strike, t, long_iv, option_type) - bs_price(spot, short_strike, t, short_iv, option_type)
                    if theo > 0:
                        samples.append({"market": mark, "theoretical": theo, "source": str(db_path)})
            if "position_snapshots" in tables:
                for row in db.execute("select payload_json from position_snapshots order by captured_at desc limit 100"):
                    payload = json.loads(row[0])
                    lq = payload.get("long_contract") or {}
                    sq = payload.get("short_contract") or {}
                    spot = num(payload.get("spot"))
                    mark = num(payload.get("spread_mark"))
                    expiry = lq.get("expiration") or lq.get("expiration_date") or sq.get("expiration") or sq.get("expiration_date")
                    option_type = lq.get("type") or lq.get("option_type") or "call"
                    long_iv = num(lq.get("iv")) or iv_from_quote(lq)
                    short_iv = num(sq.get("iv")) or iv_from_quote(sq)
                    long_strike = num(lq.get("strike"))
                    short_strike = num(sq.get("strike"))
                    if not spot or not mark or not expiry or not long_iv or not short_iv or not long_strike or not short_strike:
                        continue
                    t = years_to_expiry(str(expiry)[:10])
                    theo = bs_price(spot, long_strike, t, long_iv, option_type) - bs_price(spot, short_strike, t, short_iv, option_type)
                    if theo > 0:
                        samples.append({"market": mark, "theoretical": theo, "source": str(db_path)})
    ratios = [s["market"] / s["theoretical"] for s in samples if s["theoretical"] > 0 and s["market"] >= 0]
    ratios = [r for r in ratios if 0.1 <= r <= 5.0]
    multiplier = sorted(ratios)[len(ratios) // 2] if ratios else 1.0
    report = {
        "generated_at": now_utc(),
        "spread_multiplier": round(multiplier, 6),
        "iv_multiplier": 1.0,
        "samples": len(samples),
        "usable_ratios": len(ratios),
        "sample_preview": samples[:20],
        "caveat": "Tiny local calibration from captured marks. Needs real historical option ticks before serious training.",
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    CALIBRATION_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def predict_spread(
    ticker: str,
    expiry: str,
    option_type: str,
    long_strike: float,
    short_strike: float,
    *,
    timeframe: str = "5m",
    as_of: str | None = None,
    horizon_days: int = 1,
    use_kronos: bool = True,
) -> dict[str, Any]:
    current = current_spread_mark(ticker, expiry, option_type, long_strike, short_strike)
    calibration = load_calibration()
    forecast = {"available": False, "reason": "disabled"}
    forecast_spot = current.get("spot")
    if use_kronos and as_of:
        try:
            predictor = kronos_research.load_predictor()
            forecast = kronos_research.kronos_forecast_signal(
                predictor,
                ticker,
                as_of,
                timeframe=timeframe,
                horizon_days=horizon_days,
                lookback=400,
                max_pred_bars=78,
                sample_count=1,
            )
            if forecast.get("available") and forecast.get("pred_close"):
                forecast_spot = float(forecast["pred_close"])
        except Exception as exc:  # noqa: BLE001 - optional model path.
            forecast = {"available": False, "reason": str(exc)}
    t_now = years_to_expiry(expiry)
    t_future = max(t_now - horizon_days / 365.0, 0.0)
    current_theoretical = theoretical_spread(current, float(current["spot"]), t_now, calibration) if current.get("spot") else None
    forecast_mark = theoretical_spread(current, float(forecast_spot), t_future, calibration) if forecast_spot else None
    pnl_pct = None
    if current.get("mark") and forecast_mark is not None:
        pnl_pct = (forecast_mark - float(current["mark"])) / float(current["mark"]) * 100.0
    return {
        "generated_at": now_utc(),
        "ticker": ticker.upper(),
        "spread": f"{expiry} {long_strike:g}/{short_strike:g} {option_type} debit spread",
        "current_mark": current.get("mark"),
        "current_spot": current.get("spot"),
        "current_theoretical": round(current_theoretical, 4) if current_theoretical is not None else None,
        "forecast_spot": round(forecast_spot, 4) if forecast_spot is not None else None,
        "forecast_mark": round(forecast_mark, 4) if forecast_mark is not None else None,
        "forecast_pnl_pct_from_current_mark": round(pnl_pct, 3) if pnl_pct is not None else None,
        "required_move_for_profit": required_move_for_profit(current, calibration, horizon_days),
        "scenario_grid": scenario_grid(current, calibration, horizon_days),
        "kronos_forecast": forecast,
        "calibration": calibration,
        "market_payload": current,
        "caveat": "Research estimate: Kronos underlying forecast + Black-Scholes/calibration, not a quoted future option price.",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("train-calibration")
    pred = sub.add_parser("predict-spread")
    pred.add_argument("--ticker", required=True)
    pred.add_argument("--expiry", required=True)
    pred.add_argument("--type", default="call", choices=("call", "put"))
    pred.add_argument("--long-strike", type=float, required=True)
    pred.add_argument("--short-strike", type=float, required=True)
    pred.add_argument("--as-of")
    pred.add_argument("--timeframe", default="5m")
    pred.add_argument("--horizon-days", type=int, default=1)
    pred.add_argument("--no-kronos", action="store_true")
    args = parser.parse_args()
    if args.cmd == "train-calibration":
        print(json.dumps(train_calibration(), indent=2))
        return 0
    report = predict_spread(
        args.ticker,
        args.expiry,
        args.type,
        args.long_strike,
        args.short_strike,
        as_of=args.as_of,
        timeframe=args.timeframe,
        horizon_days=args.horizon_days,
        use_kronos=not args.no_kronos,
    )
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"kronos_option_prediction_{args.ticker.upper()}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"path": str(path), **{k: report[k] for k in ["ticker", "spread", "current_mark", "forecast_mark", "forecast_pnl_pct_from_current_mark"]}}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
