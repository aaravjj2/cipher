"""Governed exploratory regime allocator for fixed strategy components.

The allocator does not invent new component parameters.  It combines a small,
explicitly declared component set and uses only trailing component returns when
choosing weights.  Component strategies retain their own next-session-open
execution and slippage.  The allocator adds a second, explicit switching cost
when allocations change.

This module is research-only.  It has no broker, order, paper-execution, or
promotion authority.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from .hashing import stable_id


@dataclass(frozen=True)
class AllocatorSpec:
    name: str
    mode: str
    lookback: int = 252
    rebalance: int = 21
    top_k: int = 2
    objective: str = "sharpe"
    minimum_training_return: float = 0.0
    target_volatility: float | None = None
    component_subset: tuple[str, ...] = ()
    passive_component: str | None = None
    core_weight: float = 0.0

    @property
    def allocator_id(self) -> str:
        return stable_id(
            "allocator",
            {
                "name": self.name,
                "mode": self.mode,
                "lookback": self.lookback,
                "rebalance": self.rebalance,
                "top_k": self.top_k,
                "objective": self.objective,
                "minimum_training_return": self.minimum_training_return,
                "target_volatility": self.target_volatility,
                "component_subset": self.component_subset,
                "passive_component": self.passive_component,
                "core_weight": self.core_weight,
            },
        )


def default_allocator_specs(
    component_ids: Sequence[str],
    *,
    passive_component: str | None = None,
) -> tuple[AllocatorSpec, ...]:
    ordered = tuple(component_ids)
    if len(ordered) < 4:
        raise ValueError("at least four fixed components are required")
    specs = [
        AllocatorSpec("static_equal_all", "static", component_subset=ordered),
        AllocatorSpec(
            "static_trend_mean_reversion_blend",
            "static",
            component_subset=ordered[:4],
        ),
        AllocatorSpec("dynamic_126d_21d_top1_sharpe", "dynamic", 126, 21, 1, "sharpe", component_subset=ordered),
        AllocatorSpec("dynamic_126d_21d_top2_sharpe", "dynamic", 126, 21, 2, "sharpe", component_subset=ordered),
        AllocatorSpec("dynamic_252d_21d_top1_sharpe", "dynamic", 252, 21, 1, "sharpe", component_subset=ordered),
        AllocatorSpec("dynamic_252d_21d_top2_sharpe", "dynamic", 252, 21, 2, "sharpe", component_subset=ordered),
        AllocatorSpec("dynamic_252d_63d_top1_sharpe", "dynamic", 252, 63, 1, "sharpe", component_subset=ordered),
        AllocatorSpec("dynamic_252d_63d_top2_sharpe", "dynamic", 252, 63, 2, "sharpe", component_subset=ordered),
        AllocatorSpec("dynamic_504d_63d_top1_sharpe", "dynamic", 504, 63, 1, "sharpe", component_subset=ordered),
        AllocatorSpec("dynamic_504d_63d_top2_sharpe", "dynamic", 504, 63, 2, "sharpe", component_subset=ordered),
        AllocatorSpec("dynamic_252d_21d_top2_return_drawdown", "dynamic", 252, 21, 2, "return_drawdown", component_subset=ordered),
        AllocatorSpec("dynamic_252d_21d_top2_sortino", "dynamic", 252, 21, 2, "sortino", component_subset=ordered),
        AllocatorSpec(
            "dynamic_252d_21d_top2_sharpe_vol10",
            "dynamic",
            252,
            21,
            2,
            "sharpe",
            target_volatility=0.10,
            component_subset=ordered,
        ),
        AllocatorSpec(
            "dynamic_252d_21d_top2_sharpe_vol15",
            "dynamic",
            252,
            21,
            2,
            "sharpe",
            target_volatility=0.15,
            component_subset=ordered,
        ),
    ]
    if passive_component:
        combined = ordered + (passive_component,)
        specs.extend(
            [
                AllocatorSpec("benchmark_aware_126d_21d_top1_total_return", "dynamic", 126, 21, 1, "total_return", component_subset=combined, passive_component=passive_component),
                AllocatorSpec("benchmark_aware_252d_21d_top1_sharpe", "dynamic", 252, 21, 1, "sharpe", component_subset=combined, passive_component=passive_component),
                AllocatorSpec("benchmark_aware_252d_21d_top2_sharpe", "dynamic", 252, 21, 2, "sharpe", component_subset=combined, passive_component=passive_component),
                AllocatorSpec("benchmark_aware_504d_63d_top1_sharpe", "dynamic", 504, 63, 1, "sharpe", component_subset=combined, passive_component=passive_component),
                AllocatorSpec("passive_regime_252d_21d_top1_sharpe", "passive_regime", 252, 21, 1, "sharpe", component_subset=combined, passive_component=passive_component),
                AllocatorSpec("passive_regime_126d_21d_top1_return_drawdown", "passive_regime", 126, 21, 1, "return_drawdown", component_subset=combined, passive_component=passive_component),
                AllocatorSpec("core70_satellite30_252d_21d_top1_sharpe", "core_satellite", 252, 21, 1, "sharpe", component_subset=combined, passive_component=passive_component, core_weight=0.70),
                AllocatorSpec("core50_satellite50_252d_21d_top1_sharpe", "core_satellite", 252, 21, 1, "sharpe", component_subset=combined, passive_component=passive_component, core_weight=0.50),
            ]
        )
    return tuple(specs)


def equity_curve_to_returns(
    timestamps: Sequence[str],
    values: Sequence[float],
    *,
    initial_equity: float = 100_000.0,
) -> pd.Series:
    if len(timestamps) != len(values):
        raise ValueError("equity timestamps and values must have equal length")
    if not timestamps:
        return pd.Series(dtype=float)
    index = pd.to_datetime(list(timestamps), utc=True).tz_convert(None)
    equity = pd.Series([float(value) for value in values], index=index).sort_index()
    returns = equity.pct_change()
    returns.iloc[0] = float(equity.iloc[0] / initial_equity - 1.0)
    return returns.replace([np.inf, -np.inf], np.nan).fillna(0.0)


def _training_score(values: pd.Series, objective: str) -> float:
    clean = values.replace([np.inf, -np.inf], np.nan).dropna()
    if len(clean) < 20:
        return -math.inf
    cumulative = float((1.0 + clean).prod() - 1.0)
    if objective == "total_return":
        return cumulative
    if objective == "sharpe":
        std = float(clean.std(ddof=0))
        return float(clean.mean() / std * math.sqrt(252.0)) if std > 0 else -math.inf
    if objective == "sortino":
        downside = clean[clean < 0]
        deviation = float(np.sqrt(np.mean(np.square(downside)))) if len(downside) else 0.0
        return float(clean.mean() / deviation * math.sqrt(252.0)) if deviation > 0 else (999.0 if clean.mean() > 0 else -math.inf)
    if objective == "return_drawdown":
        equity = (1.0 + clean).cumprod()
        drawdown = float((equity / equity.cummax() - 1.0).min())
        return cumulative / abs(drawdown) if drawdown < 0 else (999.0 if cumulative > 0 else -math.inf)
    raise KeyError(f"unsupported allocator objective: {objective}")


def build_allocator_weights(
    component_returns: pd.DataFrame,
    spec: AllocatorSpec,
) -> pd.DataFrame:
    returns = component_returns.sort_index().replace([np.inf, -np.inf], np.nan).fillna(0.0)
    if returns.empty:
        return pd.DataFrame(index=returns.index, columns=returns.columns, dtype=float).fillna(0.0)
    if spec.component_subset:
        missing = sorted(set(spec.component_subset) - set(returns.columns))
        if missing:
            raise KeyError(f"allocator component subset is missing: {missing}")
        eligible_columns = list(spec.component_subset)
    else:
        eligible_columns = list(returns.columns)

    weights = pd.DataFrame(0.0, index=returns.index, columns=returns.columns)
    if spec.mode == "static":
        weights.loc[:, eligible_columns] = 1.0 / len(eligible_columns)
        return weights
    if spec.mode not in {"dynamic", "passive_regime", "core_satellite"}:
        raise KeyError(f"unsupported allocator mode: {spec.mode}")
    if spec.mode in {"passive_regime", "core_satellite"}:
        if not spec.passive_component or spec.passive_component not in eligible_columns:
            raise ValueError(f"{spec.mode} requires a declared passive component")
        if spec.mode == "core_satellite" and not 0.0 <= spec.core_weight <= 1.0:
            raise ValueError("core_weight must be between zero and one")

    held = pd.Series(0.0, index=returns.columns)
    for position, date in enumerate(returns.index):
        if position < spec.lookback:
            weights.loc[date] = held
            continue
        should_rebalance = (position - spec.lookback) % max(1, spec.rebalance) == 0
        if not should_rebalance:
            weights.loc[date] = held
            continue
        training = returns.iloc[position - spec.lookback : position][eligible_columns]
        passive = spec.passive_component
        active_columns = [component for component in eligible_columns if component != passive]
        if spec.mode == "passive_regime" and passive:
            passive_long = float((1.0 + training[passive]).prod() - 1.0)
            passive_short = float((1.0 + training[passive].tail(min(63, len(training)))).prod() - 1.0)
            if passive_long > 0.0 and passive_short > 0.0:
                held = pd.Series(0.0, index=returns.columns)
                held.loc[passive] = 1.0
                weights.loc[date] = held
                continue
        score_columns = active_columns if spec.mode in {"passive_regime", "core_satellite"} else eligible_columns
        scores: list[tuple[str, float, float]] = []
        for component in score_columns:
            values = training[component]
            cumulative = float((1.0 + values).prod() - 1.0)
            score = _training_score(values, spec.objective)
            if cumulative >= spec.minimum_training_return and math.isfinite(score) and score > 0:
                scores.append((component, score, cumulative))
        scores.sort(key=lambda row: (row[1], row[2], row[0]), reverse=True)
        selected = [row[0] for row in scores[: max(1, spec.top_k)]]
        held = pd.Series(0.0, index=returns.columns)
        if spec.mode == "core_satellite" and passive:
            held.loc[passive] = spec.core_weight
        if selected:
            scale = 1.0
            if spec.target_volatility is not None:
                equal = training[selected].mean(axis=1)
                realized = float(equal.std(ddof=0) * math.sqrt(252.0))
                if realized > 0:
                    scale = min(1.0, float(spec.target_volatility) / realized)
            satellite_budget = 1.0 - spec.core_weight if spec.mode == "core_satellite" else 1.0
            held.loc[selected] = satellite_budget * scale / len(selected)
            if spec.mode == "core_satellite" and passive and scale < 1.0:
                held.loc[passive] += satellite_budget * (1.0 - scale)
        elif spec.mode == "core_satellite" and passive:
            held.loc[passive] = 1.0
        weights.loc[date] = held
    return weights


def apply_allocator(
    component_returns: pd.DataFrame,
    weights: pd.DataFrame,
    *,
    switching_cost_bps: float,
) -> dict[str, Any]:
    returns = component_returns.reindex(index=weights.index, columns=weights.columns).fillna(0.0)
    weights = weights.reindex(index=returns.index, columns=returns.columns).fillna(0.0)
    gross = weights.abs().sum(axis=1)
    if bool((gross > 1.0 + 1e-10).any()):
        raise ValueError("allocator gross exposure exceeds one")
    prior = weights.shift(1).fillna(0.0)
    one_way_turnover = (weights - prior).abs().sum(axis=1) / 2.0
    gross_returns = (weights * returns).sum(axis=1)
    net_returns = gross_returns - one_way_turnover * (float(switching_cost_bps) / 10_000.0)
    return {
        "returns": net_returns,
        "gross_returns": gross_returns,
        "weights": weights,
        "turnover": one_way_turnover,
        "total_turnover": float(one_way_turnover.sum()),
        "average_gross_exposure": float(gross.mean()),
        "cash_fraction": float((1.0 - gross).clip(lower=0.0).mean()),
    }


def total_return(values: pd.Series) -> float:
    clean = values.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    return float(((1.0 + clean).prod() - 1.0) * 100.0)


def maximum_drawdown(values: pd.Series) -> float:
    clean = values.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    equity = (1.0 + clean).cumprod()
    if equity.empty:
        return 0.0
    return float(abs((equity / equity.cummax() - 1.0).min()) * 100.0)


def annualized_return(values: pd.Series) -> float:
    clean = values.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    if clean.empty:
        return 0.0
    years = len(clean) / 252.0
    compounded = float((1.0 + clean).prod())
    return float((compounded ** (1.0 / years) - 1.0) * 100.0) if years > 0 and compounded > 0 else -100.0


def sharpe(values: pd.Series) -> float | None:
    clean = values.replace([np.inf, -np.inf], np.nan).dropna()
    if len(clean) < 2:
        return None
    std = float(clean.std(ddof=0))
    return float(clean.mean() / std * math.sqrt(252.0)) if std > 0 else None


def profit_factor(values: pd.Series) -> float | None:
    clean = values.replace([np.inf, -np.inf], np.nan).dropna()
    gains = float(clean[clean > 0].sum())
    losses = float(-clean[clean < 0].sum())
    if losses <= 0:
        return 999.0 if gains > 0 else None
    return gains / losses


def fold_metrics(
    strategy: pd.Series,
    benchmark: pd.Series,
    folds: Mapping[str, tuple[str, str]],
) -> dict[str, dict[str, float]]:
    output: dict[str, dict[str, float]] = {}
    for name, (start, end) in folds.items():
        left = strategy.loc[(strategy.index >= pd.Timestamp(start)) & (strategy.index <= pd.Timestamp(end))]
        right = benchmark.reindex(left.index).fillna(0.0)
        strategy_return = total_return(left)
        benchmark_return = total_return(right)
        output[name] = {
            "strategy_return_pct": strategy_return,
            "benchmark_return_pct": benchmark_return,
            "excess_return_pct": strategy_return - benchmark_return,
            "sessions": int(len(left)),
        }
    return output


def sign_flip_p_value(values: pd.Series, *, seed: int, simulations: int = 4096) -> float:
    clean = values.replace([np.inf, -np.inf], np.nan).dropna().to_numpy(dtype=float)
    if len(clean) < 20 or float(np.mean(clean)) <= 0:
        return 1.0
    rng = np.random.default_rng(seed)
    observed = float(np.mean(clean))
    exceed = 1
    remaining = simulations
    while remaining:
        size = min(256, remaining)
        signs = rng.choice(np.array([-1.0, 1.0]), size=(size, len(clean)))
        simulated = np.mean(signs * clean, axis=1)
        exceed += int(np.sum(simulated >= observed))
        remaining -= size
    return exceed / (simulations + 1)


def block_sign_flip_p_value(
    values: pd.Series,
    *,
    seed: int,
    block_size: int = 21,
    simulations: int = 4096,
) -> float:
    """One-sided sign-flip test preserving within-block serial dependence."""
    clean = values.replace([np.inf, -np.inf], np.nan).dropna().to_numpy(dtype=float)
    if len(clean) < max(40, block_size * 2) or float(np.mean(clean)) <= 0:
        return 1.0
    blocks = [clean[index : index + block_size] for index in range(0, len(clean), block_size)]
    observed = float(np.mean(clean))
    rng = np.random.default_rng(seed)
    exceed = 1
    remaining = simulations
    while remaining:
        size = min(256, remaining)
        for _ in range(size):
            signs = rng.choice(np.array([-1.0, 1.0]), size=len(blocks))
            simulated = np.concatenate([block * sign for block, sign in zip(blocks, signs)])
            if float(np.mean(simulated)) >= observed:
                exceed += 1
        remaining -= size
    return exceed / (simulations + 1)


def holm_adjust(raw: Mapping[str, float]) -> dict[str, float]:
    ordered = sorted(raw.items(), key=lambda row: (row[1], row[0]))
    adjusted: dict[str, float] = {}
    running = 0.0
    size = len(ordered)
    for rank, (key, value) in enumerate(ordered):
        candidate = min(1.0, float(value) * (size - rank))
        running = max(running, candidate)
        adjusted[key] = running
    return adjusted


def best_month_exclusion_positive(values: pd.Series) -> bool:
    clean = values.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    monthly = clean.groupby(clean.index.to_period("M")).apply(lambda rows: float((1.0 + rows).prod() - 1.0))
    if len(monthly) < 2:
        return False
    remaining = monthly.drop(monthly.idxmax())
    return float((1.0 + remaining).prod() - 1.0) > 0.0


def component_selection_frequency(weights: pd.DataFrame) -> dict[str, float]:
    active = weights.abs() > 1e-12
    denominator = max(1, len(weights))
    return {column: float(active[column].sum() / denominator) for column in weights.columns}
