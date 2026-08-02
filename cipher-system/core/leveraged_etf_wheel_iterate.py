"""Iteratively complete wheel backtests without over-downloading option chains.

The wheel engine emits deterministic data_requests.csv files whenever a required
put, roll, or covered-call chain is missing.  Downloading every repeated covered
call request at once is wasteful because the first opened call changes all later
portfolio states.  This orchestrator therefore:

1. runs the wheel backtest;
2. selects the earliest unresolved covered-call and roll request per symbol,
   plus bounded missing new-put requests;
3. downloads those immutable option windows into a secondary archive;
4. reruns until no new actionable request remains.

No broker or order endpoint is used.
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


CORE = Path(__file__).resolve().parent
ROOT = CORE.parent
BACKTESTER = CORE / "leveraged_etf_csp_wheel.py"
DOWNLOADER = CORE / "leveraged_etf_wheel_download.py"


@dataclass(frozen=True, slots=True)
class RequestRow:
    symbol: str
    decision_day: str
    option_type: str
    min_dte: int
    max_dte: int
    target_dte: int
    reason: str
    minimum_moneyness: float
    maximum_moneyness: float
    target_moneyness: float

    @classmethod
    def from_dict(cls, row: dict[str, str]) -> "RequestRow":
        return cls(
            symbol=str(row["symbol"]).strip().upper(),
            decision_day=str(row["decision_day"]).strip(),
            option_type=str(row["option_type"]).strip().lower(),
            min_dte=int(row["min_dte"]),
            max_dte=int(row["max_dte"]),
            target_dte=int(row["target_dte"]),
            reason=str(row.get("reason") or "wheel_request").strip(),
            minimum_moneyness=float(row["minimum_moneyness"]),
            maximum_moneyness=float(row["maximum_moneyness"]),
            target_moneyness=float(row["target_moneyness"]),
        )

    def key(self) -> tuple[object, ...]:
        return (
            self.symbol,
            self.decision_day,
            self.option_type,
            self.min_dte,
            self.max_dte,
            self.target_dte,
            self.reason,
            round(self.minimum_moneyness, 8),
            round(self.maximum_moneyness, 8),
            round(self.target_moneyness, 8),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "decision_day": self.decision_day,
            "option_type": self.option_type,
            "min_dte": self.min_dte,
            "max_dte": self.max_dte,
            "target_dte": self.target_dte,
            "reason": self.reason,
            "minimum_moneyness": self.minimum_moneyness,
            "maximum_moneyness": self.maximum_moneyness,
            "target_moneyness": self.target_moneyness,
        }


def _run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, check=False)


def _load_requests(path: Path) -> list[RequestRow]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return [RequestRow.from_dict(row) for row in csv.DictReader(handle)]


def _write_requests(path: Path, rows: Iterable[RequestRow]) -> None:
    selected = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "symbol",
        "decision_day",
        "option_type",
        "min_dte",
        "max_dte",
        "target_dte",
        "reason",
        "minimum_moneyness",
        "maximum_moneyness",
        "target_moneyness",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in selected:
            writer.writerow(row.to_dict())


def _select_actionable(
    requests: Sequence[RequestRow],
    attempted: set[tuple[object, ...]],
    *,
    max_new_puts: int,
) -> list[RequestRow]:
    unique: dict[tuple[object, ...], RequestRow] = {}
    for request in requests:
        unique.setdefault(request.key(), request)
    pending = [row for row in unique.values() if row.key() not in attempted]
    pending.sort(key=lambda row: (row.decision_day, row.symbol, row.option_type, row.reason))

    selected: list[RequestRow] = []
    for reason in ("covered_call", "defensive_roll"):
        by_symbol: dict[str, RequestRow] = {}
        for row in pending:
            if row.reason == reason:
                by_symbol.setdefault(row.symbol, row)
        selected.extend(by_symbol.values())

    new_puts = [row for row in pending if row.reason == "new_cash_secured_put"]
    selected.extend(new_puts[: max(0, max_new_puts)])

    known_reasons = {"covered_call", "defensive_roll", "new_cash_secured_put"}
    other_by_symbol_reason: dict[tuple[str, str], RequestRow] = {}
    for row in pending:
        if row.reason not in known_reasons:
            other_by_symbol_reason.setdefault((row.symbol, row.reason), row)
    selected.extend(other_by_symbol_reason.values())

    deduped: dict[tuple[object, ...], RequestRow] = {}
    for row in selected:
        deduped.setdefault(row.key(), row)
    return sorted(deduped.values(), key=lambda row: (row.decision_day, row.symbol, row.reason))


def _backtest_command(args: argparse.Namespace, output_dir: Path) -> list[str]:
    command = [
        sys.executable,
        str(BACKTESTER),
        "--start",
        args.start,
        "--end",
        args.end,
        "--mode",
        args.mode,
        "--initial-cash",
        str(args.initial_cash),
        "--equity-db",
        str(Path(args.equity_db).resolve()),
        "--archive-root",
        str(Path(args.archive_root).resolve()),
        "--additional-archive-root",
        str(Path(args.secondary_archive_root).resolve()),
        "--universe-json",
        str(Path(args.universe_json).resolve()),
        "--output-dir",
        str(output_dir.resolve()),
        "--down-day",
        str(args.down_day),
        "--minimum-iv",
        str(args.minimum_iv),
        "--maximum-iv",
        str(args.maximum_iv),
        "--required-bullish-clouds",
        str(args.required_bullish_clouds),
    ]
    if args.minimum_pop is not None:
        command.extend(("--minimum-pop", str(args.minimum_pop)))
    if args.entry_end:
        command.extend(("--entry-end", args.entry_end))
    if args.relax_pop:
        command.append("--relax-pop")
    if args.aggressive_scaling:
        command.append("--aggressive-scaling")
    if args.seeking_assignment:
        command.append("--seeking-assignment")
    return command


def _download_command(args: argparse.Namespace, request_file: Path, manifest: Path) -> list[str]:
    command = [
        sys.executable,
        str(DOWNLOADER),
        "--requests",
        str(request_file.resolve()),
        "--output-root",
        str(Path(args.secondary_archive_root).resolve()),
        "--manifest",
        str(manifest.resolve()),
        "--max-contracts",
        str(args.max_contracts),
        "--discovery-contracts",
        str(args.discovery_contracts),
        "--discovery-per-expiry",
        str(args.discovery_per_expiry),
        "--expiry-policy",
        args.expiry_policy,
        "--timeout",
        str(args.timeout),
        "--retries",
        str(args.retries),
        "--continue-on-error",
        "--no-include-trades",
    ]
    command.append("--single-expiry" if args.single_expiry else "--no-single-expiry")
    return command


def run(args: argparse.Namespace) -> dict[str, object]:
    output_root = Path(args.output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    Path(args.secondary_archive_root).resolve().mkdir(parents=True, exist_ok=True)

    attempted: set[tuple[object, ...]] = set()
    iterations: list[dict[str, object]] = []
    final_report: dict[str, object] | None = None
    stop_reason = "maximum_iterations_reached"

    for iteration in range(args.max_iterations + 1):
        iteration_dir = output_root / f"iteration_{iteration:02d}"
        process = _run(_backtest_command(args, iteration_dir))
        if process.returncode != 0:
            raise RuntimeError(
                f"backtest iteration {iteration} failed: "
                f"{(process.stderr or process.stdout)[-2000:]}"
            )
        report_path = iteration_dir / "report.json"
        final_report = json.loads(report_path.read_text(encoding="utf-8"))
        requests = _load_requests(iteration_dir / "data_requests.csv")
        actionable = _select_actionable(requests, attempted, max_new_puts=args.max_new_puts)
        row: dict[str, object] = {
            "iteration": iteration,
            "summary": final_report.get("summary", {}),
            "request_count": len(requests),
            "actionable_count": len(actionable),
            "actionable": [request.to_dict() for request in actionable],
            "backtest_stdout_tail": process.stdout[-1000:],
        }
        iterations.append(row)

        if not requests:
            stop_reason = "no_data_requests"
            break
        if not actionable:
            stop_reason = "no_new_actionable_requests"
            break
        if iteration >= args.max_iterations:
            break

        request_file = output_root / f"requests_iteration_{iteration:02d}.csv"
        manifest = output_root / f"download_iteration_{iteration:02d}.json"
        _write_requests(request_file, actionable)
        for request in actionable:
            attempted.add(request.key())
        download = _run(_download_command(args, request_file, manifest))
        row["download_returncode"] = download.returncode
        row["download_stdout_tail"] = download.stdout[-1000:]
        row["download_stderr_tail"] = download.stderr[-1000:]
        if manifest.exists():
            row["download_manifest"] = json.loads(manifest.read_text(encoding="utf-8"))

    payload = {
        "mode": args.mode,
        "start": args.start,
        "end": args.end,
        "entry_end": args.entry_end,
        "stop_reason": stop_reason,
        "attempted_request_count": len(attempted),
        "iterations": iterations,
        "final_report": final_report,
    }
    report_path = output_root / "iterative_report.json"
    report_path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    payload["report_path"] = str(report_path)
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Iteratively complete a leveraged-ETF wheel backtest.")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--entry-end")
    parser.add_argument(
        "--mode",
        choices=("standard", "conservative", "set_and_forget", "advanced_assignment"),
        required=True,
    )
    parser.add_argument("--initial-cash", type=float, default=100_000.0)
    parser.add_argument(
        "--equity-db",
        default=str(ROOT / "data" / "historical_equities" / "leveraged_etf_wheel" / "equity_bars.sqlite"),
    )
    parser.add_argument("--archive-root", required=True)
    parser.add_argument("--secondary-archive-root", required=True)
    parser.add_argument(
        "--universe-json",
        default=str(ROOT / "config" / "leveraged_etf_wheel_universe.json"),
    )
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--down-day", type=float, default=-0.05)
    parser.add_argument("--minimum-iv", type=float, default=0.40)
    parser.add_argument("--maximum-iv", type=float, default=0.70)
    parser.add_argument("--minimum-pop", type=float)
    parser.add_argument(
        "--required-bullish-clouds",
        type=int,
        choices=(1, 2, 3),
        default=2,
    )
    parser.add_argument("--relax-pop", action="store_true")
    parser.add_argument("--aggressive-scaling", action="store_true")
    parser.add_argument("--seeking-assignment", action="store_true")
    parser.add_argument("--max-iterations", type=int, default=40)
    parser.add_argument("--max-new-puts", type=int, default=8)
    parser.add_argument("--max-contracts", type=int, default=24)
    parser.add_argument("--discovery-contracts", type=int, default=240)
    parser.add_argument("--discovery-per-expiry", type=int, default=40)
    parser.add_argument("--expiry-policy", choices=("monthly", "friday", "any"), default="any")
    parser.add_argument("--single-expiry", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--retries", type=int, default=4)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    payload = run(build_parser().parse_args(argv))
    print(
        json.dumps(
            {
                "stop_reason": payload["stop_reason"],
                "attempted_request_count": payload["attempted_request_count"],
                "final_summary": (payload.get("final_report") or {}).get("summary", {}),
                "report_path": payload["report_path"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
