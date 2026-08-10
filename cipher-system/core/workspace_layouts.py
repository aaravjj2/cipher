"""Named multi-pane workspace layouts for the terminal-style Workspace mode.

The frontend's docking grid (dockview) can serialize its entire arrangement —
which panels are open, how they're split, which tab is active — to a JSON blob.
This module is the storage for those blobs so a layout survives a page reload
and a browser change, rather than living only in localStorage.

Storage mirrors holdings.py exactly (single JSON file under data/, one process
lock, atomic replace via a temp file in the same directory) because it has the
same shape of problem: user-owned state with no real-data source to re-derive it
from, so a half-written or silently-reset file is a real loss.

Layout blobs are treated as opaque: this module validates the envelope (name,
size, that it parses as a JSON object) but never interprets the grid structure
itself. Panel/grid semantics belong to the frontend that produced them, and
schema-checking them here would only couple the backend to dockview's internal
format and break on its next version.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "workspace_layouts"
LAYOUTS_PATH = DATA_DIR / "layouts.json"
SCHEMA_VERSION = 1

_LOCK = threading.Lock()

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.\-]{0,39}$")
MAX_LAYOUTS = 50
# A dockview grid for a dozen panels serializes to a few KB. 256 KB is far above
# any legitimate layout and low enough that a malformed or runaway client can't
# grow the file without bound.
MAX_LAYOUT_BYTES = 256 * 1024


class WorkspaceLayoutError(ValueError):
    """A rejected layout request. Subclasses ValueError so app.py's existing
    `except ValueError as exc: send_json(422, ...)` handles it with no new plumbing."""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _validate_name(raw: Any) -> str:
    name = str(raw or "").strip()
    if not NAME_RE.match(name):
        raise WorkspaceLayoutError(
            "layout name must be 1-40 characters, start alphanumeric, and contain only "
            "letters, numbers, spaces, dots, underscores or hyphens"
        )
    return name


def _validate_layout(raw: Any) -> dict:
    if not isinstance(raw, dict):
        raise WorkspaceLayoutError("layout must be a JSON object")
    encoded = json.dumps(raw)
    if len(encoded.encode("utf-8")) > MAX_LAYOUT_BYTES:
        raise WorkspaceLayoutError(
            f"layout exceeds the {MAX_LAYOUT_BYTES // 1024} KB limit"
        )
    return raw


def _load() -> dict:
    if not LAYOUTS_PATH.is_file():
        return {"schema_version": SCHEMA_VERSION, "layouts": []}
    # As in holdings.py, a parse failure is raised rather than swallowed: silently
    # discarding every saved layout on a read error is the worse failure mode.
    with LAYOUTS_PATH.open("r", encoding="utf-8") as handle:
        store = json.load(handle)
    store.setdefault("layouts", [])
    return store


def _save(store: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(DATA_DIR), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(store, handle, indent=2, default=str)
        os.replace(tmp, LAYOUTS_PATH)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def list_layouts(include_payload: bool = False) -> list[dict]:
    """Saved layouts, newest-updated first.

    The default omits the grid blobs: the panel only needs names and timestamps to
    render its picker, and shipping every blob on every poll would send far more
    than the UI uses.
    """
    with _LOCK:
        store = _load()
    rows = sorted(store["layouts"], key=lambda r: r.get("updated_at") or "", reverse=True)
    if include_payload:
        return [dict(r) for r in rows]
    return [{k: v for k, v in r.items() if k != "layout"} for r in rows]


def get_layout(name: str) -> dict:
    name = _validate_name(name)
    with _LOCK:
        store = _load()
    for row in store["layouts"]:
        if row["name"] == name:
            return dict(row)
    raise WorkspaceLayoutError(f"no saved layout named {name!r}")


def save_layout(name: str, layout: Any) -> dict:
    """Creates or overwrites a named layout. Overwrite is intentional — the panel's
    save control is "save this arrangement as <name>", and re-saving the same name
    is how a user updates a layout they're iterating on."""
    name = _validate_name(name)
    layout = _validate_layout(layout)
    now = _utcnow()
    with _LOCK:
        store = _load()
        for row in store["layouts"]:
            if row["name"] == name:
                row["layout"] = layout
                row["updated_at"] = now
                _save(store)
                return {k: v for k, v in row.items() if k != "layout"}
        if len(store["layouts"]) >= MAX_LAYOUTS:
            raise WorkspaceLayoutError(
                f"cannot save more than {MAX_LAYOUTS} layouts; delete one first"
            )
        record = {
            "name": name,
            "layout": layout,
            "created_at": now,
            "updated_at": now,
        }
        store["layouts"].append(record)
        _save(store)
    return {k: v for k, v in record.items() if k != "layout"}


def delete_layout(name: str) -> dict:
    name = _validate_name(name)
    with _LOCK:
        store = _load()
        remaining = [r for r in store["layouts"] if r["name"] != name]
        if len(remaining) == len(store["layouts"]):
            raise WorkspaceLayoutError(f"no saved layout named {name!r}")
        store["layouts"] = remaining
        _save(store)
    return {"deleted": True, "name": name}


def layouts_status() -> dict:
    rows = list_layouts()
    return {
        "as_of": _utcnow(),
        "layouts": rows,
        "count": len(rows),
        "max_layouts": MAX_LAYOUTS,
    }
