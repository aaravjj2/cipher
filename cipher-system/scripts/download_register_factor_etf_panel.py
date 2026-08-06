#!/usr/bin/env python3
"""Download and register a survivorship-reduced factor/macro ETF panel.

The universe is fixed before download and contains broad-market, factor,
sector, international, bond, credit, real-asset, and defensive ETFs.  The data
is exploratory development evidence, not an untouched holdout.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.research_platform.factor_rotation import default_factor_rotation_specs  # noqa: E402
from core.research_platform.hashing import sha256_file, stable_id  # noqa: E402
from core.research_platform.models import DataDisposition, DatasetManifest, RawObjectManifest  # noqa: E402
from core.research_platform.registry import ResearchRegistry  # noqa: E402
from download_register_broad_equity_panel import download, validate_slice  # noqa: E402

UTC = timezone.utc
REGISTRY_PATH = ROOT / "data" / "governance" / "research_registry.sqlite"
OUTPUT_ROOT = ROOT / "data" / "historical_equities" / "factor_etf_panel_v1"
DATASET_NAME = "alpaca_factor_macro_etf_daily_2016_2026_development_v1"
START = "2016-01-04"
END = "2026-08-04"
ROLE = "factor_macro_etf_rotation_development_only_not_independent_holdout"
SYMBOLS = (
    "SPY", "QQQ", "IWM", "DIA", "RSP", "MTUM", "QUAL", "USMV", "VTV", "VUG", "IWF", "IWD", "SPLV",
    "XLK", "XLF", "XLE", "XLV", "XLY", "XLP", "XLI", "XLU", "XLB", "XLRE", "XBI", "SMH",
    "EFA", "EEM",
    "TLT", "IEF", "SHY", "BIL", "HYG", "LQD",
    "GLD", "SLV", "DBC", "DBA", "USO", "VNQ", "GDX", "UUP",
)
CATEGORIES = {
    "equity_core": ("SPY", "QQQ", "IWM", "DIA", "RSP", "MTUM", "QUAL", "USMV", "VTV", "VUG", "IWF", "IWD", "SPLV"),
    "sectors": ("XLK", "XLF", "XLE", "XLV", "XLY", "XLP", "XLI", "XLU", "XLB", "XLRE", "XBI", "SMH"),
    "international": ("EFA", "EEM"),
    "bonds": ("TLT", "IEF", "SHY", "BIL", "HYG", "LQD"),
    "real_assets": ("GLD", "SLV", "DBC", "DBA", "USO", "VNQ", "GDX", "UUP"),
    "defensive": ("TLT", "IEF", "SHY", "BIL", "GLD", "UUP", "LQD"),
}


def main() -> int:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    created_at = datetime.now(UTC)
    specs = default_factor_rotation_specs()
    strategy_grid = [
        {
            "strategy_id": spec.strategy_id,
            "name": spec.name,
            "mode": spec.mode,
            "lookback": spec.lookback,
            "skip": spec.skip,
            "top_k": spec.top_k,
            "rebalance": spec.rebalance,
            "score_type": spec.score_type,
            "absolute_momentum": spec.absolute_momentum,
            "trend_filter": spec.trend_filter,
            "defensive_symbol": spec.defensive_symbol,
            "core_weight": spec.core_weight,
            "target_volatility": spec.target_volatility,
        }
        for spec in specs
    ]
    freeze_hash = stable_id(
        "factor_rotation_grid_pre_download",
        {"symbols": SYMBOLS, "categories": CATEGORIES, "strategies": strategy_grid},
        length=64,
    )
    frame, raw_pages = download(OUTPUT_ROOT, START, END, symbols=SYMBOLS)
    quality = validate_slice(frame, START, END)
    observed_symbols = set(frame["ticker"].unique())
    missing = sorted(set(SYMBOLS) - observed_symbols)
    if missing:
        quality["passed"] = False
        quality.setdefault("failures", []).append("requested_symbols_missing")
        quality["missing_symbols"] = missing
    if not quality["passed"]:
        raise RuntimeError(f"factor ETF panel quality failed: {quality['failures']}")

    normalized = OUTPUT_ROOT / "normalized"
    normalized.mkdir(parents=True, exist_ok=True)
    parquet_path = normalized / f"{DATASET_NAME}.parquet"
    frame.to_parquet(parquet_path, index=False)
    checksum = sha256_file(parquet_path)
    registry = ResearchRegistry(REGISTRY_PATH)
    raw = RawObjectManifest(
        source="Alpaca SIP adjusted daily bars",
        dataset=DATASET_NAME,
        uri=parquet_path.resolve().as_uri(),
        checksum=checksum,
        checksum_method="sha256",
        size_bytes=parquet_path.stat().st_size,
        received_at=created_at,
        available_at=created_at,
        ingestion_run_id=stable_id("factor_etf_panel_run", {"created_at": created_at.isoformat(), "freeze_hash": freeze_hash}),
        content_type="application/x-parquet",
        event_time_start=pd.Timestamp(START, tz="UTC").to_pydatetime(),
        event_time_end=pd.Timestamp(END + "T23:59:59", tz="UTC").to_pydatetime(),
        request_metadata={
            "provider_raw_pages": raw_pages,
            "symbols_requested": list(SYMBOLS),
            "categories": {key: list(value) for key, value in CATEGORIES.items()},
            "strategy_grid_pre_download": strategy_grid,
            "strategy_grid_freeze_hash": freeze_hash,
            "normalizer_sha256": sha256_file(Path(__file__)),
            "adjustment": "all",
            "feed": "sip",
            "research_role": ROLE,
        },
        disposition=DataDisposition.FROZEN_SNAPSHOT,
    )
    with registry.connect() as db:
        existing = db.execute(
            "select raw_object_id from raw_objects where checksum=? and uri=?",
            (raw.checksum, raw.uri),
        ).fetchone()
    if existing:
        raw_object_id = str(existing[0])
    else:
        registry.register_raw_object(raw)
        raw_object_id = raw.raw_object_id
    dataset = DatasetManifest(
        name=DATASET_NAME,
        created_at=created_at,
        availability_cutoff=created_at,
        sources=("Alpaca SIP adjusted daily bars",),
        raw_object_ids=(raw_object_id,),
        symbol_universe_id="factor_macro_etf_panel_v1",
        corporate_action_version="alpaca_adjustment_all_2026_08_04",
        normalizer_version=f"download_register_factor_etf_panel.py:sha256:{sha256_file(Path(__file__))}",
        schema_name="daily_adjusted_ohlcv_v1",
        row_counts={
            "rows": len(frame),
            "sessions": frame["timestamp"].dt.date.nunique(),
            "symbols": frame["ticker"].nunique(),
            "raw_pages": len(raw_pages),
        },
        quality_checks={
            **quality,
            "research_role": ROLE,
            "strategy_grid_frozen_before_download": True,
            "strategy_grid_freeze_hash": freeze_hash,
            "strategy_count": len(specs),
            "single_stock_survivorship_dependency": False,
            "adaptive_search_allowed": False,
            "independent_holdout": False,
            "automatic_promotion": False,
            "execution_authority": False,
        },
        frozen=True,
    )
    registry.register_dataset(dataset)
    payload = {
        "schema_version": 1,
        "created_at": created_at.isoformat(),
        "status": "completed",
        "dataset_id": dataset.dataset_id,
        "dataset_name": dataset.name,
        "path": str(parquet_path),
        "sha256": checksum,
        "rows": int(len(frame)),
        "sessions": int(frame["timestamp"].dt.date.nunique()),
        "symbols": int(frame["ticker"].nunique()),
        "raw_pages": len(raw_pages),
        "research_role": ROLE,
        "strategy_grid_freeze_hash": freeze_hash,
        "strategy_count": len(specs),
        "categories": {key: list(value) for key, value in CATEGORIES.items()},
        "automatic_promotion": False,
        "paper_or_live_execution": False,
        "execution_authority": False,
    }
    report = OUTPUT_ROOT / "registration.json"
    report.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
