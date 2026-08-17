from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "google_agentic"
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from cipher_fleet.client import ALLOWED_PATHS, CipherCoreClient, validate_base_url
from cipher_fleet.policy import AuditTrail, content_fingerprint, tool_is_allowed
from cipher_fleet.tools import TOOL_NAMES


def test_core_transport_is_get_only_and_allowlisted(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit: int) -> bytes:
            return b'{"status":"ok","read_only":true}'

    def fake_urlopen(request, timeout):
        calls.extend([request, timeout])
        return Response()

    monkeypatch.setattr("cipher_fleet.client.urllib.request.urlopen", fake_urlopen)
    payload = CipherCoreClient(timeout=2).get("/api/health")
    request = calls[0]
    assert request.get_method() == "GET"
    assert request.full_url == "http://127.0.0.1:8282/api/health"
    assert payload["read_only"] is True
    with pytest.raises(ValueError, match="allowlist"):
        CipherCoreClient().get("/api/not-approved")


def test_only_read_surfaces_are_reachable() -> None:
    assert ALLOWED_PATHS
    assert all(path.startswith("/api/") for path in ALLOWED_PATHS)
    assert "/api/health" in ALLOWED_PATHS
    assert "/api/night-vision" in ALLOWED_PATHS


@pytest.mark.parametrize(
    "url",
    [
        "ftp://127.0.0.1:8282",
        "http://example.com:8282",
        "http://user:pass@127.0.0.1:8282",
        "http://127.0.0.1:8282?secret=value",
    ],
)
def test_unapproved_core_origins_fail_closed(url: str) -> None:
    with pytest.raises(ValueError):
        validate_base_url(url)


def test_fleet_tool_allowlist_is_exact() -> None:
    assert TOOL_NAMES == {
        "get_market_structure",
        "get_options_flow",
        "get_historical_evidence",
        "get_strategy_validation",
        "get_risk_and_governance_review",
    }
    assert all(tool_is_allowed(name) for name in TOOL_NAMES)
    assert tool_is_allowed("unknown_tool") is False


def test_audit_trail_records_hashes_not_payloads(tmp_path: Path) -> None:
    audit_path = tmp_path / "audit.jsonl"
    secret_text = "never-store-this-private-value"
    fingerprint = content_fingerprint({"private": secret_text})
    AuditTrail(audit_path).append("tool_completed", result=fingerprint)
    raw = audit_path.read_text(encoding="utf-8")
    row = json.loads(raw)
    assert row["event"] == "tool_completed"
    assert row["result"]["bytes"] > 0
    assert len(row["result"]["sha256"]) == 64
    assert secret_text not in raw


def test_adk_agent_tree_imports_when_optional_runtime_is_installed() -> None:
    pytest.importorskip("google.adk", reason="isolated google-adk runtime is not installed")
    from cipher_fleet.agent import app, root_agent

    assert app.root_agent is root_agent
    assert [plugin.name for plugin in app.plugins] == ["cipher_policy_audit"]
    assert root_agent.name == "cipher_supervisor"
    assert {agent.name for agent in root_agent.sub_agents} == {
        "market_structure_agent",
        "options_flow_agent",
        "historical_evidence_agent",
        "strategy_validation_agent",
        "risk_adversarial_agent",
    }
