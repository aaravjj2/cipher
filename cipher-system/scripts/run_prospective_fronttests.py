#!/usr/bin/env python3
"""Run the frozen TSLA rule and Aug-17 weekly radar shadow monitors."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, time, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import app  # noqa: E402
from core import prospective_fronttests as fronttests  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=fronttests.DEFAULT_DB)
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args(argv)
    db = fronttests.connect(args.db)
    try:
        if args.status:
            print(json.dumps({"paper_only": True, "execution_authority": False,
                              "programs": fronttests.status(db)}, indent=2))
            return 0
    finally:
        db.close()
    now = datetime.now(timezone.utc)
    local = now.astimezone(fronttests.NY)
    if local.weekday() >= 5 or not (time(9, 34) <= local.time().replace(tzinfo=None) <= time(16, 5)):
        db = fronttests.connect(args.db)
        try:
            print(json.dumps({"status": "outside_session", "paper_only": True,
                              "as_of": now.isoformat(), "programs": fronttests.status(db)}))
        finally:
            db.close()
        return 0
    tickers = sorted({spec.ticker for spec in fronttests.RADAR_SPECS} | {"TSLA"})
    bars = {}
    bar_errors = {}
    for ticker in tickers:
        try:
            bars[ticker] = app.bars(ticker, "5m", limit=220).get("bars") or []
        except Exception as exc:
            bars[ticker] = []
            bar_errors[ticker] = f"{type(exc).__name__}: {exc}"
    result = fronttests.run_once(
        bars, market=fronttests.AlpacaReadOnlyMarket(), db_path=args.db, now=now,
        bar_errors=bar_errors,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
