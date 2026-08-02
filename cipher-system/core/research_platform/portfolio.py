from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

import numpy as np
from scipy.optimize import linprog, minimize

from .hashing import stable_id
from .models import AuditEvent, PromotionState, utc_now
from .registry import ResearchRegistry, RegistryNotFoundError


@dataclass(frozen=True)
class PortfolioAsset:
    symbol: str
    strategy_id: str
    expected_return: float
    maximum_weight: float
    sector: str
    correlation_bucket: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 0 <= self.maximum_weight <= 1:
            raise ValueError("maximum_weight must be in [0, 1]")
        object.__setattr__(self, "symbol", self.symbol.upper())
        object.__setattr__(self, "metadata", dict(self.metadata))


@dataclass(frozen=True)
class PortfolioOptimizationPolicy:
    objective: str = "minimum_variance"
    risk_aversion: float = 1.0
    cvar_alpha: float = 0.95
    maximum_sector_weight: float = 0.5
    maximum_correlation_bucket_weight: float = 0.5
    minimum_cash_weight: float = 0.0
    covariance_shrinkage: float = 0.1
    required_promotion_state: PromotionState = PromotionState.PROSPECTIVE_SHADOW

    def __post_init__(self) -> None:
        if self.objective not in {"equal_weight", "inverse_volatility", "minimum_variance", "mean_variance", "cvar"}:
            raise ValueError("unsupported portfolio objective")
        if not 0 <= self.minimum_cash_weight < 1:
            raise ValueError("minimum_cash_weight must be in [0, 1)")
        if not 0 < self.cvar_alpha < 1:
            raise ValueError("cvar_alpha must be in (0, 1)")
        if not 0 <= self.covariance_shrinkage <= 1:
            raise ValueError("covariance_shrinkage must be in [0, 1]")


@dataclass(frozen=True)
class PortfolioProposal:
    proposal_id: str
    generated_at: datetime
    objective: str
    weights: Mapping[str, float]
    cash_weight: float
    expected_return: float
    expected_volatility: float
    cvar: float | None
    constraints: Mapping[str, Any]
    diagnostics: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "generated_at": self.generated_at.isoformat(),
            "objective": self.objective,
            "weights": dict(self.weights),
            "cash_weight": self.cash_weight,
            "expected_return": self.expected_return,
            "expected_volatility": self.expected_volatility,
            "cvar": self.cvar,
            "constraints": dict(self.constraints),
            "diagnostics": dict(self.diagnostics),
            "simulation_only": True,
            "order_intents": [],
            "broker_actions": [],
        }


class PortfolioOptimizationError(RuntimeError):
    pass


class DeterministicPortfolioOptimizer:
    """Produce simulation-only target weights from promoted strategy candidates."""

    def __init__(self, registry: ResearchRegistry, policy: PortfolioOptimizationPolicy):
        self.registry = registry
        self.policy = policy

    def optimize(
        self,
        assets: Sequence[PortfolioAsset],
        historical_returns: np.ndarray,
        *,
        as_of: datetime,
    ) -> PortfolioProposal:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        if not assets:
            raise PortfolioOptimizationError("assets cannot be empty")
        returns = np.asarray(historical_returns, dtype=float)
        if returns.ndim != 2 or returns.shape[1] != len(assets):
            raise ValueError("historical_returns must be T x N and match assets")
        if returns.shape[0] < 2:
            raise ValueError("at least two return observations are required")
        if not np.isfinite(returns).all():
            raise ValueError("historical_returns must be finite")
        self._validate_promotions(assets)
        investable = 1.0 - self.policy.minimum_cash_weight
        max_weights = np.asarray([asset.maximum_weight for asset in assets], dtype=float)
        if max_weights.sum() + 1e-12 < investable:
            raise PortfolioOptimizationError("asset maximum weights cannot satisfy the investable target")
        expected = np.asarray([asset.expected_return for asset in assets], dtype=float)
        covariance = _shrunk_covariance(returns, self.policy.covariance_shrinkage)
        group_constraints = self._group_constraints(assets)
        if self.policy.objective == "equal_weight":
            weights = self._bounded_equal(max_weights, investable, group_constraints)
            solver = "deterministic_bounded_equal"
        elif self.policy.objective == "inverse_volatility":
            volatility = np.sqrt(np.maximum(np.diag(covariance), 1e-12))
            raw = 1.0 / volatility
            weights = self._project(raw, max_weights, investable, group_constraints)
            solver = "slsqp_projection"
        elif self.policy.objective == "cvar":
            weights = self._cvar(returns, max_weights, investable, group_constraints)
            solver = "scipy_linprog_highs"
        else:
            weights = self._quadratic(expected, covariance, max_weights, investable, group_constraints)
            solver = "scipy_slsqp"
        portfolio_returns = returns @ weights
        proposal_payload = {
            "as_of": as_of.astimezone(timezone.utc).isoformat(),
            "objective": self.policy.objective,
            "assets": [asset.symbol for asset in assets],
            "weights": [round(float(value), 12) for value in weights],
            "policy": self.policy.__dict__,
        }
        proposal = PortfolioProposal(
            proposal_id=stable_id("portfolio_proposal", proposal_payload),
            generated_at=utc_now(),
            objective=self.policy.objective,
            weights={asset.symbol: round(float(weight), 12) for asset, weight in zip(assets, weights)},
            cash_weight=round(1.0 - float(weights.sum()), 12),
            expected_return=float(expected @ weights),
            expected_volatility=float(np.sqrt(max(0.0, weights @ covariance @ weights))),
            cvar=_empirical_cvar(portfolio_returns, self.policy.cvar_alpha),
            constraints={
                "maximum_sector_weight": self.policy.maximum_sector_weight,
                "maximum_correlation_bucket_weight": self.policy.maximum_correlation_bucket_weight,
                "minimum_cash_weight": self.policy.minimum_cash_weight,
                "asset_maximum_weights": {asset.symbol: asset.maximum_weight for asset in assets},
            },
            diagnostics={
                "solver": solver,
                "return_observations": returns.shape[0],
                "covariance_shrinkage": self.policy.covariance_shrinkage,
                "constraint_residuals": self._constraint_residuals(assets, weights),
                "trade_authority": False,
                "sizing_authority": "simulation_proposal_only",
            },
        )
        self.registry.audit(
            AuditEvent(
                event_type="PORTFOLIO_PROPOSAL_CREATED",
                entity_type="portfolio_proposal",
                entity_id=proposal.proposal_id,
                occurred_at=proposal.generated_at,
                payload=proposal.to_dict(),
            )
        )
        return proposal

    def _validate_promotions(self, assets: Sequence[PortfolioAsset]) -> None:
        for asset in assets:
            try:
                state = self.registry.current_state(asset.strategy_id)
            except RegistryNotFoundError as exc:
                raise PortfolioOptimizationError(f"strategy not registered: {asset.strategy_id}") from exc
            if _state_rank(state) < _state_rank(self.policy.required_promotion_state):
                raise PortfolioOptimizationError(
                    f"strategy {asset.strategy_id} is {state.value}, below {self.policy.required_promotion_state.value}"
                )

    def _group_constraints(self, assets: Sequence[PortfolioAsset]) -> list[tuple[np.ndarray, float]]:
        constraints: list[tuple[np.ndarray, float]] = []
        for attribute, maximum in (
            ("sector", self.policy.maximum_sector_weight),
            ("correlation_bucket", self.policy.maximum_correlation_bucket_weight),
        ):
            groups = sorted({getattr(asset, attribute) for asset in assets})
            for group in groups:
                mask = np.asarray([1.0 if getattr(asset, attribute) == group else 0.0 for asset in assets])
                constraints.append((mask, maximum))
        return constraints

    def _quadratic(
        self,
        expected: np.ndarray,
        covariance: np.ndarray,
        max_weights: np.ndarray,
        investable: float,
        groups: list[tuple[np.ndarray, float]],
    ) -> np.ndarray:
        def objective(weights: np.ndarray) -> float:
            variance = float(weights @ covariance @ weights)
            if self.policy.objective == "mean_variance":
                return self.policy.risk_aversion * variance - float(expected @ weights)
            return variance

        constraints = [{"type": "eq", "fun": lambda w: float(w.sum() - investable)}]
        constraints.extend(
            {"type": "ineq", "fun": lambda w, mask=mask, maximum=maximum: float(maximum - mask @ w)}
            for mask, maximum in groups
        )
        initial = self._bounded_equal(max_weights, investable, groups)
        result = minimize(
            objective,
            initial,
            method="SLSQP",
            bounds=[(0.0, float(value)) for value in max_weights],
            constraints=constraints,
            options={"maxiter": 2000, "ftol": 1e-12},
        )
        if not result.success:
            raise PortfolioOptimizationError(f"portfolio optimization failed: {result.message}")
        return _clean_weights(result.x, investable)

    def _project(
        self,
        raw: np.ndarray,
        max_weights: np.ndarray,
        investable: float,
        groups: list[tuple[np.ndarray, float]],
    ) -> np.ndarray:
        target = raw / raw.sum() * investable
        constraints = [{"type": "eq", "fun": lambda w: float(w.sum() - investable)}]
        constraints.extend(
            {"type": "ineq", "fun": lambda w, mask=mask, maximum=maximum: float(maximum - mask @ w)}
            for mask, maximum in groups
        )
        result = minimize(
            lambda weights: float(np.square(weights - target).sum()),
            self._bounded_equal(max_weights, investable, groups),
            method="SLSQP",
            bounds=[(0.0, float(value)) for value in max_weights],
            constraints=constraints,
            options={"maxiter": 1000, "ftol": 1e-12},
        )
        if not result.success:
            raise PortfolioOptimizationError(f"weight projection failed: {result.message}")
        return _clean_weights(result.x, investable)

    def _cvar(
        self,
        returns: np.ndarray,
        max_weights: np.ndarray,
        investable: float,
        groups: list[tuple[np.ndarray, float]],
    ) -> np.ndarray:
        scenarios, assets = returns.shape
        # Variables: weights[N], VaR z[1], excess losses u[T].
        variable_count = assets + 1 + scenarios
        objective = np.zeros(variable_count)
        objective[assets] = 1.0
        objective[assets + 1 :] = 1.0 / ((1.0 - self.policy.cvar_alpha) * scenarios)
        a_ub: list[np.ndarray] = []
        b_ub: list[float] = []
        for index in range(scenarios):
            row = np.zeros(variable_count)
            row[:assets] = -returns[index]
            row[assets] = -1.0
            row[assets + 1 + index] = -1.0
            a_ub.append(row)
            b_ub.append(0.0)
        for mask, maximum in groups:
            row = np.zeros(variable_count)
            row[:assets] = mask
            a_ub.append(row)
            b_ub.append(maximum)
        a_eq = np.zeros((1, variable_count))
        a_eq[0, :assets] = 1.0
        bounds = [(0.0, float(value)) for value in max_weights] + [(None, None)] + [(0.0, None)] * scenarios
        result = linprog(
            objective,
            A_ub=np.asarray(a_ub),
            b_ub=np.asarray(b_ub),
            A_eq=a_eq,
            b_eq=np.asarray([investable]),
            bounds=bounds,
            method="highs",
        )
        if not result.success:
            raise PortfolioOptimizationError(f"CVaR optimization failed: {result.message}")
        return _clean_weights(result.x[:assets], investable)

    def _bounded_equal(
        self,
        max_weights: np.ndarray,
        investable: float,
        groups: list[tuple[np.ndarray, float]],
    ) -> np.ndarray:
        return self._project_simple(np.ones_like(max_weights), max_weights, investable, groups)

    def _project_simple(
        self,
        raw: np.ndarray,
        max_weights: np.ndarray,
        investable: float,
        groups: list[tuple[np.ndarray, float]],
    ) -> np.ndarray:
        target = raw / raw.sum() * investable
        constraints = [{"type": "eq", "fun": lambda w: float(w.sum() - investable)}]
        constraints.extend(
            {"type": "ineq", "fun": lambda w, mask=mask, maximum=maximum: float(maximum - mask @ w)}
            for mask, maximum in groups
        )
        result = minimize(
            lambda weights: float(np.square(weights - target).sum()),
            np.minimum(target, max_weights),
            method="SLSQP",
            bounds=[(0.0, float(value)) for value in max_weights],
            constraints=constraints,
            options={"maxiter": 1000, "ftol": 1e-12},
        )
        if not result.success:
            raise PortfolioOptimizationError(f"bounded allocation failed: {result.message}")
        return _clean_weights(result.x, investable)

    def _constraint_residuals(self, assets: Sequence[PortfolioAsset], weights: np.ndarray) -> dict[str, Any]:
        sectors: dict[str, float] = {}
        buckets: dict[str, float] = {}
        for asset, weight in zip(assets, weights):
            sectors[asset.sector] = sectors.get(asset.sector, 0.0) + float(weight)
            buckets[asset.correlation_bucket] = buckets.get(asset.correlation_bucket, 0.0) + float(weight)
        return {
            "weight_sum": float(weights.sum()),
            "sector_weights": sectors,
            "correlation_bucket_weights": buckets,
            "maximum_sector_excess": max(
                [value - self.policy.maximum_sector_weight for value in sectors.values()] + [0.0]
            ),
            "maximum_correlation_bucket_excess": max(
                [value - self.policy.maximum_correlation_bucket_weight for value in buckets.values()] + [0.0]
            ),
        }


def _shrunk_covariance(returns: np.ndarray, shrinkage: float) -> np.ndarray:
    sample = np.cov(returns, rowvar=False, ddof=1)
    sample = np.atleast_2d(sample)
    diagonal = np.diag(np.diag(sample))
    covariance = (1.0 - shrinkage) * sample + shrinkage * diagonal
    return covariance + np.eye(covariance.shape[0]) * 1e-12


def _clean_weights(weights: np.ndarray, target_sum: float) -> np.ndarray:
    result = np.asarray(weights, dtype=float)
    result[np.abs(result) < 1e-12] = 0.0
    if not np.isfinite(result).all() or (result < -1e-10).any():
        raise PortfolioOptimizationError("optimizer returned invalid weights")
    residual = target_sum - float(result.sum())
    if abs(residual) > 1e-7:
        raise PortfolioOptimizationError("optimizer weights do not satisfy the capital constraint")
    return result


def _empirical_cvar(portfolio_returns: np.ndarray, alpha: float) -> float | None:
    if portfolio_returns.size == 0:
        return None
    losses = -np.asarray(portfolio_returns, dtype=float)
    var = float(np.quantile(losses, alpha))
    tail = losses[losses >= var]
    return float(tail.mean()) if tail.size else var


def _state_rank(state: PromotionState) -> int:
    order = (
        PromotionState.IDEA,
        PromotionState.SPECIFIED,
        PromotionState.DATA_VALIDATED,
        PromotionState.FAST_BACKTESTED,
        PromotionState.WALK_FORWARD_PASSED,
        PromotionState.LEAN_REPLICATED,
        PromotionState.PROSPECTIVE_SHADOW,
        PromotionState.PAPER_ELIGIBLE,
        PromotionState.LIVE_REVIEW_REQUIRED,
    )
    if state in {PromotionState.REJECTED, PromotionState.RETIRED}:
        return -1
    return order.index(state)
