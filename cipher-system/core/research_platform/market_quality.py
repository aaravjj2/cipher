"""Hard eligibility checks for local minute-bar research inputs."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable, Mapping


@dataclass(frozen=True, slots=True)
class SessionEligibility:
    observed_bars: int
    expected_bars: int = 391

    @property
    def eligible(self) -> bool:
        return self.observed_bars == self.expected_bars

    def to_dict(self) -> dict:
        return {**asdict(self), "eligible": self.eligible, "reason": None if self.eligible else "incomplete_or_duplicate_regular_session"}


@dataclass(frozen=True, slots=True)
class VolumeEligibility:
    observed_volume: float
    reference_volume: float | None
    max_relative_difference: float = 0.05

    @property
    def relative_difference(self) -> float | None:
        if self.reference_volume is None or self.reference_volume <= 0:
            return None
        return abs(self.observed_volume - self.reference_volume) / self.reference_volume

    @property
    def eligible(self) -> bool:
        return self.relative_difference is not None and self.relative_difference <= self.max_relative_difference

    def to_dict(self) -> dict:
        return {
            **asdict(self),
            "relative_difference": self.relative_difference,
            "eligible": self.eligible,
            "reason": None if self.eligible else "unreconciled_or_materially_different_volume",
        }


@dataclass(frozen=True, slots=True)
class PriceContinuityEligibility:
    """Conservative split-like discontinuity check for price-only forecasts."""

    close_ratio_to_previous_session: float | None
    min_close_ratio: float = 0.5
    max_close_ratio: float = 2.0

    @property
    def eligible(self) -> bool:
        return self.close_ratio_to_previous_session is not None and (
            self.min_close_ratio < self.close_ratio_to_previous_session < self.max_close_ratio
        )

    def to_dict(self) -> dict:
        return {
            **asdict(self),
            "eligible": self.eligible,
            "reason": None if self.eligible else "missing_or_split_like_price_discontinuity",
        }


@dataclass(frozen=True, slots=True)
class HoldoutCohortEligibility:
    """Evidence gate for an untouched, single-source ranking cohort.

    This is separate from day eligibility.  It prevents a valid-looking set
    of price-only days from being treated as a research holdout when the
    common universe, origin independence, or source provenance is inadequate.
    """

    source_count: int
    common_tickers: int
    strict_independent_origins: int
    minimum_common_tickers: int = 8
    minimum_strict_independent_origins: int = 12

    @property
    def eligible(self) -> bool:
        return (
            self.source_count == 1
            and self.common_tickers >= self.minimum_common_tickers
            and self.strict_independent_origins >= self.minimum_strict_independent_origins
        )

    def to_dict(self) -> dict:
        reasons = []
        if self.source_count != 1:
            reasons.append("single_source_required")
        if self.common_tickers < self.minimum_common_tickers:
            reasons.append("insufficient_common_tickers")
        if self.strict_independent_origins < self.minimum_strict_independent_origins:
            reasons.append("insufficient_strict_independent_origins")
        return {**asdict(self), "eligible": self.eligible, "reasons": reasons}


def require_eligible_market_day(
    *,
    observed_bars: int,
    observed_volume: float,
    reference_volume: float | None,
    expected_bars: int = 391,
    max_relative_volume_difference: float = 0.05,
) -> dict:
    """Return an auditable decision; missing reference data is a rejection, never a fill."""
    session = SessionEligibility(observed_bars=observed_bars, expected_bars=expected_bars)
    volume = VolumeEligibility(
        observed_volume=observed_volume,
        reference_volume=reference_volume,
        max_relative_difference=max_relative_volume_difference,
    )
    return {
        "eligible": session.eligible and volume.eligible,
        "session": session.to_dict(),
        "volume": volume.to_dict(),
        "allowed_use": "research_only_when_eligible",
    }


def require_price_only_market_day(
    *,
    observed_bars: int,
    close_ratio_to_previous_session: float | None,
    expected_bars: int = 391,
) -> dict:
    """Gate usable only for price-only forecast research, never volume-sensitive work.

    The full `require_eligible_market_day` gate remains mandatory for sizing,
    liquidity, volume features, or any other volume-dependent research.
    """
    session = SessionEligibility(observed_bars=observed_bars, expected_bars=expected_bars)
    continuity = PriceContinuityEligibility(
        close_ratio_to_previous_session=close_ratio_to_previous_session,
    )
    return {
        "eligible": session.eligible and continuity.eligible,
        "session": session.to_dict(),
        "price_continuity": continuity.to_dict(),
        "allowed_use": "price_forecast_research_only_no_volume_features",
        "volume_sensitive_use": False,
    }


def require_holdout_c_cohort(
    *,
    source_count: int,
    common_tickers: int,
    strict_independent_origins: int,
) -> dict:
    """Reject mixed-source or undersized Holdout C cohorts before outcomes."""
    cohort = HoldoutCohortEligibility(
        source_count=source_count,
        common_tickers=common_tickers,
        strict_independent_origins=strict_independent_origins,
    )
    return {
        "eligible": cohort.eligible,
        "cohort": cohort.to_dict(),
        "allowed_use": "pre_outcome_price_only_holdout_construction_only",
        "volume_sensitive_use": False,
        "paper_or_live_execution": False,
    }


def evaluate_market_days(rows: Iterable[Mapping[str, object]]) -> list[dict]:
    """Evaluate normalized daily rows without inferring absent bars or volume."""
    out = []
    for row in rows:
        result = require_eligible_market_day(
            observed_bars=int(row["observed_bars"]),
            observed_volume=float(row["observed_volume"]),
            reference_volume=(None if row.get("reference_volume") is None else float(row["reference_volume"])),
        )
        out.append({**dict(row), **result})
    return out
