"""Tests for the cipher-market MCP server surface.

The server is stdlib-only by design; these tests exercise the pure handlers
without any network or host process.
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

SERVER_PATH = Path(__file__).resolve().parents[1] / "mcp-server" / "market_server.py"
_spec = importlib.util.spec_from_file_location("market_server_under_test", SERVER_PATH)
ms = importlib.util.module_from_spec(_spec)
sys.modules["market_server_under_test"] = ms
_spec.loader.exec_module(ms)


def test_tool_specs_are_complete_and_annotated_read_only() -> None:
    specs = ms.tool_specs()
    names = {spec["name"] for spec in specs}
    assert {
        "cipher_health", "get_quote", "get_gex_levels", "autopilot_status",
        "paper_ledger_summary", "decision_quality", "prospective_log_tail",
        "search", "fetch",
    } <= names
    for spec in ms.annotated_tool_specs():
        assert spec["annotations"]["readOnlyHint"] is True
        assert spec["annotations"]["destructiveHint"] is False


def test_searchable_universe_loads_from_file_and_covers_traded_tickers() -> None:
    assert "fallback" not in ms.SEARCHABLE_SOURCE
    assert len(ms.SEARCHABLE) > 400
    # Tickers the autopilot actually traded must be searchable.
    for symbol in ("SNDK", "NBIS", "ALAB", "NVDA"):
        assert symbol in ms.SEARCHABLE


def test_search_falls_back_to_static_list_when_universe_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CIPHER_UNIVERSE_JSON", str(tmp_path / "missing.json"))
    fresh = importlib.util.spec_from_file_location("ms_missing", SERVER_PATH)
    module = importlib.util.module_from_spec(fresh)
    fresh.loader.exec_module(module)
    assert module.SEARCHABLE_SOURCE == "fallback static list"
    assert "NVDA" in module.SEARCHABLE


def test_prospective_log_tail_reports_absence_honestly(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(ms, "PROSPECTIVE_LOG", tmp_path / "none.jsonl")
    out = ms.handle_tool("prospective_log_tail", {"limit": 3})
    assert out["available"] is False


def test_prospective_log_tail_returns_parsed_rows(tmp_path, monkeypatch) -> None:
    log = tmp_path / "log.jsonl"
    rows = [{"date": "2026-08-26", "ticker": "NVDA", "gate": "NO_TRADE"}]
    log.write_text(json.dumps(rows[0]) + "\n", encoding="utf-8")
    monkeypatch.setattr(ms, "PROSPECTIVE_LOG", log)
    out = ms.handle_tool("prospective_log_tail", {"limit": 5})
    assert out["available"] is True and out["rows"][0]["ticker"] == "NVDA"


def test_paper_ledger_summary_reports_missing_ledger(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(ms, "PAPER_LEDGER_DB", tmp_path / "none.sqlite")
    out = ms.handle_tool("paper_ledger_summary", {})
    assert out["available"] is False
    assert out["paper_only"] is True


def test_paper_ledger_summary_reads_real_schema(tmp_path, monkeypatch) -> None:
    db = tmp_path / "ledger.sqlite"
    with sqlite3.connect(db) as conn:
        conn.executescript(
            """
            create table paper_positions(id text primary key, ticker text, direction text,
              quantity integer, entry_price real, exit_price real, exit_reason text,
              opened_at text, closed_at text, status text);
            create table paper_orders(id text primary key, side text, symbol text,
              status text, created_at text);
            """
        )
        conn.execute(
            "insert into paper_positions values('p1','NVDA','bullish',1,1.0,1.2,"
            "'option_take_profit','2026-08-25T14:00+00:00','2026-08-25T15:00+00:00','CLOSED')"
        )
        conn.execute("insert into paper_orders values('o1','BUY_TO_OPEN','NVDA','FILLED','2026-08-25T14:00+00:00')")
    monkeypatch.setattr(ms, "PAPER_LEDGER_DB", db)
    out = ms.handle_tool("paper_ledger_summary", {})
    assert out["available"] is True
    assert out["closed_total"]["n"] == 1
    assert out["closed_total"]["wins"] == 1
    assert out["recent_closed"][0]["pnl_usd"] == 20.0
    assert out["live_execution_capability"] is False


def test_agent_book_reports_absence_honestly(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(ms, "AGENT_DECISION_LOG", tmp_path / "none.jsonl")
    out = ms.handle_tool("agent_book", {})
    assert out["available"] is False
    assert out["open_positions"] == [] and out["paper_only"] is True


def test_agent_book_projects_open_lots_from_real_log(tmp_path, monkeypatch) -> None:
    import importlib.util as _iu
    spec = _iu.spec_from_file_location(
        "adl_for_mcp", Path(__file__).resolve().parents[1] / "scripts" / "agent_decision_log.py")
    adl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adl)
    log = tmp_path / "log.jsonl"
    adl.append(log, "INTENT", {"decision_id": "ab1", "ticker": "SPY", "side": "buy",
                               "contract_symbol": "SPY260904C00765000", "quantity": 1,
                               "limit_price": 3.1, "rationale": "t"})
    adl.append(log, "FILLED", {"decision_id": "ab1", "filled_price": 3.12,
                               "filled_quantity": 1})
    monkeypatch.setattr(ms, "AGENT_DECISION_LOG", log)
    out = ms.handle_tool("agent_book", {})
    assert out["available"] is True
    assert out["open_positions"][0]["quantity"] == 1
    assert out["open_positions"][0]["avg_open_price"] == 3.12
