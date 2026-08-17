"""Observed bid/ask confirmation for the provisional rare OI candidate.

This is intentionally a narrow confirmation layer. It maps point-in-time OI
signals to option contracts that were already selected by the Tradier stream,
buys at the first observed ask after the signal, and exits at the first observed
bid after the following regular-session open. Same-day expirations are rejected
because they cannot carry an overnight signal.

The result is not a backfill of contracts that were never captured and it is not
an execution service. No network, account, or order endpoint is present.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from .oi_niche_strategy_lab import (
    DEFAULT_DB as GEX_DB,
    Candidate,
    load_snapshot_panel,
    signal_trades,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TRADIER_DB = ROOT / "data" / "tradier_stream.sqlite"
DEFAULT_OUTPUT = ROOT / "data" / "oi_niche_strategy_lab"
PROVISIONAL_CANDIDATE = Candidate(
    family="vex_gex_dislocation",
    parameters={"threshold": 0.65, "mode": "follow_vex", "horizon": "next_open"},
    direction_rule="follow_vex",
    hypothesis="Opposite, extreme VEX/GEX signs may persist through the next open.",
)


@dataclass(frozen=True, slots=True)
class StreamRun:
    started_at: datetime
    completed_at: datetime
    contracts_by_underlying: dict[str, tuple[dict[str, Any], ...]]


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc)


def load_stream_runs(db_path: str | Path, dates: Iterable[str]) -> dict[str, list[StreamRun]]:
    wanted = set(dates)
    connection = sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            """SELECT started_at, completed_at, selection_json
               FROM tradier_stream_runs
               WHERE completed_at IS NOT NULL AND event_count > 0
               ORDER BY started_at"""
        ).fetchall()
    finally:
        connection.close()
    output: dict[str, list[StreamRun]] = {}
    for started_raw, completed_raw, selection_raw in rows:
        day = str(started_raw)[:10]
        if day not in wanted:
            continue
        try:
            selection = json.loads(selection_raw or "{}")
        except json.JSONDecodeError:
            continue
        grouped: dict[str, list[dict[str, Any]]] = {}
        for detail in selection.get("selection_details") or ():
            underlying = str(detail.get("underlying") or "").upper()
            if not underlying:
                continue
            for contract in detail.get("contracts") or ():
                if contract.get("symbol") and contract.get("expiration"):
                    grouped.setdefault(underlying, []).append(dict(contract))
        if not grouped:
            continue
        output.setdefault(day, []).append(StreamRun(
            started_at=_timestamp(started_raw),
            completed_at=_timestamp(completed_raw),
            contracts_by_underlying={key: tuple(value) for key, value in grouped.items()},
        ))
    return output


def _matching_run(runs: list[StreamRun], ticker: str, timestamp: datetime) -> StreamRun | None:
    containing = [
        run for run in runs
        if ticker in run.contracts_by_underlying
        and run.started_at <= timestamp <= run.completed_at + timedelta(minutes=2)
    ]
    if containing:
        return min(containing, key=lambda run: abs((run.started_at - timestamp).total_seconds()))
    eligible = [
        run for run in runs
        if ticker in run.contracts_by_underlying
        and abs((run.started_at - timestamp).total_seconds()) <= 75 * 60
    ]
    return min(eligible, key=lambda run: abs((run.started_at - timestamp).total_seconds())) if eligible else None


def _contract_choices(
    contracts: Iterable[dict[str, Any]],
    *,
    spot: float,
    direction: int,
    signal_day: str,
) -> dict[str, dict[str, Any]]:
    option_type = "call" if direction > 0 else "put"
    rows = [
        row for row in contracts
        if str(row.get("option_type") or "").lower() == option_type
        and str(row.get("expiration") or "") > signal_day
    ]
    if not rows:
        return {}
    rows.sort(key=lambda row: (abs(float(row["strike"]) - spot), str(row["symbol"])))
    atm = rows[0]
    if option_type == "call":
        itm_rows = sorted((row for row in rows if float(row["strike"]) < spot), key=lambda row: spot - float(row["strike"]))
        otm_rows = sorted((row for row in rows if float(row["strike"]) > spot), key=lambda row: float(row["strike"]) - spot)
    else:
        itm_rows = sorted((row for row in rows if float(row["strike"]) > spot), key=lambda row: float(row["strike"]) - spot)
        otm_rows = sorted((row for row in rows if float(row["strike"]) < spot), key=lambda row: spot - float(row["strike"]))
    return {
        "atm": atm,
        "itm": itm_rows[0] if itm_rows else atm,
        "otm": otm_rows[0] if otm_rows else atm,
    }


def _quote(
    connection: sqlite3.Connection,
    symbol: str,
    start: datetime,
    end: datetime,
    *,
    ascending: bool,
) -> dict[str, Any] | None:
    order = "ASC" if ascending else "DESC"
    row = connection.execute(
        f"""SELECT captured_at, bid, ask
            FROM tradier_stream_events INDEXED BY idx_tradier_events_symbol_time
            WHERE symbol = ? AND captured_at >= ? AND captured_at <= ?
              AND event_type = 'quote' AND bid > 0 AND ask > 0 AND ask >= bid
            ORDER BY captured_at {order} LIMIT 1""",
        (symbol, start.isoformat(), end.isoformat()),
    ).fetchone()
    if not row:
        return None
    return {"timestamp": row[0], "bid": float(row[1]), "ask": float(row[2])}


def confirm_candidate(
    gex_db: str | Path = GEX_DB,
    tradier_db: str | Path = DEFAULT_TRADIER_DB,
    candidate: Candidate = PROVISIONAL_CANDIDATE,
) -> dict[str, Any]:
    panel = load_snapshot_panel(gex_db)
    signals = signal_trades(panel, candidate)
    runs_by_day = load_stream_runs(tradier_db, signals["date"].unique())
    connection = sqlite3.connect(f"file:{Path(tradier_db).resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    records: list[dict[str, Any]] = []
    skips: dict[str, int] = {}

    def skip(reason: str) -> None:
        skips[reason] = skips.get(reason, 0) + 1

    try:
        for signal in signals.itertuples(index=False):
            timestamp = pd.Timestamp(signal.timestamp).to_pydatetime().astimezone(timezone.utc)
            run = _matching_run(runs_by_day.get(signal.date, []), signal.ticker, timestamp)
            if run is None:
                skip("underlying_not_in_stream_run")
                continue
            choices = _contract_choices(
                run.contracts_by_underlying[signal.ticker],
                spot=float(signal.spot),
                direction=int(signal.direction),
                signal_day=str(signal.date),
            )
            if not choices:
                skip("no_overnight_contract_captured")
                continue
            next_day = pd.Timestamp(signal.next_timestamp).date() if pd.notna(signal.next_timestamp) else None
            # For next-session signals the intraday next_timestamp still points
            # inside the signal day. The known next session comes from the
            # panel outcome; locate it from the ordered capture dates.
            future_dates = sorted(day for day in panel.loc[panel["date"] > signal.date, "date"].unique())
            if not future_dates:
                skip("no_following_capture_session")
                continue
            next_day = datetime.fromisoformat(future_dates[0]).date()
            entry_end = min(
                timestamp + timedelta(minutes=10),
                datetime.combine(timestamp.date(), time(20, 0), tzinfo=timezone.utc),
            )
            exit_start = datetime.combine(next_day, time(13, 30), tzinfo=timezone.utc)
            exit_end = datetime.combine(next_day, time(14, 15), tzinfo=timezone.utc)
            for selector, contract in choices.items():
                symbol = str(contract["symbol"])
                entry = _quote(connection, symbol, timestamp, entry_end, ascending=True)
                exit_quote = _quote(connection, symbol, exit_start, exit_end, ascending=True)
                if entry is None or exit_quote is None:
                    skip(f"missing_{selector}_entry_or_exit_quote")
                    continue
                debit = entry["ask"] * 100.0 + 0.65
                proceeds = exit_quote["bid"] * 100.0 - 0.65
                pnl = proceeds - debit
                records.append({
                    "candidate_id": candidate.candidate_id,
                    "signal_timestamp": timestamp.isoformat(),
                    "signal_date": signal.date,
                    "exit_session": next_day.isoformat(),
                    "ticker": signal.ticker,
                    "direction": int(signal.direction),
                    "selector": selector,
                    "symbol": symbol,
                    "expiration": contract["expiration"],
                    "strike": float(contract["strike"]),
                    "option_type": contract["option_type"],
                    "signal_spot": float(signal.spot),
                    "entry_timestamp": entry["timestamp"],
                    "entry_bid": entry["bid"],
                    "entry_ask": entry["ask"],
                    "entry_width_pct": (entry["ask"] - entry["bid"]) / ((entry["ask"] + entry["bid"]) / 2.0) * 100.0,
                    "exit_timestamp": exit_quote["timestamp"],
                    "exit_bid": exit_quote["bid"],
                    "exit_ask": exit_quote["ask"],
                    "pnl_per_contract": pnl,
                    "return_on_debit_pct": pnl / debit * 100.0,
                })
    finally:
        connection.close()

    frame = pd.DataFrame(records)
    selectors: dict[str, Any] = {}
    if not frame.empty:
        for selector, group in frame.groupby("selector"):
            values = group["return_on_debit_pct"].to_numpy(dtype=float)
            pnls = group["pnl_per_contract"].to_numpy(dtype=float)
            selectors[str(selector)] = {
                "trades": int(len(group)),
                "days": int(group["signal_date"].nunique()),
                "tickers": int(group["ticker"].nunique()),
                "wins": int((pnls > 0).sum()),
                "win_rate": float(np.mean(pnls > 0)),
                "total_pnl_per_one_contract_each": float(pnls.sum()),
                "mean_pnl_per_contract": float(pnls.mean()),
                "median_pnl_per_contract": float(np.median(pnls)),
                "mean_return_on_debit_pct": float(values.mean()),
                "median_return_on_debit_pct": float(np.median(values)),
                "average_entry_width_pct": float(group["entry_width_pct"].mean()),
            }
    return {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "completed",
        "candidate": candidate.to_dict(),
        "signals_in_full_oi_panel": int(len(signals)),
        "observed_option_trade_rows": int(len(frame)),
        "selectors": selectors,
        "skips": skips,
        "trades": records,
        "execution": "buy at first observed ask within 10 minutes after signal; sell at first observed bid between 09:30 and 10:15 ET next session; $0.65 per contract per side",
        "limitations": [
            "Only contracts already selected and streamed at signal time are eligible.",
            "Same-day expirations are rejected for overnight holding.",
            "The Tradier universe is much smaller than the 543-ticker OI panel.",
            "Quote presence does not prove displayed size was fillable.",
            "This audit reuses known dates and is not an independent prospective test.",
        ],
        "option_pnl": True,
        "automatic_promotion": False,
        "paper_or_live_execution": False,
        "execution_authority": False,
    }


def write_confirmation(report: dict[str, Any], output_directory: str | Path = DEFAULT_OUTPUT) -> Path:
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    path = output / "latest_oi_option_quote_confirmation.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if report["trades"]:
        pd.DataFrame(report["trades"]).to_csv(
            output / "latest_oi_option_quote_confirmation_trades.csv", index=False
        )
    return path
