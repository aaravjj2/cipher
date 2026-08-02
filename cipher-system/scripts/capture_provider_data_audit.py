#!/usr/bin/env python3
"""Capture read-only Alpaca corporate actions and Tradier daily bars with provenance."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = ROOT.parents[0]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.research_platform.bootstrap import ResearchPlatform  # noqa: E402
from core.research_platform.config import ResearchPlatformConfig  # noqa: E402
from core.research_platform.market_data_providers import (  # noqa: E402
    fetch_alpaca_corporate_actions,
    fetch_tradier_daily_history,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symbol")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    args = parser.parse_args(argv)
    now = datetime.now(timezone.utc)
    payload = {
        "schema_version": 1,
        "captured_at": now.isoformat(),
        "symbol": args.symbol.upper(),
        "start": args.start,
        "end": args.end,
        "alpaca_corporate_actions": fetch_alpaca_corporate_actions([args.symbol], start=args.start, end=args.end),
        "tradier_daily_history": fetch_tradier_daily_history(args.symbol, start=args.start, end=args.end),
        "allowed_use": "research_reconciliation_only",
        "live_execution": False,
    }
    out_dir = ROOT / "data" / "provider_audits"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{args.symbol.upper()}_{args.start}_{args.end}_{now.strftime('%Y%m%dT%H%M%SZ')}.json"
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    platform = ResearchPlatform(ResearchPlatformConfig.default(REPOSITORY_ROOT))
    lake = platform.raw_lake.freeze_file(
        out, source="alpaca_tradier", dataset="provider_reconciliation_audit",
        request_metadata={"symbol": args.symbol.upper(), "start": args.start, "end": args.end},
    )
    print(json.dumps({
        "path": str(out), "alpaca_actions": len(payload["alpaca_corporate_actions"]),
        "tradier_bars": len(payload["tradier_daily_history"]), "raw_object_id": lake.manifest.raw_object_id,
        "sha256": lake.manifest.checksum, "live_execution": False,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
