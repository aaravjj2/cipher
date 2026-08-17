"""Map Structural Fib V6 underlying trades to captured option observations.

Two protocols are intentionally kept separate:

* Alpaca historical OPRA bars are an executed-trade OHLC proxy.  They have no
  historical NBBO, so both close/close and deliberately adverse high/low marks
  are reported.
* Tradier stream events contain contemporaneous two-sided quotes.  Long option
  entries pay the first observed ask and exits receive the first observed bid.

This module is research-only.  It contains no broker client or order endpoint.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from typing import Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
UTC = timezone.utc


def _utc(day: str, hhmm: str) -> datetime:
    return datetime.fromisoformat(f"{day}T{hhmm}:00").replace(tzinfo=NY).astimezone(UTC)


def _text(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _option_type(trade: Mapping[str, object]) -> str:
    return "call" if trade["direction"] == "long" else "put"


def _summary(rows: Sequence[dict], initial_capital: float = 100_000.0) -> dict:
    equity = initial_capital
    peak = equity
    max_dd = 0.0
    for row in sorted(rows, key=lambda x: (x["day"], x["entry_time"], x["underlying"])):
        allocation = equity * 0.10
        quantity = int(allocation // (row["entry_option_price"] * 100.0))
        if quantity < 1:
            row["quantity"] = 0
            row["pnl"] = 0.0
            continue
        pnl = quantity * 100.0 * (row["exit_option_price"] - row["entry_option_price"])
        row["quantity"] = quantity
        row["pnl"] = pnl
        equity += pnl
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak * 100.0)
    executable = [r for r in rows if r.get("quantity", 0) > 0]
    returns = [r["option_return_pct"] for r in executable]
    return {
        "trades": len(executable),
        "wins": sum(value > 0 for value in returns),
        "losses": sum(value < 0 for value in returns),
        "flat": sum(value == 0 for value in returns),
        "win_rate": sum(value > 0 for value in returns) / len(returns) if returns else None,
        "avg_option_return_pct": sum(returns) / len(returns) if returns else None,
        "initial_capital": initial_capital,
        "ending_equity": equity,
        "profit_loss": equity - initial_capital,
        "return_pct": (equity / initial_capital - 1.0) * 100.0,
        "max_drawdown_pct": max_dd,
    }


def _grouped(rows: Sequence[dict], field: str) -> dict[str, dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        groups[str(row[field])].append(row)
    return {name: _summary(group) for name, group in sorted(groups.items())}


def _candidate_rows(db: sqlite3.Connection, trade: Mapping[str, object]) -> list[sqlite3.Row]:
    day = str(trade["day"])
    option_type = _option_type(trade)
    decision = db.execute(
        """select max(s.decision_date)
             from decision_selections s
            where s.decision_date<=? and s.expiration_date>=? and s.option_type=?""",
        (day, day, option_type),
    ).fetchone()[0]
    if decision is None:
        return []
    return db.execute(
        """select s.* from decision_selections s
             join selection_observation_audit a
               on a.decision_date=s.decision_date and a.symbol=s.symbol
            where s.decision_date=? and s.expiration_date>=? and s.option_type=?
              and a.observed_on_decision=1
            order by s.rank,s.symbol""",
        (decision, day, option_type),
    ).fetchall()


def _bar_at_or_after(
    db: sqlite3.Connection, symbol: str, when: datetime, seconds: int = 75
) -> sqlite3.Row | None:
    return db.execute(
        """select * from option_bars where symbol=? and timestamp>=? and timestamp<=?
            order by timestamp limit 1""",
        (symbol, _text(when), _text(when + timedelta(seconds=seconds))),
    ).fetchone()


def _exit_bar(db: sqlite3.Connection, symbol: str, trade: Mapping[str, object]) -> sqlite3.Row | None:
    start = _utc(str(trade["day"]), str(trade["exit_time"]))
    if trade["exit_reason"] in {"target", "rth_close", "end_of_data"}:
        # The underlying 5m OHLC identifies the bar, not the intrabar touch.
        # Use the final option print in that bar to avoid an optimistic timestamp.
        end = start + timedelta(minutes=5)
        return db.execute(
            """select * from option_bars where symbol=? and timestamp>=? and timestamp<?
                order by timestamp desc limit 1""",
            (symbol, _text(start), _text(end)),
        ).fetchone()
    return _bar_at_or_after(db, symbol, start)


def historical_trade_bar_test(
    trades: Iterable[Mapping[str, object]],
    databases: Mapping[tuple[str, str], Path],
) -> dict:
    """Test captured historical option bars without pretending they are NBBO."""
    connections: dict[tuple[str, str], sqlite3.Connection] = {}
    rows_by_protocol: dict[str, list[dict]] = {"close_proxy": [], "adverse_bar": []}
    skips: Counter[str] = Counter()
    eligible = 0
    try:
        for trade in sorted(trades, key=lambda x: (str(x["day"]), str(x["entry_time"]), str(x["symbol"]))):
            key = (str(trade["symbol"]), _option_type(trade))
            path = databases.get(key)
            if path is None or not path.is_file():
                skips["no_archive_for_symbol_and_type"] += 1
                continue
            eligible += 1
            if key not in connections:
                db = sqlite3.connect(path)
                db.row_factory = sqlite3.Row
                connections[key] = db
            db = connections[key]
            candidates = _candidate_rows(db, trade)
            if not candidates:
                skips["no_point_in_time_selection"] += 1
                continue
            entry_at = _utc(str(trade["day"]), str(trade["entry_time"]))
            chosen = entry = None
            for candidate in candidates:
                entry_candidate = _bar_at_or_after(db, str(candidate["symbol"]), entry_at)
                if entry_candidate is not None:
                    chosen, entry = candidate, entry_candidate
                    break
            if chosen is None or entry is None:
                skips["no_entry_minute_print"] += 1
                continue
            exit_bar = _exit_bar(db, str(chosen["symbol"]), trade)
            if exit_bar is None:
                skips["no_exit_bar_print"] += 1
                continue
            common = {
                "underlying": trade["symbol"], "day": trade["day"],
                "setup_id": trade["setup_id"], "direction": trade["direction"],
                "option_type": _option_type(trade), "contract": chosen["symbol"],
                "strike": chosen["strike"], "expiration": chosen["expiration_date"],
                "selection_day": chosen["decision_date"], "selection_rank": chosen["rank"],
                "entry_time": trade["entry_time"], "exit_time": trade["exit_time"],
                "exit_reason": trade["exit_reason"],
                "entry_observed_at": entry["timestamp"], "exit_observed_at": exit_bar["timestamp"],
                "source": entry["source"],
            }
            for protocol, entry_field, exit_field in (
                ("close_proxy", "close", "close"),
                ("adverse_bar", "high", "low"),
            ):
                entry_price, exit_price = float(entry[entry_field]), float(exit_bar[exit_field])
                if entry_price <= 0 or exit_price < 0:
                    skips[f"invalid_{protocol}_price"] += 1
                    continue
                row = dict(common, protocol=protocol, entry_option_price=entry_price,
                           exit_option_price=exit_price,
                           option_return_pct=(exit_price / entry_price - 1.0) * 100.0)
                rows_by_protocol[protocol].append(row)
    finally:
        for db in connections.values():
            db.close()
    return {
        "study": "structural_fib_v6_captured_option_trade_bars",
        "protocols": {
            name: {
                **_summary(rows),
                "by_symbol": _grouped(rows, "underlying"),
                "by_setup": _grouped(rows, "setup_id"),
            }
            for name, rows in rows_by_protocol.items()
        },
        "eligible_underlying_trades": eligible,
        "mapped_trades": len(rows_by_protocol["close_proxy"]),
        "coverage_rate": len(rows_by_protocol["close_proxy"]) / eligible if eligible else 0.0,
        "skips": dict(skips),
        "trade_records": rows_by_protocol,
        "caveat": "Historical option OHLC contains executed prints, not NBBO; close_proxy and adverse_bar are sensitivity bounds, not guaranteed fills.",
    }


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _covering_run(db: sqlite3.Connection, when: datetime, underlying: str) -> sqlite3.Row | None:
    rows = db.execute(
        """select * from tradier_stream_runs where started_at<=?
             and coalesce(last_event_at,completed_at)>=? order by started_at desc""",
        (when.isoformat(), when.isoformat()),
    ).fetchall()
    for row in rows:
        try:
            details = json.loads(row["selection_json"] or "{}").get("selection_details", [])
        except json.JSONDecodeError:
            continue
        if any(item.get("underlying") == underlying for item in details):
            return row
    return None


def _run_contracts(run: sqlite3.Row, underlying: str, option_type: str, spot: float) -> list[dict]:
    details = json.loads(run["selection_json"] or "{}").get("selection_details", [])
    detail = next((item for item in details if item.get("underlying") == underlying), None)
    if not detail:
        return []
    candidates = [x for x in detail.get("contracts", []) if x.get("option_type") == option_type]
    return sorted(candidates, key=lambda x: (abs(float(x["strike"]) - spot), x["symbol"]))


def _first_quote(db: sqlite3.Connection, symbol: str, when: datetime, max_seconds: int) -> sqlite3.Row | None:
    return db.execute(
        """select * from tradier_stream_events where symbol=? and event_type='quote'
             and captured_at>=? and captured_at<=? and bid is not null and ask is not null
             and bid>=0 and ask>0 and ask>=bid order by captured_at limit 1""",
        (symbol, when.isoformat(), (when + timedelta(seconds=max_seconds)).isoformat()),
    ).fetchone()


def tradier_nbbo_test(
    trades: Iterable[Mapping[str, object]], db_path: Path, *, quote_window_seconds: int = 15
) -> dict:
    """Pay contemporaneous ask and sell contemporaneous bid for streamed contracts."""
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    rows: list[dict] = []
    skips: Counter[str] = Counter()
    candidates_n = 0
    try:
        for trade in sorted(trades, key=lambda x: (str(x["day"]), str(x["entry_time"]), str(x["symbol"]))):
            entry_at = _utc(str(trade["day"]), str(trade["entry_time"]))
            run = _covering_run(db, entry_at, str(trade["symbol"]))
            if run is None:
                skips["no_stream_run_at_entry"] += 1
                continue
            candidates_n += 1
            contracts = _run_contracts(run, str(trade["symbol"]), _option_type(trade), float(trade["entry_price"]))
            chosen = entry_quote = None
            for contract in contracts:
                quote = _first_quote(db, contract["symbol"], entry_at, quote_window_seconds)
                if quote is not None:
                    chosen, entry_quote = contract, quote
                    break
            if chosen is None or entry_quote is None:
                skips["no_fresh_entry_nbbo"] += 1
                continue
            exit_at = _utc(str(trade["day"]), str(trade["exit_time"]))
            if trade["exit_reason"] in {"target", "rth_close", "end_of_data"}:
                exit_at += timedelta(minutes=5)
            exit_quote = _first_quote(db, chosen["symbol"], exit_at, quote_window_seconds)
            if exit_quote is None:
                skips["contract_not_streamed_at_exit"] += 1
                continue
            entry_price, exit_price = float(entry_quote["ask"]), float(exit_quote["bid"])
            rows.append({
                "underlying": trade["symbol"], "day": trade["day"],
                "setup_id": trade["setup_id"], "direction": trade["direction"],
                "option_type": _option_type(trade), "contract": chosen["symbol"],
                "strike": chosen["strike"], "expiration": chosen["expiration"],
                "entry_time": trade["entry_time"], "exit_time": trade["exit_time"],
                "exit_reason": trade["exit_reason"], "entry_quote_at": entry_quote["captured_at"],
                "exit_quote_at": exit_quote["captured_at"], "entry_option_price": entry_price,
                "exit_option_price": exit_price,
                "entry_spread_pct": (float(entry_quote["ask"]) - float(entry_quote["bid"])) / entry_price * 100.0,
                "exit_spread_pct": (float(exit_quote["ask"]) - float(exit_quote["bid"])) / float(exit_quote["ask"]) * 100.0,
                "option_return_pct": (exit_price / entry_price - 1.0) * 100.0,
            })
    finally:
        db.close()
    return {
        "study": "structural_fib_v6_captured_tradier_nbbo",
        "protocol": "first ask after entry; first bid after effective exit; target exits delayed to end of underlying 5m touch bar",
        "quote_window_seconds": quote_window_seconds,
        "underlying_trades_with_entry_run": candidates_n,
        "mapped_trades": len(rows),
        "overall": _summary(rows),
        "by_symbol": _grouped(rows, "underlying"),
        "by_setup": _grouped(rows, "setup_id"),
        "skips": dict(skips), "trade_records": rows,
        "caveat": "Small opportunistic overlap sample from dynamically selected streamed contracts; absence of a quote is skipped, never imputed.",
    }
