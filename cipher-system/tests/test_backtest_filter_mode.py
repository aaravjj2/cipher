"""Filter-mode evaluation: does a signal separate trades you were taking anyway?

Every earlier measurement asked the hardest question — can the signal alone beat
random entry on timing, direction and selection at once? Three strategies failed
it. But that failure cannot distinguish "carries no information" from "carries
information that is not an entry trigger", and those call for opposite decisions.

These tests pin the properties that keep the weaker question honest: no lookahead
in the partition key, per-partition controls, and no verdict on a partition too
small to support one.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))

import backtest_engine as be  # noqa: E402


class FakeState:
    def __init__(self, setup="", direction=""):
        self.setup = setup
        self.setup_direction = direction


def _bars(n=400, start=100.0, step=0.05, spread=0.5):
    return [
        {
            "time": f"2026-01-01T{i:05d}",
            "open": start + i * step,
            "high": start + i * step + spread,
            "low": start + i * step - spread,
            "close": start + i * step,
            "volume": 1_000_000,
        }
        for i in range(n)
    ]


def test_baseline_enters_on_a_fixed_cadence_and_records_its_bar():
    """The base must carry no view on price, or a 'filter improvement' could be
    the base's own edge leaking through."""
    trades = be.baseline_trades({"AAA": _bars()}, entry_every=20)
    assert trades
    assert all(t.setup == "BASELINE" for t in trades)
    assert all(t.entry_index is not None for t in trades)
    assert all(t.entry_index >= 120 for t in trades), "must respect the detector warmup"


def test_partitions_are_exhaustive_and_disjoint():
    bars = {"AAA": _bars()}
    base = be.baseline_trades(bars, entry_every=20)
    report = be.run_filter(bars, signal_states={"AAA": [FakeState() for _ in range(400)]},
                           base_trades=base)
    counted = sum(p["stats"]["trades"] for p in report["partitions"].values())
    assert counted == len(base)


def test_signal_state_is_read_backward_only():
    """A forward-looking window would leak the outcome into the partition key and
    manufacture separation out of nothing."""
    bars = {"AAA": _bars()}
    base = be.baseline_trades(bars, entry_every=20)
    entry = base[0].entry_index

    states = [FakeState() for _ in range(400)]
    # Fire the detector strictly AFTER the first entry. It must not be picked up.
    states[entry + 3] = FakeState("FLOOR BOUNCE", "BULLISH")
    report = be.run_filter(bars, signal_states={"AAA": states}, base_trades=base, lookback_bars=6)
    first_key = "none"
    assert first_key in report["partitions"]
    # Nothing should land in a fired partition from a purely forward signal at the
    # first trade; if it does, the lookup is reading ahead.
    fired = sum(
        p["stats"]["trades"] for k, p in report["partitions"].items() if k != "none"
    )
    assert fired == 0


def test_a_signal_inside_the_lookback_window_is_picked_up():
    bars = {"AAA": _bars()}
    base = be.baseline_trades(bars, entry_every=20)
    entry = base[0].entry_index
    states = [FakeState() for _ in range(400)]
    states[entry - 2] = FakeState("FLOOR BOUNCE", "BULLISH")
    report = be.run_filter(bars, signal_states={"AAA": states}, base_trades=base, lookback_bars=6)
    assert report["partitions"].get("bullish", {}).get("stats", {}).get("trades", 0) >= 1


def test_direction_maps_to_partition_key():
    bars = {"AAA": _bars()}
    base = be.baseline_trades(bars, entry_every=20)
    entry = base[0].entry_index
    states = [FakeState() for _ in range(400)]
    states[entry - 1] = FakeState("CEILING REJECTION", "BEARISH")
    report = be.run_filter(bars, signal_states={"AAA": states}, base_trades=base)
    assert "bearish" in report["partitions"]


def test_small_partitions_get_a_note_not_a_verdict():
    """Partitioning is a multiple-comparison machine. A slice too small to support
    a control must not be handed one."""
    bars = {"AAA": _bars()}
    base = be.baseline_trades(bars, entry_every=20)
    states = [FakeState() for _ in range(400)]
    states[base[0].entry_index - 1] = FakeState("FLOOR BOUNCE", "BULLISH")
    report = be.run_filter(bars, signal_states={"AAA": states}, base_trades=base)
    small = report["partitions"]["bullish"]
    assert "note" in small
    assert "control" not in small
    assert "beats_control_range" not in small


def test_lift_is_measured_against_the_base_not_against_zero():
    bars = {"AAA": _bars()}
    base = be.baseline_trades(bars, entry_every=20)
    report = be.run_filter(bars, signal_states={"AAA": [FakeState() for _ in range(400)]},
                           base_trades=base)
    part = report["partitions"]["none"]
    expected = round(part["stats"]["avg_return_pct"] - report["base"]["avg_return_pct"], 4)
    assert part["lift_vs_base_pp"] == expected


def test_empty_base_reports_an_error_rather_than_an_empty_verdict():
    report = be.run_filter({"AAA": _bars(n=50)}, signal_states={}, base_trades=[])
    assert "error" in report


def test_report_states_the_multiple_comparison_hazard():
    bars = {"AAA": _bars()}
    report = be.run_filter(bars, signal_states={"AAA": [FakeState() for _ in range(400)]})
    assert "multiple-comparison" in report["caveat"]
