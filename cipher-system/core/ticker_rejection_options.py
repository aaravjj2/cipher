"""Observed bid/ask checks for ticker-specific rejection candidates.

Only contracts and quotes already present in ``tradier_stream.sqlite`` are
used.  Missing coverage is reported, never synthesized.  This module has no
network or order endpoint.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping

import pandas as pd

from .oi_option_quote_confirmation import (
    DEFAULT_TRADIER_DB,
    _matching_run,
    _quote,
    load_stream_runs,
)
from .ticker_rejection_lab import DEFAULT_OUTPUT


def _intraday_contract_choices(
    contracts: Iterable[Mapping[str, Any]],
    *,
    spot: float,
    direction: int,
    signal_day: str,
) -> dict[str, dict[str, Any]]:
    kind = "call" if direction > 0 else "put"
    rows = [
        dict(row) for row in contracts
        if str(row.get("option_type") or "").lower() == kind
        and str(row.get("expiration") or "") >= signal_day
        and row.get("strike") is not None
    ]
    if not rows:
        return {}
    # Prefer the nearest expiration actually captured, then choose moneyness
    # within that expiry so ATM/ITM/OTM are comparable structures.
    expiration = min(str(row["expiration"]) for row in rows)
    rows = [row for row in rows if str(row["expiration"]) == expiration]
    rows.sort(key=lambda row: (abs(float(row["strike"]) - spot), str(row["symbol"])))
    atm = rows[0]
    if kind == "call":
        itm = sorted((r for r in rows if float(r["strike"]) < spot), key=lambda r: spot - float(r["strike"]))
        otm = sorted((r for r in rows if float(r["strike"]) > spot), key=lambda r: float(r["strike"]) - spot)
    else:
        itm = sorted((r for r in rows if float(r["strike"]) > spot), key=lambda r: float(r["strike"]) - spot)
        otm = sorted((r for r in rows if float(r["strike"]) < spot), key=lambda r: spot - float(r["strike"]))
    return {"atm": atm, "itm": itm[0] if itm else atm, "otm": otm[0] if otm else atm}


def confirm_trade_records(
    trade_records: Iterable[Mapping[str, Any]],
    *,
    tradier_db: str | Path = DEFAULT_TRADIER_DB,
) -> dict[str, Any]:
    trades = list(trade_records)
    dates = {str(row["date"]) for row in trades}
    runs = load_stream_runs(tradier_db, dates)
    connection = sqlite3.connect(f"file:{Path(tradier_db).resolve()}?mode=ro", uri=True)
    records: list[dict[str, Any]] = []
    skips: dict[str, int] = {}

    def skip(reason: str) -> None:
        skips[reason] = skips.get(reason, 0) + 1

    try:
        for trade in trades:
            ticker = str(trade["ticker"]).upper()
            signal_time = pd.Timestamp(trade["signal_timestamp"]).to_pydatetime()
            entry_time = pd.Timestamp(trade["entry_timestamp"]).to_pydatetime()
            exit_time = pd.Timestamp(trade["exit_timestamp"]).to_pydatetime()
            run = _matching_run(runs.get(str(trade["date"]), []), ticker, signal_time)
            if run is None:
                skip("ticker_not_in_nearby_selection_run")
                continue
            choices = _intraday_contract_choices(
                run.contracts_by_underlying[ticker],
                spot=float(trade["entry_price"]),
                direction=int(trade["direction"]),
                signal_day=str(trade["date"]),
            )
            if not choices:
                skip("no_matching_captured_contract")
                continue
            seen: set[str] = set()
            for selector, contract in choices.items():
                symbol = str(contract["symbol"])
                if symbol in seen:
                    continue
                seen.add(symbol)
                entry = _quote(connection, symbol, entry_time, entry_time + timedelta(minutes=10), ascending=True)
                exit_quote = _quote(connection, symbol, exit_time, exit_time + timedelta(minutes=10), ascending=True)
                if entry is None or exit_quote is None:
                    skip(f"missing_{selector}_entry_or_exit_quote")
                    continue
                debit = entry["ask"] * 100 + 0.65
                proceeds = exit_quote["bid"] * 100 - 0.65
                pnl = proceeds - debit
                midpoint_return = (
                    ((exit_quote["bid"] + exit_quote["ask"]) / 2)
                    / ((entry["bid"] + entry["ask"]) / 2) - 1
                ) * 100
                records.append({
                    "candidate_id": trade["candidate_id"],
                    "ticker": ticker,
                    "date": trade["date"],
                    "setup": trade["setup"],
                    "direction": int(trade["direction"]),
                    "selector": selector,
                    "symbol": symbol,
                    "expiration": contract["expiration"],
                    "strike": float(contract["strike"]),
                    "option_type": contract["option_type"],
                    "entry_timestamp": entry["timestamp"],
                    "entry_bid": entry["bid"],
                    "entry_ask": entry["ask"],
                    "entry_spread_pct": (
                        (entry["ask"] - entry["bid"])
                        / ((entry["ask"] + entry["bid"]) / 2) * 100
                    ),
                    "exit_timestamp": exit_quote["timestamp"],
                    "exit_bid": exit_quote["bid"],
                    "exit_ask": exit_quote["ask"],
                    "pnl_per_contract": pnl,
                    "return_on_debit_pct": pnl / debit * 100,
                    "midpoint_return_pct": midpoint_return,
                    "underlying_gross_return_pct": float(trade["gross_return"]) * 100,
                    "underlying_exit_reason": trade["exit_reason"],
                })
    finally:
        connection.close()
    frame = pd.DataFrame(records)
    summary: dict[str, Any] = {
        "trade_signals_requested": len(trades),
        "observed_contract_round_trips": len(records),
        "skips": skips,
    }
    if not frame.empty:
        summary.update({
            "unique_signals_confirmed": int(frame[["ticker", "date"]].drop_duplicates().shape[0]),
            "wins": int((frame["pnl_per_contract"] > 0).sum()),
            "win_rate": float((frame["pnl_per_contract"] > 0).mean()),
            "mean_return_on_debit_pct": float(frame["return_on_debit_pct"].mean()),
            "median_return_on_debit_pct": float(frame["return_on_debit_pct"].median()),
            "mean_entry_spread_pct": float(frame["entry_spread_pct"].mean()),
            "contract_observation_note": (
                "ATM/ITM/OTM rows are alternative structures, not simultaneous portfolio positions; "
                "their P/L must not be summed together."
            ),
            "by_selector": {
                selector: {
                    "observations": int(len(group)),
                    "wins": int((group["pnl_per_contract"] > 0).sum()),
                    "win_rate": float((group["pnl_per_contract"] > 0).mean()),
                    "sum_pnl_one_contract_sequential": float(group["pnl_per_contract"].sum()),
                    "mean_return_on_debit_pct": float(group["return_on_debit_pct"].mean()),
                    "median_return_on_debit_pct": float(group["return_on_debit_pct"].median()),
                    "mean_entry_spread_pct": float(group["entry_spread_pct"].mean()),
                }
                for selector, group in frame.groupby("selector", sort=True)
            },
        })
    return {"summary": summary, "records": records}


def confirm_report(
    report_path: str | Path = DEFAULT_OUTPUT / "latest_ticker_rejection_report.json",
    *,
    tradier_db: str | Path = DEFAULT_TRADIER_DB,
) -> dict[str, Any]:
    report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    output: dict[str, Any] = {
        "schema_version": 1,
        "source_report": str(Path(report_path).resolve()),
        "source_as_of": report.get("as_of"),
        "status": "OBSERVED_QUOTES_ONLY",
        "by_ticker": {},
        "caveat": "Captured bid/ask observations only; absent contracts or quote windows remain missing.",
        "automatic_promotion": False,
        "execution_authority": False,
    }
    records_by_candidate = report.get("descriptive_trade_records") or {}
    for ticker, leaders in (report.get("ticker_descriptive_leaders") or {}).items():
        if not leaders:
            output["by_ticker"][ticker] = {"status": "NO_FOUR_TRADE_CANDIDATE"}
            continue
        candidate_id = leaders[0]["candidate"]["candidate_id"]
        records = [
            row for row in records_by_candidate.get(candidate_id, [])
            if str(row.get("ticker")) == ticker
        ]
        result = confirm_trade_records(records, tradier_db=tradier_db)
        result["candidate_id"] = candidate_id
        output["by_ticker"][ticker] = result
    target = Path(report_path).with_name("latest_ticker_rejection_option_confirmation.json")
    target.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output
