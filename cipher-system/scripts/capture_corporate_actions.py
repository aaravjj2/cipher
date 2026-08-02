#!/usr/bin/env python3
"""Capture a research-only corporate-actions pilot snapshot; never adjusts prices."""
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

import yfinance as yf  # noqa: E402

from core.research_platform.bootstrap import ResearchPlatform  # noqa: E402
from core.research_platform.config import ResearchPlatformConfig  # noqa: E402
from core.research_platform.corporate_actions import capture_actions  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symbols", nargs="+", help="registered pilot symbols")
    args = parser.parse_args(argv)
    now = datetime.now(timezone.utc)
    payload = capture_actions(
        args.symbols,
        fetch_actions=lambda symbol: yf.Ticker(symbol).history(period="max", actions=True),
        retrieved_at=now,
    )
    out_dir = ROOT / "data" / "corporate_actions"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"yfinance_actions_{now.strftime('%Y%m%dT%H%M%SZ')}.json"
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    platform = ResearchPlatform(ResearchPlatformConfig.default(REPOSITORY_ROOT))
    lake = platform.raw_lake.freeze_file(
        out,
        source="yfinance",
        dataset="corporate_actions_pilot",
        request_metadata={"symbols": payload["symbols"], "point_in_time_ready": False},
    )
    print(json.dumps({
        "path": str(out), "rows": len(payload["rows"]), "raw_object_id": lake.manifest.raw_object_id,
        "sha256": lake.manifest.checksum, "point_in_time_ready": False,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
