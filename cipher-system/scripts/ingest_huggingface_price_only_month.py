#!/usr/bin/env python3
"""Fetch and audit one immutable monthly Hugging Face OHLCV file.

The upstream archive is useful for supplementary price-only research.  It is
never a replacement for the frozen Alpaca panel and must never be used as the
independent volume reference until its volume coverage is separately proven.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import duckdb
from huggingface_hub import HfApi, hf_hub_download

ROOT = Path(__file__).resolve().parents[1]
DATASET = "mito0o852/OHLCV-1m"
FROZEN_SYMBOLS = ("SPY", "QQQ", "IWM", "XLF", "XLE", "AAPL", "MSFT", "NVDA", "GE")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--month", required=True, help="YYYY-MM, one public monthly file")
    parser.add_argument("--destination", type=Path, default=ROOT / "data" / "market_raw" / "huggingface_ohlcv_1m")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if len(args.month) != 7 or args.month[4] != "-":
        raise SystemExit("--month must be YYYY-MM")
    filename = f"data/ohlcv_{args.month}.parquet"
    revision = HfApi().dataset_info(DATASET).sha
    target = hf_hub_download(
        repo_id=DATASET,
        repo_type="dataset",
        filename=filename,
        revision=revision,
        local_dir=args.destination,
    )
    raw = Path(target)
    with duckdb.connect(":memory:") as db:
        placeholders = ", ".join("?" for _ in FROZEN_SYMBOLS)
        rows = db.execute(
            f"""
            select ticker, count(*) as bars, min(timestamp) as first_timestamp,
                   max(timestamp) as last_timestamp
            from read_parquet(?)
            where ticker in ({placeholders})
            group by ticker order by ticker
            """,
            [str(raw), *FROZEN_SYMBOLS],
        ).fetchall()
    present = {row[0] for row in rows}
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": {"dataset": DATASET, "revision": revision, "file": filename, "sha256": sha256(raw)},
        "raw_path": str(raw.relative_to(ROOT)),
        "month": args.month,
        "coverage": [
            {"ticker": ticker, "bars": int(bars), "first_timestamp": str(first), "last_timestamp": str(last)}
            for ticker, bars, first, last in rows
        ],
        "missing_frozen_symbols": sorted(set(FROZEN_SYMBOLS) - present),
        "allowed_use": "supplemental_price_only_research_no_volume_features",
        "prohibited": [
            "holdout_c_cohort_replacement_or_mixing",
            "independent_volume_reference",
            "volume_sensitive_backtesting",
            "promotion_or_trading",
        ],
    }
    output = ROOT / "data" / "governance" / f"huggingface_price_only_{args.month}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(output)


if __name__ == "__main__":
    main()
