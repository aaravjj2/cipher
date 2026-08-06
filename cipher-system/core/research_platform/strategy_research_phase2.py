"""Phase-two strategy families for Cipher's autonomous research loop.

The original loop intentionally started with six simple, long-only price
families.  This module broadens the hypothesis space without changing the
research boundary: all signals are formed from canonical close-of-session data,
all positions begin at the next session open, and no result can promote or
execute a strategy.

Phase two adds both single-symbol and panel-level strategies:

* EMA trend and time-series momentum
* Keltner breakouts and volatility-regime switching
* multi-signal ensembles and short-term reversal
* cross-sectional momentum/reversal, both long-only and market-neutral
* risk-on rotation and low-volatility selection
* fixed-pair z-score mean reversion
"""
from __future__ import annotations

import math
from dataclasses import replace
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from . import strategy_research_loop as base
from .hashing import stable_id
from .experiments import EquityPoint, StandardBacktestOutput, TradeRecord


PHASE2_FAMILIES = frozenset(
    {
        "ema_trend",
        "time_series_momentum",
        "keltner_breakout",
        "regime_switch",
        "ensemble_vote",
        "short_term_reversal",
        "cross_sectional_momentum",
        "cross_sectional_reversal",
        "risk_on_rotation",
        "cross_sectional_low_vol",
        "pair_zscore",
    }
)

PANEL_FAMILIES = frozenset(
    {
        "cross_sectional_momentum",
        "cross_sectional_reversal",
        "risk_on_rotation",
        "cross_sectional_low_vol",
        "pair_zscore",
    }
)


def is_phase2_family(family: str) -> bool:
    return family in PHASE2_FAMILIES


def phase2_candidate_catalog() -> tuple[base.StrategyCandidate, ...]:
    """Return deterministic phase-two seed hypotheses.

    The catalog is fixed in source and outcome-independent.  Adaptive children
    may later vary these parameters inside guarded bounds.
    """

    candidates: list[base.StrategyCandidate] = []

    def add(family: str, parameters: Mapping[str, Any], hypothesis: str) -> None:
        candidates.append(base.StrategyCandidate(family, parameters, hypothesis=hypothesis))

    for fast, slow, confirmation in (
        (5, 40, 0),
        (10, 60, 0),
        (12, 100, 50),
        (20, 150, 100),
    ):
        add(
            "ema_trend",
            {"fast": fast, "slow": slow, "confirmation": confirmation},
            "Exponentially weighted trend estimates may react faster than simple moving averages while retaining a long-horizon confirmation filter.",
        )

    for lookback, entry_threshold, exit_threshold, trend_filter in (
        (20, 0.02, 0.00, 0),
        (60, 0.04, 0.00, 100),
        (120, 0.06, 0.01, 200),
        (252, 0.08, 0.02, 200),
    ):
        add(
            "time_series_momentum",
            {
                "lookback": lookback,
                "entry_threshold": entry_threshold,
                "exit_threshold": exit_threshold,
                "trend_filter": trend_filter,
            },
            "Positive absolute momentum may persist when the signal is required to clear a minimum return threshold.",
        )

    for ema_window, atr_window, entry_multiple, exit_multiple in (
        (20, 10, 1.5, 0.0),
        (20, 14, 2.0, 0.0),
        (40, 14, 1.5, 0.5),
        (60, 20, 2.0, 0.5),
    ):
        add(
            "keltner_breakout",
            {
                "ema_window": ema_window,
                "atr_window": atr_window,
                "entry_multiple": entry_multiple,
                "exit_multiple": exit_multiple,
            },
            "ATR-scaled channel breakouts may normalize entry thresholds across securities and volatility regimes.",
        )

    for trend_window, vol_window, vol_quantile, rsi_period, rsi_entry in (
        (100, 20, 0.50, 2, 10),
        (150, 20, 0.60, 5, 20),
        (200, 40, 0.50, 5, 25),
        (200, 60, 0.65, 7, 30),
    ):
        add(
            "regime_switch",
            {
                "trend_window": trend_window,
                "vol_window": vol_window,
                "vol_quantile": vol_quantile,
                "rsi_period": rsi_period,
                "rsi_entry": rsi_entry,
                "rsi_exit": 55,
            },
            "Trend following in quieter regimes and oversold reversion in volatile regimes may outperform either rule used unconditionally.",
        )

    for fast, slow, breakout, rsi_period, rsi_entry, minimum_votes in (
        (10, 50, 20, 2, 10, 2),
        (20, 100, 40, 5, 20, 2),
        (20, 150, 55, 5, 25, 2),
        (50, 200, 80, 7, 30, 2),
    ):
        add(
            "ensemble_vote",
            {
                "fast": fast,
                "slow": slow,
                "breakout": breakout,
                "rsi_period": rsi_period,
                "rsi_entry": rsi_entry,
                "minimum_votes": minimum_votes,
                "exit_votes": 0,
            },
            "Combining trend, breakout, and reversion evidence may reduce dependence on one fragile signal family.",
        )

    for lookback, drop_threshold, trend_filter, exit_rebound in (
        (2, 0.025, 0, 0.005),
        (3, 0.040, 100, 0.010),
        (5, 0.060, 150, 0.015),
        (7, 0.080, 200, 0.020),
    ):
        add(
            "short_term_reversal",
            {
                "lookback": lookback,
                "drop_threshold": drop_threshold,
                "trend_filter": trend_filter,
                "exit_rebound": exit_rebound,
            },
            "Sharp multi-session declines may partially reverse, particularly when the longer trend remains intact.",
        )

    for lookback, skip, top_k, rebalance, long_short in (
        (20, 0, 2, 5, False),
        (60, 5, 2, 10, False),
        (120, 20, 3, 20, False),
        (20, 0, 2, 5, True),
        (60, 5, 2, 10, True),
        (120, 20, 3, 20, True),
    ):
        add(
            "cross_sectional_momentum",
            {
                "lookback": lookback,
                "skip": skip,
                "top_k": top_k,
                "rebalance": rebalance,
                "long_short": long_short,
                "minimum_momentum": 0.0,
            },
            "Relative winners may continue to outperform relative losers after a delayed, next-open rebalance.",
        )

    for lookback, top_k, rebalance, long_short in (
        (2, 2, 2, False),
        (5, 2, 5, False),
        (2, 2, 2, True),
        (10, 3, 5, True),
    ):
        add(
            "cross_sectional_reversal",
            {
                "lookback": lookback,
                "top_k": top_k,
                "rebalance": rebalance,
                "long_short": long_short,
            },
            "Extreme relative underperformance may mean-revert against recent winners over short horizons.",
        )

    for lookback, regime_window, top_k, rebalance, defensive in (
        (20, 100, 1, 5, "cash"),
        (60, 150, 2, 10, "spy"),
        (120, 200, 2, 20, "cash"),
        (252, 200, 3, 20, "spy"),
    ):
        add(
            "risk_on_rotation",
            {
                "lookback": lookback,
                "regime_window": regime_window,
                "top_k": top_k,
                "rebalance": rebalance,
                "defensive": defensive,
            },
            "Rotating into the strongest risk assets only during a positive SPY regime may reduce drawdowns without using future information.",
        )

    for momentum_window, vol_window, top_k, rebalance in (
        (20, 20, 2, 5),
        (60, 20, 2, 10),
        (120, 40, 3, 20),
        (252, 60, 3, 20),
    ):
        add(
            "cross_sectional_low_vol",
            {
                "momentum_window": momentum_window,
                "vol_window": vol_window,
                "top_k": top_k,
                "rebalance": rebalance,
            },
            "Among assets with positive momentum, lower realized volatility may identify more stable risk-adjusted leadership.",
        )

    for left, right, window, entry_z, exit_z in (
        ("QQQ", "SPY", 20, 1.5, 0.25),
        ("QQQ", "SPY", 60, 2.0, 0.50),
        ("IWM", "SPY", 20, 1.5, 0.25),
        ("IWM", "SPY", 60, 2.0, 0.50),
        ("XLE", "XLF", 20, 1.5, 0.25),
        ("XLE", "XLF", 60, 2.0, 0.50),
    ):
        add(
            "pair_zscore",
            {"left": left, "right": right, "window": window, "entry_z": entry_z, "exit_z": exit_z},
            "A fixed economic pair may mean-revert after an unusually large log-price-ratio deviation.",
        )

    unique = {candidate.candidate_id: candidate for candidate in candidates}
    return tuple(sorted(unique.values(), key=lambda item: (item.family, item.candidate_id)))


def phase2_strategy_metadata(candidate: base.StrategyCandidate) -> dict[str, Any]:
    params = candidate.parameters
    if candidate.family in {"cross_sectional_momentum", "cross_sectional_reversal"} and bool(params.get("long_short")):
        return {
            "direction": "long_short_market_neutral",
            "long_only": False,
            "benchmark": "cash_zero_return",
            "allocation": "cross_sectional_equal_gross_weights",
            "scope": "panel",
        }
    if candidate.family == "pair_zscore":
        return {
            "direction": "long_short_pair",
            "long_only": False,
            "benchmark": "cash_zero_return",
            "allocation": "fixed_pair_equal_gross_weights",
            "scope": "panel",
        }
    if candidate.family in PANEL_FAMILIES:
        return {
            "direction": "long_only_rotation",
            "long_only": True,
            "benchmark": "SPY_open_to_open" if candidate.family == "risk_on_rotation" else "equal_weight_panel_open_to_open",
            "allocation": "panel_rotation_equal_weights",
            "scope": "panel",
        }
    return {
        "direction": "long_only",
        "long_only": True,
        "benchmark": "SPY_open_to_open",
        "allocation": "equal_weight_across_symbols",
        "scope": "per_symbol",
    }


def run_phase2_candidate_backtest(
    panel: base.CanonicalPanel,
    candidate: base.StrategyCandidate,
    policy: base.StrategyResearchPolicy,
) -> base.CandidateBacktest:
    dates = pd.DatetimeIndex(sorted(pd.Timestamp(value) for value in panel.frame["date"].unique()))
    window = base.evaluation_window(panel, dates)
    scored_dates = dates if window is None else dates[(dates >= window[1]) & (dates <= window[2])]
    if len(scored_dates) < policy.minimum_sessions:
        raise RuntimeError(f"evaluation dataset has {len(scored_dates)} sessions; {policy.minimum_sessions} required")
    if candidate.family in PANEL_FAMILIES:
        simulation = _run_panel_strategy(panel, candidate, policy.slippage_bps_per_side)
    else:
        simulation = _run_single_symbol_strategy(panel, candidate, policy.slippage_bps_per_side)
    return _finalize(panel, candidate, policy, simulation)


def _run_single_symbol_strategy(
    panel: base.CanonicalPanel,
    candidate: base.StrategyCandidate,
    slippage_bps: float,
) -> dict[str, Any]:
    daily = panel.frame
    trades: list[TradeRecord] = []
    strategy_returns: dict[str, pd.Series] = {}
    benchmark_returns: dict[str, pd.Series] = {}
    exclusions: list[dict[str, Any]] = []
    for symbol, group in daily.groupby("ticker", sort=True):
        bars = group.sort_values("date").set_index("date")
        signal = _single_symbol_signal(bars, candidate)
        simulation_bars, simulation_signal = base.slice_signal_for_evaluation(panel, bars, signal)
        result = base._simulate_symbol(symbol, simulation_bars, simulation_signal, slippage_bps)
        trades.extend(result["trades"])
        strategy_returns[symbol] = result["daily_returns"]
        benchmark_returns[symbol] = result["benchmark_returns"]
    frame = pd.concat(strategy_returns, axis=1).sort_index().fillna(0.0)
    benchmarks = pd.concat(benchmark_returns, axis=1).sort_index().fillna(0.0)
    benchmark = benchmarks["SPY"] if "SPY" in benchmarks.columns else benchmarks.mean(axis=1)
    return {
        "trades": trades,
        "portfolio_returns": frame.mean(axis=1),
        "benchmark_returns": benchmark,
        "benchmark_name": "SPY_open_to_open" if "SPY" in benchmarks.columns else "equal_weight_panel_open_to_open",
        "exclusions": exclusions,
        "allocation": "equal_weight_across_symbols",
        "market_neutral": False,
    }


def _single_symbol_signal(bars: pd.DataFrame, candidate: base.StrategyCandidate) -> pd.Series:
    close = bars["close"].astype(float)
    high = bars["high"].astype(float)
    params = candidate.parameters
    family = candidate.family

    if family == "ema_trend":
        fast = close.ewm(span=int(params["fast"]), adjust=False, min_periods=int(params["fast"])).mean()
        slow = close.ewm(span=int(params["slow"]), adjust=False, min_periods=int(params["slow"])).mean()
        desired = fast > slow
        confirmation = int(params.get("confirmation") or 0)
        if confirmation:
            desired &= close > close.rolling(confirmation, min_periods=confirmation).mean()
        return desired.fillna(False)

    if family == "time_series_momentum":
        lookback = int(params["lookback"])
        momentum = close.pct_change(lookback)
        trend_filter = int(params.get("trend_filter") or 0)
        trend_ok = pd.Series(True, index=close.index)
        if trend_filter:
            trend_ok = close > close.rolling(trend_filter, min_periods=trend_filter).mean()
        return base._stateful_signal(
            (momentum > float(params["entry_threshold"])) & trend_ok,
            momentum < float(params["exit_threshold"]),
        )

    if family == "keltner_breakout":
        ema_window = int(params["ema_window"])
        centre = close.ewm(span=ema_window, adjust=False, min_periods=ema_window).mean()
        atr = base._atr(bars, int(params["atr_window"]))
        upper = centre + float(params["entry_multiple"]) * atr
        exit_line = centre - float(params["exit_multiple"]) * atr
        return base._stateful_signal(close > upper, close < exit_line)

    if family == "regime_switch":
        trend_window = int(params["trend_window"])
        vol_window = int(params["vol_window"])
        returns = close.pct_change()
        realized = returns.rolling(vol_window, min_periods=vol_window).std(ddof=0)
        threshold = realized.expanding(min_periods=max(60, vol_window)).quantile(float(params["vol_quantile"]))
        quiet = realized <= threshold
        trend = close > close.rolling(trend_window, min_periods=trend_window).mean()
        rsi = base._rsi(close, int(params["rsi_period"]))
        entries = (quiet & trend) | (~quiet & (rsi < float(params["rsi_entry"])))
        exits = (quiet & ~trend) | (~quiet & (rsi > float(params["rsi_exit"])))
        return base._stateful_signal(entries, exits)

    if family == "ensemble_vote":
        fast = close.rolling(int(params["fast"]), min_periods=int(params["fast"])).mean()
        slow = close.rolling(int(params["slow"]), min_periods=int(params["slow"])).mean()
        prior_high = high.shift(1).rolling(int(params["breakout"]), min_periods=int(params["breakout"])).max()
        rsi = base._rsi(close, int(params["rsi_period"]))
        votes = (fast > slow).astype(int) + (close > prior_high).astype(int) + (rsi < float(params["rsi_entry"])).astype(int)
        return base._stateful_signal(votes >= int(params["minimum_votes"]), votes <= int(params["exit_votes"]))

    if family == "short_term_reversal":
        lookback = int(params["lookback"])
        move = close.pct_change(lookback)
        trend_window = int(params.get("trend_filter") or 0)
        trend_ok = pd.Series(True, index=close.index)
        if trend_window:
            trend_ok = close > close.rolling(trend_window, min_periods=trend_window).mean()
        rebound = close.pct_change(max(1, min(3, lookback)))
        return base._stateful_signal(
            (move < -float(params["drop_threshold"])) & trend_ok,
            rebound > float(params["exit_rebound"]),
        )

    raise KeyError(f"unsupported phase-two single-symbol family: {family}")


def _run_panel_strategy(
    panel: base.CanonicalPanel,
    candidate: base.StrategyCandidate,
    slippage_bps: float,
) -> dict[str, Any]:
    daily = panel.frame
    opens = daily.pivot(index="date", columns="ticker", values="open").sort_index().astype(float)
    closes = daily.pivot(index="date", columns="ticker", values="close").sort_index().astype(float)
    common = opens.index.intersection(closes.index)
    opens = opens.reindex(common)
    closes = closes.reindex(common)
    desired = _panel_weights(closes, candidate).reindex(index=common, columns=closes.columns).fillna(0.0)
    desired = _normalize_weight_bounds(desired)
    window = base.evaluation_window(panel, common)
    if window is not None:
        simulation_start, _scoring_start, scoring_end = window
        mask = (opens.index >= simulation_start) & (opens.index <= scoring_end)
        opens = opens.loc[mask]
        closes = closes.loc[mask]
        desired = desired.loc[mask]
    simulation = _simulate_weight_matrix(opens, closes, desired, slippage_bps, candidate)

    metadata = phase2_strategy_metadata(candidate)
    if metadata["benchmark"] == "cash_zero_return":
        benchmark = pd.Series(0.0, index=simulation["portfolio_returns"].index)
    elif metadata["benchmark"] == "equal_weight_panel_open_to_open":
        benchmark = (opens.shift(-1) / opens - 1.0).mean(axis=1).fillna(0.0)
    elif "SPY" in opens.columns:
        benchmark = (opens["SPY"].shift(-1) / opens["SPY"] - 1.0).fillna(0.0)
    else:
        benchmark = (opens.shift(-1) / opens - 1.0).mean(axis=1).fillna(0.0)

    return {
        **simulation,
        "benchmark_returns": benchmark.reindex(simulation["portfolio_returns"].index).fillna(0.0),
        "benchmark_name": metadata["benchmark"],
        "allocation": metadata["allocation"],
        "market_neutral": not metadata["long_only"],
    }


def _panel_weights(closes: pd.DataFrame, candidate: base.StrategyCandidate) -> pd.DataFrame:
    params = candidate.parameters
    family = candidate.family

    if family == "cross_sectional_momentum":
        lookback = int(params["lookback"])
        skip = int(params.get("skip") or 0)
        score = closes.shift(skip) / closes.shift(lookback + skip) - 1.0
        eligible = score >= float(params.get("minimum_momentum") or 0.0)
        return _rank_weights(
            score,
            top_k=int(params["top_k"]),
            rebalance=int(params["rebalance"]),
            long_short=bool(params.get("long_short")),
            long_eligible=eligible,
        )

    if family == "cross_sectional_reversal":
        score = -closes.pct_change(int(params["lookback"]))
        return _rank_weights(
            score,
            top_k=int(params["top_k"]),
            rebalance=int(params["rebalance"]),
            long_short=bool(params.get("long_short")),
        )

    if family == "risk_on_rotation":
        lookback = int(params["lookback"])
        score = closes.pct_change(lookback)
        spy = closes["SPY"] if "SPY" in closes.columns else closes.mean(axis=1)
        regime = (spy > spy.rolling(int(params["regime_window"]), min_periods=int(params["regime_window"])).mean()) & (spy.pct_change(lookback) > 0.0)
        weights = _rank_weights(
            score.drop(columns=["SPY"], errors="ignore"),
            top_k=int(params["top_k"]),
            rebalance=int(params["rebalance"]),
            long_short=False,
            long_eligible=score.drop(columns=["SPY"], errors="ignore") > 0.0,
        ).reindex(columns=closes.columns, fill_value=0.0)
        weights = weights.where(regime, 0.0)
        if str(params["defensive"]) == "spy" and "SPY" in weights.columns:
            weights.loc[~regime, "SPY"] = 1.0
        return weights

    if family == "cross_sectional_low_vol":
        returns = closes.pct_change()
        volatility = returns.rolling(int(params["vol_window"]), min_periods=int(params["vol_window"])).std(ddof=0)
        momentum = closes.pct_change(int(params["momentum_window"]))
        return _rank_weights(
            -volatility,
            top_k=int(params["top_k"]),
            rebalance=int(params["rebalance"]),
            long_short=False,
            long_eligible=momentum > 0.0,
        )

    if family == "pair_zscore":
        left = str(params["left"])
        right = str(params["right"])
        if left not in closes.columns or right not in closes.columns:
            return pd.DataFrame(0.0, index=closes.index, columns=closes.columns)
        spread = np.log(closes[left]) - np.log(closes[right])
        window = int(params["window"])
        mean = spread.rolling(window, min_periods=window).mean()
        std = spread.rolling(window, min_periods=window).std(ddof=0).replace(0.0, np.nan)
        z = (spread - mean) / std
        weights = pd.DataFrame(0.0, index=closes.index, columns=closes.columns)
        state = 0
        entry = float(params["entry_z"])
        exit_z = float(params["exit_z"])
        for date, value in z.items():
            if not math.isfinite(float(value)):
                continue
            if state == 0:
                if value > entry:
                    state = -1
                elif value < -entry:
                    state = 1
            elif abs(value) < exit_z:
                state = 0
            if state:
                weights.loc[date, left] = 0.5 * state
                weights.loc[date, right] = -0.5 * state
        return weights

    raise KeyError(f"unsupported phase-two panel family: {family}")


def _rank_weights(
    score: pd.DataFrame,
    *,
    top_k: int,
    rebalance: int,
    long_short: bool,
    long_eligible: pd.DataFrame | None = None,
) -> pd.DataFrame:
    output = pd.DataFrame(0.0, index=score.index, columns=score.columns)
    held = pd.Series(0.0, index=score.columns)
    for index, date in enumerate(score.index):
        if index % max(1, rebalance) != 0:
            output.loc[date] = held
            continue
        row = score.loc[date].dropna().sort_values(ascending=False)
        if row.empty:
            held = pd.Series(0.0, index=score.columns)
            output.loc[date] = held
            continue
        eligible = row
        if long_eligible is not None:
            allowed = long_eligible.loc[date].reindex(row.index).fillna(False).astype(bool)
            eligible = row[allowed]
        longs = eligible.head(top_k).index.tolist()
        held = pd.Series(0.0, index=score.columns)
        if long_short:
            shorts = [symbol for symbol in row.tail(top_k).index.tolist() if symbol not in longs]
            if longs:
                held.loc[longs] = 0.5 / len(longs)
            if shorts:
                held.loc[shorts] = -0.5 / len(shorts)
        elif longs:
            held.loc[longs] = 1.0 / len(longs)
        output.loc[date] = held
    return output


def _normalize_weight_bounds(weights: pd.DataFrame) -> pd.DataFrame:
    output = weights.replace([np.inf, -np.inf], np.nan).fillna(0.0).astype(float)
    gross = output.abs().sum(axis=1)
    excessive = gross > 1.0 + 1e-12
    if excessive.any():
        output.loc[excessive] = output.loc[excessive].div(gross.loc[excessive], axis=0)
    return output.clip(-1.0, 1.0)


def _simulate_weight_matrix(
    opens: pd.DataFrame,
    closes: pd.DataFrame,
    desired_close_weights: pd.DataFrame,
    slippage_bps: float,
    candidate: base.StrategyCandidate,
) -> dict[str, Any]:
    weights = desired_close_weights.shift(1).fillna(0.0)
    next_open_returns = (opens.shift(-1) / opens - 1.0).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    prior_weights = weights.shift(1).fillna(0.0)
    turnover = (weights - prior_weights).abs().sum(axis=1)
    portfolio = (weights * next_open_returns).sum(axis=1) - turnover * (slippage_bps / 10_000.0)

    trades: list[TradeRecord] = []
    exclusions: list[dict[str, Any]] = []
    for symbol in weights.columns:
        active_sign = 0
        active_entry: pd.Timestamp | None = None
        active_weight = 0.0
        for date in weights.index:
            value = float(weights.loc[date, symbol])
            sign = 1 if value > 1e-12 else -1 if value < -1e-12 else 0
            if active_sign and sign != active_sign:
                _append_weight_trade(
                    trades,
                    symbol=symbol,
                    direction=active_sign,
                    entry_date=active_entry,
                    exit_date=pd.Timestamp(date),
                    entry_open=float(opens.loc[active_entry, symbol]),
                    exit_open=float(opens.loc[date, symbol]),
                    weight=active_weight,
                    slippage_bps=slippage_bps,
                    candidate_id=candidate.candidate_id,
                )
                active_sign = 0
                active_entry = None
                active_weight = 0.0
            if not active_sign and sign:
                active_sign = sign
                active_entry = pd.Timestamp(date)
                active_weight = abs(value)
        if active_sign and active_entry is not None:
            final_date = pd.Timestamp(weights.index[-1])
            if final_date > active_entry:
                _append_weight_trade(
                    trades,
                    symbol=symbol,
                    direction=active_sign,
                    entry_date=active_entry,
                    exit_date=final_date,
                    entry_open=float(opens.loc[active_entry, symbol]),
                    exit_open=float(closes.loc[final_date, symbol]),
                    weight=active_weight,
                    slippage_bps=slippage_bps,
                    candidate_id=candidate.candidate_id,
                    forced=True,
                )
            else:
                exclusions.append({"symbol": symbol, "reason": "panel_entry_on_final_session", "date": final_date.isoformat()})

    return {
        "trades": trades,
        "portfolio_returns": portfolio,
        "exclusions": exclusions,
        "average_gross_exposure": float(weights.abs().sum(axis=1).mean()),
        "average_net_exposure": float(weights.sum(axis=1).mean()),
        "turnover": float(turnover.sum()),
    }


def _append_weight_trade(
    trades: list[TradeRecord],
    *,
    symbol: str,
    direction: int,
    entry_date: pd.Timestamp | None,
    exit_date: pd.Timestamp,
    entry_open: float,
    exit_open: float,
    weight: float,
    slippage_bps: float,
    candidate_id: str,
    forced: bool = False,
) -> None:
    if entry_date is None:
        return
    slip = slippage_bps / 10_000.0
    if direction > 0:
        entry_price = entry_open * (1.0 + slip)
        exit_price = exit_open * (1.0 - slip)
        trade_return = exit_price / entry_price - 1.0
        net_pnl = exit_price - entry_price
        label = "long"
    else:
        entry_price = entry_open * (1.0 - slip)
        exit_price = exit_open * (1.0 + slip)
        trade_return = (entry_price - exit_price) / entry_price
        net_pnl = entry_price - exit_price
        label = "short"
    trades.append(
        TradeRecord(
            trade_id=stable_id(
                "panel_trade",
                {
                    "candidate": candidate_id,
                    "symbol": symbol,
                    "direction": label,
                    "entry": entry_date.isoformat(),
                    "exit": exit_date.isoformat(),
                },
            ),
            symbol=symbol,
            direction=label,
            entry_time=entry_date.isoformat(),
            exit_time=exit_date.isoformat(),
            entry_price=float(entry_price),
            exit_price=float(exit_price),
            quantity=float(weight),
            gross_pnl=float((exit_open - entry_open) * direction),
            net_pnl=float(net_pnl),
            return_pct=float(trade_return * 100.0),
            metadata={
                "execution": "next_session_open",
                "slippage_bps_per_side": slippage_bps,
                "panel_strategy": True,
                "forced_final_close": forced,
            },
        )
    )


def _finalize(
    panel: base.CanonicalPanel,
    candidate: base.StrategyCandidate,
    policy: base.StrategyResearchPolicy,
    simulation: Mapping[str, Any],
) -> base.CandidateBacktest:
    portfolio_returns = pd.Series(simulation["portfolio_returns"]).sort_index().fillna(0.0)
    portfolio_returns = base.slice_scored_series(panel, portfolio_returns)
    benchmark = pd.Series(simulation["benchmark_returns"]).reindex(portfolio_returns.index).fillna(0.0)
    trades = tuple(trade for trade in simulation["trades"] if base.trade_in_evaluation_window(panel, trade))
    trade_returns = np.asarray(
        [float(item.return_pct) / 100.0 for item in trades if item.return_pct is not None and math.isfinite(item.return_pct)],
        dtype=float,
    )
    equity = 100_000.0 * (1.0 + portfolio_returns).cumprod()
    benchmark_equity = 100_000.0 * (1.0 + benchmark).cumprod()
    fold_returns = base._walk_forward_returns(portfolio_returns, policy.walk_forward_folds)
    fold_benchmark = base._walk_forward_returns(benchmark, policy.walk_forward_folds)
    positive_excess_folds = sum(left > right for left, right in zip(fold_returns, fold_benchmark))
    walk_forward_passed = positive_excess_folds >= math.ceil(policy.walk_forward_folds / 2) and float(np.median(fold_returns)) > 0.0
    raw_p = base._sign_flip_p_value(
        trade_returns,
        seed=policy.random_seed + int(candidate.candidate_id[-6:], 16) % 100_000,
    )
    total_return_pct = base._total_return_pct(portfolio_returns)
    benchmark_return_pct = base._total_return_pct(benchmark)
    maximum_drawdown_pct = base._maximum_drawdown_pct(equity)
    profit_factor = base._profit_factor(trade_returns)
    best_trade_exclusion = base._best_trade_exclusion(trade_returns)
    metadata = phase2_strategy_metadata(candidate)
    composite_score = base._composite_score(
        fold_returns=fold_returns,
        fold_benchmark=fold_benchmark,
        maximum_drawdown_pct=maximum_drawdown_pct,
        profit_factor=profit_factor,
        trade_count=len(trade_returns),
    )
    equity_points = tuple(EquityPoint(timestamp=index.isoformat(), equity=float(value)) for index, value in equity.items())

    quality = {
        "passed": True,
        "point_in_time_validated": True,
        "next_session_open_execution": True,
        "walk_forward_passed": walk_forward_passed,
        "best_trade_exclusion_passed": best_trade_exclusion,
        "benchmark_outperformance_passed": total_return_pct > benchmark_return_pct,
        "canonical_dataset_only": True,
        "canonical_raw_hashes_verified": True,
        "volume_features": False,
        "volume_evaluation": False,
        "research_role": panel.research_role,
        "final_holdout_claim": False,
        "automatic_promotion": False,
        "phase_two_family": True,
        "panel_strategy": candidate.family in PANEL_FAMILIES,
        "market_neutral": bool(simulation.get("market_neutral")),
        "evaluation_window_enforced": bool(panel.evaluation_start or panel.evaluation_end),
    }

    output = StandardBacktestOutput(
        trades=trades,
        equity_curve=equity_points,
        metrics={
            "total_return_pct": total_return_pct,
            "annualized_return_pct": base._annualized_return_pct(portfolio_returns),
            "annualized_volatility_pct": base._annualized_volatility_pct(portfolio_returns),
            "sharpe_ratio": base._sharpe_ratio(portfolio_returns),
            "maximum_drawdown_pct": maximum_drawdown_pct,
            "trade_count": len(trade_returns),
            "profit_factor": profit_factor,
            "win_rate": float(np.mean(trade_returns > 0.0)) if len(trade_returns) else None,
            "median_trade_return_pct": float(np.median(trade_returns) * 100.0) if len(trade_returns) else None,
            "composite_score": composite_score,
            "positive_excess_folds": positive_excess_folds,
            "walk_forward_fold_count": policy.walk_forward_folds,
            "average_gross_exposure": simulation.get("average_gross_exposure"),
            "average_net_exposure": simulation.get("average_net_exposure"),
            "total_turnover": simulation.get("turnover"),
        },
        benchmark_metrics={
            "benchmark": simulation["benchmark_name"],
            "total_return_pct": benchmark_return_pct,
            "annualized_return_pct": base._annualized_return_pct(benchmark),
            "maximum_drawdown_pct": base._maximum_drawdown_pct(benchmark_equity),
            "strategy_excess_return_pct": total_return_pct - benchmark_return_pct,
        },
        regime_metrics=base._regime_metrics(panel.frame, portfolio_returns),
        statistical_tests={
            "test": "deterministic_sign_flip_mean_trade_return",
            "raw_p_value": raw_p,
            "holm_adjusted_p_value": raw_p,
            "multiple_testing_family": "pending_batch_adjustment",
            "trade_observations": len(trade_returns),
        },
        quality_checks=quality,
        exclusions=tuple(simulation.get("exclusions") or ()),
        assumptions={
            "execution": "signal formed at session close; positions established at next session open",
            "slippage_bps_per_side": policy.slippage_bps_per_side,
            "fees": "zero explicit commission; turnover slippage charged",
            "capital_allocation": simulation["allocation"],
            "data_scope": "price_only_daily_ohlc_derived_from_canonical_one_minute_partitions",
            "dataset_id": panel.dataset_id,
            "dataset_name": panel.dataset_name,
            "lineage_hash": panel.lineage_hash,
            "research_role": panel.research_role,
            "phase": 2,
            "strategy_scope": metadata["scope"],
            "direction": metadata["direction"],
            "evaluation_start": panel.evaluation_start,
            "evaluation_end": panel.evaluation_end,
            "warmup_data_excluded_from_metrics": bool(panel.evaluation_start or panel.evaluation_end),
        },
        notes=(
            "Phase-two exploratory autonomous research only; this period is not treated as a new untouched final holdout.",
            "Market-neutral candidates use cash as the benchmark; long-only panel candidates use SPY or the equal-weight panel as declared.",
            "A screening PASS cannot promote, paper trade, or place an order.",
        ),
    )
    return base.CandidateBacktest(
        candidate=candidate,
        output=output,
        raw_p_value=raw_p,
        composite_score=composite_score,
        fold_returns_pct=tuple(fold_returns),
    )


def phase2_neighbour_parameter_sets(candidate: base.StrategyCandidate) -> tuple[dict[str, Any], ...]:
    params = dict(candidate.parameters)
    neighbours: list[dict[str, Any]] = []
    numeric_keys = [key for key, value in params.items() if isinstance(value, (int, float)) and not isinstance(value, bool)]
    preferred = [
        key
        for key in (
            "lookback",
            "fast",
            "slow",
            "window",
            "entry_z",
            "entry_threshold",
            "entry_multiple",
            "vol_window",
            "regime_window",
            "rebalance",
        )
        if key in numeric_keys
    ]
    keys = preferred[:3] or numeric_keys[:3]
    for key in keys:
        value = params[key]
        for multiplier in (0.75, 1.25):
            changed = dict(params)
            if isinstance(value, int):
                changed[key] = max(2, int(round(value * multiplier)))
            else:
                changed[key] = max(0.001, round(float(value) * multiplier, 6))
            if changed != params and phase2_valid_candidate_parameters(candidate.family, changed):
                neighbours.append(changed)
    unique = {stable_id("phase2_params", item): item for item in neighbours}
    return tuple(unique.values())


def phase2_valid_candidate_parameters(family: str, params: Mapping[str, Any]) -> bool:
    try:
        if family in {"ema_trend", "ensemble_vote"}:
            return 2 <= int(params["fast"]) < int(params["slow"]) <= 400
        if family == "time_series_momentum":
            return 5 <= int(params["lookback"]) <= 400 and float(params["entry_threshold"]) > float(params["exit_threshold"]) >= -0.20
        if family == "keltner_breakout":
            return 5 <= int(params["ema_window"]) <= 250 and 5 <= int(params["atr_window"]) <= 100 and float(params["entry_multiple"]) > float(params["exit_multiple"]) >= 0.0
        if family == "regime_switch":
            return 20 <= int(params["trend_window"]) <= 400 and 10 <= int(params["vol_window"]) <= 150 and 0.2 <= float(params["vol_quantile"]) <= 0.8
        if family == "short_term_reversal":
            return 2 <= int(params["lookback"]) <= 30 and 0.005 <= float(params["drop_threshold"]) <= 0.30
        if family in {"cross_sectional_momentum", "cross_sectional_reversal"}:
            return 2 <= int(params["lookback"]) <= 400 and 1 <= int(params["top_k"]) <= 4 and 1 <= int(params["rebalance"]) <= 60
        if family == "risk_on_rotation":
            return 5 <= int(params["lookback"]) <= 400 and 20 <= int(params["regime_window"]) <= 400 and 1 <= int(params["top_k"]) <= 4
        if family == "cross_sectional_low_vol":
            return 5 <= int(params["momentum_window"]) <= 400 and 10 <= int(params["vol_window"]) <= 150 and 1 <= int(params["top_k"]) <= 4
        if family == "pair_zscore":
            return 10 <= int(params["window"]) <= 250 and float(params["entry_z"]) > float(params["exit_z"]) >= 0.0
    except (KeyError, TypeError, ValueError):
        return False
    return False
