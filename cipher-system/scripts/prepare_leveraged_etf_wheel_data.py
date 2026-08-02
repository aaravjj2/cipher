"""Plan or execute targeted data downloads for the leveraged-ETF wheel lab.

The workflow is intentionally iterative:

1. Download adjusted daily bars for NVDL/TSLL/SOXL/TQQQ.
2. Generate point-in-time-safe down-day signals using the prior completed
   weekly Ripster clouds and weekly RSI.
3. Download only put contracts for those eligible decision dates.
4. Run ``core/leveraged_etf_csp_wheel.py``.
5. Feed its ``data_requests.csv`` back into this script to fetch roll and
   covered-call chains, then rerun the backtest.

No broker or order API is used.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "core"
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

from equity_history_download import build_parser as build_equity_parser
from equity_history_download import run_download as run_equity_download
from historical_options_download import build_parser as build_options_parser
from historical_options_download import run_download as run_options_download
from leveraged_etf_csp_wheel import (
    DEFAULT_ARCHIVE_ROOT,
    DEFAULT_EQUITY_DB,
    DEFAULT_MODES,
    SQLiteWheelMarketData,
    WheelConfig,
    find_entry_signal_days,
    load_universe,
)


DEFAULT_EQUITY_ROOT = DEFAULT_EQUITY_DB.parent
DEFAULT_PLAN_DIR = ROOT / "data" / "leveraged_etf_wheel" / "download_plan"


def _mode_moneyness(mode_name: str) -> tuple[float, float, float]:
    if mode_name == "conservative":
        return 0.80, 1.00, 0.93
    if mode_name == "set_and_forget":
        return 0.88, 1.04, 1.00
    if mode_name == "advanced_assignment":
        return 0.90, 1.03, 0.99
    return 0.75, 1.02, 0.95


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def download_equities(
    symbols: Sequence[str],
    *,
    history_start: date,
    end: date,
    output_root: Path,
) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    parser = build_equity_parser()
    for symbol in symbols:
        args = parser.parse_args(
            [
                "--symbol",
                symbol,
                "--start",
                history_start.isoformat(),
                "--end",
                end.isoformat(),
                "--timeframe",
                "1Day",
                "--output-root",
                str(output_root),
                "--resume",
            ]
        )
        result = run_equity_download(args)
        summaries.append({"symbol": symbol, **result})
    return summaries


def build_initial_requests(
    *,
    equity_db: Path,
    universe_json: Path,
    start: date,
    end: date,
    mode_names: Sequence[str],
    down_day_threshold: float,
    minimum_iv: float,
    maximum_iv: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    universe = load_universe(universe_json)
    data = SQLiteWheelMarketData(equity_db, ())
    signals: list[dict[str, Any]] = []
    requests: list[dict[str, Any]] = []
    try:
        base_config = WheelConfig(
            down_day_threshold=down_day_threshold,
            minimum_iv=minimum_iv,
            maximum_iv=maximum_iv,
        )
        for asset in universe:
            asset_signals = find_entry_signal_days(data, asset, start, end, base_config)
            signals.extend(asset_signals)
            for signal in asset_signals:
                if not signal.get("eligible") or not signal.get("day"):
                    continue
                for mode_name in mode_names:
                    mode = DEFAULT_MODES[mode_name]
                    min_money, max_money, target_money = _mode_moneyness(mode_name)
                    requests.append(
                        {
                            "symbol": asset.symbol,
                            "decision_day": signal["day"],
                            "option_type": "put",
                            "min_dte": mode.min_dte,
                            "max_dte": mode.max_dte,
                            "target_dte": mode.target_dte,
                            "reason": f"initial_{mode_name}_cash_secured_put",
                            "minimum_moneyness": min_money,
                            "maximum_moneyness": max_money,
                            "target_moneyness": target_money,
                            "mode": mode_name,
                            "daily_return_pct": signal.get("daily_return_pct"),
                            "bullish_clouds": signal.get("bullish_clouds"),
                            "weekly_rsi": signal.get("weekly_rsi"),
                            "allocation_fraction": signal.get("allocation_fraction"),
                        }
                    )
    finally:
        data.close()
    return signals, requests


def load_backtest_requests(path: Path) -> list[dict[str, Any]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    output = []
    for row in rows:
        output.append(
            {
                "symbol": str(row["symbol"]).upper(),
                "decision_day": row["decision_day"],
                "option_type": row["option_type"],
                "min_dte": int(float(row["min_dte"])),
                "max_dte": int(float(row["max_dte"])),
                "target_dte": int(float(row["target_dte"])),
                "reason": row.get("reason") or "lifecycle_request",
                "minimum_moneyness": float(row["minimum_moneyness"]),
                "maximum_moneyness": float(row["maximum_moneyness"]),
                "target_moneyness": float(row["target_moneyness"]),
                "mode": "lifecycle",
            }
        )
    return output


def _request_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        row["symbol"],
        row["decision_day"],
        row["option_type"],
        int(row["min_dte"]),
        int(row["max_dte"]),
        int(row["target_dte"]),
        round(float(row["minimum_moneyness"]), 4),
        round(float(row["maximum_moneyness"]), 4),
        round(float(row["target_moneyness"]), 4),
    )


def deduplicate_requests(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    output: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        output.setdefault(_request_key(row), dict(row))
    return [output[key] for key in sorted(output)]


def apply_symbol_cooldown(
    rows: Sequence[Mapping[str, Any]],
    cooldown_days: int,
) -> list[dict[str, Any]]:
    """Keep the first request per symbol/mode after a calendar-day cooldown.

    This is a download-planning optimization, not a backtest assumption. The
    stateful engine still enforces its real collateral and open-position rules.
    It prevents consecutive leveraged-ETF down days from generating many nearly
    identical archives before a pilot run establishes actual holding periods.
    """
    if cooldown_days <= 0:
        return [dict(row) for row in rows]
    output: list[dict[str, Any]] = []
    last_kept: dict[tuple[str, str, str], date] = {}
    for row in sorted(
        rows,
        key=lambda item: (
            str(item["decision_day"]),
            str(item["symbol"]),
            str(item.get("mode") or ""),
            str(item["option_type"]),
        ),
    ):
        key = (
            str(row["symbol"]),
            str(row.get("mode") or ""),
            str(row["option_type"]),
        )
        day = date.fromisoformat(str(row["decision_day"]))
        previous = last_kept.get(key)
        if previous is not None and (day - previous).days < cooldown_days:
            continue
        output.append(dict(row))
        last_kept[key] = day
    return output


def execute_request(
    row: Mapping[str, Any],
    *,
    history_start: date,
    option_root: Path,
    include_trades: bool,
) -> dict[str, Any]:
    symbol = str(row["symbol"]).upper()
    option_type = str(row["option_type"]).lower()
    mode = str(row.get("mode") or "lifecycle")
    reason = str(row.get("reason") or "request")
    root = option_root / symbol.lower() / mode / option_type
    parser = build_options_parser()
    argv = [
        "--underlying",
        symbol,
        "--start",
        str(row["decision_day"]),
        "--end",
        str(row["decision_day"]),
        "--decision-date",
        str(row["decision_day"]),
        "--underlying-history-start",
        history_start.isoformat(),
        "--option-type",
        option_type,
        "--min-dte",
        str(int(row["min_dte"])),
        "--max-dte",
        str(int(row["max_dte"])),
        "--target-dte",
        str(int(row["target_dte"])),
        "--min-moneyness",
        str(float(row["minimum_moneyness"])),
        "--max-moneyness",
        str(float(row["maximum_moneyness"])),
        "--target-moneyness",
        str(float(row["target_moneyness"])),
        "--max-contracts",
        "24",
        "--discovery-contracts",
        "96",
        "--discovery-per-expiry",
        "18",
        "--no-single-expiry",
        "--expiry-policy",
        "any",
        "--batch-size",
        "80",
        "--output-root",
        str(root),
        "--resume",
        "--include-trades" if include_trades else "--no-include-trades",
    ]
    args = parser.parse_args(argv)
    result = run_options_download(args)
    return {
        "request": dict(row),
        "reason": reason,
        "archive": str(root),
        "result": result,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Plan or download leveraged-ETF CSP/wheel historical data."
    )
    parser.add_argument("--start", required=True, help="Option decision-period start")
    parser.add_argument("--end", required=True, help="Option decision-period end")
    parser.add_argument(
        "--history-start",
        default="2022-08-01",
        help="Earlier daily-bar start for weekly 72/89 EMA warm-up",
    )
    parser.add_argument(
        "--mode",
        action="append",
        choices=tuple(DEFAULT_MODES),
        help="Repeat for multiple modes; default is all four modes.",
    )
    parser.add_argument(
        "--universe-json",
        default=str(ROOT / "config" / "leveraged_etf_wheel_universe.json"),
    )
    parser.add_argument("--equity-root", default=str(DEFAULT_EQUITY_ROOT))
    parser.add_argument("--option-root", default=str(DEFAULT_ARCHIVE_ROOT))
    parser.add_argument("--plan-dir", default=str(DEFAULT_PLAN_DIR))
    parser.add_argument("--from-backtest-requests")
    parser.add_argument("--down-day", type=float, default=-0.05)
    parser.add_argument("--minimum-iv", type=float, default=0.40)
    parser.add_argument("--maximum-iv", type=float, default=0.70)
    parser.add_argument("--skip-equity-download", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--include-trades", action="store_true")
    parser.add_argument("--request-offset", type=int, default=0)
    parser.add_argument("--max-requests", type=int, default=0)
    parser.add_argument(
        "--symbol-cooldown-days",
        type=int,
        default=0,
        help="Planning-only cooldown between requests for the same symbol/mode/type.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    history_start = date.fromisoformat(args.history_start)
    if not history_start < start <= end:
        raise SystemExit("require history-start < start <= end")
    universe_json = Path(args.universe_json)
    universe = load_universe(universe_json)
    symbols = [asset.symbol for asset in universe]
    equity_root = Path(args.equity_root)
    option_root = Path(args.option_root)
    plan_dir = Path(args.plan_dir)
    plan_dir.mkdir(parents=True, exist_ok=True)

    equity_summaries: list[dict[str, Any]] = []
    if not args.skip_equity_download:
        equity_summaries = download_equities(
            symbols,
            history_start=history_start,
            end=end,
            output_root=equity_root,
        )

    equity_db = equity_root / "equity_bars.sqlite"
    mode_names = tuple(args.mode or DEFAULT_MODES.keys())
    signals, initial_requests = build_initial_requests(
        equity_db=equity_db,
        universe_json=universe_json,
        start=start,
        end=end,
        mode_names=mode_names,
        down_day_threshold=args.down_day,
        minimum_iv=args.minimum_iv,
        maximum_iv=args.maximum_iv,
    )
    lifecycle_requests = (
        load_backtest_requests(Path(args.from_backtest_requests))
        if args.from_backtest_requests
        else []
    )
    requests = deduplicate_requests([*initial_requests, *lifecycle_requests])
    requests_before_cooldown = len(requests)
    requests = apply_symbol_cooldown(requests, args.symbol_cooldown_days)
    if args.request_offset < 0:
        raise SystemExit("request-offset cannot be negative")
    requests = requests[args.request_offset :]
    if args.max_requests > 0:
        requests = requests[: args.max_requests]

    signals_path = plan_dir / "signals.csv"
    requests_path = plan_dir / "requests.csv"
    summary_path = plan_dir / "summary.json"
    _write_csv(signals_path, signals)
    _write_csv(requests_path, requests)

    downloads: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    if args.execute:
        for index, request in enumerate(requests, 1):
            try:
                result = execute_request(
                    request,
                    history_start=history_start,
                    option_root=option_root,
                    include_trades=args.include_trades,
                )
                downloads.append(result)
                print(
                    json.dumps(
                        {
                            "progress": f"{index}/{len(requests)}",
                            "symbol": request["symbol"],
                            "day": request["decision_day"],
                            "type": request["option_type"],
                            "archive": result["archive"],
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
            except Exception as exc:  # Continue other independent requests.
                failures.append({"request": request, "error": str(exc)})

    summary = {
        "generated_at": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat(),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "history_start": history_start.isoformat(),
        "universe": [asdict(asset) for asset in universe],
        "modes": list(mode_names),
        "signals": len([row for row in signals if row.get("eligible")]),
        "requests_before_cooldown": requests_before_cooldown,
        "symbol_cooldown_days": args.symbol_cooldown_days,
        "request_offset": args.request_offset,
        "requests": len(requests),
        "executed": len(downloads),
        "failures": failures,
        "equity_summaries": equity_summaries,
        "files": {
            "signals": str(signals_path),
            "requests": str(requests_path),
            "summary": str(summary_path),
        },
        "next_step": (
            "Run core/leveraged_etf_csp_wheel.py, then feed its data_requests.csv "
            "back with --from-backtest-requests for roll and covered-call archives."
        ),
    }
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True, default=str), encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
