"""Translate pooled per-trade percentages into a return, and test its concentration.

Pooling sums the per-trade percentages of every symbol. That is a reasonable way to measure a
signal and a misleading way to state a return: with ten symbols it reads about ten times larger
than trading them equally weighted would have produced. On the crowned 1-minute candidate the
pooled holdout figure was +6.999% where equal-weight compounding gives +0.735% -- and annualized
that is +3.84%, below the 4% risk-free rate the same capital earns sitting still.

Kept in `core/` rather than inside one report script so the sweep and the deep dive state the
same numbers, computed once.

Research-only. Not trading advice.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

RISK_FREE_PCT = 4.0


def _compound_pct(returns: list[float]) -> float:
    """Compound a sequence of per-trade percentage returns into one percentage."""
    equity = 1.0
    for value in returns:
        equity *= 1.0 + value / 100.0
    return (equity - 1.0) * 100.0


def _holdout_economics(
    trades: list[dict[str, Any]],
    start: date,
    end: date,
    *,
    universe: list[str] | tuple[str, ...] | None = None,
    risk_free_pct: float = RISK_FREE_PCT,
) -> dict[str, Any]:
    """Translate pooled per-trade percentages into a return a reader can act on.

    Pooling sums the per-trade percentages of every symbol, which is useful for measuring a
    signal and misleading as a return: with ten symbols it reads about ten times larger than
    trading them equally weighted would have produced. Compounding per symbol and then
    averaging is the honest translation, and annualizing it against the risk-free rate is the
    only comparison that answers whether the strategy is worth running rather than holding
    cash.

    `leave_one_out` re-weights the remaining symbols after dropping each one. It is a
    concentration diagnostic, not a robustness claim: if the result only clears zero with one
    particular name included, the edge is that name's, not the strategy's.
    """
    by_symbol: dict[str, list[float]] = defaultdict(list)
    for trade in trades:
        by_symbol[str(trade["symbol"])].append(float(trade["net_return_pct"]))
    symbols = tuple(dict.fromkeys(universe or tuple(sorted(by_symbol))))
    # A symbol in the declared universe that generated no trade still consumed
    # an equal-weight capital sleeve; its return is 0%, not "missing". Omitting
    # zero-trade symbols makes sparse widened-universe runs look better than the
    # portfolio they claim to represent.
    compounded = {symbol: _compound_pct(by_symbol.get(symbol, [])) for symbol in symbols}
    days = max((end - start).days, 1)
    years = days / 365.0

    def annualize(pct: float) -> float:
        base = 1.0 + pct / 100.0
        return -100.0 if base <= 0 else (base ** (1.0 / years) - 1.0) * 100.0

    equal_weight = sum(compounded.values()) / len(compounded) if compounded else 0.0
    leave_one_out = []
    for symbol in compounded:
        rest = [value for name, value in compounded.items() if name != symbol]
        rest_ew = sum(rest) / len(rest) if rest else 0.0
        rest_ann = annualize(rest_ew)
        leave_one_out.append({
            "symbol": symbol,
            "equal_weight_pct": round(rest_ew, 6),
            "annualized_pct": round(rest_ann, 6),
            "clears_hurdle": rest_ann > risk_free_pct,
        })
    leave_one_out.sort(key=lambda row: row["annualized_pct"])

    pooled = sum(value for rows in by_symbol.values() for value in rows)
    pre_cost = sum(t["gross_return_pct"] for t in trades)
    annualized = annualize(equal_weight)
    return {
        "symbols": len(compounded),
        "symbols_with_trades": sum(bool(by_symbol.get(symbol)) for symbol in compounded),
        "days": days,
        "pooled_sum_pct": round(pooled, 6),
        "pre_cost_sum_pct": round(pre_cost, 6),
        "slippage_drag_pct": round(pre_cost - pooled, 6),
        "slippage_share_of_pre_cost_pct": (
            round((pre_cost - pooled) / pre_cost * 100.0, 3) if pre_cost else None
        ),
        "equal_weight_pct": round(equal_weight, 6),
        "annualized_pct": round(annualized, 6),
        "risk_free_pct": float(risk_free_pct),
        "excess_vs_risk_free_pp": round(annualized - risk_free_pct, 6),
        "beats_risk_free": annualized > risk_free_pct,
        # Reported so the gap between the pooled headline and the honest figure is explicit
        # rather than something a reader has to notice.
        "overstatement_ratio": round(pooled / equal_weight, 3) if equal_weight else None,
        "positive_symbols": sum(1 for value in compounded.values() if value > 0),
        "per_symbol_compounded_pct": {k: round(v, 6) for k, v in sorted(compounded.items())},
        "leave_one_out": leave_one_out,
    }
