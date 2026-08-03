from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "audit_reference_volume_feasibility.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("reference_volume_audit", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_reference_volume_audit_preserves_no_spend_and_no_patch_boundaries(tmp_path, monkeypatch):
    module = _load_module()
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "SAMPLE_DIR", tmp_path / "samples")
    module.SAMPLE_DIR.mkdir()
    (module.SAMPLE_DIR / "sample.zip").write_bytes(b"raw sample")
    module.main()
    output = next((tmp_path / "data" / "governance").glob("reference_volume_feasibility_*.json"))
    payload = json.loads(output.read_text())
    assert payload["frozen_cases"]["symbols"] == module.FROZEN_SYMBOLS
    assert payload["Databento"]["status"] == "rejected_semantic_coverage_not_proven_no_pilot"
    assert payload["FirstRate"]["sample_evidence"][0]["sha256"]
    assert payload["FirstRate"]["stage"] == "excluded_by_no_purchase_policy"
    assert payload["LondonStrategicEdge"]["status"] == "blocked_pending_free_api_key_and_volume_semantic_verification"
    assert payload["acceptance"]["vendor_patches_price_data"] is False
    assert payload["acceptance"]["trading_or_signal_evaluation"] is False
