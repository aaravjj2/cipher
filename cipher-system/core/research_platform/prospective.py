from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from .hashing import canonical_json, stable_id
from .models import AuditEvent, utc_now
from .registry import ResearchRegistry, RegistryConflictError, RegistryNotFoundError


@dataclass(frozen=True)
class ProspectiveRegistration:
    strategy_id: str
    name: str
    configuration: Mapping[str, Any]
    minimum_sample: int
    acceptance_criteria: Mapping[str, Any]
    created_at: datetime
    registration_id: str = ""

    def __post_init__(self) -> None:
        if self.minimum_sample < 1:
            raise ValueError("minimum_sample must be positive")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        created = self.created_at.astimezone(timezone.utc)
        payload = {
            "strategy_id": self.strategy_id,
            "name": self.name,
            "configuration": dict(self.configuration),
            "minimum_sample": self.minimum_sample,
            "acceptance_criteria": dict(self.acceptance_criteria),
            "created_at": created.isoformat(),
        }
        object.__setattr__(self, "configuration", dict(self.configuration))
        object.__setattr__(self, "acceptance_criteria", dict(self.acceptance_criteria))
        object.__setattr__(self, "created_at", created)
        object.__setattr__(self, "registration_id", self.registration_id or stable_id("prospective", payload))

    def to_dict(self) -> dict[str, Any]:
        return {
            "registration_id": self.registration_id,
            "strategy_id": self.strategy_id,
            "name": self.name,
            "configuration": dict(self.configuration),
            "minimum_sample": self.minimum_sample,
            "acceptance_criteria": dict(self.acceptance_criteria),
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True)
class ProspectiveObservation:
    prospective_test_id: str
    signal_id: str
    signal_time: datetime
    available_at: datetime
    symbol: str
    direction: str
    feature_snapshot_ids: Sequence[str]
    contract_candidates: Sequence[Mapping[str, Any]]
    selected_instrument: Mapping[str, Any] | None
    simulated_entry: Mapping[str, Any] | None
    rejection_reasons: Sequence[str]
    outcome: Mapping[str, Any] | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    observation_id: str = ""

    def __post_init__(self) -> None:
        signal = self._aware(self.signal_time, "signal_time")
        available = self._aware(self.available_at, "available_at")
        if available < signal:
            raise ValueError("available_at cannot precede signal_time")
        payload = {
            "prospective_test_id": self.prospective_test_id,
            "signal_id": self.signal_id,
            "signal_time": signal.isoformat(),
            "available_at": available.isoformat(),
            "symbol": self.symbol.upper(),
            "direction": self.direction,
            "feature_snapshot_ids": tuple(sorted(set(self.feature_snapshot_ids))),
            "contract_candidates": tuple(dict(item) for item in self.contract_candidates),
            "selected_instrument": dict(self.selected_instrument) if self.selected_instrument else None,
            "simulated_entry": dict(self.simulated_entry) if self.simulated_entry else None,
            "rejection_reasons": tuple(self.rejection_reasons),
            "outcome": dict(self.outcome) if self.outcome else None,
            "metadata": dict(self.metadata),
        }
        object.__setattr__(self, "signal_time", signal)
        object.__setattr__(self, "available_at", available)
        object.__setattr__(self, "symbol", self.symbol.upper())
        object.__setattr__(self, "feature_snapshot_ids", payload["feature_snapshot_ids"])
        object.__setattr__(self, "contract_candidates", payload["contract_candidates"])
        object.__setattr__(self, "selected_instrument", payload["selected_instrument"])
        object.__setattr__(self, "simulated_entry", payload["simulated_entry"])
        object.__setattr__(self, "rejection_reasons", payload["rejection_reasons"])
        object.__setattr__(self, "outcome", payload["outcome"])
        object.__setattr__(self, "metadata", payload["metadata"])
        object.__setattr__(
            self,
            "observation_id",
            self.observation_id
            or stable_id(
                "observation",
                {"prospective_test_id": self.prospective_test_id, "signal_id": self.signal_id},
            ),
        )

    @property
    def status(self) -> str:
        if self.rejection_reasons:
            return "REJECTED"
        if self.outcome is not None:
            return "SCORED"
        return "PENDING"

    def to_dict(self) -> dict[str, Any]:
        return {
            "observation_id": self.observation_id,
            "prospective_test_id": self.prospective_test_id,
            "signal_id": self.signal_id,
            "signal_time": self.signal_time.isoformat(),
            "available_at": self.available_at.isoformat(),
            "symbol": self.symbol,
            "direction": self.direction,
            "feature_snapshot_ids": list(self.feature_snapshot_ids),
            "contract_candidates": [dict(item) for item in self.contract_candidates],
            "selected_instrument": dict(self.selected_instrument) if self.selected_instrument else None,
            "simulated_entry": dict(self.simulated_entry) if self.simulated_entry else None,
            "rejection_reasons": list(self.rejection_reasons),
            "outcome": dict(self.outcome) if self.outcome else None,
            "metadata": dict(self.metadata),
            "status": self.status,
        }

    @staticmethod
    def _aware(value: datetime, field_name: str) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{field_name} must be timezone-aware")
        return value.astimezone(timezone.utc)


class ProspectiveService:
    def __init__(self, registry: ResearchRegistry):
        self.registry = registry

    def register(self, registration: ProspectiveRegistration) -> str:
        payload = canonical_json(registration.to_dict())
        with self.registry.connect() as db:
            if not db.execute(
                "select 1 from strategies where strategy_id = ?",
                (registration.strategy_id,),
            ).fetchone():
                raise RegistryNotFoundError(registration.strategy_id)
            existing = db.execute(
                "select registration_json from prospective_tests where prospective_test_id = ?",
                (registration.registration_id,),
            ).fetchone()
            if existing and existing["registration_json"] != payload:
                raise RegistryConflictError("prospective registration ID reused with different content")
            db.execute(
                """
                insert or ignore into prospective_tests(
                    prospective_test_id, strategy_id, registration_json, minimum_sample,
                    scored_count, status, created_at, updated_at
                ) values (?, ?, ?, ?, 0, 'REGISTERED', ?, ?)
                """,
                (
                    registration.registration_id,
                    registration.strategy_id,
                    payload,
                    registration.minimum_sample,
                    registration.created_at.isoformat(),
                    registration.created_at.isoformat(),
                ),
            )
        self.registry.audit(
            AuditEvent(
                event_type="PROSPECTIVE_TEST_REGISTERED",
                entity_type="prospective_test",
                entity_id=registration.registration_id,
                occurred_at=registration.created_at,
                payload={
                    "strategy_id": registration.strategy_id,
                    "minimum_sample": registration.minimum_sample,
                    "acceptance_criteria": dict(registration.acceptance_criteria),
                },
            )
        )
        return registration.registration_id

    def append(self, observation: ProspectiveObservation) -> bool:
        payload = canonical_json(observation.to_dict())
        now = utc_now().isoformat()
        with self.registry.connect() as db:
            test = db.execute(
                "select strategy_id, status from prospective_tests where prospective_test_id = ?",
                (observation.prospective_test_id,),
            ).fetchone()
            if not test:
                raise RegistryNotFoundError(observation.prospective_test_id)
            if test["status"] in {"PASSED", "FAILED", "CLOSED"}:
                raise RuntimeError("prospective test is closed to new observations")
            existing = db.execute(
                "select payload_json from prospective_observations where observation_id = ?",
                (observation.observation_id,),
            ).fetchone()
            if existing:
                if existing["payload_json"] != payload:
                    raise RegistryConflictError("observation ID reused with different content")
                return False
            db.execute(
                """
                insert into prospective_observations(
                    observation_id, prospective_test_id, signal_time, available_at, status, payload_json
                ) values (?, ?, ?, ?, ?, ?)
                """,
                (
                    observation.observation_id,
                    observation.prospective_test_id,
                    observation.signal_time.isoformat(),
                    observation.available_at.isoformat(),
                    observation.status,
                    payload,
                ),
            )
            scored = int(
                db.execute(
                    "select count(*) from prospective_observations where prospective_test_id = ? and status = 'SCORED'",
                    (observation.prospective_test_id,),
                ).fetchone()[0]
            )
            db.execute(
                """
                update prospective_tests
                set scored_count = ?, status = case when status = 'REGISTERED' then 'RUNNING' else status end,
                    updated_at = ?
                where prospective_test_id = ?
                """,
                (scored, now, observation.prospective_test_id),
            )
        self.registry.audit(
            AuditEvent(
                event_type="PROSPECTIVE_OBSERVATION_RECORDED",
                entity_type="prospective_test",
                entity_id=observation.prospective_test_id,
                occurred_at=utc_now(),
                payload={
                    "observation_id": observation.observation_id,
                    "signal_id": observation.signal_id,
                    "status": observation.status,
                },
            )
        )
        return True

    def evaluate(self, prospective_test_id: str, *, close_when_minimum_reached: bool = True) -> dict[str, Any]:
        with self.registry.connect() as db:
            test = db.execute(
                "select * from prospective_tests where prospective_test_id = ?",
                (prospective_test_id,),
            ).fetchone()
            if not test:
                raise RegistryNotFoundError(prospective_test_id)
            registration = json.loads(test["registration_json"])
            rows = db.execute(
                "select payload_json from prospective_observations where prospective_test_id = ? order by signal_time",
                (prospective_test_id,),
            ).fetchall()
        observations = [json.loads(row["payload_json"]) for row in rows]
        scored = [item for item in observations if item.get("status") == "SCORED" and item.get("outcome")]
        returns = [
            float(item["outcome"]["return_pct"])
            for item in scored
            if _finite(item["outcome"].get("return_pct"))
        ]
        wins = sum(1 for value in returns if value > 0)
        gains = sum(value for value in returns if value > 0)
        losses = -sum(value for value in returns if value < 0)
        metrics = {
            "observations": len(observations),
            "scored": len(scored),
            "rejected": sum(1 for item in observations if item.get("status") == "REJECTED"),
            "pending": sum(1 for item in observations if item.get("status") == "PENDING"),
            "return_observations": len(returns),
            "win_rate": wins / len(returns) if returns else None,
            "average_return_pct": sum(returns) / len(returns) if returns else None,
            "total_return_points": sum(returns),
            "profit_factor": gains / losses if losses > 0 else None,
            "maximum_losing_streak": _maximum_losing_streak(returns),
        }
        minimum_reached = len(scored) >= int(test["minimum_sample"])
        criteria = registration.get("acceptance_criteria") or {}
        failures: list[str] = []
        if minimum_reached:
            _minimum(metrics, "win_rate", criteria.get("minimum_win_rate"), failures)
            _minimum(metrics, "average_return_pct", criteria.get("minimum_average_return_pct"), failures)
            _minimum(metrics, "profit_factor", criteria.get("minimum_profit_factor"), failures)
            _maximum(metrics, "maximum_losing_streak", criteria.get("maximum_losing_streak"), failures)
        status = str(test["status"])
        if minimum_reached and close_when_minimum_reached:
            if failures:
                status = "FAILED"
            elif criteria.get("manual_review_required"):
                status = "AWAITING_LOCKED_ANALYSIS"
            else:
                status = "PASSED"
            with self.registry.connect() as db:
                db.execute(
                    "update prospective_tests set status = ?, scored_count = ?, updated_at = ? where prospective_test_id = ?",
                    (status, len(scored), utc_now().isoformat(), prospective_test_id),
                )
            self.registry.audit(
                AuditEvent(
                    event_type="PROSPECTIVE_TEST_EVALUATED",
                    entity_type="prospective_test",
                    entity_id=prospective_test_id,
                    occurred_at=utc_now(),
                    payload={"status": status, "metrics": metrics, "failures": failures},
                )
            )
        return {
            "prospective_test_id": prospective_test_id,
            "strategy_id": test["strategy_id"],
            "status": status,
            "minimum_sample": int(test["minimum_sample"]),
            "minimum_reached": minimum_reached,
            "remaining": max(0, int(test["minimum_sample"]) - len(scored)),
            "metrics": metrics,
            "acceptance_criteria": criteria,
            "failures": failures,
        }


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _minimum(metrics: Mapping[str, Any], key: str, threshold: Any, failures: list[str]) -> None:
    if threshold is None:
        return
    value = metrics.get(key)
    if not _finite(value) or float(value) < float(threshold):
        failures.append(f"minimum_failed:{key}")


def _maximum(metrics: Mapping[str, Any], key: str, threshold: Any, failures: list[str]) -> None:
    if threshold is None:
        return
    value = metrics.get(key)
    if not _finite(value) or float(value) > float(threshold):
        failures.append(f"maximum_failed:{key}")


def _maximum_losing_streak(returns: Sequence[float]) -> int:
    maximum = 0
    current = 0
    for value in returns:
        if value < 0:
            current += 1
            maximum = max(maximum, current)
        else:
            current = 0
    return maximum
