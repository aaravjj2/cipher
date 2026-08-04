from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def load_script():
    path = SCRIPTS / "audit_post_merge_verification.py"
    name = "cipher_test_post_merge_verification"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_verdict_preserves_known_lineage_gap():
    module = load_script()
    checks = {"a": True, "b": True}
    assert module.classify(checks, holdout_lineage_present=True) == "PASSED"
    assert (
        module.classify(checks, holdout_lineage_present=False)
        == "PASSED_WITH_KNOWN_CANONICAL_LINEAGE_GAP"
    )
    assert module.classify({"a": True, "b": False}, holdout_lineage_present=True) == "FAILED"


def test_pre_restart_pid_parser_requires_all_values_to_be_service_scoped():
    module = load_script()
    parsed = module.parse_pre_restart(
        [
            "cipher-core.service=10",
            "cipher-web.service=11",
            "cipher-gex.service=12",
            "cipher-tradier.service=13",
        ]
    )
    assert parsed["cipher-core.service"] == 10
    assert parsed["cipher-tradier.service"] == 13
    with pytest.raises(ValueError):
        module.parse_pre_restart(["unknown.service=99"])
    with pytest.raises(ValueError):
        module.parse_pre_restart(["cipher-core.service"])


def test_timestamp_fix_behavior_survived_merge():
    module = load_script()
    evidence = module.timestamp_code_evidence()
    assert evidence == {
        "observed_availability_helper_present": True,
        "historical_publication_uses_observation_time": True,
        "future_publication_never_precedes_publication": True,
        "news_model_rejects_received_before_publication": True,
        "news_model_rejects_available_before_received": True,
    }


def test_eight_layer_topology_and_noncausal_naming_survived_merge():
    module = load_script()
    evidence = module.stack_evidence()
    assert evidence["implemented_layer_count"] == 8
    assert evidence["layer_numbers"] == list(range(1, 9))
    assert evidence["shadow_paper_is_formal_layer_7"] is True
    assert evidence["attribution_layer_name"] == "attribution_and_anomaly_engine"
    assert evidence["compatibility_alias_points_to_eight_layer_class"] is True
    assert evidence["boundary_violations"] == []
    assert evidence["stale_causal_attribution_and_anomaly_name_present"] is False
    assert evidence["stale_causal_attribution_source_present"] is False


def test_post_merge_report_records_qualified_verdict():
    report = (ROOT / "docs" / "post_merge_verification.md").read_text(encoding="utf-8")
    assert "PASSED_WITH_KNOWN_CANONICAL_LINEAGE_GAP" in report
    assert "11/12 strict independent origins" in report
    assert "Holdout C dataset manifests: 0" in report
    assert "attribution_and_anomaly_engine" in report
