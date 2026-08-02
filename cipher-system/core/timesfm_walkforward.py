"""Leakage-aware TimesFM bridge for GEX feature forecasts.

The saved TimesFM model in this checkout was fine-tuned on the available GEX
archive.  It must never be used to generate historical predictions at or before
its training cutoff.  This module therefore requires an explicit model manifest
and produces forecasts only from observations strictly after ``trained_through``.

TimesFM is optional.  The data preparation, point-in-time validation, and output
schema remain testable without installing the heavyweight runtime.
"""
from __future__ import annotations

import importlib.util
import json
import math
import sqlite3
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Protocol, Sequence


UTC = timezone.utc
DEFAULT_BASE_MODEL_ID = "google/timesfm-2.5-200m-pytorch"


class TimesFMValidationError(ValueError):
    """Raised when model provenance or forecast inputs are unsafe."""


class TimesFMRuntimeError(RuntimeError):
    """Raised when optional TimesFM inference cannot run."""


def _aware_utc(value: datetime, *, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TimesFMValidationError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise TimesFMValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _finite(value: float, *, field_name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise TimesFMValidationError(f"{field_name} must be numeric") from exc
    if not math.isfinite(number):
        raise TimesFMValidationError(f"{field_name} must be finite")
    return number


def _positive_int(value: int, *, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise TimesFMValidationError(f"{field_name} must be a positive integer")
    return value


def parse_timestamp(value: str | datetime, *, field_name: str) -> datetime:
    if isinstance(value, datetime):
        return _aware_utc(value, field_name=field_name)
    raw = str(value or "").strip()
    if not raw:
        raise TimesFMValidationError(f"{field_name} is required")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TimesFMValidationError(f"invalid {field_name}: {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def next_weekday(day: date) -> date:
    candidate = day + timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


def next_session_availability(timestamp: datetime) -> datetime:
    timestamp = _aware_utc(timestamp, field_name="timestamp")
    return datetime.combine(next_weekday(timestamp.date()), time(14, 30), tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class TimesFMModelManifest:
    model_id: str
    weights_file: str
    trained_at: datetime
    trained_through: datetime
    context_len: int
    horizon_len: int
    training_split: str
    point_in_time_validation: bool
    allowed_use: str = "prospective_after_training_cutoff"
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        model_id = str(self.model_id or "").strip()
        weights_file = str(self.weights_file or "").strip()
        if not model_id:
            raise TimesFMValidationError("model_id is required")
        if not weights_file:
            raise TimesFMValidationError("weights_file is required")
        trained_at = _aware_utc(self.trained_at, field_name="trained_at")
        trained_through = _aware_utc(
            self.trained_through, field_name="trained_through"
        )
        if trained_through > trained_at:
            raise TimesFMValidationError("trained_through cannot exceed trained_at")
        context_len = _positive_int(self.context_len, field_name="context_len")
        horizon_len = _positive_int(self.horizon_len, field_name="horizon_len")
        split = str(self.training_split or "").strip()
        if not split:
            raise TimesFMValidationError("training_split is required")
        if not isinstance(self.point_in_time_validation, bool):
            raise TimesFMValidationError("point_in_time_validation must be boolean")
        allowed_use = str(self.allowed_use or "").strip()
        if allowed_use not in {
            "prospective_after_training_cutoff",
            "walk_forward_historical",
        }:
            raise TimesFMValidationError("unsupported allowed_use")
        object.__setattr__(self, "model_id", model_id)
        object.__setattr__(self, "weights_file", weights_file)
        object.__setattr__(self, "trained_at", trained_at)
        object.__setattr__(self, "trained_through", trained_through)
        object.__setattr__(self, "context_len", context_len)
        object.__setattr__(self, "horizon_len", horizon_len)
        object.__setattr__(self, "training_split", split)
        object.__setattr__(self, "allowed_use", allowed_use)
        object.__setattr__(self, "notes", tuple(str(x) for x in self.notes))

    @classmethod
    def from_json(cls, path: str | Path) -> "TimesFMModelManifest":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise TimesFMValidationError("TimesFM manifest must contain an object")
        return cls(
            model_id=str(payload.get("model_id") or ""),
            weights_file=str(payload.get("weights_file") or ""),
            trained_at=parse_timestamp(
                payload.get("trained_at"), field_name="trained_at"
            ),
            trained_through=parse_timestamp(
                payload.get("trained_through"), field_name="trained_through"
            ),
            context_len=int(payload.get("context_len") or 0),
            horizon_len=int(payload.get("horizon_len") or 0),
            training_split=str(payload.get("training_split") or ""),
            point_in_time_validation=payload.get("point_in_time_validation"),
            allowed_use=str(
                payload.get("allowed_use")
                or "prospective_after_training_cutoff"
            ),
            notes=tuple(str(x) for x in payload.get("notes", ()) or ()),
        )

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["trained_at"] = self.trained_at.isoformat()
        payload["trained_through"] = self.trained_through.isoformat()
        payload["notes"] = list(self.notes)
        return payload

    def permits_origin(self, origin: datetime) -> bool:
        origin = _aware_utc(origin, field_name="origin")
        if self.allowed_use == "walk_forward_historical":
            return self.point_in_time_validation and origin > self.trained_through
        return origin >= self.trained_at and origin > self.trained_through


@dataclass(frozen=True, slots=True)
class GexProfilePoint:
    timestamp: datetime
    underlying: str
    strike: float
    net_gex: float
    spot: float

    def __post_init__(self) -> None:
        timestamp = _aware_utc(self.timestamp, field_name="timestamp")
        underlying = str(self.underlying or "").strip().upper()
        if not underlying:
            raise TimesFMValidationError("underlying is required")
        strike = _finite(self.strike, field_name="strike")
        net_gex = _finite(self.net_gex, field_name="net_gex")
        spot = _finite(self.spot, field_name="spot")
        if strike <= 0 or spot <= 0:
            raise TimesFMValidationError("strike and spot must be positive")
        object.__setattr__(self, "timestamp", timestamp)
        object.__setattr__(self, "underlying", underlying)
        object.__setattr__(self, "strike", strike)
        object.__setattr__(self, "net_gex", net_gex)
        object.__setattr__(self, "spot", spot)


@dataclass(frozen=True, slots=True)
class ForecastEstimate:
    point: float
    lower: float | None = None
    upper: float | None = None

    def __post_init__(self) -> None:
        point = _finite(self.point, field_name="forecast point")
        lower = None if self.lower is None else _finite(
            self.lower, field_name="forecast lower"
        )
        upper = None if self.upper is None else _finite(
            self.upper, field_name="forecast upper"
        )
        if lower is not None and upper is not None and lower > upper:
            raise TimesFMValidationError("forecast lower cannot exceed upper")
        object.__setattr__(self, "point", point)
        object.__setattr__(self, "lower", lower)
        object.__setattr__(self, "upper", upper)


class GexForecaster(Protocol):
    def forecast(
        self,
        contexts: Sequence[Sequence[float]],
        *,
        horizon: int,
    ) -> Sequence[ForecastEstimate]: ...


@dataclass(frozen=True, slots=True)
class TimesFMFeatureForecast:
    source_date: date
    source_cutoff_at: datetime
    available_at: datetime
    underlying: str
    timesfm_gex_change_pct: float
    timesfm_confidence: float
    forecast_origin_count: int
    selected_strikes: tuple[float, ...]
    model_id: str
    trained_through: datetime
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        cutoff = _aware_utc(self.source_cutoff_at, field_name="source_cutoff_at")
        available = _aware_utc(self.available_at, field_name="available_at")
        if cutoff > available:
            raise TimesFMValidationError("source cutoff cannot exceed availability")
        underlying = str(self.underlying or "").strip().upper()
        if not underlying:
            raise TimesFMValidationError("underlying is required")
        change = _finite(
            self.timesfm_gex_change_pct, field_name="timesfm_gex_change_pct"
        )
        confidence = _finite(
            self.timesfm_confidence, field_name="timesfm_confidence"
        )
        if not 0 <= confidence <= 1:
            raise TimesFMValidationError("timesfm_confidence must be in [0, 1]")
        count = _positive_int(
            self.forecast_origin_count, field_name="forecast_origin_count"
        )
        strikes = tuple(
            _finite(value, field_name="selected strike")
            for value in self.selected_strikes
        )
        if len(strikes) != count:
            raise TimesFMValidationError(
                "forecast_origin_count must equal selected_strikes length"
            )
        trained_through = _aware_utc(
            self.trained_through, field_name="trained_through"
        )
        if cutoff <= trained_through:
            raise TimesFMValidationError(
                "forecast source cutoff must be after model training cutoff"
            )
        object.__setattr__(self, "source_cutoff_at", cutoff)
        object.__setattr__(self, "available_at", available)
        object.__setattr__(self, "underlying", underlying)
        object.__setattr__(self, "timesfm_gex_change_pct", change)
        object.__setattr__(self, "timesfm_confidence", confidence)
        object.__setattr__(self, "forecast_origin_count", count)
        object.__setattr__(self, "selected_strikes", strikes)
        object.__setattr__(self, "trained_through", trained_through)
        object.__setattr__(self, "notes", tuple(str(x) for x in self.notes))

    def to_csv_dict(self) -> dict[str, str | float | int]:
        return {
            "source_date": self.source_date.isoformat(),
            "source_cutoff_at": self.source_cutoff_at.isoformat(),
            "available_at": self.available_at.isoformat(),
            "underlying": self.underlying,
            "timesfm_gex_change_pct": self.timesfm_gex_change_pct,
            "timesfm_confidence": self.timesfm_confidence,
            "forecast_origin_count": self.forecast_origin_count,
            "selected_strikes": "|".join(str(x) for x in self.selected_strikes),
            "model_id": self.model_id,
            "trained_through": self.trained_through.isoformat(),
            "notes": " | ".join(self.notes),
        }


def load_gex_profile_points(
    db_path: str | Path,
    *,
    underlying: str,
) -> tuple[GexProfilePoint, ...]:
    source = Path(db_path)
    if not source.exists():
        raise FileNotFoundError(source)
    sql = """
        select
            c.captured_at,
            c.ticker,
            c.strike,
            sum(c.net_gex) as net_gex,
            max(s.spot) as spot
        from gex_strike_cells c
        join gex_snapshots s on s.id = c.snapshot_id
        where c.ticker = ?
        group by c.captured_at, c.ticker, c.strike
        order by c.captured_at, c.strike
    """
    points: list[GexProfilePoint] = []
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as db:
        for captured_at, ticker, strike, net_gex, spot in db.execute(
            sql, (underlying.upper(),)
        ):
            points.append(
                GexProfilePoint(
                    timestamp=parse_timestamp(
                        captured_at, field_name="captured_at"
                    ),
                    underlying=ticker,
                    strike=strike,
                    net_gex=net_gex,
                    spot=spot,
                )
            )
    return tuple(points)


def _confidence(estimates: Sequence[ForecastEstimate]) -> float:
    with_intervals = [
        estimate
        for estimate in estimates
        if estimate.lower is not None and estimate.upper is not None
    ]
    if not with_intervals:
        return 0.0
    predicted_scale = sum(abs(estimate.point) for estimate in with_intervals)
    interval_width = sum(
        abs(float(estimate.upper) - float(estimate.lower))
        for estimate in with_intervals
    )
    denominator = max(2.0 * predicted_scale, 1e-9)
    return max(0.0, min(1.0, 1.0 - interval_width / denominator))


def build_walk_forward_forecasts(
    points: Sequence[GexProfilePoint],
    *,
    manifest: TimesFMModelManifest,
    forecaster: GexForecaster,
    top_strikes: int = 20,
    minimum_origins: int = 5,
) -> tuple[TimesFMFeatureForecast, ...]:
    """Create aggregate GEX forecasts with strict origin/training separation."""
    top_strikes = _positive_int(top_strikes, field_name="top_strikes")
    minimum_origins = _positive_int(minimum_origins, field_name="minimum_origins")
    if not points:
        return ()
    underlyings = {point.underlying for point in points}
    if len(underlyings) != 1:
        raise TimesFMValidationError("points must contain exactly one underlying")

    by_timestamp: dict[datetime, list[GexProfilePoint]] = defaultdict(list)
    history_by_strike: dict[float, list[GexProfilePoint]] = defaultdict(list)
    for point in sorted(points, key=lambda row: (row.timestamp, row.strike)):
        by_timestamp[point.timestamp].append(point)
        history_by_strike[point.strike].append(point)

    outputs: list[TimesFMFeatureForecast] = []
    for origin in sorted(by_timestamp):
        if not manifest.permits_origin(origin):
            continue
        origin_rows = by_timestamp[origin]
        ranked = sorted(origin_rows, key=lambda row: abs(row.net_gex), reverse=True)
        selected: list[GexProfilePoint] = []
        contexts: list[list[float]] = []
        for point in ranked:
            history = [
                row
                for row in history_by_strike[point.strike]
                if row.timestamp <= origin
            ]
            if len(history) < manifest.context_len:
                continue
            selected.append(point)
            contexts.append(
                [row.net_gex for row in history[-manifest.context_len :]]
            )
            if len(selected) >= top_strikes:
                break
        if len(selected) < minimum_origins:
            continue

        estimates = tuple(
            forecaster.forecast(contexts, horizon=manifest.horizon_len)
        )
        if len(estimates) != len(selected):
            raise TimesFMValidationError(
                "forecaster result count does not match selected contexts"
            )
        current_abs = sum(abs(context[-1]) for context in contexts)
        predicted_abs = sum(abs(estimate.point) for estimate in estimates)
        if current_abs <= 0:
            continue
        change_pct = (predicted_abs / current_abs - 1.0) * 100.0
        outputs.append(
            TimesFMFeatureForecast(
                source_date=origin.date(),
                source_cutoff_at=origin,
                available_at=next_session_availability(origin),
                underlying=next(iter(underlyings)),
                timesfm_gex_change_pct=change_pct,
                timesfm_confidence=_confidence(estimates),
                forecast_origin_count=len(selected),
                selected_strikes=tuple(point.strike for point in selected),
                model_id=manifest.model_id,
                trained_through=manifest.trained_through,
                notes=(
                    "Aggregate forecast uses top absolute-GEX strikes",
                    "Forecast origin is strictly after model training cutoff",
                ),
            )
        )
    return tuple(outputs)


def runtime_status(
    *,
    model_dir: str | Path,
    manifest_path: str | Path,
    latest_observation: datetime | None = None,
) -> dict:
    model_dir = Path(model_dir)
    manifest_path = Path(manifest_path)
    package_available = importlib.util.find_spec("timesfm") is not None
    manifest: TimesFMModelManifest | None = None
    blockers: list[str] = []
    warnings: list[str] = []
    try:
        manifest = TimesFMModelManifest.from_json(manifest_path)
    except (FileNotFoundError, TimesFMValidationError, json.JSONDecodeError) as exc:
        blockers.append(f"invalid_or_missing_model_manifest: {exc}")

    weights_present = False
    prospective_origins_available = False
    if manifest is not None:
        weights_present = (model_dir / manifest.weights_file).is_file()
        if not weights_present:
            blockers.append("declared TimesFM weights file is absent")
        if latest_observation is not None:
            latest = _aware_utc(
                latest_observation, field_name="latest_observation"
            )
            prospective_origins_available = manifest.permits_origin(latest)
            if not prospective_origins_available:
                blockers.append(
                    "no GEX observation exists after the model training cutoff"
                )
        if not manifest.point_in_time_validation:
            warnings.append(
                "model validation used a non-temporal split; historical claims are blocked"
            )
    if not package_available:
        blockers.append("timesfm runtime package is not installed")

    return {
        "runtime_available": package_available,
        "manifest_present": manifest is not None,
        "weights_present": weights_present,
        "prospective_origins_available": prospective_origins_available,
        "ready_for_prospective_forecast": not blockers,
        "ready_for_historical_backtest": bool(
            not blockers
            and manifest is not None
            and manifest.allowed_use == "walk_forward_historical"
            and manifest.point_in_time_validation
        ),
        "manifest": manifest.to_dict() if manifest is not None else None,
        "blockers": blockers,
        "warnings": warnings,
    }


class TimesFM25Adapter:
    """Optional adapter for the official TimesFM 2.5 PyTorch package."""

    def __init__(
        self,
        *,
        model_dir: str | Path,
        manifest: TimesFMModelManifest,
    ) -> None:
        if importlib.util.find_spec("timesfm") is None:
            raise TimesFMRuntimeError("timesfm runtime package is not installed")
        try:
            import numpy as np
            import torch
            from timesfm import ForecastConfig, TimesFM_2p5_200M_torch
        except ImportError as exc:
            raise TimesFMRuntimeError(
                "TimesFM runtime dependencies are incomplete"
            ) from exc

        weights_path = Path(model_dir) / manifest.weights_file
        if not weights_path.is_file():
            raise TimesFMRuntimeError(f"weights file not found: {weights_path}")
        self._np = np
        self._model = TimesFM_2p5_200M_torch.from_pretrained(manifest.model_id)
        state_dict = torch.load(weights_path, map_location="cpu")
        self._model.model.load_state_dict(state_dict)
        self._model.compile(
            ForecastConfig(
                max_context=max(32, manifest.context_len),
                max_horizon=max(128, manifest.horizon_len),
            )
        )

    def forecast(
        self,
        contexts: Sequence[Sequence[float]],
        *,
        horizon: int,
    ) -> Sequence[ForecastEstimate]:
        arrays = [self._np.asarray(context, dtype=self._np.float32) for context in contexts]
        point, quantiles = self._model.forecast(horizon=horizon, inputs=arrays)
        estimates: list[ForecastEstimate] = []
        for index in range(len(arrays)):
            predicted = float(point[index][0])
            lower = None
            upper = None
            try:
                lower = float(quantiles[index, 0, 1])
                upper = float(quantiles[index, 0, 9])
            except (IndexError, TypeError):
                pass
            estimates.append(
                ForecastEstimate(point=predicted, lower=lower, upper=upper)
            )
        return tuple(estimates)


def base_ohlcv_context_forecast(
    context: Sequence[float],
    *,
    horizon: int,
    model_id: str = DEFAULT_BASE_MODEL_ID,
    model_loader: Callable[[str, int, int], Any] | None = None,
) -> dict[str, Any]:
    """Run the public base model as unvalidated context, never as a promotable signal."""

    horizon = _positive_int(horizon, field_name="horizon")
    values = [_finite(value, field_name="context value") for value in context]
    if len(values) < 32:
        raise TimesFMValidationError("base TimesFM context requires at least 32 observations")
    model_id = str(model_id or "").strip()
    if not model_id:
        raise TimesFMValidationError("model_id is required")
    max_context = len(values)
    if model_loader is None:
        if importlib.util.find_spec("timesfm") is None:
            raise TimesFMRuntimeError("timesfm runtime package is not installed")
        try:
            import numpy as np
            from timesfm import ForecastConfig, TimesFM_2p5_200M_torch
        except ImportError as exc:
            raise TimesFMRuntimeError("TimesFM runtime dependencies are incomplete") from exc
        model = TimesFM_2p5_200M_torch.from_pretrained(model_id)
        model.compile(ForecastConfig(max_context=max_context, max_horizon=horizon))
        inputs = [np.asarray(values, dtype=np.float32)]
    else:
        model = model_loader(model_id, max_context, horizon)
        inputs = [values]
    point, quantiles = model.forecast(horizon=horizon, inputs=inputs)
    points = [float(value) for value in point[0]]
    if len(points) != horizon or not all(math.isfinite(value) for value in points):
        raise TimesFMRuntimeError("TimesFM returned an invalid forecast shape")
    return {
        "model_id": model_id,
        "context_points": len(values),
        "horizon": horizon,
        "point_forecast": points,
        "quantiles_available": quantiles is not None,
        "allowed_use": "context_only_unvalidated_base_model",
        "promotion_eligible": False,
        "live_execution": False,
        "notes": [
            "Base-model output has no Cipher-specific training manifest.",
            "Use only as context until separate walk-forward and prospective evidence is registered.",
        ],
    }
