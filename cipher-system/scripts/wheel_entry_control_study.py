#!/usr/bin/env python3
"""Run a leveraged-ETF wheel backtest against a matched random-entry control.

The runs already in `data/leveraged_etf_wheel/` report what the strategy earned but never
what random entry would have earned with the same machinery, so they cannot say whether
the down-day and weekly-cloud filters contribute anything. This produces that comparison.

    python3 scripts/wheel_entry_control_study.py \
        --equity-db data/historical_bars.sqlite \
        --archive-root data/historical_options \
        --start 2024-02-01 --end 2026-06-01 --replicates 40

Writes one JSON report. It does not overwrite or reinterpret any existing run.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

CORE = Path(__file__).resolve().parents[1] / "core"
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

import leveraged_etf_csp_wheel as wheel  # noqa: E402
from wheel_entry_control import RandomEntryBacktester, SignalRateProbe, summarize_control  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--equity-db", type=Path, required=True)
    parser.add_argument("--archive-root", type=Path, required=True)
    parser.add_argument("--additional-archive-root", type=Path, action="append", default=[])
    parser.add_argument("--universe-json", type=Path)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--mode", default="standard", choices=sorted(wheel.DEFAULT_MODES))
    parser.add_argument("--initial-cash", type=float, default=100_000.0)
    parser.add_argument(
        "--replicates",
        type=int,
        default=40,
        help="Control runs. Below ~20 the empirical position is too coarse to read.",
    )
    parser.add_argument("--seed", type=int, default=0, help="Base seed; replicate i uses seed+i.")
    parser.add_argument("--metric", default="total_return_pct")
    parser.add_argument("--output-dir", type=Path, default=Path("data/leveraged_etf_wheel"))
    parser.add_argument("--relax-pop", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.replicates < 2:
        raise SystemExit("--replicates must be at least 2; a control needs a distribution")
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)

    config = wheel.WheelConfig(mode=wheel.DEFAULT_MODES[args.mode], enforce_target_pop=not args.relax_pop)
    universe = wheel.load_universe(args.universe_json)
    archives = tuple(
        dict.fromkeys(
            archive
            for root in [args.archive_root, *args.additional_archive_root]
            for archive in wheel.discover_archives(root)
        )
    )
    if not archives:
        raise SystemExit(f"no option archives discovered under {args.archive_root}")

    data = wheel.SQLiteWheelMarketData(args.equity_db, archives, entry_time_et=config.entry_time_et)
    try:
        probe = SignalRateProbe(data, universe, config, initial_cash=args.initial_cash)
        actual = probe.run(start, end)
        rates = probe.entry_rates()
        if not rates:
            raise SystemExit(
                "no eligible entry days were found, so there is no rate to match; "
                "widen the window or check the archives"
            )
        print(f"signal fired on {sum(probe.fires.values())} of {sum(probe.opportunities.values())} eligible days")

        controls = []
        for index in range(args.replicates):
            control = RandomEntryBacktester(
                data, universe, config,
                initial_cash=args.initial_cash,
                entry_rates=rates,
                seed=args.seed + index,
            )
            controls.append(control.run(start, end))
            print(f"  control {index + 1}/{args.replicates}: "
                  f"{controls[-1].summary.get(args.metric)}", flush=True)

        report = summarize_control(actual, controls, metric=args.metric, entry_rates=rates)
    finally:
        data.close()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    path = args.output_dir / f"entry_control_study_{args.mode}_{stamp}.json"
    path.write_text(
        json.dumps(
            {
                "window": {"start": args.start, "end": args.end},
                "mode": args.mode,
                "replicates": args.replicates,
                "base_seed": args.seed,
                "signal_opportunities": dict(probe.opportunities),
                "signal_fires": dict(probe.fires),
                "actual_summary": actual.summary,
                "control": report,
            },
            indent=2,
            sort_keys=True,
            default=str,
        ),
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
