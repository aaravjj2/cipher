from __future__ import annotations

from datetime import datetime, timezone
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import options_terminal  # noqa: E402


def row(kind: str, strike: float, *, iv=.30, delta=None, oi=500, volume=100):
    return {"symbol": f"X-{kind}-{strike}", "type": kind, "strike": strike,
            "expiry": "2026-08-21", "bid": 2.0, "ask": 2.2, "mid": 2.1,
            "quote_time": "2026-08-14T14:00:00Z", "trade_time": None,
            "iv": iv, "delta": delta, "gamma": .02, "theta": -.05,
            "vega": .1, "rho": .01, "open_interest": oi,
            "open_interest_date": "2026-08-13", "volume": volume, "feed": "opra"}


def test_chain_view_pairs_calls_puts_and_preserves_unknown_oi() -> None:
    rows = [row("call", 100, delta=.5), row("put", 100, delta=-.5), row("call", 105, delta=.25), row("put", 95, delta=-.25, oi=None)]
    result = options_terminal.chain_view(
        "X", {"price_context": 100, "as_of": "2026-08-14T14:00:00Z"}, rows,
        now=datetime(2026, 8, 14, 14, 0, 30, tzinfo=timezone.utc),
    )
    assert len(result["expirations"]) == 1
    assert result["expirations"][0]["expected_move"] == 4.2
    put = next(x["put"] for x in result["expirations"][0]["rows"] if x["put"] and x["strike"] == 95)
    assert put["open_interest"] is None
    assert "OI_UNKNOWN" in put["liquidity_flags"]
    assert put["liquid"] is None
    assert result["iv_rank"] is None
    assert result["iv_history_status"] == "UNAVAILABLE_INSUFFICIENT_HISTORY"


def test_vertical_payoff_uses_executable_sides_and_contract_multiplier() -> None:
    legs = [
        {"contract": "C100", "type": "call", "strike": 100, "expiration": "2026-08-21", "side": "buy", "quantity": 1, "bid": 4.8, "ask": 5.0, "delta": .55, "gamma": .02, "theta": -.1, "vega": .2, "rho": .01},
        {"contract": "C110", "type": "call", "strike": 110, "expiration": "2026-08-21", "side": "sell", "quantity": 1, "bid": 1.9, "ask": 2.0, "delta": .25, "gamma": .01, "theta": -.05, "vega": .1, "rho": .005},
    ]
    result = options_terminal.analyze_structure("X", 100, legs)
    assert result["net_debit"] == 310
    assert round(result["max_loss"], 6) == -310
    assert round(result["max_profit"], 6) == 690
    assert result["execution_capability"] is False


def test_calendar_refuses_to_invent_terminal_max_profit() -> None:
    legs = [
        {"type": "call", "strike": 100, "expiration": "2026-08-21", "side": "sell", "quantity": 1, "bid": 2, "ask": 2.1},
        {"type": "call", "strike": 100, "expiration": "2026-09-18", "side": "buy", "quantity": 1, "bid": 4, "ask": 4.2},
    ]
    result = options_terminal.analyze_structure("X", 100, legs)
    assert result["same_expiration"] is False
    assert result["max_profit"] is None
    assert result["max_loss"] is None
    assert result["calendar_caveat"]


def test_covered_call_includes_stock_delta_and_assignment_warning() -> None:
    result = options_terminal.analyze_structure("X", 100, [
        {"type": "stock", "side": "buy", "quantity": 100, "entry_price": 100},
        {"type": "call", "strike": 110, "expiration": "2026-08-21", "side": "sell", "quantity": 1, "bid": 2, "ask": 2.1, "delta": .25, "gamma": .01, "theta": -.05, "vega": .1, "rho": .01},
    ])
    assert result["aggregate_greeks"]["delta"] == 75
    assert result["assignment_warning"] is True
    assert result["max_profit"] == 1200


def test_naked_short_call_has_unbounded_loss() -> None:
    result = options_terminal.analyze_structure("X", 100, [
        {"type": "call", "strike": 110, "expiration": "2026-08-21", "side": "sell", "quantity": 1,
         "bid": 2, "ask": 2.1, "delta": .25, "gamma": .01, "theta": -.05, "vega": .1, "rho": .01,
         "liquidity_flags": ["WIDE_SPREAD"]},
    ])
    assert result["max_loss_unbounded"] is True
    assert result["max_loss"] is None
    assert result["risk_per_structure"] is None
    assert result["liquidity_warnings"] == ["WIDE_SPREAD"]
