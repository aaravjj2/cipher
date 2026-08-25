"""Register the earnings strategy family in the governance ladder — as REJECTED.

The v2 holdout audit (docs/audits/simple_morning_brief_and_earnings_v2_2026-08-23.md)
is unambiguous: day-5 direction posted 52.03% against a 51.76% naive majority
baseline, the >=58% confidence gate went 42.86% (N=7) against a 57.14% baseline,
day-1 direction and reversal tied their baselines, and only absolute-gap
magnitude improved (1.9777% MAE vs 2.2523% naive, +12.19%) — a size estimate,
which by itself validates no directional option trade. The live engine already
enforces this as `NO_TRADE_DIRECTION_FAILED_HOLDOUT`; leaving the governance
registry silent about the family would let the ladder disagree with reality
about what exists and what it earned.

Mirrors scripts/register_catalog_strategies.py exactly: build a StrategySpec per
family, register it, then use the platform's own PromotionService to move
IDEA -> REJECTED so every step rides the same state machine other strategies
answer to. Blocked families are registered too — "we measured it and it failed"
is a fact worth recording, and an absence would read as "never evaluated".
Both entries carry honest blockers (no historical NBBO/options P&L with
underlying-proxy fills; single-source calendar until the 2026-08 cross-check;
legacy estimated-entry cohort) and promotion thresholds they do not currently
satisfy, so nothing about these rows can be mistaken for trade eligibility.
REJECTED transitions onward only to SPECIFIED or RETIRED; PAPER_ELIGIBLE and
LIVE_REVIEW_REQUIRED are unreachable without new passing evidence.

Writes to data/governance/research_registry.sqlite. Idempotent: registration is
immutable-insert and the rejection is recorded once, so re-running reports
existing rows rather than duplicating or conflicting.

Usage:
  python3 scripts/register_earnings_strategies.py --dry-run
  python3 scripts/register_earnings_strategies.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _path in (str(ROOT), str(ROOT / "core")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from research_platform.models import PromotionState, StrategySpec  # noqa: E402
from research_platform.promotion import PromotionService  # noqa: E402
from research_platform.registry import ResearchRegistry  # noqa: E402

REGISTRY_PATH = ROOT / "data" / "governance" / "research_registry.sqlite"
VERSION = "earnings-v2.0-2026-08-23"

#: The evidence these verdicts rest on. Evidence ids are opaque strings here;
#: the first names the audit of record, the second the walk-forward study that
#: must independently confirm any future reopening of the magnitude question.
EVIDENCE_IDS = (
    "docs/audits/simple_morning_brief_and_earnings_v2_2026-08-23.md",
    "earnings_gap_magnitude_walkforward",
)

ACTOR = "earnings-phase2-revalidation"

BLOCKERS = (
    "no historical NBBO, option quotes, or IV series exist locally, so fills are "
    "underlying-proxy approximations and the legacy paper cohort priced entries "
    "from estimates rather than captured quotes",
    "earnings dates were single-sourced from Yahoo until the 2026-08 Nasdaq "
    "cross-check existed",
)

DIRECTION_REJECT_REASON = (
    "v2 independent holdout rejects direction: day-5 accuracy 52.03% vs 51.76% "
    "naive majority baseline; confidence-gated subset 42.86% (N=7) vs 57.14% "
    "baseline; day-1 50.77% vs 50.88%; reversal 74.31% vs 74.31%. Engine status "
    "NO_TRADE_DIRECTION_FAILED_HOLDOUT."
)
GAP_REJECT_REASON = (
    "absolute-gap magnitude improved holdout MAE 12.19% (1.9777% vs 2.2523% "
    "naive), but a size estimate does not validate a directional option trade; "
    "recorded REJECTED for trading eligibility while the descriptive model "
    "stays available and the walk-forward study must confirm it out of sample."
)


def _direction_spec() -> StrategySpec:
    return StrategySpec(
        strategy_id="earnings_direction_v1",
        name="earnings_direction_v1",
        version=VERSION,
        description=(
            "Day-5/direction-gated earnings option trades (debit spreads) selected "
            "by the earnings_model engine. EVIDENCE TIER 4 (rejected): the "
            "direction model failed its untouched 20% chronological holdout "
            "(22,688-event dataset, N=4,538 holdout). BLOCKED: "
            + "; ".join(BLOCKERS)
        ),
        signal_rule={
            "source": "earnings_model.model.predict_for_symbol",
            "adapter": "cipher-github/earnings_model",
            "gate": "NO_TRADE_DIRECTION_FAILED_HOLDOUT",
            "trade_confidence": 0.58,
        },
        instrument_rule={"asset_class": "equity_options", "universe": "optionable_by_cap_tier"},
        contract_selection_rule={
            "structure": "defined-risk debit spreads only",
            "expiry": "nearest Friday on or after the report date",
        },
        entry_rule={
            "fill": "captured two-sided core quotes required (long ask - short bid)",
            "fallback": "refuse unless allow_estimated_debit=True (labeled ESTIMATED_DEBIT)",
        },
        exit_rule={"settlement": "underlying close on expiry; legs valued at intrinsic"},
        sizing_rule={"quantity": "target risk per trade, max one position per symbol/event"},
        portfolio_constraints={"no_overlapping_positions_per_symbol_event": True},
        required_feature_ids=(),
        fill_model={
            "cost_bps_per_side": "unmeasured for historical events",
            "source": "underlying-proxy; no historical NBBO captured",
        },
        benchmark="naive majority-class direction (51.76% day-5 holdout)",
        statistical_plan={
            "split": "chronological 60/20/20 train/selection/untouched-holdout",
            "criterion": (
                ">=100 gated holdout samples, >=55% gated accuracy, >=2pt lift over "
                "baseline, better Brier than naive"
            ),
            "result": "failed every criterion (gated N=7)",
        },
        promotion_thresholds={
            "minimum_gated_holdout_samples": 100,
            "minimum_gated_accuracy": 0.55,
            "minimum_lift_over_baseline": 0.02,
            "require_walk_forward": True,
            "data_requirement": "historical NBBO/options P&L plus dual-source calendar",
            "blocked_reasons": list(BLOCKERS),
        },
    )


def _gap_magnitude_spec() -> StrategySpec:
    return StrategySpec(
        strategy_id="earnings_gap_magnitude_v1",
        name="earnings_gap_magnitude_v1",
        version=VERSION,
        description=(
            "Absolute opening-gap magnitude estimate for earnings events (ridge / "
            "Huber gradient boosting). Descriptive only while direction gating is "
            "closed; a size estimate validates no directional trade. EVIDENCE "
            "TIER 4 pending the walk-forward study of record "
            "(earnings_gap_magnitude_walkforward). BLOCKED: "
            + "; ".join(BLOCKERS)
        ),
        signal_rule={
            "source": "earnings_model.model._fit_gap_regressor",
            "adapter": "cipher-github/earnings_model",
            "role": "descriptive-magnitude-only while NO_TRADE_DIRECTION_FAILED_HOLDOUT",
        },
        instrument_rule={"asset_class": "none", "note": "size estimate; no instrument selected"},
        contract_selection_rule={"applies": False, "reason": "magnitude estimate only"},
        entry_rule={"applies": False, "reason": "no entry may be derived from a size estimate alone"},
        exit_rule={"applies": False},
        sizing_rule={"applies": False},
        portfolio_constraints={},
        required_feature_ids=(),
        fill_model={"cost_bps_per_side": "not-applicable: no positions taken"},
        benchmark="training-window median |gap| (holdout MAE 2.2523%)",
        statistical_plan={
            "split": "chronological 60/20/20 train/selection/untouched-holdout",
            "criterion": "MAE improvement >= 5% over strongest naive baseline (model.MIN_GAP_MAE_IMPROVEMENT)",
            "result": "single-split improvement 12.19%; walk-forward confirmation outstanding",
        },
        promotion_thresholds={
            "minimum_mae_improvement_vs_strongest_baseline": 0.05,
            "require_walk_forward": True,
            "require_direction_gate_reopen": True,
            "data_requirement": "historical implied-move proxy (option IV), never captured before 2026",
            "blocked_reasons": list(BLOCKERS),
        },
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--registry-path", type=str, default=str(REGISTRY_PATH),
        help="Override the registry location (tests use a temp file)",
    )
    args = ap.parse_args()

    registry = ResearchRegistry(args.registry_path)
    service = PromotionService(registry)
    specs = [_direction_spec(), _gap_magnitude_spec()]
    reasons = {
        "earnings_direction_v1": DIRECTION_REJECT_REASON,
        "earnings_gap_magnitude_v1": GAP_REJECT_REASON,
    }

    print(f"earnings family strategies: {len(specs)}")
    print(f"model version: {VERSION}")
    if args.dry_run:
        print("\n--dry-run: nothing written")
        for spec in specs:
            print(f"  would register {spec.strategy_id} then promote {PromotionState.IDEA.value} -> {PromotionState.REJECTED.value}")
        return 0

    registered = existing = rejected = already_rejected = failed = 0
    for spec in specs:
        try:
            if registry.register_strategy(spec):
                registered += 1
            else:
                existing += 1
            if registry.current_state(spec.strategy_id) is PromotionState.REJECTED:
                # A previous run's immutable rejection stands; nothing duplicates.
                already_rejected += 1
                continue
            service.reject(
                spec.strategy_id,
                actor=ACTOR,
                reason=reasons[spec.strategy_id],
                evidence_ids=EVIDENCE_IDS,
            )
            rejected += 1
        except Exception as exc:  # noqa: BLE001 - report and continue
            failed += 1
            print(f"  FAILED {spec.strategy_id}: {type(exc).__name__}: {exc}")

    print(f"\nregistered {registered}, already present {existing}")
    print(f"rejected {rejected}, previously rejected {already_rejected}, failed {failed}")

    not_eligible = True
    for spec in specs:
        state = registry.current_state(spec.strategy_id).value
        eligible_states = {
            PromotionState.PAPER_ELIGIBLE.value,
            PromotionState.LIVE_REVIEW_REQUIRED.value,
            PromotionState.PROSPECTIVE_SHADOW.value,
            PromotionState.WALK_FORWARD_PASSED.value,
            PromotionState.LEAN_REPLICATED.value,
            PromotionState.FAST_BACKTESTED.value,
        }
        print(f"  ladder: {spec.strategy_id} = {state}")
        if state in eligible_states:
            not_eligible = False
    print(
        "\npromotion ladder check: earnings family is NOT eligible for trading"
        if not_eligible else
        "\nUNEXPECTED: an earnings family row reached a pre-trade state — investigate"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
