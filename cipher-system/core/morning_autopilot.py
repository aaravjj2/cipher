"""Morning research autopilot built from current or locally captured option data."""
from __future__ import annotations

import json
from datetime import datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from core.copilot import tools
from core.copilot.rating_engine import rate_ticker


STATE_FILE = Path(tools.RUNTIME_DATA) / "autopilot" / "morning_state.json"


def scan_universe() -> list[str]:
    return [
        "SPY", "QQQ", "NVDA", "MSFT", "AAPL", "AMZN", "AVGO", "AMD",
        "TSLA", "META", "GOOGL", "MU", "AMKR", "RMBS", "MRVL", "COST",
    ]


def filter_liquid_options(tickers: list[str]) -> list[str]:
    """Use local captures first, so a live API outage does not erase the universe."""
    liquid: list[str] = []
    for ticker in tickers:
        snapshot = tools.latest_capture(ticker)
        contracts = (snapshot or {}).get("contracts") or []
        if not contracts:
            continue
        oi_rows = [row for row in contracts if row.get("open_interest") is not None]
        oi_coverage = 100 * len(oi_rows) / len(contracts)
        quoted_rows = [
            row for row in contracts
            if _positive_number(row.get("bid")) is not None
            and _positive_number(row.get("ask")) is not None
            and float(row["ask"]) >= float(row["bid"])
        ]
        quote_coverage = 100 * len(quoted_rows) / len(contracts)
        has_oi_liquidity = oi_coverage >= 50 and sum(float(row.get("open_interest") or 0) for row in oi_rows) > 0
        has_traded_quotes = quote_coverage >= 50 and sum(float(row.get("volume") or 0) for row in quoted_rows) > 0
        if has_oi_liquidity or has_traded_quotes:
            liquid.append(ticker.upper())
    return liquid[:20]


def _positive_number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def score_trade_opportunity(ticker: str) -> dict | None:
    rating_result = rate_ticker(ticker)
    rating = _positive_number(rating_result.get("rating"))
    confidence = _positive_number(rating_result.get("confidence"))
    if rating is None or confidence is None or rating < 7:
        return None
    strategy = str(rating_result.get("option_strategy") or "none")
    if strategy.lower() == "none":
        return None

    quote = tools.dispatch("get_quote", {"ticker": ticker})
    if quote.get("error"):
        return None
    spot = _positive_number(quote.get("price_context") or quote.get("mid") or quote.get("last"))
    if spot is None:
        return None

    technicals = tools.dispatch("get_technicals", {"ticker": ticker})
    supports = [_positive_number(value) for value in technicals.get("supports") or []]
    resistances = [_positive_number(value) for value in technicals.get("resistances") or []]
    supports = [value for value in supports if value is not None]
    resistances = [value for value in resistances if value is not None]
    return {
        "ticker": ticker.upper(),
        "rating": rating,
        "confidence": confidence,
        "strategy": strategy,
        "thesis": rating_result.get("current_thesis"),
        "entry_zone": f"{spot * 0.995:.2f} - {spot * 1.005:.2f}",
        "spot": spot,
        "stop_loss": min(supports) * 0.98 if supports else spot * 0.95,
        "target_1": max(resistances) * 1.02 if resistances else spot * 1.08,
        "target_2": spot * 1.15,
        "expiration_preference": "2-6 weeks",
        "delta_target": "0.30-0.50",
        "position_size_pct": "1-2% of portfolio",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def run_morning_autopilot() -> dict:
    tickers = scan_universe()
    liquid = filter_liquid_options(tickers)
    scored = [trade for ticker in liquid if (trade := score_trade_opportunity(ticker))]
    scored.sort(key=lambda row: row["rating"] * row["confidence"], reverse=True)
    result = {
        "date": datetime.now(timezone.utc).date().isoformat(),
        "scanned": len(tickers),
        "liquid": len(liquid),
        "scored": len(scored),
        "top_trades": scored[:3],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(result, indent=2), encoding="utf-8")
    temporary.replace(STATE_FILE)
    return result


def format_discord_message(result: dict) -> str:
    lines = [f"**Morning Autopilot — {result['date']}**", ""]
    for index, trade in enumerate(result["top_trades"], 1):
        lines.extend([
            f"**#{index} {trade['ticker']} — Rating {trade['rating']:g}/10 (Confidence {trade['confidence']:g}/5)**",
            f"  Strategy: {trade['strategy']}",
            f"  Thesis: {trade['thesis']}",
            f"  Entry: {trade['entry_zone']} | SL: {trade['stop_loss']:.2f} | T1: {trade['target_1']:.2f} | T2: {trade['target_2']:.2f}",
            f"  Size: {trade['position_size_pct']} | Exp: {trade['expiration_preference']} | Delta: {trade['delta_target']}",
            "",
        ])
    lines.append(f"_Scanned {result['scanned']} | Liquid {result['liquid']} | Scored {result['scored']}_")
    return "\n".join(lines)


if __name__ == "__main__":
    now = datetime.now(ZoneInfo("America/New_York"))
    if now.weekday() >= 5:
        print("Weekend - skipping")
    elif not time(9, 30) <= now.time() <= time(11, 0):
        print("Outside morning window (9:30-11:00 ET)")
    else:
        print(json.dumps(run_morning_autopilot(), indent=2))
