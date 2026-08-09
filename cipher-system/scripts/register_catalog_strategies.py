"""Register every catalogued strategy in the governance ladder.

`core/research_platform/promotion.py:111` already routes `FAST_BACKTESTED` and
`WALK_FORWARD_PASSED` to an engine it calls `cipher_fast`, and
`experiments.FastGateEvaluator` already refuses to advance a strategy when a
declared `required_quality_check` is missing from its backtest output. That slot
had no engine behind it, so the ladder was built and unclimbed: two strategies
registered, zero experiments, zero promotion events.

`core/backtest_engine.py` is that engine. Registering each catalogued strategy with

    required_quality_checks = ["beats_control_range", "control_matched"]

is what turns the standard from a field in a JSON report into a condition the
state machine enforces. Nothing new is invented here; an existing gate is given
something to gate.

Blocked strategies are registered too, with their reason in the spec and a
threshold set they cannot currently satisfy. Leaving them out would make the
registry disagree with the catalog about what exists, and "we cannot measure this
yet" is a fact worth recording rather than an absence.

Writes to data/governance/research_registry.sqlite. Idempotent: registration is
immutable-insert, so re-running reports the rows that already exist rather than
duplicating them.

Usage:
  python3 scripts/register_catalog_strategies.py --dry-run
  python3 scripts/register_catalog_strategies.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _path in (str(ROOT), str(ROOT / "core")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import strategy_catalog as sc  # noqa: E402
from research_platform.models import StrategySpec  # noqa: E402
from research_platform.registry import ResearchRegistry  # noqa: E402

REGISTRY_PATH = ROOT / "data" / "governance" / "research_registry.sqlite"
VERSION = "cipher-fast-v1"

# The sample below which a control comparison cannot separate a result from noise.
# Kept in step with strategy_evaluation.MIN_TRADES_FOR_VERDICT.
MINIMUM_TRADES = 30


def _spec_for(catalog_spec) -> StrategySpec:
    blocked = not catalog_spec.evaluable
    return StrategySpec(
        strategy_id=catalog_spec.strategy_id,
        name=catalog_spec.strategy_id,
        version=VERSION,
        description=(
            f"{catalog_spec.name} ({catalog_spec.family}), adapted from "
            f"{catalog_spec.source}. Entry logic only; fills, costs and the "
            f"matched random-entry control come from core/backtest_engine.py."
            + (f" BLOCKED: {catalog_spec.blocked_reason}" if blocked else "")
        ),
        signal_rule={
            "source": catalog_spec.source,
            "family": catalog_spec.family,
            "adapter": "core/strategy_catalog.py",
            "bar_timeframe": catalog_spec.bar_timeframe,
        },
        instrument_rule={"asset_class": "equity", "universe": "caller-supplied"},
        contract_selection_rule={"applies": False, "reason": "equity bars only"},
        entry_rule={
            "fill": "next bar open",
            "note": "the signal bar's close was not tradable when the signal formed",
        },
        exit_rule={
            "stop_atr": 1.0, "target_atr": 1.5, "max_hold_bars": 24,
            "intrabar": "stop assumed before target when a bar spans both",
            "note": ("one exit rule for every strategy: comparing strategies that "
                     "each brought their own would measure the exit rules as much "
                     "as the entries"),
        },
        sizing_rule={"quantity": 1, "note": "research only; no position sizing"},
        portfolio_constraints={"no_overlapping_positions_per_symbol": True},
        required_feature_ids=(),
        fill_model={
            "cost_bps_per_side": "measured per symbol where captured, else 2.0",
            "source": "core/execution_cost.py",
            "charged": "both sides",
        },
        benchmark="matched random-entry control",
        statistical_plan={
            "control": ("random entries matched trade-for-trade by symbol and "
                        "direction, under an identical exit rule"),
            "criterion": ("must clear the BEST of N random draws, not their mean, "
                          "so a lucky draw cannot be mistaken for an edge"),
            "repeats": 20,
        },
        promotion_thresholds={
            "minimum_trades": MINIMUM_TRADES,
            "required_quality_checks": ["beats_control_range", "control_matched"],
            "require_walk_forward": True,
            "data_requirement": catalog_spec.data_requirement,
            **({"blocked_reason": catalog_spec.blocked_reason} if blocked else {}),
        },
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    registry = ResearchRegistry(REGISTRY_PATH)
    specs = [_spec_for(s) for s in sc.CATALOG.values()]

    print(f"catalogued strategies: {len(specs)}")
    print(f"  evaluable : {len(sc.evaluable())}")
    print(f"  blocked   : {len(sc.blocked())}")
    print(f"\nrequired quality checks: beats_control_range, control_matched")
    print(f"minimum trades         : {MINIMUM_TRADES}")

    if args.dry_run:
        print("\n--dry-run: nothing written")
        for spec in specs[:5]:
            print(f"  would register {spec.strategy_id} ({spec.version})")
        print(f"  … and {max(0, len(specs) - 5)} more")
        return 0

    inserted = existing = failed = 0
    for spec in specs:
        try:
            if registry.register_strategy(spec):
                inserted += 1
            else:
                existing += 1
        except Exception as exc:  # noqa: BLE001 - report and continue
            failed += 1
            print(f"  FAILED {spec.strategy_id}: {type(exc).__name__}: {exc}")

    print(f"\nregistered {inserted}, already present {existing}, failed {failed}")

    import sqlite3
    conn = sqlite3.connect(REGISTRY_PATH)
    total = conn.execute("select count(*) from strategies").fetchone()[0]
    experiments = conn.execute("select count(*) from experiments").fetchone()[0]
    promotions = conn.execute("select count(*) from promotion_events").fetchone()[0]
    conn.close()
    print(f"registry now: {total} strategies, {experiments} experiments, "
          f"{promotions} promotion events")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
