"""Recent-regime primitives for rolling 2025-2026 selectors and market gates.

Selectors and gates use only component returns and market features available
before each calendar month. Component strategies retain next-session-open
execution and their own slippage; allocation changes add a separate switching
cost. This module has no promotion, paper-execution, broker, or order authority.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from .hashing import stable_id


TREND_FAMILIES = frozenset(
    {
        "sma_trend",
        "ema_trend",
        "time_series_momentum",
        "donchian_breakout",
        "keltner_breakout",
        "low_vol_breakout",
        "ensemble_vote",
        "risk_on_rotation",
    }
)
REVERSION_FAMILIES = frozenset(
    {
        "rsi_reversion",
        "bollinger_reversion",
        "short_term_reversal",
        "cross_sectional_reversal",
    }
)

# Outcome-informed pool frozen before the first monthly recent-regime selector
# run on August 4, 2026. Later phase-three discoveries cannot silently alter it.
RECENT_CANDIDATE_IDS = (
    "candidate_0160799097a46e42419b37ba",
    "candidate_1f30ee83f82bf6891d6fdb50",
    "candidate_2fcf4cd15dc8a508013b350e",
    "candidate_450ab714a604e63bc221ccfb",
    "candidate_4b542ee69f5320cedb0439be",
    "candidate_5236dfbb2512a94569d57a7a",
    "candidate_7d8580584de158f76e339984",
    "candidate_8fff4451fbc71abd0a5d2639",
    "candidate_a19fdcaef2510d901d9f4335",
    "candidate_a1dd25c6c1804b1ca67a40be",
    "candidate_b7c98046a81d6600c3d21c29",
    "candidate_ddba1d2abde0e55c6663eacb",
    "candidate_f77ecd3f538b4211298f59e1",
    "candidate_fc4e54dbae61339b495f6aff",
)


@dataclass(frozen=True)
class RecentSelectorSpec:
    name: str
    lookback_sessions: int
    top_k: int
    objective: str
    mode: str = "dynamic"
    passive_component: str = "passive_spy"
    core_weight: float = 0.0
    minimum_training_return: float = 0.0

    @property
    def selector_id(self) -> str:
        return stable_id(
            "recent_selector",
            {
                "name": self.name,
                "lookback_sessions": self.lookback_sessions,
                "top_k": self.top_k,
                "objective": self.objective,
                "mode": self.mode,
                "passive_component": self.passive_component,
                "core_weight": self.core_weight,
                "minimum_training_return": self.minimum_training_return,
            },
        )


@dataclass(frozen=True)
class RecentGateSpec:
    name: str
    condition: str

    @property
    def gate_id(self) -> str:
        return stable_id("recent_gate", {"name": self.name, "condition": self.condition})


def default_recent_gate_specs() -> tuple[RecentGateSpec, ...]:
    return (
        RecentGateSpec("active_when_spy_21d_weak", "spy_return_21_le_zero"),
        RecentGateSpec("active_when_spy_63d_weak", "spy_return_63_le_zero"),
        RecentGateSpec("active_when_spy_below_sma50", "spy_below_sma50"),
        RecentGateSpec("active_when_spy_drawdown_2pct", "spy_drawdown_63_le_minus_2pct"),
        RecentGateSpec("active_when_realized_vol_high", "realized_vol_21_ge_trailing_median"),
        RecentGateSpec("active_when_dispersion_high", "dispersion_21_ge_trailing_median"),
        RecentGateSpec("active_when_weak_or_high_vol", "spy_21_weak_or_high_vol"),
        RecentGateSpec("active_when_weak_or_drawdown", "spy_21_weak_or_drawdown"),
    )


def default_recent_selector_specs() -> tuple[RecentSelectorSpec, ...]:
    return (
        RecentSelectorSpec("monthly_top1_63d_return", 63, 1, "total_return"),
        RecentSelectorSpec("monthly_top2_63d_sharpe", 63, 2, "sharpe"),
        RecentSelectorSpec("monthly_top1_126d_return_drawdown", 126, 1, "return_drawdown"),
        RecentSelectorSpec("monthly_top2_126d_sharpe", 126, 2, "sharpe"),
        RecentSelectorSpec("monthly_family_balanced_126d_sharpe", 126, 2, "sharpe", mode="family_balanced"),
        RecentSelectorSpec("monthly_family_balanced_63d_return", 63, 2, "total_return", mode="family_balanced"),
        RecentSelectorSpec("monthly_spy50_top1_126d_sharpe", 126, 1, "sharpe", mode="core_satellite", core_weight=0.50),
        RecentSelectorSpec("monthly_spy70_top1_126d_return_drawdown", 126, 1, "return_drawdown", mode="core_satellite", core_weight=0.70),
    )


def training_score(values: pd.Series, objective: str) -> tuple[float, float]:
    clean = values.replace([np.inf, -np.inf], np.nan).dropna()
    if len(clean) < 20:
        return -math.inf, -1.0
    cumulative = float((1.0 + clean).prod() - 1.0)
    if objective == "total_return":
        return cumulative, cumulative
    if objective == "sharpe":
        deviation = float(clean.std(ddof=0))
        score = float(clean.mean() / deviation * math.sqrt(252.0)) if deviation > 0 else -math.inf
        return score, cumulative
    if objective == "sortino":
        downside = clean[clean < 0]
        deviation = float(np.sqrt(np.mean(np.square(downside)))) if len(downside) else 0.0
        score = float(clean.mean() / deviation * math.sqrt(252.0)) if deviation > 0 else (999.0 if clean.mean() > 0 else -math.inf)
        return score, cumulative
    if objective == "return_drawdown":
        equity = (1.0 + clean).cumprod()
        drawdown = float((equity / equity.cummax() - 1.0).min())
        score = cumulative / abs(drawdown) if drawdown < 0 else (999.0 if cumulative > 0 else -math.inf)
        return score, cumulative
    raise KeyError(f"unsupported recent selector objective: {objective}")


def _rank_components(
    training: pd.DataFrame,
    columns: Sequence[str],
    spec: RecentSelectorSpec,
) -> list[tuple[str, float, float]]:
    ranked: list[tuple[str, float, float]] = []
    for component in columns:
        score, cumulative = training_score(training[component], spec.objective)
        if cumulative >= spec.minimum_training_return and math.isfinite(score) and score > 0:
            ranked.append((component, score, cumulative))
    ranked.sort(key=lambda item: (item[1], item[2], item[0]), reverse=True)
    return ranked


def build_monthly_selector_weights(
    component_returns: pd.DataFrame,
    component_families: Mapping[str, str],
    spec: RecentSelectorSpec,
    *,
    evaluation_start: str = "2026-01-02",
    evaluation_end: str = "2026-12-31",
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Build monthly 2026 weights using only returns before each month."""
    returns = component_returns.sort_index().replace([np.inf, -np.inf], np.nan).fillna(0.0)
    if spec.passive_component not in returns.columns:
        raise KeyError(f"passive component is missing: {spec.passive_component}")
    start = pd.Timestamp(evaluation_start)
    end = pd.Timestamp(evaluation_end)
    evaluation_index = returns.index[(returns.index >= start) & (returns.index <= end)]
    if evaluation_index.empty:
        raise RuntimeError("recent selector evaluation window has no sessions")

    active_columns = [column for column in returns.columns if column != spec.passive_component]
    weights = pd.DataFrame(0.0, index=evaluation_index, columns=returns.columns)
    decisions: list[dict[str, Any]] = []
    periods = pd.Series(evaluation_index.to_period("M"), index=evaluation_index)

    for month in periods.drop_duplicates().tolist():
        month_dates = evaluation_index[periods.to_numpy() == month]
        first_date = pd.Timestamp(month_dates[0])
        prior = returns.index[returns.index < first_date]
        if len(prior) < spec.lookback_sessions:
            selected: list[str] = []
            ranked: list[tuple[str, float, float]] = []
            training_start = None
            training_end = None
        else:
            training_index = prior[-spec.lookback_sessions :]
            training = returns.loc[training_index, active_columns]
            ranked = _rank_components(training, active_columns, spec)
            selected = []
            if spec.mode == "family_balanced":
                trend = [item for item in ranked if component_families.get(item[0]) in TREND_FAMILIES]
                reversion = [item for item in ranked if component_families.get(item[0]) in REVERSION_FAMILIES]
                if trend:
                    selected.append(trend[0][0])
                if reversion:
                    selected.append(reversion[0][0])
                if len(selected) < spec.top_k:
                    for component, _score, _ret in ranked:
                        if component not in selected:
                            selected.append(component)
                        if len(selected) >= spec.top_k:
                            break
            else:
                selected = [item[0] for item in ranked[: max(1, spec.top_k)]]
            training_start = pd.Timestamp(training_index[0]).date().isoformat()
            training_end = pd.Timestamp(training_index[-1]).date().isoformat()

        month_weight = pd.Series(0.0, index=returns.columns)
        if spec.mode == "core_satellite":
            if not 0.0 <= spec.core_weight <= 1.0:
                raise ValueError("recent selector core_weight must be between zero and one")
            month_weight.loc[spec.passive_component] = spec.core_weight
            budget = 1.0 - spec.core_weight
            if selected:
                month_weight.loc[selected] = budget / len(selected)
            else:
                month_weight.loc[spec.passive_component] = 1.0
        elif selected:
            month_weight.loc[selected] = 1.0 / len(selected)
        else:
            month_weight.loc[spec.passive_component] = 1.0

        weights.loc[month_dates, :] = np.tile(month_weight.to_numpy(dtype=float), (len(month_dates), 1))
        score_by_component = {
            component: {"score": float(score), "training_return_pct": float(cumulative * 100.0)}
            for component, score, cumulative in ranked[:10]
        }
        decisions.append(
            {
                "month": str(month),
                "first_session": first_date.date().isoformat(),
                "last_session": pd.Timestamp(month_dates[-1]).date().isoformat(),
                "training_start": training_start,
                "training_end": training_end,
                "selected_components": selected,
                "weights": {key: float(value) for key, value in month_weight.items() if abs(float(value)) > 1e-12},
                "ranked_training_scores": score_by_component,
                "fallback_to_spy": not selected,
            }
        )
    return weights, decisions


def _gate_active(features: Mapping[str, Any], condition: str) -> bool:
    return {
        "spy_return_21_le_zero": float(features.get("spy_return_21") or 0.0) <= 0.0,
        "spy_return_63_le_zero": float(features.get("spy_return_63") or 0.0) <= 0.0,
        "spy_below_sma50": float(features.get("spy_sma50_distance") or 0.0) <= 0.0,
        "spy_drawdown_63_le_minus_2pct": float(features.get("spy_drawdown_63") or 0.0) <= -0.02,
        "realized_vol_21_ge_trailing_median": float(features.get("realized_vol_21") or 0.0)
        >= float(features.get("realized_vol_median_252") or math.inf),
        "dispersion_21_ge_trailing_median": float(features.get("dispersion_21") or 0.0)
        >= float(features.get("dispersion_median_252") or math.inf),
        "spy_21_weak_or_high_vol": (
            float(features.get("spy_return_21") or 0.0) <= 0.0
            or float(features.get("realized_vol_21") or 0.0)
            >= float(features.get("realized_vol_median_252") or math.inf)
        ),
        "spy_21_weak_or_drawdown": (
            float(features.get("spy_return_21") or 0.0) <= 0.0
            or float(features.get("spy_drawdown_63") or 0.0) <= -0.02
        ),
    }[condition]


def build_monthly_gate_weights(
    base_weights: pd.DataFrame,
    market_features: pd.DataFrame,
    spec: RecentGateSpec,
    *,
    passive_component: str = "passive_spy",
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Switch a monthly active selector to SPY using prior-session features only."""
    weights = base_weights.sort_index().copy()
    if passive_component not in weights.columns:
        raise KeyError(f"passive component is missing: {passive_component}")
    features = market_features.sort_index().replace([np.inf, -np.inf], np.nan)
    decisions: list[dict[str, Any]] = []
    periods = pd.Series(weights.index.to_period("M"), index=weights.index)
    for month in periods.drop_duplicates().tolist():
        month_dates = weights.index[periods.to_numpy() == month]
        first_date = pd.Timestamp(month_dates[0])
        prior_dates = features.index[features.index < first_date]
        feature_date = pd.Timestamp(prior_dates[-1]) if len(prior_dates) else None
        row = features.loc[feature_date].dropna().to_dict() if feature_date is not None else {}
        active = bool(row and _gate_active(row, spec.condition))
        if not active:
            weights.loc[month_dates, :] = 0.0
            weights.loc[month_dates, passive_component] = 1.0
        decisions.append(
            {
                "month": str(month),
                "first_session": first_date.date().isoformat(),
                "feature_date": feature_date.date().isoformat() if feature_date is not None else None,
                "active_selector": active,
                "fallback_to_spy": not active,
                "condition": spec.condition,
                "features": {key: float(value) for key, value in row.items() if pd.notna(value)},
            }
        )
    return weights, decisions


def apply_monthly_selector(
    component_returns: pd.DataFrame,
    weights: pd.DataFrame,
    *,
    switching_cost_bps: float,
) -> dict[str, Any]:
    returns = component_returns.reindex(index=weights.index, columns=weights.columns).fillna(0.0)
    weights = weights.reindex(index=returns.index, columns=returns.columns).fillna(0.0)
    gross = weights.abs().sum(axis=1)
    if bool((gross > 1.0 + 1e-10).any()):
        raise ValueError("recent selector gross exposure exceeds one")
    prior = weights.shift(1).fillna(0.0)
    turnover = (weights - prior).abs().sum(axis=1) / 2.0
    gross_returns = (weights * returns).sum(axis=1)
    net_returns = gross_returns - turnover * (float(switching_cost_bps) / 10_000.0)
    return {
        "returns": net_returns,
        "gross_returns": gross_returns,
        "weights": weights,
        "turnover": turnover,
        "total_turnover": float(turnover.sum()),
        "average_gross_exposure": float(gross.mean()),
        "cash_fraction": float((1.0 - gross).clip(lower=0.0).mean()),
    }


def total_return_pct(values: pd.Series) -> float:
    clean = values.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    return float(((1.0 + clean).prod() - 1.0) * 100.0)


def maximum_drawdown_pct(values: pd.Series) -> float:
    clean = values.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    equity = (1.0 + clean).cumprod()
    if equity.empty:
        return 0.0
    return float(abs((equity / equity.cummax() - 1.0).min()) * 100.0)


def sharpe_ratio(values: pd.Series) -> float | None:
    clean = values.replace([np.inf, -np.inf], np.nan).dropna()
    if len(clean) < 2:
        return None
    deviation = float(clean.std(ddof=0))
    return float(clean.mean() / deviation * math.sqrt(252.0)) if deviation > 0 else None


def monthly_return_map(values: pd.Series) -> dict[str, float]:
    clean = values.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    return {
        str(period): float(((1.0 + group).prod() - 1.0) * 100.0)
        for period, group in clean.groupby(clean.index.to_period("M"))
    }


def best_month_exclusion_positive(values: pd.Series) -> bool:
    monthly = monthly_return_map(values)
    if len(monthly) < 2:
        return False
    best = max(monthly, key=monthly.get)
    remaining = [value / 100.0 for key, value in monthly.items() if key != best]
    return bool(remaining and (np.prod([1.0 + value for value in remaining]) - 1.0) > 0.0)


def block_sign_flip_p_value(
    values: pd.Series,
    *,
    seed: int,
    block_size: int = 10,
    simulations: int = 4096,
) -> float:
    clean = values.replace([np.inf, -np.inf], np.nan).dropna().to_numpy(dtype=float)
    if len(clean) < max(30, block_size * 2) or float(np.mean(clean)) <= 0:
        return 1.0
    blocks = [clean[index : index + block_size] for index in range(0, len(clean), block_size)]
    observed = float(np.mean(clean))
    rng = np.random.default_rng(seed)
    exceed = 1
    for _ in range(simulations):
        signs = rng.choice(np.array([-1.0, 1.0]), size=len(blocks))
        simulated = np.concatenate([block * sign for block, sign in zip(blocks, signs)])
        if float(np.mean(simulated)) >= observed:
            exceed += 1
    return exceed / (simulations + 1)


def current_selection(decisions: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    return dict(decisions[-1]) if decisions else None
