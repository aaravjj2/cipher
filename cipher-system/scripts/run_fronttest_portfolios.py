#!/usr/bin/env python3
"""Run one prospective pass of all six isolated option shadow portfolios."""
from __future__ import annotations

import argparse
from datetime import datetime, time, timedelta, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import fronttest_portfolios as fronttest  # noqa: E402
from core.structural_fib_bars import NY  # noqa: E402
from core.structural_fib_forward import drop_forming, to_bars  # noqa: E402


def fetch(symbol: str, timeframe: str, days: int):
    from core.data_fetcher import fetch_alpaca_bars, load_env
    end = datetime.now(timezone.utc).replace(tzinfo=None)
    raw = fetch_alpaca_bars(symbol, end - timedelta(days=days), end,
                            timeframe=timeframe, creds=load_env())
    minutes = 1 if timeframe == "1Min" else 5
    return drop_forming(to_bars(raw), minutes=minutes)


def status(path: Path) -> list[dict]:
    db = fronttest.connect(path)
    try:
        return fronttest.portfolio_status(db)
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=fronttest.DEFAULT_DB)
    parser.add_argument("--status", action="store_true")
    parser.add_argument(
        "--reconcile-outcomes", action="store_true",
        help="Score already-recorded signals from available underlying bars without opening positions.",
    )
    parser.add_argument("--lookback-days", type=int, default=0)
    args = parser.parse_args(argv)
    if args.status:
        print(json.dumps({"paper_only": True, "portfolios": status(args.db)}, indent=2))
        return 0
    now = datetime.now(NY)
    if not args.reconcile_outcomes and (now.weekday() >= 5 or not (time(9, 31) <= now.time() <= time(15, 50))):
        print(json.dumps({"status": "outside_session", "paper_only": True,
                          "as_of": now.isoformat(), "portfolios": status(args.db)}))
        return 0
    bars = {
        "NVDA": fetch("NVDA", "5Min", max(12, args.lookback_days)),
        "QQQ": fetch("QQQ", "1Min", max(3, args.lookback_days)),
        "MU": fetch("MU", "5Min", max(3, args.lookback_days)),
    }
    if args.reconcile_outcomes:
        db = fronttest.connect(args.db)
        try:
            updates = fronttest.update_signal_outcomes(db, bars, now_et=now)
            db.commit()
        finally:
            db.close()
        print(json.dumps({"status": "reconciled", "paper_only": True, **updates}, sort_keys=True))
        return 0
    print(json.dumps(fronttest.run_pass(bars, db_path=args.db), sort_keys=True))
    # Individual market-data gaps are recorded in the ledger; the next minute
    # retries monitoring without causing a service restart storm.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
