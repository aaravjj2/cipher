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
        "call_oi_available": available, "put_oi_available": available,
        "oi_available": available, "volume_available": available,
        "call_mid_available": available, "put_mid_available": available,
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


def test_listed_but_uncalculable_exposure_is_null_not_zero(monkeypatch):
    """A listed contract can exist while missing gamma/size makes exposure unknown."""
    rows = [{"strike": 500.0, "cells": [
        _cell(False, listed=True, net_gex=0.0, net_vex=0.0, call_oi=0.0, put_oi=0.0),
    ]}]
    out = _heatmap(rows, monkeypatch)
    assert out["gex"][0] == [None]
    assert out["vex"][0] == [None]
    assert out["oi"][0] == [None]


def test_a_listed_strike_with_no_open_interest_still_reports_zero(monkeypatch):
    """The distinction only matters if a genuine zero survives as a zero."""
    rows = [{"strike": 500.0, "cells": [_cell(True, call_oi=0.0, put_oi=0.0, net_gex=0.0)]}]
    out = _heatmap(rows, monkeypatch)
    assert out["oi"][0] == [0.0], "a listed contract with zero OI must not become null"
    assert out["gex"][0] == [0.0]


def test_combined_open_interest_keeps_listed_unknown_side_unknown(monkeypatch):
    """A listed side with missing OI must not be summed as a measured zero."""
    rows = [{"strike": 500.0, "cells": [{
        **_cell(True, call_oi=0.0, put_oi=75.0),
        "call_listed": True,
        "put_listed": True,
        "call_oi_available": False,
        "put_oi_available": True,
    }]}]
    assert _heatmap(rows, monkeypatch)["oi"][0] == [None]


def test_combined_open_interest_allows_an_absent_side(monkeypatch):
    """An absent option side contributes zero to a known one-sided total."""
    rows = [{"strike": 500.0, "cells": [{
        **_cell(True, call_oi=0.0, put_oi=75.0),
        "call_listed": False,
        "put_listed": True,
        "call_oi_available": False,
        "put_oi_available": True,
    }]}]
    assert _heatmap(rows, monkeypatch)["oi"][0] == [75.0]


def test_partial_leg_exposure_is_not_presented_as_complete_net(monkeypatch):
    rows = [{"strike": 500.0, "cells": [{
        "available": False, "listed": True,
        "call_listed": True, "put_listed": True,
        "call_gex_available": True, "put_gex_available": False,
        "net_gex": None, "net_vex": None,
        "call_oi": 10.0, "put_oi": 10.0, "volume": 2.0,
        "call_mid": 1.0, "put_mid": None,
    }]}]
    out = _heatmap(rows, monkeypatch)
    assert out["gex"][0] == [None]


def test_real_exposure_returns_unknown_when_gamma_and_size_are_missing():
    from exposure import gex

    contract = {"type": "call", "gamma": None, "open_interest": None, "volume": None}
    assert gex(contract, 100.0) is None


def test_absent_option_side_does_not_make_valid_net_unknown(monkeypatch):
    result = _matrix(monkeypatch, [_contract("call", 10.0)])
    cell = result["rows"][0]["cells"][0]
    assert cell["call_listed"] is True
    assert cell["put_listed"] is False
    assert cell["available"] is True
    assert cell["net_gex"] == 10.0


def test_the_caveat_states_what_a_null_means(monkeypatch):
    out = _heatmap([{"strike": 500.0, "cells": [_cell(False)]}], monkeypatch)
    assert "no listed/calculable exposure" in out["caveat"]
    assert "not a zero measurement" in out["caveat"]


def _matrix(monkeypatch, contracts):
    """Exercise the shipped matrix cell assembly with network and math isolated."""
    monkeypatch.setattr(core_app, "resolve_options_feed", lambda requested: "opra")
    monkeypatch.setattr(
        core_app,
        "quote",
        lambda ticker: {
            "ticker": ticker,
            "price_context": 100.0,
            "day_change_pct": 0.0,
        },
    )
    monkeypatch.setattr(core_app, "option_chain", lambda *args, **kwargs: contracts)
    monkeypatch.setattr(core_app, "gex", lambda contract, spot: contract["_stub_gex"])
    monkeypatch.setattr(core_app, "vex", lambda contract, spot: contract["_stub_vex"])
    return core_app.matrix("MATRIX_TEST", "opra", "all", 1, force=True, chain_pages=1)


def _contract(kind, gex_value, oi=0.0):
    return {
        "expiry": "2099-01-17",
        "strike": 100.0,
        "type": kind,
        "open_interest": oi,
        "volume": 0.0,
        "mid": 1.0,
        "gamma": 0.1,
        "_stub_gex": gex_value,
        "_stub_vex": 0.0,
        "feed": "opra",
    }


def test_matrix_preserves_genuine_zero_and_rejects_partial_net(monkeypatch):
    zero = _matrix(monkeypatch, [_contract("call", 0.0), _contract("put", 0.0)])
    zero_cell = zero["rows"][0]["cells"][0]
    assert zero_cell["available"] is True
    assert zero_cell["net_gex"] == 0.0
    assert zero_cell["net_vex"] == 0.0
    assert zero_cell["call_oi"] == 0.0
    assert zero_cell["call_listed"] is True
    assert zero_cell["put_listed"] is True
    assert zero_cell["call_gex_available"] is True
    assert zero_cell["put_gex_available"] is True

    partial = _matrix(monkeypatch, [_contract("call", 10.0), _contract("put", None)])
    partial_cell = partial["rows"][0]["cells"][0]
    assert partial_cell["available"] is False
    assert partial_cell["call_gex"] == 10.0
    assert partial_cell["put_gex"] is None
    assert partial_cell["net_gex"] is None
    assert partial_cell["call_listed"] is True
    assert partial_cell["put_listed"] is True
    assert partial_cell["call_gex_available"] is True
    assert partial_cell["put_gex_available"] is False
