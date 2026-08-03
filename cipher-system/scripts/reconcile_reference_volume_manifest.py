#!/usr/bin/env python3
"""Reconcile Alpaca regular-session minute volume against a frozen reference.

The reference manifest is verification-only.  This script never substitutes
reference prices or bars into the Alpaca dataset and never changes the existing
5% full-gate threshold.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Mapping
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.research_platform.reference_volume import (  # noqa: E402
    reference_summary_from_mapping,
    reconcile_session_volume,
    validate_reference_manifest,
)

DEFAULT_ALPACA_ROOT = ROOT / "data" / "normalized" / "alpaca_sip_holdout_c_1m"
DEFAULT_OUTPUT_ROOT = ROOT / "data" / "market_quality"
NY = ZoneInfo("America/New_York")


def load_manifest(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    validate_reference_manifest(payload)
    return payload


def _session_policy(manifest: Mapping[str, object]) -> dict[str, object]:
    policy = manifest.get("policy")
    if not isinstance(policy, Mapping):
        raise ValueError("manifest policy is missing")
    session = policy.get("session")
    if not isinstance(session, Mapping):
        raise ValueError("manifest session policy is missing")
    return dict(session)


def alpaca_daily_path(root: Path, session_date: str) -> Path:
    return root / f"year={session_date[:4]}" / f"month={session_date[5:7]}" / f"{session_date}.parquet"


def observed_sessions(
    *,
    alpaca_root: Path,
    session_date: str,
    session_policy: Mapping[str, object],
) -> dict[str, dict[str, float | int]]:
    path = alpaca_daily_path(alpaca_root, session_date)
    if not path.is_file():
        return {}
    frame = pd.read_parquet(path, columns=["timestamp", "ticker", "volume"])
    stamp = pd.to_datetime(frame["timestamp"], utc=True).dt.tz_convert(NY)
    local_time = stamp.dt.time
    start = time.fromisoformat(str(session_policy["start"]))
    end = time.fromisoformat(str(session_policy["end"]))
    if bool(session_policy.get("end_inclusive", True)):
        mask = (stamp.dt.date.astype(str) == session_date) & (local_time >= start) & (local_time <= end)
    else:
        mask = (stamp.dt.date.astype(str) == session_date) & (local_time >= start) & (local_time < end)
    regular = frame.loc[mask].copy()
    if regular.empty:
        return {}
    grouped = regular.groupby("ticker", sort=True)["volume"].agg(["size", "sum"])
    return {
        str(symbol).upper(): {"bars": int(row["size"]), "volume": float(row["sum"])}
        for symbol, row in grouped.iterrows()
    }


def reconcile_manifest(
    *,
    manifest: Mapping[str, object],
    alpaca_root: Path,
    max_relative_difference: float = 0.05,
) -> dict[str, object]:
    session_policy = _session_policy(manifest)
    by_date: dict[str, dict[str, dict[str, float | int]]] = {}
    results: list[dict[str, object]] = []
    for row in manifest["sessions"]:  # type: ignore[index]
        if not isinstance(row, Mapping):
            raise ValueError("manifest session row is not an object")
        reference = reference_summary_from_mapping(row)
        if reference.session_date not in by_date:
            by_date[reference.session_date] = observed_sessions(
                alpaca_root=alpaca_root,
                session_date=reference.session_date,
                session_policy=session_policy,
            )
        observed = by_date[reference.session_date].get(reference.symbol)
        if observed is None:
            result = reconcile_session_volume(
                observed_source="Alpaca SIP minute bars",
                observed_bars=0,
                observed_volume=0.0,
                reference=reference,
                max_relative_difference=max_relative_difference,
            )
            result["rejection_reasons"] = [*result["rejection_reasons"], "missing_observed_alpaca_session"]
            result["eligible"] = False
        else:
            result = reconcile_session_volume(
                observed_source="Alpaca SIP minute bars",
                observed_bars=int(observed["bars"]),
                observed_volume=float(observed["volume"]),
                reference=reference,
                max_relative_difference=max_relative_difference,
            )
        results.append(result)

    valid_comparisons = [
        item
        for item in results
        if item["reference_valid"] and item["observed_session_complete"]
    ]
    passed = [item for item in results if item["eligible"]]
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "observed_source": "Alpaca SIP minute bars",
        "reference_source": manifest["provider"],
        "reference_purpose": "verification_only",
        "price_source": "alpaca",
        "price_substitution_allowed": False,
        "vendor_patch_into_price_data_allowed": False,
        "volume_scaling_allowed": False,
        "daily_bar_reference_allowed": False,
        "max_relative_difference": max_relative_difference,
        "results": results,
        "total_cases": len(results),
        "valid_comparisons": len(valid_comparisons),
        "pass_count": len(passed),
        "fail_count": len(results) - len(passed),
        "pass_rate": (len(passed) / len(valid_comparisons)) if valid_comparisons else None,
        "status": "reference_reconciliation_complete" if results else "no_reference_sessions",
        "full_volume_gate_changed": False,
        "holdout_c_changed": False,
        "trading_or_signal_evaluation": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--alpaca-root", type=Path, default=DEFAULT_ALPACA_ROOT)
    parser.add_argument("--max-relative-difference", type=float, default=0.05)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    manifest_path = args.manifest if args.manifest.is_absolute() else ROOT / args.manifest
    alpaca_root = args.alpaca_root if args.alpaca_root.is_absolute() else ROOT / args.alpaca_root
    manifest = load_manifest(manifest_path)
    payload = reconcile_manifest(
        manifest=manifest,
        alpaca_root=alpaca_root,
        max_relative_difference=args.max_relative_difference,
    )
    output = args.output or DEFAULT_OUTPUT_ROOT / f"reference_volume_reconciliation_{str(manifest['provider']).lower().replace(' ', '_')}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    if not output.is_absolute():
        output = ROOT / output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
