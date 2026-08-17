"""How continuous is the measured-spread capture, and is the profile itself current?

The measurement programme rests on one asset: an unbroken run of captured option quotes.
Every trading day of continuous capture makes a future study costable with a `measured:`
basis; every gap permanently weakens the window that study will use, and no later work can
fill it -- quotes are observations, and a day not captured is a day gone.

`spread_profile.json` already records `missing_weekdays` and `sparse_days`, computed when the
profile is built. Nothing surfaced them. Two weekdays were already missing from a twelve-day
window (2026-07-24 and 2026-07-28, collector downtime rather than market closures) and that
was visible only to someone reading the raw artifact.

This module reads those fields and turns them into a monitored fact, so the autopilot can
report a *new* gap rather than leaving it to be discovered months later when a study needs
the window. It deliberately computes nothing the profile does not already know: a second
implementation of "which weekdays are missing" would be a second thing that can be wrong.

It also reports the profile's own age. A profile rebuilt weeks ago describes a capture that
has since continued, so acting on its `distinct_days` as though it were current would
understate coverage — and understating it is the failure that would make the programme look
stalled when it is not.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_PROFILE = Path(
    "/home/aarav/Aarav/cipher/runtime/data/execution_costs/spread_profile.json"
)

#: The profile is expensive to rebuild -- a full scan of a 56 GB database -- so it is not
#: rebuilt daily and a week is not alarming. Two weeks means the reported coverage is
#: materially behind the capture and any decision resting on it is using a stale count.
PROFILE_STALE_AFTER_DAYS = 14


@dataclass(frozen=True)
class CaptureHealth:
    available: bool
    reason: str = ""
    first_event: str | None = None
    last_event: str | None = None
    distinct_days: int | None = None
    missing_weekdays: tuple[str, ...] = ()
    sparse_days: tuple[str, ...] = ()
    profile_built_at: str | None = None
    profile_age_days: float | None = None
    profile_stale: bool = True

    @property
    def gap_count(self) -> int:
        """Missing weekdays plus sparse ones. A sparse day is a partial observation, not a
        clean one, so it counts against continuity rather than for it."""
        return len(self.missing_weekdays) + len(self.sparse_days)

    @property
    def verdict(self) -> str:
        if not self.available:
            return "no profile: capture continuity is unknown"
        if self.profile_stale:
            return (
                "the profile is stale, so this describes coverage as of the last rebuild, "
                "not as of today"
            )
        if self.gap_count == 0:
            return "continuous over the captured window"
        return f"{self.gap_count} imperfect day(s) in the captured window"

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "reason": self.reason,
            "first_event": self.first_event,
            "last_event": self.last_event,
            "distinct_days": self.distinct_days,
            "missing_weekdays": list(self.missing_weekdays),
            "sparse_days": list(self.sparse_days),
            "gap_count": self.gap_count,
            "profile_built_at": self.profile_built_at,
            "profile_age_days": self.profile_age_days,
            "profile_stale": self.profile_stale,
            "verdict": self.verdict,
        }


def _age_days(stamp: str | None, now: datetime | None) -> float | None:
    if not stamp:
        return None
    try:
        moment = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    delta = (now or datetime.now(timezone.utc)) - moment
    return round(delta.total_seconds() / 86400.0, 2)


def read(path: Path = DEFAULT_PROFILE, *, now: datetime | None = None) -> CaptureHealth:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return CaptureHealth(available=False, reason=f"no readable profile at {path}")
    if not isinstance(payload, Mapping):
        return CaptureHealth(available=False, reason="profile is not an object")

    window = payload.get("capture_window")
    window = window if isinstance(window, Mapping) else {}

    sparse = window.get("sparse_days")
    # sparse_days is a mapping of day -> event count in the artifact; only the keys matter
    # here, and sorting them keeps the fingerprint stable across rebuilds.
    if isinstance(sparse, Mapping):
        sparse_days = tuple(sorted(str(day) for day in sparse))
    elif isinstance(sparse, (list, tuple)):
        sparse_days = tuple(sorted(str(day) for day in sparse))
    else:
        sparse_days = ()

    missing = window.get("missing_weekdays")
    missing_weekdays = (
        tuple(sorted(str(day) for day in missing)) if isinstance(missing, (list, tuple)) else ()
    )

    built_at = payload.get("created_at")
    age = _age_days(str(built_at) if built_at else None, now)

    return CaptureHealth(
        available=True,
        first_event=(str(window["first_event"]) if window.get("first_event") else None),
        last_event=(str(window["last_event"]) if window.get("last_event") else None),
        distinct_days=(int(window["distinct_days"]) if window.get("distinct_days") is not None else None),
        missing_weekdays=missing_weekdays,
        sparse_days=sparse_days,
        profile_built_at=(str(built_at) if built_at else None),
        profile_age_days=age,
        # An unparseable or absent build date is treated as stale rather than fresh: reporting
        # unknown age as current is the one error that would make a decision wrong instead of
        # merely uninformed.
        profile_stale=age is None or age > PROFILE_STALE_AFTER_DAYS,
    )
