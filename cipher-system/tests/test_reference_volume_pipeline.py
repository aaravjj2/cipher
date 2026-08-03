from __future__ import annotations

from datetime import date, time
import importlib.util
from pathlib import Path

import pytest

from core.research_platform.reference_volume import (
    REFERENCE_ALLOWED_USE,
    ReferenceImportPolicy,
    RegularSessionSpec,
    ensure_raw_reference_path,
    reconcile_session_volume,
    summarize_reference_rows,
    validate_reference_manifest,
)


ROOT = Path(__file__).resolve().parents[1]
IMPORT_SCRIPT = ROOT / "scripts" / "import_reference_volume_csv.py"
RECONCILE_SCRIPT = ROOT / "scripts" / "reconcile_reference_volume_manifest.py"


def load_script(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def policy(*, expected_bars: int = 2) -> ReferenceImportPolicy:
    return ReferenceImportPolicy(
        provider="Independent Test Feed",
        source_timezone="America/New_York",
        symbols=("SPY",),
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 2),
        session=RegularSessionSpec(
            start=time(9, 30),
            end=time(16, 0),
            end_inclusive=True,
            expected_bars=expected_bars,
        ),
    )


def test_reference_summary_uses_inclusive_canonical_session_without_price_fields():
    rows = [
        {"timestamp": "2024-01-02T09:29:00", "symbol": "SPY", "volume": "99", "close": "1"},
        {"timestamp": "2024-01-02T09:30:00", "symbol": "SPY", "volume": "100", "close": "999"},
        {"timestamp": "2024-01-02T16:00:00", "symbol": "SPY", "volume": "200", "close": "0"},
        {"timestamp": "2024-01-02T16:01:00", "symbol": "SPY", "volume": "999", "close": "1"},
    ]
    summary = summarize_reference_rows(rows, policy=policy())[0]
    assert summary.reference_valid is True
    assert summary.raw_rows == 4
    assert summary.regular_rows == 2
    assert summary.regular_volume == 300
    assert summary.first_regular_timestamp.endswith("09:30:00-05:00")
    assert summary.last_regular_timestamp.endswith("16:00:00-05:00")


def test_reference_summary_rejects_duplicate_or_incomplete_sessions():
    rows = [
        {"timestamp": "2024-01-02T09:30:00", "symbol": "SPY", "volume": "100"},
        {"timestamp": "2024-01-02T09:30:00", "symbol": "SPY", "volume": "100"},
    ]
    summary = summarize_reference_rows(rows, policy=policy())[0]
    assert summary.reference_valid is False
    assert summary.duplicate_timestamps == 1
    assert "duplicate_reference_minutes" in summary.rejection_reasons
    assert "incomplete_reference_regular_session" in summary.rejection_reasons


def test_reference_summary_rejects_negative_volume():
    rows = [{"timestamp": "2024-01-02T09:30:00", "symbol": "SPY", "volume": "-1"}]
    summary = summarize_reference_rows(rows, policy=policy(expected_bars=1))[0]
    assert summary.reference_valid is False
    assert summary.invalid_rows == 1
    assert "invalid_reference_rows" in summary.rejection_reasons


def test_reconciliation_preserves_exact_five_percent_boundary():
    reference = summarize_reference_rows(
        [
            {"timestamp": "2024-01-02T09:30:00", "symbol": "SPY", "volume": "400"},
            {"timestamp": "2024-01-02T16:00:00", "symbol": "SPY", "volume": "600"},
        ],
        policy=policy(),
    )[0]
    passing = reconcile_session_volume(
        observed_source="Alpaca SIP minute bars",
        observed_bars=2,
        observed_volume=1050,
        reference=reference,
    )
    failing = reconcile_session_volume(
        observed_source="Alpaca SIP minute bars",
        observed_bars=2,
        observed_volume=1050.01,
        reference=reference,
    )
    assert passing["relative_difference"] == pytest.approx(0.05)
    assert passing["eligible"] is True
    assert failing["eligible"] is False
    assert "material_volume_difference" in failing["rejection_reasons"]


def test_invalid_reference_is_never_reconciled_or_filled():
    reference = summarize_reference_rows(
        [{"timestamp": "2024-01-02T09:30:00", "symbol": "SPY", "volume": "1000"}],
        policy=policy(),
    )[0]
    result = reconcile_session_volume(
        observed_source="Alpaca SIP minute bars",
        observed_bars=2,
        observed_volume=1000,
        reference=reference,
    )
    assert result["eligible"] is False
    assert result["relative_difference"] is None
    assert result["price_substitution_allowed"] is False
    assert result["volume_scaling_allowed"] is False


def test_manifest_validation_enforces_reference_only_boundary():
    valid = {
        "allowed_use": REFERENCE_ALLOWED_USE,
        "price_substitution_allowed": False,
        "volume_scaling_allowed": False,
        "sessions": [],
    }
    validate_reference_manifest(valid)
    with pytest.raises(ValueError, match="price substitution"):
        validate_reference_manifest({**valid, "price_substitution_allowed": True})
    with pytest.raises(ValueError, match="volume scaling"):
        validate_reference_manifest({**valid, "volume_scaling_allowed": True})


def test_importer_builds_hashed_reference_only_manifest(tmp_path: Path, monkeypatch):
    module = load_script(IMPORT_SCRIPT, "reference_volume_importer")
    monkeypatch.setattr(module, "ROOT", tmp_path)
    raw = tmp_path / "reference.csv"
    raw.write_text(
        "timestamp,symbol,volume,close\n"
        "2024-01-02T09:30:00,SPY,400,999\n"
        "2024-01-02T16:00:00,SPY,600,0\n",
        encoding="utf-8",
    )
    manifest = module.build_manifest(
        provider="Independent Test Feed",
        paths=[raw],
        policy=policy(),
        delimiter=",",
    )
    assert manifest["allowed_use"] == REFERENCE_ALLOWED_USE
    assert manifest["price_substitution_allowed"] is False
    assert manifest["volume_scaling_allowed"] is False
    assert manifest["raw_files"][0]["sha256"]
    assert manifest["sessions"][0]["regular_volume"] == 1000
    assert "close" not in manifest["sessions"][0]


def test_reconciler_uses_frozen_manifest_and_does_not_patch_prices(monkeypatch):
    module = load_script(RECONCILE_SCRIPT, "reference_volume_reconciler")
    reference = summarize_reference_rows(
        [
            {"timestamp": "2024-01-02T09:30:00", "symbol": "SPY", "volume": "400"},
            {"timestamp": "2024-01-02T16:00:00", "symbol": "SPY", "volume": "600"},
        ],
        policy=policy(),
    )[0]
    manifest = {
        "provider": "Independent Test Feed",
        "allowed_use": REFERENCE_ALLOWED_USE,
        "price_substitution_allowed": False,
        "volume_scaling_allowed": False,
        "policy": policy().to_dict(),
        "sessions": [reference.to_dict()],
    }
    monkeypatch.setattr(
        module,
        "observed_sessions",
        lambda **_: {"SPY": {"bars": 2, "volume": 1025.0}},
    )
    result = module.reconcile_manifest(manifest=manifest, alpaca_root=Path("unused"))
    assert result["pass_count"] == 1
    assert result["price_source"] == "alpaca"
    assert result["price_substitution_allowed"] is False
    assert result["vendor_patch_into_price_data_allowed"] is False
    assert result["full_volume_gate_changed"] is False


def test_raw_reference_file_must_live_under_reference_tree(tmp_path: Path):
    root = tmp_path / "data" / "reference_volume" / "raw"
    root.mkdir(parents=True)
    accepted = root / "sample.csv"
    accepted.write_text("timestamp,symbol,volume\n", encoding="utf-8")
    assert ensure_raw_reference_path(accepted, root) == accepted.resolve()

    outside = tmp_path / "outside.csv"
    outside.write_text("timestamp,symbol,volume\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must be stored"):
        ensure_raw_reference_path(outside, root)
