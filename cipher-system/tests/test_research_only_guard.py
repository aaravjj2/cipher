"""Cipher must never be able to place an order. This is the test that makes that true."""
from __future__ import annotations

from pathlib import Path

import pytest

from core.research_only_guard import (
    ALLOWED_FILES,
    FORBIDDEN_BROKER_IMPORTS,
    forbidden_terms,
    scan,
)
from core.research_platform.seven_layer_stack import EightLayerStackSpec

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_the_shipped_tree_contains_no_order_placing_term() -> None:
    violations = scan(REPO_ROOT)
    assert violations == [], "live-order authority reached the tree:\n" + "\n".join(
        v.describe() for v in violations
    )


def test_the_guard_can_actually_fail(tmp_path: Path) -> None:
    """A guard that cannot fail proves nothing.

    The check above passes on a clean tree whether the scan works or is quietly broken --
    a typo in a glob, an exclusion that swallows everything -- so plant a real violation
    and require it to be found.
    """
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "sneaky.py").write_text(
        "def go(client):\n    return client.submit_order(symbol='NVDA', qty=1)\n",
        encoding="utf-8",
    )
    violations = scan(tmp_path)
    assert len(violations) == 1
    found = violations[0]
    assert found.term == "submit_order"
    assert found.path == "core/sneaky.py"
    assert found.line == 2


def test_a_broker_sdk_import_is_caught(tmp_path: Path) -> None:
    (tmp_path / "feed.py").write_text("import alpaca_trade_api as tradeapi\n", encoding="utf-8")
    assert [v.term for v in scan(tmp_path)] == ["alpaca_trade_api"]


def test_typescript_and_mjs_are_scanned_too(tmp_path: Path) -> None:
    """The web and app layers can reach a broker as easily as Python can."""
    (tmp_path / "panel.tsx").write_text("const c = new TradingClient();\n", encoding="utf-8")
    (tmp_path / "server.mjs").write_text("await fetch('/v2/orders', {method:'POST'});\n", encoding="utf-8")
    terms = sorted(v.term for v in scan(tmp_path))
    assert terms == ["/v2/orders", "TradingClient"]


def test_tests_may_name_the_terms() -> None:
    """This very file contains them; excluding tests is what makes that possible."""
    assert "submit_order" in Path(__file__).read_text(encoding="utf-8")
    assert scan(REPO_ROOT) == []


@pytest.mark.parametrize("excluded", ["vendor", "node_modules", "__pycache__"])
def test_third_party_and_build_output_is_not_cipher_s_promise(tmp_path: Path, excluded: str) -> None:
    """Cipher promises this about its own code, not about 30 cloned repositories."""
    directory = tmp_path / excluded / "someproject"
    directory.mkdir(parents=True)
    (directory / "broker.py").write_text("def place_order(): ...\n", encoding="utf-8")
    assert scan(tmp_path) == []


def test_the_guard_enforces_the_spec_s_own_list_not_a_copy() -> None:
    """If the two ever drift, the spec's report and the code's behaviour disagree."""
    for term in EightLayerStackSpec.forbidden_live_terms:
        assert term in forbidden_terms()
    for term in FORBIDDEN_BROKER_IMPORTS:
        assert term in forbidden_terms()


def test_adding_a_term_to_the_spec_tightens_the_guard(tmp_path: Path) -> None:
    (tmp_path / "x.py").write_text("wire_transfer_everything()\n", encoding="utf-8")
    assert scan(tmp_path) == []
    assert len(scan(tmp_path, terms=("wire_transfer_everything",))) == 1


def test_allowed_files_exist_so_the_exemption_cannot_rot() -> None:
    """An exemption for a moved or deleted file silently widens the scan's blind spot."""
    for relative in ALLOWED_FILES:
        assert (REPO_ROOT / relative).is_file(), f"exempted file is missing: {relative}"
