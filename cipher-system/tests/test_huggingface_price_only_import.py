from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ingest_huggingface_price_only_month.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("hf_price_only", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_huggingface_importer_keeps_price_only_boundary(monkeypatch):
    module = _load_module()
    assert module.DATASET == "mito0o852/OHLCV-1m"
    assert set(module.FROZEN_SYMBOLS) == {"SPY", "QQQ", "IWM", "XLF", "XLE", "AAPL", "MSFT", "NVDA", "GE"}


def test_huggingface_importer_rejects_invalid_month(monkeypatch):
    module = _load_module()
    monkeypatch.setattr(module, "parse_args", lambda: type("Args", (), {"month": "202306", "destination": Path("/tmp")})())
    with pytest.raises(SystemExit, match="YYYY-MM"):
        module.main()
