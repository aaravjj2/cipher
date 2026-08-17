"""Adapt existing lab reports into the shared envelope.

Deliberately written against each lab's *published payload* -- the `report.json` it already
writes -- rather than inside the labs themselves. `eod_pattern_lab` is 1,581 lines and
`leveraged_etf_wheel_parameter_lab` sits beside a 2,178-line engine; editing either to emit
a new shape risks the results while proving nothing about the shape. Reading what they
already produce costs them no change at all, and if the envelope turns out wrong the labs
never knew about it.

Two adapters are enough to prove the contract, and these two were chosen because they
disagree about almost everything: the wheel ranks parameter *variants* on
`total_return_pct`, EOD ranks *patterns* on `profit_factor` beside an FDR q-value. If one
shape holds both, it holds.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from core.research_envelope import (
    UNKNOWN,
    Candidate,
    Metric,
    Provenance,
    ResearchResult,
    Sample,
    verdict_from_blockers,
)

# The wheel trades NVDL, SOXL, TQQQ and TSLL. As of the 2026-08-12 profile rebuild all four
# ARE captured with sufficient samples -- an earlier version of this file asserted they were
# absent, which was true of the 23-symbol profile and stopped being true at 27.
#
# They are still not *costable*, and the reason is now sharper rather than gone: the capture
# window is 2026-07-22 to 08-12 while the wheel studies run 2024-02 to 2026-06, so the
# measured spread describes a period the study never traded. Marking these results
# `measured:` because a number now exists would be precisely the retrofit this programme
# forbids -- it would let a study reach evidence tier 1 on a cost basis that does not apply
# to its own window.
#
# What the new measurement does tell us points the opposite way from the equity indices:
# NVDL's measured median option half-spread is 12.125% of premium and TSLL's is 6.375%,
# against the labs' harshest `severe` assumption of 10%. For this universe the assumption is
# optimistic, not conservative.
WHEEL_UNCOSTED = "assumed:capture-window-does-not-overlap-study-window"

WHEEL_COVERAGE_BLOCKER = (
    "execution cost is assumed, not measured, for this study's period: the wheel's symbols "
    "are captured only from 2026-07-22 onward, so no measured spread covers the years this "
    "study trades. The recent capture also indicates the assumption is optimistic here -- "
    "NVDL's measured median option half-spread is 12.125% of premium against a 10% worst case"
)


def _number(value: Any) -> float | None:
    """Coerce to float, or None. A non-numeric metric is unknown, never zero."""
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    # inf shows up as a profit factor when there were no losing trades. It is a real
    # observation but not a rankable number, so it is reported as unknown rather than
    # winning every sort forever.
    return result if result == result and abs(result) != float("inf") else None


def wheel_parameter_lab_envelope(
    payload: Mapping[str, Any],
    *,
    study_id: str,
    commit: str = UNKNOWN,
    cost_basis: str = WHEEL_UNCOSTED,
    extra_blockers: Sequence[str] = (),
) -> ResearchResult:
    """Envelope for `leveraged_etf_wheel_parameter_lab`'s report.json.

    Expects `{"start", "end", "variants": [...]}` as that lab writes it.
    """
    variants = list(payload.get("variants") or [])
    candidates: list[Candidate] = []
    for row in variants:
        candidates.append(
            Candidate(
                candidate_id=str(row.get("variant_id") or f"variant-{len(candidates)}"),
                primary=Metric(
                    name="total_return_pct",
                    value=_number(row.get("total_return_pct")),
                    unit="pct",
                    higher_is_better=True,
                ),
                metrics={
                    "max_drawdown_pct": _number(row.get("max_drawdown_pct")),
                    "win_rate_pct": _number(row.get("win_rate_pct")),
                    "closed_option_events": _number(row.get("closed_option_events")),
                    "pop_floor": _number(row.get("pop_floor")),
                    "minimum_iv": _number(row.get("minimum_iv")),
                },
            )
        )

    # The most closed events any single variant managed. Not the sum: variants re-run the
    # same window, so adding them counts the same market twice and would inflate the sample
    # in proportion to how many parameters were swept.
    event_counts = [int(_number(row.get("closed_option_events")) or 0) for row in variants]
    observations = max(event_counts, default=0)

    symbols: list[str] = []
    for row in variants:
        for symbol in row.get("stock_symbols") or []:
            if symbol not in symbols:
                symbols.append(str(symbol))

    blockers = [WHEEL_COVERAGE_BLOCKER, *extra_blockers]
    positive = any((_number(row.get("total_return_pct")) or 0.0) > 0.0 for row in variants)
    verdict = verdict_from_blockers(blockers, observations, passes=positive)

    return ResearchResult(
        study_id=study_id,
        engine="leveraged_etf_wheel_parameter_lab",
        verdict=verdict,
        sample=Sample(
            observations=observations,
            symbols=tuple(symbols),
            start=payload.get("start"),
            end=payload.get("end"),
        ),
        provenance=Provenance(cost_basis=cost_basis, commit=commit, generated_at=UNKNOWN),
        blockers=tuple(blockers),
        candidates=tuple(candidates),
        notes=(f"{len(variants)} parameter variants swept",),
    )


def wheel_engine_envelope(
    payload: Mapping[str, Any],
    *,
    study_id: str,
    commit: str = UNKNOWN,
    cost_basis: str = WHEEL_UNCOSTED,
) -> ResearchResult:
    """Envelope for `leveraged_etf_csp_wheel`'s report.json -- 54 of the studies on disk.

    Everything lives under `summary`, which is why a probe at the top level read `events`,
    `total_return_pct` and `skips` as `None` and the runs were recorded as malformed. They
    were never malformed; they were read at the wrong depth.

    One run, so one candidate. The engine already publishes `research_grade` and
    `research_grade_blockers`, so the adapter reads those rather than inventing its own.
    """
    summary = payload.get("summary")
    summary = summary if isinstance(summary, Mapping) else {}

    total_return = _number(summary.get("total_return_pct"))
    observations = int(_number(summary.get("closed_option_events")) or 0)

    blockers: list[str] = [WHEEL_COVERAGE_BLOCKER]
    if not summary.get("research_grade", False):
        blockers.extend(str(b) for b in (summary.get("research_grade_blockers") or ()))

    candidate = Candidate(
        candidate_id="run",
        primary=Metric("total_return_pct", total_return, "pct", higher_is_better=True),
        metrics={
            "max_drawdown_pct": _number(summary.get("max_drawdown_pct")),
            "win_rate_pct": _number(summary.get("win_rate_pct")),
            "closed_option_events": _number(summary.get("closed_option_events")),
            "events": _number(summary.get("events")),
            "skips": _number(summary.get("skips")),
            "realized_pnl": _number(summary.get("realized_pnl")),
        },
    )

    verdict = verdict_from_blockers(blockers, observations, passes=bool(total_return and total_return > 0))

    return ResearchResult(
        study_id=study_id,
        engine="leveraged_etf_csp_wheel",
        verdict=verdict,
        sample=Sample(
            observations=observations,
            # `stock_symbols` is a *count* in this shape, unlike the parameter lab where the
            # same key holds a list. It cannot name the symbols, so it is reported as a
            # metric and the symbol tuple is left empty rather than filled with a number.
            symbols=(),
            start=summary.get("start"),
            end=summary.get("end"),
        ),
        provenance=Provenance(cost_basis=cost_basis, commit=commit, generated_at=UNKNOWN),
        blockers=tuple(blockers),
        candidates=(candidate,),
        notes=(f"stock_symbols count: {summary.get('stock_symbols')}",),
    )


#: Execution models the walkforward stresses, from most to least favourable. Ordered so the
#: adapter can find the harshest one that was actually run without hard-coding which exist.
EXECUTION_SEVERITY: tuple[str, ...] = ("base", "worse", "severe")


def eod_option_walkforward_envelope(
    payload: Mapping[str, Any],
    *,
    study_id: str,
    commit: str = UNKNOWN,
    cost_basis: str = UNKNOWN,
) -> ResearchResult:
    """Envelope for the EOD option walkforward -- the strongest evidence class here.

    This is the only shape with genuine out-of-sample holdout months, so it is the only one
    that could reach a selectable verdict. It also stress-tests execution across `base`,
    `worse` and `severe` models, and that changes how the verdict must be computed.

    The study is judged on the **harshest** execution model that was run, not the best. On
    the expanding run, `robust` posts a profit factor of 1.16 under `base` and 0.61 under
    `severe`: reporting 1.16 as the result would be choosing the most favourable cost
    assumption available and calling it a finding. Candidate ids carry their execution model
    so every number on the page says which assumption produced it.
    """
    aggregates = [a for a in (payload.get("aggregate_results") or []) if isinstance(a, Mapping)]

    candidates: list[Candidate] = []
    for row in aggregates:
        policy = str(row.get("policy") or "policy")
        model = str(row.get("execution_model") or UNKNOWN)
        candidates.append(
            Candidate(
                candidate_id=f"{policy}@{model}",
                primary=Metric("profit_factor", _number(row.get("profit_factor")), "ratio", True),
                metrics={
                    "trades": _number(row.get("trades")),
                    "win_rate_pct": _number(row.get("win_rate_pct")),
                    "mean_return_pct": _number(row.get("mean_return_pct")),
                    "pnl_on_deployed_risk_pct": _number(row.get("pnl_on_deployed_risk_pct")),
                    "total_pnl_dollars": _number(row.get("total_pnl_dollars")),
                    "max_drawdown_dollars": _number(row.get("max_drawdown_dollars")),
                },
            )
        )

    models_present = {str(r.get("execution_model")) for r in aggregates}
    harshest = next(
        (m for m in reversed(EXECUTION_SEVERITY) if m in models_present),
        None,
    )
    under_harshest = [
        _number(r.get("profit_factor"))
        for r in aggregates
        if str(r.get("execution_model")) == harshest
    ]
    survives = any((pf or 0.0) > 1.0 for pf in under_harshest)

    # Trades are the same holdout trades re-scored under each execution model, so the sample
    # is the largest single arm, not the sum across models.
    observations = max((int(_number(r.get("trades")) or 0) for r in aggregates), default=0)

    blockers: list[str] = []
    if not payload.get("research_grade", False):
        reason = payload.get("research_grade_reason")
        blockers.append(str(reason) if reason else "not research grade; no reason recorded")

    resolved_cost = cost_basis
    if resolved_cost == UNKNOWN and models_present:
        resolved_cost = f"assumed:execution-stressed({'|'.join(sorted(models_present))})"

    verdict = verdict_from_blockers(blockers, observations, passes=survives)

    best_case = max((pf for pf in (_number(r.get("profit_factor")) for r in aggregates) if pf is not None), default=None)
    notes = [
        f"holdout months: {', '.join(str(m) for m in (payload.get('holdout_months') or ())) or 'none'}",
        (
            f"judged on the harshest execution model run ({harshest}): "
            f"{'a policy clears profit factor 1' if survives else 'no policy clears profit factor 1'}"
        ),
    ]
    if best_case is not None:
        notes.append(f"best case under any model: profit factor {best_case:.3f}")

    return ResearchResult(
        study_id=study_id,
        engine="eod_option_walkforward",
        verdict=verdict,
        sample=Sample(
            observations=observations,
            symbols=(),
            start=payload.get("analysis_start"),
            end=payload.get("analysis_end"),
        ),
        provenance=Provenance(
            cost_basis=resolved_cost,
            commit=commit,
            generated_at=str(payload.get("generated_at") or UNKNOWN),
        ),
        blockers=tuple(blockers),
        candidates=tuple(candidates),
        notes=tuple(notes),
    )


def eod_pattern_lab_envelope(
    payload: Mapping[str, Any],
    *,
    study_id: str,
    commit: str = UNKNOWN,
    cost_basis: str = UNKNOWN,
) -> ResearchResult:
    """Envelope for `eod_pattern_lab`'s report.json.

    That lab already publishes `research_grade`, `research_grade_reason` and `caveats`, which
    is exactly the blockers concept under different names -- so the adapter reads them
    instead of inventing a parallel notion of what is wrong with the study.
    """
    patterns = list(payload.get("patterns_full") or [])
    candidates: list[Candidate] = []
    for row in patterns:
        candidates.append(
            Candidate(
                candidate_id=str(row.get("pattern_id") or f"pattern-{len(candidates)}"),
                primary=Metric(
                    name="profit_factor",
                    value=_number(row.get("profit_factor")),
                    unit="ratio",
                    higher_is_better=True,
                ),
                metrics={
                    "n": _number(row.get("n")),
                    "net_mean_return_pct": _number(row.get("net_mean_return_pct")),
                    "win_rate_pct": _number(row.get("win_rate_pct")),
                    "hac_p_value": _number(row.get("hac_p_value")),
                    "fdr_q_value": _number(row.get("fdr_q_value")),
                },
            )
        )

    # Pattern occurrences for the most-observed pattern. Summing across patterns would count
    # the same sessions once per hypothesis tested, which is precisely backwards: testing
    # more hypotheses on one dataset weakens the evidence, it does not multiply it.
    observations = max((int(_number(row.get("n")) or 0) for row in patterns), default=0)

    blockers: list[str] = []
    if not payload.get("research_grade", False):
        reason = payload.get("research_grade_reason")
        blockers.append(str(reason) if reason else "not research grade; no reason recorded")
    blockers.extend(str(c) for c in (payload.get("caveats") or []))

    # A pattern is only evidence if it survives multiple-testing correction. Without that,
    # "the best of 900 patterns looked good" is a description of chance.
    survivors = [
        row for row in patterns
        if (_number(row.get("fdr_q_value")) is not None)
        and (_number(row.get("fdr_q_value")) or 1.0) <= 0.10
        and (_number(row.get("profit_factor")) or 0.0) > 1.0
    ]
    verdict = verdict_from_blockers(blockers, observations, passes=bool(survivors))

    # Symbols and dates live under data.coverage keyed by symbol, not as data.symbols --
    # reading the latter returned an empty tuple that looked like "no symbols" rather than
    # "wrong key", which is the failure mode this whole envelope exists to stop.
    data_summary = payload.get("data") or {}
    coverage = data_summary.get("coverage") if isinstance(data_summary, Mapping) else None
    coverage = coverage if isinstance(coverage, Mapping) else {}
    symbols = tuple(sorted(str(s) for s in coverage))
    ends = [str(v.get("end")) for v in coverage.values() if isinstance(v, Mapping) and v.get("end")]

    # The lab records a flat round-trip cost, so the study is costed -- just not measured
    # per symbol. Saying so beats reporting `unknown`, which would understate what is known,
    # and beats implying measurement, which would overstate it.
    resolved_cost = cost_basis
    if resolved_cost == UNKNOWN:
        round_trip = _number(data_summary.get("round_trip_cost_pct"))
        if round_trip is not None:
            resolved_cost = f"assumed:flat-round-trip-{round_trip}pct"

    return ResearchResult(
        study_id=study_id,
        engine="eod_pattern_lab",
        verdict=verdict,
        sample=Sample(
            observations=observations,
            symbols=symbols,
            start=payload.get("analysis_sample_start"),
            end=max(ends) if ends else None,
        ),
        provenance=Provenance(
            cost_basis=resolved_cost,
            commit=commit,
            generated_at=str(payload.get("generated_at") or UNKNOWN),
        ),
        blockers=tuple(blockers),
        candidates=tuple(candidates),
        notes=(
            f"{len(patterns)} patterns tested, {len(survivors)} survive FDR<=0.10 with "
            "profit factor above 1",
        ),
    )


def structural_fib_envelope(
    payload: Mapping[str, Any],
    *,
    study_id: str,
    commit: str = UNKNOWN,
    cost_basis: str = UNKNOWN,
) -> ResearchResult:
    """Envelope for `core/structural_fib_lab`'s report.json.

    This study is shaped unlike the others in one way that matters: its primary quantity is
    a *refutation*, not a return. The strategy publishes hit rates, so the measurement that
    settles it is the gap between each claimed rate and the interval the data supports. The
    candidate metric is therefore the measured touch rate, and a candidate "passes" only if
    its own published claim falls inside its confidence interval.

    Cost basis is `not-applicable:price-level-claim` rather than assumed or measured. Whether
    price reaches a level is a fact about the price path with no execution cost in it at all.
    Recording an assumed option cost here would attach a number to a question that does not
    take one, and recording `unknown` would imply the study is missing something it is not.
    """
    overall = payload.get("overall") or {}
    overall = overall if isinstance(overall, Mapping) else {}

    candidates: list[Candidate] = []
    for leg, row in sorted(overall.items()):
        if not isinstance(row, Mapping) or not row.get("n"):
            continue
        touch = _number(row.get("touch_rate"))
        candidates.append(
            Candidate(
                candidate_id=str(leg),
                primary=Metric(
                    name="touch_rate",
                    value=None if touch is None else touch * 100.0,
                    unit="pct",
                    higher_is_better=True,
                ),
                metrics={
                    "n": _number(row.get("n")),
                    "claimed_pct": (None if _number(row.get("claimed")) is None
                                    else (_number(row.get("claimed")) or 0.0) * 100.0),
                    "race_win_rate_pct": (None if _number(row.get("race_win_rate")) is None
                                          else (_number(row.get("race_win_rate")) or 0.0) * 100.0),
                    "avg_return_pct": _number(row.get("avg_return_pct")),
                },
            )
        )

    # One observation per signal, summed across legs: each is a distinct dated event, not the
    # same session re-tested under another hypothesis.
    observations = sum(int(_number(r.get("n")) or 0)
                       for r in overall.values() if isinstance(r, Mapping))

    blockers: list[str] = list(str(x) for x in (payload.get("limitations") or ()))

    # Every leg carrying a published claim must have that claim excluded by its own interval
    # for the study to count as having settled anything.
    claimed_legs = [r for r in overall.values()
                    if isinstance(r, Mapping) and r.get("claim_excluded") is not None]
    refuted = [r for r in claimed_legs if r.get("claim_excluded")]
    underpowered = [r for r in claimed_legs if r.get("underpowered")]
    if underpowered:
        blockers.append(
            f"{len(underpowered)} leg(s) with a published claim have too few observations to "
            "support a verdict either way"
        )

    # `passes` means "a claim survived", which is the thing a reader would act on. Every claim
    # being refuted is a decisive result, and it is a REJECTED verdict, not a blocked one.
    survived = [r for r in claimed_legs if not r.get("claim_excluded")]
    verdict = verdict_from_blockers(blockers, observations, passes=bool(survived))

    coverage = payload.get("coverage") or {}
    coverage = coverage if isinstance(coverage, Mapping) else {}
    symbols = tuple(sorted(str(s) for s, c in coverage.items()
                           if isinstance(c, Mapping) and c.get("days")))
    starts = [str(c.get("start")) for c in coverage.values()
              if isinstance(c, Mapping) and c.get("start")]
    ends = [str(c.get("end")) for c in coverage.values()
            if isinstance(c, Mapping) and c.get("end")]

    resolved_cost = cost_basis
    if resolved_cost == UNKNOWN:
        resolved_cost = str(payload.get("cost_basis") or "not-applicable:price-level-claim")

    control = payload.get("matched_random_entry_control") or {}
    control_note = ""
    if isinstance(control, Mapping) and control:
        edges = []
        for leg, row in control.items():
            strat = overall.get(leg)
            if isinstance(strat, Mapping) and isinstance(row, Mapping):
                s_t, c_t = _number(strat.get("touch_rate")), _number(row.get("touch_rate"))
                if s_t is not None and c_t is not None:
                    edges.append((s_t - c_t) * 100.0)
        if edges:
            control_note = (
                f"matched random-entry control run; touch-rate edge spans "
                f"{min(edges):+.1f} to {max(edges):+.1f} points"
            )

    return ResearchResult(
        study_id=study_id,
        engine="structural_fib_lab",
        verdict=verdict,
        sample=Sample(
            observations=observations,
            symbols=symbols,
            start=min(starts) if starts else None,
            end=max(ends) if ends else None,
        ),
        provenance=Provenance(
            cost_basis=resolved_cost,
            commit=commit,
            generated_at=str(payload.get("generated_at") or UNKNOWN),
        ),
        blockers=tuple(blockers),
        candidates=tuple(candidates),
        notes=tuple(n for n in (
            f"{len(refuted)} of {len(claimed_legs)} published claims refuted by their own "
            f"95% interval",
            control_note,
        ) if n),
    )


def wave_lock_envelope(
    payload: Mapping[str, Any],
    *,
    study_id: str,
    commit: str = UNKNOWN,
    cost_basis: str = UNKNOWN,
) -> ResearchResult:
    """Envelope for `core/wave_lock_exits`'s report.json — the study of record for Wave Lock.

    Routed on the exit sweep rather than the descriptive hit-rate report because this is the
    payload carrying the statistic that decides the verdict: a session-clustered t on the mean
    return. Wave Lock's hit rates are real and are not the question; whether its expectancy is
    distinguishable from zero is, and only this report answers it.

    A candidate here is an `engine|policy` pair. It passes only if its mean return is positive
    *and* clears |t| >= 2 on the clustered estimator. Ranking policies by point estimate alone
    would promote whichever one noise favoured -- which, on the measured data, is what the
    spread between the best and worst policy consists of.
    """
    by_engine = payload.get("by_engine") or {}
    by_engine = by_engine if isinstance(by_engine, Mapping) else {}
    control = payload.get("matched_random_entry_control") or {}
    control = control if isinstance(control, Mapping) else {}

    candidates: list[Candidate] = []
    observations = 0
    established: list[str] = []
    for engine, policies in sorted(by_engine.items()):
        if not isinstance(policies, Mapping):
            continue
        engine_control = control.get(engine) or {}
        engine_control = engine_control if isinstance(engine_control, Mapping) else {}
        for policy, row in sorted(policies.items()):
            if not isinstance(row, Mapping) or not row.get("n"):
                continue
            # Observations are counted once per engine, on its baseline policy: every policy
            # re-scores the same signals, so summing across policies would multiply one
            # dataset by seven and report it as seven times the evidence.
            if policy == "baseline_1R":
                observations += int(_number(row.get("n")) or 0)
            mean = _number(row.get("avg_return_pct"))
            tstat = _number(row.get("cluster_robust_t"))
            control_mean = _number((engine_control.get(policy) or {}).get("avg_return_pct")) \
                if isinstance(engine_control.get(policy), Mapping) else None
            if row.get("distinguishable_from_zero") and (mean or 0.0) > 0:
                established.append(f"{engine}|{policy}")
            candidates.append(Candidate(
                candidate_id=f"{engine}|{policy}",
                primary=Metric(
                    name="avg_return_pct", value=mean, unit="pct", higher_is_better=True,
                ),
                metrics={
                    "n": _number(row.get("n")),
                    "sessions": _number(row.get("sessions")),
                    "win_rate_pct": (None if _number(row.get("win_rate")) is None
                                     else (_number(row.get("win_rate")) or 0.0) * 100.0),
                    "cluster_robust_t": tstat,
                    "cluster_robust_se_pct": _number(row.get("cluster_robust_se_pct")),
                    "control_avg_return_pct": control_mean,
                    "edge_over_control_pct": (None if (mean is None or control_mean is None)
                                              else mean - control_mean),
                },
            ))

    blockers: list[str] = list(str(x) for x in (payload.get("limitations") or ()))
    if candidates and not established:
        blockers.append(
            f"no configuration of {len(candidates)} tested clears |t| >= 2 on a "
            "session-clustered standard error, so the sign of the mean return is not "
            "established for any of them"
        )

    verdict = verdict_from_blockers(blockers, observations, passes=bool(established))

    resolved_cost = cost_basis
    if resolved_cost == UNKNOWN:
        resolved_cost = str(payload.get("cost_basis") or "not-applicable:price-level-claim")

    params = payload.get("params") or {}
    params = params if isinstance(params, Mapping) else {}
    return ResearchResult(
        study_id=study_id,
        engine="wave_lock_exits",
        verdict=verdict,
        sample=Sample(observations=observations, symbols=("QQQ",)),
        provenance=Provenance(
            cost_basis=resolved_cost,
            commit=commit,
            generated_at=str(payload.get("generated_at") or UNKNOWN),
        ),
        blockers=tuple(blockers),
        candidates=tuple(candidates),
        notes=(
            f"{len(candidates)} engine/policy combinations scored on one in-sample dataset; "
            f"{len(established)} with a mean return distinguishable from zero",
            f"bar size {params.get('bar_minutes', 1)} minute, pivot anchor doubles as the stop",
        ),
    )
