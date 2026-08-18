"""User-owned standing calendar notes for hosted mode."""
from __future__ import annotations

from datetime import date, datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _date(value: object) -> str:
    try:
        return date.fromisoformat(str(value or "").strip()).isoformat()
    except ValueError:
        raise ValueError("note date must be YYYY-MM-DD") from None


def _row(row: dict) -> dict:
    return {"id": row.get("id"), "date": row.get("note_date"), "note": row.get("note"),
            "created_at": row.get("created_at"), "updated_at": row.get("updated_at")}


def list_notes(*, repository) -> dict:
    rows = repository.list_rows("standing_notes", query={"order": "note_date.asc"}) or []
    return {"notes": [_row(dict(item)) for item in rows], "read_only": True, "execution_capability": False}


def save_note(raw: dict, *, repository) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("standing note must be an object")
    note = str(raw.get("note") or "").strip()
    if not note or len(note) > 2000:
        raise ValueError("note must contain 1-2000 characters")
    note_date = _date(raw.get("date"))
    existing = repository.list_rows("standing_notes", query={"note_date": f"eq.{note_date}"}) or []
    payload = {"note_date": note_date, "note": note, "updated_at": _now()}
    if existing:
        rows = repository.update_row("standing_notes", str(existing[0]["id"]), payload) or []
    else:
        rows = repository.insert_row("standing_notes", {**payload, "created_at": _now()}) or []
    if not rows:
        raise ValueError("standing note was not saved")
    return _row(dict(rows[0]))


def delete_note(note_date: object, *, repository) -> dict:
    normalized = _date(note_date)
    rows = repository.list_rows("standing_notes", query={"note_date": f"eq.{normalized}"}) or []
    if not rows:
        raise ValueError("unknown standing note")
    repository.delete_row("standing_notes", str(rows[0]["id"]))
    return {"deleted": normalized, "execution_capability": False}
