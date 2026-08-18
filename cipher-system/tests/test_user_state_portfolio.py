from core import portfolio_risk


class PortfolioRepository:
    def __init__(self):
        self.tables = {"portfolio_risk_positions": [], "portfolio_risk_settings": []}
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

    def upsert_row(self, table, payload, *, conflict_column):
        if self.tables[table]:
            self.tables[table][0].update(payload)
            return [self.tables[table][0]]
        return self.insert_row(table, payload)

    def update_row(self, table, row_id, payload):
        row = self.get_row(table, row_id)
        if row is None:
            return []
        row.update(payload)
        return [row]

    def delete_row(self, table, row_id):
        self.tables[table][:] = [row for row in self.tables[table] if row.get("id") != row_id]
        return [{"deleted": row_id}]


def test_portfolio_risk_repository_stores_positions_and_cash():
    repository = PortfolioRepository()
    assert portfolio_risk.set_cash(5000, repository=repository)["cash"] == 5000
    position = portfolio_risk.add_position({
        "strategy": "Research",
        "asset_type": "stock",
        "ticker": "SPY",
        "quantity": 2,
        "entry_price": 500,
    }, repository=repository)
    assert position["ticker"] == "SPY"
    assert portfolio_risk.status(
        quote_fn=lambda _ticker: {"price_context": 510, "as_of": "now"},
        chain_fn=lambda *_args: [],
        repository=repository,
    )["summary"]["position_count"] == 1
    assert portfolio_risk.delete_position(position["id"], repository=repository)["deleted"] == position["id"]
