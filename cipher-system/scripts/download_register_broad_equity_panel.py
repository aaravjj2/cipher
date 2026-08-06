#!/usr/bin/env python3
"""Download and register a broad adjusted Alpaca SIP daily equity panel.

The download is split into two immutable canonical datasets:

* 2010-2019: locked temporal validation for candidates fixed before download.
* 2020-2022: broad development data for additional autonomous research.

The script downloads market data only. It has no account or order endpoints.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sys
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.historical_options_download import JsonHttpClient, alpaca_credentials  # noqa: E402
from core.research_platform.hashing import sha256_file, stable_id  # noqa: E402
from core.research_platform.models import DataDisposition, DatasetManifest, RawObjectManifest  # noqa: E402
from core.research_platform.registry import ResearchRegistry  # noqa: E402

DATA_URL = "https://data.alpaca.markets/v2/stocks/bars"
NY = ZoneInfo("America/New_York")
UTC = timezone.utc
DEFAULT_ROOT = ROOT / "data" / "historical_equities" / "broad_research_panel_v1"
REGISTRY_PATH = ROOT / "data" / "governance" / "research_registry.sqlite"
SYMBOLS = (
    "SPY", "QQQ", "IWM", "DIA",
    "XLK", "XLF", "XLE", "XLV", "XLY", "XLP", "XLI", "XLU", "XLB", "XLRE",
    "SMH", "SOXX", "TLT", "IEF", "HYG", "LQD", "GLD", "SLV", "USO", "VNQ", "EEM", "EFA",
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "JPM", "XOM", "UNH", "CAT", "COST", "WMT",
)
SLICES = (
    {
        "name": "alpaca_broad_daily_2016_2019_locked_validation_v1",
        "start": "2016-01-04",
        "end": "2019-12-31",
        "research_role": "locked_temporal_validation_fixed_pre_download_candidates_only",
    },
    {
        "name": "alpaca_broad_daily_2020_2022_development_v1",
        "start": "2020-01-01",
        "end": "2022-12-31",
        "research_role": "broad_phase3_development_only",
    },
)


def iso_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def load_server_env() -> None:
    env_path = ROOT / "app" / ".env"
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("ALPACA_"):
            os.environ.setdefault(key, value.strip().strip('"').strip("'"))


def download(
    output_root: Path,
    start: str,
    end: str,
    *,
    symbols: tuple[str, ...] = SYMBOLS,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    load_server_env()
    key, secret, feed = alpaca_credentials()
    client = JsonHttpClient(
        {
            "APCA-API-KEY-ID": key,
            "APCA-API-SECRET-KEY": secret,
            "Accept": "application/json",
            "User-Agent": "Cipher-Broad-Equity-Research/1.0",
        },
        timeout=90,
        retries=8,
    )
    start_at = datetime.combine(pd.Timestamp(start).date(), time(0, 0), tzinfo=NY).astimezone(UTC)
    end_at = datetime.combine(pd.Timestamp(end).date(), time(23, 59, 59), tzinfo=NY).astimezone(UTC)
    raw_root = output_root / "raw"
    raw_root.mkdir(parents=True, exist_ok=True)
    token: str | None = None
    page = 0
    rows: list[dict[str, Any]] = []
    raw_pages: list[dict[str, Any]] = []
    while True:
        query: dict[str, Any] = {
            "symbols": ",".join(symbols),
            "timeframe": "1Day",
            "start": iso_utc(start_at),
            "end": iso_utc(end_at),
            "limit": 10000,
            "adjustment": "all",
            "feed": feed,
            "sort": "asc",
        }
        if token:
            query["page_token"] = token
        payload, raw, status = client.get(DATA_URL, query)
        page += 1
        digest = sha256_bytes(raw)
        raw_path = raw_root / f"page_{page:04d}_{digest}.json.gz"
        if not raw_path.exists():
            raw_path.write_bytes(gzip.compress(raw, compresslevel=6))
        page_rows = 0
        for symbol, bars in sorted((payload.get("bars") or {}).items()):
            for bar in bars or []:
                rows.append(
                    {
                        "timestamp": str(bar["t"]),
                        "ticker": str(symbol).upper(),
                        "open": float(bar["o"]),
                        "high": float(bar["h"]),
                        "low": float(bar["l"]),
                        "close": float(bar["c"]),
                        "volume": float(bar.get("v") or 0.0),
                        "vwap": float(bar["vw"]) if bar.get("vw") is not None else None,
                        "trades": int(bar["n"]) if bar.get("n") is not None else None,
                    }
                )
                page_rows += 1
        raw_pages.append(
            {
                "page": page,
                "status": status,
                "path": str(raw_path),
                "sha256": digest,
                "rows": page_rows,
                "next_page_token_present": bool(payload.get("next_page_token")),
            }
        )
        token = payload.get("next_page_token")
        if not token:
            break
    frame = pd.DataFrame(rows)
    if frame.empty:
        raise RuntimeError("Alpaca returned no broad-panel daily bars")
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    frame = frame.sort_values(["timestamp", "ticker"]).drop_duplicates(["timestamp", "ticker"], keep="last")
    return frame.reset_index(drop=True), raw_pages


def validate_slice(frame: pd.DataFrame, expected_start: str, expected_end: str) -> dict[str, Any]:
    failures: list[str] = []
    required = {"timestamp", "ticker", "open", "high", "low", "close", "volume"}
    if not required.issubset(frame.columns):
        failures.append("missing_required_columns")
    if frame.empty:
        failures.append("empty")
    numeric = ["open", "high", "low", "close"]
    if not frame.empty and (frame[numeric].isna().any().any() or (frame[numeric] <= 0).any().any()):
        failures.append("invalid_ohlc")
    invalid_ohlc = (
        (frame["low"] > frame[["open", "close"]].min(axis=1))
        | (frame["high"] < frame[["open", "close"]].max(axis=1))
    ) if not frame.empty else pd.Series(dtype=bool)
    if not invalid_ohlc.empty and bool(invalid_ohlc.any()):
        failures.append("ohlc_relationship_failed")
    dates = frame["timestamp"].dt.date if not frame.empty else []
    observed_start = min(dates) if len(dates) else None
    observed_end = max(dates) if len(dates) else None
    expected_start_date = pd.Timestamp(expected_start).date()
    expected_end_date = pd.Timestamp(expected_end).date()
    if observed_start is not None and abs((observed_start - expected_start_date).days) > 7:
        failures.append("observed_start_outside_tolerance")
    if observed_end is not None and abs((expected_end_date - observed_end).days) > 7:
        failures.append("observed_end_outside_tolerance")
    symbols = sorted(frame["ticker"].unique().tolist()) if not frame.empty else []
    if "SPY" not in symbols or "QQQ" not in symbols or "IWM" not in symbols:
        failures.append("core_benchmark_symbols_missing")
    return {
        "passed": not failures,
        "failures": failures,
        "expected_start": expected_start,
        "expected_end": expected_end,
        "observed_start": str(observed_start) if observed_start else None,
        "observed_end": str(observed_end) if observed_end else None,
        "rows": int(len(frame)),
        "sessions": int(frame["timestamp"].dt.date.nunique()) if not frame.empty else 0,
        "symbols": symbols,
        "symbol_count": len(symbols),
        "duplicate_timestamp_symbol_rows": int(frame.duplicated(["timestamp", "ticker"]).sum()),
        "adjustment": "all",
        "feed": "sip",
        "point_in_time_daily_bars": True,
        "historical_nbbo": False,
    }


def register_slice(
    registry: ResearchRegistry,
    output_root: Path,
    full_frame: pd.DataFrame,
    slice_spec: dict[str, str],
    *,
    downloaded_at: datetime,
    raw_pages: list[dict[str, Any]],
    candidate_set_hash: str,
) -> dict[str, Any]:
    start = pd.Timestamp(slice_spec["start"], tz="UTC")
    end = pd.Timestamp(slice_spec["end"] + "T23:59:59", tz="UTC")
    frame = full_frame[(full_frame["timestamp"] >= start) & (full_frame["timestamp"] <= end)].copy()
    quality = validate_slice(frame, slice_spec["start"], slice_spec["end"])
    if not quality["passed"]:
        raise RuntimeError(f"dataset quality failed for {slice_spec['name']}: {quality['failures']}")
    normalized_root = output_root / "normalized"
    normalized_root.mkdir(parents=True, exist_ok=True)
    parquet_path = normalized_root / f"{slice_spec['name']}.parquet"
    frame.to_parquet(parquet_path, index=False)
    raw = RawObjectManifest(
        source="Alpaca SIP adjusted daily bars",
        dataset=slice_spec["name"],
        uri=parquet_path.resolve().as_uri(),
        checksum=sha256_file(parquet_path),
        checksum_method="sha256",
        size_bytes=parquet_path.stat().st_size,
        received_at=downloaded_at,
        available_at=downloaded_at,
        ingestion_run_id=stable_id("broad_panel_run", {"downloaded_at": downloaded_at.isoformat(), "slice": slice_spec["name"]}),
        content_type="application/x-parquet",
        event_time_start=start.to_pydatetime(),
        event_time_end=end.to_pydatetime(),
        request_metadata={
            "provider_raw_pages": raw_pages,
            "normalizer_sha256": sha256_file(Path(__file__)),
            "research_role": slice_spec["research_role"],
            "candidate_set_hash_before_download": candidate_set_hash,
            "symbols_requested": list(SYMBOLS),
            "adjustment": "all",
            "feed": "sip",
        },
        disposition=DataDisposition.FROZEN_SNAPSHOT,
    )
    with registry.connect() as db:
        existing_raw = db.execute(
            "select raw_object_id from raw_objects where checksum=? and uri=?",
            (raw.checksum, raw.uri),
        ).fetchone()
    if existing_raw:
        raw_object_id = str(existing_raw[0])
    else:
        registry.register_raw_object(raw)
        raw_object_id = raw.raw_object_id
    dataset = DatasetManifest(
        name=slice_spec["name"],
        created_at=downloaded_at,
        availability_cutoff=downloaded_at,
        sources=("Alpaca SIP adjusted daily bars",),
        raw_object_ids=(raw_object_id,),
        symbol_universe_id="broad_cross_asset_equity_etf_panel_v1",
        corporate_action_version="alpaca_adjustment_all_2026_08_04",
        normalizer_version=f"download_register_broad_equity_panel.py:sha256:{sha256_file(Path(__file__))}",
        schema_name="daily_adjusted_ohlcv_v1",
        row_counts={
            "rows": len(frame),
            "sessions": frame["timestamp"].dt.date.nunique(),
            "symbols": frame["ticker"].nunique(),
            "raw_pages": len(raw_pages),
        },
        quality_checks={
            **quality,
            "research_role": slice_spec["research_role"],
            "candidate_set_hash_before_download": candidate_set_hash,
            "adaptive_search_allowed": slice_spec["research_role"] == "broad_phase3_development_only",
            "automatic_promotion": False,
            "execution_authority": False,
        },
        frozen=True,
    )
    registry.register_dataset(dataset)
    return {
        "dataset_id": dataset.dataset_id,
        "name": dataset.name,
        "research_role": slice_spec["research_role"],
        "path": str(parquet_path),
        "sha256": raw.checksum,
        "rows": len(frame),
        "sessions": int(frame["timestamp"].dt.date.nunique()),
        "symbols": sorted(frame["ticker"].unique().tolist()),
        "quality": quality,
    }


def current_candidate_set_hash(registry: ResearchRegistry) -> tuple[str, int]:
    with registry.connect() as db:
        rows = db.execute(
            "select strategy_id, payload_json from strategies where name like 'autonomous_price_only_%' order by strategy_id"
        ).fetchall()
    return stable_id("candidate_set_pre_broad_download", [(row[0], row[1]) for row in rows], length=64), len(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2010-01-01")
    parser.add_argument("--end", default="2022-12-31")
    parser.add_argument("--output-root", default=str(DEFAULT_ROOT))
    args = parser.parse_args()
    output_root = Path(args.output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    registry = ResearchRegistry(REGISTRY_PATH)
    candidate_hash, candidate_count = current_candidate_set_hash(registry)
    downloaded_at = datetime.now(UTC)
    frame, raw_pages = download(output_root, args.start, args.end)
    datasets = [
        register_slice(
            registry,
            output_root,
            frame,
            spec,
            downloaded_at=downloaded_at,
            raw_pages=raw_pages,
            candidate_set_hash=candidate_hash,
        )
        for spec in SLICES
    ]
    payload = {
        "schema_version": 1,
        "created_at": downloaded_at.isoformat(),
        "status": "completed",
        "download": {
            "start": args.start,
            "end": args.end,
            "symbols_requested": list(SYMBOLS),
            "rows": len(frame),
            "pages": len(raw_pages),
            "raw_pages": raw_pages,
        },
        "candidate_set_frozen_before_download": {
            "strategy_count": candidate_count,
            "hash": candidate_hash,
        },
        "datasets": datasets,
        "automatic_promotion": False,
        "paper_or_live_execution": False,
        "execution_authority": False,
    }
    report = output_root / "broad_panel_registration.json"
    report.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
