"""Research-only comparison of optional forecast model outputs.

This module intentionally does not produce a trade recommendation.  It makes
the agreement (or disagreement) between independently produced model context
visible while retaining the platform's paper-only promotion boundary.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping


class ModelContextValidationError(ValueError):
    """Raised when a model context payload cannot be safely compared."""


def _finite(value: Any, *, field_name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ModelContextValidationError(f"{field_name} must be numeric") from exc
    if not math.isfinite(number):
        raise ModelContextValidationError(f"{field_name} must be finite")
    return number


def _direction(return_pct: float) -> str:
    if return_pct > 0:
        return "long"
    if return_pct < 0:
        return "short"
    return "flat"


def _aware_utc(value: datetime | str, *, field_name: str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ModelContextValidationError(f"invalid {field_name}") from exc
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ModelContextValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _timesfm_return(payload: Mapping[str, Any], last_close: float) -> dict[str, Any]:
    points = payload.get("point_forecast")
    if not isinstance(points, (list, tuple)) or not points:
        raise ModelContextValidationError("TimesFM point_forecast is required")
    forecast_close = _finite(points[-1], field_name="TimesFM terminal forecast")
    return_pct = (forecast_close - last_close) / last_close * 100.0
    return {
        "available": True,
        "return_pct": round(return_pct, 6),
        "direction": _direction(return_pct),
        "horizon_bars": int(payload.get("horizon") or len(points)),
        "model_id": str(payload.get("model_id") or "TimesFM"),
    }


def _kronos_return(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not payload.get("available"):
        return {
            "available": False,
            "reason": str(payload.get("reason") or "Kronos unavailable"),
        }
    return_pct = _finite(payload.get("pred_return_pct"), field_name="Kronos pred_return_pct")
    return {
        "available": True,
        "return_pct": round(return_pct, 6),
        "direction": _direction(return_pct),
        "horizon_bars": int(payload.get("pred_bars") or 0),
        "model_id": str(payload.get("model_id") or "Kronos"),
    }


def build_model_context_assessment(
    *,
    last_close: float,
    timesfm: Mapping[str, Any] | None = None,
    kronos: Mapping[str, Any] | None = None,
    source_end: datetime | str | None = None,
    assessed_at: datetime | str | None = None,
    max_data_age: timedelta = timedelta(days=1),
) -> dict[str, Any]:
    """Summarize model context without making it eligible for promotion or execution."""
    last_close = _finite(last_close, field_name="last_close")
    if last_close <= 0:
        raise ModelContextValidationError("last_close must be positive")
    if max_data_age <= timedelta(0):
        raise ModelContextValidationError("max_data_age must be positive")

    models: dict[str, dict[str, Any]] = {}
    if timesfm is not None:
        models["timesfm"] = _timesfm_return(timesfm, last_close)
    if kronos is not None:
        models["kronos"] = _kronos_return(kronos)

    available = [value for value in models.values() if value["available"]]
    directions = {value["direction"] for value in available}
    horizons = {value["horizon_bars"] for value in available}
    if len(available) < 2:
        status = "partial_context" if available else "unavailable"
    elif len(horizons) != 1:
        status = "horizon_mismatch"
    elif len(directions) == 1:
        status = "directional_agreement"
    else:
        status = "directional_disagreement"

    freshness: dict[str, Any] = {"status": "unknown"}
    if source_end is not None:
        source = _aware_utc(source_end, field_name="source_end")
        assessed = _aware_utc(
            assessed_at or datetime.now(timezone.utc), field_name="assessed_at"
        )
        age = assessed - source
        if age < timedelta(0):
            raise ModelContextValidationError("source_end cannot be after assessed_at")
        freshness = {
            "status": "fresh" if age <= max_data_age else "stale",
            "source_end": source.isoformat(),
            "assessed_at": assessed.isoformat(),
            "age_seconds": round(age.total_seconds(), 3),
            "max_age_seconds": max_data_age.total_seconds(),
        }

    return {
        "last_close": last_close,
        "models": models,
        "available_model_count": len(available),
        "context_status": status,
        "data_freshness": freshness,
        "allowed_use": "context_only_unvalidated_models",
        "actionable": False,
        "promotion_eligible": False,
        "live_execution": False,
        "notes": [
            "Agreement is model context, not evidence of predictive edge.",
            "This assessment cannot create, rank, approve, or execute a trade.",
        ],
    }
