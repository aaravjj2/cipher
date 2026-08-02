"""Download option history requested by leveraged_etf_csp_wheel.

Consumes the deterministic ``data_requests.csv`` emitted by the wheel engine and
invokes ``historical_options_download.py`` once per unique request. The helper
is intentionally separate from the backtester so research remains reproducible:
first generate requests, then acquire immutable data, then rerun the backtest.

No trading or broker order endpoints are used.
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Sequence


CORE = Path(__file__).resolve().parent
ROOT = CORE.parent
DEFAULT_REQUESTS = ROOT / "data" / "leveraged_etf_wheel" / "standard_initial" / "data_requests.csv"
DEFAULT_OUTPUT_ROOT = ROOT / "data" / "historical_options" / "leveraged_etf_wheel"
DOWNLOADER = CORE / "historical_options_download.py"


@dataclass(frozen=True, slots=True)
class Request:
    symbol: str
    decision_day: str
    option_type: str
    min_dte: int
    max_dte: int
    target_dte: int
    minimum_moneyness: float
    maximum_moneyness: float
    target_moneyness: float
    reason: str

    @classmethod
    def from_row(cls, row: dict[str, str]) -> "Request":
        option_type = row["option_type"].strip().lower()
        if option_type not in {"put", "call"}:
            raise ValueError(f"invalid option type {option_type!r}")
        return cls(
            symbol=row["symbol"].strip().upper(),
            decision_day=row["decision_day"].strip(),
            option_type=option_type,
            min_dte=int(row["min_dte"]),
            max_dte=int(row["max_dte"]),
            target_dte=int(row["target_dte"]),
            minimum_moneyness=float(row["minimum_moneyness"]),
            maximum_moneyness=float(row["maximum_moneyness"]),
            target_moneyness=float(row["target_moneyness"]),
            reason=row.get("reason", "wheel_request").strip() or "wheel_request",
        )

    def key(self) -> tuple[object, ...]:
        return (
            self.symbol,
            self.decision_day,
            self.option_type,
            self.min_dte,
            self.max_dte,
            self.target_dte,
            round(self.minimum_moneyness, 6),
            round(self.maximum_moneyness, 6),
            round(self.target_moneyness, 6),
        )


def load_requests(path: Path) -> list[Request]:
    if not path.exists():
        raise FileNotFoundError(path)
    unique: dict[tuple[object, ...], Request] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            request = Request.from_row(row)
            unique.setdefault(request.key(), request)
    return sorted(unique.values(), key=lambda row: (row.decision_day, row.symbol, row.option_type))


def build_command(request: Request, args: argparse.Namespace) -> list[str]:
    command = [
        sys.executable,
        str(DOWNLOADER),
        "--underlying",
        request.symbol,
        "--start",
        request.decision_day,
        "--end",
        request.decision_day,
        "--decision-date",
        request.decision_day,
        "--option-type",
        request.option_type,
        "--min-dte",
        str(request.min_dte),
        "--max-dte",
        str(request.max_dte),
        "--target-dte",
        str(request.target_dte),
        "--min-moneyness",
        str(request.minimum_moneyness),
        "--max-moneyness",
        str(request.maximum_moneyness),
        "--target-moneyness",
        str(request.target_moneyness),
        "--max-contracts",
        str(args.max_contracts),
        "--discovery-contracts",
        str(args.discovery_contracts),
        "--discovery-per-expiry",
        str(args.discovery_per_expiry),
        "--expiry-policy",
        args.expiry_policy,
        "--output-root",
        str(Path(args.output_root).resolve()),
        "--timeout",
        str(int(args.timeout)),
        "--retries",
        str(args.retries),
    ]
    if args.underlying_lookback_days:
        history_start = date.fromisoformat(request.decision_day) - timedelta(
            days=max(1, int(args.underlying_lookback_days))
        )
        command.extend(("--underlying-history-start", history_start.isoformat()))
    command.append("--include-trades" if args.include_trades else "--no-include-trades")
    command.append("--resume" if args.resume else "--no-resume")
    command.append("--single-expiry" if args.single_expiry else "--no-single-expiry")
    return command


def run(args: argparse.Namespace) -> dict[str, object]:
    requests = load_requests(Path(args.requests).resolve())
    if args.symbol:
        allowed_symbols = {symbol.strip().upper() for symbol in args.symbol if symbol.strip()}
        requests = [request for request in requests if request.symbol in allowed_symbols]
    if args.option_type:
        allowed_types = {value.strip().lower() for value in args.option_type if value.strip()}
        requests = [request for request in requests if request.option_type in allowed_types]
    if args.reason:
        allowed_reasons = {value.strip() for value in args.reason if value.strip()}
        requests = [request for request in requests if request.reason in allowed_reasons]
    if args.start_date:
        requests = [request for request in requests if request.decision_day >= args.start_date]
    if args.end_date:
        requests = [request for request in requests if request.decision_day <= args.end_date]
    if args.offset:
        requests = requests[max(0, args.offset) :]
    if args.limit is not None:
        requests = requests[: max(0, args.limit)]
    completed: list[dict[str, object]] = []
    failed: list[dict[str, object]] = []
    for index, request in enumerate(requests, start=1):
        command = build_command(request, args)
        if args.dry_run:
            completed.append({"request": asdict(request), "command": command, "dry_run": True})
            continue
        process = subprocess.run(command, text=True, capture_output=True, check=False)
        record = {
            "index": index,
            "request": asdict(request),
            "returncode": process.returncode,
            "stdout_tail": process.stdout[-2000:],
            "stderr_tail": process.stderr[-2000:],
        }
        if process.returncode == 0:
            completed.append(record)
        else:
            failed.append(record)
            if not args.continue_on_error:
                break
    summary = {
        "requests_file": str(Path(args.requests).resolve()),
        "output_root": str(Path(args.output_root).resolve()),
        "requested": len(requests),
        "completed": len(completed),
        "failed": len(failed),
        "dry_run": args.dry_run,
        "completed_rows": completed,
        "failed_rows": failed,
    }
    manifest = Path(args.manifest).resolve()
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Download leveraged-ETF wheel option requests.")
    parser.add_argument("--requests", default=str(DEFAULT_REQUESTS))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument(
        "--manifest",
        default=str(ROOT / "data" / "leveraged_etf_wheel" / "download_manifest.json"),
    )
    parser.add_argument("--max-contracts", type=int, default=14)
    parser.add_argument("--discovery-contracts", type=int, default=160)
    parser.add_argument("--discovery-per-expiry", type=int, default=30)
    parser.add_argument("--expiry-policy", choices=("monthly", "friday", "any"), default="any")
    parser.add_argument("--single-expiry", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--include-trades", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--retries", type=int, default=4)
    parser.add_argument(
        "--underlying-lookback-days",
        type=int,
        default=0,
        help="Download daily underlying history this many calendar days before each decision date.",
    )
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--symbol", action="append", help="Restrict to one symbol; repeatable.")
    parser.add_argument(
        "--option-type",
        action="append",
        choices=("put", "call"),
        help="Restrict to an option type; repeatable.",
    )
    parser.add_argument("--reason", action="append", help="Restrict to a request reason; repeatable.")
    parser.add_argument("--start-date", help="Restrict requests to decision dates on or after YYYY-MM-DD.")
    parser.add_argument("--end-date", help="Restrict requests to decision dates on or before YYYY-MM-DD.")
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    summary = run(build_parser().parse_args(argv))
    print(json.dumps({key: value for key, value in summary.items() if not key.endswith("_rows")}, indent=2))
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
