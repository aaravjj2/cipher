from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any, Mapping

from .artifact_store import ArtifactReference, ArtifactStore
from .hashing import canonical_json, stable_id
from .models import AuditEvent, utc_now
from .registry import ResearchRegistry, RegistryNotFoundError


@dataclass(frozen=True)
class ReconciliationPolicy:
    maximum_trade_frequency_drift_pct: float = 35.0
    maximum_mean_return_drift_pct: float = 50.0
    maximum_drawdown_increase_pct: float = 50.0
    maximum_win_rate_drift_points: float = 0.15
    maximum_fill_decay_pct: float = 30.0
    require_lean: bool = True
    require_prospective: bool = True


class EvidenceReconciliationService:
    def __init__(
        self,
        *,
        registry: ResearchRegistry,
        artifact_store: ArtifactStore,
        policy: ReconciliationPolicy | None = None,
    ):
        self.registry = registry
        self.artifact_store = artifact_store
        self.policy = policy or ReconciliationPolicy()

    def reconcile(
        self,
        *,
        strategy_id: str,
        fast_experiment_id: str,
        lean_experiment_id: str | None,
        prospective_test_id: str | None,
        paper_metrics: Mapping[str, Any] | None = None,
    ) -> tuple[dict[str, Any], ArtifactReference]:
        fast = self.registry.experiment_summary(fast_experiment_id)
        self._strategy_match(strategy_id, fast, "fast")
        lean = self.registry.experiment_summary(lean_experiment_id) if lean_experiment_id else None
        if lean:
            self._strategy_match(strategy_id, lean, "LEAN")
        prospective = self._prospective(prospective_test_id) if prospective_test_id else None
        if prospective and prospective["strategy_id"] != strategy_id:
            raise ValueError("prospective test belongs to a different strategy")
        failures: list[str] = []
        warnings: list[str] = []
        if self.policy.require_lean and not lean:
            failures.append("lean_evidence_missing")
        if self.policy.require_prospective and not prospective:
            failures.append("prospective_evidence_missing")
        fast_metrics = _result_metrics(fast)
        lean_metrics = _result_metrics(lean) if lean else {}
        prospective_metrics = (prospective or {}).get("metrics") or {}
        paper = dict(paper_metrics or {})
        comparisons: dict[str, Any] = {}
        if lean:
            comparisons["fast_vs_lean"] = self._compare(
                fast_metrics,
                lean_metrics,
                failures,
                warnings,
                label="fast_vs_lean",
            )
        if prospective:
            comparisons["lean_or_fast_vs_prospective"] = self._compare(
                lean_metrics or fast_metrics,
                prospective_metrics,
                failures,
                warnings,
                label="historical_vs_prospective",
            )
        if paper:
            comparisons["prospective_or_historical_vs_paper"] = self._compare(
                prospective_metrics or lean_metrics or fast_metrics,
                paper,
                failures,
                warnings,
                label="evidence_vs_paper",
            )
        status = "BLOCKED" if failures else "DEGRADED" if warnings else "PASS"
        actions: list[str] = []
        if status == "BLOCKED":
            actions.extend(("open_research_issue", "pause_new_shadow_entries"))
        elif status == "DEGRADED":
            actions.extend(("open_research_issue", "schedule_fixed_diagnostic_experiment"))
        payload = {
            "schema_version": 1,
            "strategy_id": strategy_id,
            "created_at": utc_now().isoformat(),
            "status": status,
            "evidence": {
                "fast_experiment_id": fast_experiment_id,
                "lean_experiment_id": lean_experiment_id,
                "prospective_test_id": prospective_test_id,
                "paper_metrics_present": bool(paper_metrics),
            },
            "metrics": {
                "fast": fast_metrics,
                "lean": lean_metrics,
                "prospective": prospective_metrics,
                "paper": paper,
            },
            "comparisons": comparisons,
            "failures": failures,
            "warnings": warnings,
            "allowed_automated_actions": actions,
            "human_approval_required_for": [
                "strategy_rule_change",
                "feature_allowed_use_change",
                "position_sizing_change",
                "strategy_promotion",
                "broker_execution",
            ],
        }
        reconciliation_id = stable_id("reconciliation", payload)
        payload["reconciliation_id"] = reconciliation_id
        artifact = self.artifact_store.put_json(
            payload,
            metadata={"kind": "evidence_reconciliation", "strategy_id": strategy_id},
        )
        self.registry.register_artifact(artifact.to_dict())
        serialized = canonical_json(payload)
        with self.registry.connect() as db:
            existing = db.execute(
                "select payload_json from evidence_reconciliations where reconciliation_id = ?",
                (reconciliation_id,),
            ).fetchone()
            if existing and existing["payload_json"] != serialized:
                raise RuntimeError("reconciliation ID collision")
            db.execute(
                """
                insert or ignore into evidence_reconciliations(
                    reconciliation_id, strategy_id, created_at, status, payload_json
                ) values (?, ?, ?, ?, ?)
                """,
                (reconciliation_id, strategy_id, payload["created_at"], status, serialized),
            )
        self.registry.audit(
            AuditEvent(
                event_type="EVIDENCE_RECONCILED",
                entity_type="strategy",
                entity_id=strategy_id,
                occurred_at=utc_now(),
                payload={
                    "reconciliation_id": reconciliation_id,
                    "status": status,
                    "failures": failures,
                    "warnings": warnings,
                    "artifact_id": artifact.artifact_id,
                },
            )
        )
        return payload, artifact

    def _compare(
        self,
        baseline: Mapping[str, Any],
        observed: Mapping[str, Any],
        failures: list[str],
        warnings: list[str],
        *,
        label: str,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        trade_drift = _relative_drift(baseline.get("trade_count"), observed.get("trade_count"))
        result["trade_frequency_drift_pct"] = trade_drift
        if trade_drift is not None and abs(trade_drift) > self.policy.maximum_trade_frequency_drift_pct:
            warnings.append(f"{label}:trade_frequency_drift")
        return_drift = _relative_drift(
            _mean_return(baseline),
            _mean_return(observed),
        )
        result["mean_return_drift_pct"] = return_drift
        if return_drift is not None and return_drift < -self.policy.maximum_mean_return_drift_pct:
            failures.append(f"{label}:mean_return_decay")
        baseline_drawdown = _number(baseline.get("maximum_drawdown_pct"))
        observed_drawdown = _number(observed.get("maximum_drawdown_pct"))
        drawdown_increase = _relative_drift(baseline_drawdown, observed_drawdown)
        result["drawdown_increase_pct"] = drawdown_increase
        if drawdown_increase is not None and drawdown_increase > self.policy.maximum_drawdown_increase_pct:
            failures.append(f"{label}:drawdown_increase")
        baseline_win = _number(baseline.get("win_rate"))
        observed_win = _number(observed.get("win_rate"))
        win_drift = None if baseline_win is None or observed_win is None else observed_win - baseline_win
        result["win_rate_drift_points"] = win_drift
        if win_drift is not None and win_drift < -self.policy.maximum_win_rate_drift_points:
            warnings.append(f"{label}:win_rate_decay")
        baseline_fill = _number(baseline.get("average_fill_cost") or baseline.get("average_slippage"))
        observed_fill = _number(observed.get("average_fill_cost") or observed.get("average_slippage"))
        fill_decay = _relative_drift(baseline_fill, observed_fill)
        result["fill_decay_pct"] = fill_decay
        if fill_decay is not None and fill_decay > self.policy.maximum_fill_decay_pct:
            warnings.append(f"{label}:fill_decay")
        return result

    @staticmethod
    def _strategy_match(strategy_id: str, summary: Mapping[str, Any], label: str) -> None:
        if summary.get("strategy_id") != strategy_id:
            raise ValueError(f"{label} experiment belongs to a different strategy")

    def _prospective(self, prospective_test_id: str) -> dict[str, Any]:
        with self.registry.connect() as db:
            test = db.execute(
                "select * from prospective_tests where prospective_test_id = ?",
                (prospective_test_id,),
            ).fetchone()
            if not test:
                raise RegistryNotFoundError(prospective_test_id)
            rows = db.execute(
                "select payload_json from prospective_observations where prospective_test_id = ? and status = 'SCORED'",
                (prospective_test_id,),
            ).fetchall()
        observations = [json.loads(row["payload_json"]) for row in rows]
        returns = [
            _number(item.get("outcome", {}).get("return_pct"))
            for item in observations
        ]
        clean = [value for value in returns if value is not None]
        return {
            "strategy_id": test["strategy_id"],
            "status": test["status"],
            "metrics": {
                "trade_count": len(clean),
                "mean_trade_return_pct": sum(clean) / len(clean) if clean else None,
                "win_rate": sum(1 for value in clean if value > 0) / len(clean) if clean else None,
                "profit_factor": _profit_factor(clean),
            },
        }


def _result_metrics(summary: Mapping[str, Any] | None) -> dict[str, Any]:
    if not summary:
        return {}
    result = summary.get("result") or {}
    return dict(result.get("metrics") or {})


def _mean_return(metrics: Mapping[str, Any]) -> float | None:
    return _number(
        metrics.get("mean_trade_return_pct")
        if metrics.get("mean_trade_return_pct") is not None
        else metrics.get("average_return_pct")
    )


def _relative_drift(baseline: Any, observed: Any) -> float | None:
    base = _number(baseline)
    obs = _number(observed)
    if base is None or obs is None:
        return None
    if abs(base) < 1e-12:
        return None
    return (obs - base) / abs(base) * 100.0


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _profit_factor(returns: list[float]) -> float | None:
    gains = sum(value for value in returns if value > 0)
    losses = -sum(value for value in returns if value < 0)
    return gains / losses if losses > 0 else None
