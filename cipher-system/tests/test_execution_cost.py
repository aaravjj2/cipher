"""The execution-cost profile must stay a measurement, not drift into a guess.

`core/backtest_engine.DEFAULT_COST_BPS` decides every verdict this repository
produces, so the tests here guard the two ways the measurement could quietly stop
being one: the artifact going hollow, and the per-symbol lookup silently
substituting the fallback for a measured value without saying so.
"""
from __future__ import annotations

import sys
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (str(ROOT), str(ROOT / "core")):
    if path not in sys.path:
        sys.path.insert(0, path)

import execution_cost as ec  # noqa: E402
from conftest import require_artifact  # noqa: E402


def test_profile_artifact_is_not_hollow():
    path = require_artifact("data/execution_costs/spread_profile.json",
                            non_empty_key="equity_half_spread_bps")
    profile = ec.load_profile(path)
    assert profile is not None
    assert profile["capture_window"]["distinct_days"] >= 1
    assert profile["cells"] > 0
    # The caveat is load-bearing: it is the only thing stopping a nine-day
    # sample being read as a cost model for a decade of backtests.
    assert "not a cost model for historical periods" in profile["caveat"]


def test_every_cell_reports_its_sample_count():
    """A median without its n is unusable, and `sufficient` must follow from n."""
    path = require_artifact("data/execution_costs/spread_profile.json",
                            non_empty_key="equity_half_spread_bps")
    profile = ec.load_profile(path)
    for name, cell in profile["equity_half_spread_bps"].items():
        assert cell["samples"] > 0, name
        assert cell["sufficient"] == (cell["samples"] >= ec.MIN_SAMPLES_FOR_USE), name
        assert cell["p25"] <= cell["median"] <= cell["p75"] <= cell["p95"], name


def test_lookup_reports_provenance_rather_than_blending():
    """An assumed value must be labelled as assumed, never returned as measured."""
    profile = {"equity_half_spread_bps": {
        "AAA": {"median": 0.5, "samples": ec.MIN_SAMPLES_FOR_USE, "sufficient": True},
        "BBB": {"median": 9.9, "samples": 3, "sufficient": False},
    }}
    assert ec.equity_half_spread_bps("AAA", profile=profile, fallback=2.0) == (0.5, "measured:median")

    value, provenance = ec.equity_half_spread_bps("BBB", profile=profile, fallback=2.0)
    assert value == 2.0 and provenance.startswith("assumed:insufficient-samples")

    value, provenance = ec.equity_half_spread_bps("ZZZ", profile=profile, fallback=2.0)
    assert value == 2.0 and provenance == "assumed:symbol-not-captured"

    assert ec.equity_half_spread_bps("AAA", profile=None, fallback=2.0) == (2.0, "assumed:no-profile")


def test_combined_corpus_aggregation_uses_one_sequential_scan():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE tradier_stream_events (
            captured_at TEXT, event_type TEXT, symbol TEXT, bid REAL, ask REAL,
            asset_class TEXT, underlying TEXT, option_expiration TEXT
        );
        CREATE INDEX idx_events_type ON tradier_stream_events(event_type, captured_at);
        INSERT INTO tradier_stream_events VALUES
            ('2026-08-01T14:00:00Z', 'quote', 'SPY', 100, 100.02,
             'underlying', NULL, NULL),
            ('2026-08-01T14:00:00Z', 'quote', 'SPY260821C00100000', 2, 2.20,
             'option', 'SPY', '2026-08-21');
        """
    )
    statements = []
    conn.set_trace_callback(statements.append)

    equity, option = ec.build_profiles(conn)

    assert equity["SPY"]["samples"] == 1
    assert option["SPY|8-30"]["samples"] == 1
    scans = [sql for sql in statements if "tradier_stream_events not indexed" in sql.lower()]
    assert len(scans) == 1
    conn.set_trace_callback(None)
    assert equity == ec.build_equity_profile(conn)
    assert option == ec.build_option_profile(conn)


def test_capture_window_labels_sparse_days_and_missing_weekdays():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE tradier_stream_events(captured_at TEXT)")
    conn.executemany(
        "INSERT INTO tradier_stream_events VALUES (?)",
        [
            ("2026-08-03T13:30:00Z",),
            ("2026-08-05T13:30:00Z",),
        ],
    )

    window = ec._capture_window(conn)

    assert window["daily_event_counts"] == {
        "2026-08-03": 1,
        "2026-08-05": 1,
    }
    assert window["sparse_days"] == window["daily_event_counts"]
    assert window["missing_weekdays"] == ["2026-08-04"]


def test_engine_without_a_profile_is_unchanged():
    """Every recorded result was produced with no profile; that path must not move."""
    import backtest_engine as be
    assert be._cost_for("NVDA", 2.0, None) == 2.0
    assert be._cost_for("NVDA", 2.0, {}) == 2.0


def test_engine_charges_the_measured_spread_when_given_one():
    import backtest_engine as be
    profile = {"equity_half_spread_bps": {
        "NVDA": {"median": 0.525, "samples": 10_000, "sufficient": True},
    }}
    assert be._cost_for("NVDA", 2.0, profile) == 0.525
    # A symbol outside the capture universe must fall back rather than borrow a
    # neighbour's spread — the disjoint sweep set is 9/10 uncaptured, so this is
    # the common case, not an edge case.
    assert be._cost_for("NFLX", 2.0, profile) == 2.0
