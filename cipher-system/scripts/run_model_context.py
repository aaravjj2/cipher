#!/usr/bin/env python3
"""Compare local TimesFM and Kronos context without producing a trade signal."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import kronos_research  # noqa: E402
from core.research_platform.model_context import build_model_context_assessment  # noqa: E402
from core.timesfm_walkforward import base_ohlcv_context_forecast  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ticker")
    parser.add_argument("--timeframe", default="5m")
    parser.add_argument("--lookback", type=int, default=128)
    parser.add_argument("--horizon", type=int, default=12)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)

    if args.horizon <= 0 or args.lookback < 32:
        raise SystemExit("--lookback must be at least 32 and --horizon must be positive")
    rows = kronos_research.load_local_ohlcv_rows(args.ticker, args.timeframe)
    if len(rows) < args.lookback:
        raise SystemExit(f"need {args.lookback} local bars for {args.ticker.upper()}; found {len(rows)}")

    context_rows = rows[-args.lookback :]
    kronos_research.set_research_seed(args.seed)
    timesfm = base_ohlcv_context_forecast(
        [row["close"] for row in context_rows], horizon=args.horizon
    )
    predictor = kronos_research.load_predictor(device="cpu", max_context=args.lookback)
    kronos = kronos_research.kronos_forecast_signal(
        predictor,
        args.ticker,
        context_rows[-1]["timestamp"].date().isoformat(),
        timeframe=args.timeframe,
        horizon_days=1,
        lookback=args.lookback,
        max_pred_bars=args.horizon,
    )
    result = build_model_context_assessment(
        last_close=context_rows[-1]["close"],
        timesfm=timesfm,
        kronos=kronos,
        source_end=context_rows[-1]["timestamp"],
    )
    result.update({
        "ticker": args.ticker.upper(),
        "timeframe": args.timeframe,
        "seed": args.seed,
        "source_end": context_rows[-1]["timestamp"].isoformat(),
    })
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
