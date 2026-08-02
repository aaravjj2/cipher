"""Point-in-time feature fusion for external options-research backtests.

The module combines optional London Strategic Edge, Kronos, and TimesFM
features without allowing them to alter the core strategy silently.  Every
feature row has an explicit source cutoff and availability timestamp.  A row
may influence a decision only after ``available_at`` and only within a bounded
freshness window.

This module is research-only.  It contains no broker or order API.
"""
from __future__ import annotations

import csv
import math
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable


class FeatureValidationError(ValueError):
    """Raised when a feature row or gate configuration is invalid."""


_ALLOWED_MODES = {
    "none",
    "lse",
    "kronos",
    "timesfm",
    "lse_kronos",
    "lse_timesfm",
    "kronos_timesfm",
    "all",
}


def _utc(value: datetime, *, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise FeatureValidationError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise FeatureValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _finite(value: float, *, field_name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise FeatureValidationError(f"{field_name} must be numeric") from exc
    if not math.isfinite(number):
        raise FeatureValidationError(f"{field_name} must be finite")
    return number


def _optional_finite(value: float | None, *, field_name: str) -> float | None:
    if value is None:
        return None
    return _finite(value, field_name=field_name)


def _strict_bool(value: bool, *, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise FeatureValidationError(f"{field_name} must be a boolean")
    return value


def _nonnegative_int(value: int, *, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise FeatureValidationError(f"{field_name} must be a non-negative integer")
    return value


def parse_timestamp(value: str) -> datetime:
    raw = str(value or "").strip()
    if not raw:
        raise FeatureValidationError("timestamp is required")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise FeatureValidationError(f"invalid timestamp {value!r}") from exc
    return _utc(parsed, field_name="timestamp")


def parse_optional_float(value: str | None) -> float | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    return _finite(raw, field_name="CSV numeric field")


def parse_bool(value: str | bool | None) -> bool:
    if isinstance(value, bool):
        return value
    raw = str(value or "").strip().lower()
    if raw in {"1", "true", "yes"}:
        return True
    if raw in {"0", "false", "no", ""}:
        return False
    raise FeatureValidationError(f"invalid boolean value {value!r}")


@dataclass(frozen=True, slots=True)
class ResearchFeatureRow:
    """Feature values known at a particular point in time.

    Values are deliberately compact so the same CSV can be consumed by LEAN,
    Cipher, and audit scripts.  Numeric percentages use percentage points, not
    decimal fractions, unless the field name explicitly says ``ratio``.
    """

    available_at: datetime
    source_cutoff_at: datetime
    source_date: date
    underlying: str

    lse_available: bool = False
    lse_call_premium: float | None = None
    lse_put_premium: float | None = None
    lse_flow_imbalance: float | None = None
    lse_print_count: int = 0

    kronos_available: bool = False
    kronos_pred_return_pct: float | None = None

    timesfm_available: bool = False
    timesfm_gex_change_pct: float | None = None
    timesfm_confidence: float | None = None

    gex_snapshot_count: int = 0
    provenance: str = ""
    notes: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        available = _utc(self.available_at, field_name="available_at")
        cutoff = _utc(self.source_cutoff_at, field_name="source_cutoff_at")
        if cutoff > available:
            raise FeatureValidationError("source_cutoff_at cannot be after available_at")
        if not isinstance(self.source_date, date) or isinstance(self.source_date, datetime):
            raise FeatureValidationError("source_date must be a date")
        underlying = str(self.underlying or "").strip().upper()
        if not underlying:
            raise FeatureValidationError("underlying is required")

        lse_available = _strict_bool(self.lse_available, field_name="lse_available")
        kronos_available = _strict_bool(
            self.kronos_available, field_name="kronos_available"
        )
        timesfm_available = _strict_bool(
            self.timesfm_available, field_name="timesfm_available"
        )

        lse_call = _optional_finite(
            self.lse_call_premium, field_name="lse_call_premium"
        )
        lse_put = _optional_finite(
            self.lse_put_premium, field_name="lse_put_premium"
        )
        lse_imbalance = _optional_finite(
            self.lse_flow_imbalance, field_name="lse_flow_imbalance"
        )
        if lse_call is not None and lse_call < 0:
            raise FeatureValidationError("lse_call_premium cannot be negative")
        if lse_put is not None and lse_put < 0:
            raise FeatureValidationError("lse_put_premium cannot be negative")
        if lse_imbalance is not None and not -1 <= lse_imbalance <= 1:
            raise FeatureValidationError("lse_flow_imbalance must be between -1 and 1")

        kronos_return = _optional_finite(
            self.kronos_pred_return_pct,
            field_name="kronos_pred_return_pct",
        )
        timesfm_change = _optional_finite(
            self.timesfm_gex_change_pct,
            field_name="timesfm_gex_change_pct",
        )
        timesfm_confidence = _optional_finite(
            self.timesfm_confidence,
            field_name="timesfm_confidence",
        )
        if timesfm_confidence is not None and not 0 <= timesfm_confidence <= 1:
            raise FeatureValidationError("timesfm_confidence must be between 0 and 1")

        lse_count = _nonnegative_int(self.lse_print_count, field_name="lse_print_count")
        gex_count = _nonnegative_int(
            self.gex_snapshot_count, field_name="gex_snapshot_count"
        )
        if lse_available and (
            lse_call is None or lse_put is None or lse_imbalance is None
        ):
            raise FeatureValidationError(
                "LSE availability requires call premium, put premium, and imbalance"
            )
        if kronos_available and kronos_return is None:
            raise FeatureValidationError(
                "Kronos availability requires kronos_pred_return_pct"
            )
        if timesfm_available and (
            timesfm_change is None or timesfm_confidence is None
        ):
            raise FeatureValidationError(
                "TimesFM availability requires change and confidence"
            )

        object.__setattr__(self, "available_at", available)
        object.__setattr__(self, "source_cutoff_at", cutoff)
        object.__setattr__(self, "underlying", underlying)
        object.__setattr__(self, "lse_available", lse_available)
        object.__setattr__(self, "kronos_available", kronos_available)
        object.__setattr__(self, "timesfm_available", timesfm_available)
        object.__setattr__(self, "lse_call_premium", lse_call)
        object.__setattr__(self, "lse_put_premium", lse_put)
        object.__setattr__(self, "lse_flow_imbalance", lse_imbalance)
        object.__setattr__(self, "lse_print_count", lse_count)
        object.__setattr__(self, "kronos_pred_return_pct", kronos_return)
        object.__setattr__(self, "timesfm_gex_change_pct", timesfm_change)
        object.__setattr__(self, "timesfm_confidence", timesfm_confidence)
        object.__setattr__(self, "gex_snapshot_count", gex_count)
        object.__setattr__(self, "provenance", str(self.provenance or "").strip())
        object.__setattr__(self, "notes", tuple(str(x) for x in self.notes))

    def to_csv_dict(self) -> dict[str, str | int | float]:
        return {
            "available_at": self.available_at.isoformat(),
            "source_cutoff_at": self.source_cutoff_at.isoformat(),
            "source_date": self.source_date.isoformat(),
            "underlying": self.underlying,
            "lse_available": int(self.lse_available),
            "lse_call_premium": "" if self.lse_call_premium is None else self.lse_call_premium,
            "lse_put_premium": "" if self.lse_put_premium is None else self.lse_put_premium,
            "lse_flow_imbalance": "" if self.lse_flow_imbalance is None else self.lse_flow_imbalance,
            "lse_print_count": self.lse_print_count,
            "kronos_available": int(self.kronos_available),
            "kronos_pred_return_pct": "" if self.kronos_pred_return_pct is None else self.kronos_pred_return_pct,
            "timesfm_available": int(self.timesfm_available),
            "timesfm_gex_change_pct": "" if self.timesfm_gex_change_pct is None else self.timesfm_gex_change_pct,
            "timesfm_confidence": "" if self.timesfm_confidence is None else self.timesfm_confidence,
            "gex_snapshot_count": self.gex_snapshot_count,
            "provenance": self.provenance,
            "notes": "; ".join(self.notes),
        }


@dataclass(frozen=True, slots=True)
class FeatureGateConfig:
    maximum_feature_age_days: float = 3.0
    minimum_lse_print_count: int = 10
    minimum_lse_flow_imbalance: float = -0.10
    minimum_kronos_return_pct: float = -0.25
    minimum_timesfm_gex_change_pct: float = -10.0
    minimum_timesfm_confidence: float = 0.25

    def __post_init__(self) -> None:
        max_age = _finite(
            self.maximum_feature_age_days,
            field_name="maximum_feature_age_days",
        )
        if max_age < 0:
            raise FeatureValidationError("maximum_feature_age_days cannot be negative")
        min_prints = _nonnegative_int(
            self.minimum_lse_print_count,
            field_name="minimum_lse_print_count",
        )
        lse_threshold = _finite(
            self.minimum_lse_flow_imbalance,
            field_name="minimum_lse_flow_imbalance",
        )
        if not -1 <= lse_threshold <= 1:
            raise FeatureValidationError(
                "minimum_lse_flow_imbalance must be between -1 and 1"
            )
        kronos_threshold = _finite(
            self.minimum_kronos_return_pct,
            field_name="minimum_kronos_return_pct",
        )
        timesfm_threshold = _finite(
            self.minimum_timesfm_gex_change_pct,
            field_name="minimum_timesfm_gex_change_pct",
        )
        confidence = _finite(
            self.minimum_timesfm_confidence,
            field_name="minimum_timesfm_confidence",
        )
        if not 0 <= confidence <= 1:
            raise FeatureValidationError(
                "minimum_timesfm_confidence must be between 0 and 1"
            )
        object.__setattr__(self, "maximum_feature_age_days", max_age)
        object.__setattr__(self, "minimum_lse_print_count", min_prints)
        object.__setattr__(self, "minimum_lse_flow_imbalance", lse_threshold)
        object.__setattr__(self, "minimum_kronos_return_pct", kronos_threshold)
        object.__setattr__(self, "minimum_timesfm_gex_change_pct", timesfm_threshold)
        object.__setattr__(self, "minimum_timesfm_confidence", confidence)


@dataclass(frozen=True, slots=True)
class FeatureGateDecision:
    eligible: bool
    mode: str
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]
    feature_age_days: float | None
    components: dict[str, bool]

    def to_dict(self) -> dict:
        return asdict(self)


def required_components(mode: str) -> tuple[str, ...]:
    normalized = str(mode or "").strip().lower()
    if normalized not in _ALLOWED_MODES:
        raise FeatureValidationError(
            f"unsupported feature mode {mode!r}; choose from {sorted(_ALLOWED_MODES)}"
        )
    if normalized == "none":
        return ()
    if normalized == "all":
        return ("lse", "kronos", "timesfm")
    return tuple(normalized.split("_"))


def evaluate_feature_gate(
    row: ResearchFeatureRow | None,
    *,
    mode: str,
    decision_time: datetime,
    underlying: str,
    config: FeatureGateConfig | None = None,
) -> FeatureGateDecision:
    """Evaluate an explicit model-feature ablation gate.

    ``none`` always passes and is the proper baseline.  All other modes fail
    closed when a required feature is absent, stale, or not yet available.
    """
    config = config or FeatureGateConfig()
    components_required = required_components(mode)
    normalized_mode = str(mode).strip().lower()
    decision_time = _utc(decision_time, field_name="decision_time")
    normalized_underlying = str(underlying or "").strip().upper()
    if not normalized_underlying:
        raise FeatureValidationError("underlying is required")

    if not components_required:
        return FeatureGateDecision(
            eligible=True,
            mode=normalized_mode,
            blockers=(),
            warnings=(),
            feature_age_days=None,
            components={},
        )

    blockers: list[str] = []
    warnings: list[str] = []
    component_results: dict[str, bool] = {}
    if row is None:
        blockers.append("required_feature_row_missing")
        return FeatureGateDecision(
            eligible=False,
            mode=normalized_mode,
            blockers=tuple(blockers),
            warnings=(),
            feature_age_days=None,
            components={component: False for component in components_required},
        )

    if row.underlying != normalized_underlying:
        blockers.append("feature_underlying_mismatch")
    if row.available_at > decision_time:
        blockers.append("feature_not_yet_available_lookahead")
        feature_age_days = None
    else:
        feature_age_days = (decision_time - row.available_at).total_seconds() / 86_400.0
        if feature_age_days > config.maximum_feature_age_days:
            blockers.append("feature_row_stale")

    if "lse" in components_required:
        lse_ok = True
        if not row.lse_available:
            blockers.append("lse_feature_unavailable")
            lse_ok = False
        else:
            if row.lse_print_count < config.minimum_lse_print_count:
                blockers.append("lse_print_count_too_low")
                lse_ok = False
            if (
                row.lse_flow_imbalance is None
                or row.lse_flow_imbalance < config.minimum_lse_flow_imbalance
            ):
                blockers.append("lse_flow_too_bearish_for_short_put")
                lse_ok = False
        component_results["lse"] = lse_ok

    if "kronos" in components_required:
        kronos_ok = True
        if not row.kronos_available:
            blockers.append("kronos_feature_unavailable")
            kronos_ok = False
        elif (
            row.kronos_pred_return_pct is None
            or row.kronos_pred_return_pct < config.minimum_kronos_return_pct
        ):
            blockers.append("kronos_forecast_below_threshold")
            kronos_ok = False
        component_results["kronos"] = kronos_ok

    if "timesfm" in components_required:
        timesfm_ok = True
        if not row.timesfm_available:
            blockers.append("timesfm_feature_unavailable")
            timesfm_ok = False
        else:
            if (
                row.timesfm_confidence is None
                or row.timesfm_confidence < config.minimum_timesfm_confidence
            ):
                blockers.append("timesfm_confidence_too_low")
                timesfm_ok = False
            if (
                row.timesfm_gex_change_pct is None
                or row.timesfm_gex_change_pct
                < config.minimum_timesfm_gex_change_pct
            ):
                blockers.append("timesfm_gex_forecast_below_threshold")
                timesfm_ok = False
        component_results["timesfm"] = timesfm_ok

    if row.gex_snapshot_count < 4 and "timesfm" in components_required:
        warnings.append("timesfm_sample_history_is_short")
    if row.source_date.year >= 2026:
        warnings.append("external_feature_coverage_is_recent_and_regime_concentrated")

    return FeatureGateDecision(
        eligible=not blockers,
        mode=normalized_mode,
        blockers=tuple(dict.fromkeys(blockers)),
        warnings=tuple(dict.fromkeys(warnings)),
        feature_age_days=(
            round(feature_age_days, 6) if feature_age_days is not None else None
        ),
        components=component_results,
    )


def load_feature_csv(path: str | Path) -> tuple[ResearchFeatureRow, ...]:
    source = Path(path)
    rows: list[ResearchFeatureRow] = []
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {
            "available_at",
            "source_cutoff_at",
            "source_date",
            "underlying",
        }
        missing = required - set(reader.fieldnames or ())
        if missing:
            raise FeatureValidationError(
                f"missing feature CSV columns: {', '.join(sorted(missing))}"
            )
        for line_number, payload in enumerate(reader, start=2):
            try:
                rows.append(
                    ResearchFeatureRow(
                        available_at=parse_timestamp(payload["available_at"]),
                        source_cutoff_at=parse_timestamp(payload["source_cutoff_at"]),
                        source_date=date.fromisoformat(payload["source_date"]),
                        underlying=payload["underlying"],
                        lse_available=parse_bool(payload.get("lse_available")),
                        lse_call_premium=parse_optional_float(
                            payload.get("lse_call_premium")
                        ),
                        lse_put_premium=parse_optional_float(
                            payload.get("lse_put_premium")
                        ),
                        lse_flow_imbalance=parse_optional_float(
                            payload.get("lse_flow_imbalance")
                        ),
                        lse_print_count=int(payload.get("lse_print_count") or 0),
                        kronos_available=parse_bool(
                            payload.get("kronos_available")
                        ),
                        kronos_pred_return_pct=parse_optional_float(
                            payload.get("kronos_pred_return_pct")
                        ),
                        timesfm_available=parse_bool(
                            payload.get("timesfm_available")
                        ),
                        timesfm_gex_change_pct=parse_optional_float(
                            payload.get("timesfm_gex_change_pct")
                        ),
                        timesfm_confidence=parse_optional_float(
                            payload.get("timesfm_confidence")
                        ),
                        gex_snapshot_count=int(
                            payload.get("gex_snapshot_count") or 0
                        ),
                        provenance=payload.get("provenance") or "",
                        notes=tuple(
                            item.strip()
                            for item in (payload.get("notes") or "").split(";")
                            if item.strip()
                        ),
                    )
                )
            except (FeatureValidationError, ValueError) as exc:
                raise FeatureValidationError(
                    f"{source}:{line_number}: {exc}"
                ) from exc

    rows.sort(key=lambda row: (row.available_at, row.underlying))
    for previous, current in zip(rows, rows[1:]):
        if (
            previous.available_at == current.available_at
            and previous.underlying == current.underlying
        ):
            raise FeatureValidationError(
                "duplicate feature timestamp for "
                f"{current.underlying} at {current.available_at.isoformat()}"
            )
    return tuple(rows)


def latest_available_feature(
    rows: Iterable[ResearchFeatureRow],
    *,
    underlying: str,
    decision_time: datetime,
) -> ResearchFeatureRow | None:
    decision_time = _utc(decision_time, field_name="decision_time")
    normalized = str(underlying or "").strip().upper()
    eligible = [
        row
        for row in rows
        if row.underlying == normalized and row.available_at <= decision_time
    ]
    return max(eligible, key=lambda row: row.available_at) if eligible else None
