"""Forward test: the pre-registration guarantees, which are the only thing making it evidence.

A forward test whose signals can acquire their outcome at detection time, or whose records can
be rewritten once the result is known, is not a forward test. These tests exist to make that
impossible rather than merely intended.
"""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from core import structural_fib_forward as fwd
from core import structural_fib_lab as lab

NY = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


def bar(hhmm: str, o: float, h: float, l: float, c: float, day: str = "2026-03-02") -> lab.Bar:
    y, m, d = (int(x) for x in day.split("-"))
    hh, mm = (int(x) for x in hhmm.split(":"))
    return lab.Bar(datetime(y, m, d, hh, mm, tzinfo=NY), o, h, l, c, 1000.0)


def _session(day: str = "2026-03-02") -> list[lab.Bar]:
    """A pre-market that qualifies, then a rally that clears the long 0.5 and 1 levels."""
    pre = [bar("09:00", 100.0, 100.5, 99.5, 100.0, day),
           bar("09:05", 100.0, 100.5, 99.5, 100.0, day)]  # PMH 100.5 / PML 99.5, R = 1.0
    reg = [bar("09:30", 99.6, 99.7, 99.5, 99.6, day),
           bar("09:35", 99.6, 99.7, 99.5, 99.6, day),
           bar("09:40", 100.1, 100.3, 100.05, 100.2, day),   # clears 0.5 level (100.0)
           bar("09:45", 100.7, 100.9, 100.6, 100.8, day)]    # clears 1 level (100.5)
    reg += [bar(f"1{h}:{m:02d}", 100.8, 100.9, 100.7, 100.8, day)
            for h, m in ((0, 0), (0, 5), (0, 10), (0, 15), (0, 20), (0, 25))]
    return pre + reg


# ───────────────────────────────────────────── the geometry is shared, not duplicated

def test_the_forward_detector_uses_the_same_triggers_as_the_backtest() -> None:
    """If these ever diverge, a forward-versus-backtest gap could be the code rather than the
    market, and the whole comparison stops meaning anything."""
    bars = _session()
    pending = fwd.detect("T", bars, today=date(2026, 3, 2))
    sessions = lab.split_sessions(bars)
    ctx = {c.day: c for c in lab.classify_days(sessions)}[date(2026, 3, 2)]
    triggers = lab.iter_triggers(sessions[date(2026, 3, 2)]["reg"], ctx.unit,
                                min_advance_r=fwd.PARAMS["reversal_min_advance_r"],
                                entry_mode=fwd.PARAMS["entry_mode"])
    assert [(p.setup, p.leg, p.direction) for p in pending] == \
           [(t.setup, t.leg, t.direction) for t in triggers]
    for p, t in zip(pending, triggers):
        assert p.entry_price == pytest.approx(t.entry_price)
        assert p.target == pytest.approx(t.target)
        assert p.stop == pytest.approx(t.stop)


def test_a_pending_signal_carries_no_outcome_field_at_all() -> None:
    """Not "carries an empty outcome" — carries none. A field that exists can be filled in by
    the same pass that created it, which is exactly the leak this design forbids."""
    (first, *_rest) = fwd.detect("T", _session(), today=date(2026, 3, 2))
    fields = set(vars(first)) if not hasattr(first, "__slots__") else set(first.__slots__)
    for forbidden in ("touched", "race", "return_pct", "outcome", "resolution", "won"):
        assert forbidden not in fields, f"pending signal exposes {forbidden}"


def test_no_signals_when_the_premarket_range_disqualifies_and_there_is_no_fallback() -> None:
    wide = [bar("09:00", 100.0, 130.0, 100.0, 120.0), bar("09:30", 120.0, 121.0, 119.0, 120.0)]
    assert fwd.detect("T", wide, today=date(2026, 3, 2)) == []


# ───────────────────────────────────────────── first detection wins

def test_re_running_the_same_session_records_each_signal_once(tmp_path: Path) -> None:
    """The loop re-scans every five minutes, so it re-detects what it has already seen."""
    pending = fwd.detect("T", _session(), today=date(2026, 3, 2))
    assert pending
    first = fwd.record(pending, directory=tmp_path)
    second = fwd.record(pending, directory=tmp_path)
    assert first["newly_recorded"] == len(pending)
    assert second["newly_recorded"] == 0
    assert second["already_known"] == len(pending)
    lines = (tmp_path / "signals.jsonl").read_text().strip().splitlines()
    assert len(lines) == len(pending)


def test_a_later_bar_revision_cannot_improve_an_already_recorded_entry(tmp_path: Path) -> None:
    """A vendor revision that would have produced a better fill must not rewrite history."""
    pending = fwd.detect("T", _session(), today=date(2026, 3, 2))
    fwd.record(pending, directory=tmp_path)
    original = json.loads((tmp_path / "signals.jsonl").read_text().strip().splitlines()[0])

    flattered = [replace(p, entry_price=p.entry_price * 0.5, target=p.target * 2)
                 for p in pending]
    fwd.record(flattered, directory=tmp_path)
    after = json.loads((tmp_path / "signals.jsonl").read_text().strip().splitlines()[0])
    assert after["entry_price"] == original["entry_price"]
    assert after["target"] == original["target"]


def test_recording_writes_a_params_manifest_once(tmp_path: Path) -> None:
    pending = fwd.detect("T", _session(), today=date(2026, 3, 2))
    fwd.record(pending, directory=tmp_path)
    manifest = json.loads((tmp_path / "params.json").read_text())
    assert manifest["params_hash"] == fwd.params_hash()
    assert manifest["claimed_rates"]["reversal 1->2"] == 0.98
    started = manifest["started_at"]
    fwd.record(pending, directory=tmp_path)
    assert json.loads((tmp_path / "params.json").read_text())["started_at"] == started


def test_the_params_hash_changes_when_any_parameter_changes() -> None:
    altered = dict(fwd.PARAMS, reversal_min_advance_r=0.0)
    assert fwd.params_hash(altered) != fwd.params_hash()


# ───────────────────────────────────────────── scoring cannot run early or twice

def _recorded(tmp_path: Path, day: str) -> None:
    fwd.record(fwd.detect("T", _session(day), today=date.fromisoformat(day)),
               directory=tmp_path)


def test_an_open_session_is_never_scored(tmp_path: Path) -> None:
    """The target may yet be reached. Scoring mid-session would resolve every signal as a
    stop or a close and permanently understate the strategy."""
    _recorded(tmp_path, "2026-03-02")
    midday = datetime(2026, 3, 2, 12, 0, tzinfo=NY).astimezone(UTC)
    out = fwd.score(directory=tmp_path, now=midday, fetch=lambda _s: _session("2026-03-02"))
    assert out["scored_now"] == 0
    assert out["waiting_on_open_sessions"] == out["unscored"] > 0
    assert not (tmp_path / "outcomes.jsonl").exists()


def test_a_closed_session_is_scored_into_a_separate_append_only_file(tmp_path: Path) -> None:
    _recorded(tmp_path, "2026-03-02")
    after_close = datetime(2026, 3, 2, 16, 30, tzinfo=NY).astimezone(UTC)
    out = fwd.score(directory=tmp_path, now=after_close,
                    fetch=lambda _s: _session("2026-03-02"))
    assert out["scored_now"] > 0
    rows = [json.loads(x) for x in
            (tmp_path / "outcomes.jsonl").read_text().strip().splitlines()]
    assert all(r["resolution"] in ("scored", "unresolvable") for r in rows)
    # The signal file is untouched by scoring.
    signals = (tmp_path / "signals.jsonl").read_text()
    assert "touched" not in signals and "race" not in signals


def test_an_outcome_is_never_re_scored(tmp_path: Path) -> None:
    """Re-scoring is how a result quietly becomes the best of several attempts."""
    _recorded(tmp_path, "2026-03-02")
    after_close = datetime(2026, 3, 2, 16, 30, tzinfo=NY).astimezone(UTC)
    fetch = lambda _s: _session("2026-03-02")  # noqa: E731
    first = fwd.score(directory=tmp_path, now=after_close, fetch=fetch)
    again = fwd.score(directory=tmp_path, now=after_close, fetch=fetch)
    assert first["scored_now"] > 0
    assert again["scored_now"] == 0
    lines = (tmp_path / "outcomes.jsonl").read_text().strip().splitlines()
    assert len(lines) == first["scored_now"]


def test_a_signal_with_no_bars_after_it_is_recorded_unresolvable_not_dropped(
    tmp_path: Path,
) -> None:
    """The signal count must always reconcile, or a silently dropped signal becomes a
    selection effect."""
    _recorded(tmp_path, "2026-03-02")
    after_close = datetime(2026, 3, 2, 16, 30, tzinfo=NY).astimezone(UTC)
    out = fwd.score(directory=tmp_path, now=after_close, fetch=lambda _s: [])
    rows = [json.loads(x) for x in
            (tmp_path / "outcomes.jsonl").read_text().strip().splitlines()]
    assert out["scored_now"] == len(rows)
    assert all(r["resolution"] == "unresolvable" for r in rows)


# ───────────────────────────────────────────── the report

def test_the_report_is_honest_about_an_empty_record(tmp_path: Path) -> None:
    summary = fwd.report(directory=tmp_path)
    assert summary["signals_recorded"] == 0
    assert summary["legs"] == {}
    assert summary["sessions_recorded"] == 0


def test_unscored_signals_are_counted_but_never_rated(tmp_path: Path) -> None:
    _recorded(tmp_path, "2026-03-02")
    summary = fwd.report(directory=tmp_path)
    assert summary["signals_recorded"] > 0
    assert summary["signals_awaiting_outcome"] == summary["signals_recorded"]
    assert summary["legs"] == {}, "a rate computed before any outcome exists is fabrication"


def test_the_report_compares_against_both_the_claim_and_the_backtest(tmp_path: Path) -> None:
    _recorded(tmp_path, "2026-03-02")
    after = datetime(2026, 3, 2, 16, 30, tzinfo=NY).astimezone(UTC)
    fwd.score(directory=tmp_path, now=after, fetch=lambda _s: _session("2026-03-02"))
    legs = fwd.report(directory=tmp_path)["legs"]
    assert legs, "a closed session with signals should produce at least one rated leg"
    for key, row in legs.items():
        assert row["backtest_touch_rate"] == fwd.BACKTEST_TOUCH.get(key)
        assert row["claimed"] == lab.CLAIMED.get(key)
        assert row["underpowered"] is True, "one session can never be a result"


def test_parameter_drift_is_surfaced_rather_than_pooled(tmp_path: Path) -> None:
    """Two parameter sets in one record are two experiments. Pooling them silently would
    produce a rate describing neither."""
    pending = fwd.detect("T", _session(), today=date(2026, 3, 2))
    fwd.record(pending, directory=tmp_path)
    drifted = [replace(p, params_hash="deadbeef0000",
                       signal_id=p.signal_id + "|v2") for p in pending]
    fwd.record(drifted, directory=tmp_path)
    summary = fwd.report(directory=tmp_path)
    assert summary["params_drift"] is True
    assert len(summary["params_hashes_in_data"]) == 2


# ───────────────────────────────────────────── recorded context

def test_measured_option_cost_is_read_with_its_provenance(tmp_path: Path) -> None:
    profile = tmp_path / "p.json"
    profile.write_text(json.dumps({
        "capture_window": {"last_event": "2026-08-12T19:59:59+00:00"},
        "option_half_spread_pct_of_premium": {
            "NVDA|0dte": {"median": 2.375, "p95": 19.875, "samples": 1981960},
        },
    }))
    cost = fwd.measured_option_cost("NVDA", profile_path=profile)
    assert cost["basis"] == "measured:median"
    assert cost["half_spread_pct_of_premium"] == 2.375
    assert cost["bucket"] == "0dte"


def test_an_uncaptured_symbol_says_so_instead_of_assuming(tmp_path: Path) -> None:
    profile = tmp_path / "p.json"
    profile.write_text(json.dumps({"option_half_spread_pct_of_premium": {}}))
    assert fwd.measured_option_cost("ZZZZ", profile_path=profile)["basis"] == \
        "assumed:symbol-not-captured"


def test_a_missing_profile_is_reported_not_silently_defaulted(tmp_path: Path) -> None:
    cost = fwd.measured_option_cost("NVDA", profile_path=tmp_path / "absent.json")
    assert cost["basis"] == "assumed:no-profile"
    assert cost["half_spread_pct_of_premium"] is None


def test_the_nearest_level_toward_the_target_is_recorded_with_its_distance() -> None:
    levels = [{"kind": "prev_day_high", "label": "PDH", "price": 101.0},
              {"kind": "prev_week_high", "label": "PWH", "price": 105.0},
              {"kind": "prev_day_low", "label": "PDL", "price": 98.0}]
    near = fwd.nearest_session_level(levels, 100.0, "long")
    assert near["label"] == "PDH"
    assert near["distance_pct"] == pytest.approx(1.0)
    # Direction matters: a short looks the other way.
    assert fwd.nearest_session_level(levels, 100.0, "short")["label"] == "PDL"


def test_no_level_ahead_records_absence_rather_than_a_zero() -> None:
    assert fwd.nearest_session_level([{"kind": "x", "price": 99.0}], 100.0, "long") is None
    assert fwd.nearest_session_level([], 100.0, "long") is None


def test_a_torn_final_line_does_not_destroy_the_record(tmp_path: Path) -> None:
    """An append interrupted by a crash must cost one signal, not the whole experiment."""
    path = tmp_path / "signals.jsonl"
    path.write_text('{"signal_id": "a", "day": "2026-03-02", "symbol": "T",'
                    ' "setup": "continuation", "leg": "0.5->1"}\n{"signal_id": "b"')
    assert len(fwd._read_jsonl(path)) == 1
    assert fwd.report(directory=tmp_path)["signals_recorded"] == 1


# ───────────────────────────────────────────── only closed bars may be evaluated

def test_a_bar_still_forming_is_dropped() -> None:
    """The vendor returns the in-progress bar with the closed ones. Evaluating it recorded a
    phantom AAPL short on 2026-08-13: close 304.44 while forming, 304.62 once complete."""
    bars = [bar("10:10", 1, 1, 1, 1), bar("10:15", 1, 1, 1, 1), bar("10:20", 1, 1, 1, 1)]
    # 10:23 ET — the 10:20 bar does not close until 10:25.
    at_1023 = datetime(2026, 3, 2, 10, 23, tzinfo=NY)
    kept = fwd.drop_forming(bars, now=at_1023)
    assert [b.t.strftime("%H:%M") for b in kept] == ["10:10", "10:15"]


def test_a_bar_that_has_just_closed_is_kept() -> None:
    bars = [bar("10:15", 1, 1, 1, 1), bar("10:20", 1, 1, 1, 1)]
    at_1025 = datetime(2026, 3, 2, 10, 25, tzinfo=NY)
    assert len(fwd.drop_forming(bars, now=at_1025)) == 2


def test_several_trailing_incomplete_bars_are_all_dropped() -> None:
    """A stale clock or a slow pass can leave more than one bar unclosed."""
    bars = [bar("10:00", 1, 1, 1, 1), bar("10:05", 1, 1, 1, 1), bar("10:10", 1, 1, 1, 1)]
    at_1003 = datetime(2026, 3, 2, 10, 3, tzinfo=NY)
    assert [b.t.strftime("%H:%M") for b in fwd.drop_forming(bars, now=at_1003)] == []


def test_dropping_forming_bars_from_an_empty_list_is_safe() -> None:
    assert fwd.drop_forming([], now=datetime(2026, 3, 2, 10, 0, tzinfo=NY)) == []


def test_the_phantom_signal_does_not_fire_once_the_bar_closes() -> None:
    """End to end: the same session detected mid-bar and after the close must not differ."""
    complete = _session()
    partial = complete + [bar("10:30", 100.8, 100.9, 100.7, 99.0)]  # a wild forming bar
    at_1032 = datetime(2026, 3, 2, 10, 32, tzinfo=NY)
    guarded = fwd.drop_forming(partial, now=at_1032)
    assert [(p.setup, p.leg, p.direction) for p in
            fwd.detect("T", guarded, today=date(2026, 3, 2))] == \
           [(p.setup, p.leg, p.direction) for p in
            fwd.detect("T", complete, today=date(2026, 3, 2))]
