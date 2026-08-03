"""Governed optional-engine adapters for local research only."""

from __future__ import annotations

import importlib.metadata
from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np

from .artifact_store import ArtifactReference, ArtifactStore
from .market_quality import HoldoutCohortEligibility


class EngineGateError(RuntimeError):
    pass


@dataclass(frozen=True)
class VectorBTScreenResult:
    total_return: float
    max_drawdown: float
    trade_count: int
    artifact: ArtifactReference


@dataclass(frozen=True)
class PriceOnlyVectorBTResult:
    """A price-only screen result that can enter the normal evidence chain.

    This adapter does not grant promotion.  It records the data-scope contract
    so later walk-forward and LEAN evidence can use the same promotion states
    without accidentally invoking the volume-sensitive Holdout C gate.
    """

    total_return: float
    max_drawdown: float
    trade_count: int
    artifact: ArtifactReference


def engine_versions() -> dict[str, str]:
    packages = {"vectorbt": "vectorbt", "qlib": "pyqlib", "riskfolio": "Riskfolio-Lib", "rdagent": "rdagent"}
    return {name: importlib.metadata.version(package) for name, package in packages.items()}


def screen_vectorbt_buy_and_hold(
    closes: Sequence[float],
    cohort: HoldoutCohortEligibility,
    artifacts: ArtifactStore,
) -> VectorBTScreenResult:
    """Run a non-promotable baseline only after the unchanged Holdout C gate."""

    if not cohort.eligible:
        raise EngineGateError(f"Holdout C gate is not cleared: {cohort.to_dict()['reasons']}")
    values = np.asarray(closes, dtype=float)
    if values.ndim != 1 or len(values) < 2 or not np.isfinite(values).all() or (values <= 0).any():
        raise ValueError("closes must contain at least two finite positive values")
    import vectorbt as vbt

    portfolio = vbt.Portfolio.from_holding(values)
    payload = {
        "engine": "VectorBT",
        "engine_version": importlib.metadata.version("vectorbt"),
        "cohort": cohort.to_dict(),
        "strategy": "buy_and_hold_baseline",
        "total_return": float(portfolio.total_return()),
        "max_drawdown": float(portfolio.max_drawdown()),
        "trade_count": int(portfolio.trades.count()),
        "promotion_eligible": False,
        "execution_authority": False,
    }
    artifact = artifacts.put_json(payload, metadata={"kind": "vectorbt_screen", "promotion_eligible": False})
    return VectorBTScreenResult(
        total_return=payload["total_return"],
        max_drawdown=payload["max_drawdown"],
        trade_count=payload["trade_count"],
        artifact=artifact,
    )


def screen_vectorbt_price_only_signal(
    closes: Sequence[float],
    entries: Sequence[bool],
    exits: Sequence[bool],
    artifacts: ArtifactStore,
    *,
    strategy_id: str,
    dataset_id: str,
) -> PriceOnlyVectorBTResult:
    """Run a price-only signal with an explicit no-volume contract."""

    values = np.asarray(closes, dtype=float)
    enter = np.asarray(entries, dtype=bool)
    leave = np.asarray(exits, dtype=bool)
    if values.ndim != 1 or len(values) < 2 or not np.isfinite(values).all() or (values <= 0).any():
        raise ValueError("closes must contain at least two finite positive values")
    if enter.shape != values.shape or leave.shape != values.shape:
        raise ValueError("entries and exits must match closes")
    if not strategy_id.strip() or not dataset_id.strip():
        raise ValueError("strategy_id and dataset_id are required")
    import vectorbt as vbt

    portfolio = vbt.Portfolio.from_signals(values, entries=enter, exits=leave, freq="1D")
    payload = {
        "engine": "VectorBT",
        "engine_version": importlib.metadata.version("vectorbt"),
        "strategy_id": strategy_id,
        "dataset_id": dataset_id,
        "data_scope": "price_only",
        "volume_features": False,
        "volume_evaluation": False,
        "total_return": float(portfolio.total_return()),
        "max_drawdown": float(portfolio.max_drawdown()),
        "trade_count": int(portfolio.trades.count()),
        "promotion_path": "same_ordered_states_as_other_validated_strategies",
        "promotion_eligible_now": False,
        "execution_authority": False,
    }
    artifact = artifacts.put_json(payload, metadata={"kind": "vectorbt_price_only_screen", "data_scope": "price_only"})
    return PriceOnlyVectorBTResult(
        total_return=payload["total_return"],
        max_drawdown=payload["max_drawdown"],
        trade_count=payload["trade_count"],
        artifact=artifact,
    )
