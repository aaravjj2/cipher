"""Run every catalogued strategy through one standard and record the verdict.

`core/strategy_catalog.py` reduces each strategy to its entries.
`core/backtest_engine.py` supplies the fills, the costs and the matched random
control. This module joins them and packs the answer into the shape the governance
layer already expects, so that a strategy's verdict lives in the promotion ladder
rather than in terminal scrollback.

The standard is one line: **a strategy must beat a random-entry control matched
trade-for-trade by symbol and direction, clearing the BEST of N random draws
rather than their mean.** Everything else is bookkeeping around that.

`core/research_platform/promotion.py:111` already routes `FAST_BACKTESTED` and
`WALK_FORWARD_PASSED` to an engine it calls `cipher_fast`, and
`experiments.FastGateEvaluator` already refuses to promote when a declared
`required_quality_check` is absent. That slot was empty. This fills it: nothing
new is invented, `beats_control_range` is simply promoted from a field in a JSON
blob to a required check in a state machine that already knows how to refuse.

A blocked strategy is never scored. It returns a verdict of BLOCKED carrying the
reason and, where the blocker is an accrual clock, how far that clock has to go.
Scoring it anyway would let a lookahead-biased number outrank an honest one, which
is the failure this whole arrangement exists to prevent.

Research only. Simulated fills over historical bars; places no orders.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _path in (str(ROOT), str(ROOT / "core")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import backtest_engine as be  # noqa: E402
import strategy_catalog as sc  # noqa: E402

# A control comparison on a handful of trades is not evidence either way. Below
# this the verdict is INSUFFICIENT rather than FAIL: the strategy has not been
# shown to fail, it has not been shown anything.
MIN_TRADES_FOR_VERDICT = 30

DEFAULT_CONTROL_REPEATS = 20


def _clock_note() -> str | None:
    """How far the open-interest clock has to run, for blocked GEX strategies."""
    try:
        import evidence_status
        for clock in evidence_status.status().get("clocks", []):
            if "open interest" in clock.get("name", "").lower():
                return (f"{clock.get('have')} of {clock.get('need')} "
                        f"{clock.get('unit')} ({clock.get('progress_pct')}%)")
    except Exception:
        return None
    return None


def evaluate(
    strategy_id: str,
    bars_by_symbol: dict[str, list[dict]],
    *,
    control_repeats: int = DEFAULT_CONTROL_REPEATS,
    cost_profile: dict | None = None,
    timeframe: str | None = None,
    **engine_kw,
) -> dict:
    """Measure one strategy. Returns a verdict dict, never raises on a bad strategy."""
    spec = sc.get(strategy_id)
    if spec is None:
        return {"strategy_id": strategy_id, "verdict": "UNKNOWN",
                "reason": "not in the catalog"}

    base = {
        "strategy_id": spec.strategy_id,
        "name": spec.name,
        "family": spec.family,
        "source": spec.source,
        "data_requirement": spec.data_requirement,
        "evaluated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    if not spec.evaluable:
        blocked = dict(base)
        blocked.update({
            "verdict": "BLOCKED",
            "reason": spec.blocked_reason,
            "metrics": None,
        })
        clock = _clock_note() if spec.family == "gex" else None
        if clock:
            blocked["accrual"] = clock
        return blocked

    if timeframe and spec.bar_timeframe != timeframe:
        out = dict(base)
        out.update({
            "verdict": "WRONG_TIMEFRAME",
            "reason": (f"written against {spec.bar_timeframe} bars but given "
                       f"{timeframe}; an intraday rule fed daily bars finds nothing, "
                       f"and calling that a null result blames the strategy for the "
                       f"caller's choice"),
            "metrics": None,
            "bar_timeframe": spec.bar_timeframe,
        })
        return out

    result = be.run_signals(
        bars_by_symbol, spec.signal_fn, strategy=spec.strategy_id,
        cost_profile=cost_profile, **engine_kw,
    )
    stats = result.stats or {}
    trades = int(stats.get("trades") or 0)

    errors = list(getattr(spec.signal_fn, "errors", []) or [])
    if trades == 0:
        out = dict(base)
        if errors:
            # A strategy that raised on every symbol produced no trades for a very
            # different reason than one that simply found no setups, and reporting
            # both as NO_TRADES would hide a broken strategy behind a null result.
            out.update({"verdict": "ERROR", "metrics": stats,
                        "reason": f"raised on {len(errors)} symbol(s): {errors[0]}",
                        "errors": errors[:5]})
        else:
            out.update({"verdict": "NO_TRADES", "metrics": stats,
                        "reason": "produced no entries on this universe and timeframe"})
        return out

    control = be.run_control(result, bars_by_symbol, repeats=control_repeats,
                             cost_profile=cost_profile, **engine_kw)
    beats = bool(control.get("detector_beats_control_range"))

    if trades < MIN_TRADES_FOR_VERDICT:
        verdict = "INSUFFICIENT"
        reason = (f"{trades} trades is below the {MIN_TRADES_FOR_VERDICT} needed to "
                  f"tell a result from noise; this is not a failure, it is an "
                  f"absence of evidence")
    elif beats:
        verdict = "PASS"
        reason = "beat a matched random-entry control, clearing the best random draw"
    else:
        verdict = "FAIL"
        reason = "did not beat a random-entry control matched by symbol and direction"

    out = dict(base)
    out.update({
        "verdict": verdict,
        "reason": reason,
        "metrics": stats,
        "control": control.get("control"),
        "beats_control_range": beats,
        "vs_control": control.get("detector_minus_control"),
        "params": result.params,
        "caveat": result.caveat,
    })
    if errors:
        out["errors"] = errors[:5]
    return out


def to_standard_output(verdict: dict, result: be.BacktestResult | None = None):
    """Pack a verdict into the governance layer's StandardBacktestOutput.

    `quality_checks` is the load-bearing part: `FastGateEvaluator` reads the names
    listed in a strategy's `required_quality_checks` straight out of it, so
    declaring `beats_control_range` there is what makes the promotion ladder
    enforce the standard rather than merely record it.
    """
    from research_platform.experiments import StandardBacktestOutput

    metrics = dict(verdict.get("metrics") or {})
    normalized = {
        "trade_count": metrics.get("trades", 0),
        # The registry works in fractions; the engine reports percentages.
        "win_rate": (metrics.get("win_rate") or 0) / 100.0,
        "profit_factor": metrics.get("profit_factor"),
        "mean_trade_return_pct": metrics.get("avg_return_pct"),
        "maximum_drawdown_pct": abs(metrics.get("max_drawdown_pct") or 0.0),
    }
    passed = verdict.get("verdict") == "PASS"
    quality = {
        "passed": passed,
        "control_matched": verdict.get("control") is not None,
        "beats_control_range": bool(verdict.get("beats_control_range")),
        "cost_charged_both_sides": True,
        "sufficient_sample": metrics.get("trades", 0) >= MIN_TRADES_FOR_VERDICT,
    }
    trades = tuple()
    if result is not None:
        from research_platform.experiments import TradeRecord
        trades = tuple(
            TradeRecord(
                trade_id=f"{verdict['strategy_id']}:{i}", symbol=t.symbol,
                direction=t.direction, entry_time=t.entry_time,
                exit_time=t.exit_time, entry_price=t.entry_price,
                exit_price=t.exit_price, quantity=1.0,
                gross_pnl=None, net_pnl=None, return_pct=t.return_pct,
                metadata={"setup": t.setup, "exit_reason": t.exit_reason},
            )
            for i, t in enumerate(result.trades)
        )

    return StandardBacktestOutput(
        trades=trades,
        equity_curve=tuple(),
        metrics=normalized,
        benchmark_metrics={"control": verdict.get("control") or {}},
        regime_metrics={},
        statistical_tests={"matched_random_control": verdict.get("vs_control") or {}},
        quality_checks=quality,
        exclusions=tuple(),
        assumptions={
            "fills": "next bar open",
            "intrabar": "stop assumed before target",
            "cost": "charged both sides, per-symbol measured half-spread where available",
            "control": "random entries matched trade-for-trade by symbol and direction",
        },
        notes=(verdict.get("reason") or "", verdict.get("caveat") or ""),
    )


def evaluate_all(
    bars_by_symbol: dict[str, list[dict]],
    *,
    strategy_ids: list[str] | None = None,
    control_repeats: int = DEFAULT_CONTROL_REPEATS,
    cost_profile: dict | None = None,
    timeframe: str | None = None,
    progress=None,
    **engine_kw,
) -> dict:
    """Evaluate the whole catalog (or a subset) and summarise the outcome."""
    ids = strategy_ids or list(sc.CATALOG)
    results = []
    for index, strategy_id in enumerate(ids):
        if progress:
            progress(index, len(ids), strategy_id)
        results.append(evaluate(
            strategy_id, bars_by_symbol, control_repeats=control_repeats,
            cost_profile=cost_profile, timeframe=timeframe, **engine_kw,
        ))

    tally: dict[str, int] = {}
    for row in results:
        tally[row["verdict"]] = tally.get(row["verdict"], 0) + 1

    return {
        "evaluated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "symbols": sorted(bars_by_symbol),
        "timeframe": timeframe,
        "verdicts": tally,
        "results": results,
        "standard": (
            "A strategy passes only by beating a random-entry control matched "
            "trade-for-trade by symbol and direction, clearing the best of "
            f"{control_repeats} random draws rather than their mean. Costs are "
            "charged on both sides. Strategies whose data cannot support an honest "
            "measurement are reported BLOCKED and are never given a number."
        ),
    }
