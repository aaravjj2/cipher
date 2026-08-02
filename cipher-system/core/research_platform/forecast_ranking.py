"""Price-only cross-sectional forecast ranking diagnostics.

All functions operate on already frozen forecast records.  They deliberately
exclude volume and emit research metrics only; no ranking output is a signal,
position size, or execution instruction.
"""
from __future__ import annotations

import math
import random
from collections import defaultdict
from statistics import mean
from typing import Iterable, Mapping, Sequence


def future_return(origin_price: float, realized_price: float) -> float:
    if origin_price <= 0:
        raise ValueError("origin_price must be positive")
    return (realized_price / origin_price) - 1.0


def ordinal_ranks(values: Sequence[float]) -> list[float]:
    """Average tied ranks, ascending, with no dependency on scipy."""
    indexed = sorted(enumerate(values), key=lambda item: item[1])
    out = [0.0] * len(values)
    index = 0
    while index < len(indexed):
        end = index + 1
        while end < len(indexed) and indexed[end][1] == indexed[index][1]:
            end += 1
        rank = (index + 1 + end) / 2.0
        for original, _ in indexed[index:end]:
            out[original] = rank
        index = end
    return out


def pearson(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    left_mean, right_mean = mean(left), mean(right)
    numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right))
    left_var = sum((a - left_mean) ** 2 for a in left)
    right_var = sum((b - right_mean) ** 2 for b in right)
    if left_var <= 0 or right_var <= 0:
        return None
    return numerator / math.sqrt(left_var * right_var)


def spearman(left: Sequence[float], right: Sequence[float]) -> float | None:
    return pearson(ordinal_ranks(left), ordinal_ranks(right))


def kendall(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    concordant = discordant = 0
    for i in range(len(left)):
        for j in range(i + 1, len(left)):
            sign = (left[i] - left[j]) * (right[i] - right[j])
            if sign > 0:
                concordant += 1
            elif sign < 0:
                discordant += 1
    total = concordant + discordant
    return None if total == 0 else (concordant - discordant) / total


def pairwise_accuracy(scores: Sequence[float], returns: Sequence[float]) -> float | None:
    return (kendall(scores, returns) + 1.0) / 2.0 if kendall(scores, returns) is not None else None


def top_minus_bottom(scores: Sequence[float], returns: Sequence[float], *, buckets: int = 4) -> float | None:
    if len(scores) != len(returns) or len(scores) < buckets:
        return None
    ordered = sorted(zip(scores, returns), key=lambda item: item[0])
    size = max(1, len(ordered) // buckets)
    return mean(value for _, value in ordered[-size:]) - mean(value for _, value in ordered[:size])


def deterministic_random_scores(case_ids: Sequence[str], *, seed: int = 42) -> list[float]:
    rng = random.Random(seed)
    values = list(case_ids)
    rng.shuffle(values)
    rank = {case_id: index for index, case_id in enumerate(values)}
    return [float(rank[case_id]) for case_id in case_ids]


def equal_weight_rank_ensemble(*score_sets: Sequence[float]) -> list[float]:
    """Average within-cohort ordinal ranks, preserving each model's direction."""
    if not score_sets or any(len(scores) != len(score_sets[0]) for scores in score_sets):
        raise ValueError("at least one equally sized score set is required")
    ranked = [ordinal_ranks(scores) for scores in score_sets]
    return [mean(values) for values in zip(*ranked)]


def cohort_ticker_sets(rows: Iterable[Mapping[str, object]]) -> dict[tuple[str, int], frozenset[str]]:
    """Return exact ticker membership per same-origin/horizon cohort."""
    grouped: dict[tuple[str, int], set[str]] = defaultdict(set)
    for row in rows:
        grouped[(str(row["origin"]), int(row["horizon_sessions"]))].add(str(row["ticker"]))
    return {key: frozenset(value) for key, value in grouped.items()}


def overlap(left: Mapping[str, str], right: Mapping[str, str]) -> bool:
    """Whether inclusive context/outcome intervals overlap for one ticker."""
    if left["ticker"] != right["ticker"]:
        return False
    return not (left["outcome_end"] < right["context_start"] or right["outcome_end"] < left["context_start"])


def strict_independence_subset(cases: Iterable[Mapping[str, str]]) -> list[dict]:
    """Deterministically retain earliest non-overlapping case per ticker."""
    selected: list[dict] = []
    by_ticker: dict[str, list[dict]] = defaultdict(list)
    for case in sorted((dict(item) for item in cases), key=lambda row: (row["ticker"], row["origin"], row["horizon_sessions"])):
        if not any(overlap(case, previous) for previous in by_ticker[case["ticker"]]):
            by_ticker[case["ticker"]].append(case)
            selected.append(case)
    return selected
