from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .artifact_store import ArtifactStore
from .experiments import EquityPoint, StandardBacktestOutput, TradeRecord
from .hashing import sha256_file, stable_id
from .models import (
    AuditEvent,
    EngineKind,
    ExperimentManifest,
    ExperimentResult,
    ExperimentVerdict,
    utc_now,
)
from .registry import ResearchRegistry


@dataclass(frozen=True)
class LeanAuditValidation:
    research_grade: bool
    reconciliation_passed: bool
    failures: tuple[str, ...]
    warnings: tuple[str, ...]
    checks: Mapping[str, Any]

    @property
    def promotable(self) -> bool:
        return self.research_grade and self.reconciliation_passed and not self.failures


class LeanAuditValidator:
    """Validate a LEAN audit export before it can count as replication evidence."""

    REQUIRED_TOP_LEVEL = (
        "schema_version",
        "generated_at",
        "lean_version",
        "strategy_id",
        "dataset_id",
        "code_hash",
        "date_range",
        "metrics",
        "trades",
        "quality_checks",
        "reconciliation",
    )

    def validate(self, payload: Mapping[str, Any]) -> LeanAuditValidation:
        failures: list[str] = []
        warnings: list[str] = []
        for key in self.REQUIRED_TOP_LEVEL:
            if key not in payload:
                failures.append(f"missing:{key}")
        checks = dict(payload.get("quality_checks") or {})
        reconciliation = dict(payload.get("reconciliation") or {})
        date_range = dict(payload.get("date_range") or {})
        research_grade = bool(payload.get("research_grade"))
        if not research_grade:
            failures.append("research_grade_false")
        if not checks.get("point_in_time_data"):
            failures.append("point_in_time_data_failed")
        if not checks.get("chronological_event_loop"):
            failures.append("chronological_event_loop_failed")
        if not checks.get("contract_selection_audited"):
            failures.append("contract_selection_not_audited")
        if not checks.get("fill_evidence_observed"):
            failures.append("observed_fill_evidence_missing")
        if checks.get("historical_nbbo_required") and not checks.get("historical_nbbo_present"):
            failures.append("historical_nbbo_missing")
        if not checks.get("corporate_actions_handled"):
            failures.append("corporate_actions_not_verified")
        if not checks.get("survivorship_bias_controlled"):
            failures.append("survivorship_bias_not_controlled")
        if not date_range.get("start") or not date_range.get("end"):
            failures.append("date_range_incomplete")
        trades = payload.get("trades") or []
        metrics = dict(payload.get("metrics") or {})
        declared_count = metrics.get("trade_count")
        if declared_count is not None and int(declared_count) != len(trades):
            failures.append("trade_count_reconciliation_failed")
        if not trades:
            failures.append("zero_trade_result")
        for index, trade in enumerate(trades):
            if not isinstance(trade, Mapping):
                failures.append(f"trade_{index}_not_object")
                continue
            for field in ("trade_id", "symbol", "entry_time", "entry_price", "quantity"):
                if trade.get(field) in (None, ""):
                    failures.append(f"trade_{index}_missing_{field}")
            if trade.get("exit_time") is None and not trade.get("open_at_end"):
                failures.append(f"trade_{index}_missing_exit")
            if trade.get("entry_bid") is None or trade.get("entry_ask") is None:
                failures.append(f"trade_{index}_entry_nbbo_missing")
            if trade.get("exit_time") and (trade.get("exit_bid") is None or trade.get("exit_ask") is None):
                failures.append(f"trade_{index}_exit_nbbo_missing")
        reconciliation_passed = bool(reconciliation.get("passed"))
        for name in (
            "cash_ledger_balanced",
            "position_ledger_balanced",
            "order_fill_counts_match",
            "fees_included",
            "slippage_included",
        ):
            if not reconciliation.get(name):
                failures.append(f"reconciliation_failed:{name}")
        if not reconciliation_passed:
            failures.append("reconciliation_passed_false")
        if not payload.get("benchmark"):
            warnings.append("benchmark_missing")
        if not payload.get("regime_metrics"):
            warnings.append("regime_metrics_missing")
        if not payload.get("statistical_tests"):
            warnings.append("statistical_tests_missing")
        return LeanAuditValidation(
            research_grade=research_grade,
            reconciliation_passed=reconciliation_passed,
            failures=tuple(sorted(set(failures))),
            warnings=tuple(sorted(set(warnings))),
            checks={
                "quality_checks": checks,
                "reconciliation": reconciliation,
                "date_range": date_range,
                "trade_count": len(trades),
            },
        )


class LeanAuditAdapter:
    def __init__(self, payload: Mapping[str, Any], validation: LeanAuditValidation):
        self.payload = dict(payload)
        self.validation = validation

    def output(self) -> StandardBacktestOutput:
        trades = tuple(self._trade(item, index) for index, item in enumerate(self.payload.get("trades") or ()))
        equity = tuple(
            EquityPoint(
                timestamp=str(item["timestamp"]),
                equity=float(item["equity"]),
                cash=_optional_float(item.get("cash")),
                gross_exposure=_optional_float(item.get("gross_exposure")),
                net_exposure=_optional_float(item.get("net_exposure")),
            )
            for item in self.payload.get("equity_curve") or ()
            if isinstance(item, Mapping) and item.get("timestamp") is not None and item.get("equity") is not None
        )
        quality = {
            **dict(self.payload.get("quality_checks") or {}),
            "passed": self.validation.promotable,
            "reconciliation_passed": self.validation.reconciliation_passed,
            "lean_audit_promotable": self.validation.promotable,
            "lean_validation_failures": list(self.validation.failures),
            "lean_validation_warnings": list(self.validation.warnings),
        }
        return StandardBacktestOutput(
            trades=trades,
            equity_curve=equity,
            metrics=dict(self.payload.get("metrics") or {}),
            benchmark_metrics=dict(self.payload.get("benchmark") or {}),
            regime_metrics=dict(self.payload.get("regime_metrics") or {}),
            statistical_tests=dict(self.payload.get("statistical_tests") or {}),
            quality_checks=quality,
            exclusions=tuple(self.payload.get("exclusions") or ()),
            assumptions={
                "engine": "LEAN",
                "lean_version": self.payload.get("lean_version"),
                "fill_model": self.payload.get("fill_model"),
                "date_range": self.payload.get("date_range"),
            },
            notes=tuple(self.payload.get("notes") or ()) + self.validation.warnings,
        )

    @staticmethod
    def _trade(item: Mapping[str, Any], index: int) -> TradeRecord:
        entry_price = float(item.get("entry_price") or 0.0)
        exit_price = _optional_float(item.get("exit_price"))
        return_pct = _optional_float(item.get("return_pct"))
        net_pnl = _optional_float(item.get("net_pnl"))
        if return_pct is None and exit_price is not None and entry_price:
            direction = str(item.get("direction") or "long").lower()
            multiplier = -1.0 if direction in {"short", "bearish", "sell"} else 1.0
            return_pct = (exit_price - entry_price) / entry_price * 100.0 * multiplier
        return TradeRecord(
            trade_id=str(item.get("trade_id") or stable_id("lean_trade", {"index": index, "trade": dict(item)})),
            symbol=str(item.get("symbol") or "UNKNOWN").upper(),
            direction=str(item.get("direction") or "unknown"),
            entry_time=str(item.get("entry_time") or ""),
            exit_time=None if item.get("exit_time") is None else str(item.get("exit_time")),
            entry_price=entry_price,
            exit_price=exit_price,
            quantity=float(item.get("quantity") or 0.0),
            gross_pnl=_optional_float(item.get("gross_pnl")),
            net_pnl=net_pnl,
            return_pct=return_pct,
            metadata={
                "entry_bid": item.get("entry_bid"),
                "entry_ask": item.get("entry_ask"),
                "exit_bid": item.get("exit_bid"),
                "exit_ask": item.get("exit_ask"),
                "fees": item.get("fees"),
                "slippage": item.get("slippage"),
                "contract_selection": item.get("contract_selection"),
            },
        )


class LeanEvidenceService:
    def __init__(self, registry: ResearchRegistry, artifacts: ArtifactStore):
        self.registry = registry
        self.artifacts = artifacts
        self.validator = LeanAuditValidator()

    def import_audit(self, path: str | Path) -> tuple[ExperimentManifest, ExperimentResult, LeanAuditValidation]:
        candidate = Path(path)
        payload = json.loads(candidate.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("LEAN audit payload must be an object")
        validation = self.validator.validate(payload)
        started_at = _timestamp(payload.get("generated_at") or datetime.fromtimestamp(candidate.stat().st_mtime, timezone.utc))
        manifest = ExperimentManifest(
            strategy_id=str(payload.get("strategy_id") or "strategy_missing"),
            dataset_id=str(payload.get("dataset_id") or "dataset_missing"),
            feature_set_id=str(payload.get("feature_set_id") or "feature_set_none"),
            parameter_set=dict(payload.get("parameters") or {}),
            engine=EngineKind.LEAN,
            code_hash=str(payload.get("code_hash") or sha256_file(candidate)),
            runtime_environment_id=str(
                payload.get("runtime_environment_id")
                or stable_id("lean_runtime", {"lean_version": payload.get("lean_version")})
            ),
            random_seed=int(payload.get("random_seed") or 0),
            started_at=started_at,
            preregistered=bool(payload.get("preregistered", False)),
            hypothesis=str(payload.get("hypothesis") or "LEAN replication audit"),
            parent_experiment_id=payload.get("parent_experiment_id"),
        )
        self.registry.begin_experiment(manifest)
        raw_artifact = self.artifacts.put_file(
            candidate,
            content_type="application/json",
            metadata={"kind": "lean_audit_raw", "experiment_id": manifest.experiment_id},
        )
        self.registry.register_artifact(raw_artifact.to_dict())
        self.registry.link_experiment_artifact(manifest.experiment_id, "lean_audit_raw", raw_artifact.artifact_id)
        normalized = LeanAuditAdapter(payload, validation).output()
        normalized_artifact = self.artifacts.put_json(
            normalized.to_dict(),
            metadata={"kind": "lean_audit_normalized", "experiment_id": manifest.experiment_id},
        )
        self.registry.register_artifact(normalized_artifact.to_dict())
        self.registry.link_experiment_artifact(manifest.experiment_id, "standard_result", normalized_artifact.artifact_id)
        verdict = ExperimentVerdict.PASS if validation.promotable else ExperimentVerdict.BLOCKED
        result = ExperimentResult(
            experiment_id=manifest.experiment_id,
            completed_at=utc_now(),
            verdict=verdict,
            metrics=normalized.normalized_metrics(),
            statistical_tests=dict(normalized.statistical_tests),
            exclusions=normalized.exclusions,
            quality_checks=dict(normalized.quality_checks),
            artifacts={
                "lean_audit_raw": raw_artifact.artifact_id,
                "standard_result": normalized_artifact.artifact_id,
            },
            notes=tuple(normalized.notes) + validation.failures,
        )
        self.registry.complete_experiment(result)
        self.registry.audit(
            AuditEvent(
                event_type="LEAN_AUDIT_IMPORTED",
                entity_type="experiment",
                entity_id=manifest.experiment_id,
                occurred_at=utc_now(),
                payload={
                    "verdict": verdict.value,
                    "promotable": validation.promotable,
                    "failures": list(validation.failures),
                    "warnings": list(validation.warnings),
                },
            )
        )
        return manifest, result, validation


def _timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("LEAN audit timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
