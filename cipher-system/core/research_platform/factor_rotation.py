"""Point-in-time factor and macro ETF rotation research primitives.

Signals are formed from adjusted daily closes and applied at the following
session open.  The module is read-only and cannot promote or execute orders.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from .hashing import stable_id


@dataclass(frozen=True)
class FactorRotationSpec:
    name: str
    mode: str
    lookback: int
    skip: int
    top_k: int
    rebalance: int = 21
    score_type: str = "momentum"
    absolute_momentum: bool = True
    trend_filter: int = 0
    defensive_symbol: str = "BIL"
    core_weight: float = 0.0
    target_volatility: float | None = None
    relative_overlay_lookback: int = 0
    market_trend_filter: int = 0
    passive_symbol: str = "SPY"
    risk_off_symbol: str = "BIL"

    @property
    def strategy_id(self) -> str:
        # Preserve the exact identity contract used by the initial grid before
        # the factor dataset was downloaded. Optional overlay fields extend the
        # payload only when non-default, so adding a new feature cannot silently
        # rename an already frozen strategy.
        payload: dict[str, Any] = {
            "name": self.name,
            "mode": self.mode,
            "lookback": self.lookback,
            "skip": self.skip,
            "top_k": self.top_k,
            "rebalance": self.rebalance,
            "score_type": self.score_type,
            "absolute_momentum": self.absolute_momentum,
            "trend_filter": self.trend_filter,
            "defensive_symbol": self.defensive_symbol,
            "core_weight": self.core_weight,
            "target_volatility": self.target_volatility,
        }
        if self.relative_overlay_lookback:
            payload["relative_overlay_lookback"] = self.relative_overlay_lookback
        if self.market_trend_filter:
            payload["market_trend_filter"] = self.market_trend_filter
        if self.passive_symbol != "SPY":
            payload["passive_symbol"] = self.passive_symbol
        if self.risk_off_symbol != "BIL":
            payload["risk_off_symbol"] = self.risk_off_symbol
        return stable_id("factor_rotation", payload)


def default_factor_rotation_specs() -> tuple[FactorRotationSpec, ...]:
    return (
        FactorRotationSpec("momentum_63_top1", "rank", 63, 0, 1),
        FactorRotationSpec("momentum_126_top1", "rank", 126, 0, 1),
        FactorRotationSpec("momentum_252_top1", "rank", 252, 0, 1),
        FactorRotationSpec("momentum_126_top3", "rank", 126, 0, 3),
        FactorRotationSpec("momentum_252_top3", "rank", 252, 0, 3),
        FactorRotationSpec("momentum_12_1_top3", "rank", 252, 21, 3),
        FactorRotationSpec("risk_adjusted_126_top3", "rank", 126, 0, 3, score_type="risk_adjusted"),
        FactorRotationSpec("risk_adjusted_252_top3", "rank", 252, 0, 3, score_type="risk_adjusted"),
        FactorRotationSpec("low_vol_positive_momentum_top5", "inverse_vol", 126, 0, 5, score_type="low_vol"),
        FactorRotationSpec("dual_momentum_126_top3", "rank", 126, 0, 3, defensive_symbol="BIL"),
        FactorRotationSpec("dual_momentum_252_top3", "rank", 252, 0, 3, defensive_symbol="BIL"),
        FactorRotationSpec("trend_filtered_126_top3", "rank", 126, 0, 3, trend_filter=200),
        FactorRotationSpec("trend_filtered_252_top3", "rank", 252, 0, 3, trend_filter=200),
        FactorRotationSpec("category_balanced_126", "category", 126, 0, 1),
        FactorRotationSpec("risk_on_off_126", "risk_on_off", 126, 0, 3),
        FactorRotationSpec("core50_satellite50_126", "core_satellite", 126, 0, 2, core_weight=0.50),
    )


def adaptive_factor_rotation_specs() -> tuple[FactorRotationSpec, ...]:
    """Bounded descendants of high-return, high-drawdown initial near-misses."""
    return (
        FactorRotationSpec("momentum_252_top3_vol10", "rank", 252, 0, 3, target_volatility=0.10),
        FactorRotationSpec("momentum_252_top3_vol12", "rank", 252, 0, 3, target_volatility=0.12),
        FactorRotationSpec("momentum_252_top3_vol15", "rank", 252, 0, 3, target_volatility=0.15),
        FactorRotationSpec("momentum_12_1_top3_vol12", "rank", 252, 21, 3, target_volatility=0.12),
        FactorRotationSpec("trend_filtered_252_top3_vol12", "rank", 252, 0, 3, trend_filter=200, target_volatility=0.12),
        FactorRotationSpec("risk_adjusted_252_top3_vol12", "rank", 252, 0, 3, score_type="risk_adjusted", target_volatility=0.12),
        FactorRotationSpec("momentum_252_top5_vol12", "rank", 252, 0, 5, target_volatility=0.12),
        FactorRotationSpec("core25_satellite75_252", "core_satellite", 252, 0, 3, core_weight=0.25),
        FactorRotationSpec("momentum_252_top3_relative126", "rank", 252, 0, 3, relative_overlay_lookback=126),
        FactorRotationSpec("momentum_252_top3_relative252", "rank", 252, 0, 3, relative_overlay_lookback=252),
        FactorRotationSpec("momentum_12_1_top3_relative126", "rank", 252, 21, 3, relative_overlay_lookback=126),
        FactorRotationSpec("trend_filtered_252_top3_relative126", "rank", 252, 0, 3, trend_filter=200, relative_overlay_lookback=126),
        FactorRotationSpec("momentum_252_top3_market200", "rank", 252, 0, 3, market_trend_filter=200),
        FactorRotationSpec("momentum_252_top3_relative126_market200", "rank", 252, 0, 3, relative_overlay_lookback=126, market_trend_filter=200),
        FactorRotationSpec("risk_adjusted_252_top3_relative126", "rank", 252, 0, 3, score_type="risk_adjusted", relative_overlay_lookback=126),
        FactorRotationSpec("core25_satellite75_252_relative126", "core_satellite", 252, 0, 3, core_weight=0.25, relative_overlay_lookback=126),
        FactorRotationSpec("core50_satellite50_252_relative126", "core_satellite", 252, 0, 3, core_weight=0.50, relative_overlay_lookback=126),
        FactorRotationSpec("core70_satellite30_252_relative126", "core_satellite", 252, 0, 3, core_weight=0.70, relative_overlay_lookback=126),
        FactorRotationSpec("core85_satellite15_252_relative126", "core_satellite", 252, 0, 3, core_weight=0.85, relative_overlay_lookback=126),
        FactorRotationSpec("core70_satellite30_252_relative252", "core_satellite", 252, 0, 3, core_weight=0.70, relative_overlay_lookback=252),
        FactorRotationSpec("category_balanced_252", "category", 252, 0, 1),
        FactorRotationSpec("category_balanced_252_relative126", "category", 252, 0, 1, relative_overlay_lookback=126),
        FactorRotationSpec("core50_category50_252_relative126", "category_core_satellite", 252, 0, 1, core_weight=0.50, relative_overlay_lookback=126),
        FactorRotationSpec("core70_category30_252_relative126", "category_core_satellite", 252, 0, 1, core_weight=0.70, relative_overlay_lookback=126),
    )


def score_frame(closes: pd.DataFrame, spec: FactorRotationSpec) -> tuple[pd.DataFrame, pd.DataFrame]:
    momentum = closes.shift(spec.skip) / closes.shift(spec.lookback + spec.skip) - 1.0
    returns = closes.pct_change()
    volatility = returns.rolling(spec.lookback, min_periods=spec.lookback).std(ddof=0) * math.sqrt(252.0)
    if spec.score_type == "momentum":
        score = momentum
    elif spec.score_type == "risk_adjusted":
        score = momentum / volatility.replace(0.0, np.nan)
    elif spec.score_type == "low_vol":
        score = -volatility
    else:
        raise KeyError(f"unsupported factor score type: {spec.score_type}")
    eligible = pd.DataFrame(True, index=closes.index, columns=closes.columns)
    if spec.absolute_momentum:
        eligible &= momentum > 0.0
    if spec.trend_filter:
        trend = closes > closes.rolling(spec.trend_filter, min_periods=spec.trend_filter).mean()
        eligible &= trend.fillna(False)
    return score, eligible


def _equal_weights(columns: Sequence[str], universe: Sequence[str], total: float = 1.0) -> pd.Series:
    weights = pd.Series(0.0, index=list(universe))
    selected = list(columns)
    if selected:
        weights.loc[selected] = float(total) / len(selected)
    return weights


def _inverse_vol_weights(
    selected: Sequence[str],
    volatility: pd.Series,
    universe: Sequence[str],
    total: float = 1.0,
) -> pd.Series:
    weights = pd.Series(0.0, index=list(universe))
    if not selected:
        return weights
    inverse = 1.0 / volatility.reindex(selected).replace(0.0, np.nan)
    inverse = inverse.replace([np.inf, -np.inf], np.nan).dropna()
    if inverse.empty or float(inverse.sum()) <= 0:
        return _equal_weights(selected, universe, total)
    weights.loc[inverse.index] = inverse / inverse.sum() * float(total)
    return weights


def build_desired_weights(
    closes: pd.DataFrame,
    spec: FactorRotationSpec,
    categories: Mapping[str, Sequence[str]],
) -> pd.DataFrame:
    closes = closes.sort_index().astype(float)
    score, eligible = score_frame(closes, spec)
    daily_vol = closes.pct_change().rolling(spec.lookback, min_periods=spec.lookback).std(ddof=0)
    weights = pd.DataFrame(0.0, index=closes.index, columns=closes.columns)
    held = pd.Series(0.0, index=closes.columns)
    risk_assets = [symbol for name in ("equity_core", "sectors", "international") for symbol in categories.get(name, ()) if symbol in closes.columns]
    defensive_assets = [symbol for symbol in categories.get("defensive", ()) if symbol in closes.columns]

    for position, date in enumerate(closes.index):
        if position < max(spec.lookback + spec.skip, spec.trend_filter):
            weights.loc[date] = held
            continue
        if (position - max(spec.lookback + spec.skip, spec.trend_filter)) % max(1, spec.rebalance) != 0:
            weights.loc[date] = held
            continue
        row = score.loc[date].replace([np.inf, -np.inf], np.nan).dropna().sort_values(ascending=False)
        allowed = eligible.loc[date].reindex(row.index).fillna(False).astype(bool)
        ranked = row[allowed]
        held = pd.Series(0.0, index=closes.columns)

        if spec.mode == "rank":
            selected = ranked.head(spec.top_k).index.tolist()
            if selected:
                held = _equal_weights(selected, closes.columns)
            elif spec.defensive_symbol in closes.columns:
                held.loc[spec.defensive_symbol] = 1.0
        elif spec.mode == "inverse_vol":
            selected = ranked.head(spec.top_k).index.tolist()
            if selected:
                held = _inverse_vol_weights(selected, daily_vol.loc[date], closes.columns)
            elif spec.defensive_symbol in closes.columns:
                held.loc[spec.defensive_symbol] = 1.0
        elif spec.mode == "category":
            selected: list[str] = []
            for category in ("equity_core", "sectors", "international", "bonds", "real_assets"):
                members = [symbol for symbol in categories.get(category, ()) if symbol in ranked.index]
                if members:
                    selected.append(str(ranked.reindex(members).idxmax()))
            if selected:
                held = _equal_weights(selected, closes.columns)
            elif spec.defensive_symbol in closes.columns:
                held.loc[spec.defensive_symbol] = 1.0
        elif spec.mode == "risk_on_off":
            spy = closes["SPY"] if "SPY" in closes.columns else closes.mean(axis=1)
            risk_on = bool(
                spy.loc[date] > spy.rolling(200, min_periods=200).mean().loc[date]
                and spy.loc[date] / spy.shift(63).loc[date] - 1.0 > 0.0
            )
            pool = risk_assets if risk_on else defensive_assets
            selected = [symbol for symbol in ranked.index if symbol in pool][: spec.top_k]
            if selected:
                held = _equal_weights(selected, closes.columns)
            elif spec.defensive_symbol in closes.columns:
                held.loc[spec.defensive_symbol] = 1.0
        elif spec.mode == "core_satellite":
            if "SPY" not in closes.columns:
                raise ValueError("core-satellite factor rotation requires SPY")
            held.loc["SPY"] = spec.core_weight
            selected = [symbol for symbol in ranked.index if symbol != "SPY"][: spec.top_k]
            if selected:
                satellite = 1.0 - spec.core_weight
                held.loc[selected] = satellite / len(selected)
            else:
                held.loc["SPY"] = 1.0
        elif spec.mode == "category_core_satellite":
            if "SPY" not in closes.columns:
                raise ValueError("category core-satellite rotation requires SPY")
            selected = []
            for category in ("equity_core", "sectors", "international", "bonds", "real_assets"):
                members = [symbol for symbol in categories.get(category, ()) if symbol in ranked.index and symbol != "SPY"]
                if members:
                    selected.append(str(ranked.reindex(members).idxmax()))
            held.loc["SPY"] = spec.core_weight
            if selected:
                satellite = 1.0 - spec.core_weight
                held.loc[selected] = satellite / len(selected)
            else:
                held.loc["SPY"] = 1.0
        else:
            raise KeyError(f"unsupported factor rotation mode: {spec.mode}")

        weights.loc[date] = held

    if spec.relative_overlay_lookback:
        if spec.passive_symbol not in closes.columns:
            raise ValueError("relative overlay requires the passive symbol")
        close_returns = closes.pct_change().fillna(0.0)
        active_returns = (weights.shift(1).fillna(0.0) * close_returns).sum(axis=1)
        passive_returns = close_returns[spec.passive_symbol]
        lookback = int(spec.relative_overlay_lookback)
        active_trailing = (1.0 + active_returns).rolling(lookback, min_periods=lookback).apply(np.prod, raw=True) - 1.0
        passive_trailing = (1.0 + passive_returns).rolling(lookback, min_periods=lookback).apply(np.prod, raw=True) - 1.0
        use_active = (active_trailing > passive_trailing).fillna(False)
        weights = weights.where(use_active, 0.0)
        weights.loc[~use_active, spec.passive_symbol] = 1.0

    if spec.market_trend_filter:
        if spec.passive_symbol not in closes.columns:
            raise ValueError("market trend overlay requires the passive symbol")
        trend = closes[spec.passive_symbol] > closes[spec.passive_symbol].rolling(
            int(spec.market_trend_filter),
            min_periods=int(spec.market_trend_filter),
        ).mean()
        risk_off = ~trend.fillna(False)
        weights = weights.where(~risk_off, 0.0)
        if spec.risk_off_symbol in weights.columns:
            weights.loc[risk_off, spec.risk_off_symbol] = 1.0

    if spec.target_volatility is not None:
        close_returns = closes.pct_change().fillna(0.0)
        counterfactual = (weights.shift(1).fillna(0.0) * close_returns).sum(axis=1)
        realized = counterfactual.rolling(63, min_periods=42).std(ddof=0) * math.sqrt(252.0)
        scale = (float(spec.target_volatility) / realized.replace(0.0, np.nan)).clip(upper=1.0).fillna(0.0)
        weights = weights.mul(scale, axis=0)
        remainder = (1.0 - weights.sum(axis=1)).clip(lower=0.0)
        if spec.defensive_symbol in weights.columns:
            weights.loc[:, spec.defensive_symbol] = weights[spec.defensive_symbol] + remainder
    return weights


def simulate_rotation(
    opens: pd.DataFrame,
    desired_close_weights: pd.DataFrame,
    *,
    slippage_bps_per_side: float,
) -> dict[str, Any]:
    opens = opens.sort_index().astype(float)
    desired = desired_close_weights.reindex(index=opens.index, columns=opens.columns).fillna(0.0)
    weights = desired.shift(1).fillna(0.0)
    gross = weights.abs().sum(axis=1)
    if bool((gross > 1.0 + 1e-10).any()):
        raise ValueError("factor rotation gross exposure exceeds one")
    next_open = (opens.shift(-1) / opens - 1.0).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    prior = weights.shift(1).fillna(0.0)
    one_way_turnover = (weights - prior).abs().sum(axis=1) / 2.0
    gross_return = (weights * next_open).sum(axis=1)
    net_return = gross_return - one_way_turnover * (float(slippage_bps_per_side) / 10_000.0)
    return {
        "returns": net_return,
        "gross_returns": gross_return,
        "weights": weights,
        "turnover": one_way_turnover,
        "total_turnover": float(one_way_turnover.sum()),
        "average_gross_exposure": float(gross.mean()),
        "cash_fraction": float((1.0 - gross).clip(lower=0.0).mean()),
    }


def selection_frequency(weights: pd.DataFrame) -> dict[str, float]:
    denominator = max(1, len(weights))
    return {column: float((weights[column].abs() > 1e-12).sum() / denominator) for column in weights.columns}
