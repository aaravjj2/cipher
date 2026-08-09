"""One register of every strategy, and what each one is allowed to claim.

Thirty-one named strategies live in four modules, and each module carries its own
simulate-and-score half. Those halves disagree with `core/backtest_engine.py` on
the things that decide a verdict:

  * `price_backtest`, `edge_backtest` and `intraday_backtest` charge **no
    transaction cost at all** — 0 references to cost, slippage, commission or fees
    between them, against 12 in the engine. Every Sharpe and expectancy they
    report is gross, and this repository's own findings turn on a 1-2bp difference.
  * `edge_backtest` truncated chronologically. Two strategies hardcoded a break at
    five trades regardless of their own `max_trades`, so their "sample" was the
    five earliest signals in the history.
  * `strategy_backtest` fetches **today's** GEX matrix (`:836`) and trades it over
    60 days of past bars (`:869`). Every one of its eight strategies uses open
    interest that did not exist on the trade dates.

So the entry logic is kept and the measurement is replaced. Each adapter reduces a
legacy strategy to a `signal_fn` — the bars it would have entered on, and in which
direction — and `backtest_engine.run_signals` supplies the fills, the costs, the
matched random control and the verdict.

`data_requirement` is the other half of the register. A strategy that cannot be
honestly measured with the data on disk is not scored badly; it is not scored at
all, and it carries the reason. The alternative is a leaderboard where a
lookahead-biased number outranks an honest one.

Research only. Nothing here places orders.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

# Large enough to mean "no truncation" for any real history, while still bounding
# a runaway strategy. Daily bars over ten years are ~2500, so a strategy firing
# every other bar cannot reach this.
NO_TRUNCATION = 100_000


@dataclass(frozen=True)
class StrategySpec:
    strategy_id: str
    name: str
    family: str
    source: str
    signal_fn: Callable | None
    data_requirement: str
    blocked_reason: str | None = None
    # The bar size this strategy was written against. Feeding an intraday rule
    # daily bars produces zero entries, and reporting that as "found no setups"
    # blames the strategy for the caller's mistake.
    bar_timeframe: str = "1Day"

    @property
    def evaluable(self) -> bool:
        return self.blocked_reason is None and self.signal_fn is not None


def _day_index(bars: list[dict]) -> dict[str, int]:
    """Map a calendar day to its first bar index.

    Legacy trades record `entry_day`, taken from `bars[i+1]["time"][:10]`, because
    they fill on the next bar's open. Recovering the signal bar means finding that
    day and stepping back one.
    """
    out: dict[str, int] = {}
    for index, bar in enumerate(bars):
        day = str(bar.get("time", ""))[:10]
        if day and day not in out:
            out[day] = index
    return out


def _adapt(fn, tag: str, **kwargs):
    """Turn a legacy `fn(ticker, bars) -> [PriceTrade]` into a signal function.

    Only the entry decision survives: which bar, which direction. The legacy
    object's own target, stop, hold limit and simulated result are discarded,
    because comparing strategies that each brought their own exit rule measures
    the exit rules as much as the entries. `run_signals` applies one rule to all
    of them, and the matched control then faces that same rule.
    """
    def signal_fn(symbol: str, bars: list[dict]):
        try:
            trades = fn(symbol, bars, max_trades=NO_TRUNCATION, **kwargs)
        except TypeError:
            # Not every legacy strategy accepts max_trades; those never truncated.
            # This retry is deliberately inside its own try: an exception raised
            # while handling the TypeError would otherwise escape every guard
            # below and abort the whole sweep, which is exactly what a NameError
            # in one strategy did.
            try:
                trades = fn(symbol, bars, **kwargs)
            except Exception as exc:  # noqa: BLE001
                signal_fn.errors.append(f"{symbol}: {type(exc).__name__}: {exc}")
                return []
        except Exception as exc:  # noqa: BLE001
            # A strategy that throws on one symbol must not lose the whole sweep,
            # but it must not look like a strategy that simply found no setups
            # either. The error is recorded so the evaluation can report NO_TRADES
            # and ERROR as the different things they are.
            signal_fn.errors.append(f"{symbol}: {type(exc).__name__}: {exc}")
            return []

        days = _day_index(bars)
        signals = []
        for trade in trades or []:
            entry_day = getattr(trade, "entry_day", "") or ""
            fill_index = days.get(str(entry_day)[:10])
            if fill_index is None or fill_index < 1:
                continue
            direction = str(getattr(trade, "direction", "") or "").upper()
            if direction in ("SHORT", "SELL"):
                direction = "SHORT"
            elif direction in ("LONG", "BUY"):
                direction = "LONG"
            else:
                continue
            # The legacy trade records the bar it FILLED on; run_signals expects
            # the bar the signal was evaluated on and fills on the next open
            # itself. Passing the fill bar through unchanged would shift every
            # entry one bar late.
            signals.append((fill_index - 1, direction, tag))
        return signals

    # Errors accumulate on the function so a caller can distinguish "found no
    # setups" from "threw on every symbol"; both otherwise return an empty list.
    signal_fn.errors = []
    return signal_fn


# Options priced by a Black-Scholes model at a flat iv=0.25 (`price_backtest.bs_price`)
# rather than from a quoted premium. A model price at a constant volatility cannot
# answer a question about volatility, which is what all six of these structures are.
_MODEL_PRICED = (
    "option structure priced by model at a flat iv=0.25, not from quoted premium; "
    "a constant-volatility model cannot answer a volatility question. Needs the "
    "captured option marks in data/tradier_stream.sqlite to be joined per contract."
)

_LOOKAHEAD_GEX = (
    "needs point-in-time open interest. core/strategy_backtest.py reads TODAY's GEX "
    "matrix and trades it over past bars, so its results are lookahead by "
    "construction. GEX capture stands at 12 of the 60 days the evidence gate asks "
    "for, and a missed session cannot be back-filled from any vendor."
)


def _build() -> dict[str, StrategySpec]:
    specs: dict[str, StrategySpec] = {}

    def add(strategy_id, name, family, source, signal_fn, requirement,
            blocked=None, timeframe="1Day"):
        specs[strategy_id] = StrategySpec(
            strategy_id=strategy_id, name=name, family=family, source=source,
            signal_fn=signal_fn, data_requirement=requirement,
            blocked_reason=blocked, bar_timeframe=timeframe,
        )

    try:
        from . import edge_backtest, intraday_backtest, price_backtest
    except ImportError:
        import edge_backtest
        import intraday_backtest
        import price_backtest

    edge_names = [
        "vol_risk_premium", "overnight_harvest", "vol_mean_reversion",
        "skew_harvest", "pead_drift", "weekend_theta", "vol_regime_switch",
        "momentum_vol_filter", "iv_rv_spread", "gap_and_go", "rsi2_reversion",
        "breakout_20d", "trend_pullback", "three_day_reversal", "bollinger_squeeze",
    ]
    for name in edge_names:
        fn = getattr(edge_backtest, f"strategy_{name}", None)
        if fn is None:
            continue
        add(f"edge.{name}", name, "edge", "core/edge_backtest.py",
            _adapt(fn, name), "daily bars")

    # Six of these are option structures and cannot be measured on bars; five are
    # ordinary directional rules and can.
    price_structures = {"long_straddle", "long_strangle", "iron_condor",
                        "covered_call", "bull_call_spread", "bear_put_spread"}
    price_names = [
        "long_straddle", "long_strangle", "iron_condor", "covered_call",
        "bull_call_spread", "bear_put_spread", "momentum_long", "mean_reversion",
        "bollinger_squeeze", "gap_fill", "trend_follow",
    ]
    for name in price_names:
        fn = getattr(price_backtest, f"strategy_{name}", None)
        if fn is None:
            continue
        structure = name in price_structures
        add(f"price.{name}", name, "price", "core/price_backtest.py",
            None if structure else _adapt(fn, name),
            "option marks" if structure else "daily bars",
            blocked=_MODEL_PRICED if structure else None)

    for name in ["orb_15min", "vwap_momentum", "intraday_rsi_momentum",
                 "momentum_ignition", "pullback_to_vwap"]:
        fn = getattr(intraday_backtest, f"strategy_{name}", None)
        if fn is None:
            continue
        add(f"intraday.{name}", name, "intraday", "core/intraday_backtest.py",
            _adapt(fn, name), "intraday bars", timeframe="15Min")

    for name in ["wall_bounce", "gamma_squeeze", "vacuum_breakout",
                 "divergence_reversal", "gex_momentum", "cluster_magnet",
                 "term_aligned", "flow_confluence"]:
        add(f"gex.{name}", name, "gex", "core/strategy_backtest.py",
            None, "point-in-time open interest", blocked=_LOOKAHEAD_GEX)

    # The one strategy already measured to this standard, kept in the register so
    # the catalog is the whole picture rather than the unmeasured part of it.
    add("obsidian.eod", "Obsidian EOD detector", "obsidian", "core/obsidian_eod.py",
        None, "bars",
        blocked="measured separately via /api/signal-backtest; see docs/backtest-findings.md")

    return specs


CATALOG: dict[str, StrategySpec] = _build()


def get(strategy_id: str) -> StrategySpec | None:
    return CATALOG.get(strategy_id)


def by_timeframe() -> dict[str, list[StrategySpec]]:
    """Evaluable strategies grouped by the bar size they need."""
    out: dict[str, list[StrategySpec]] = {}
    for spec in evaluable():
        out.setdefault(spec.bar_timeframe, []).append(spec)
    return out


def evaluable() -> list[StrategySpec]:
    return [s for s in CATALOG.values() if s.evaluable]


def blocked() -> list[StrategySpec]:
    return [s for s in CATALOG.values() if not s.evaluable]


def summary() -> dict:
    families: dict[str, dict] = {}
    for spec in CATALOG.values():
        bucket = families.setdefault(spec.family, {"total": 0, "evaluable": 0})
        bucket["total"] += 1
        bucket["evaluable"] += 1 if spec.evaluable else 0
    return {
        "total": len(CATALOG),
        "evaluable": len(evaluable()),
        "blocked": len(blocked()),
        "families": families,
    }
