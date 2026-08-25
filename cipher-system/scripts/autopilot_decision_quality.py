#!/usr/bin/env python3
"""Decision-quality report for the paper autopilot ledger.

Read-only. Answers the questions that decide whether parameters should change:
which entries were ever profitable (MFE), when entries lose (entry hour),
what losses actually cost versus the configured stop (slippage through stops),
and whether expectancy is positive at the current sample size.

No thresholds are hardcoded beyond reporting: this names numbers, it does not
recommend parameter changes — those belong to experiments against the
accumulating prospective record, not to a report's opinion.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path

DEFAULT_DB = Path("/home/aarav/Aarav/cipher/runtime/data/paper_runtime/data/paper_trades/autopilot_shadow.sqlite")


def _rows(db_path: Path) -> list[dict]:
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    rows = [dict(r) for r in db.execute(
        """select ticker, direction, quantity, entry_price, exit_price,
                  exit_reason, opened_at, closed_at, payload_json
           from paper_positions where status='CLOSED' order by opened_at""")]
    db.close()
    return rows


def _pct(row: dict) -> float:
    return (row["exit_price"] - row["entry_price"]) / row["entry_price"] * 100.0


def _payload_field(row: dict, key: str) -> float | None:
    try:
        value = json.loads(row["payload_json"]).get(key)
    except (TypeError, ValueError):
        return None
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def analyze(rows: list[dict]) -> dict:
    trades = []
    for row in rows:
        pnl_pct = _pct(row)
        hour_et = datetime.fromisoformat(row["opened_at"]).astimezone().strftime("%H:%M")
        trades.append({
            "ticker": row["ticker"], "direction": row["direction"],
            "entry_hour_et": hour_et[:5], "exit_reason": row["exit_reason"],
            "pnl_pct": round(pnl_pct, 2),
            "mfe_pct": _payload_field(row, "mfe_pct"),
            "mae_pct": _payload_field(row, "mae_pct"),
            "dead_on_arrival": (_payload_field(row, "mfe_pct") or 0) < 5.0 and pnl_pct < 0,
        })

    def bucket(key_fn, label):
        groups: dict[str, list[dict]] = defaultdict(list)
        for trade in trades:
            groups[key_fn(trade)].append(trade)
        out = {}
        for key, items in sorted(groups.items()):
            pnls = [t["pnl_pct"] for t in items]
            out[key] = {
                "trades": len(items),
                "wins": sum(1 for p in pnls if p > 0),
                "win_rate_pct": round(100 * sum(1 for p in pnls if p > 0) / len(items), 1),
                "total_pnl_pct": round(sum(pnls), 2),
                "avg_pnl_pct": round(statistics.mean(pnls), 2),
            }
        return {label: out}

    wins = [t["pnl_pct"] for t in trades if t["pnl_pct"] > 0]
    losses = [t["pnl_pct"] for t in trades if t["pnl_pct"] <= 0]
    expectancy = {}
    if trades:
        win_rate = len(wins) / len(trades)
        avg_win = statistics.mean(wins) if wins else 0.0
        avg_loss = statistics.mean(losses) if losses else 0.0
        expectancy = {
            "sample_size": len(trades),
            "win_rate_pct": round(win_rate * 100, 1),
            "avg_win_pct": round(avg_win, 2),
            "avg_loss_pct": round(avg_loss, 2),
            "payoff_ratio": round(abs(avg_win / avg_loss), 2) if losses else None,
            "expectancy_per_trade_pct": round(win_rate * avg_win + (1 - win_rate) * avg_loss, 2),
            "note": (
                f"n={len(trades)} is below the ~30-trade minimum before any parameter "
                "change is supportable"
            ) if len(trades) < 30 else None,
        }

    doa = [t for t in trades if t["dead_on_arrival"]]
    stop_exits = [t for t in trades if t["exit_reason"] == "option_stop_loss"]
    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "trade_count": len(trades),
        "expectancy": expectancy,
        "dead_on_arrival": {
            "count": len(doa),
            "share_pct": round(100 * len(doa) / len(trades), 1) if trades else None,
            "definition": "loss where MFE never reached +5%",
            "tickers": [t["ticker"] for t in doa],
        },
        "stop_slippage_pct": [
            {"ticker": t["ticker"], "realized": t["pnl_pct"]} for t in stop_exits
        ],
        **bucket(lambda t: t["entry_hour_et"], "by_entry_hour_et"),
        **bucket(lambda t: t["exit_reason"], "by_exit_reason"),
        **bucket(lambda t: t["direction"], "by_direction"),
        "trades": trades,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args(argv)
    print(json.dumps(analyze(_rows(args.db)), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
