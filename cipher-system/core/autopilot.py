"""The unattended loop over Cipher's evidence: what do we believe, and what changed?

Phase 3 of the spine. It reads every adaptable report, ranks it, asks the agenda agent what
is worth doing, compares all of that against the previous run, and reports the difference.

A correction to the plan this implements. That plan said to collapse seven schedulers into
one, which was arrived at by counting filenames rather than reading them. They are not
redundant: `run_safe_scheduled_jobs` runs bounded operational jobs, `run_build_healing_loop`
heals the build, `run_local_research_scheduler` gates jobs on model-capability readiness,
`run_strategy_research_loop` discovers strategies, and `manage_unified_cipher` supervises
processes. Collapsing them would have deleted working behaviour. What was genuinely missing
is this: nothing read the evidence corpus as a whole or noticed when it changed.

Why change detection is the whole point. A loop that prints the same 61-study ranking every
morning trains its reader to ignore it. The useful output of an unattended research system is
the delta -- a study that became selectable, a blocker that appeared, a tier that moved --
and silence when there is none. `noop` is therefore a first-class result, not a failure.

What this loop can never do. Its highest output is a *proposal a human reads*. It ranks
evidence and names actions; it does not run engines, promote findings, or place orders. The
ceiling is asserted in the report itself and enforced by
tests/test_research_only_guard.py at the tree level.
"""
from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core import capture_continuity
from core.research_agenda import propose
from core.research_corpus import (
    DEFAULT_ROOT,
    DEPRIORITISED_REASON,
    collect,
    coverage,
    is_deprioritised,
)
from core.research_envelope import ResearchResult, Verdict, current_commit, rank

SCHEMA_VERSION = 1

DEFAULT_STATE_PATH = Path("/home/aarav/Aarav/cipher/runtime/governance/autopilot_state.json")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fingerprint(results: list[ResearchResult]) -> dict[str, Any]:
    """The comparable shape of one run, small enough to store and diff.

    Deliberately not the whole corpus: a state file that grows with every study becomes a
    thing nobody reads, and the diff only needs per-study tier, verdict and blocker set.
    """
    health = capture_continuity.read()
    return {
        # Capture continuity lives in the fingerprint because a new gap is exactly the kind of
        # change worth waking someone for: measured-spread coverage is the asset the whole
        # research programme rests on, and a day not captured cannot be captured later.
        "capture": {
            "distinct_days": health.distinct_days,
            "gap_count": health.gap_count,
            "missing_weekdays": list(health.missing_weekdays),
            "last_event": health.last_event,
        },
        "studies": {
            r.study_id: {
                "tier": r.evidence_tier,
                "verdict": r.verdict.value,
                "observations": r.sample.observations,
                "cost_basis": r.provenance.cost_basis,
                "blockers": sorted(r.blockers),
            }
            for r in results
        },
        "tier_counts": {
            str(tier): sum(1 for r in results if r.evidence_tier == tier) for tier in range(1, 6)
        },
    }


def diff_fingerprints(
    previous: Mapping[str, Any] | None, current: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Every meaningful change between two runs, most important first.

    A first run reports `baseline` rather than inventing 61 "new study" changes, because
    "everything is new" is noise that hides the one line that matters on run two.
    """
    if not previous:
        return [{
            "kind": "baseline",
            "detail": f"first run: {len(current['studies'])} studies recorded as the baseline",
        }]

    changes: list[dict[str, Any]] = []
    old_studies: Mapping[str, Any] = previous.get("studies") or {}
    new_studies: Mapping[str, Any] = current["studies"]

    for study_id, now in new_studies.items():
        before = old_studies.get(study_id)
        if before is None:
            changes.append({
                "kind": "new_study",
                "study_id": study_id,
                "detail": f"appeared at tier {now['tier']} ({now['verdict']})",
            })
            continue
        verdict_changed = before.get("verdict") != now["verdict"]
        tier_changed = before.get("tier") != now["tier"]

        if verdict_changed:
            # Report the verdict move alone, carrying the tier with it. Emitting a separate
            # tier_improved/regressed line here double-counts one event and mislabels it: a
            # study going from REJECTED to INCONCLUSIVE moves tier 4 -> 5, which is a real
            # loss of certainty but reads as "got worse" when it may mean the study stopped
            # being clearly dead and became merely unmeasurable.
            changes.append({
                "kind": "verdict_changed",
                "study_id": study_id,
                "detail": (
                    f"{before.get('verdict')} -> {now['verdict']} "
                    f"(tier {before.get('tier')} -> {now['tier']})"
                ),
            })
        elif tier_changed:
            # Same verdict, different tier: the cost provenance or the blocker set moved
            # underneath an unchanged conclusion, which is a genuine independent change.
            improved = now["tier"] < before.get("tier", 9)
            changes.append({
                "kind": "tier_improved" if improved else "tier_regressed",
                "study_id": study_id,
                "detail": (
                    f"tier {before.get('tier')} -> {now['tier']} with the verdict unchanged "
                    f"at {now['verdict']}"
                ),
            })
        # Compared because they are stored. A field kept in the fingerprint but left out of
        # the diff is the worst of both: it looks covered and silently is not. The sample
        # growing by ten trades with an unchanged verdict was invisible until this was added.
        if before.get("cost_basis") != now["cost_basis"]:
            changes.append({
                "kind": "cost_basis_changed",
                "study_id": study_id,
                "detail": f"{before.get('cost_basis')} -> {now['cost_basis']}",
            })
        old_observations = before.get("observations")
        if old_observations != now["observations"]:
            direction = "grew" if (old_observations or 0) < now["observations"] else "shrank"
            changes.append({
                "kind": "sample_changed",
                "study_id": study_id,
                "detail": f"sample {direction}: {old_observations} -> {now['observations']}",
            })

        gained = set(now["blockers"]) - set(before.get("blockers") or ())
        cleared = set(before.get("blockers") or ()) - set(now["blockers"])
        for blocker in sorted(gained):
            changes.append({"kind": "blocker_appeared", "study_id": study_id, "detail": blocker})
        for blocker in sorted(cleared):
            changes.append({"kind": "blocker_cleared", "study_id": study_id, "detail": blocker})

    old_capture = previous.get("capture") or {}
    new_capture = current.get("capture") or {}
    # Only diff when the previous pass recorded capture at all. A state file written before
    # this field existed has no capture block, and comparing against an empty set reported
    # every long-standing gap as newly appeared -- two historical gaps showed up as fresh
    # alarms on the first pass after the field was added. An absent previous block is a
    # baseline for capture, exactly as an absent previous state is a baseline for studies.
    if old_capture:
        old_gaps = set(old_capture.get("missing_weekdays") or ())
        new_gaps = set(new_capture.get("missing_weekdays") or ())
        for day in sorted(new_gaps - old_gaps):
            changes.append({
                "kind": "capture_gap_appeared",
                "study_id": "",
                "detail": f"no option-quote capture on {day}; that day cannot be recovered",
            })
        old_days = old_capture.get("distinct_days")
        new_days = new_capture.get("distinct_days")
        if old_days is not None and new_days is not None and new_days != old_days:
            changes.append({
                "kind": "capture_days_changed",
                "study_id": "",
                "detail": f"measured-spread coverage {old_days} -> {new_days} capture days",
            })

    for study_id in old_studies:
        if study_id not in new_studies:
            # A study leaving the corpus because scope changed is a decision; a study leaving
            # for any other reason is a fault. Reporting both as "disappeared" would make a
            # deliberate deprioritisation look like data loss, and the next reader would go
            # hunting for files that are exactly where they were left.
            if is_deprioritised(study_id):
                changes.append({
                    "kind": "study_deprioritised",
                    "study_id": study_id,
                    "detail": (
                        "held out of the active agenda by scope decision, not lost: "
                        f"{DEPRIORITISED_REASON}. Still on disk and readable with scope='all'."
                    ),
                })
            else:
                changes.append({
                    "kind": "study_disappeared",
                    "study_id": study_id,
                    "detail": "present in the previous run, absent now",
                })

    # A study becoming believable is the only change worth waking someone for, so it sorts
    # first regardless of how many blockers moved around beneath it.
    priority = {
        "tier_improved": 0,
        "blocker_cleared": 1,
        "capture_gap_appeared": 1,
        "verdict_changed": 2,
        "cost_basis_changed": 3,
        "tier_regressed": 4,
        "blocker_appeared": 5,
        "capture_days_changed": 6,
        "sample_changed": 6,
        "new_study": 7,
        "study_disappeared": 8,
        "study_deprioritised": 9,
    }
    changes.sort(key=lambda c: (priority.get(c["kind"], 9), c.get("study_id", "")))
    return changes


def load_state(path: Path) -> dict[str, Any] | None:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # A missing or corrupt state file makes this run a baseline. It must never make the
        # run fail: the loop's job is to report evidence, and it can still do that.
        return None
    return loaded if isinstance(loaded, dict) else None


def save_state(path: Path, state: Mapping[str, Any]) -> None:
    """Write atomically, so an interrupted run cannot leave a half-written baseline.

    A truncated state file would read as "no previous run" and silently reset the diff, which
    is the one failure that would make the next report wrong rather than absent.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(state, stream, indent=2, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def run_once(
    *,
    root: Path = DEFAULT_ROOT,
    state_path: Path = DEFAULT_STATE_PATH,
    dry_run: bool = False,
) -> dict[str, Any]:
    """One pass: observe, rank, propose, diff, report. Never act on a market."""
    commit = current_commit()
    results, unadapted = collect(root, commit=commit)
    ranking = rank(results)
    agenda = propose(results)

    current = _fingerprint(results)
    previous_state = load_state(state_path)
    previous = (previous_state or {}).get("fingerprint")
    changes = diff_fingerprints(previous, current)
    is_baseline = bool(changes) and changes[0]["kind"] == "baseline"

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": utcnow(),
        "commit": commit,
        "root": str(root),
        # Asserted in every report so an operator reading only this file still knows the
        # ceiling. The tree-level guarantee is tests/test_research_only_guard.py.
        "live_order_authority": False,
        "highest_possible_output": "a proposal a human reads",
        "coverage": coverage(results, unadapted),
        "capture_health": capture_continuity.read().to_dict(),
        "tier_counts": current["tier_counts"],
        "selectable": [r.study_id for r in results if r.verdict is Verdict.SELECTABLE],
        "changes": changes if not is_baseline else [],
        "baseline": is_baseline,
        "noop": not is_baseline and not changes,
        "recommended_actions": agenda["recommended_actions"],
        "nothing_to_run_because": agenda["nothing_to_run_because"],
        "unclassified_blockers": agenda["unclassified_blockers"],
        "groups": ranking["groups"],
        "dry_run": dry_run,
    }

    if not dry_run:
        save_state(state_path, {
            "schema_version": SCHEMA_VERSION,
            "updated_at": report["generated_at"],
            "commit": commit,
            "fingerprint": current,
        })

    return report


DEFAULT_REPORT_PATH = Path(
    "/home/aarav/Aarav/cipher/runtime/governance/autopilot_last_run.json"
)

#: The timer runs daily, so anything older than a day and a half means a pass was missed.
#: Reported rather than hidden: a stale belief presented as current is the whole failure this
#: system is built to avoid.
STALE_AFTER_SECONDS = 36 * 3600


def last_run_summary(
    path: Path = DEFAULT_REPORT_PATH, *, now: datetime | None = None
) -> dict[str, Any]:
    """What Cipher currently believes, read from the last autopilot pass.

    Serves the stored artifact instead of recomputing. A live recomputation walks every
    report under runtime/data -- 61 studies and rising -- which is far too slow for an HTTP
    handler and would compete with the live collector for IO on every page load. The cost of
    caching is staleness, so the age is returned with the data and `stale` is computed rather
    than left for the caller to infer.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "available": False,
            "reason": (
                "no autopilot pass has been recorded yet. The pass runs daily on "
                "cipher-autopilot.timer, or on demand with scripts/run_autopilot.py."
            ),
            "live_order_authority": False,
        }
    if not isinstance(payload, dict):
        return {"available": False, "reason": "stored report is not an object",
                "live_order_authority": False}

    generated_at = str(payload.get("generated_at") or "")
    age_seconds: float | None = None
    try:
        moment = datetime.fromisoformat(generated_at)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        age_seconds = ((now or datetime.now(timezone.utc)) - moment).total_seconds()
    except ValueError:
        # An unparseable timestamp must not be reported as fresh. Age stays None and `stale`
        # is True, so the UI degrades to "unknown age" rather than to "current".
        age_seconds = None

    return {
        "available": True,
        "as_of": generated_at or None,
        "age_seconds": age_seconds,
        "stale": age_seconds is None or age_seconds > STALE_AFTER_SECONDS,
        "stale_after_seconds": STALE_AFTER_SECONDS,
        "commit": payload.get("commit"),
        "live_order_authority": False,
        "highest_possible_output": payload.get("highest_possible_output"),
        "headline": headline(payload),
        "coverage": payload.get("coverage"),
        "capture_health": payload.get("capture_health"),
        "tier_counts": payload.get("tier_counts"),
        "selectable": payload.get("selectable") or [],
        "changes": payload.get("changes") or [],
        "baseline": bool(payload.get("baseline")),
        "noop": bool(payload.get("noop")),
        "recommended_actions": payload.get("recommended_actions") or [],
        "nothing_to_run_because": payload.get("nothing_to_run_because") or "",
        "unclassified_blockers": payload.get("unclassified_blockers") or [],
        "groups": payload.get("groups") or [],
    }


def headline(report: Mapping[str, Any]) -> str:
    """One line fit for an alert channel.

    Phrased so that the quiet case is obviously quiet. An unattended loop that shouts every
    morning is one whose alerts get muted, and a muted channel is worse than no channel.
    """
    if report.get("baseline"):
        studies = report["coverage"]["adapted"]
        return f"Cipher autopilot: baseline recorded over {studies} studies, nothing to compare yet."
    if report.get("noop"):
        return "Cipher autopilot: no change in the evidence."

    changes = report.get("changes") or []
    improved = [c for c in changes if c["kind"] == "tier_improved"]
    cleared = [c for c in changes if c["kind"] == "blocker_cleared"]
    regressed = [c for c in changes if c["kind"] == "tier_regressed"]
    parts = []
    if improved:
        parts.append(f"{len(improved)} study(s) improved tier")
    if cleared:
        parts.append(f"{len(cleared)} blocker(s) cleared")
    if regressed:
        parts.append(f"{len(regressed)} regressed")
    if not parts:
        parts.append(f"{len(changes)} change(s)")
    selectable = report.get("selectable") or []
    tail = f"; {len(selectable)} selectable" if selectable else "; still nothing selectable"
    return "Cipher autopilot: " + ", ".join(parts) + tail
