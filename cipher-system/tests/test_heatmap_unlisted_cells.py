"""Unlisted heatmap cells must read as absent, not as zero.

`/api/heatmap` built `gex` and `vex` with a null for any unavailable cell, but summed two
`.get(..., 0.0)` defaults for `oi`. Against a live SPY surface that meant 1180 of 3255 cells
reported 0.0 open interest while the same cells reported null exposure: 36% of the grid
asserting a measurement it did not have. On a heatmap "no contract is listed here" and "a
listed contract carrying no open interest" are different facts that look identical once both
render as 0.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "core") not in sys.path:
    sys.path.insert(0, str(ROOT / "core"))

import app as core_app  # noqa: E402


def _cell(available: bool, **fields):
    # Mirrors a real unavailable cell: listed false, every numeric already 0.0, mids null.
    base = {
        "available": available, "listed": available,
        "net_gex": 0.0, "net_vex": 0.0, "call_oi": 0.0, "put_oi": 0.0,
        "volume": 0.0, "call_mid": None, "put_mid": None,
    }
    base.update(fields)
    return base


def _payload(rows):
    # heatmap() indexes cells by expiration position, so the two must agree in length.
    width = max((len(row["cells"]) for row in rows), default=0)
    return {
        "ticker": "SPY",
        "as_of": "2026-08-11T13:00:00Z",
        "quote": {"price_context": 500.0, "day_change_pct": 0.1},
        "expirations": [f"2026-08-{14 + 7 * i:02d}" for i in range(width)],
        "rows": rows,
        "summary": {},
        "coverage": {"contracts": 4},
        "formula": "test",
    }


def _heatmap(rows, monkeypatch):
    """Drive the real `heatmap()` with a stubbed matrix, so the surface logic under test
    is the shipped one rather than a reimplementation."""
    monkeypatch.setattr(core_app, "matrix", lambda *a, **k: _payload(rows))
    return core_app.heatmap("SPY", "opra", "compact", 2)


def test_unlisted_cells_are_null_in_every_surface_including_oi(monkeypatch):
    rows = [{"strike": 500.0, "cells": [
        _cell(True, net_gex=1_000.0, net_vex=5.0, call_oi=120.0, put_oi=80.0, volume=42.0),
        _cell(False),
    ]}]
    out = _heatmap(rows, monkeypatch)

    assert out["gex"][0] == [1_000.0, None]
    assert out["vex"][0] == [5.0, None]
    # The regression: this used to be [200.0, 0.0].
    assert out["oi"][0] == [200.0, None]
    assert out["vol"][0] == [42.0, None]
    assert out["call_oi"][0] == [120.0, None]
    assert out["put_oi"][0] == [80.0, None]


def test_a_listed_strike_with_no_open_interest_still_reports_zero(monkeypatch):
    """The distinction only matters if a genuine zero survives as a zero."""
    rows = [{"strike": 500.0, "cells": [_cell(True, call_oi=0.0, put_oi=0.0, net_gex=0.0)]}]
    out = _heatmap(rows, monkeypatch)
    assert out["oi"][0] == [0.0], "a listed contract with zero OI must not become null"
    assert out["gex"][0] == [0.0]


def test_combined_open_interest_tolerates_missing_sides(monkeypatch):
    """A listed cell missing one side must not raise on None + float."""
    rows = [{"strike": 500.0, "cells": [_cell(True, call_oi=None, put_oi=75.0)]}]
    assert _heatmap(rows, monkeypatch)["oi"][0] == [75.0]


def test_the_caveat_states_what_a_null_means(monkeypatch):
    out = _heatmap([{"strike": 500.0, "cells": [_cell(False)]}], monkeypatch)
    assert "not listed" in out["caveat"]
    assert "not a zero measurement" in out["caveat"]
