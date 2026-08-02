from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

import numpy as np

from .artifact_store import ArtifactReference, ArtifactStore
from .hashing import stable_id
from .models import AllowedUse, AuditEvent, FeatureSpec, utc_now
from .registry import ResearchRegistry

BASE_COLUMNS = frozenset({"open", "high", "low", "close", "volume", "vwap"})
SAFE_FUNCTIONS = frozenset(
    {
        "abs",
        "clip",
        "ema",
        "lag",
        "log",
        "pct_change",
        "rolling_max",
        "rolling_mean",
        "rolling_min",
        "rolling_std",
        "sqrt",
        "zscore",
    }
)


class UnsafeFactorError(ValueError):
    pass


@dataclass(frozen=True)
class FactorCandidate:
    name: str
    version: str
    expression: str
    hypothesis: str
    expected_direction: str
    availability_lag_seconds: int
    missing_value_policy: str
    allowed_use: AllowedUse = AllowedUse.CONTEXT
    metadata: Mapping[str, Any] = field(default_factory=dict)
    candidate_id: str = ""

    def __post_init__(self) -> None:
        if self.availability_lag_seconds < 0:
            raise ValueError("availability_lag_seconds cannot be negative")
        payload = {
            "name": self.name,
            "version": self.version,
            "expression": self.expression,
            "hypothesis": self.hypothesis,
            "expected_direction": self.expected_direction,
            "availability_lag_seconds": self.availability_lag_seconds,
            "missing_value_policy": self.missing_value_policy,
            "allowed_use": self.allowed_use.value,
            "metadata": dict(self.metadata),
        }
        object.__setattr__(self, "metadata", dict(self.metadata))
        object.__setattr__(self, "candidate_id", self.candidate_id or stable_id("factor_candidate", payload))

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "name": self.name,
            "version": self.version,
            "expression": self.expression,
            "hypothesis": self.hypothesis,
            "expected_direction": self.expected_direction,
            "availability_lag_seconds": self.availability_lag_seconds,
            "missing_value_policy": self.missing_value_policy,
            "allowed_use": self.allowed_use.value,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class CompiledFactor:
    candidate: FactorCandidate
    tree_dump: str
    required_columns: tuple[str, ...]
    implementation_hash: str

    def evaluate(self, columns: Mapping[str, Sequence[float] | np.ndarray]) -> np.ndarray:
        missing = set(self.required_columns) - set(columns)
        if missing:
            raise KeyError(f"factor inputs missing columns: {sorted(missing)}")
        arrays = {name: np.asarray(columns[name], dtype=float) for name in self.required_columns}
        lengths = {array.shape[0] for array in arrays.values()}
        if len(lengths) != 1:
            raise ValueError("factor input columns must have equal length")
        tree = ast.parse(self.candidate.expression, mode="eval")
        result = _evaluate_node(tree.body, arrays)
        result = np.asarray(result, dtype=float)
        if result.ndim == 0:
            result = np.full(next(iter(lengths), 1), float(result), dtype=float)
        if result.ndim != 1:
            raise ValueError("factor expression must produce a one-dimensional series")
        return result


class SafeFactorCompiler:
    """Compile a restricted raw-OHLCV factor DSL without Python eval."""

    def compile(self, candidate: FactorCandidate) -> CompiledFactor:
        try:
            tree = ast.parse(candidate.expression, mode="eval")
        except SyntaxError as exc:
            raise UnsafeFactorError(f"invalid factor expression: {exc}") from exc
        validator = _FactorValidator()
        validator.visit(tree)
        required = tuple(sorted(validator.required_columns))
        if not required:
            raise UnsafeFactorError("factor must reference at least one raw market column")
        tree_dump = ast.dump(tree, annotate_fields=True, include_attributes=False)
        implementation_hash = stable_id(
            "factor_impl",
            {
                "candidate_id": candidate.candidate_id,
                "tree": tree_dump,
                "required_columns": required,
                "dsl_version": 1,
            },
            length=64,
        )
        return CompiledFactor(candidate, tree_dump, required, implementation_hash)


class FactorResearchService:
    def __init__(self, registry: ResearchRegistry, artifacts: ArtifactStore):
        self.registry = registry
        self.artifacts = artifacts
        self.compiler = SafeFactorCompiler()

    def register_candidate(self, candidate: FactorCandidate) -> tuple[CompiledFactor, FeatureSpec, ArtifactReference]:
        if candidate.allowed_use != AllowedUse.CONTEXT:
            raise UnsafeFactorError(
                "new factor candidates must start as context-only; allowed-use elevation requires separate validated governance"
            )
        compiled = self.compiler.compile(candidate)
        spec = FeatureSpec(
            name=candidate.name,
            version=candidate.version,
            inputs=compiled.required_columns,
            lookback=_infer_lookback(candidate.expression),
            availability_lag_seconds=candidate.availability_lag_seconds,
            missing_value_policy=candidate.missing_value_policy,
            allowed_use=candidate.allowed_use,
            implementation_hash=compiled.implementation_hash,
            leakage_checks={
                "raw_columns_only": True,
                "factor_of_factor_forbidden": True,
                "negative_lag_forbidden": True,
                "unsafe_python_forbidden": True,
                "dsl_version": 1,
            },
            description=candidate.hypothesis,
        )
        self.registry.register_feature(spec)
        artifact = self.artifacts.put_json(
            {
                "candidate": candidate.to_dict(),
                "feature_spec": spec.to_dict(),
                "tree_dump": compiled.tree_dump,
                "required_columns": list(compiled.required_columns),
            },
            metadata={"kind": "factor_candidate", "candidate_id": candidate.candidate_id},
        )
        self.registry.register_artifact(artifact.to_dict())
        self.registry.audit(
            AuditEvent(
                event_type="FACTOR_CANDIDATE_REGISTERED",
                entity_type="feature",
                entity_id=spec.feature_id,
                occurred_at=utc_now(),
                payload={
                    "candidate_id": candidate.candidate_id,
                    "artifact_id": artifact.artifact_id,
                    "allowed_use": candidate.allowed_use.value,
                    "raw_columns_only": True,
                },
            )
        )
        return compiled, spec, artifact


class _FactorValidator(ast.NodeVisitor):
    ALLOWED_NODES = (
        ast.Expression,
        ast.BinOp,
        ast.UnaryOp,
        ast.Call,
        ast.Name,
        ast.Load,
        ast.Constant,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.Pow,
        ast.Mod,
        ast.USub,
        ast.UAdd,
    )

    def __init__(self) -> None:
        self.required_columns: set[str] = set()

    def generic_visit(self, node: ast.AST) -> None:
        if not isinstance(node, self.ALLOWED_NODES):
            raise UnsafeFactorError(f"factor node is forbidden: {type(node).__name__}")
        super().generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in BASE_COLUMNS:
            self.required_columns.add(node.id)
            return
        if node.id in SAFE_FUNCTIONS:
            return
        raise UnsafeFactorError(f"unknown or derived factor name: {node.id}")

    def visit_Call(self, node: ast.Call) -> None:
        if not isinstance(node.func, ast.Name) or node.func.id not in SAFE_FUNCTIONS:
            raise UnsafeFactorError("only whitelisted factor functions are allowed")
        if node.keywords:
            raise UnsafeFactorError("factor functions do not accept keyword arguments")
        for argument in node.args:
            self.visit(argument)
        _validate_call_constants(node.func.id, node.args)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise UnsafeFactorError("only finite numeric constants are allowed")
        if not np.isfinite(float(node.value)):
            raise UnsafeFactorError("factor constants must be finite")


def _validate_call_constants(name: str, args: list[ast.expr]) -> None:
    arity = {
        "abs": (1, 1),
        "sqrt": (1, 1),
        "log": (1, 1),
        "lag": (2, 2),
        "pct_change": (2, 2),
        "rolling_mean": (2, 2),
        "rolling_std": (2, 2),
        "rolling_min": (2, 2),
        "rolling_max": (2, 2),
        "ema": (2, 2),
        "zscore": (2, 2),
        "clip": (3, 3),
    }[name]
    if not arity[0] <= len(args) <= arity[1]:
        raise UnsafeFactorError(f"{name} expects {arity[0]} argument(s)")
    if name in {"lag", "pct_change", "rolling_mean", "rolling_std", "rolling_min", "rolling_max", "ema", "zscore"}:
        period_node = args[1]
        if not isinstance(period_node, ast.Constant) or not isinstance(period_node.value, int):
            raise UnsafeFactorError(f"{name} period must be an integer constant")
        minimum = 0 if name in {"lag", "pct_change"} else 1
        if period_node.value < minimum:
            raise UnsafeFactorError(f"{name} period cannot be less than {minimum}")
    if name == "clip":
        for node in args[1:]:
            if not isinstance(node, ast.Constant) or not isinstance(node.value, (int, float)):
                raise UnsafeFactorError("clip bounds must be numeric constants")
        if float(args[1].value) > float(args[2].value):
            raise UnsafeFactorError("clip lower bound cannot exceed upper bound")


def _evaluate_node(node: ast.AST, columns: Mapping[str, np.ndarray]) -> Any:
    if isinstance(node, ast.Name):
        return columns[node.id]
    if isinstance(node, ast.Constant):
        return float(node.value)
    if isinstance(node, ast.UnaryOp):
        value = _evaluate_node(node.operand, columns)
        if isinstance(node.op, ast.USub):
            return -value
        if isinstance(node.op, ast.UAdd):
            return value
    if isinstance(node, ast.BinOp):
        left = _evaluate_node(node.left, columns)
        right = _evaluate_node(node.right, columns)
        with np.errstate(all="ignore"):
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
            if isinstance(node.op, ast.Pow):
                return left**right
            if isinstance(node.op, ast.Mod):
                return left % right
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        values = [_evaluate_node(argument, columns) for argument in node.args]
        return _call(node.func.id, values)
    raise UnsafeFactorError(f"cannot evaluate node: {type(node).__name__}")


def _call(name: str, args: list[Any]) -> np.ndarray:
    series = np.asarray(args[0], dtype=float)
    if name == "abs":
        return np.abs(series)
    if name == "sqrt":
        with np.errstate(invalid="ignore"):
            return np.sqrt(series)
    if name == "log":
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.log(series)
    if name == "clip":
        return np.clip(series, float(args[1]), float(args[2]))
    period = int(args[1])
    if name == "lag":
        return _lag(series, period)
    if name == "pct_change":
        lagged = _lag(series, period)
        with np.errstate(divide="ignore", invalid="ignore"):
            return (series - lagged) / lagged
    if name == "rolling_mean":
        return _rolling(series, period, np.nanmean)
    if name == "rolling_std":
        return _rolling(series, period, lambda value: np.nanstd(value, ddof=1))
    if name == "rolling_min":
        return _rolling(series, period, np.nanmin)
    if name == "rolling_max":
        return _rolling(series, period, np.nanmax)
    if name == "ema":
        return _ema(series, period)
    if name == "zscore":
        mean = _rolling(series, period, np.nanmean)
        std = _rolling(series, period, lambda value: np.nanstd(value, ddof=1))
        with np.errstate(divide="ignore", invalid="ignore"):
            return (series - mean) / std
    raise UnsafeFactorError(f"unsupported function: {name}")


def _lag(values: np.ndarray, periods: int) -> np.ndarray:
    result = np.full(values.shape, np.nan, dtype=float)
    if periods == 0:
        return values.copy()
    if periods < len(values):
        result[periods:] = values[:-periods]
    return result


def _rolling(values: np.ndarray, window: int, reducer) -> np.ndarray:
    result = np.full(values.shape, np.nan, dtype=float)
    for index in range(window - 1, len(values)):
        sample = values[index - window + 1 : index + 1]
        if np.isfinite(sample).sum() == 0:
            continue
        result[index] = reducer(sample)
    return result


def _ema(values: np.ndarray, span: int) -> np.ndarray:
    result = np.full(values.shape, np.nan, dtype=float)
    alpha = 2.0 / (span + 1.0)
    previous = np.nan
    for index, value in enumerate(values):
        if not np.isfinite(value):
            result[index] = previous
            continue
        previous = value if not np.isfinite(previous) else alpha * value + (1.0 - alpha) * previous
        result[index] = previous
    return result


def _infer_lookback(expression: str) -> str:
    tree = ast.parse(expression, mode="eval")
    periods: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in {"lag", "pct_change", "rolling_mean", "rolling_std", "rolling_min", "rolling_max", "ema", "zscore"}:
                if len(node.args) > 1 and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, int):
                    periods.append(int(node.args[1].value))
    return f"{max(periods, default=1)} bars"
