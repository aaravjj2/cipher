from core import trader_journal, watchlists, workspace_layouts


class FakeRepository:
    def __init__(self):
        self.rows = []
        self.calls = []
        self.next_id = 1

    def list_rows(self, table, *, query=None):
        self.calls.append(("list", table, query or {}))
        return list(self.rows)

    def insert_row(self, table, payload):
        self.calls.append(("insert", table, payload))
        row = {"id": f"row-{self.next_id}", **payload}
        self.next_id += 1
        self.rows.append(row)
        return [row]

    def update_row(self, table, row_id, payload):
        self.calls.append(("update", table, row_id, payload))
        for row in self.rows:
            if row["id"] == row_id:
                row.update(payload)
                return [row]
        return []

    def delete_row(self, table, row_id):
        self.calls.append(("delete", table, row_id))
        before = len(self.rows)
        self.rows[:] = [row for row in self.rows if row.get("id") != row_id]
        return [{"deleted": row_id}] if len(self.rows) != before else []


def test_workspace_layouts_use_authenticated_repository_owner():
    repository = FakeRepository()

    saved = workspace_layouts.save_layout(
        "research",
        {"panels": ["Night Vision"]},
        repository=repository,
    )
    assert saved["name"] == "research"
    assert repository.calls[0][0] == "list"
    assert repository.calls[1][0] == "insert"
    assert repository.calls[1][2]["name"] == "research"
    assert "user_id" not in repository.calls[1][2]

    listed = workspace_layouts.list_layouts(include_payload=True, repository=repository)
    assert listed[0]["name"] == "research"
    assert listed[0]["layout"] == {"panels": ["Night Vision"]}

    workspace_layouts.delete_layout("research", repository=repository)
    assert workspace_layouts.list_layouts(repository=repository) == []


class WatchlistRepository:
    def __init__(self):
        self.tables = {"watchlists": [], "watchlist_members": [], "saved_screens": []}
        self.next_id = 1

    def list_rows(self, table, *, query=None):
        return list(self.tables[table])

    def get_row(self, table, row_id):
        return next((row for row in self.tables[table] if row.get("id") == row_id), None)

    def insert_row(self, table, payload):
        row = {"id": f"row-{self.next_id}", **payload}
        self.next_id += 1
        self.tables[table].append(row)
        return [row]

    def update_row(self, table, row_id, payload):
        row = self.get_row(table, row_id)
        if row is None:
            return []
        row.update(payload)
        return [row]

    def delete_row(self, table, row_id):
        self.tables[table][:] = [row for row in self.tables[table] if row.get("id") != row_id]
        return [{"deleted": row_id}]


def test_watchlists_use_repository_for_lists_and_members():
    repository = WatchlistRepository()
    created = watchlists.create_watchlist("same-name", repository=repository)
    watchlists.add_member(created["id"], "SPY", repository=repository)

    listed = watchlists.list_all(repository=repository)
    assert listed["watchlists"][0]["tickers"] == ["SPY"]
    assert listed["watchlists"][0]["name"] == "same-name"

    removed = watchlists.remove_member(created["id"], "SPY", repository=repository)
    assert removed["deleted"] is True
    watchlists.delete_watchlist(created["id"], repository=repository)
    assert watchlists.list_all(repository=repository)["watchlists"] == []


class JournalRepository:
    def __init__(self):
        self.tables = {"journal_entries": [], "chart_templates": []}
        self.next_id = 1

    def list_rows(self, table, *, query=None):
        rows = list(self.tables[table])
        if query and "ticker" in query:
            ticker = query["ticker"].removeprefix("eq.")
            rows = [row for row in rows if row.get("ticker") == ticker]
        return rows

    def get_row(self, table, row_id):
        return next((row for row in self.tables[table] if row.get("id") == row_id), None)

    def insert_row(self, table, payload):
        row = {"id": f"row-{self.next_id}", **payload}
        self.next_id += 1
        self.tables[table].append(row)
        return [row]

    def update_row(self, table, row_id, payload):
        row = self.get_row(table, row_id)
        if row is None:
            return []
        row.update(payload)
        return [row]

    def delete_row(self, table, row_id):
        self.tables[table][:] = [row for row in self.tables[table] if row.get("id") != row_id]
        return [{"deleted": row_id}]


def test_journal_repository_isolates_entries_and_templates():
    repository = JournalRepository()
    created = trader_journal.create_entry({
        "ticker": "SPY",
        "title": "same-name",
        "direction": "long",
        "status": "planned",
        "thesis": "Evidence-backed note",
    }, repository=repository)
    assert created["ticker"] == "SPY"
    listed = trader_journal.list_entries(repository=repository)
    assert [entry["title"] for entry in listed["entries"]] == ["same-name"]

    template = trader_journal.save_template("default", {"timeframe": "5m"}, repository=repository)
    assert template["name"] == "default"
    trader_journal.delete_entry(created["id"], repository=repository)
    assert trader_journal.list_entries(repository=repository)["entries"] == []
