"""Hard boundary for future auto-healing actions.

Repair may address operational delivery only. It may never mutate research
evidence, substitute data, loosen gates, or decide promotion.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


class RepairBoundaryViolation(PermissionError):
    pass


@dataclass(frozen=True)
class RepairRequest:
    action: str
    target: str
    changes: Mapping[str, object]


FORBIDDEN_FIELDS = frozenset({
    "cohort", "cohort_id", "origin_windows", "ticker_universe", "parameters",
    "promotion_state", "promotion_decision", "replacement_data", "source",
    "session_completeness", "volume_reconciliation", "minimum_common_tickers",
    "minimum_strict_independent_origins", "gate_threshold",
})


def authorize_repair(request: RepairRequest) -> None:
    """Reject any repair that could change research evidence or authority."""
    forbidden = FORBIDDEN_FIELDS.intersection(request.changes)
    if forbidden:
        raise RepairBoundaryViolation(f"repair cannot alter protected fields: {sorted(forbidden)}")
    if request.action not in {
        "retry_transient_delivery",
        "rebuild_derived_cache",
        "recompute_checksum",
        "clear_generated_test_caches",
        "retry_validation_command",
    }:
        raise RepairBoundaryViolation(f"repair action is not allowlisted: {request.action}")
