"""Forward test of Structural Fib: pre-register signals live, score them strictly later.

Research-only. Reads Alpaca SIP bars, writes append-only JSONL. It places no orders, holds no
position, and its output is a hit-rate table a human reads.

## Why forward-test a strategy the backtest rejected

Two reasons, and the second is the stronger one.

The backtest (`core/structural_fib_lab.py`) refuted all three published claims over
2025-12-02 → 2026-08-11, but it is **in-sample**: the same 173 sessions produced the finding
and would produce any re-test. A prospective test on sessions nobody has looked at is the only
reading that cannot be accused of fitting, and if the claimed rates are real it is where they
will show up.

More importantly, the backtest is stuck at evidence tier 4 for a reason no amount of
re-analysis fixes. Its cost basis is `not-applicable:price-level-claim` — honest, but it means
the study can never say what a *traded* version returns, because the measured option-spread
capture begins 2026-07-22 and the study window starts 2025-12-02. Signals recorded from today
forward fall inside the capture window, so each one can carry a **measured** option half-spread
taken at signal time. That is the only path from tier 4 to tier 1 for this strategy, and it
requires recording forward rather than re-reading the past.

## The pre-registration guarantee

A forward test is worthless if the outcome can leak into the signal. Three mechanics prevent
it here:

  1. `iter_triggers` in the lab is the *single* implementation of the entry geometry, shared
     with the backtest. A forward-versus-backtest difference therefore cannot be the geometry.
  2. `PARAMS` is frozen and hashed. The hash is written beside every signal, so a parameter
     changed mid-test is visible in the data rather than silently splicing two experiments.
  3. Signals go to `signals.jsonl` when they fire, carrying target and stop and nothing else.
     Outcomes go to a *separate* append-only `outcomes.jsonl`, written by a later pass, joined
     on `signal_id`. Neither file is ever rewritten, so a recorded signal cannot acquire its
     result at detection time and a scored outcome cannot be revised once written.

## What is recorded but deliberately not filtered on

The indicator this strategy is taught with also draws PMH/PML, PDH/PDL, PDM and PWH/PWL, and
the method's reasoning leans on them constantly ("the 2 level lined up with the previous day
low"). Whether that confluence carries information is a separate, untested claim. So the
distance from entry to the nearest session level in the target's direction is **recorded** with
every signal and **used to filter nothing**. That way the confluence question can be asked
later from data gathered without knowing the answer, instead of being built into the test now.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from core import session_levels, structural_fib_lab as lab
from core.structural_fib_lab import NY, UTC, Bar

DEFAULT_DIR = Path("/home/aarav/Aarav/cipher/runtime/data/structural_fib_forward")
SPREAD_PROFILE = Path("/home/aarav/Aarav/cipher/runtime/data/execution_costs/spread_profile.json")

#: Frozen at the start of the test. Changing any value starts a new experiment, which is why
#: the hash travels with every signal rather than living only in a config file.
PARAMS: dict[str, Any] = {
    "symbols": ["NVDA", "AAPL"],
    "range_filter_pct": lab.RANGE_FILTER_PCT,
    "overextension_frac": lab.OVEREXTENSION_FRAC,
    "continuation_cutoff_et": lab.CONTINUATION_CUTOFF.strftime("%H:%M"),
    "anchor_grace_bars": lab.ANCHOR_GRACE_BARS,
    "reversal_min_advance_r": lab.REVERSAL_MIN_ADVANCE_R,
    "entry_mode": "confirmed",
    "bar_minutes": 5,
    "ambiguous_bar": "scored as the stop",
    "anchor": "trailed session extreme, inclusive of the signal bar",
}

#: Backtest result the forward run is compared against, so a divergence is visible without
#: re-running 173 sessions. Source: runtime/data/structural_fib_faithful/report.json.
BACKTEST_TOUCH = {
    "continuation 0.5->1": 0.837,
    "continuation 1->2": 0.572,
    "reversal 1->2": 0.440,
    "reversal 2->3": 0.451,
}


def params_hash(params: dict[str, Any] | None = None) -> str:
    payload = json.dumps(params if params is not None else PARAMS, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


# ───────────────────────────────────────────────────────────────── bars

def to_bars(raw: Iterable[dict[str, Any]]) -> list[Bar]:
    """Alpaca REST bars -> the lab's Bar, in exchange local time."""
    out: list[Bar] = []
    for row in raw or ():
        stamp = row.get("t")
        if not stamp:
            continue
        try:
            moment = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        except ValueError:
            continue
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        out.append(Bar(
            moment.astimezone(NY),
            float(row["o"]), float(row["h"]), float(row["l"]), float(row["c"]),
            float(row.get("v") or 0.0),
        ))
    return sorted(out, key=lambda b: b.t)


def drop_forming(bars: Sequence[Bar], *, now: datetime | None = None,
                 minutes: int = 5) -> list[Bar]:
    """Discard a trailing bar that has not finished forming.

    The vendor returns the in-progress bar alongside the closed ones, and its close moves
    until the interval ends. Evaluating it records **phantom signals**: on 2026-08-13 an AAPL
    short fired at 10:21 off a 10:20 bar whose close was 304.44 at the time and 304.62 once
    complete — a signal that does not exist on closed data and could never have been traded.

    This is also the method as taught: entry is a *body close* past the level, and a bar that
    has not closed has no body yet. So the guard is fidelity, not just hygiene.
    """
    if not bars:
        return []
    moment = now or datetime.now(UTC)
    cutoff = moment.astimezone(NY)
    out = list(bars)
    while out and out[-1].t + timedelta(minutes=minutes) > cutoff:
        out.pop()
    return out


def fetch_recent(symbol: str, *, days: int = 12, now: datetime | None = None) -> list[Bar]:
    """Enough history for the fallback-leg rule, which needs prior qualifying sessions.

    Fetched at 5-minute resolution directly rather than resampled from 1-minute: the vendor
    aggregates on the same exchange-local grid this strategy uses. The in-progress bar is
    dropped here, at the boundary where live data enters, so no caller can evaluate it.
    """
    from core.data_fetcher import fetch_alpaca_bars, load_env

    end = datetime.now(UTC).replace(tzinfo=None)
    raw = fetch_alpaca_bars(symbol, end - timedelta(days=days), end,
                            timeframe="5Min", creds=load_env())
    return drop_forming(to_bars(raw), now=now,
                        minutes=int(PARAMS.get("bar_minutes") or 5))


def measured_option_cost(symbol: str, *, profile_path: Path = SPREAD_PROFILE) -> dict[str, Any]:
    """The measured option half-spread for this symbol, or an explicit absence.

    Recorded at signal time so the forward test can reach a `measured:` cost basis. Returns
    provenance alongside the number, because a cost that silently falls back to an assumption
    is the failure this whole programme is built to avoid.
    """
    try:
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"basis": "assumed:no-profile", "half_spread_pct_of_premium": None}
    buckets = profile.get("option_half_spread_pct_of_premium") or {}
    out: dict[str, Any] = {"basis": "assumed:symbol-not-captured",
                           "half_spread_pct_of_premium": None}
    for bucket in ("0dte", "1-7"):
        row = buckets.get(f"{symbol}|{bucket}")
        if isinstance(row, dict) and row.get("median") is not None:
            out = {
                "basis": "measured:median",
                "bucket": bucket,
                "half_spread_pct_of_premium": row.get("median"),
                "p95_pct_of_premium": row.get("p95"),
                "samples": row.get("samples") or row.get("n"),
                "capture_window": (profile.get("capture_window") or {}).get("last_event"),
            }
            break
    return out


def nearest_session_level(levels: Sequence[dict[str, Any]], entry: float,
                          direction: str) -> dict[str, Any] | None:
    """The closest drawn level in the direction the trade is trying to go.

    Recorded, never filtered on -- see the module docstring.
    """
    ahead = []
    for level in levels or ():
        price = level.get("price")
        if price is None:
            continue
        if (direction == "long" and price > entry) or (direction == "short" and price < entry):
            ahead.append((abs(price - entry), level))
    if not ahead:
        return None
    distance, level = min(ahead, key=lambda pair: pair[0])
    return {
        "kind": level.get("kind"),
        "label": level.get("label"),
        "price": level.get("price"),
        "distance_pct": distance / entry * 100.0 if entry else None,
    }


# ───────────────────────────────────────────────────────────────── detection

@dataclass(slots=True)
class PendingSignal:
    signal_id: str
    params_hash: str
    detected_at: str
    bars_as_of: str
    symbol: str
    day: str
    setup: str
    leg: str
    direction: str
    regime: str
    leg_source: str
    pm_range_pct: float | None
    pm_high: float | None
    pm_low: float | None
    unit: float
    anchor: float
    signal_time_et: str
    level: float
    entry_price: float
    target: float
    stop: float
    reward_pct: float
    risk_pct: float
    option_cost: dict[str, Any]
    nearest_level_toward_target: dict[str, Any] | None
    session_levels: list[dict[str, Any]]


def detect(symbol: str, bars: Sequence[Bar], *, today: date | None = None,
           params: dict[str, Any] | None = None) -> list[PendingSignal]:
    """Signals fired so far in today's session, with no outcome attached."""
    active = params or PARAMS
    sessions = lab.split_sessions(bars)
    if not sessions:
        return []
    day = today or max(sessions)
    if day not in sessions:
        return []
    contexts = {c.day: c for c in lab.classify_days(sessions)}
    ctx = contexts.get(day)
    if ctx is None or ctx.unit is None:
        return []
    reg = sessions[day]["reg"]
    if not reg:
        return []

    triggers = lab.iter_triggers(
        reg, ctx.unit,
        min_advance_r=float(active["reversal_min_advance_r"]),
        entry_mode=str(active["entry_mode"]),
    )
    if not triggers:
        return []

    # Session levels from the same indicator the method is taught with. Daily bars are not
    # fetched here, so prior-day/week extents come from the minute history and carry the
    # module's own coverage warning rather than being trusted silently.
    as_dicts = [{"t": b.t.isoformat(), "high": b.h, "low": b.l} for b in bars]
    computed = session_levels.compute(as_dicts, as_of=day.isoformat())
    levels = computed.get("levels") or []
    cost = measured_option_cost(symbol)
    now = datetime.now(UTC).isoformat()
    bars_as_of = reg[-1].t.isoformat()

    out: list[PendingSignal] = []
    for trigger in triggers:
        entry = trigger.entry_price
        out.append(PendingSignal(
            signal_id=f"{symbol}|{day.isoformat()}|{trigger.setup}|{trigger.leg}|{trigger.direction}",
            params_hash=params_hash(active),
            detected_at=now,
            bars_as_of=bars_as_of,
            symbol=symbol,
            day=day.isoformat(),
            setup=trigger.setup,
            leg=trigger.leg,
            direction=trigger.direction,
            regime=ctx.regime,
            leg_source=ctx.leg_source,
            pm_range_pct=ctx.pm_range_pct,
            pm_high=ctx.pm_high,
            pm_low=ctx.pm_low,
            unit=ctx.unit,
            anchor=trigger.anchor,
            signal_time_et=trigger.at.strftime("%Y-%m-%d %H:%M"),
            level=trigger.level,
            entry_price=entry,
            target=trigger.target,
            stop=trigger.stop,
            reward_pct=abs(trigger.target - entry) / entry * 100.0 if entry else 0.0,
            risk_pct=abs(trigger.stop - entry) / entry * 100.0 if entry else 0.0,
            option_cost=cost,
            nearest_level_toward_target=nearest_session_level(levels, entry, trigger.direction),
            session_levels=levels,
        ))
    return out


# ───────────────────────────────────────────────────────────────── storage

def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue  # a torn final line must not destroy the record
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _append_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    rows = list(rows)
    if not rows:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, sort_keys=True) + "\n")
        stream.flush()
    return len(rows)


def record(pending: Sequence[PendingSignal], *, directory: Path = DEFAULT_DIR) -> dict[str, Any]:
    """Append signals not already on file. First detection wins, always.

    The loop re-scans the whole session every few minutes, so it re-detects signals it has
    already seen. Keeping the first record rather than the newest is what makes this a
    pre-registration: a later bar revision cannot retroactively improve a recorded entry.
    """
    path = directory / "signals.jsonl"
    known = {row.get("signal_id") for row in _read_jsonl(path)}
    fresh = [asdict(p) for p in pending if p.signal_id not in known]
    written = _append_jsonl(path, fresh)

    manifest = directory / "params.json"
    if not manifest.is_file():
        directory.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps({
            "params": PARAMS,
            "params_hash": params_hash(),
            "started_at": datetime.now(UTC).isoformat(),
            "backtest_touch_rates": BACKTEST_TOUCH,
            "claimed_rates": lab.CLAIMED,
            "note": (
                "Pre-registered. Signals are appended when they fire and never rewritten; "
                "outcomes live in outcomes.jsonl and are joined on signal_id."
            ),
        }, indent=2), encoding="utf-8")

    return {"detected": len(pending), "newly_recorded": written,
            "already_known": len(pending) - written}


def score(*, directory: Path = DEFAULT_DIR, now: datetime | None = None,
          fetch=fetch_recent) -> dict[str, Any]:
    """Resolve signals whose session has closed. Never re-scores an outcome already written."""
    moment = now or datetime.now(UTC)
    signals = _read_jsonl(directory / "signals.jsonl")
    outcomes_path = directory / "outcomes.jsonl"
    scored = {row.get("signal_id") for row in _read_jsonl(outcomes_path)}

    today_et = moment.astimezone(NY).date()
    session_over = moment.astimezone(NY).time() >= dtime(16, 0)
    pending = [s for s in signals if s.get("signal_id") not in scored]
    # A session still trading cannot be scored: the target may yet be reached.
    ready = [s for s in pending
             if s.get("day") and (date.fromisoformat(s["day"]) < today_et
                                  or (date.fromisoformat(s["day"]) == today_et and session_over))]
    if not ready:
        return {"unscored": len(pending), "scored_now": 0,
                "waiting_on_open_sessions": len(pending)}

    by_symbol: dict[str, dict[date, list[Bar]]] = {}
    for symbol in sorted({s["symbol"] for s in ready}):
        by_symbol[symbol] = {
            day: parts["reg"]
            for day, parts in lab.split_sessions(fetch(symbol)).items()
        }

    rows: list[dict[str, Any]] = []
    for signal in ready:
        reg = by_symbol.get(signal["symbol"], {}).get(date.fromisoformat(signal["day"]), [])
        signal_at = signal.get("signal_time_et", "")[-5:]
        after = [b for b in reg if b.t.strftime("%H:%M") > signal_at]
        if not after:
            # Out of the fetch window, or no bar after the signal. Recorded as unresolvable
            # rather than dropped, so the count of signals always reconciles.
            rows.append({
                "signal_id": signal["signal_id"], "scored_at": moment.isoformat(),
                "resolution": "unresolvable",
                "reason": "no bars after the signal within the fetch window",
            })
            continue
        entry = float(signal["entry_price"])
        race, ret = lab._race(entry, float(signal["target"]), float(signal["stop"]),
                              after, signal["direction"])
        rows.append({
            "signal_id": signal["signal_id"],
            "scored_at": moment.isoformat(),
            "resolution": "scored",
            "touched": lab._touched(float(signal["target"]), after, signal["direction"]),
            "race": race,
            "return_pct": ret,
            "bars_after_signal": len(after),
        })
    written = _append_jsonl(outcomes_path, rows)
    return {"unscored": len(pending), "scored_now": written,
            "waiting_on_open_sessions": len(pending) - len(ready)}


# ───────────────────────────────────────────────────────────────── reporting

def report(*, directory: Path = DEFAULT_DIR) -> dict[str, Any]:
    """Forward result so far, against the backtest and the published claim."""
    signals = {s["signal_id"]: s for s in _read_jsonl(directory / "signals.jsonl")
               if s.get("signal_id")}
    outcomes = {o["signal_id"]: o for o in _read_jsonl(directory / "outcomes.jsonl")
                if o.get("signal_id")}

    groups: dict[str, list[tuple[dict, dict]]] = {}
    for signal_id, signal in signals.items():
        outcome = outcomes.get(signal_id)
        if not outcome or outcome.get("resolution") != "scored":
            continue
        groups.setdefault(f"{signal['setup']} {signal['leg']}", []).append((signal, outcome))

    legs: dict[str, Any] = {}
    for key, pairs in sorted(groups.items()):
        n = len(pairs)
        touched = sum(1 for _s, o in pairs if o.get("touched"))
        won = sum(1 for _s, o in pairs if o.get("race") == "target")
        rets = sorted(float(o.get("return_pct") or 0.0) for _s, o in pairs)
        lo, hi = lab.wilson(touched, n)
        claimed = lab.CLAIMED.get(key)
        backtest = BACKTEST_TOUCH.get(key)
        legs[key] = {
            "n": n,
            "touch_rate": touched / n,
            "touch_ci95": [lo, hi],
            "claimed": claimed,
            "backtest_touch_rate": backtest,
            "claim_excluded": None if claimed is None else not (lo <= claimed <= hi),
            "backtest_excluded": None if backtest is None else not (lo <= backtest <= hi),
            "race_win_rate": won / n,
            "avg_return_pct": sum(rets) / n,
            "median_return_pct": rets[n // 2],
            "underpowered": n < lab.MIN_REPORT_N,
        }

    days = sorted({s["day"] for s in signals.values() if s.get("day")})
    hashes = sorted({s.get("params_hash") for s in signals.values() if s.get("params_hash")})
    costs = {s["symbol"]: (s.get("option_cost") or {}).get("basis")
             for s in signals.values() if s.get("symbol")}
    return {
        "study": "structural_fib_forward",
        "generated_at": datetime.now(UTC).isoformat(),
        "params_hash": params_hash(),
        "params_hashes_in_data": hashes,
        "params_drift": len(hashes) > 1,
        "sessions_recorded": len(days),
        "first_session": days[0] if days else None,
        "last_session": days[-1] if days else None,
        "signals_recorded": len(signals),
        "signals_scored": sum(1 for o in outcomes.values()
                              if o.get("resolution") == "scored"),
        "signals_awaiting_outcome": len(signals) - len(outcomes),
        "cost_basis_by_symbol": costs,
        "legs": legs,
        "limitations": [
            "A forward test is only as long as it has run. Distinguishing 98% from the "
            "backtest's 44% needs very few observations, but estimating the true rate to "
            "+/-10 points needs roughly 30 per leg.",
            "Signals within one session are not independent; a session-clustered interval "
            "would be wider than the Wilson interval shown.",
            "Option cost is recorded per signal but not applied to the return, which is the "
            "underlying price move. Applying it needs a contract choice this test does not "
            "make.",
            "Bars can be revised by the vendor after the fact. The first detection is kept, "
            "so a revision cannot improve a recorded entry, but it can mean a recorded "
            "signal would not fire on the final data.",
        ],
    }
