"""End-to-end checks for the Cipher Market MCP over its real stdio protocol.

Every test drives `market_server.py` as a subprocess with JSON-RPC on stdin/stdout, which
is exactly how Claude Desktop speaks to it. Testing the handler functions directly would
miss framing, id handling and error mapping -- the parts that actually break an integration.

Tests that need live market data skip when cipher-core is unreachable, so the suite stays
runnable on a machine where the service is stopped. The read-only guarantees are asserted
without a network at all, because they must hold regardless.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SERVER = HERE / "market_server.py"
sys.path.insert(0, str(HERE))

import market_server  # noqa: E402


def rpc(*requests: dict, env: dict | None = None) -> list[dict]:
    """Send framed JSON-RPC lines to a fresh server process and collect the replies."""
    payload = "".join(json.dumps(r) + "\n" for r in requests)
    proc = subprocess.run(
        [sys.executable, str(SERVER)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, **(env or {})},
    )
    assert proc.returncode == 0, proc.stderr
    return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]


def call(tool: str, args: dict | None = None, env: dict | None = None) -> dict:
    replies = rpc(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": tool, "arguments": args or {}}},
        env=env,
    )
    return replies[-1]["result"]


def core_available() -> bool:
    try:
        market_server._get("/api/health")
        return True
    except Exception:
        return False


needs_core = pytest.mark.skipif(not core_available(), reason="cipher-core is not reachable")


# ----------------------------------------------------------------- read-only guarantees

def test_no_path_outside_the_allowlist_can_be_fetched():
    """The allowlist is the security boundary; a typo must fail closed, not pass through."""
    with pytest.raises(ValueError, match="not in the read-only allowlist"):
        market_server._get("/api/holdings")
    with pytest.raises(ValueError, match="not in the read-only allowlist"):
        market_server._get("/api/backtest")


def test_allowlist_contains_no_mutating_endpoint():
    """cipher-core serves POST routes for these paths. None may be reachable from here."""
    for path in ("/api/holdings", "/api/backtest", "/api/alerts", "/api/ask",
                 "/api/workspace-layouts", "/api/options-backtest"):
        assert path not in market_server.ALLOWED_PATHS, path


def test_every_request_is_a_get(monkeypatch):
    seen = []

    class FakeResponse:
        def read(self):
            return b"{}"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout=None):
        seen.append(request.get_method())
        return FakeResponse()

    monkeypatch.setattr(market_server.urllib.request, "urlopen", fake_urlopen)
    for path in sorted(market_server.ALLOWED_PATHS):
        market_server._get(path)
    assert seen == ["GET"] * len(market_server.ALLOWED_PATHS)


def test_no_tool_can_place_an_order():
    names = {spec["name"] for spec in market_server.tool_specs()}
    forbidden = {"order", "buy", "sell", "trade", "position", "execute", "submit", "cancel"}
    for name in names:
        assert not (forbidden & set(name.lower().split("_"))), name


# ----------------------------------------------------------------- protocol

def test_initialize_advertises_the_research_notice_and_tools():
    reply = rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})[0]
    result = reply["result"]
    assert result["serverInfo"]["name"] == "cipher-market-mcp"
    assert "read-only" in result["instructions"]
    assert result["capabilities"]["tools"] == {"listChanged": False}


def test_tools_list_schemas_are_well_formed():
    reply = rpc(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    )[-1]
    tools = reply["result"]["tools"]
    assert len(tools) >= 9
    for tool in tools:
        assert tool["description"].strip()
        schema = tool["inputSchema"]
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        for field in schema.get("required", []):
            assert field in schema["properties"], (tool["name"], field)


def test_notifications_produce_no_reply():
    """A notification has no id; answering one corrupts the stream."""
    replies = rpc({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
    assert replies == []


def test_unknown_method_is_an_error_not_a_crash():
    reply = rpc({"jsonrpc": "2.0", "id": 7, "method": "does/not/exist", "params": {}})[0]
    assert reply["error"]["code"] == -32603
    assert reply["id"] == 7


def test_unreachable_core_reports_which_url_failed():
    result = call("cipher_health", env={"CIPHER_CORE_URL": "http://127.0.0.1:9"})
    assert result["isError"] is True
    text = result["content"][0]["text"]
    assert "cannot reach cipher-core" in text and "127.0.0.1:9" in text


def test_missing_required_argument_is_a_tool_error():
    result = call("get_quote", {})
    assert result["isError"] is True
    assert "symbol is required" in result["content"][0]["text"]

    result = call("search_contract", {"symbol": "SPY"})
    assert result["isError"] is True
    assert "strike is required" in result["content"][0]["text"]


# ----------------------------------------------------------------- projections

def test_unknown_exposure_cells_are_skipped_rather_than_summed_as_zero():
    """A null cell means 'not listed or not calculable'. Treating it as 0 invents data."""
    totals, known, unknown = market_server._strike_totals(
        [100.0, 101.0, 102.0],
        [[1.0, None, 2.0], [None, None], [5.0]],
    )
    # known cells are 1.0, 2.0 and 5.0; unknown are the three nulls
    assert known == 3 and unknown == 3
    by_strike = {row["strike"]: row for row in totals}
    assert by_strike[100.0]["net_gex"] == 3.0
    assert by_strike[100.0]["expirations_counted"] == 2
    # A strike with no calculable cell at all is omitted, not reported as zero exposure.
    assert 101.0 not in by_strike


def test_gex_projection_keeps_the_caveat_and_reports_coverage():
    payload = {
        "ticker": "TST", "spot": 100.0, "day_change_pct": 1.0, "updated": "now",
        "contracts": 5, "strikes": [99.0, 100.0, 101.0],
        "gex": [[1.0], [None], [-4.0]],
        "expirations": ["2026-01-16", "2026-02-20"],
        "totals": {"gex_by_expiration": [10.0, -50.0]},
        "summary": {"call_wall_strike": 101.0},
        "formula": {"gex": "..."}, "caveat": "a null cell is not a zero",
    }
    out = market_server.project_gex_levels(payload, near=2)
    assert out["caveat"] == "a null cell is not a zero"
    assert out["cell_coverage"]["unavailable_cells"] == 1
    assert out["net_gex_total"] == -3.0
    assert out["levels"] == {"call_wall_strike": 101.0}
    # Largest absolute exposure leads, and expirations are paired with their totals.
    assert out["largest_absolute_gex_expirations"][0] == {"expiration": "2026-02-20", "net_gex": -50.0}
    assert "read-only" in out["research_notice"]


def test_night_vision_projection_drops_the_grid_but_keeps_the_levels():
    payload = {
        "ticker": "TST", "summary": {"gamma_flip_level": 1.0}, "peak": {"price": 2.0},
        "levels": [{"price": 3.0}], "xray": [{"strike": 4.0}] * 50,
        "session_levels": {"levels": []}, "rows": [{"strike": 1.0, "cells": [{}] * 30}] * 90,
        "caveat": "local estimate",
    }
    out = market_server.project_night_vision(payload)
    assert "rows" not in out
    assert out["levels"] == {"gamma_flip_level": 1.0}
    assert out["peak_exposure"] == {"price": 2.0}
    assert len(out["xray_strikes"]) == 20
    assert out["caveat"] == "local estimate"


def test_oversized_results_are_truncated_with_a_reason():
    big = {"rows": ["x" * 200] * 2000}
    out = market_server.result(big)
    text = out["content"][0]["text"]
    assert '"truncated": true' in text
    assert "structuredContent" not in out


def test_strategy_projection_keeps_the_real_identifier():
    """The payload's key is `strategy_id`; projecting `id` silently dropped it."""
    out = market_server.project_strategies({
        "summary": {"total": 1},
        "standard": "beat a random-entry control",
        "strategies": [{"strategy_id": "edge.x", "name": "x", "family": "edge",
                        "evaluable": True, "blocked_reason": None, "source": "core/x.py"}],
    })
    assert out["strategies"][0]["strategy_id"] == "edge.x"
    assert "source" not in out["strategies"][0]


# ----------------------------------------------------------------- live data

@needs_core
def test_health_reports_read_only():
    result = call("cipher_health")
    assert result["structuredContent"]["read_only"] is True


@needs_core
def test_quote_returns_a_price():
    data = call("get_quote", {"symbol": "spy"})["structuredContent"]
    assert data["ticker"] == "SPY"
    assert data["mid"] > 0


@needs_core
def test_bars_respect_the_limit():
    data = call("get_bars", {"symbol": "SPY", "timeframe": "1Day", "limit": 5})["structuredContent"]
    assert 1 <= len(data["bars"]) <= 5
    assert {"open", "high", "low", "close", "volume"} <= set(data["bars"][0])


@needs_core
def test_gex_levels_are_small_enough_for_a_host_context():
    result = call("get_gex_levels", {"symbol": "SPY", "strikes_near_spot": 8})
    text = result["content"][0]["text"]
    assert len(text) < 20_000, f"projection is {len(text)} bytes; the raw grid is ~200 KB"
    data = result["structuredContent"]
    assert data["spot"] > 0
    assert len(data["strikes_near_spot"]) <= 8
    assert data["cell_coverage"]["calculable_cells"] > 0


@needs_core
def test_night_vision_is_small_enough_for_a_host_context():
    result = call("get_night_vision", {"symbol": "SPY"})
    text = result["content"][0]["text"]
    assert len(text) < 20_000, f"projection is {len(text)} bytes; the raw payload is ~730 KB"
    data = result["structuredContent"]
    assert data["quote"]["mid"] > 0
    assert "gamma_flip_level" in data["levels"]


@needs_core
def test_contract_search_without_a_strike_is_a_clear_message_not_a_500():
    """cipher-core used to answer this with float() failing on None."""
    result = call("search_contract", {"symbol": "SPY", "strike": "not-a-number"})
    assert result["isError"] is True
    assert "must be numeric" in result["content"][0]["text"]


@needs_core
def test_strategies_carry_the_standard_they_must_beat():
    data = call("list_strategies")["structuredContent"]
    assert "random-entry control" in data["standard"]
    assert data["summary"]["total"] > 0
    assert data["strategies"][0]["strategy_id"]
