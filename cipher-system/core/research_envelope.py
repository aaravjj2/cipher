"""One result shape for every research engine, and the rules that keep it honest.

Cipher has 25 research engines and 11 of the 12 strategy labs emit results in their own
private shape. Nothing can rank across them, so nothing can decide what to run next: the
missing contract is what blocks the research agent and the autopilot, not the agent's own
difficulty.

The engines are genuinely incommensurable and this module does not pretend otherwise. The
wheel parameter lab's unit of result is a parameter *variant* scored on
`total_return_pct`; the EOD pattern lab's is a *pattern* scored on `profit_factor` beside an
FDR q-value. There is no arithmetic that makes 1.4 profit factor comparable to +12% return,
and inventing one would be exactly the fabrication this system exists to avoid.

So the envelope carries a *declared* primary metric -- name, unit, and direction -- rather
than a fixed field. That is enough to rank within a metric, and `rank()` refuses to rank
across metrics, reporting the groups separately instead. What it ranks *first* is evidence
quality, which is comparable across every engine: a measured-cost selectable result
outranks an assumed-cost one, and anything carrying a blocker outranks nothing at all.

Three invariants are enforced in `__post_init__` rather than left to callers, because each
one has already been violated in a stored artifact:

  * A result with blockers cannot be SELECTABLE. `research_grade: False` sat beside
    `beat_control_pct: 100.0` in the wheel study for a day.
  * A result with zero observations cannot be SELECTABLE. The same artifact claimed a clean
    sweep of 40 replicates from an arm that took no position.
  * Cost basis is required and may be "unknown", but never absent. An assumed cost that
    reads like a measured one is how a costed verdict becomes a guess.
"""
from __future__ import annotations

import json
import subprocess
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

UNKNOWN = "unknown"

#: Cost-basis strings that mean "this number was measured". Anything else is an assumption,
#: including `unknown`. Kept as a prefix check so `measured:median` and any future
#: `measured:*` variant from core.execution_cost are covered without edits here.
MEASURED_PREFIX = "measured:"


class Verdict(str, Enum):
    """What the study concluded.

    INCONCLUSIVE exists as a first-class state, and is the honest majority. Collapsing it
    into REJECTED loses the distinction between "we looked and it does not work" and "we
    could not tell" -- and the second is the one that tells an agent a further experiment
    would pay.
    """

    SELECTABLE = "selectable"
    REJECTED = "rejected"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True)
class Metric:
    """A number with enough context to be compared to another of the same name."""

    name: str
    value: float | None
    unit: str = "pct"
    higher_is_better: bool = True

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("metric name is required: an unnamed number cannot be ranked")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "unit": self.unit,
            "higher_is_better": self.higher_is_better,
        }


@dataclass(frozen=True)
class Sample:
    """What the result was computed over.

    `observations` is the count of the thing the engine actually measured -- closed events
    for the wheel, pattern occurrences for EOD. A verdict without it is unrankable, and a
    zero here blocks SELECTABLE.
    """

    observations: int
    symbols: tuple[str, ...] = ()
    start: str | None = None
    end: str | None = None

    def __post_init__(self) -> None:
        if self.observations < 0:
            raise ValueError("observations cannot be negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "observations": self.observations,
            "symbols": list(self.symbols),
            "start": self.start,
            "end": self.end,
        }


@dataclass(frozen=True)
class Provenance:
    """Where the numbers came from.

    `cost_basis` carries `core.execution_cost.equity_half_spread_bps`'s second return value
    verbatim -- `measured:median`, `assumed:no-profile`, `assumed:symbol-not-captured`,
    `assumed:insufficient-samples(N)`. Verbatim, not re-encoded: the moment this module
    starts translating those strings it becomes a second place where cost provenance can be
    wrong.
    """

    cost_basis: str
    commit: str = UNKNOWN
    generated_at: str = UNKNOWN

    def __post_init__(self) -> None:
        if not self.cost_basis:
            raise ValueError(
                "cost_basis is required; pass 'unknown' explicitly rather than leaving it "
                "empty, so an uncosted result cannot be mistaken for a costed one"
            )

    @property
    def cost_is_measured(self) -> bool:
        return self.cost_basis.startswith(MEASURED_PREFIX)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cost_basis": self.cost_basis,
            "cost_is_measured": self.cost_is_measured,
            "commit": self.commit,
            "generated_at": self.generated_at,
        }


@dataclass(frozen=True)
class Candidate:
    """One rankable thing the engine produced: a variant, a pattern, a configuration."""

    candidate_id: str
    primary: Metric
    metrics: Mapping[str, float | None] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.candidate_id:
            raise ValueError("candidate_id is required")

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "primary": self.primary.to_dict(),
            "metrics": dict(self.metrics),
        }


@dataclass(frozen=True)
class ResearchResult:
    """The envelope. Every engine returns this, whatever it computes internally."""

    study_id: str
    engine: str
    verdict: Verdict
    sample: Sample
    provenance: Provenance
    blockers: tuple[str, ...] = ()
    candidates: tuple[Candidate, ...] = ()
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.study_id:
            raise ValueError("study_id is required")
        if not self.engine:
            raise ValueError("engine is required")
        if self.verdict is Verdict.SELECTABLE:
            if self.blockers:
                raise ValueError(
                    "a result carrying blockers cannot be SELECTABLE: "
                    f"{list(self.blockers)}. Report INCONCLUSIVE and name what is missing."
                )
            if self.sample.observations == 0:
                raise ValueError(
                    "a result with zero observations cannot be SELECTABLE: nothing was "
                    "measured, so there is no evidence to select on"
                )

    @property
    def best(self) -> Candidate | None:
        """The strongest candidate by its own primary metric, or None.

        Candidates with a null primary value are skipped rather than sorted as zero -- an
        unavailable metric is not a bad one, and the exposure-cell lesson applies here too.
        """
        scored = [c for c in self.candidates if c.primary.value is not None]
        if not scored:
            return None
        direction = scored[0].primary.higher_is_better
        return max(scored, key=lambda c: c.primary.value if direction else -c.primary.value)  # type: ignore[operator, return-value]

    @property
    def evidence_tier(self) -> int:
        """Lower is stronger. Comparable across every engine, unlike the metrics.

        1  selectable, cost measured      -- believe this
        2  selectable, cost assumed       -- believe it as far as the assumption holds
        3  inconclusive                   -- a further experiment would pay
        4  rejected                       -- looked, does not work
        5  blocked                        -- cannot be evaluated at all yet
        """
        if self.blockers and self.verdict is not Verdict.REJECTED:
            return 5
        if self.verdict is Verdict.SELECTABLE:
            return 1 if self.provenance.cost_is_measured else 2
        if self.verdict is Verdict.INCONCLUSIVE:
            return 3
        return 4

    def to_dict(self) -> dict[str, Any]:
        best = self.best
        return {
            "schema_version": SCHEMA_VERSION,
            "study_id": self.study_id,
            "engine": self.engine,
            "verdict": self.verdict.value,
            "evidence_tier": self.evidence_tier,
            "sample": self.sample.to_dict(),
            "provenance": self.provenance.to_dict(),
            "blockers": list(self.blockers),
            "notes": list(self.notes),
            "candidate_count": len(self.candidates),
            "best": best.to_dict() if best else None,
            "candidates": [c.to_dict() for c in self.candidates],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)


@dataclass(frozen=True)
class RankedGroup:
    """Results sharing a primary metric, so ranking within them is arithmetic, not opinion."""

    metric: str
    unit: str
    results: tuple[ResearchResult, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "unit": self.unit,
            "results": [
                {
                    "study_id": r.study_id,
                    "engine": r.engine,
                    "evidence_tier": r.evidence_tier,
                    "verdict": r.verdict.value,
                    "cost_basis": r.provenance.cost_basis,
                    "observations": r.sample.observations,
                    "best_value": r.best.primary.value if r.best else None,
                    "blockers": list(r.blockers),
                }
                for r in self.results
            ],
        }


def _primary_metric_of(result: ResearchResult) -> tuple[str, str]:
    best = result.best
    if best is not None:
        return best.primary.name, best.primary.unit
    if result.candidates:
        return result.candidates[0].primary.name, result.candidates[0].primary.unit
    return UNKNOWN, UNKNOWN


def rank(results: Iterable[ResearchResult]) -> dict[str, Any]:
    """Order results by evidence quality, and group them by primary metric.

    Two deliberate refusals:

    * No single leaderboard. Results are grouped by primary metric name, because a profit
      factor and a percent return do not share a scale and stacking them in one column would
      manufacture a comparison nobody can defend.
    * No metric-first ordering. Evidence tier leads, so an assumed-cost result cannot
      outrank a measured one by posting a bigger number -- which is the failure mode a cost
      profile exists to prevent.
    """
    materialised = list(results)
    groups: dict[tuple[str, str], list[ResearchResult]] = {}
    for result in materialised:
        groups.setdefault(_primary_metric_of(result), []).append(result)

    ranked_groups = []
    for (metric, unit), members in sorted(groups.items()):
        direction = True
        for member in members:
            if member.best is not None:
                direction = member.best.primary.higher_is_better
                break

        def sort_key(r: ResearchResult) -> tuple[int, float, int]:
            best = r.best
            value = best.primary.value if best and best.primary.value is not None else None
            # Null metrics sort last within their tier rather than as zero.
            ordered = -float("inf") if value is None else (value if direction else -value)
            return (r.evidence_tier, -ordered, -r.sample.observations)

        ranked_groups.append(
            RankedGroup(metric=metric, unit=unit, results=tuple(sorted(members, key=sort_key)))
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "result_count": len(materialised),
        "comparable_across_groups": False,
        "why": (
            "Groups use different primary metrics and share no scale. Compare within a "
            "group; across groups compare evidence_tier only."
        ),
        "tier_counts": {
            str(tier): sum(1 for r in materialised if r.evidence_tier == tier)
            for tier in range(1, 6)
        },
        "groups": [g.to_dict() for g in ranked_groups],
    }


def current_commit(repo: Path | None = None) -> str:
    """The commit a run should be bound to, or `unknown`.

    Returns `unknown` rather than raising or inventing a value when git is unavailable or
    the tree is not a repository, because a fabricated commit is worse than an absent one.
    A dirty worktree is reported as such so a result cannot claim to be bound to a commit
    that does not contain the code that produced it.
    """
    root = repo or Path(__file__).resolve().parent.parent
    try:
        head = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10, check=False,
        )
        if head.returncode != 0:
            return UNKNOWN
        commit = head.stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"],
            capture_output=True, text=True, timeout=15, check=False,
        )
        if dirty.returncode == 0 and dirty.stdout.strip():
            return f"{commit}-dirty"
        return commit
    except (OSError, subprocess.SubprocessError):
        return UNKNOWN


def verdict_from_blockers(
    blockers: Sequence[str], observations: int, *, passes: bool
) -> Verdict:
    """The verdict rule an adapter should use, in one place.

    Adapters keep re-deriving this and erring in the same direction -- towards SELECTABLE --
    so it lives here. The rule is deliberately asymmetric:

      * Zero observations is INCONCLUSIVE whatever else is true. Nothing was measured, so
        the study cannot support selection *or* rejection.
      * Blockers plus a passing result is INCONCLUSIVE. Flawed evidence cannot select.
      * Blockers plus a failing result is still REJECTED. A study that lost and also carries
        caveats has not been rescued by the caveats, and forcing it to INCONCLUSIVE would
        keep dead strategies alive in the queue forever.

    The asymmetry is the point: a false SELECTABLE costs real money, a false REJECTED costs
    an idea. They are not the same error and the gate should not treat them as one.
    """
    if observations == 0:
        return Verdict.INCONCLUSIVE
    if not passes:
        return Verdict.REJECTED
    return Verdict.INCONCLUSIVE if blockers else Verdict.SELECTABLE
