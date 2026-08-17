"""Decide what to research next, from the evidence already gathered.

Phase 2 of the spine. This consumes the envelopes Phase 1 produces and answers one question:
given everything Cipher has already established, what is the next experiment worth running?

The mechanism is a blocker census rather than a scoring heuristic. Every envelope carries the
reasons it cannot be believed; counting which reasons recur, and how many studies each one
holds down, turns "what should I do next" into arithmetic over evidence Cipher already owns.
On the real corpus one blocker holds down 55 studies and can be cleared with a pipeline that
already exists, while four others hold down 54 each and cannot be cleared at all -- a
distinction no amount of parameter search would have surfaced.

The classification is the part that matters, because the obvious version of this tool is
useless. Four of the five most common blockers are not gaps:

    "risk-neutral POP is a model estimate, not an observed probability"

No action removes that. It is a property of the method, and an agent proposing to fix it
would generate work forever. So blockers are classified, and only ACTIONABLE ones become
proposals. ACQUIRABLE ones are reported with what acquiring them would take, so the decision
to spend is the owner's. UNCLASSIFIED exists so a blocker nobody anticipated shows up in the
output instead of being silently dropped -- the failure that would make this agent quietly
wrong rather than loudly incomplete.

The agenda is also allowed to be empty. An agent that always finds something to do will
manufacture underpowered studies indefinitely, and on a research system every one of those
looks like a finding.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

from core.research_envelope import ResearchResult, Verdict


class BlockerClass(str, Enum):
    ACTIONABLE = "actionable"
    ACQUIRABLE = "acquirable"
    INHERENT = "inherent"
    UNCLASSIFIED = "unclassified"


@dataclass(frozen=True)
class Rule:
    """How to recognise and treat one family of blocker.

    `match` is a lowercase substring rather than a regex: these strings are written by
    Cipher's own engines, so the coupling is intentional and a substring is easier to audit
    against the text that produced it.
    """

    match: str
    classification: BlockerClass
    action: str
    detail: str = ""
    latency: str = "unknown"
    limitation: str = ""


#: Order matters only where two rules could match one blocker; the first wins. Every entry
#: was written against a blocker string observed in the real corpus on 2026-08-12, not
#: invented in advance.
RULES: tuple[Rule, ...] = (
    Rule(
        match="execution cost is unmeasured for this universe",
        classification=BlockerClass.ACTIONABLE,
        action="Capture live option spreads for the wheel's symbols, then rebuild the cost profile",
        detail=(
            "Add NVDL, SOXL, TQQQ and TSLL to TRADIER_OPTION_UNDERLYINGS so the Tradier "
            "stream collects them, then re-run scripts/build_execution_cost_profile.py "
            "against tradier_stream.sqlite. The pipeline already exists and produced the "
            "23-symbol profile; these four were simply never in the capture list."
        ),
        latency="~12 trading days of capture before the cells report sufficient samples",
        limitation=(
            "This bounds whether the wheel's cost assumption is optimistic against currently "
            "observable spreads. It does NOT retroactively cost a 2024-2026 backtest -- the "
            "profile's own caveat is explicit that a multi-year study cannot be costed from a "
            "12-day capture window."
        ),
    ),
    Rule(
        match="no out-of-sample future period",
        classification=BlockerClass.ACTIONABLE,
        action="Hold out a future period and re-evaluate on it",
        detail=(
            "The walkforward engines already do this with holdout months; the pattern lab "
            "does not. Its verdict cannot improve without one."
        ),
        latency="as long as the holdout window itself",
    ),
    Rule(
        match="overlaps the six-month sample",
        classification=BlockerClass.ACTIONABLE,
        action="Wait for a genuinely independent window rather than a nested stability check",
        detail="A three-month window inside a six-month sample validates nothing about the six.",
        latency="until a non-overlapping window exists",
    ),
    # ── Structural Fib (NVDA/AAPL) ───────────────────────────────────────────────────
    Rule(
        match="carry no intrabar sequence",
        classification=BlockerClass.ACTIONABLE,
        action="Resolve the target-vs-stop race on 1-minute bars already on disk",
        detail=(
            "The study resamples 1-minute bars to 5-minute and then cannot order two touches "
            "inside one bar, so it scores every ambiguous bar as the stop. The 1-minute "
            "source is already local (obsidian_pine_ytd_2026/equity_bars.sqlite, 188 days for "
            "NVDA/AAPL/MU), so re-resolving at 1-minute resolution needs no new data -- only "
            "the race function pointed at the finer series. It narrows the conservative bias "
            "rather than removing it; 1-minute bars are ambiguous too, just less often."
        ),
        latency="a single re-run; no data acquisition",
        limitation=(
            "This can only move results in the strategy's favour, so it must not be used to "
            "revisit a refutation selectively. Re-run every leg or none."
        ),
    ),
    Rule(
        match="necessary but not sufficient for the trade to pay",
        classification=BlockerClass.ACQUIRABLE,
        action="Model option P&L for the legs whose window the chain capture covers",
        detail=(
            "The study measures the underlying price path, which is the right way to test a "
            "claim about price reaching a level, but the method trades 0DTE/1DTE options. "
            "Converting to option P&L needs a chain for each signal date. Local capture "
            "starts 2026-07-22 while the study runs from 2025-12-02, so most of the sample "
            "cannot be costed -- only the tail can, and that tail is small."
        ),
        latency="forward-accruing; the covered fraction grows one trading day at a time",
        limitation=(
            "Do not retrofit the recent capture onto the earlier window. The measured option "
            "half-spread for these names (NVDA 0DTE median 2.375% of premium, AAPL 2.875%) "
            "describes July-August 2026 and nothing before it."
        ),
    ),
    Rule(
        match="discretionary in the source method",
        classification=BlockerClass.INHERENT,
        action=None,
        detail=(
            "'Clean break' and 'strength' are judgement calls in the taught method, and the "
            "source says so outright -- \"if you wait for full closes, you're going to miss "
            "half of the move sometimes\". Any encoding picks one reading of a rule that was "
            "never fully specified, so no data closes this. The study states its encoding "
            "and reports a sensitivity instead, which is the honest available move."
        ),
        latency=None,
    ),
    Rule(
        match="compounding repeated re-entries",
        classification=BlockerClass.ACTIONABLE,
        action="Allow repeated intraday re-entries per setup and report both readings",
        detail=(
            "The study counts one signal per setup/leg/direction per session; the method "
            "re-enters as the anchor trails, which the source demonstrates repeatedly. "
            "Allowing re-entry raises n and tests whether later signals in a session are "
            "worse than the first -- a question the current count cannot ask."
        ),
        latency="a single re-run; no data acquisition",
        limitation=(
            "Re-entries within one session are not independent observations, so n rising is "
            "not the same as evidence strengthening; the confidence intervals would need a "
            "session-clustered treatment."
        ),
    ),
    # ── Wave Lock (QQQ) ──────────────────────────────────────────────────────────────
    Rule(
        match="no out-of-sample period",
        classification=BlockerClass.ACTIONABLE,
        action="Forward-test the selected exit policy rather than re-reading the sweep",
        detail=(
            "Seven exit policies were scored on the same 173 sessions that produced the "
            "original finding, so the best of them is whichever noise favoured. The "
            "pre-registration machinery already exists in core/structural_fib_forward.py and "
            "generalises: freeze one policy, record signals as they fire, score them later."
        ),
        latency="weeks; roughly 30 independent sessions before a rate means anything",
        limitation=(
            "Worth doing only if a candidate is worth forward-testing. On the measured data no "
            "policy clears |t| >= 2, so this would be spending weeks to confirm a zero unless "
            "something else changes first."
        ),
    ),
    Rule(
        match="finest series held",
        classification=BlockerClass.ACQUIRABLE,
        action="Obtain quote or tick data to order touches inside a one-minute bar",
        detail=(
            "Unlike the five-minute studies, this one cannot be improved by resampling: "
            "one-minute is already the finest series on disk. Ordering a bar that spans target "
            "and stop needs sub-minute data, which no local pipeline produces."
        ),
        latency="vendor-dependent; a purchase decision, not a compute job",
        limitation=(
            "It would only narrow a deliberately conservative bias. With no configuration "
            "significantly positive, resolving the ambiguity cannot change the verdict."
        ),
    ),
    Rule(
        match="clears |t| >= 2 on a session-clustered",
        classification=BlockerClass.INHERENT,
        action=None,
        detail=(
            "No further analysis of this dataset can establish a sign that is not in it. "
            "Twenty-seven configurations were measured -- seven exit policies plus a "
            "pre-registered grid of five stop widths against two targets -- and the largest "
            "|t| was 1.53, on the strategy's own original parameterisation. Continuing to "
            "search parameters here would be fitting the residual noise."
        ),
        latency=None,
    ),
    Rule(
        match="assume both halves fill at the stated level",
        classification=BlockerClass.INHERENT,
        action=None,
        detail=(
            "A scale-out needs a fill model to price properly, and the bias is known and "
            "small and in the strategy's favour. Since the policy is not significantly "
            "positive even with the flattering assumption, removing it cannot help."
        ),
        latency=None,
    ),
    Rule(
        match="nbbo",
        classification=BlockerClass.ACQUIRABLE,
        action="Obtain historical option NBBO",
        detail=(
            "No local pipeline produces this. Trade-bar proxies are standing in, which is "
            "why several engines cap themselves below research grade regardless of result."
        ),
        latency="vendor-dependent; a purchase decision, not a compute job",
        limitation="Cost and licensing are the owner's call; nothing here can proceed without it.",
    ),
    Rule(
        match="five-minute bars are not executable quotes",
        classification=BlockerClass.ACQUIRABLE,
        action="Obtain quote-level data for the studied window",
        detail="Same underlying gap as historical NBBO, stated per-engine.",
        latency="vendor-dependent",
    ),
    Rule(
        match="point-in-time fundamentals",
        classification=BlockerClass.ACQUIRABLE,
        action="Obtain point-in-time fundamentals to replace the curated whitelist",
        detail=(
            "The whitelist encodes what is known now, so any study selecting on it is "
            "look-ahead biased by construction."
        ),
        latency="vendor-dependent",
    ),
    Rule(
        match="historical borrow, taxes, dividends",
        classification=BlockerClass.ACQUIRABLE,
        action="Obtain borrow, dividend and corporate-action history",
        latency="vendor-dependent",
    ),
    Rule(
        match="model estimate, not an observed probability",
        classification=BlockerClass.INHERENT,
        action="",
        detail=(
            "Risk-neutral POP is what the model computes; there is no dataset that turns it "
            "into an observed frequency. It can be accepted and reported, or the study can "
            "stop depending on it, but it cannot be removed."
        ),
    ),
    Rule(
        match="many hypotheses are tested",
        classification=BlockerClass.INHERENT,
        action="",
        detail=(
            "Already handled the only way it can be: FDR correction and stability fields. "
            "Testing fewer hypotheses is a different study, not a fix to this one."
        ),
    ),
    Rule(
        match="not an options p/l study",
        classification=BlockerClass.INHERENT,
        action="",
        detail="A statement of the study's scope. Widening the scope means a new study.",
    ),
    Rule(
        match="execution remains a conservative",
        classification=BlockerClass.INHERENT,
        action="",
        detail=(
            "The engine is telling you it already stress-tested execution rather than "
            "assuming the best case. That is a strength being reported as a caveat."
        ),
    ),
)


def classify(blocker: str) -> Rule | None:
    lowered = blocker.lower()
    for rule in RULES:
        if rule.match in lowered:
            return rule
    return None


@dataclass(frozen=True)
class BlockerCensus:
    text: str
    classification: BlockerClass
    study_count: int
    studies: tuple[str, ...]
    best_tier_blocked: int
    rule: Rule | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "blocker": self.text,
            "classification": self.classification.value,
            "study_count": self.study_count,
            "best_tier_blocked": self.best_tier_blocked,
            "studies": list(self.studies[:8]),
            "studies_truncated": max(0, len(self.studies) - 8),
        }


@dataclass(frozen=True)
class Action:
    summary: str
    detail: str
    unblocks: int
    studies: tuple[str, ...]
    latency: str
    limitation: str
    blocker: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.summary,
            "detail": self.detail,
            "unblocks_studies": self.unblocks,
            "example_studies": list(self.studies[:5]),
            "latency": self.latency,
            "limitation": self.limitation,
            "clears_blocker": self.blocker,
        }


def census(results: Iterable[ResearchResult]) -> list[BlockerCensus]:
    """Every distinct blocker, how many studies it holds down, and whether it can be cleared."""
    seen: dict[str, list[ResearchResult]] = {}
    for result in results:
        for blocker in result.blockers:
            seen.setdefault(blocker, []).append(result)

    rows: list[BlockerCensus] = []
    for text, affected in seen.items():
        rule = classify(text)
        rows.append(
            BlockerCensus(
                text=text,
                classification=rule.classification if rule else BlockerClass.UNCLASSIFIED,
                study_count=len(affected),
                studies=tuple(r.study_id for r in affected),
                best_tier_blocked=min(r.evidence_tier for r in affected),
                rule=rule,
            )
        )
    # Most-blocking first, then by the strongest tier it holds down: clearing a blocker on a
    # study that is otherwise nearly believable is worth more than clearing one on a study
    # that would still be rejected afterwards.
    rows.sort(key=lambda r: (-r.study_count, r.best_tier_blocked, r.text))
    return rows


def propose(results: Sequence[ResearchResult]) -> dict[str, Any]:
    """The agenda: ordered actions, or an explicit statement that there is nothing to do."""
    materialised = list(results)
    rows = census(materialised)

    actions: list[Action] = []
    for row in rows:
        if row.classification is not BlockerClass.ACTIONABLE or row.rule is None:
            continue
        actions.append(
            Action(
                summary=row.rule.action,
                detail=row.rule.detail,
                unblocks=row.study_count,
                studies=row.studies,
                latency=row.rule.latency,
                limitation=row.rule.limitation,
                blocker=row.text,
            )
        )

    unclassified = [r for r in rows if r.classification is BlockerClass.UNCLASSIFIED]
    acquirable = [r for r in rows if r.classification is BlockerClass.ACQUIRABLE]
    inherent = [r for r in rows if r.classification is BlockerClass.INHERENT]

    selectable = [r for r in materialised if r.verdict is Verdict.SELECTABLE]

    if not actions:
        if not materialised:
            nothing = "no studies were supplied, so there is nothing to reason about"
        elif not rows:
            nothing = (
                "no study carries a blocker. Every result here has been concluded on its own "
                "terms, so the next experiment is a new question rather than a repair"
            )
        else:
            nothing = (
                "nothing is worth running: every remaining blocker is either inherent to the "
                "method or requires data acquisition, which is a spending decision rather "
                "than a compute job"
            )
    else:
        nothing = ""

    return {
        "schema_version": 1,
        "studies_considered": len(materialised),
        "selectable_today": len(selectable),
        "recommended_actions": [a.to_dict() for a in actions],
        "nothing_to_run_because": nothing,
        "blockers_requiring_acquisition": [r.to_dict() for r in acquirable],
        "blockers_that_cannot_be_cleared": [r.to_dict() for r in inherent],
        "unclassified_blockers": [r.to_dict() for r in unclassified],
        "unclassified_warning": (
            "These blockers matched no rule, so no action was proposed for them. They are "
            "listed rather than dropped: an unrecognised blocker is a gap in this agent, not "
            "an absence of work."
            if unclassified else ""
        ),
        "census": [r.to_dict() for r in rows],
    }
