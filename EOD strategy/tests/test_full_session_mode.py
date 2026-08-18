"""Full Session mode has to actually widen the signal window.

`PINE_DEFAULTS` has always offered `mode: "Full Session"` alongside `"EOD Focus"`, and the
detector's internal `gate` honoured it — the CLPS flags fire all day. But `BarState` published
only `in_window`, which is `window_ok`: the end-of-day arming window. The runner filtered
candidates on `in_window`, so under Full Session the detector produced all-day signals and the
runner discarded every one outside the final `arm_minutes`. The mode was inert, and silently:
no error, just an identical trade list.

`signal_gate` is the mode-aware flag, and these tests pin the distinction — including that
`in_window` keeps its narrow meaning, since conflating them again is the regression.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (str(ROOT), str(ROOT / "core")):
    if path not in sys.path:
        sys.path.insert(0, path)

from core import obsidian_eod  # noqa: E402
from scripts.run_obsidian_pine_ytd import PINE_DEFAULTS  # noqa: E402

UTC = timezone.utc


def _session_bars(day: str = "2026-04-01", count: int = 390) -> list[dict]:
    """One full regular session of 1-minute bars, 09:30–15:59 ET, as UTC timestamps.

April is inside US DST (ET = UTC-4), so 09:30 ET is 13:30Z. The date matters: on an EST
    date 13:30Z is 08:30 ET and the 15:30-15:59 arming window never appears in the session at
    all, which makes every gate assertion below vacuously false.
    """
    start = datetime.fromisoformat(f"{day}T13:30:00+00:00").astimezone(UTC)
    bars = []
    price = 100.0
    for index in range(count):
        # A slow oscillation: enough movement to produce runs and collapses.
        price += 0.05 if (index // 17) % 2 == 0 else -0.05
        stamp = (start + timedelta(minutes=index)).isoformat().replace("+00:00", "Z")
        bars.append({
            "time": stamp,
            "timestamp": stamp,
            "open": price,
            "high": price + 0.08,
            "low": price - 0.08,
            "close": price,
            "volume": 10_000.0,
        })
    return bars


def _minutes_from_open(state_index: int) -> int:
    return state_index  # one bar per minute, starting at 09:30


def test_eod_focus_gates_only_the_final_arm_window():
    bars = _session_bars()
    params = {**PINE_DEFAULTS, "mode": "EOD Focus", "arm_minutes": 30}
    states, _summary = obsidian_eod.compute(bars, params)

    gated = [i for i, s in enumerate(states) if s.signal_gate]
    assert gated, "some bars must be gated open"
    # 09:30 + 360 minutes = 15:30, so only the last 30 bars qualify.
    assert min(gated) >= 360, f"earliest gated bar was minute {min(gated)}, expected >= 360"
    # Under EOD Focus the two flags agree, which is why the bug was invisible here.
    assert all(s.signal_gate == s.in_window for s in states)


def test_full_session_gates_the_whole_day_while_in_window_stays_narrow():
    bars = _session_bars()
    params = {**PINE_DEFAULTS, "mode": "Full Session", "arm_minutes": 30}
    states, _summary = obsidian_eod.compute(bars, params)

    gated = [i for i, s in enumerate(states) if s.signal_gate]
    assert min(gated) == 0, "Full Session must admit the opening bar"
    assert len(gated) == len(states), "Full Session must admit every RTH bar"

    # in_window keeps meaning "end-of-day arming window" regardless of mode. If a future change
    # makes these identical again, the runner's filter silently narrows Full Session once more.
    narrow = [i for i, s in enumerate(states) if s.in_window]
    assert min(narrow) >= 360
    assert len(narrow) < len(gated)


def test_full_session_produces_signals_outside_the_eod_window():
    """The end-to-end property, on real bars.

    A synthetic price path is not used here: the CLPS classifier needs a genuine thrust
    followed by a collapse, and a mechanical sawtooth produces neither, so a synthetic version
    of this test passes or fails on the realism of the fixture rather than on the gate. The
    archive is local, so this reads it and skips if it is absent.
    """
    import pytest

    from core.equity_history_download import EquityBarStore
    from scripts.run_obsidian_pine_ytd import _candidate_indices, _et, _rth, load_rows

    db = ROOT / "data" / "historical_equities" / "obsidian_pine_ytd_2026" / "equity_bars.sqlite"
    if not db.exists():
        pytest.skip("bar archive not present")

    store = EquityBarStore(db.parent, db_path=db)
    bars = _rth(load_rows(store, "SPY", "1Min"))
    if not bars:
        pytest.skip("no SPY bars in the archive")

    def candidates(mode):
        states, _ = obsidian_eod.compute(bars, {**PINE_DEFAULTS, "mode": mode})
        return _candidate_indices(
            bars,
            states,
            evaluation_start=None,
            strategy_mode="CLPS Only",
            entry_delay=1,
            min_signal_lead=2,
            rls_lookback=6,
            rls_relation="Any",
        )

    def before_1530(rows):
        out = []
        for index, _kind, _direction in rows:
            local = _et(str(bars[index]["time"]))
            if local.hour * 60 + local.minute < 15 * 60 + 30:
                out.append(index)
        return out

    eod = candidates("EOD Focus")
    full = candidates("Full Session")

    # EOD Focus must stay inside the arming window; that is the control for this comparison.
    assert before_1530(eod) == [], "EOD Focus must not signal before 15:30"

    # The regression: before `signal_gate` these two lists were identical, because the runner
    # filtered on `in_window`. Measured on the current archive: 195 vs 1938 candidates, of
    # which 1743 fall before 15:30.
    assert len(full) > len(eod) * 2, (
        f"Full Session found {len(full)} candidates against EOD Focus's {len(eod)}; "
        "comparable counts mean the mode is being ignored again"
    )
    assert len(before_1530(full)) > 0, "Full Session must signal outside the arming window"
