from __future__ import annotations

import json
import math
import platform
import sys
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from .artifact_store import ArtifactReference, ArtifactStore
from .hashing import stable_id
from .models import (
    EngineKind,
    ExperimentManifest,
    ExperimentResult,
    ExperimentVerdict,
    StrategySpec,
    utc_now,
)
from .registry import ResearchRegistry


@dataclass(frozen=True)
class TradeRecord:
    trade_id: str
    symbol: str
    direction: str
    entry_time: str
    exit_time: str | None
    entry_price: float
    exit_price: float | None
    quantity: float
    gross_pnl: float | None
    net_pnl: float | None
    return_pct: float | None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trade_id": self.trade_id,
            "symbol": self.symbol,
            "direction": self.direction,
            "entry_time": self.entry_time,
            "exit_time": self.exit_time,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "quantity": self.quantity,
            "gross_pnl": self.gross_pnl,
            "net_pnl": self.net_pnl,
            "return_pct": self.return_pct,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class EquityPoint:
    timestamp: str
    equity: float
    cash: float | None = None
    gross_exposure: float | None = None
    net_exposure: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class StandardBacktestOutput:
    trades: tuple[TradeRecord, ...]
    equity_curve: tuple[EquityPoint, ...]
    metrics: Mapping[str, Any]
    benchmark_metrics: Mapping[str, Any]
    regime_metrics: Mapping[str, Any]
    statistical_tests: Mapping[str, Any]
    quality_checks: Mapping[str, Any]
    exclusions: tuple[Mapping[str, Any], ...]
    assumptions: Mapping[str, Any]
    notes: tuple[str, ...] = ()

    def normalized_metrics(self) -> dict[str, Any]:
        result = dict(self.metrics)
        returns = [trade.return_pct for trade in self.trades if trade.return_pct is not None and math.isfinite(trade.return_pct)]
        net_pnls = [trade.net_pnl for trade in self.trades if trade.net_pnl is not None and math.isfinite(trade.net_pnl)]
        result.setdefault("trade_count", len(self.trades))
        if returns:
            result.setdefault("win_rate", sum(1 for value in returns if value > 0) / len(returns))
            result.setdefault("mean_trade_return_pct", sum(returns) / len(returns))
            gains = sum(value for value in returns if value > 0)
            losses = -sum(value for value in returns if value < 0)
            result.setdefault("profit_factor", gains / losses if losses > 0 else None)
        if net_pnls:
            result.setdefault("total_net_pnl", sum(net_pnls))
        if self.equity_curve:
            equities = [point.equity for point in self.equity_curve]
            result.setdefault("ending_equity", equities[-1])
            result.setdefault("maximum_drawdown_pct", _maximum_drawdown_pct(equities))
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "trades": [trade.to_dict() for trade in self.trades],
            "equity_curve": [point.to_dict() for point in self.equity_curve],
            "metrics": self.normalized_metrics(),
            "benchmark_metrics": dict(self.benchmark_metrics),
            "regime_metrics": dict(self.regime_metrics),
            "statistical_tests": dict(self.statistical_tests),
            "quality_checks": dict(self.quality_checks),
            "exclusions": [dict(item) for item in self.exclusions],
            "assumptions": dict(self.assumptions),
            "notes": list(self.notes),
        }


class ExperimentAdapter(Protocol):
    def run(self, manifest: ExperimentManifest) -> StandardBacktestOutput:
        ...


class CallableExperimentAdapter:
    def __init__(self, callback: Callable[[ExperimentManifest], StandardBacktestOutput]):
        self.callback = callback

    def run(self, manifest: ExperimentManifest) -> StandardBacktestOutput:
        output = self.callback(manifest)
        if not isinstance(output, StandardBacktestOutput):
            raise TypeError("experiment callback must return StandardBacktestOutput")
        return output


class LegacyJsonReportAdapter:
    """Normalize a historical Cipher JSON report into the common result contract."""

    def __init__(self, report: str | Path | Mapping[str, Any]):
        self.report = report

    def run(self, manifest: ExperimentManifest) -> StandardBacktestOutput:
        payload = self._load()
        trades_payload = payload.get("trades") or payload.get("trade_log") or payload.get("results") or []
        trades = tuple(self._trade(item, index) for index, item in enumerate(trades_payload) if isinstance(item, Mapping))
        equity_payload = payload.get("equity_curve") or payload.get("daily_equity") or []
        equity = tuple(self._equity(item) for item in equity_payload if isinstance(item, Mapping))
        metrics = dict(payload.get("metrics") or payload.get("summary") or {})
        for source, target in (
            ("total_pnl", "total_net_pnl"),
            ("max_drawdown_pct", "maximum_drawdown_pct"),
            ("mdd_pct", "maximum_drawdown_pct"),
            ("trades", "trade_count"),
        ):
            if source in payload and target not in metrics:
                metrics[target] = payload[source]
        quality = dict(payload.get("quality_checks") or {})
        quality.setdefault("legacy_import", True)
        quality.setdefault("point_in_time_validated", bool(payload.get("point_in_time_validated", False)))
        return StandardBacktestOutput(
            trades=trades,
            equity_curve=equity,
            metrics=metrics,
            benchmark_metrics=dict(payload.get("benchmark_metrics") or payload.get("benchmark") or {}),
            regime_metrics=dict(payload.get("regime_metrics") or payload.get("regimes") or {}),
            statistical_tests=dict(payload.get("statistical_tests") or payload.get("statistics") or {}),
            quality_checks=quality,
            exclusions=tuple(payload.get("exclusions") or ()),
            assumptions=dict(payload.get("assumptions") or {"source": "legacy_json_report"}),
            notes=tuple(payload.get("notes") or ()),
        )

    def _load(self) -> dict[str, Any]:
        if isinstance(self.report, Mapping):
            return dict(self.report)
        return json.loads(Path(self.report).read_text(encoding="utf-8"))

    @staticmethod
    def _trade(item: Mapping[str, Any], index: int) -> TradeRecord:
        symbol = str(item.get("symbol") or item.get("ticker") or "UNKNOWN").upper()
        entry_time = str(item.get("entry_time") or item.get("opened_at") or item.get("date") or "")
        exit_time = item.get("exit_time") or item.get("closed_at")
        entry_price = _float(item.get("entry_price") or item.get("entry") or item.get("entry_debit"), 0.0)
        exit_price_value = item.get("exit_price") or item.get("exit") or item.get("exit_credit")
        exit_price = _optional_float(exit_price_value)
        return_pct = _optional_float(item.get("return_pct") or item.get("pnl_pct") or item.get("trade_return_pct"))
        net_pnl = _optional_float(item.get("net_pnl") or item.get("pnl") or item.get("pnl_dollars"))
        gross_pnl = _optional_float(item.get("gross_pnl"))
        trade_id = str(item.get("trade_id") or item.get("id") or stable_id("legacy_trade", {"index": index, "item": dict(item)}))
        known = {
            "trade_id", "id", "symbol", "ticker", "direction", "side", "entry_time", "opened_at", "date",
            "exit_time", "closed_at", "entry_price", "entry", "entry_debit", "exit_price", "exit", "exit_credit",
            "quantity", "contracts", "gross_pnl", "net_pnl", "pnl", "pnl_dollars", "return_pct", "pnl_pct",
            "trade_return_pct",
        }
        return TradeRecord(
            trade_id=trade_id,
            symbol=symbol,
            direction=str(item.get("direction") or item.get("side") or "unknown"),
            entry_time=entry_time,
            exit_time=None if exit_time is None else str(exit_time),
            entry_price=entry_price,
            exit_price=exit_price,
            quantity=_float(item.get("quantity") or item.get("contracts"), 1.0),
            gross_pnl=gross_pnl,
            net_pnl=net_pnl,
            return_pct=return_pct,
            metadata={key: value for key, value in item.items() if key not in known},
        )

    @staticmethod
    def _equity(item: Mapping[str, Any]) -> EquityPoint:
        return EquityPoint(
            timestamp=str(item.get("timestamp") or item.get("date") or item.get("time") or ""),
            equity=_float(item.get("equity") or item.get("value"), 0.0),
            cash=_optional_float(item.get("cash")),
            gross_exposure=_optional_float(item.get("gross_exposure")),
            net_exposure=_optional_float(item.get("net_exposure")),
        )


@dataclass(frozen=True)
class GateEvaluation:
    verdict: ExperimentVerdict
    failures: tuple[str, ...]
    warnings: tuple[str, ...]
    checks: Mapping[str, Any]


class FastGateEvaluator:
    """Evaluate declared thresholds; never infer a strategy edge from Sharpe alone."""

    def evaluate(self, strategy: StrategySpec, output: StandardBacktestOutput) -> GateEvaluation:
        thresholds = dict(strategy.promotion_thresholds)
        metrics = output.normalized_metrics()
        quality = dict(output.quality_checks)
        failures: list[str] = []
        warnings: list[str] = []
        if quality.get("passed") is False:
            failures.append("quality_checks_failed")
        required_quality = thresholds.get("required_quality_checks") or ()
        for name in required_quality:
            if not bool(quality.get(name)):
                failures.append(f"missing_quality_check:{name}")
        self._minimum(metrics, "trade_count", thresholds.get("minimum_trades"), failures)
        self._minimum(metrics, "profit_factor", thresholds.get("minimum_profit_factor"), failures)
        self._minimum(metrics, "win_rate", thresholds.get("minimum_win_rate"), failures)
        self._maximum(metrics, "maximum_drawdown_pct", thresholds.get("maximum_drawdown_pct"), failures)
        self._minimum(metrics, "holdout_pnl", thresholds.get("minimum_holdout_pnl"), failures)
        adjusted_p = _find_adjusted_p_value(output.statistical_tests)
        maximum_adjusted_p = thresholds.get("maximum_adjusted_p_value")
        if maximum_adjusted_p is not None:
            if adjusted_p is None:
                failures.append("adjusted_p_value_missing")
            elif adjusted_p > float(maximum_adjusted_p):
                failures.append("adjusted_p_value_failed")
        if thresholds.get("require_best_trade_exclusion") and not quality.get("best_trade_exclusion_passed"):
            failures.append("best_trade_exclusion_failed")
        if thresholds.get("require_benchmark_outperformance") and not quality.get("benchmark_outperformance_passed"):
            failures.append("benchmark_outperformance_failed")
        if thresholds.get("require_walk_forward") and not quality.get("walk_forward_passed"):
            failures.append("walk_forward_failed")
        if output.exclusions:
            excluded_count = len(output.exclusions)
            total = max(1, int(metrics.get("trade_count") or 0) + excluded_count)
            ratio = excluded_count / total
            maximum_ratio = float(thresholds.get("maximum_exclusion_ratio", 1.0))
            if ratio > maximum_ratio:
                failures.append("exclusion_ratio_failed")
            elif ratio > 0.2:
                warnings.append("high_exclusion_ratio")
        if not output.trades:
            warnings.append("no_standardized_trade_records")
        if failures:
            verdict = ExperimentVerdict.FAIL
        elif warnings:
            verdict = ExperimentVerdict.CONDITIONAL_PASS
        else:
            verdict = ExperimentVerdict.PASS
        return GateEvaluation(
            verdict=verdict,
            failures=tuple(failures),
            warnings=tuple(warnings),
            checks={
                "thresholds": thresholds,
                "metrics": metrics,
                "adjusted_p_value": adjusted_p,
                "failures": failures,
                "warnings": warnings,
            },
        )

    @staticmethod
    def _minimum(metrics: Mapping[str, Any], key: str, threshold: Any, failures: list[str]) -> None:
        if threshold is None:
            return
        value = _optional_float(metrics.get(key))
        if value is None or value < float(threshold):
            failures.append(f"minimum_failed:{key}")

    @staticmethod
    def _maximum(metrics: Mapping[str, Any], key: str, threshold: Any, failures: list[str]) -> None:
        if threshold is None:
            return
        value = _optional_float(metrics.get(key))
        if value is None or value > float(threshold):
            failures.append(f"maximum_failed:{key}")


class ExperimentRunner:
    def __init__(
        self,
        *,
        registry: ResearchRegistry,
        artifact_store: ArtifactStore,
        gate_evaluator: FastGateEvaluator | None = None,
    ):
        self.registry = registry
        self.artifact_store = artifact_store
        self.gate_evaluator = gate_evaluator or FastGateEvaluator()

    def run(
        self,
        manifest: ExperimentManifest,
        *,
        strategy: StrategySpec,
        adapter: ExperimentAdapter,
    ) -> ExperimentResult:
        if strategy.strategy_id != manifest.strategy_id:
            raise ValueError("strategy and experiment manifest do not match")
        self.registry.begin_experiment(manifest)
        try:
            output = adapter.run(manifest)
            evaluation = self.gate_evaluator.evaluate(strategy, output)
            artifacts = self._persist_output(manifest, output, evaluation)
            quality = {
                **dict(output.quality_checks),
                "gate_failures": list(evaluation.failures),
                "gate_warnings": list(evaluation.warnings),
            }
            result = ExperimentResult(
                experiment_id=manifest.experiment_id,
                completed_at=utc_now(),
                verdict=evaluation.verdict,
                metrics=output.normalized_metrics(),
                statistical_tests=dict(output.statistical_tests),
                exclusions=output.exclusions,
                quality_checks=quality,
                artifacts=artifacts,
                notes=tuple(output.notes) + evaluation.warnings,
            )
        except Exception as exc:
            error_artifact = self.artifact_store.put_json(
                {
                    "experiment_id": manifest.experiment_id,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                },
                metadata={"kind": "experiment_error", "experiment_id": manifest.experiment_id},
            )
            self.registry.register_artifact(error_artifact.to_dict())
            self.registry.link_experiment_artifact(manifest.experiment_id, "error", error_artifact.artifact_id)
            result = ExperimentResult(
                experiment_id=manifest.experiment_id,
                completed_at=utc_now(),
                verdict=ExperimentVerdict.ERROR,
                metrics={},
                statistical_tests={},
                exclusions=(),
                quality_checks={"passed": False, "runtime_error": str(exc)},
                artifacts={"error": error_artifact.artifact_id},
                notes=(f"{type(exc).__name__}: {exc}",),
            )
        self.registry.complete_experiment(result)
        return result

    def _persist_output(
        self,
        manifest: ExperimentManifest,
        output: StandardBacktestOutput,
        evaluation: GateEvaluation,
    ) -> dict[str, str]:
        artifacts: dict[str, str] = {}
        items = {
            "standard_result": output.to_dict(),
            "trades": [item.to_dict() for item in output.trades],
            "equity_curve": [item.to_dict() for item in output.equity_curve],
            "gate_evaluation": {
                "verdict": evaluation.verdict.value,
                "failures": list(evaluation.failures),
                "warnings": list(evaluation.warnings),
                "checks": dict(evaluation.checks),
            },
        }
        for role, payload in items.items():
            artifact = self.artifact_store.put_json(
                payload,
                metadata={"kind": role, "experiment_id": manifest.experiment_id},
            )
            self.registry.register_artifact(artifact.to_dict())
            self.registry.link_experiment_artifact(manifest.experiment_id, role, artifact.artifact_id)
            artifacts[role] = artifact.artifact_id
        return artifacts


def runtime_environment_id(requirements_path: str | Path | None = None) -> str:
    requirements = ""
    if requirements_path and Path(requirements_path).exists():
        requirements = Path(requirements_path).read_text(encoding="utf-8")
    return stable_id(
        "runtime",
        {
            "python": sys.version,
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "requirements": requirements,
        },
    )


def _maximum_drawdown_pct(equities: Sequence[float]) -> float | None:
    if not equities:
        return None
    peak = equities[0]
    maximum = 0.0
    for value in equities:
        peak = max(peak, value)
        if peak > 0:
            maximum = max(maximum, (peak - value) / peak * 100.0)
    return maximum


def _find_adjusted_p_value(tests: Mapping[str, Any]) -> float | None:
    candidates = (
        "holm_adjusted_p_value",
        "adjusted_p_value",
        "bonferroni_p_value",
        "multiple_testing_adjusted_p_value",
    )
    for key in candidates:
        value = _optional_float(tests.get(key))
        if value is not None:
            return value
    for value in tests.values():
        if isinstance(value, Mapping):
            nested = _find_adjusted_p_value(value)
            if nested is not None:
                return nested
    return None


def _float(value: Any, default: float) -> float:
    parsed = _optional_float(value)
    return default if parsed is None else parsed


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None
