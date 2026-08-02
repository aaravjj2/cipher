"""Long-window validation of the strongest SPY put-writing candidates.

The monthly discovery lab can accidentally reward the first trading day of the
month. This module uses a separate weekly point-in-time archive to answer two
questions:

1. Does the result survive entries outside month start?
2. Do prior-market-state filters improve outcomes relative to a pure calendar
   rule?

All option fills remain historical minute trade-bar approximations. Historical
NBBO is unavailable, so this module never permits research-grade claims.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import statistics
from collections import Counter
from dataclasses import asdict
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from historical_option_strategy_lab import (
    EXECUTION_ASSUMPTIONS,
    HistoricalOptionResearchDataset,
    _block_bootstrap_mean_ci,
)
from recent_option_strategy_expansion import (
    DTE7,
    EXPIRY,
    PT25,
    PT50,
    PT50_DTE7,
    ExpandedRun,
    ExpandedStrategySpec,
    ExpandedTrade,
    LegTarget,
    RecentPathStore,
    apply_position_cap,
    simulate_expanded_strategy,
    summarize_recent,
)


ROOT = Path(__file__).resolve().parents[2]
WEEKLY_DB = (
    ROOT
    / "cipher-system"
    / "data"
    / "historical_options"
    / "alpaca_spy_weekly_backfill"
    / "historical_options.sqlite"
)
MONTHLY_DB = (
    ROOT
    / "cipher-system"
    / "data"
    / "historical_options"
    / "alpaca_spy_monthly_backfill"
    / "historical_options.sqlite"
)
DEFAULT_OUTPUT = (
    ROOT
    / "cipher-system"
    / "data"
    / "historical_options"
    / "weekly_strategy_validation"
)


class WeeklyValidationError(RuntimeError):
    pass


class DatasetView:
    """Small read-only view accepted by the shared strategy simulator."""

    def __init__(self, parent: HistoricalOptionResearchDataset, snapshots: Sequence[Any]):
        self.parent = parent
        self.snapshots = tuple(sorted(snapshots, key=lambda row: row.decision_date))
        self.daily_closes = parent.daily_closes
        if not self.snapshots:
            raise WeeklyValidationError("dataset view cannot be empty")

    def settlement(self, expiration_date: date) -> tuple[date, float]:
        return self.parent.settlement(expiration_date)


def canonical_weekly_view(dataset: HistoricalOptionResearchDataset) -> DatasetView:
    """Keep the earliest loaded trading day in each ISO week.

    The archive was downloaded in quarterly chunks. A chunk beginning midweek
    can contain an extra boundary date. Selecting the earliest date globally
    removes those chunk artifacts without changing any market observations.
    """

    first_by_week: dict[tuple[int, int], Any] = {}
    for snapshot in sorted(dataset.snapshots, key=lambda row: row.decision_date):
        iso_year, iso_week, _ = snapshot.decision_date.isocalendar()
        first_by_week.setdefault((iso_year, iso_week), snapshot)
    return DatasetView(dataset, tuple(first_by_week.values()))


def first_trading_days_by_month(
    daily_closes: Sequence[tuple[date, float]],
) -> dict[tuple[int, int], date]:
    result: dict[tuple[int, int], date] = {}
    for day, _ in daily_closes:
        result.setdefault((day.year, day.month), day)
    return result


def early_month_trading_days(
    daily_closes: Sequence[tuple[date, float]],
    count: int = 3,
) -> set[date]:
    if count <= 0:
        raise ValueError("count must be positive")
    grouped: dict[tuple[int, int], list[date]] = {}
    for day, _ in daily_closes:
        grouped.setdefault((day.year, day.month), []).append(day)
    return {
        day
        for days in grouped.values()
        for day in sorted(days)[:count]
    }


def validation_specs() -> tuple[ExpandedStrategySpec, ...]:
    """A bounded winner-focused family, not an unrestricted parameter search."""

    specs: list[ExpandedStrategySpec] = []
    always_rules = (EXPIRY, PT25, PT50, PT50_DTE7, DTE7)
    for rule in always_rules:
        specs.append(
            ExpandedStrategySpec(
                f"weekly_m94_always_{rule.name}",
                "csp",
                "always",
                (LegTarget(-1, 0.94),),
                rule,
            )
        )
    setup_signals = (
        "momentum20_positive",
        "trend200",
        "momentum20_and_trend200",
        "mild_pullback_uptrend",
        "elevated_rv_momentum",
    )
    for signal in setup_signals:
        for rule in (EXPIRY, PT50, DTE7):
            specs.append(
                ExpandedStrategySpec(
                    f"weekly_m94_{signal}_{rule.name}",
                    "csp",
                    signal,
                    (LegTarget(-1, 0.94),),
                    rule,
                )
            )
    return tuple(specs)


def _window_trades(
    trades: Sequence[ExpandedTrade],
    start: date,
    end: date,
) -> tuple[ExpandedTrade, ...]:
    return tuple(
        sorted(
            (row for row in trades if start <= row.decision_date <= end),
            key=lambda row: row.decision_date,
        )
    )


def _max_drawdown(trades: Sequence[ExpandedTrade]) -> float:
    running = 0.0
    peak = 0.0
    drawdown = 0.0
    for trade in sorted(trades, key=lambda row: row.decision_date):
        running += trade.pnl
        peak = max(peak, running)
        drawdown = min(drawdown, running - peak)
    return drawdown


def _profit_factor(trades: Sequence[ExpandedTrade]) -> float | str | None:
    gains = sum(row.pnl for row in trades if row.pnl > 0)
    losses = -sum(row.pnl for row in trades if row.pnl < 0)
    if losses > 0:
        return gains / losses
    return "Infinity" if gains > 0 else None


def _enhanced_summary(
    run: ExpandedRun,
    *,
    start: date,
    end: date,
    decision_dates: int,
    seed_text: str,
) -> dict[str, Any]:
    summary = summarize_recent(
        run.trades,
        run.skips,
        start=start,
        end=end,
        decision_dates=decision_dates,
    )
    trades = _window_trades(run.trades, start, end)
    returns = [row.return_on_risk for row in trades]
    seed = int(hashlib.sha256(seed_text.encode("utf-8")).hexdigest()[:8], 16)
    ci_low, ci_high = _block_bootstrap_mean_ci(returns, seed=seed)
    pnls = [row.pnl for row in trades]
    one_position = apply_position_cap(trades, 1)
    one_returns = [row.return_on_risk for row in one_position]
    one_ci_low, one_ci_high = _block_bootstrap_mean_ci(
        one_returns,
        seed=seed ^ 0xA5A5A5A5,
    )
    summary.update(
        {
            "losses": sum(row.pnl < 0 for row in trades),
            "profit_factor": _profit_factor(trades),
            "max_drawdown_pnl": _max_drawdown(trades),
            "mean_return_on_risk_ci_95": [ci_low, ci_high],
            "average_entry_dte": (
                statistics.mean(
                    (row.expiration_date - row.decision_date).days for row in trades
                )
                if trades
                else None
            ),
            "total_pnl_excluding_best_trade": (
                sum(pnls) - max(pnls) if len(pnls) >= 2 else None
            ),
            "total_pnl_excluding_worst_trade": (
                sum(pnls) - min(pnls) if len(pnls) >= 2 else None
            ),
        }
    )
    summary["one_position_cap"].update(
        {
            "losses": sum(row.pnl < 0 for row in one_position),
            "worst_trade_pnl": (
                min((row.pnl for row in one_position), default=None)
            ),
            "profit_factor": _profit_factor(one_position),
            "max_drawdown_pnl": _max_drawdown(one_position),
            "mean_return_on_risk_ci_95": [one_ci_low, one_ci_high],
            "average_days_held": (
                statistics.mean(row.days_held for row in one_position)
                if one_position
                else None
            ),
            "total_pnl_excluding_best_trade": (
                sum(row.pnl for row in one_position)
                - max(row.pnl for row in one_position)
                if len(one_position) >= 2
                else None
            ),
        }
    )
    return summary


def _summary_for_subset(
    trades: Sequence[ExpandedTrade],
    accepted_dates: set[date],
) -> dict[str, Any]:
    subset = tuple(row for row in trades if row.decision_date in accepted_dates)
    if not subset:
        return {
            "trades": 0,
            "total_pnl": 0.0,
            "mean_return_on_risk": None,
            "win_rate": None,
            "worst_trade_pnl": None,
        }
    return {
        "trades": len(subset),
        "total_pnl": sum(row.pnl for row in subset),
        "mean_return_on_risk": statistics.mean(row.return_on_risk for row in subset),
        "win_rate": sum(row.pnl > 0 for row in subset) / len(subset),
        "worst_trade_pnl": min(row.pnl for row in subset),
    }


def _year_windows(view: DatasetView) -> dict[str, tuple[date, date, int]]:
    result: dict[str, tuple[date, date, int]] = {}
    for year in sorted({row.decision_date.year for row in view.snapshots}):
        dates = [row.decision_date for row in view.snapshots if row.decision_date.year == year]
        result[str(year)] = (min(dates), max(dates), len(dates))
    result["full"] = (
        view.snapshots[0].decision_date,
        view.snapshots[-1].decision_date,
        len(view.snapshots),
    )
    return result


def _run_matrix(
    view: DatasetView,
    path_store: RecentPathStore,
    specs: Sequence[ExpandedStrategySpec],
) -> tuple[list[dict[str, Any]], dict[tuple[str, str], ExpandedRun]]:
    windows = _year_windows(view)
    rows: list[dict[str, Any]] = []
    runs: dict[tuple[str, str], ExpandedRun] = {}
    for spec in specs:
        for execution in EXECUTION_ASSUMPTIONS:
            run = simulate_expanded_strategy(view, path_store, spec, execution)
            runs[(spec.name, execution.name)] = run
            row: dict[str, Any] = {
                "strategy": spec.name,
                "signal": spec.signal,
                "management_rule": spec.management.name,
                "execution_assumption": execution.name,
            }
            for label, (start, end, count) in windows.items():
                row[label] = _enhanced_summary(
                    run,
                    start=start,
                    end=end,
                    decision_dates=count,
                    seed_text=f"{spec.name}:{execution.name}:{label}",
                )
            rows.append(row)
    return rows, runs


def _monthly_comparison(
    monthly_dataset: HistoricalOptionResearchDataset,
    specs: Sequence[ExpandedStrategySpec],
) -> list[dict[str, Any]]:
    view = DatasetView(monthly_dataset, monthly_dataset.snapshots)
    path_store = RecentPathStore(monthly_dataset.database_path)
    severe = next(row for row in EXECUTION_ASSUMPTIONS if row.name == "severe")
    result = []
    for spec in specs:
        if spec.signal != "always":
            continue
        run = simulate_expanded_strategy(view, path_store, spec, severe)
        result.append(
            {
                "strategy": spec.name,
                "summary": _enhanced_summary(
                    run,
                    start=view.snapshots[0].decision_date,
                    end=view.snapshots[-1].decision_date,
                    decision_dates=len(view.snapshots),
                    seed_text=f"monthly:{spec.name}",
                ),
            }
        )
    return result


def _stress_candidate(
    database_path: Path,
    base_view: DatasetView,
    spec: ExpandedStrategySpec,
) -> dict[str, Any]:
    severe = next(row for row in EXECUTION_ASSUMPTIONS if row.name == "severe")
    result: dict[str, Any] = {}
    entry_variants = {
        "1545": (time(15, 45), 15),
        "1550": (time(15, 50), 10),
        "1555": (time(15, 55), 5),
    }
    for label, (entry_time, minutes) in entry_variants.items():
        parent = HistoricalOptionResearchDataset(
            database_path,
            entry_time=entry_time,
            entry_window_minutes=minutes,
        )
        canonical = canonical_weekly_view(parent)
        path_store = RecentPathStore(database_path)
        run = simulate_expanded_strategy(canonical, path_store, spec, severe)
        result[f"entry_{label}"] = _enhanced_summary(
            run,
            start=canonical.snapshots[0].decision_date,
            end=canonical.snapshots[-1].decision_date,
            decision_dates=len(canonical.snapshots),
            seed_text=f"stress:{spec.name}:{label}",
        )

    path_store = RecentPathStore(database_path)
    for label, kwargs in {
        "volume_5_confirm_2": {
            "minimum_entry_volume": 5.0,
            "minimum_exit_volume": 5.0,
            "target_confirmations": 2,
        },
        "volume_10": {
            "minimum_entry_volume": 10.0,
            "minimum_exit_volume": 10.0,
            "target_confirmations": 1,
        },
    }.items():
        run = simulate_expanded_strategy(
            base_view,
            path_store,
            spec,
            severe,
            **kwargs,
        )
        result[label] = _enhanced_summary(
            run,
            start=base_view.snapshots[0].decision_date,
            end=base_view.snapshots[-1].decision_date,
            decision_dates=len(base_view.snapshots),
            seed_text=f"stress:{spec.name}:{label}",
        )
    return result


def _candidate_score(row: Mapping[str, Any]) -> tuple[float, float, int]:
    one = row["full"]["one_position_cap"]
    return (
        float(one.get("total_pnl") or -math.inf),
        float(one.get("mean_return_on_risk") or -math.inf),
        int(one.get("trades") or 0),
    )


def run_weekly_validation(
    weekly_db: str | Path = WEEKLY_DB,
    monthly_db: str | Path = MONTHLY_DB,
    *,
    output_directory: str | Path = DEFAULT_OUTPUT,
) -> dict[str, Any]:
    weekly_db = Path(weekly_db)
    monthly_db = Path(monthly_db)
    weekly_parent = HistoricalOptionResearchDataset(weekly_db)
    weekly = canonical_weekly_view(weekly_parent)
    specs = validation_specs()
    rows, runs = _run_matrix(weekly, RecentPathStore(weekly_db), specs)
    severe = [row for row in rows if row["execution_assumption"] == "severe"]

    eligible = [
        row
        for row in severe
        if int(row["full"]["one_position_cap"]["trades"] or 0) >= 15
        and float(row["full"]["one_position_cap"]["total_pnl"] or 0.0) > 0
        and row["2025"]["one_position_cap"]["total_pnl"] > 0
        and row["2026"]["one_position_cap"]["total_pnl"] > 0
    ]
    eligible.sort(key=_candidate_score, reverse=True)

    first_days = set(first_trading_days_by_month(weekly.daily_closes).values())
    early_days = early_month_trading_days(weekly.daily_closes, 3)
    calendar_breakdown: list[dict[str, Any]] = []
    canonical_dates = {snapshot.decision_date for snapshot in weekly.snapshots}
    early = early_days & canonical_dates
    later = canonical_dates - early
    early_view = DatasetView(
        weekly_parent,
        [row for row in weekly.snapshots if row.decision_date in early],
    )
    later_view = DatasetView(
        weekly_parent,
        [row for row in weekly.snapshots if row.decision_date in later],
    )
    severe_execution = next(
        row for row in EXECUTION_ASSUMPTIONS if row.name == "severe"
    )
    path_store = RecentPathStore(weekly_db)
    specs_by_name = {spec.name: spec for spec in specs}
    calendar_portfolio_replays: list[dict[str, Any]] = []
    for row in severe:
        run = runs[(row["strategy"], "severe")]
        calendar_breakdown.append(
            {
                "strategy": row["strategy"],
                "first_trading_day": _summary_for_subset(run.trades, first_days),
                "first_three_trading_days": _summary_for_subset(run.trades, early),
                "later_in_month": _summary_for_subset(run.trades, later),
            }
        )
        spec = specs_by_name[row["strategy"]]
        early_run = simulate_expanded_strategy(
            early_view,
            path_store,
            spec,
            severe_execution,
        )
        later_run = simulate_expanded_strategy(
            later_view,
            path_store,
            spec,
            severe_execution,
        )
        calendar_portfolio_replays.append(
            {
                "strategy": row["strategy"],
                "first_three_trading_days": _enhanced_summary(
                    early_run,
                    start=early_view.snapshots[0].decision_date,
                    end=early_view.snapshots[-1].decision_date,
                    decision_dates=len(early_view.snapshots),
                    seed_text=f"calendar:early:{row['strategy']}",
                ),
                "later_in_month": _enhanced_summary(
                    later_run,
                    start=later_view.snapshots[0].decision_date,
                    end=later_view.snapshots[-1].decision_date,
                    decision_dates=len(later_view.snapshots),
                    seed_text=f"calendar:later:{row['strategy']}",
                ),
            }
        )

    monthly_parent = HistoricalOptionResearchDataset(monthly_db)
    monthly = _monthly_comparison(monthly_parent, specs)

    stress_names = [
        "weekly_m94_always_expiry",
        "weekly_m94_momentum20_positive_expiry",
        "weekly_m94_momentum20_and_trend200_expiry",
        "weekly_m94_always_pt50",
        "weekly_m94_always_dte7",
        "weekly_m94_mild_pullback_uptrend_pt50",
    ]
    stress = {
        name: _stress_candidate(weekly_db, weekly, specs_by_name[name])
        for name in stress_names
    }

    raw_week_dates = [row.decision_date for row in weekly_parent.snapshots]
    canonical_dates = {row.decision_date for row in weekly.snapshots}
    removed_boundary_dates = [
        day.isoformat() for day in raw_week_dates if day not in canonical_dates
    ]

    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "WEEKLY_LONG_WINDOW_EXECUTION_APPROXIMATION_ONLY",
        "research_claims_allowed": False,
        "dataset": {
            "weekly_database": str(weekly_db),
            "monthly_database": str(monthly_db),
            "weekly_raw_decision_dates": len(weekly_parent.snapshots),
            "weekly_canonical_decision_dates": len(weekly.snapshots),
            "weekly_start": weekly.snapshots[0].decision_date.isoformat(),
            "weekly_end": weekly.snapshots[-1].decision_date.isoformat(),
            "removed_chunk_boundary_dates": removed_boundary_dates,
            "historical_nbbo_available": False,
            "execution_classification": "HISTORICAL_OPTION_MINUTE_BAR_APPROXIMATION",
        },
        "protocol": {
            "entry": "15:45 ET; contract selected using only pre-entry observations",
            "target": "SPY put nearest 94% strike/spot moneyness",
            "dte": "28-45 calendar days; target 35",
            "weekly_cadence": "earliest available trading day in each ISO week",
            "portfolio_views": [
                "all qualifying weekly entries, allowing overlap",
                "maximum one open CSP at a time",
            ],
            "fixed_strategy_count": len(specs),
            "execution_assumptions": [asdict(row) for row in EXECUTION_ASSUMPTIONS],
        },
        "ranked_one_position_candidates": eligible,
        "all_results": rows,
        "monthly_first_day_comparison": monthly,
        "calendar_breakdown": calendar_breakdown,
        "calendar_portfolio_replays": calendar_portfolio_replays,
        "candidate_stress": stress,
        "caveats": [
            "Alpaca local option history begins in February 2024, so this is the maximum local period currently available.",
            "Historical option bid/ask, quote size, open interest, IV, and Greeks are absent.",
            "Entry and managed-exit fills are conservative minute-bar approximations, not executable NBBO fills.",
            "Weekly overlapping entries can require several simultaneous cash-secured positions; the one-position replay is the practical capital comparison.",
            "The setup family was fixed before this weekly validation, but multiple comparisons remain and results are exploratory.",
        ],
    }
    write_weekly_validation_outputs(payload, output_directory)
    return payload


def _csv_value(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, sort_keys=True)
    return value


def write_weekly_validation_outputs(
    payload: Mapping[str, Any],
    output_directory: str | Path,
) -> None:
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    (output / "weekly_option_strategy_validation.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False),
        encoding="utf-8",
    )
    with (output / "weekly_option_strategy_rankings.csv").open(
        "w", newline="", encoding="utf-8"
    ) as fh:
        fields = [
            "strategy",
            "signal",
            "management_rule",
            "execution_assumption",
            "full_trades",
            "full_pnl",
            "full_worst_trade",
            "full_peak_capital",
            "one_position_trades",
            "one_position_pnl",
            "one_position_worst_trade",
            "one_position_mean_return",
            "pnl_2024",
            "pnl_2025",
            "pnl_2026",
        ]
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in payload["all_results"]:
            full = row["full"]
            one = full["one_position_cap"]
            writer.writerow(
                {
                    "strategy": row["strategy"],
                    "signal": row["signal"],
                    "management_rule": row["management_rule"],
                    "execution_assumption": row["execution_assumption"],
                    "full_trades": full["trades"],
                    "full_pnl": full["total_pnl"],
                    "full_worst_trade": full["worst_trade_pnl"],
                    "full_peak_capital": full["peak_combined_risk_capital"],
                    "one_position_trades": one["trades"],
                    "one_position_pnl": one["total_pnl"],
                    "one_position_worst_trade": one["worst_trade_pnl"],
                    "one_position_mean_return": one["mean_return_on_risk"],
                    "pnl_2024": row["2024"]["one_position_cap"]["total_pnl"],
                    "pnl_2025": row["2025"]["one_position_cap"]["total_pnl"],
                    "pnl_2026": row["2026"]["one_position_cap"]["total_pnl"],
                }
            )

    lines = [
        "# Weekly SPY CSP Validation",
        "",
        f"**Status:** `{payload['status']}`",
        "",
        "This report tests the strongest 94%-moneyness CSP rules on canonical weekly dates across the maximum local Alpaca history.",
        "",
        "## One-position severe-cost leaders",
        "",
        "| Strategy | Trades | P&L | Worst trade | Mean return/trade | 2024 | 2025 | 2026 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["ranked_one_position_candidates"][:10]:
        one = row["full"]["one_position_cap"]
        lines.append(
            "| {name} | {trades} | ${pnl:,.2f} | ${worst:,.2f} | {mean:.3%} | ${y24:,.2f} | ${y25:,.2f} | ${y26:,.2f} |".format(
                name=row["strategy"],
                trades=one["trades"],
                pnl=one["total_pnl"],
                worst=one["worst_trade_pnl"],
                mean=one["mean_return_on_risk"] or 0.0,
                y24=row["2024"]["one_position_cap"]["total_pnl"],
                y25=row["2025"]["one_position_cap"]["total_pnl"],
                y26=row["2026"]["one_position_cap"]["total_pnl"],
            )
        )
    lines.extend(
        [
            "",
            "## Limitations",
            "",
            *[f"- {item}" for item in payload["caveats"]],
            "",
        ]
    )
    (output / "weekly_option_strategy_validation.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )
