"""Storage and validation tests for core/workspace_layouts.py.

Every test redirects the module's module-level paths at a tmp_path so the real
data/workspace_layouts/layouts.json is never touched — the same isolation reason
this module keeps its paths as module globals rather than reading them inline.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

CORE = Path(__file__).resolve().parents[1] / "core"
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

import workspace_layouts as wl  # noqa: E402


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(wl, "DATA_DIR", tmp_path)
    monkeypatch.setattr(wl, "LAYOUTS_PATH", tmp_path / "layouts.json")
    return wl


GRID = {"grid": {"root": {"type": "branch", "data": []}}, "panels": {"p1": {"id": "p1"}}}


def test_empty_store_reports_zero(store):
    assert store.layouts_status()["count"] == 0
    assert store.list_layouts() == []


def test_save_then_get_round_trips_the_blob_verbatim(store):
    store.save_layout("Trading Desk", GRID)
    assert store.get_layout("Trading Desk")["layout"] == GRID


def test_list_omits_payload_by_default(store):
    store.save_layout("Trading Desk", GRID)
    (row,) = store.list_layouts()
    assert "layout" not in row
    assert row["name"] == "Trading Desk"
    (with_payload,) = store.list_layouts(include_payload=True)
    assert with_payload["layout"] == GRID


def test_resaving_same_name_overwrites_without_adding_a_row(store):
    store.save_layout("Desk", GRID)
    store.save_layout("Desk", {"grid": "v2"})
    assert store.layouts_status()["count"] == 1
    assert store.get_layout("Desk")["layout"] == {"grid": "v2"}


def test_overwrite_preserves_created_at_and_advances_updated_at(store):
    first = store.save_layout("Desk", GRID)
    store.save_layout("Desk", {"grid": "v2"})
    row = store.get_layout("Desk")
    assert row["created_at"] == first["created_at"]
    assert row["updated_at"] >= first["updated_at"]


def test_list_is_newest_updated_first(store):
    store.save_layout("Older", GRID)
    store.save_layout("Newer", GRID)
    # Equal-second timestamps are possible, so assert set membership plus the
    # ordering key rather than a brittle exact order on a fast machine.
    names = [r["name"] for r in store.list_layouts()]
    assert set(names) == {"Older", "Newer"}
    stamps = [r["updated_at"] for r in store.list_layouts()]
    assert stamps == sorted(stamps, reverse=True)


@pytest.mark.parametrize("bad", ["", "   ", "!leading-punct", "x" * 41, None])
def test_invalid_names_are_rejected(store, bad):
    with pytest.raises(wl.WorkspaceLayoutError):
        store.save_layout(bad, GRID)


@pytest.mark.parametrize("good", ["A", "Trading Desk", "desk_1", "a.b-c", "x" * 40])
def test_valid_names_are_accepted(store, good):
    assert store.save_layout(good, GRID)["name"] == good


@pytest.mark.parametrize("bad", ["not a dict", 42, None, ["a"]])
def test_non_object_layouts_are_rejected(store, bad):
    with pytest.raises(wl.WorkspaceLayoutError):
        store.save_layout("Desk", bad)


def test_oversize_layout_is_rejected(store):
    with pytest.raises(wl.WorkspaceLayoutError, match="KB limit"):
        store.save_layout("Desk", {"blob": "x" * (wl.MAX_LAYOUT_BYTES + 1)})


def test_layout_cap_is_enforced(store, monkeypatch):
    monkeypatch.setattr(wl, "MAX_LAYOUTS", 2)
    store.save_layout("One", GRID)
    store.save_layout("Two", GRID)
    with pytest.raises(wl.WorkspaceLayoutError, match="cannot save more than 2"):
        store.save_layout("Three", GRID)
    # An existing name must still be overwritable at the cap — otherwise a user who
    # hits the ceiling can no longer update the layouts they already have.
    assert store.save_layout("One", {"grid": "v2"})["name"] == "One"


def test_delete_removes_only_the_named_layout(store):
    store.save_layout("Keep", GRID)
    store.save_layout("Drop", GRID)
    assert store.delete_layout("Drop") == {"deleted": True, "name": "Drop"}
    assert [r["name"] for r in store.list_layouts()] == ["Keep"]


def test_get_and_delete_raise_for_unknown_names(store):
    with pytest.raises(wl.WorkspaceLayoutError, match="no saved layout"):
        store.get_layout("Nope")
    with pytest.raises(wl.WorkspaceLayoutError, match="no saved layout"):
        store.delete_layout("Nope")


def test_state_survives_a_fresh_read(store):
    store.save_layout("Desk", GRID)
    # _load() re-reads from disk, so a second call proves the write landed rather
    # than only mutating in-memory state.
    assert store.get_layout("Desk")["layout"] == GRID
    assert store.LAYOUTS_PATH.is_file()


def test_unreadable_store_raises_rather_than_silently_resetting(store):
    store.LAYOUTS_PATH.write_text("{ not json", encoding="utf-8")
    # Matching holdings.py's deliberate choice: losing a user's saved layouts
    # silently is a worse failure than a loud one they can act on.
    with pytest.raises(ValueError):
        store.list_layouts()
