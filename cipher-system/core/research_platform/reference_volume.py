"""Provider-neutral, reference-only regular-session volume reconciliation.

This module deliberately does not expose any price-replacement path.  A
reference provider may contribute minute timestamps, symbols, and share volume
only.  Alpaca remains the price source, the existing 5% reconciliation rule is
unchanged, and invalid or incomplete reference evidence is rejected rather
than filled or scaled.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from typing import Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

from .market_quality import VolumeEligibility

REFERENCE_ALLOWED_USE = "independent_regular_session_volume_reconciliation_only"
REFERENCE_PRICE_SUBSTITUTION_ALLOWED = False
REFERENCE_VOLUME_SCALING_ALLOWED = False


@dataclass(frozen=True, slots=True)
class RegularSessionSpec:
    """Canonical regular-session window used by the existing full gate."""

    timezone: str = "America/New_York"
    start: time = time(9, 30)
    end: time = time(16, 0)
    end_inclusive: bool = True
    expected_bars: int = 391

    def contains(self, local_timestamp: datetime) -> bool:
        local_time = local_timestamp.timetz().replace(tzinfo=None)
        if self.end_inclusive:
            return self.start <= local_time <= self.end
        return self.start <= local_time < self.end

    def to_dict(self) -> dict[str, object]:
        return {
            "timezone": self.timezone,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "end_inclusive": self.end_inclusive,
            "expected_bars": self.expected_bars,
        }


@dataclass(frozen=True, slots=True)
class ReferenceImportPolicy:
    """Frozen mapping and scope for one provider import."""

    provider: str
    timestamp_column: str = "timestamp"
    symbol_column: str = "symbol"
    volume_column: str = "volume"
    source_timezone: str = "UTC"
    timestamp_semantics: str = "minute_start"
    symbols: tuple[str, ...] = ()
    start_date: date | None = None
    end_date: date | None = None
    session: RegularSessionSpec = field(default_factory=RegularSessionSpec)

    def __post_init__(self) -> None:
        if not self.provider.strip():
            raise ValueError("provider is required")
        if self.timestamp_semantics not in {"minute_start", "minute_end"}:
            raise ValueError("timestamp_semantics must be minute_start or minute_end")
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("start_date must not be after end_date")

    def to_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "timestamp_column": self.timestamp_column,
            "symbol_column": self.symbol_column,
            "volume_column": self.volume_column,
            "source_timezone": self.source_timezone,
            "timestamp_semantics": self.timestamp_semantics,
            "symbols": list(self.symbols),
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "end_date": self.end_date.isoformat() if self.end_date else None,
            "session": self.session.to_dict(),
            "allowed_use": REFERENCE_ALLOWED_USE,
            "price_substitution_allowed": REFERENCE_PRICE_SUBSTITUTION_ALLOWED,
            "volume_scaling_allowed": REFERENCE_VOLUME_SCALING_ALLOWED,
        }


@dataclass(frozen=True, slots=True)
class ReferenceSessionSummary:
    provider: str
    symbol: str
    session_date: str
    raw_rows: int
    regular_rows: int
    regular_volume: float
    duplicate_timestamps: int
    invalid_rows: int
    first_regular_timestamp: str | None
    last_regular_timestamp: str | None
    expected_bars: int
    reference_valid: bool
    rejection_reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True)
class _MutableSummary:
    raw_rows: int = 0
    regular_rows: int = 0
    regular_volume: float = 0.0
    duplicate_timestamps: int = 0
    invalid_rows: int = 0
    first_regular_timestamp: datetime | None = None
    last_regular_timestamp: datetime | None = None
    regular_timestamps: set[datetime] = field(default_factory=set)


def parse_timestamp(value: object, source_timezone: str) -> datetime:
    """Parse a provider timestamp and return a timezone-aware datetime.

    Naive timestamps are interpreted only in the explicitly frozen source
    timezone.  This avoids machine-local timezone dependence.
    """

    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if not text:
            raise ValueError("empty timestamp")
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(source_timezone))
    return parsed


def _normalize_symbol(value: object) -> str:
    symbol = str(value).strip().upper()
    if not symbol:
        raise ValueError("empty symbol")
    return symbol


def _normalize_volume(value: object) -> float:
    volume = float(value)
    if volume < 0:
        raise ValueError("negative volume")
    if volume != volume or volume in {float("inf"), float("-inf")}:
        raise ValueError("non-finite volume")
    return volume


def summarize_reference_rows(
    rows: Iterable[Mapping[str, object]],
    *,
    policy: ReferenceImportPolicy,
) -> list[ReferenceSessionSummary]:
    """Summarize reference rows without filling, scaling, or using prices."""

    ny = ZoneInfo(policy.session.timezone)
    allowlist = set(policy.symbols)
    groups: dict[tuple[str, str], _MutableSummary] = {}

    for row in rows:
        symbol_hint = str(row.get(policy.symbol_column, "")).strip().upper()
        date_hint = "unknown"
        try:
            symbol = _normalize_symbol(row[policy.symbol_column])
            timestamp = parse_timestamp(row[policy.timestamp_column], policy.source_timezone)
            volume = _normalize_volume(row[policy.volume_column])
            local = timestamp.astimezone(ny)
            session_date = local.date()
            date_hint = session_date.isoformat()
        except (KeyError, TypeError, ValueError):
            key = (symbol_hint or "UNKNOWN", date_hint)
            group = groups.setdefault(key, _MutableSummary())
            group.raw_rows += 1
            group.invalid_rows += 1
            continue

        if allowlist and symbol not in allowlist:
            continue
        if policy.start_date and session_date < policy.start_date:
            continue
        if policy.end_date and session_date > policy.end_date:
            continue

        key = (symbol, session_date.isoformat())
        group = groups.setdefault(key, _MutableSummary())
        group.raw_rows += 1
        if not policy.session.contains(local):
            continue
        canonical_minute = local.replace(second=0, microsecond=0)
        if canonical_minute in group.regular_timestamps:
            group.duplicate_timestamps += 1
            continue
        group.regular_timestamps.add(canonical_minute)
        group.regular_rows += 1
        group.regular_volume += volume
        if group.first_regular_timestamp is None or canonical_minute < group.first_regular_timestamp:
            group.first_regular_timestamp = canonical_minute
        if group.last_regular_timestamp is None or canonical_minute > group.last_regular_timestamp:
            group.last_regular_timestamp = canonical_minute

    summaries: list[ReferenceSessionSummary] = []
    for (symbol, session_date), group in sorted(groups.items()):
        reasons: list[str] = []
        if group.invalid_rows:
            reasons.append("invalid_reference_rows")
        if group.duplicate_timestamps:
            reasons.append("duplicate_reference_minutes")
        if group.regular_rows != policy.session.expected_bars:
            reasons.append("incomplete_reference_regular_session")
        if group.regular_volume <= 0:
            reasons.append("non_positive_reference_volume")
        summaries.append(
            ReferenceSessionSummary(
                provider=policy.provider,
                symbol=symbol,
                session_date=session_date,
                raw_rows=group.raw_rows,
                regular_rows=group.regular_rows,
                regular_volume=group.regular_volume,
                duplicate_timestamps=group.duplicate_timestamps,
                invalid_rows=group.invalid_rows,
                first_regular_timestamp=(
                    group.first_regular_timestamp.isoformat() if group.first_regular_timestamp else None
                ),
                last_regular_timestamp=(
                    group.last_regular_timestamp.isoformat() if group.last_regular_timestamp else None
                ),
                expected_bars=policy.session.expected_bars,
                reference_valid=not reasons,
                rejection_reasons=tuple(reasons),
            )
        )
    return summaries


def reconcile_session_volume(
    *,
    observed_source: str,
    observed_bars: int,
    observed_volume: float,
    reference: ReferenceSessionSummary,
    max_relative_difference: float = 0.05,
) -> dict[str, object]:
    """Reconcile one Alpaca session against one valid independent reference."""

    volume = VolumeEligibility(
        observed_volume=float(observed_volume),
        reference_volume=(reference.regular_volume if reference.reference_valid else None),
        max_relative_difference=max_relative_difference,
    )
    observed_session_complete = observed_bars == reference.expected_bars
    eligible = observed_session_complete and reference.reference_valid and volume.eligible
    reasons: list[str] = []
    if not observed_session_complete:
        reasons.append("incomplete_observed_regular_session")
    if not reference.reference_valid:
        reasons.extend(reference.rejection_reasons)
    if reference.reference_valid and not volume.eligible:
        reasons.append("material_volume_difference")
    return {
        "symbol": reference.symbol,
        "session_date": reference.session_date,
        "observed_source": observed_source,
        "reference_source": reference.provider,
        "reference_purpose": "verification_only",
        "allowed_use": REFERENCE_ALLOWED_USE,
        "price_substitution_allowed": REFERENCE_PRICE_SUBSTITUTION_ALLOWED,
        "volume_scaling_allowed": REFERENCE_VOLUME_SCALING_ALLOWED,
        "observed_bars": int(observed_bars),
        "expected_bars": reference.expected_bars,
        "observed_session_complete": observed_session_complete,
        "observed_volume": float(observed_volume),
        "reference_bars": reference.regular_rows,
        "reference_volume": reference.regular_volume,
        "reference_valid": reference.reference_valid,
        "relative_difference": volume.relative_difference,
        "max_relative_difference": max_relative_difference,
        "eligible": eligible,
        "rejection_reasons": reasons,
    }


def validate_reference_manifest(payload: Mapping[str, object]) -> None:
    """Reject manifests that could blur the reference-only boundary."""

    if payload.get("allowed_use") != REFERENCE_ALLOWED_USE:
        raise ValueError("manifest allowed_use is not reference-only")
    if payload.get("price_substitution_allowed") is not False:
        raise ValueError("manifest must prohibit price substitution")
    if payload.get("volume_scaling_allowed") is not False:
        raise ValueError("manifest must prohibit volume scaling")
    sessions = payload.get("sessions")
    if not isinstance(sessions, Sequence) or isinstance(sessions, (str, bytes)):
        raise ValueError("manifest sessions must be a sequence")


def reference_summary_from_mapping(row: Mapping[str, object]) -> ReferenceSessionSummary:
    return ReferenceSessionSummary(
        provider=str(row["provider"]),
        symbol=str(row["symbol"]),
        session_date=str(row["session_date"]),
        raw_rows=int(row["raw_rows"]),
        regular_rows=int(row["regular_rows"]),
        regular_volume=float(row["regular_volume"]),
        duplicate_timestamps=int(row["duplicate_timestamps"]),
        invalid_rows=int(row["invalid_rows"]),
        first_regular_timestamp=(
            None if row.get("first_regular_timestamp") is None else str(row["first_regular_timestamp"])
        ),
        last_regular_timestamp=(
            None if row.get("last_regular_timestamp") is None else str(row["last_regular_timestamp"])
        ),
        expected_bars=int(row["expected_bars"]),
        reference_valid=bool(row["reference_valid"]),
        rejection_reasons=tuple(str(value) for value in row.get("rejection_reasons", ())),
    )


def ensure_raw_reference_path(path: Path, reference_root: Path) -> Path:
    """Require immutable raw evidence to live under the reference-volume tree."""

    resolved = path.resolve()
    root = reference_root.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"reference file must be stored under {root}") from exc
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    return resolved
