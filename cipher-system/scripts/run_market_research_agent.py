#!/usr/bin/env python3
"""Capture one scheduled, read-only Cipher Research Desk report."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "core") not in sys.path:
    sys.path.insert(0, str(ROOT / "core"))

from core import finviz_discovery, market_research_agent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--groups", default=",".join(market_research_agent.DEFAULT_GROUPS))
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--out-dir", type=Path, default=market_research_agent.DATA_DIR)
    args = parser.parse_args()

    # Importing the active app here means the scheduled job uses the exact same Alpaca
    # matrix implementation, caches, formulas, and local settings as the browser API.
    from core import app
    from core.scanner import run_scan

    def scan_fn(**kwargs):
        return run_scan(
            app.matrix, feed=app.local_settings()[2], workers=1,
            cluster_exp="nearest", bars_fn=None, save_history=False, **kwargs,
        )

    report = market_research_agent.run(
        scan_fn, groups=[part.strip() for part in args.groups.split(",") if part.strip()],
        candidate_limit=max(1, min(args.limit, 30)),
        discovery_fn=lambda: finviz_discovery.discover(limit=45),
    )
    target = market_research_agent.save(report, args.out_dir)
    print(json.dumps({"saved": str(target), "generated_at": report["generated_at"], "errors": report["errors"]}))
    return 0 if report["candidates"]["intraday"] or report["candidates"]["weekly"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
