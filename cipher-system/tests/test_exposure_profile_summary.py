"""Gamma flip selection and one-sided exposure in `exposure.profile_summary`.

Two defects, both silent -- they produced a plausible number rather than an error:

1. The flip was whichever sign change came first scanning up from the lowest strike. A net
   GEX profile usually crosses zero more than once; SPY on 2026-08-12 crossed 13 times
   between 740.99 and 773.63 against a spot of 772.68, and the published "gamma flip" was
   740.99 -- the lowest crossing, 4.1% below spot. Nearest-spot is both correct and stable:
   recomputing over different subsets of well-covered expirations moved it only between
   772.26 and 773.63.

2. `cell["available"]` is the AND of call and put availability, so it is false where calls
   are unlisted but puts are measured -- 363 of SPY's 1,044 cells that day. Row inclusion
   used that flag while the sums added any non-null value, so identical one-sided data was
   counted for a row that passed the test and discarded for a row that did not.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.exposure import profile_summary  # noqa: E402


def cell(call=None, put=None, *, call_ok=None, put_ok=None):
    """A matrix cell. Availability defaults to "measured if a value is present"."""
    call_ok = (call is not None) if call_ok is None else call_ok
    put_ok = (put is not None) if put_ok is None else put_ok
    return {
        "call_gex": call,
        "put_gex": put,
        "call_gex_available": call_ok,
        "put_gex_available": put_ok,
        "available": call_ok and put_ok,
    }


def row(strike, *cells):
    return {"strike": strike, "cells": list(cells)}


# ------------------------------------------------------------------ flip selection

def multi_crossing_rows():
    """Net profile crossing zero three times: near 105, near 125 and near 145."""
    return [
        row(100.0, cell(10.0, -1.0)),    # net +9
        row(110.0, cell(1.0, -10.0)),    # net -9   -> crossing ~105
        row(120.0, cell(1.0, -10.0)),    # net -9
        row(130.0, cell(10.0, -1.0)),    # net +9   -> crossing ~125
        row(140.0, cell(10.0, -1.0)),    # net +9
        row(150.0, cell(1.0, -10.0)),    # net -9   -> crossing ~145
    ]


def test_flip_is_the_crossing_nearest_spot():
    rows = multi_crossing_rows()
    assert profile_summary(rows, spot=148.0)["gamma_flip_level"] == 145.0
    assert profile_summary(rows, spot=124.0)["gamma_flip_level"] == 125.0
    assert profile_summary(rows, spot=101.0)["gamma_flip_level"] == 105.0


def test_the_old_rule_would_always_have_returned_the_lowest_crossing():
    """The regression, stated as the property that failed."""
    rows = multi_crossing_rows()
    summary = profile_summary(rows, spot=148.0)
    lowest = summary["gamma_flip_candidates"][0]
    assert lowest == 105.0
    assert summary["gamma_flip_level"] != lowest


def test_every_crossing_is_reported_so_a_noisy_profile_is_visible():
    summary = profile_summary(multi_crossing_rows(), spot=124.0)
    assert summary["gamma_flip_candidates"] == [105.0, 125.0, 145.0]
    assert summary["gamma_flip_reference"] == "nearest_spot"


def test_a_single_clean_crossing_is_unchanged_by_the_fix():
    """NVDA had exactly one crossing; the fix must not move such cases."""
    rows = [row(200.0, cell(10.0, -1.0)), row(220.0, cell(1.0, -10.0))]
    summary = profile_summary(rows, spot=260.0)
    assert summary["gamma_flip_level"] == 210.0
    assert summary["gamma_flip_candidates"] == [210.0]


def test_without_spot_the_rule_is_named_rather_than_guessed():
    summary = profile_summary(multi_crossing_rows())
    assert summary["gamma_flip_reference"] == "nearest_dominant_strike"
    assert summary["gamma_flip_level"] in summary["gamma_flip_candidates"]


def test_a_strike_sitting_exactly_at_zero_net_is_a_crossing():
    rows = [row(100.0, cell(5.0, -5.0)), row(110.0, cell(9.0, -1.0))]
    assert profile_summary(rows, spot=100.0)["gamma_flip_level"] == 100.0


def test_no_crossing_yields_no_flip_but_still_yields_walls():
    rows = [row(100.0, cell(10.0, -1.0)), row(110.0, cell(20.0, -2.0))]
    summary = profile_summary(rows, spot=105.0)
    assert summary["gamma_flip_level"] is None
    assert summary["gamma_flip_candidates"] == []
    assert summary["call_wall_strike"] == 110.0


# ------------------------------------------------------------------ one-sided exposure

def test_a_put_only_strike_is_not_discarded():
    """available is False for these cells, but the put exposure was really measured."""
    rows = [
        row(100.0, cell(None, -500.0, call_ok=False, put_ok=True)),
        row(110.0, cell(10.0, -1.0)),
    ]
    summary = profile_summary(rows, spot=105.0)
    assert summary["put_wall_strike"] == 100.0


def test_a_call_only_strike_is_not_discarded():
    rows = [
        row(100.0, cell(10.0, -1.0)),
        row(110.0, cell(900.0, None, call_ok=True, put_ok=False)),
    ]
    assert profile_summary(rows, spot=105.0)["call_wall_strike"] == 110.0


def test_an_unavailable_value_is_never_summed():
    """A stale or uncalculable number must not enter the profile just for being non-null."""
    rows = [
        row(100.0, cell(1.0, -1.0), cell(10_000.0, None, call_ok=False, put_ok=False)),
        row(110.0, cell(50.0, -1.0)),
    ]
    summary = profile_summary(rows, spot=105.0)
    assert summary["call_wall_strike"] == 110.0, "the 10,000 was flagged unavailable"


def test_a_strike_with_nothing_measured_is_skipped_entirely():
    rows = [
        row(100.0, cell(None, None, call_ok=False, put_ok=False)),
        row(110.0, cell(10.0, -1.0)),
    ]
    summary = profile_summary(rows, spot=105.0)
    assert summary["global_max_strike"] == 110.0


def test_rows_without_per_side_flags_fall_back_to_available():
    """Callers that build their own rows must keep working."""
    rows = [
        {"strike": 100.0, "cells": [{"call_gex": 10.0, "put_gex": -1.0, "available": True}]},
        {"strike": 110.0, "cells": [{"call_gex": 99.0, "put_gex": -1.0, "available": False}]},
    ]
    summary = profile_summary(rows, spot=105.0)
    assert summary["call_wall_strike"] == 100.0


def test_empty_input_returns_the_full_shape():
    summary = profile_summary([], spot=100.0)
    assert summary["gamma_flip_level"] is None
    assert summary["gamma_flip_candidates"] == []
    assert summary["call_wall_strike"] is None
    assert summary["put_wall_strike"] is None


def test_walls_require_the_right_sign():
    """A call wall with no positive call gamma, and a put wall with no negative put gamma."""
    rows = [row(100.0, cell(0.0, 0.0))]
    summary = profile_summary(rows, spot=100.0)
    assert summary["call_wall_strike"] is None
    assert summary["put_wall_strike"] is None
