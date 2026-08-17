import sqlite3

from core import journal_option_analytics as analytics


def _db(path):
    db = sqlite3.connect(path)
    db.execute("create table tradier_option_timesales(provider_ts text,bid real,ask real,price real,symbol text)")
    db.executemany("insert into tradier_option_timesales values(?,?,?,?,?)", [
        ("2026-08-14T14:00:00Z", 1.0, 1.2, 1.1, "NVDA260821C00230000"),
        ("2026-08-14T14:05:00Z", 1.5, 1.7, 1.6, "NVDA260821C00230000"),
        ("2026-08-14T14:10:00Z", .7, .9, .8, "NVDA260821C00230000"),
    ])
    db.commit(); db.close()


def test_exact_leg_mark_excursions_use_side_quantity_and_multiplier(tmp_path):
    path = tmp_path / "marks.sqlite"; _db(path)
    result = analytics.analyze({"entry_at": "2026-08-14T14:00:00Z", "exit_at": "2026-08-14T14:10:00Z", "legs": [
        {"contract_symbol": "NVDA260821C00230000", "side": "buy", "quantity": 2, "multiplier": 100, "entry_mark": 1.2, "entry_mark_type": "ask"}
    ]}, path)
    bid = result["legs"][0]["excursions"]["bid"]
    assert bid["mfe_dollars"] == 60 and bid["mae_dollars"] == -100
    assert result["status"] == "CALCULATED"


def test_missing_contract_is_partial_not_zero(tmp_path):
    path = tmp_path / "marks.sqlite"; _db(path)
    result = analytics.analyze({"entry_at": "2026-08-14T14:00:00Z", "legs": [
        {"contract_symbol": "MISSING", "side": "sell", "quantity": 1, "multiplier": 100, "entry_mark": 1}
    ]}, path)
    assert result["status"] == "UNAVAILABLE"
    assert result["legs"][0]["status"] == "NO_CAPTURED_MARKS"
