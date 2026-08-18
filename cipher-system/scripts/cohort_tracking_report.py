#!/usr/bin/env python3
"""Cohort tracking report: signals -> entries -> outcomes per strategy.

Reads the fronttest portfolio DB (read-only) and the autopilot shadow DB and
prints a weekly-style summary of how each strategy's cohort is progressing:
signals seen, entries taken, outcomes resolved, and the realized option P&L.
The report deliberately does not rank strategies — no strategy is rank-eligible
before 20 prospective closes.

Usage:
    python scripts/cohort_tracking_report.py [--json] [--db PATH] [--shadow-db PATH]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB = Path("/home/aarav/Aarav/cipher/runtime/data/fronttest_portfolios/fronttest.sqlite")
DEFAULT_SHADOW_DB = Path("/home/aarav/Aarav/cipher/runtime/data/paper_runtime/data/paper_trades/autopilot_shadow.sqlite")


def _connect(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    return db


def fronttest_cohorts(db_path: Path) -> list[dict]:
    with _connect(db_path) as db:
        portfolios = [dict(r) for r in db.execute(
            "select portfolio_id, strategy, symbol from portfolios order by rowid"
        )]
        rows = []
        for portfolio in portfolios:
            pid = portfolio["portfolio_id"]
            signals = db.execute(
                "select count(*) n, coalesce(sum(case when disposition='OPENED' then 1 else 0 end),0) entered, "
                "coalesce(sum(case when disposition='SKIPPED' then 1 else 0 end),0) skipped from signals where portfolio_id=?",
                (pid,),
            ).fetchone()
            outcomes = db.execute(
                "select status, outcome, count(*) n from signal_outcomes where portfolio_id=? group by status, outcome",
                (pid,),
            ).fetchall()
            closed = db.execute(
                "select count(*) n, coalesce(sum(pnl),0) pnl, coalesce(sum(case when pnl>0 then 1 else 0 end),0) wins "
                "from positions where portfolio_id=? and status='CLOSED'",
                (pid,),
            ).fetchone()
            open_pos = db.execute(
                "select count(*) from positions where portfolio_id=? and status='OPEN'", (pid,),
            ).fetchone()[0]
            rows.append({
                "portfolio_id": pid,
                "strategy": portfolio["strategy"],
                "symbol": portfolio["symbol"],
                "signals": int(signals["n"]),
                "entries": int(signals["entered"]),
                "skipped": int(signals["skipped"]),
                "open_positions": int(open_pos),
                "closed_trades": int(closed["n"]),
                "wins": int(closed["wins"]),
                "realized_pnl": round(float(closed["pnl"] or 0), 2),
                "outcomes": [dict(r) for r in outcomes],
                "minimum_sample": 20,
                "cohort_ready": int(closed["n"]) >= 20,
            })
        return rows


def shadow_cohort(db_path: Path) -> dict:
    """Autopilot shadow executor: cards admitted, orders, positions, events."""
    if not db_path.exists():
        return {"status": "no_shadow_db", "cards": 0, "orders": 0, "positions": 0}
    try:
        with _connect(db_path) as db:
            cards = db.execute("select count(*) n from signal_cards").fetchone()["n"]
            accepted = db.execute(
                "select count(*) n from signal_cards where status='ACCEPTED' or status='TRACKED'"
            ).fetchone()["n"]
            rejected = db.execute("select count(*) n from signal_cards where status='REJECTED'").fetchone()["n"]
            orders = db.execute("select count(*) n from paper_orders").fetchone()["n"]
            positions = db.execute("select count(*) n from paper_positions").fetchone()["n"]
            return {
                "status": "shadow_db",
                "cards": int(cards), "accepted": int(accepted), "rejected": int(rejected),
                "orders": int(orders), "positions": int(positions),
            }
    except sqlite3.Error as exc:
        return {"status": "shadow_db_error", "error": str(exc)}


def render(rows: list[dict], shadow: dict) -> str:
    lines = [
        "COHORT TRACKING (prospective)",
        "=" * 78,
        f"{'strategy':28s} {'sig':>4s} {'ent':>4s} {'skp':>4s} {'open':>4s} {'cls':>4s} {'win':>3s} {'realized':>10s}",
        "-" * 78,
    ]
    for row in rows:
        lines.append(
            f"{row['strategy'][:28]:28s} {row['signals']:4d} {row['entries']:4d} {row['skipped']:4d} "
            f"{row['open_positions']:4d} {row['closed_trades']:4d} {row['wins']:3d} {row['realized_pnl']:10.2f}"
        )
    lines.append("-" * 78)
    for row in rows:
        if row["outcomes"]:
            detail = ", ".join(
                f"{o['status']}:{o['outcome'] or 'pending'}={o['n']}" for o in row["outcomes"]
            )
            lines.append(f"  {row['portfolio_id']}: {detail}")
    lines.append("")
    lines.append("AUTOPILOT SHADOW EXECUTOR")
    lines.append("-" * 78)
    if shadow.get("status") == "shadow_db":
        lines.append(
            f"cards {shadow['cards']} (accepted {shadow['accepted']}, rejected {shadow['rejected']}) · "
            f"orders {shadow['orders']} · positions {shadow['positions']}"
        )
    else:
        lines.append(shadow.get("status", "unknown"))
    lines.append("")
    lines.append("Rule: no strategy is rank-eligible before 20 prospective closes. Do not tune on this table.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--shadow-db", type=Path, default=DEFAULT_SHADOW_DB)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    rows = fronttest_cohorts(args.db)
    shadow = shadow_cohort(args.shadow_db)
    payload = {
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fronttest": rows,
        "shadow": shadow,
        "caveat": "Prospective tracking only; no strategy is rank-eligible before 20 closed trades.",
    }
    if args.json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(render(rows, shadow))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
