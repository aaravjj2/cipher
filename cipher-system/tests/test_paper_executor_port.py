"""The paper executor must be runnable here, and must refuse to run on nothing.

Two separate concerns. The port: a hardcoded Windows runtime root and a
Windows-only credential lookup made 24 modules and 14 test files unstartable on
this machine and on the VM. The gate: being startable is not a reason to start
shadowing, and a forward record of a strategy with no established edge is worse
than no record, because prospective collection makes noise look like evidence.

The market-data allowlist is deliberately re-asserted here. It is the boundary
that keeps this subsystem incapable of placing an order, and a port is exactly the
kind of change that could weaken it by accident.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
for path in (str(ROOT), str(ROOT / "core")):
    if path not in sys.path:
        sys.path.insert(0, path)

from paper_executor import config as pe_config  # noqa: E402
from paper_executor import promotion_gate  # noqa: E402
from paper_executor import tradier_market_data as tmd  # noqa: E402


def test_runtime_root_is_not_a_windows_path():
    """The default root decided whether this subsystem could run at all."""
    root = str(pe_config.DEFAULT_RUNTIME)
    assert not root.startswith("C:"), root
    assert "\\" not in root, root
    # Its parent must exist, or nothing can create the runtime tree on first start.
    assert pe_config.DEFAULT_RUNTIME.parent.exists()


def test_runtime_root_still_honours_the_environment():
    """The Windows deployment sets this; the port must not have broken it."""
    import importlib
    os.environ["CIPHER_PAPER_RUNTIME"] = "/tmp/cipher-paper-test-root"
    try:
        importlib.reload(pe_config)
        assert str(pe_config.DEFAULT_RUNTIME) == "/tmp/cipher-paper-test-root"
    finally:
        del os.environ["CIPHER_PAPER_RUNTIME"]
        importlib.reload(pe_config)


def test_token_resolves_from_the_environment(monkeypatch):
    monkeypatch.setenv("TRADIER_MARKET_TOKEN", "test-token-value")
    cfg = tmd.MarketDataConfig()
    assert tmd.load_token(cfg) == "test-token-value"


def test_token_refuses_a_world_readable_file(tmp_path, monkeypatch):
    """A market-data token in a readable file is still a leaked credential."""
    for name in ("TRADIER_MARKET_TOKEN", "TRADIER_ACCESS_TOKEN", "TRADIER_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    token_file = tmp_path / "tradier_token"
    token_file.write_text("secret-value")
    token_file.chmod(0o644)
    monkeypatch.setenv("CIPHER_TRADIER_TOKEN_FILE", str(token_file))
    monkeypatch.setattr(tmd, "keyring", None, raising=False)

    with pytest.raises(RuntimeError, match="readable"):
        tmd.load_token(tmd.MarketDataConfig())

    token_file.chmod(0o600)
    assert tmd.load_token(tmd.MarketDataConfig()) == "secret-value"


def test_order_endpoints_remain_blocked_after_the_port():
    """The port must not have loosened the market-data-only boundary."""
    for path in ("/v1/accounts", "/v1/accounts/orders", "/v1/markets/orders",
                 "/v1/accounts/positions", "/v1/accounts/balances"):
        with pytest.raises(tmd.TradierEndpointBlocked):
            tmd.assert_allowed_path(path)


def test_gate_is_empty_and_says_why():
    """Nothing has cleared the fast gate, so nothing may be shadowed."""
    status = promotion_gate.gate_status()
    assert status["eligible_count"] == 0
    assert status["queue_empty_is_expected"] is True
    assert "no established edge" in status["reason"]


def test_gate_fails_closed_on_a_missing_registry(tmp_path):
    """An unreadable registry must yield nothing eligible, never everything."""
    assert promotion_gate.eligible_strategies(tmp_path / "absent.sqlite") == set()


def test_gate_refuses_an_unpromoted_strategy():
    assert promotion_gate.is_eligible("edge.rsi2_reversion") is False
    assert promotion_gate.is_eligible("gex.wall_bounce") is False


def test_gate_reads_the_current_registry_schema(tmp_path):
    registry = tmp_path / "registry.sqlite"
    with sqlite3.connect(registry) as db:
        db.execute("create table strategies(strategy_id text, current_state text)")
        db.executemany("insert into strategies values (?,?)", [
            ("idea", "IDEA"), ("qualified", "FAST_BACKTESTED"),
        ])
        db.execute("create table promotion_events(strategy_id text, to_state text, decided_at text)")
    assert promotion_gate.eligible_strategies(registry) == {"qualified"}
