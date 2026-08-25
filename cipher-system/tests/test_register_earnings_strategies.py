"""Tests for the earnings family's governance registration.

The registration must ride the platform's own state machine — StrategySpec in,
PromotionService rejection out — and land both earnings rows in REJECTED with
honest blockers, so the promotion ladder can never read them as trade-eligible.
Runs entirely against a temp registry; the live governance database is never
touched from tests.
"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(REPO_ROOT), str(REPO_ROOT / "core"), str(REPO_ROOT / "scripts")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import register_earnings_strategies as reg  # noqa: E402
from research_platform.models import PromotionState  # noqa: E402
from research_platform.promotion import PromotionBlockedError, PromotionService  # noqa: E402
from research_platform.registry import ResearchRegistry  # noqa: E402


@pytest.fixture()
def tmp_registry(tmp_path):
    return str(tmp_path / "governance" / "research_registry.sqlite")


def _run(tmp_registry, capsys, dry_run=False):
    argv = ["register_earnings_strategies.py", "--registry-path", tmp_registry]
    if dry_run:
        argv.append("--dry-run")
    original = sys.argv
    sys.argv = argv
    try:
        rc = reg.main()
    finally:
        sys.argv = original
    return rc, capsys.readouterr().out


def test_both_earnings_rows_land_rejected_with_blockers(tmp_registry, capsys):
    rc, out = _run(tmp_registry, capsys)
    assert rc == 0
    registry = ResearchRegistry(tmp_registry)
    for strategy_id in ("earnings_direction_v1", "earnings_gap_magnitude_v1"):
        assert registry.current_state(strategy_id) is PromotionState.REJECTED
        spec = registry.get_payload("strategies", "strategy_id", strategy_id)
        assert "BLOCKED:" in spec["description"]
    assert "NOT eligible for trading" in out


def test_rerun_is_idempotent_and_keeps_the_original_rejection(tmp_registry, capsys):
    assert _run(tmp_registry, capsys)[0] == 0
    events_before = _promotion_events(tmp_registry)
    rc, out = _run(tmp_registry, capsys)
    assert rc == 0
    assert "previously rejected 2" in out
    assert _promotion_events(tmp_registry) == events_before
    registry = ResearchRegistry(tmp_registry)
    assert registry.current_state("earnings_direction_v1") is PromotionState.REJECTED


def _promotion_events(registry_path):
    import sqlite3

    conn = sqlite3.connect(registry_path)
    rows = conn.execute(
        "select strategy_id, from_state, to_state from promotion_events order by strategy_id"
    ).fetchall()
    conn.close()
    return rows


def test_ladder_refuses_any_pre_trade_promotion_for_the_family(tmp_registry):
    # Register + reject via the script path, then attempt to climb past
    # REJECTED through the platform itself: only SPECIFIED/RETIRED are legal
    # exits, and every pre-trade state must be refused.
    registry = ResearchRegistry(tmp_registry)
    service = PromotionService(registry)
    original = sys.argv
    sys.argv = ["x", "--registry-path", tmp_registry]
    try:
        reg.main()
    finally:
        sys.argv = original

    for strategy_id in ("earnings_direction_v1", "earnings_gap_magnitude_v1"):
        for target in (
            PromotionState.PAPER_ELIGIBLE,
            PromotionState.LIVE_REVIEW_REQUIRED,
            PromotionState.WALK_FORWARD_PASSED,
            PromotionState.FAST_BACKTESTED,
        ):
            with pytest.raises((PromotionBlockedError, Exception)):
                service.promote(
                    strategy_id,
                    target,
                    actor="test",
                    reason="must be impossible without new passing evidence",
                )
        assert registry.current_state(strategy_id) is PromotionState.REJECTED


def test_specs_carry_real_v2_holdout_numbers(capsys):
    assert reg.DIRECTION_REJECT_REASON.startswith(
        "v2 independent holdout rejects direction"
    )
    assert "52.03%" in reg.DIRECTION_REJECT_REASON
    assert "42.86%" in reg.DIRECTION_REJECT_REASON
    assert "12.19%" in reg.GAP_REJECT_REASON
