#!/usr/bin/env python3
"""One-time locked validation of pre-download autonomous candidates.

All autonomous strategy specifications that existed before the broad Alpaca
panel download are evaluated together on the corrected 2016-2019 dataset. Holm
correction is applied across the complete frozen candidate family. No adaptive
children are generated from this validation dataset.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.research_platform.artifact_store import ArtifactStore  # noqa: E402
from core.research_platform.experiments import CallableExperimentAdapter, ExperimentRunner, runtime_environment_id  # noqa: E402
from core.research_platform.hashing import sha256_file, stable_id  # noqa: E402
from core.research_platform.models import EngineKind, ExperimentManifest, StrategySpec  # noqa: E402
from core.research_platform.registry import ResearchRegistry  # noqa: E402
from core.research_platform.strategy_research_loop import (  # noqa: E402
    CanonicalPanel,
    StrategyCandidate,
    StrategyResearchPolicy,
    holm_adjust,
    load_canonical_daily_panel,
    run_candidate_backtest,
)

REGISTRY_PATH = ROOT / "data" / "governance" / "research_registry.sqlite"
DATASET_ID = "ds_fb1e8d9aeb51f12407b08123"
ORIGINAL_DATASET_ID = "ds_380c76da95f0c3787529c6b8"
DATA_ROOT = ROOT / "data" / "historical_equities" / "broad_research_panel_v1"
FREEZE_REPORT = DATA_ROOT / "broad_panel_registration.json"
OUTPUT_ROOT = ROOT / "data" / "governance" / "strategy_research_validation"
ARTIFACT_ROOT = ROOT / "data" / "artifacts" / "strategy_research_validation"
CACHE_ROOT = ROOT / "data" / "cache" / "strategy_research_validation"


def load_frozen_strategies(registry: ResearchRegistry) -> tuple[list[tuple[StrategySpec, StrategyCandidate]], dict[str, Any]]:
    freeze = json.loads(FREEZE_REPORT.read_text(encoding="utf-8"))["candidate_set_frozen_before_download"]
    expected_count = int(freeze["strategy_count"])
    expected_hash = str(freeze["hash"])
    with registry.connect() as db:
        rows = db.execute(
            """
            select strategy_id, payload_json
            from strategies
            where name like 'autonomous_price_only_%'
            order by strategy_id
            """
        ).fetchall()
    if len(rows) != expected_count:
        raise RuntimeError(f"candidate family changed after freeze: expected {expected_count}, found {len(rows)}")
    observed_hash = stable_id("candidate_set_pre_broad_download", [(row[0], row[1]) for row in rows], length=64)
    if observed_hash != expected_hash:
        raise RuntimeError("candidate-set hash no longer matches the pre-download freeze")
    output: list[tuple[StrategySpec, StrategyCandidate]] = []
    for _, payload_json in rows:
        payload = json.loads(payload_json)
        spec = StrategySpec(**payload)
        signal = payload["signal_rule"]
        version = str(payload.get("version") or "research-g0-v1")
        generation = 0
        if version.startswith("research-g"):
            try:
                generation = int(version.split("research-g", 1)[1].split("-", 1)[0])
            except ValueError:
                generation = 0
        candidate = StrategyCandidate(
            family=str(signal["family"]),
            parameters=dict(signal["parameters"]),
            generation=generation,
            parent_candidate_id=signal.get("parent_candidate_id"),
            hypothesis=str(payload.get("description") or "Pre-download frozen candidate validation."),
            candidate_id=str(signal["candidate_id"]),
        )
        output.append((spec, candidate))
    return output, {"count": expected_count, "hash": expected_hash}


def parent_experiment_id(registry: ResearchRegistry, strategy_id: str) -> str | None:
    with registry.connect() as db:
        row = db.execute(
            """
            select experiment_id from experiments
            where strategy_id=? and dataset_id=? and status='COMPLETED'
            order by completed_at desc limit 1
            """,
            (strategy_id, ORIGINAL_DATASET_ID),
        ).fetchone()
    return str(row[0]) if row else None


def main() -> int:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    registry = ResearchRegistry(REGISTRY_PATH)
    frozen, freeze = load_frozen_strategies(registry)
    loaded = load_canonical_daily_panel(REGISTRY_PATH, DATASET_ID, cache_root=CACHE_ROOT)
    panel = CanonicalPanel(
        dataset_id=loaded.dataset_id,
        dataset_name=loaded.dataset_name,
        frame=loaded.frame,
        raw_object_count=loaded.raw_object_count,
        source_paths=loaded.source_paths,
        lineage_hash=loaded.lineage_hash,
        research_role="independent_temporal_validation_fixed_pre_download_candidate_family",
    )
    policy = StrategyResearchPolicy(
        batch_size=len(frozen),
        maximum_total_candidates=len(frozen),
        maximum_generation=0,
        maximum_adaptive_children_per_cycle=0,
        slippage_bps_per_side=10.0,
        minimum_sessions=750,
        minimum_trades=30,
        minimum_profit_factor=1.10,
        maximum_drawdown_pct=25.0,
        maximum_holm_adjusted_p_value=0.10,
        walk_forward_folds=4,
        random_seed=271828,
    )
    evaluated = []
    for spec, candidate in frozen:
        evaluated.append((spec, run_candidate_backtest(panel, candidate, policy)))
    adjusted = holm_adjust({item.candidate.candidate_id: item.raw_p_value for _, item in evaluated})
    runner = ExperimentRunner(registry=registry, artifact_store=ArtifactStore(ARTIFACT_ROOT))
    now = datetime.now(timezone.utc)
    code_hash = sha256_file(Path(__file__))
    runtime_id = runtime_environment_id()
    family_ids = [item.candidate.candidate_id for _, item in evaluated]
    records: list[dict[str, Any]] = []
    for spec, item in evaluated:
        adjusted_output = replace(
            item.output,
            statistical_tests={
                **dict(item.output.statistical_tests),
                "holm_adjusted_p_value": adjusted[item.candidate.candidate_id],
                "multiple_testing_family": family_ids,
                "family_size": len(family_ids),
                "candidate_family_frozen_before_data_download": True,
            },
            quality_checks={
                **dict(item.output.quality_checks),
                "independent_temporal_validation": True,
                "candidate_family_frozen_before_data_download": True,
                "adaptive_feedback_allowed": False,
                "final_holdout_claim": False,
                "automatic_promotion": False,
            },
            assumptions={
                **dict(item.output.assumptions),
                "validation_window": "2016-01-04 through 2019-12-31",
                "candidate_family_freeze_hash": freeze["hash"],
                "candidate_family_size": freeze["count"],
                "adaptive_feedback_allowed": False,
            },
            notes=tuple(item.output.notes) + (
                "Independent reverse-time validation of a candidate family frozen before this dataset was downloaded.",
                "The validation dataset cannot generate adaptive children or authorize promotion.",
            ),
        )
        manifest = ExperimentManifest(
            strategy_id=spec.strategy_id,
            dataset_id=DATASET_ID,
            feature_set_id="broad_daily_price_only_locked_validation_v1",
            parameter_set={
                "candidate": item.candidate.to_dict(),
                "candidate_family_freeze": freeze,
                "validation_role": panel.research_role,
                "adaptive_feedback_allowed": False,
            },
            engine=EngineKind.CIPHER_FAST,
            code_hash=code_hash,
            runtime_environment_id=runtime_id,
            random_seed=policy.random_seed,
            started_at=now,
            preregistered=True,
            hypothesis=f"Locked temporal validation: {item.candidate.hypothesis}",
            parent_experiment_id=parent_experiment_id(registry, spec.strategy_id),
        )
        result = runner.run(
            manifest,
            strategy=spec,
            adapter=CallableExperimentAdapter(lambda _manifest, output=adjusted_output: output),
        )
        records.append(
            {
                "candidate": item.candidate.to_dict(),
                "strategy_id": spec.strategy_id,
                "experiment_id": manifest.experiment_id,
                "verdict": result.verdict.value,
                "metrics": dict(result.metrics),
                "statistical_tests": dict(result.statistical_tests),
                "quality_checks": dict(result.quality_checks),
                "fold_returns_pct": list(item.fold_returns_pct),
                "composite_score": item.composite_score,
            }
        )
    ranking = sorted(
        records,
        key=lambda row: (
            row["verdict"] in {"PASS", "CONDITIONAL_PASS"},
            float(row.get("composite_score") or -1e9),
        ),
        reverse=True,
    )
    payload = {
        "schema_version": 1,
        "created_at": now.isoformat(),
        "status": "completed",
        "dataset": {
            "dataset_id": panel.dataset_id,
            "dataset_name": panel.dataset_name,
            "research_role": panel.research_role,
            "sessions": int(panel.frame["date"].nunique()),
            "symbols": sorted(panel.frame["ticker"].unique().tolist()),
            "lineage_hash": panel.lineage_hash,
        },
        "candidate_family_freeze": freeze,
        "multiple_testing": "Holm across the complete 92-candidate frozen family",
        "results": records,
        "ranking": ranking,
        "summary": {
            "candidates": len(records),
            "passes": sum(row["verdict"] == "PASS" for row in records),
            "conditional_passes": sum(row["verdict"] == "CONDITIONAL_PASS" for row in records),
            "failures": sum(row["verdict"] == "FAIL" for row in records),
            "errors": sum(row["verdict"] == "ERROR" for row in records),
        },
        "adaptive_feedback_allowed": False,
        "automatic_promotion": False,
        "paper_or_live_execution": False,
        "execution_authority": False,
    }
    latest = OUTPUT_ROOT / "latest_locked_broad_validation.json"
    timestamped = OUTPUT_ROOT / f"locked_broad_validation_{now.strftime('%Y%m%dT%H%M%SZ')}.json"
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    latest.write_text(encoded, encoding="utf-8")
    timestamped.write_text(encoded, encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "dataset_id": panel.dataset_id,
        "candidate_count": len(records),
        **payload["summary"],
        "leader_candidate_id": (ranking[0]["candidate"]["candidate_id"] if ranking else None),
        "leader_verdict": (ranking[0]["verdict"] if ranking else None),
        "leader_score": (ranking[0]["composite_score"] if ranking else None),
        "report": str(latest),
        "automatic_promotion": False,
        "execution_authority": False,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
