#!/usr/bin/env python3
"""Confirm the provisional OI candidate on already-captured option quotes."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.oi_option_quote_confirmation import (  # noqa: E402
    DEFAULT_OUTPUT, DEFAULT_TRADIER_DB, GEX_DB, confirm_candidate, write_confirmation,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gex-db", type=Path, default=GEX_DB)
    parser.add_argument("--tradier-db", type=Path, default=DEFAULT_TRADIER_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = confirm_candidate(args.gex_db, args.tradier_db)
    path = write_confirmation(report, args.output)
    print(json.dumps({
        "status": report["status"],
        "signals": report["signals_in_full_oi_panel"],
        "observed_option_trade_rows": report["observed_option_trade_rows"],
        "selectors": report["selectors"],
        "report": str(path.resolve()),
        "execution_authority": False,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
