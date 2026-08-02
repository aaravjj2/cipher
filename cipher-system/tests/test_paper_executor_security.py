from pathlib import Path

import pytest

from core.paper_executor.config import ExecutorConfig, ServerConfig
from core.paper_executor.tradier_market_data import TradierEndpointBlocked, assert_allowed_path


def test_forbidden_tradier_paths_are_blocked():
    with pytest.raises(TradierEndpointBlocked):
        assert_allowed_path("/v1/accounts/demo/orders")
    assert_allowed_path("/v1/markets/quotes")
    assert_allowed_path("/v1/markets/timesales")


def test_localhost_only_binding():
    with pytest.raises(ValueError):
        ExecutorConfig(server=ServerConfig(host="0.0.0.0"))


def test_source_contains_no_forbidden_url_path_patterns():
    root = Path(__file__).resolve().parents[1] / "core" / "paper_executor"
    forbidden = ["/accounts/", "/orders", "/placeorder", "/positions"]
    offenders = []
    for path in root.glob("*.py"):
        text = path.read_text(encoding="utf-8").lower()
        for pattern in forbidden:
            if pattern in text:
                offenders.append((path.name, pattern))
    assert offenders == []
