"""Focused sensitivity lab for the leveraged-ETF CSP thesis.

This lab uses only the already-acquired immutable option archives.  It does not
search new strategy families.  It varies the most consequential thesis filters:
mode, modeled POP floor, weekly cloud requirement, and down-day threshold.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from statistics import mean
from typing import Any, Sequence

from leveraged_etf_csp_wheel import (
    DEFAULT_MODES,
    ROOT,
    LeveragedEtfWheelBacktester,
    SQLiteWheelMarketData,
    WheelConfig,
    discover_archives,
    load_universe,
)


@dataclass(frozen=True, slots=True)
class Variant:
    mode: str
    minimum_iv: float
    maximum_iv: float
    pop_floor: float | None
    required_bullish_clouds: int
    down_day_threshold: float
    entry_end: date
    archive_roots: tuple[str, ...]

    @property
    def variant_id(self) -> str:
        pop = "none" if self.pop_floor is None else f"{self.pop_floor:.2f}"
        return (
            f"{self.mode}__iv{self.minimum_iv:.2f}-{self.maximum_iv:.2f}"
            f"__pop{pop}__clouds{self.required_bullish_clouds}"
            f"__down{abs(self.down_day_threshold):.2f}"
        )


def _closed_event_pnls(events: Sequence[Any]) -> list[float]:
    names = {
        "buy_to_close_50pct",
        "put_expired_otm",
        "call_expired_otm",
        "call_assigned_shares_called",
        "buy_to_close_for_roll",
    }
    return [
        float(event.realized_pnl)
        for event in events
        if event.event in names and event.realized_pnl is not None
    ]


def _run_variant(
    variant: Variant,
    *,
    equity_db: Path,
    universe_json: Path,
    start: date,
    end: date,
    initial_cash: float,
) -> dict[str, Any]:
    mode = DEFAULT_MODES[variant.mode]
    if variant.pop_floor is None:
        enforce_pop = False
        tolerance = 0.05
    else:
        enforce_pop = True
        if mode.target_pop is None:
            raise ValueError(f"mode {mode.name} has no target POP")
        tolerance = max(float(mode.target_pop) - variant.pop_floor, 0.0)
    config = WheelConfig(
        mode=mode,
        down_day_threshold=variant.down_day_threshold,
        minimum_iv=variant.minimum_iv,
        maximum_iv=variant.maximum_iv,
        required_bullish_clouds=variant.required_bullish_clouds,
        assignment_unwanted=not mode.seeking_assignment,
        enforce_target_pop=enforce_pop,
        target_pop_tolerance=tolerance,
    )
    archive_paths = tuple(
        dict.fromkeys(
            archive
            for root in variant.archive_roots
            for archive in discover_archives(root)
        )
    )
    data = SQLiteWheelMarketData(equity_db, archive_paths, entry_time_et=config.entry_time_et)
    try:
        result = LeveragedEtfWheelBacktester(
            data,
            load_universe(universe_json),
            config,
            initial_cash=initial_cash,
        ).run(start, end, entry_end=variant.entry_end)
    finally:
        data.close()
    event_counts = Counter(event.event for event in result.events)
    pnls = _closed_event_pnls(result.events)
    return {
        "variant_id": variant.variant_id,
        "mode": variant.mode,
        "minimum_iv": variant.minimum_iv,
        "maximum_iv": variant.maximum_iv,
        "pop_floor": variant.pop_floor,
        "required_bullish_clouds": variant.required_bullish_clouds,
        "down_day_threshold": variant.down_day_threshold,
        "entry_end": variant.entry_end.isoformat(),
        "total_return_pct": result.summary["total_return_pct"],
        "max_drawdown_pct": result.summary["max_drawdown_pct"],
        "closed_option_events": result.summary["closed_option_events"],
        "win_rate_pct": result.summary["win_rate_pct"],
        "data_requests": result.summary["data_requests"],
        "open_options": result.summary["open_options"],
        "stock_symbols": result.summary["stock_symbols"],
        "put_entries": event_counts["sell_to_open_put"],
        "call_entries": event_counts["sell_to_open_call"],
        "profit_target_exits": event_counts["buy_to_close_50pct"],
        "put_assignments": event_counts["put_assigned"],
        "rolls": event_counts["roll_completed"],
        "called_away": event_counts["call_assigned_shares_called"],
        "realized_event_pnl_sum": sum(pnls),
        "worst_closed_event_pnl": min(pnls) if pnls else None,
        "best_closed_event_pnl": max(pnls) if pnls else None,
    }


def _variants(args: argparse.Namespace) -> list[Variant]:
    standard_roots = tuple(args.standard_archive_root)
    conservative_roots = tuple(args.conservative_archive_root)
    rows: list[Variant] = []
    specifications = (
        (
            "standard",
            0.65,
            1.00,
            (None, 0.55, 0.60, 0.65, 0.70),
            date.fromisoformat(args.standard_entry_end),
            standard_roots,
        ),
        (
            "conservative",
            0.65,
            1.20,
            (None, 0.60, 0.65, 0.70, 0.75, 0.80),
            date.fromisoformat(args.conservative_entry_end),
            conservative_roots,
        ),
    )
    for mode, min_iv, max_iv, floors, entry_end, roots in specifications:
        for floor in floors:
            for clouds in (2, 3):
                for down_day in (-0.05, -0.06, -0.07):
                    rows.append(
                        Variant(
                            mode=mode,
                            minimum_iv=min_iv,
                            maximum_iv=max_iv,
                            pop_floor=floor,
                            required_bullish_clouds=clouds,
                            down_day_threshold=down_day,
                            entry_end=entry_end,
                            archive_roots=roots,
                        )
                    )
    return rows


def _write_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0]) if rows else []
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _group_summary(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, float | None], list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault((str(row["mode"]), row["pop_floor"]), []).append(row)
    output: list[dict[str, Any]] = []
    for (mode, floor), group in grouped.items():
        complete = [
            row for row in group
            if row["open_options"] == 0 and row["stock_symbols"] == 0
        ]
        source = complete or group
        output.append(
            {
                "mode": mode,
                "pop_floor": floor,
                "variants": len(group),
                "complete_variants": len(complete),
                "mean_return_pct": mean(float(row["total_return_pct"]) for row in source),
                "worst_return_pct": min(float(row["total_return_pct"]) for row in source),
                "best_return_pct": max(float(row["total_return_pct"]) for row in source),
                "mean_max_drawdown_pct": mean(float(row["max_drawdown_pct"]) for row in source),
                "worst_max_drawdown_pct": min(float(row["max_drawdown_pct"]) for row in source),
                "mean_closed_events": mean(float(row["closed_option_events"]) for row in source),
                "mean_data_requests": mean(float(row["data_requests"]) for row in source),
            }
        )
    return sorted(output, key=lambda row: (row["mode"], -999 if row["pop_floor"] is None else row["pop_floor"]))


def _markdown(rows: Sequence[dict[str, Any]], groups: Sequence[dict[str, Any]]) -> str:
    complete = [
        row for row in rows
        if row["open_options"] == 0 and row["stock_symbols"] == 0
    ]
    ranked = sorted(
        complete,
        key=lambda row: (
            float(row["total_return_pct"]) + 0.5 * float(row["max_drawdown_pct"]),
            float(row["total_return_pct"]),
        ),
        reverse=True,
    )
    lines = [
        "# Leveraged-ETF CSP Focused Sensitivity Lab",
        "",
        "This is a post-thesis sensitivity analysis, not an independent holdout. It uses only previously acquired option chains and preserves the 50% premium exit.",
        "",
        "## Top complete variants",
        "",
        "| Rank | Mode | POP floor | Clouds | Down day | Return | Max drawdown | Closed events | Data requests |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for index, row in enumerate(ranked[:15], start=1):
        pop = "None" if row["pop_floor"] is None else f"{float(row['pop_floor']) * 100:.0f}%"
        lines.append(
            f"| {index} | {row['mode']} | {pop} | {row['required_bullish_clouds']} | "
            f"{float(row['down_day_threshold']) * 100:.0f}% | {float(row['total_return_pct']):+.2f}% | "
            f"{float(row['max_drawdown_pct']):.2f}% | {int(row['closed_option_events'])} | {int(row['data_requests'])} |"
        )
    lines.extend(
        [
            "",
            "## POP-floor stability",
            "",
            "| Mode | POP floor | Mean return | Worst return | Best return | Worst drawdown | Mean closed events |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in groups:
        pop = "None" if row["pop_floor"] is None else f"{float(row['pop_floor']) * 100:.0f}%"
        lines.append(
            f"| {row['mode']} | {pop} | {float(row['mean_return_pct']):+.2f}% | "
            f"{float(row['worst_return_pct']):+.2f}% | {float(row['best_return_pct']):+.2f}% | "
            f"{float(row['worst_max_drawdown_pct']):.2f}% | {float(row['mean_closed_events']):.1f} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- Rankings are exploratory because all variants share the same 2026 archive.",
            "- Variants with missing chains are retained and their data-request counts are shown.",
            "- A robust rule should remain positive when requiring three clouds or a 6%-7% down day, not merely maximize the base case.",
            "- Historical NBBO is unavailable, so execution remains based on conservative one-minute trade-bar proxies.",
        ]
    )
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace) -> dict[str, Any]:
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    equity_db = Path(args.equity_db).resolve()
    universe_json = Path(args.universe_json).resolve()
    results = [
        _run_variant(
            variant,
            equity_db=equity_db,
            universe_json=universe_json,
            start=start,
            end=end,
            initial_cash=args.initial_cash,
        )
        for variant in _variants(args)
    ]
    groups = _group_summary(results)
    output = Path(args.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    _write_csv(output / "variants.csv", results)
    _write_csv(output / "pop_floor_summary.csv", groups)
    payload = {
        "start": args.start,
        "end": args.end,
        "variant_count": len(results),
        "variants": results,
        "pop_floor_summary": groups,
    }
    (output / "report.json").write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    (output / "report.md").write_text(_markdown(results, groups), encoding="utf-8")
    return {
        "variant_count": len(results),
        "output_dir": str(output),
        "report": str(output / "report.md"),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run focused leveraged-ETF CSP sensitivity tests.")
    parser.add_argument("--start", default="2026-01-02")
    parser.add_argument("--end", default="2026-07-24")
    parser.add_argument("--standard-entry-end", default="2026-06-19")
    parser.add_argument("--conservative-entry-end", default="2026-07-10")
    parser.add_argument("--initial-cash", type=float, default=100_000.0)
    parser.add_argument(
        "--equity-db",
        default=str(ROOT / "data" / "historical_equities" / "leveraged_etf_wheel" / "equity_bars.sqlite"),
    )
    parser.add_argument(
        "--universe-json",
        default=str(ROOT / "config" / "leveraged_etf_wheel_universe.json"),
    )
    parser.add_argument("--standard-archive-root", action="append", required=True)
    parser.add_argument("--conservative-archive-root", action="append", required=True)
    parser.add_argument(
        "--output-dir",
        default=str(ROOT / "data" / "leveraged_etf_wheel" / "parameter_lab_2026"),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    print(json.dumps(run(build_parser().parse_args(argv)), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
