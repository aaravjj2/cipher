#!/usr/bin/env python3
"""Register and summarize immutable reference-only minute-volume CSV files.

The importer never adds provider prices to Cipher's Alpaca dataset.  It reads
only the configured timestamp, symbol, and volume fields, requires raw evidence
under data/reference_volume/raw, and emits a frozen reconciliation manifest.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.research_platform.reference_volume import (  # noqa: E402
    REFERENCE_ALLOWED_USE,
    REFERENCE_PRICE_SUBSTITUTION_ALLOWED,
    REFERENCE_VOLUME_SCALING_ALLOWED,
    ReferenceImportPolicy,
    RegularSessionSpec,
    ensure_raw_reference_path,
    summarize_reference_rows,
)

DEFAULT_SYMBOLS = ("SPY", "QQQ", "IWM", "XLF", "XLE", "AAPL", "MSFT", "NVDA", "GE")
RAW_ROOT = ROOT / "data" / "reference_volume" / "raw"
MANIFEST_ROOT = ROOT / "data" / "reference_volume" / "manifests"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def iter_csv_rows(paths: Iterable[Path], *, delimiter: str) -> Iterable[Mapping[str, object]]:
    for path in paths:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, delimiter=delimiter)
            if reader.fieldnames is None:
                raise ValueError(f"missing CSV header: {path}")
            yield from reader


def parse_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def build_manifest(
    *,
    provider: str,
    paths: list[Path],
    policy: ReferenceImportPolicy,
    delimiter: str,
) -> dict[str, object]:
    summaries = summarize_reference_rows(iter_csv_rows(paths, delimiter=delimiter), policy=policy)
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "provider": provider,
        "allowed_use": REFERENCE_ALLOWED_USE,
        "reference_purpose": "verification_only",
        "price_substitution_allowed": REFERENCE_PRICE_SUBSTITUTION_ALLOWED,
        "volume_scaling_allowed": REFERENCE_VOLUME_SCALING_ALLOWED,
        "daily_bar_reference_allowed": False,
        "vendor_patch_into_price_data_allowed": False,
        "policy": policy.to_dict(),
        "raw_files": [
            {
                "path": str(path.relative_to(ROOT)),
                "sha256": sha256(path),
                "bytes": path.stat().st_size,
            }
            for path in paths
        ],
        "sessions": [summary.to_dict() for summary in summaries],
        "session_count": len(summaries),
        "valid_session_count": sum(summary.reference_valid for summary in summaries),
        "invalid_session_count": sum(not summary.reference_valid for summary in summaries),
        "status": "ready_for_alpaca_reconciliation" if summaries and all(s.reference_valid for s in summaries) else "contains_invalid_reference_sessions",
        "full_volume_gate_changed": False,
        "trading_or_signal_evaluation": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", required=True)
    parser.add_argument("--input", type=Path, action="append", required=True)
    parser.add_argument("--timestamp-column", default="timestamp")
    parser.add_argument("--symbol-column", default="symbol")
    parser.add_argument("--volume-column", default="volume")
    parser.add_argument("--source-timezone", default="UTC")
    parser.add_argument("--timestamp-semantics", choices=("minute_start", "minute_end"), default="minute_start")
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--delimiter", default=",")
    parser.add_argument("--expected-bars", type=int, default=391)
    parser.add_argument("--session-end-exclusive", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    paths = [ensure_raw_reference_path(path, RAW_ROOT) for path in args.input]
    symbols = tuple(dict.fromkeys(symbol.strip().upper() for symbol in args.symbols.split(",") if symbol.strip()))
    policy = ReferenceImportPolicy(
        provider=args.provider,
        timestamp_column=args.timestamp_column,
        symbol_column=args.symbol_column,
        volume_column=args.volume_column,
        source_timezone=args.source_timezone,
        timestamp_semantics=args.timestamp_semantics,
        symbols=symbols,
        start_date=parse_date(args.start_date),
        end_date=parse_date(args.end_date),
        session=RegularSessionSpec(
            start=time(9, 30),
            end=time(16, 0),
            end_inclusive=not args.session_end_exclusive,
            expected_bars=args.expected_bars,
        ),
    )
    manifest = build_manifest(provider=args.provider, paths=paths, policy=policy, delimiter=args.delimiter)
    output = args.output or MANIFEST_ROOT / f"reference_volume_{args.provider.lower().replace(' ', '_')}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    if not output.is_absolute():
        output = ROOT / output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
