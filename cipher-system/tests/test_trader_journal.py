from core import trader_journal
import pytest


def test_journal_links_and_underlying_excursion(tmp_path):
    path = tmp_path / "journal.sqlite"
    entry = trader_journal.create_entry({"ticker": "AAPL", "title": "PM break", "status": "open", "direction": "long", "entry_at": "2026-08-14T14:00:00Z", "entry_price": 100, "targets": [105], "tags": ["breakout"], "position_id": "p1", "signal_id": "s1", "chart_state": {"timeframe": "5m"}}, path)
    bars = lambda *a, **k: {"bars": [{"time": "2026-08-14T14:05:00Z", "high": 106, "low": 98}]}
    result = trader_journal.list_entries(bars_fn=bars, path=path)
    assert result["entries"][0]["excursion"]["mfe_pct"] == 6
    assert result["entries"][0]["excursion"]["mae_pct"] == -2
    assert result["entries"][0]["chart_snapshot_svg"].startswith("<svg")
    assert result["entries"][0]["position_id"] == "p1"
    assert trader_journal.update_entry(entry["id"], {"status": "closed", "exit_reason": "target"}, path)["status"] == "closed"
    assert result["execution_capability"] is False


def test_chart_template_upserts_by_name(tmp_path):
    path = tmp_path / "journal.sqlite"
    trader_journal.save_template("Intraday", {"timeframe": "5m"}, path)
    trader_journal.save_template("Intraday", {"timeframe": "1m"}, path)
    rows = trader_journal.list_templates(path)["templates"]
    assert len(rows) == 1 and rows[0]["state"]["timeframe"] == "1m"


def test_journal_update_revalidates_bounded_structured_fields(tmp_path):
    path = tmp_path / "journal.sqlite"
    entry = trader_journal.create_entry({"ticker": "MU", "title": "Review"}, path)
    updated = trader_journal.update_entry(entry["id"], {
        "direction": "SHORT", "entry_price": "123.5", "tags": ["a" * 80], "chart_state": {"timeframe": "1m"},
    }, path)
    assert updated["direction"] == "short"
    assert updated["entry_price"] == 123.5
    assert updated["tags"] == ["a" * 60]
    with pytest.raises(ValueError, match="chart_state"):
        trader_journal.update_entry(entry["id"], {"chart_state": ["not", "an", "object"]}, path)
    with pytest.raises(ValueError, match="at most 100"):
        trader_journal.update_entry(entry["id"], {"targets": list(range(101))}, path)


def test_journal_validates_and_round_trips_exact_option_legs(tmp_path):
    entry = trader_journal.create_entry({"ticker": "NVDA", "title": "Call review", "legs": [
        {"contract_symbol": "NVDA260821C00230000", "side": "buy", "quantity": 2, "multiplier": 100, "entry_mark": 1.25, "entry_mark_type": "ask"}
    ]}, tmp_path / "journal.sqlite")
    assert entry["legs"][0]["quantity"] == 2
    assert entry["legs"][0]["entry_mark"] == 1.25
