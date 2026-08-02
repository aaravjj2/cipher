"""Targeted Alpaca historical option archive for the EOD pattern lab.

This read-only downloader enumerates listed contracts for SPY, QQQ, or IWM,
selects a compact point-in-time strike set around four late-session checkpoints,
and downloads only the decision session plus the following trading session.
That is enough for every holding period in :mod:`eod_pattern_lab` without
archiving the entire contract life.

Historical NBBO quotes are not available. The archive contains one-minute OPRA
trade bars and immutable compressed provider pages. Selection uses only the
underlying prices available by each checkpoint; current metadata fields are not
used as historical liquidity features.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable, Sequence

from eod_pattern_lab import DEFAULT_DB as DEFAULT_EQUITY_DB
from eod_pattern_lab import SYMBOLS, load_sessions
from historical_options_download import (
    AlpacaHistoricalOptionsDownloader,
    ContractSelection,
    HistoricalOptionsStore,
    JsonHttpClient,
    alpaca_credentials,
    chunks,
    iso_utc,
    market_window,
    utcnow,
)


CORE = Path(__file__).resolve().parent
CIPHER_ROOT = CORE.parent
DEFAULT_ROOT = CIPHER_ROOT / "data" / "historical_options" / "eod_indices_targeted"
ANALYSIS_START = date(2026, 1, 26)
ANALYSIS_END = date(2026, 7, 24)
CHECKPOINT_FIELDS = ("price_1500", "price_1530", "price_1545", "close")


@dataclass(frozen=True, slots=True)
class ExpiryBucket:
    name: str
    min_dte: int
    max_dte: int
    target_dte: int
    friday_only: bool = False


BUCKETS: tuple[ExpiryBucket, ...] = (
    ExpiryBucket("0dte", 0, 0, 0),
    ExpiryBucket("front", 1, 3, 2),
    # Longer-dated daily expirations are frequently present in today's contract
    # catalog even though they were not listed on the historical decision date.
    # Established Friday weeklies/monthlies provide point-in-time observable bars.
    ExpiryBucket("weekly", 3, 10, 5, True),
    ExpiryBucket("swing", 10, 23, 17, True),
)


@dataclass(frozen=True, slots=True)
class EodSelection:
    decision_date: str
    next_session_date: str | None
    underlying: str
    bucket: str
    checkpoint: str
    target_style: str
    option_type: str
    symbol: str
    expiration_date: str
    strike: float
    dte: int
    checkpoint_spot: float
    target_moneyness: float
    actual_moneyness: float


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _contract_rows_by_expiry(
    contracts: Iterable[dict[str, Any]], underlying: str
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    index: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in contracts:
        symbol = str(row.get("symbol") or "").upper().strip()
        expiry = str(row.get("expiration_date") or "")[:10]
        option_type = str(row.get("type") or "").lower()
        strike = _finite(row.get("strike_price"))
        row_underlying = str(row.get("underlying_symbol") or "").upper()
        if (
            not symbol
            or not expiry
            or option_type not in {"call", "put"}
            or strike is None
            or row_underlying != underlying
        ):
            continue
        normalized = dict(row)
        normalized["symbol"] = symbol
        normalized["expiration_date"] = expiry
        normalized["type"] = option_type
        normalized["strike_price"] = strike
        index.setdefault((expiry, option_type), []).append(normalized)
    for values in index.values():
        values.sort(key=lambda row: (float(row["strike_price"]), str(row["symbol"])))
    return index


def _choose_expiry(
    decision_day: date,
    available: Iterable[str],
    bucket: ExpiryBucket,
) -> str | None:
    candidates: list[tuple[int, str]] = []
    for raw in available:
        try:
            expiry = date.fromisoformat(raw)
        except ValueError:
            continue
        dte = (expiry - decision_day).days
        if bucket.friday_only and expiry.weekday() != 4:
            continue
        if bucket.min_dte <= dte <= bucket.max_dte:
            candidates.append((dte, raw))
    if not candidates:
        return None
    return min(candidates, key=lambda item: (abs(item[0] - bucket.target_dte), item[0], item[1]))[1]


def _nearest_contract(
    rows: Sequence[dict[str, Any]], target_strike: float
) -> dict[str, Any] | None:
    if not rows:
        return None
    return min(
        rows,
        key=lambda row: (
            abs(float(row["strike_price"]) - target_strike),
            abs(float(row["strike_price"])),
            str(row["symbol"]),
        ),
    )


def build_selections(
    *,
    underlying: str,
    sessions: Sequence[dict[str, Any]],
    contract_index: dict[tuple[str, str], list[dict[str, Any]]],
) -> list[EodSelection]:
    output: list[EodSelection] = []
    available_expiries = sorted({expiry for expiry, _kind in contract_index})
    day_to_next = {
        row["day"]: sessions[index + 1]["day"] if index + 1 < len(sessions) else None
        for index, row in enumerate(sessions)
    }
    for row in sessions:
        decision_day = date.fromisoformat(row["day"])
        if not ANALYSIS_START <= decision_day <= ANALYSIS_END:
            continue
        next_day = day_to_next[row["day"]]
        for bucket in BUCKETS:
            expiry = _choose_expiry(decision_day, available_expiries, bucket)
            if not expiry:
                continue
            dte = (date.fromisoformat(expiry) - decision_day).days
            for option_type in ("call", "put"):
                contracts = contract_index.get((expiry, option_type), [])
                if not contracts:
                    continue
                for checkpoint in CHECKPOINT_FIELDS:
                    spot = _finite(row.get(checkpoint))
                    if spot is None or spot <= 0:
                        continue
                    targets = (
                        ("atm", 1.0),
                        ("otm075", 1.0075 if option_type == "call" else 0.9925),
                    )
                    for target_style, target_moneyness in targets:
                        contract = _nearest_contract(contracts, spot * target_moneyness)
                        if not contract:
                            continue
                        strike = float(contract["strike_price"])
                        output.append(
                            EodSelection(
                                decision_date=row["day"],
                                next_session_date=next_day,
                                underlying=underlying,
                                bucket=bucket.name,
                                checkpoint=checkpoint,
                                target_style=target_style,
                                option_type=option_type,
                                symbol=str(contract["symbol"]),
                                expiration_date=expiry,
                                strike=strike,
                                dte=dte,
                                checkpoint_spot=spot,
                                target_moneyness=target_moneyness,
                                actual_moneyness=strike / spot,
                            )
                        )
    # The same contract can satisfy several checkpoint targets. Keep all mapping
    # rows but remove exact duplicates to keep the manifest deterministic.
    unique: dict[tuple[Any, ...], EodSelection] = {}
    for row in output:
        key = (
            row.decision_date,
            row.bucket,
            row.checkpoint,
            row.target_style,
            row.option_type,
            row.symbol,
        )
        unique[key] = row
    return sorted(
        unique.values(),
        key=lambda row: (
            row.decision_date,
            row.bucket,
            row.option_type,
            row.checkpoint,
            row.target_style,
            row.strike,
        ),
    )


def ensure_eod_schema(store: HistoricalOptionsStore) -> None:
    with store.connect() as db:
        db.executescript(
            """
            create table if not exists eod_contract_selections (
                decision_date text not null,
                next_session_date text,
                underlying text not null,
                bucket text not null,
                checkpoint text not null,
                target_style text not null,
                option_type text not null,
                symbol text not null,
                expiration_date text not null,
                strike real not null,
                dte integer not null,
                checkpoint_spot real not null,
                target_moneyness real not null,
                actual_moneyness real not null,
                selected_at text not null,
                primary key(
                    decision_date,bucket,checkpoint,target_style,option_type,symbol
                )
            );
            create index if not exists eod_selection_lookup
                on eod_contract_selections(decision_date,bucket,option_type,checkpoint);
            """
        )


def save_eod_selections(
    store: HistoricalOptionsStore, selections: Sequence[EodSelection]
) -> None:
    ensure_eod_schema(store)
    selected_at = iso_utc(utcnow())
    with store.connect() as db:
        db.execute("delete from eod_contract_selections")
        db.executemany(
            """insert into eod_contract_selections
               (decision_date,next_session_date,underlying,bucket,checkpoint,
                target_style,option_type,symbol,expiration_date,strike,dte,
                checkpoint_spot,target_moneyness,actual_moneyness,selected_at)
               values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                (
                    row.decision_date,
                    row.next_session_date,
                    row.underlying,
                    row.bucket,
                    row.checkpoint,
                    row.target_style,
                    row.option_type,
                    row.symbol,
                    row.expiration_date,
                    row.strike,
                    row.dte,
                    row.checkpoint_spot,
                    row.target_moneyness,
                    row.actual_moneyness,
                    selected_at,
                )
                for row in selections
            ],
        )

    # Populate the generic selection table as a compatibility view for existing
    # option research utilities. Rank is deterministic within each day.
    generic: list[ContractSelection] = []
    grouped: dict[str, dict[str, EodSelection]] = {}
    for row in selections:
        grouped.setdefault(row.decision_date, {})[row.symbol] = row
    for decision_date, symbol_rows in sorted(grouped.items()):
        for rank, row in enumerate(
            sorted(symbol_rows.values(), key=lambda item: (item.dte, item.option_type, item.strike)),
            start=1,
        ):
            generic.append(
                ContractSelection(
                    decision_date=decision_date,
                    symbol=row.symbol,
                    expiration_date=row.expiration_date,
                    strike=row.strike,
                    option_type=row.option_type,
                    spot=row.checkpoint_spot,
                    dte=row.dte,
                    moneyness=row.actual_moneyness,
                    rank=rank,
                )
            )
    store.save_selections(generic, sorted(grouped))


def write_selection_csv(root: Path, selections: Sequence[EodSelection]) -> Path:
    path = root / "eod_contract_selections.csv"
    fields = list(asdict(selections[0]).keys()) if selections else [
        "decision_date",
        "next_session_date",
        "underlying",
        "bucket",
        "checkpoint",
        "target_style",
        "option_type",
        "symbol",
        "expiration_date",
        "strike",
        "dte",
        "checkpoint_spot",
        "target_moneyness",
        "actual_moneyness",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(asdict(row) for row in selections)
    return path


def run_archive(args: argparse.Namespace) -> dict[str, Any]:
    underlying = str(args.symbol).upper()
    if underlying not in SYMBOLS:
        raise ValueError(f"symbol must be one of {SYMBOLS}")
    equity_db = Path(args.equity_db or DEFAULT_EQUITY_DB).resolve()
    sessions_by_symbol = load_sessions(equity_db)
    sessions = [
        row
        for row in sessions_by_symbol[underlying]
        if ANALYSIS_START <= date.fromisoformat(row["day"]) <= ANALYSIS_END
    ]
    if not sessions:
        raise RuntimeError(f"no complete sessions found for {underlying}")

    key, secret, _stock_feed = alpaca_credentials()
    client = JsonHttpClient(
        {
            "APCA-API-KEY-ID": key,
            "APCA-API-SECRET-KEY": secret,
            "Accept": "application/json",
            "User-Agent": "Cipher-EOD-Option-Archive/1.0",
        },
        timeout=args.timeout,
        retries=args.retries,
    )
    root = Path(args.output_root or (DEFAULT_ROOT / underlying.lower())).resolve()
    store = HistoricalOptionsStore(root)
    downloader = AlpacaHistoricalOptionsDownloader(store, client)
    config = {
        "underlying": underlying,
        "start_date": ANALYSIS_START.isoformat(),
        "end_date": ANALYSIS_END.isoformat(),
        "analysis_start": ANALYSIS_START.isoformat(),
        "analysis_end": ANALYSIS_END.isoformat(),
        "equity_db": str(equity_db),
        "buckets": [asdict(bucket) for bucket in BUCKETS],
        "checkpoints": list(CHECKPOINT_FIELDS),
        "target_styles": {"atm": 1.0, "otm075_call": 1.0075, "otm075_put": 0.9925},
        "download_scope": "decision session plus following trading session only",
        "include_trades": False,
        "resume": bool(args.resume),
    }
    run_id = store.start_run(config)
    summary: dict[str, Any] = {"run_id": run_id, "config": config}
    try:
        expiry_end = ANALYSIS_END + timedelta(days=max(bucket.max_dte for bucket in BUCKETS) + 3)
        contracts = downloader.enumerate_contracts(
            run_id,
            underlying,
            ANALYSIS_START.isoformat(),
            expiry_end.isoformat(),
        )
        contract_index = _contract_rows_by_expiry(contracts, underlying)
        selections = build_selections(
            underlying=underlying,
            sessions=sessions,
            contract_index=contract_index,
        )
        if not selections:
            raise RuntimeError("no EOD contracts selected")
        save_eod_selections(store, selections)
        selection_csv = write_selection_csv(root, selections)

        by_window: dict[tuple[str, str], set[str]] = {}
        for row in selections:
            start_day = date.fromisoformat(row.decision_date)
            end_day = (
                date.fromisoformat(row.next_session_date)
                if row.next_session_date
                else start_day
            )
            start_at, end_at = market_window(start_day, end_day)
            by_window.setdefault((start_at, end_at), set()).add(row.symbol)

        results: list[dict[str, Any]] = []
        for (start_at, end_at), symbols in sorted(by_window.items()):
            for batch in chunks(sorted(symbols), args.batch_size):
                result = downloader.download_option_window(
                    run_id=run_id,
                    kind="bars",
                    symbols=batch,
                    start_at=start_at,
                    end_at=end_at,
                    timeframe="1Min",
                    resume=args.resume,
                )
                results.append(result)

        with store.connect() as db:
            observed_rows = int(
                db.execute("select count(*) from option_bars").fetchone()[0]
            )
            observed_symbols = int(
                db.execute("select count(distinct symbol) from option_bars").fetchone()[0]
            )
            selection_count = int(
                db.execute("select count(*) from eod_contract_selections").fetchone()[0]
            )
            selected_symbols = int(
                db.execute("select count(distinct symbol) from eod_contract_selections").fetchone()[0]
            )
            bucket_counts = {
                row[0]: int(row[1])
                for row in db.execute(
                    "select bucket,count(*) from eod_contract_selections group by bucket order by bucket"
                )
            }
        summary.update(
            {
                "contracts_enumerated": len(contracts),
                "contract_expiry_type_groups": len(contract_index),
                "sessions": len(sessions),
                "selection_rows": selection_count,
                "selected_unique_symbols": selected_symbols,
                "bucket_selection_rows": bucket_counts,
                "option_bar_rows": observed_rows,
                "observed_unique_symbols": observed_symbols,
                "download_calls": len(results),
                "download_rows_reported": sum(int(row.get("rows") or 0) for row in results),
                "selection_csv": str(selection_csv),
                "database": str(store.db_path),
                "research_grade": False,
                "research_grade_reason": (
                    "Historical NBBO is absent; the downstream lab uses conservative one-minute trade-bar execution proxies."
                ),
            }
        )
        store.finish_run(run_id, "complete", summary)
        (root / "eod_archive_manifest.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8"
        )
        return summary
    except Exception as exc:
        summary["error"] = str(exc)
        store.finish_run(run_id, "failed", summary, str(exc)[:1000])
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build the targeted SPY/QQQ/IWM EOD historical option archive."
    )
    parser.add_argument("--symbol", choices=SYMBOLS, required=True)
    parser.add_argument("--equity-db")
    parser.add_argument("--output-root")
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--retries", type=int, default=6)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    print(json.dumps(run_archive(args), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
