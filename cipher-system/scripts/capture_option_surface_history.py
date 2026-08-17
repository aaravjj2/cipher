#!/usr/bin/env python3
"""Capture daily OPRA option-surface metrics for the Research Desk universe."""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import option_history  # noqa: E402
from core.market_research_agent import universe  # noqa: E402
from core.app import option_chain  # noqa: E402


def capture(symbols: list[str], *, max_pages: int = 12) -> dict:
    now = datetime.now(timezone.utc)
    start, end = date.today(), date.today() + timedelta(days=120)
    result = {"started_at": now.isoformat(), "symbols": symbols, "success": [], "errors": [],
              "source": "alpaca_opra", "read_only": True, "live_order_authority": False}
    for symbol in symbols:
        try:
            rows = option_chain(symbol, "opra", max_pages=max_pages, force=True,
                                expiration_gte=start.isoformat(), expiration_lte=end.isoformat())
            recorded = option_history.record_snapshot({
                "ticker": symbol, "timestamp": datetime.now(timezone.utc).isoformat(),
                "feed": "opra", "contracts": rows,
            })
            result["success"].append({key: recorded.get(key) for key in (
                "ticker", "observed_at", "contract_count", "front_expiry", "front_atm_iv",
                "market_session_date", "iv_30d", "iv_30d_quality", "methodology_version",
                "front_skew_25d", "term_slope", "iv_coverage", "oi_coverage", "quote_coverage", "raw_sha256",
            )})
        except Exception as exc:
            result["errors"].append({"ticker": symbol, "error": f"{type(exc).__name__}: {exc}"})
        time.sleep(.35)
    result["finished_at"] = datetime.now(timezone.utc).isoformat()
    out_dir = ROOT / "data" / "option_history_runs"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{now.strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    result["report"] = str(path)
    return result


def seed_latest(symbols: list[str]) -> dict:
    directory = ROOT / "data" / "live_option_chains"
    success, errors = [], []
    for symbol in symbols:
        path = directory / f"latest_{symbol}.json"
        if not path.exists():
            errors.append({"ticker": symbol, "error": "latest snapshot unavailable"})
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["ticker"] = symbol
            success.append(option_history.record_snapshot(payload))
        except Exception as exc:
            errors.append({"ticker": symbol, "error": f"{type(exc).__name__}: {exc}"})
    return {"mode": "seed_latest", "success": len(success), "errors": errors,
            "read_only": True, "live_order_authority": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--all", action="store_true")
    scope.add_argument("--tickers")
    parser.add_argument("--max-pages", type=int, default=12)
    parser.add_argument("--from-latest", action="store_true", help="Seed from existing latest files without network calls")
    args = parser.parse_args()
    symbols = universe() if args.all else sorted({s.strip().upper() for s in args.tickers.split(",") if s.strip()})
    result = seed_latest(symbols) if args.from_latest else capture(symbols, max_pages=max(1, min(args.max_pages, 36)))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 1 if result.get("errors") and not result.get("success") else 0


if __name__ == "__main__":
    raise SystemExit(main())
