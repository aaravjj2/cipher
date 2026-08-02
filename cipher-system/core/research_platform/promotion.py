from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .models import ExperimentVerdict, PromotionEvent, PromotionState, utc_now
from .registry import ResearchRegistry, RegistryNotFoundError


class PromotionBlockedError(RuntimeError):
    pass


_ALLOWED_TRANSITIONS: dict[PromotionState, set[PromotionState]] = {
    PromotionState.IDEA: {PromotionState.SPECIFIED, PromotionState.REJECTED, PromotionState.RETIRED},
    PromotionState.SPECIFIED: {PromotionState.DATA_VALIDATED, PromotionState.REJECTED, PromotionState.RETIRED},
    PromotionState.DATA_VALIDATED: {PromotionState.FAST_BACKTESTED, PromotionState.REJECTED, PromotionState.RETIRED},
    PromotionState.FAST_BACKTESTED: {PromotionState.WALK_FORWARD_PASSED, PromotionState.REJECTED, PromotionState.RETIRED},
    PromotionState.WALK_FORWARD_PASSED: {PromotionState.LEAN_REPLICATED, PromotionState.REJECTED, PromotionState.RETIRED},
    PromotionState.LEAN_REPLICATED: {PromotionState.PROSPECTIVE_SHADOW, PromotionState.REJECTED, PromotionState.RETIRED},
    PromotionState.PROSPECTIVE_SHADOW: {PromotionState.PAPER_ELIGIBLE, PromotionState.REJECTED, PromotionState.RETIRED},
    PromotionState.PAPER_ELIGIBLE: {PromotionState.LIVE_REVIEW_REQUIRED, PromotionState.REJECTED, PromotionState.RETIRED},
    PromotionState.LIVE_REVIEW_REQUIRED: {PromotionState.REJECTED, PromotionState.RETIRED},
    PromotionState.REJECTED: {PromotionState.SPECIFIED, PromotionState.RETIRED},
    PromotionState.RETIRED: set(),
}


@dataclass(frozen=True)
class GateEvidence:
    evidence_ids: tuple[str, ...]
    metadata: Mapping[str, Any]


class PromotionService:
    """Enforce ordered strategy graduation without providing a live state."""

    def __init__(self, registry: ResearchRegistry):
        self.registry = registry

    def promote(
        self,
        strategy_id: str,
        to_state: PromotionState,
        *,
        actor: str,
        reason: str,
        evidence_ids: Sequence[str] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> PromotionEvent:
        current = self.registry.current_state(strategy_id)
        if to_state not in _ALLOWED_TRANSITIONS[current]:
            raise PromotionBlockedError(f"transition {current.value} -> {to_state.value} is not allowed")
        evidence = GateEvidence(tuple(sorted(set(evidence_ids))), dict(metadata or {}))
        self._validate_gate(strategy_id, current, to_state, evidence)
        event = PromotionEvent(
            strategy_id=strategy_id,
            from_state=current,
            to_state=to_state,
            decided_at=utc_now(),
            actor=actor,
            reason=reason,
            evidence_ids=evidence.evidence_ids,
            metadata=evidence.metadata,
        )
        self.registry.record_promotion(event)
        return event

    def reject(
        self,
        strategy_id: str,
        *,
        actor: str,
        reason: str,
        evidence_ids: Sequence[str] = (),
    ) -> PromotionEvent:
        return self.promote(
            strategy_id,
            PromotionState.REJECTED,
            actor=actor,
            reason=reason,
            evidence_ids=evidence_ids,
        )

    def _validate_gate(
        self,
        strategy_id: str,
        current: PromotionState,
        target: PromotionState,
        evidence: GateEvidence,
    ) -> None:
        if target in {PromotionState.REJECTED, PromotionState.RETIRED, PromotionState.SPECIFIED}:
            return
        if target == PromotionState.DATA_VALIDATED:
            dataset_id = str(evidence.metadata.get("dataset_id") or "")
            if not dataset_id:
                raise PromotionBlockedError("DATA_VALIDATED requires metadata.dataset_id")
            dataset = self.registry.get_payload("datasets", "dataset_id", dataset_id)
            checks = dataset.get("quality_checks") or {}
            passed = checks.get("passed")
            failures = checks.get("failures") or []
            if passed is False or failures:
                raise PromotionBlockedError("dataset quality checks did not pass")
            return
        if target in {
            PromotionState.FAST_BACKTESTED,
            PromotionState.WALK_FORWARD_PASSED,
            PromotionState.LEAN_REPLICATED,
        }:
            required_engine = {
                PromotionState.FAST_BACKTESTED: "cipher_fast",
                PromotionState.WALK_FORWARD_PASSED: "cipher_fast",
                PromotionState.LEAN_REPLICATED: "lean",
            }[target]
            experiment_id = str(evidence.metadata.get("experiment_id") or "")
            if not experiment_id:
                raise PromotionBlockedError(f"{target.value} requires metadata.experiment_id")
            summary = self.registry.experiment_summary(experiment_id)
            if summary["strategy_id"] != strategy_id:
                raise PromotionBlockedError("experiment belongs to a different strategy")
            if summary["engine"] != required_engine:
                raise PromotionBlockedError(f"{target.value} requires engine {required_engine}")
            result = summary.get("result") or {}
            verdict = result.get("verdict")
            if verdict not in {ExperimentVerdict.PASS.value, ExperimentVerdict.CONDITIONAL_PASS.value}:
                raise PromotionBlockedError(f"experiment verdict is not promotable: {verdict}")
            if target == PromotionState.WALK_FORWARD_PASSED:
                checks = result.get("quality_checks") or {}
                if not bool(checks.get("walk_forward_passed")):
                    raise PromotionBlockedError("walk-forward evidence is missing or failed")
            if target == PromotionState.LEAN_REPLICATED:
                checks = result.get("quality_checks") or {}
                if not bool(checks.get("reconciliation_passed")):
                    raise PromotionBlockedError("LEAN reconciliation evidence is missing or failed")
            return
        if target == PromotionState.PROSPECTIVE_SHADOW:
            prospective_test_id = str(evidence.metadata.get("prospective_test_id") or "")
            if not prospective_test_id:
                raise PromotionBlockedError("PROSPECTIVE_SHADOW requires a prospective registration")
            return
        if target == PromotionState.PAPER_ELIGIBLE:
            prospective_test_id = str(evidence.metadata.get("prospective_test_id") or "")
            if not prospective_test_id:
                raise PromotionBlockedError("PAPER_ELIGIBLE requires metadata.prospective_test_id")
            with self.registry.connect() as db:
                row = db.execute(
                    "select strategy_id, minimum_sample, scored_count, status from prospective_tests where prospective_test_id = ?",
                    (prospective_test_id,),
                ).fetchone()
            if not row:
                raise RegistryNotFoundError(prospective_test_id)
            if row["strategy_id"] != strategy_id:
                raise PromotionBlockedError("prospective test belongs to a different strategy")
            if int(row["scored_count"]) < int(row["minimum_sample"]):
                raise PromotionBlockedError("prospective minimum sample has not been reached")
            if row["status"] != "PASSED":
                raise PromotionBlockedError("prospective test has not passed")
            return
        if target == PromotionState.LIVE_REVIEW_REQUIRED:
            if not bool(evidence.metadata.get("human_risk_review_requested")):
                raise PromotionBlockedError("LIVE_REVIEW_REQUIRED requires an explicit human risk-review request")
            if evidence.metadata.get("live_execution_enabled"):
                raise PromotionBlockedError("promotion cannot enable live execution")
            return
        raise PromotionBlockedError(f"no gate validator exists for {current.value} -> {target.value}")


def allowed_transitions(state: PromotionState) -> tuple[PromotionState, ...]:
    return tuple(sorted(_ALLOWED_TRANSITIONS[state], key=lambda item: item.value))
