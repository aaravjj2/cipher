from core import alerts, holdings


class TableRepository:
    def __init__(self):
        self.tables = {"holdings": [], "alerts": []}
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


def test_holdings_repository_isolates_manual_positions():
    repository = TableRepository()
    created = holdings.add_position("SPY", 2, 500, "2026-08-01", "note", repository=repository)
    assert created["ticker"] == "SPY"
    assert holdings.list_positions(repository=repository)[0]["shares"] == 2
    holdings.delete_position(created["id"], repository=repository)
    assert holdings.list_positions(repository=repository) == []


def test_alert_repository_isolates_rules():
    repository = TableRepository()
    created = alerts.add_rule(ticker="SPY", kind="price_above", threshold=600, repository=repository)
    assert created["enabled"] is True
    assert alerts.list_rules(repository=repository)["rules"][0]["ticker"] == "SPY"
    assert alerts.delete_rule(created["id"], repository=repository)["deleted"] == created["id"]
    assert alerts.list_rules(repository=repository)["rules"] == []
