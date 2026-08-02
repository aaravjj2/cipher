#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import kronos_research  # noqa: E402
from core.timesfm_walkforward import base_ohlcv_context_forecast  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run an unvalidated, context-only TimesFM base forecast.")
    parser.add_argument("ticker")
    parser.add_argument("--timeframe", default="5m")
    parser.add_argument("--lookback", type=int, default=128)
    parser.add_argument("--horizon", type=int, default=12)
    args = parser.parse_args(argv)
    rows = kronos_research.load_local_ohlcv_rows(args.ticker, args.timeframe)
    if len(rows) < args.lookback:
        raise SystemExit(f"need {args.lookback} local bars for {args.ticker.upper()}; found {len(rows)}")
    payload = base_ohlcv_context_forecast(
        [row["close"] for row in rows[-args.lookback :]],
        horizon=args.horizon,
    )
    payload.update({
        "ticker": args.ticker.upper(),
        "timeframe": args.timeframe,
        "source_end": rows[-1]["timestamp"].isoformat(),
    })
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
