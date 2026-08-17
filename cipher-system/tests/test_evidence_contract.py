from datetime import datetime, timezone

import pytest

from core.evidence_contract import EvidenceSnapshot, SignalRecord, attach_contracts, canonical_json


def _evidence() -> dict:
    return {
        "snapshot_id": "evidence_fixture_12345678",
        "ticker": "aapl",
        "provider": "Alpaca",
        "feed": "OPRA",
        "event_at": "2026-08-17T13:30:00Z",
        "captured_at": "2026-08-17T13:30:02Z",
        "freshness": {"status": "current", "age_seconds": 2},
        "coverage": {"status": "sufficient"},
        "missing_reasons": ["gamma_partial"],
        "feature_snapshot_ids": ["gex_1"],
        "session": {"phase": "regular"},
    }


def test_evidence_contract_is_deterministic_and_normalizes_inputs() -> None:
    first = EvidenceSnapshot.from_mapping(_evidence())
    second = EvidenceSnapshot.from_mapping({**_evidence(), "ticker": "AAPL", "feed": "opra"})
    assert first.to_dict() == second.to_dict()
    assert first.ticker == "AAPL"
    assert first.snapshot_id == "evidence_fixture_12345678"
    assert canonical_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'


def test_signal_contract_has_stable_identity_and_read_only_boundary() -> None:
    signal = SignalRecord(
        ticker="TSLA", strategy="wall_rejection_v1", direction="SHORT",
        signal_at="2026-08-17T14:00:00+00:00", available_at="2026-08-17T14:00:02+00:00",
        evidence_snapshot_ids=("evidence_1",), decision="candidate",
    )
    rendered = signal.to_dict()
    assert rendered["signal_id"].startswith("signal_")
    assert rendered["read_only"] is True
    assert rendered["execution_capability"] is False


def test_contract_rejects_future_available_time_and_invalid_quality() -> None:
    with pytest.raises(ValueError, match="available_at"):
        SignalRecord(
            ticker="MU", strategy="radar", direction="long",
            signal_at="2026-08-17T14:00:00Z", available_at="2026-08-17T13:59:59Z",
            evidence_snapshot_ids=(),
        )
    with pytest.raises(ValueError, match="invalid freshness"):
        EvidenceSnapshot(
            ticker="MU", provider="alpaca", feed="opra",
            event_at="2026-08-17T14:00:00Z", captured_at="2026-08-17T14:00:01Z",
            freshness="fresh", coverage="unknown",
        )


def test_attach_contracts_preserves_legacy_payload() -> None:
    payload = {"ticker": "AAPL", "evidence_snapshot": _evidence(), "score": 70}
    result = attach_contracts(payload)
    assert result["score"] == 70
    assert result["evidence_contract"]["ticker"] == "AAPL"
    assert "signal_record" not in result  # no signal timestamp was fabricated
