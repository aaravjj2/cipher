#!/usr/bin/env python3
"""One scheduled pass of the local Structural Fib V6 paper account."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from core import structural_fib_v6_paper as paper  # noqa: E402
from core.structural_fib_bars import NY  # noqa: E402
from core.structural_fib_forward import fetch_recent  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", default=",".join(paper.SYMBOLS))
    parser.add_argument("--db", type=Path, default=paper.DEFAULT_DB)
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args(argv)
    if args.status:
        conn = paper.connect(args.db)
        try:
            print(json.dumps(paper.account_status(conn), indent=2))
        finally:
            conn.close()
        return 0
    now = datetime.now(NY)
    # Fetch only where a closed RTH bar can change the paper book. The timer is
    # intentionally non-persistent, so missed signals cannot be manufactured later.
    if now.weekday() >= 5 or not (time(9, 35) <= now.time() <= time(16, 5)):
        print(json.dumps({"status": "outside_session", "as_of": now.isoformat()}))
        return 0
    symbols = [x.strip().upper() for x in args.symbols.split(",") if x.strip()]
    result = paper.run_pass(lambda symbol: fetch_recent(symbol, days=12), symbols=symbols, db_path=args.db)
    print(json.dumps(result, sort_keys=True))
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
