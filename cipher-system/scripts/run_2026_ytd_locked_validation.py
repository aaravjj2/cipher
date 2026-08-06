#!/usr/bin/env python3
"""Locked 2026 YTD validation of all pre-download candidate identities.

The registered dataset contains 2024-2025 warmup bars, but the canonical
backtest evaluation window resets positions and scores only 2026-01-02 through
2026-08-04. Holm correction is applied across all 194 candidate identities that
were frozen before the provider request. The holdout cannot create adaptive
children, promote strategies, or trade.
"""
from __future__ import annotations

import json
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
DATASET_ID = "ds_f20f2e15e7d1041ce6a1858d"
FREEZE_REPORT = ROOT / "data" / "historical_equities" / "broad_2026_ytd_holdout_v1" / "holdout_registration.json"
OUTPUT_ROOT = ROOT / "data" / "governance" / "strategy_research_2026_ytd"
ARTIFACT_ROOT = ROOT / "data" / "artifacts" / "strategy_research_2026_ytd"
CACHE_ROOT = ROOT / "data" / "cache" / "strategy_research_2026_ytd"
EVALUATION_START = "2026-01-02"
EVALUATION_END = "2026-08-04"


def load_frozen_candidates(
    registry: ResearchRegistry,
) -> tuple[list[tuple[StrategySpec, StrategyCandidate]], dict[str, Any]]:
    freeze = json.loads(FREEZE_REPORT.read_text(encoding="utf-8"))["candidate_identity_freeze"]
    expected_ids = tuple(str(value) for value in freeze["candidate_ids"])
    with registry.connect() as db:
        rows = db.execute(
            "select strategy_id,payload_json from strategies where name like 'autonomous_price_only_%' order by strategy_id"
        ).fetchall()
    candidates: dict[str, tuple[StrategySpec, StrategyCandidate, dict[str, Any]]] = {}
    definitions: dict[str, dict[str, Any]] = {}
    for _strategy_id, payload_json in rows:
        payload = json.loads(payload_json)
        signal = payload.get("signal_rule") or {}
        candidate_id = str(signal.get("candidate_id") or "")
        if not candidate_id:
            continue
        definition = {
            "candidate_id": candidate_id,
            "family": signal.get("family"),
            "parameters": signal.get("parameters"),
            "parent_candidate_id": signal.get("parent_candidate_id"),
        }
        previous = definitions.get(candidate_id)
        if previous is not None and previous != definition:
            raise RuntimeError(f"candidate identity collision for {candidate_id}")
        definitions[candidate_id] = definition
        if candidate_id in candidates:
            continue
        spec = StrategySpec(**payload)
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
            hypothesis=str(payload.get("description") or "Frozen 2026 YTD candidate validation."),
            candidate_id=candidate_id,
        )
        candidates[candidate_id] = (spec, candidate, definition)

    observed_ids = tuple(sorted(definitions))
    observed_definitions = [definitions[key] for key in observed_ids]
    observed_hash = stable_id("candidate_id_set_pre_2026_ytd_download", observed_definitions, length=64)
    if observed_ids != tuple(sorted(expected_ids)):
        missing = sorted(set(expected_ids) - set(observed_ids))
        added = sorted(set(observed_ids) - set(expected_ids))
        raise RuntimeError(f"candidate identity set changed after freeze; missing={missing[:5]} added={added[:5]}")
    if observed_hash != str(freeze["hash"]):
        raise RuntimeError("candidate identity hash no longer matches the pre-download freeze")
    output = [(candidates[key][0], candidates[key][1]) for key in observed_ids]
    return output, {
        "candidate_count": len(observed_ids),
        "strategy_spec_count_at_freeze": int(freeze["strategy_spec_count"]),
        "hash": observed_hash,
        "candidate_ids": list(observed_ids),
    }


def parent_experiment_id(registry: ResearchRegistry, strategy_id: str) -> str | None:
    with registry.connect() as db:
        row = db.execute(
            """
            select experiment_id from experiments
            where strategy_id=? and status='COMPLETED' and dataset_id<>?
            order by completed_at desc limit 1
            """,
            (strategy_id, DATASET_ID),
        ).fetchone()
    return str(row[0]) if row else None


def main() -> int:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    registry = ResearchRegistry(REGISTRY_PATH)
    frozen, freeze = load_frozen_candidates(registry)
    loaded = load_canonical_daily_panel(REGISTRY_PATH, DATASET_ID, cache_root=CACHE_ROOT)
    panel = CanonicalPanel(
        dataset_id=loaded.dataset_id,
        dataset_name=loaded.dataset_name,
        frame=loaded.frame,
        raw_object_count=loaded.raw_object_count,
        source_paths=loaded.source_paths,
        lineage_hash=loaded.lineage_hash,
        research_role="independent_short_temporal_validation_fixed_pre_download_candidate_family",
        evaluation_start=EVALUATION_START,
        evaluation_end=EVALUATION_END,
    )
    policy = StrategyResearchPolicy(
        batch_size=len(frozen),
        maximum_total_candidates=len(frozen),
        maximum_generation=0,
        maximum_adaptive_children_per_cycle=0,
        slippage_bps_per_side=10.0,
        minimum_sessions=100,
        minimum_trades=10,
        minimum_profit_factor=1.10,
        maximum_drawdown_pct=25.0,
        maximum_holm_adjusted_p_value=0.10,
        walk_forward_folds=3,
        random_seed=314159,
    )

    evaluated: list[tuple[StrategySpec, Any]] = []
    errors: list[dict[str, Any]] = []
    for spec, candidate in frozen:
        try:
            evaluated.append((spec, run_candidate_backtest(panel, candidate, policy)))
        except Exception as exc:
            errors.append(
                {
                    "candidate": candidate.to_dict(),
                    "strategy_id": spec.strategy_id,
                    "verdict": "ERROR",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )

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
                "family_size": len(freeze["candidate_ids"]),
                "candidate_family_frozen_before_data_download": True,
                "short_holdout_family_wide_correction": True,
            },
            quality_checks={
                **dict(item.output.quality_checks),
                "independent_temporal_validation": True,
                "short_temporal_holdout": True,
                "candidate_family_frozen_before_data_download": True,
                "adaptive_feedback_allowed": False,
                "final_holdout_claim": False,
                "automatic_promotion": False,
            },
            assumptions={
                **dict(item.output.assumptions),
                "warmup_window": "2024-01-02 through 2025-12-31",
                "evaluation_window": f"{EVALUATION_START} through {EVALUATION_END}",
                "candidate_family_freeze_hash": freeze["hash"],
                "candidate_family_size": freeze["candidate_count"],
                "minimum_trades_for_short_holdout": policy.minimum_trades,
                "adaptive_feedback_allowed": False,
            },
            notes=tuple(item.output.notes)
            + (
                "The 2024-2025 bars are indicator warmup only and are excluded from positions, metrics, folds, and statistical tests.",
                "This is a short 2026 YTD temporal holdout, not sufficient by itself for final validation.",
                "No result from this holdout may generate adaptive children or authorize promotion.",
            ),
        )
        manifest = ExperimentManifest(
            strategy_id=spec.strategy_id,
            dataset_id=DATASET_ID,
            feature_set_id="broad_daily_price_only_2026_ytd_locked_holdout_v1",
            parameter_set={
                "candidate": item.candidate.to_dict(),
                "candidate_identity_freeze": freeze,
                "evaluation_start": EVALUATION_START,
                "evaluation_end": EVALUATION_END,
                "adaptive_feedback_allowed": False,
            },
            engine=EngineKind.CIPHER_FAST,
            code_hash=code_hash,
            runtime_environment_id=runtime_id,
            random_seed=policy.random_seed,
            started_at=now,
            preregistered=True,
            hypothesis=f"2026 YTD locked validation: {item.candidate.hypothesis}",
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
                "benchmark_metrics": dict(adjusted_output.benchmark_metrics),
                "statistical_tests": dict(result.statistical_tests),
                "quality_checks": dict(result.quality_checks),
                "fold_returns_pct": list(item.fold_returns_pct),
                "composite_score": item.composite_score,
            }
        )

    records.extend(errors)
    ranking = sorted(
        records,
        key=lambda row: (
            row.get("verdict") in {"PASS", "CONDITIONAL_PASS"},
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
            "warmup_sessions": int(panel.frame.loc[panel.frame["date"] < EVALUATION_START, "date"].nunique()),
            "scoreable_sessions": int(panel.frame.loc[(panel.frame["date"] >= EVALUATION_START) & (panel.frame["date"] <= EVALUATION_END), "date"].nunique()),
            "evaluation_start": EVALUATION_START,
            "evaluation_end": EVALUATION_END,
            "symbols": sorted(panel.frame["ticker"].unique().tolist()),
            "lineage_hash": panel.lineage_hash,
        },
        "candidate_identity_freeze": freeze,
        "multiple_testing": f"Holm across all {freeze['candidate_count']} pre-download candidate identities",
        "results": records,
        "ranking": ranking,
        "summary": {
            "candidates": len(records),
            "passes": sum(row.get("verdict") == "PASS" for row in records),
            "conditional_passes": sum(row.get("verdict") == "CONDITIONAL_PASS" for row in records),
            "failures": sum(row.get("verdict") == "FAIL" for row in records),
            "errors": sum(row.get("verdict") == "ERROR" for row in records),
        },
        "adaptive_feedback_allowed": False,
        "automatic_promotion": False,
        "paper_or_live_execution": False,
        "execution_authority": False,
    }
    latest = OUTPUT_ROOT / "latest_2026_ytd_locked_validation.json"
    timestamped = OUTPUT_ROOT / f"locked_2026_ytd_validation_{now.strftime('%Y%m%dT%H%M%SZ')}.json"
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    latest.write_text(encoded, encoding="utf-8")
    timestamped.write_text(encoded, encoding="utf-8")
    print(
        json.dumps(
            {
                "status": payload["status"],
                "dataset_id": panel.dataset_id,
                **payload["summary"],
                "leader_candidate_id": ranking[0].get("candidate", {}).get("candidate_id") if ranking else None,
                "leader_verdict": ranking[0].get("verdict") if ranking else None,
                "leader_score": ranking[0].get("composite_score") if ranking else None,
                "report": str(latest),
                "automatic_promotion": False,
                "execution_authority": False,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
