import pytest

from core.research_platform.forecast_ranking import (
    cohort_ticker_sets,
    deterministic_random_scores,
    equal_weight_rank_ensemble,
    future_return,
    kendall,
    pairwise_accuracy,
    spearman,
    strict_independence_subset,
    top_minus_bottom,
)


def test_returns_and_rank_metrics_have_expected_direction():
    assert future_return(100, 110) == pytest.approx(0.1)
    assert spearman([1, 2, 3], [10, 20, 30]) == 1.0
    assert kendall([1, 2, 3], [30, 20, 10]) == -1.0
    assert pairwise_accuracy([1, 2, 3], [10, 20, 30]) == 1.0


def test_quartile_spread_and_deterministic_random_baseline():
    assert top_minus_bottom([1, 2, 3, 4], [-.1, 0, .1, .2]) == pytest.approx(.3)
    assert deterministic_random_scores(["a", "b", "c"]) == deterministic_random_scores(["a", "b", "c"])


def test_ties_and_equal_weight_rank_ensemble_are_deterministic():
    assert equal_weight_rank_ensemble([1, 1, 3], [3, 2, 1]) == pytest.approx([2.25, 1.75, 2.0])


def test_cohort_ticker_sets_preserve_same_membership():
    rows = [
        {"ticker": "A", "origin": "2020-01-01", "horizon_sessions": 5},
        {"ticker": "B", "origin": "2020-01-01", "horizon_sessions": 5},
        {"ticker": "A", "origin": "2020-01-01", "horizon_sessions": 20},
    ]
    assert cohort_ticker_sets(rows) == {("2020-01-01", 5): frozenset({"A", "B"}), ("2020-01-01", 20): frozenset({"A"})}


def test_strict_subset_excludes_overlapping_windows_for_one_ticker():
    cases = [
        {"ticker": "A", "context_start": "2020-01-01", "origin": "2020-02-01", "outcome_end": "2020-02-20", "horizon_sessions": 20},
        {"ticker": "A", "context_start": "2020-02-10", "origin": "2020-03-01", "outcome_end": "2020-03-20", "horizon_sessions": 20},
        {"ticker": "B", "context_start": "2020-02-10", "origin": "2020-03-01", "outcome_end": "2020-03-20", "horizon_sessions": 20},
    ]
    assert [row["ticker"] for row in strict_independence_subset(cases)] == ["A", "B"]
