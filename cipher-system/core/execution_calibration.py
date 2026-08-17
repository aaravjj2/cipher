"""Compare a lab's assumed execution cost against measured option spreads.

Cipher's option labs price fills through `ExecutionModel(fraction, floor, fee)` triples --
`base` 0.03, `worse` 0.05, `severe` 0.10 -- where `fraction` is a half-spread expressed as a
fraction of premium, applied on top of an already-conservative bar-high buy. Those three
numbers were chosen, not measured, and every options verdict in the corpus is decided by
them: the walkforward studies are rejected precisely because no policy clears profit factor
1 under `severe`.

Meanwhile `data/execution_costs/spread_profile.json` holds measured
`option_half_spread_pct_of_premium` percentiles per symbol and DTE bucket, from millions of
quote samples. Nothing compared the two. This does.

What this is not. It is **not** a repricing of any backtest, and it must never be used as
one. The profile covers a 12-day capture window; the studies span months. The profile's own
caveat is explicit: "A backtest spanning years cannot be costed from it -- it can only be
told whether its assumption is optimistic against currently observable spreads." That single
question is what this module answers, and the answer is reported as a calibration verdict
rather than as a corrected return.

Why it is worth answering. For SPY and QQQ 0dte the measured median half-spread is 0.625% of
premium while `base` assumes 3% -- so the *base* case already sits near the measured p95, and
`severe` at 10% is roughly three times the measured p95. For IWM 0dte the measured p95 is
14.375%, so `severe` is, at the tail, optimistic. The three models do not sit at consistent
places in the measured distribution, which means "it failed under severe" carries a different
meaning per symbol. That is a finding about the evidence, not a correction to it.
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_PROFILE = Path(
    "/home/aarav/Aarav/cipher/runtime/data/execution_costs/spread_profile.json"
)

#: The labs bucket contracts as 0dte / front / weekly / swing; the measured profile buckets
#: them as 0dte / 1-7. `swing` has no measured counterpart and is reported as unmapped rather
#: than folded into 1-7, because a 30-day contract's spread is not a week's.
BUCKET_MAP: Mapping[str, str | None] = {
    "0dte": "0dte",
    "front": "1-7",
    "weekly": "1-7",
    "swing": None,
}

#: The models as the labs define them, mirrored here so this module can be run against a
#: profile without importing a 1,300-line lab. Kept in sync by
#: tests/test_execution_calibration.py, which imports the real tuple and asserts equality --
#: a copy that silently drifts would calibrate against numbers nothing uses.
ASSUMED_MODELS: Mapping[str, float] = {
    "base": 0.03,
    "worse": 0.05,
    "severe": 0.10,
}


class Verdict:
    HARSHER_THAN_P95 = "harsher than measured p95"
    BETWEEN_P75_AND_P95 = "between measured p75 and p95"
    BETWEEN_MEDIAN_AND_P75 = "between measured median and p75"
    AT_OR_BELOW_MEDIAN = "optimistic: at or below the measured median"
    NO_MEASUREMENT = "no measured cell for this symbol and bucket"


@dataclass(frozen=True)
class Calibration:
    symbol: str
    lab_bucket: str
    measured_bucket: str | None
    model: str
    assumed_pct_of_premium: float
    measured_median: float | None
    measured_p75: float | None
    measured_p95: float | None
    samples: int | None
    sufficient: bool | None
    verdict: str

    @property
    def ratio_to_median(self) -> float | None:
        """How many times the measured median the assumption is. None when unmeasured."""
        if not self.measured_median:
            return None
        return round(self.assumed_pct_of_premium / self.measured_median, 3)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "lab_bucket": self.lab_bucket,
            "measured_bucket": self.measured_bucket,
            "model": self.model,
            "assumed_pct_of_premium": self.assumed_pct_of_premium,
            "measured_median": self.measured_median,
            "measured_p75": self.measured_p75,
            "measured_p95": self.measured_p95,
            "ratio_to_median": self.ratio_to_median,
            "samples": self.samples,
            "sufficient": self.sufficient,
            "verdict": self.verdict,
        }


def load_profile(path: Path = DEFAULT_PROFILE) -> dict[str, Any] | None:
    """Read the profile, or None. Never falls back to a bundled default.

    `core.execution_cost.equity_half_spread_bps` refuses to auto-load for the same reason:
    a cost lookup whose answer depends on whether a file happens to exist is not something a
    research verdict can rest on.
    """
    try:
        loaded = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _verdict(assumed: float, median: float | None, p75: float | None, p95: float | None) -> str:
    if median is None:
        return Verdict.NO_MEASUREMENT
    if p95 is not None and assumed > p95:
        return Verdict.HARSHER_THAN_P95
    if p75 is not None and assumed > p75:
        return Verdict.BETWEEN_P75_AND_P95
    if assumed > median:
        return Verdict.BETWEEN_MEDIAN_AND_P75
    return Verdict.AT_OR_BELOW_MEDIAN


def calibrate(
    symbols: Sequence[str],
    lab_buckets: Sequence[str] = ("0dte",),
    *,
    profile: Mapping[str, Any] | None,
    models: Mapping[str, float] = ASSUMED_MODELS,
) -> list[Calibration]:
    """One row per symbol x bucket x model.

    `profile` is required and None means exactly that -- no profile, so every row reports
    NO_MEASUREMENT rather than silently comparing against nothing.
    """
    cells: Mapping[str, Any] = {}
    if profile:
        found = profile.get("option_half_spread_pct_of_premium")
        cells = found if isinstance(found, Mapping) else {}

    rows: list[Calibration] = []
    for symbol in symbols:
        for lab_bucket in lab_buckets:
            measured_bucket = BUCKET_MAP.get(lab_bucket)
            cell = cells.get(f"{symbol.upper()}|{measured_bucket}") if measured_bucket else None
            cell = cell if isinstance(cell, Mapping) else None
            for model, fraction in models.items():
                # fraction is a fraction of premium; the profile stores percent of premium.
                assumed_pct = round(fraction * 100.0, 4)
                median = float(cell["median"]) if cell and cell.get("median") is not None else None
                p75 = float(cell["p75"]) if cell and cell.get("p75") is not None else None
                p95 = float(cell["p95"]) if cell and cell.get("p95") is not None else None
                rows.append(
                    Calibration(
                        symbol=symbol.upper(),
                        lab_bucket=lab_bucket,
                        measured_bucket=measured_bucket,
                        model=model,
                        assumed_pct_of_premium=assumed_pct,
                        measured_median=median,
                        measured_p75=p75,
                        measured_p95=p95,
                        samples=int(cell["samples"]) if cell and cell.get("samples") is not None else None,
                        sufficient=bool(cell["sufficient"]) if cell and cell.get("sufficient") is not None else None,
                        verdict=_verdict(assumed_pct, median, p75, p95),
                    )
                )
    return rows


def report(
    symbols: Sequence[str],
    lab_buckets: Sequence[str] = ("0dte",),
    *,
    profile: Mapping[str, Any] | None,
) -> dict[str, Any]:
    rows = calibrate(symbols, lab_buckets, profile=profile)
    window = (profile or {}).get("capture_window") or {}

    measured = [r for r in rows if r.verdict != Verdict.NO_MEASUREMENT]
    harsher = [r for r in measured if r.verdict == Verdict.HARSHER_THAN_P95]
    optimistic = [r for r in measured if r.verdict == Verdict.AT_OR_BELOW_MEDIAN]

    return {
        "schema_version": 1,
        "question_answered": (
            "Is each lab's assumed execution cost optimistic or pessimistic against "
            "currently observable spreads?"
        ),
        "not_a_repricing": (
            "These percentiles come from a short recent capture window and cannot cost a "
            "study that spans months. Nothing here corrects a published return; it only says "
            "where the assumption sits in the measured distribution."
        ),
        "measured_window": {
            "first_event": window.get("first_event"),
            "last_event": window.get("last_event"),
            "distinct_days": window.get("distinct_days"),
        },
        "profile_caveat": (profile or {}).get("caveat"),
        "cells": len(rows),
        "measured_cells": len(measured),
        "unmeasured_cells": len(rows) - len(measured),
        "assumptions_harsher_than_p95": len(harsher),
        "assumptions_at_or_below_median": len(optimistic),
        "rows": [r.to_dict() for r in rows],
    }
