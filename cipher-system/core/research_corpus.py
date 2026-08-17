"""Find every research report on disk and put it in the envelope.

Discovery lives here rather than in a script because three callers need it: the ranking read,
the agenda agent, and the autopilot loop. It was briefly a script-only function, and the
tests had to reach it through importlib -- a reliable sign the logic was in the wrong place.

The `unadapted` return value is load-bearing. Cipher has roughly 25 research engines and
four adapters; a collector that quietly returned only what it understood would let a caller
describe a fraction of the corpus as though it were all of it. Every report that cannot be
adapted is returned with the reason, so partial coverage is visible rather than inferred.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from core.research_envelope import ResearchResult, current_commit, rank
from core.research_envelope_adapters import (
    eod_option_walkforward_envelope,
    eod_pattern_lab_envelope,
    structural_fib_envelope,
    wave_lock_envelope,
    wheel_engine_envelope,
    wheel_parameter_lab_envelope,
)

Adapter = Callable[..., ResearchResult]

DEFAULT_ROOT = Path("/home/aarav/Aarav/cipher/runtime/data")

#: A report is routed by a key only its own engine writes, not by filename or directory --
#: report.json appears at dozens of paths here and the directory name is a study label
#: somebody chose, so both would misroute the moment a run is renamed.
#:
#: Order matters where shapes overlap: the wheel *engine* report and the wheel *parameter
#: lab* report both live under leveraged_etf_wheel/, but only the lab writes `variants`, so
#: it matches first and the engine's `stock_positions` catches the rest.
ROUTES: tuple[tuple[str, str, Adapter], ...] = (
    ("variants", "leveraged_etf_wheel_parameter_lab", wheel_parameter_lab_envelope),
    ("patterns_full", "eod_pattern_lab", eod_pattern_lab_envelope),
    ("fold_results", "eod_option_walkforward", eod_option_walkforward_envelope),
    ("stock_positions", "leveraged_etf_csp_wheel", wheel_engine_envelope),
    ("premarket_filter_check", "structural_fib_lab", structural_fib_envelope),
    ("policies_tested", "wave_lock_exits", wave_lock_envelope),
)

#: Directory name holding artifacts deliberately removed from the readable path. Two of them
#: assert a verdict the current code forbids, so they must never re-enter a ranking.
SUPERSEDED_DIR = "superseded"

#: Study prefixes held out of the active agenda by an explicit research decision.
#:
#: Not superseded and not wrong: these are real results that stay on disk, stay readable,
#: and stay in the corpus census. They are excluded from the *agenda* because the wheel is
#: no longer being worked, and 55 wheel studies sharing one blocker otherwise dominate every
#: recommendation the agent can make — the census stops describing the work in progress and
#: starts describing a line of work that was stopped.
#:
#: Scope is a decision, not a fact about the data, so it lives here as one editable tuple
#: rather than being inferred. Pass `scope="all"` to read the whole corpus regardless.
DEPRIORITISED_PREFIXES: tuple[str, ...] = ("leveraged_etf_wheel/",)

#: Why, recorded next to the what, so a later reader does not re-derive it from git history.
DEPRIORITISED_REASON = (
    "the wheel universe and wheel strategy are out of scope as of 2026-08-13; active focus "
    "is the Structural Fib (NVDA/AAPL), Flash Agentic floor bounce (MU), and EOD studies"
)


def is_deprioritised(study_id: str) -> bool:
    return any(study_id.startswith(prefix) for prefix in DEPRIORITISED_PREFIXES)


def route(payload: Mapping[str, Any]) -> tuple[str, Adapter] | None:
    for key, engine, adapter in ROUTES:
        if key in payload:
            return engine, adapter
    return None


def study_id_for(path: Path, root: Path) -> str:
    """A stable id from the path, so two runs of one engine stay distinguishable."""
    try:
        relative = path.relative_to(root)
    except ValueError:
        relative = path
    return relative.parent.as_posix() or relative.as_posix()


def collect(
    root: Path, *, commit: str | None = None, scope: str = "focus"
) -> tuple[list[ResearchResult], list[dict[str, Any]]]:
    """Adapt every readable report under `root`.

    `scope="focus"` (the default) omits `DEPRIORITISED_PREFIXES`, which is what the agenda
    and the autopilot read. `scope="all"` returns everything, for the census and for any
    question about the corpus as a whole. Nothing is deleted either way.
    """
    if scope not in ("focus", "all"):
        raise ValueError(f"scope must be 'focus' or 'all', got {scope!r}")
    resolved_commit = commit if commit is not None else current_commit()
    results: list[ResearchResult] = []
    unadapted: list[dict[str, Any]] = []
    for path in sorted(root.rglob("report.json")):
        if SUPERSEDED_DIR in path.parts:
            continue
        if scope == "focus" and is_deprioritised(study_id_for(path, root)):
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            unadapted.append({"path": str(path), "reason": f"unreadable: {exc.__class__.__name__}"})
            continue
        if not isinstance(payload, Mapping):
            unadapted.append({"path": str(path), "reason": "not a JSON object"})
            continue
        routed = route(payload)
        if routed is None:
            unadapted.append({
                "path": str(path),
                "reason": "no adapter for this shape",
                "top_level_keys": sorted(payload)[:8],
            })
            continue
        _engine, adapter = routed
        try:
            results.append(
                adapter(payload, study_id=study_id_for(path, root), commit=resolved_commit)
            )
        except (ValueError, TypeError, KeyError) as exc:
            # Reported, never swallowed: a study missing from the ranking because of a silent
            # exception is a study nobody knows is missing.
            unadapted.append({"path": str(path), "reason": f"adapter failed: {exc}"})
    return results, unadapted


def coverage(results: list[ResearchResult], unadapted: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "adapted": len(results),
        "unadapted": len(unadapted),
        "adapters_available": len(ROUTES),
        "note": (
            "Cipher has roughly 25 research engines. Only shapes with an adapter appear in "
            "the ranking; the rest are listed under 'unadapted' so this reads as partial "
            "coverage rather than as the whole picture."
        ),
    }


def build_ranking(root: Path) -> dict[str, Any]:
    commit = current_commit()
    results, unadapted = collect(root, commit=commit)
    report = rank(results)
    report["commit"] = commit
    report["root"] = str(root)
    report["coverage"] = coverage(results, unadapted)
    report["unadapted"] = unadapted
    return report
