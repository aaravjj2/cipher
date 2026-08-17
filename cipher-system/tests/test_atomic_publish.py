from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "atomic_publish.py"
spec = importlib.util.spec_from_file_location("atomic_publish", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(module)


def test_exchange_swaps_complete_directory_trees(tmp_path: Path) -> None:
    staged = tmp_path / "staged"
    live = tmp_path / "live"
    staged.mkdir(); live.mkdir()
    (staged / "index.html").write_text("new", encoding="utf-8")
    (live / "index.html").write_text("old", encoding="utf-8")
    module.exchange(staged, live)
    assert (live / "index.html").read_text(encoding="utf-8") == "new"
    assert (staged / "index.html").read_text(encoding="utf-8") == "old"
