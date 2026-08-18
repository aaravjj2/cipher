from core import chart_saves, standing_notes


class FakeRepository:
    def __init__(self):
        self.rows = {"chart_saves": [], "standing_notes": []}

    def list_rows(self, table, *, query=None):
        rows = list(self.rows[table])
        query = query or {}
        if "note_date" in query:
            wanted = query["note_date"].removeprefix("eq.")
            rows = [row for row in rows if row.get("note_date") == wanted]
        return rows

    def insert_row(self, table, payload):
        row = {"id": f"{table}-1", **payload}
        self.rows[table].append(row)
        return [row]

    def update_row(self, table, row_id, payload):
        for row in self.rows[table]:
            if row["id"] == row_id:
                row.update(payload)
                return [row]
        return []

    def get_row(self, table, row_id):
        return next((row for row in self.rows[table] if row["id"] == row_id), None)

    def delete_row(self, table, row_id):
        self.rows[table] = [row for row in self.rows[table] if row["id"] != row_id]


def test_chart_save_is_repository_backed_and_validated():
    repository = FakeRepository()
    saved = chart_saves.create_save({
        "ticker": "SPY", "price": 500, "view": "1 Exp", "dateAdded": "8/18/26",
        "topLevels": [{"level": 501, "score": 100}], "imageUrl": "",
    }, repository=repository)
    assert saved["ticker"] == "SPY"
    assert chart_saves.list_saves(repository=repository)["saves"][0]["topLevels"][0]["score"] == 100


def test_standing_note_upsert_and_delete_is_repository_backed():
    repository = FakeRepository()
    saved = standing_notes.save_note({"date": "2026-08-18", "note": "review"}, repository=repository)
    assert saved["date"] == "2026-08-18"
    updated = standing_notes.save_note({"date": "2026-08-18", "note": "review again"}, repository=repository)
    assert updated["note"] == "review again"
    assert standing_notes.delete_note("2026-08-18", repository=repository)["deleted"] == "2026-08-18"
