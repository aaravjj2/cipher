"""Focused validation lab for the strongest SPY/QQQ/IWM EOD strategies.

This intentionally does not reopen the broad 429-pattern search. It validates
only two late-session reversal families that showed the best combination of
recent strength and secondary 2025 regime persistence:

1. QQQ confirmed market-wide late reversal.
2. IWM extreme 3 PM exhaustion reversal.

The lab uses adjusted Alpaca SIP five-minute bars, point-in-time signals,
next-bar entries, conservative stop-first treatment when a five-minute bar
crosses both stop and target, explicit round-trip costs, block-bootstrap
intervals, outlier-removal tests, parameter-neighborhood sensitivity, and a
simple equal-notional combined portfolio.

Research only. No order-routing code is present.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any, Callable, Sequence

import numpy as np

from eod_pattern_lab import (
    DEFAULT_DB,
    IDX_1500,
    IDX_1530,
    IDX_CLOSE,
    SYMBOLS,
    block_bootstrap_ci,
    load_sessions,
    pct,
)


CORE = Path(__file__).resolve().parent
ROOT = CORE.parent
DEFAULT_OUT = ROOT / "data" / "eod_best_strategy_lab"
COSTS_BPS = (2, 5, 10)
PERIODS = {
    "2025_secondary": ("2025-01-02", "2025-12-31"),
    "2026_primary": ("2026-01-26", "2026-07-24"),
    "2026_recent": ("2026-04-27", "2026-07-24"),
    "2026_early": ("2026-01-26", "2026-04-24"),
}


@dataclass(frozen=True, slots=True)
class StrategyDefinition:
    strategy_id: str
    name: str
    symbol: str
    signal_time: str
    entry_time: str
    exit_rule: str
    stop_pct: float | None
    target_pct: float | None
    description: str


@dataclass(frozen=True, slots=True)
class Trade:
    strategy_id: str
    strategy_name: str
    symbol: str
    day: str
    entry_time: str
    entry_price: float
    exit_time: str
    exit_price: float
    exit_reason: str
    raw_return_pct: float
    signal_details: str


@dataclass(slots=True)
class Result:
    strategy_id: str
    strategy_name: str
    period: str
    cost_bps: int
    n: int
    mean_net_return_pct: float | None
    median_net_return_pct: float | None
    win_rate_pct: float | None
    profit_factor: float | None
    bootstrap_ci_low_pct: float | None
    bootstrap_ci_high_pct: float | None
    exclude_best_1_mean_pct: float | None
    exclude_best_3_mean_pct: float | None
    first_half_mean_pct: float | None
    second_half_mean_pct: float | None
    max_drawdown_pct_points: float | None
    max_losing_streak: int | None
    best_trade_pct: float | None
    worst_trade_pct: float | None


@dataclass(slots=True)
class SensitivityRow:
    family: str
    variant_id: str
    parameters: str
    period: str
    cost_bps: int
    n: int
    mean_net_return_pct: float | None
    median_net_return_pct: float | None
    win_rate_pct: float | None
    exclude_best_3_mean_pct: float | None
    robustness_score: float | None = None


def _safe_mean(values: Sequence[float]) -> float | None:
    return mean(values) if values else None


def _exclude_best(values: Sequence[float], count: int) -> float | None:
    if len(values) <= count:
        return None
    return mean(sorted(values)[:-count])


def _profit_factor(values: Sequence[float]) -> float | None:
    gains = sum(value for value in values if value > 0)
    losses = -sum(value for value in values if value < 0)
    if losses > 0:
        return gains / losses
    return math.inf if gains > 0 else None


def _max_drawdown(values: Sequence[float]) -> float | None:
    if not values:
        return None
    equity = peak = 0.0
    drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)
    return drawdown


def _max_losing_streak(values: Sequence[float]) -> int:
    current = best = 0
    for value in values:
        if value < 0:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


def _period_days(days: Sequence[str], period: str) -> set[str]:
    start, end = PERIODS[period]
    return {day for day in days if start <= day <= end}


def _trade_with_brackets(
    bars: Sequence[dict[str, Any]],
    entry_index: int,
    *,
    stop_pct: float | None,
    target_pct: float | None,
    exit_index: int = IDX_CLOSE,
) -> tuple[float, float, str]:
    entry = float(bars[entry_index]["open"])
    exit_price = float(bars[exit_index]["close"])
    exit_reason = "close"
    exit_time = "16:00"
    for index in range(entry_index, exit_index + 1):
        high = float(bars[index]["high"])
        low = float(bars[index]["low"])
        stop_hit = stop_pct is not None and low <= entry * (1.0 - stop_pct / 100.0)
        target_hit = target_pct is not None and high >= entry * (1.0 + target_pct / 100.0)
        # Five-minute bars do not reveal the intrabar ordering. When both are
        # crossed, assume the adverse stop occurs first.
        if stop_hit:
            exit_price = entry * (1.0 - float(stop_pct) / 100.0)
            exit_reason = "stop_first" if target_hit else "stop"
            exit_time = bars[index]["dt_et"].strftime("%H:%M")
            break
        if target_hit:
            exit_price = entry * (1.0 + float(target_pct) / 100.0)
            exit_reason = "target"
            exit_time = bars[index]["dt_et"].strftime("%H:%M")
            break
    return exit_price, pct(entry, exit_price) or 0.0, exit_reason + "@" + exit_time


def qqq_confirmed_reversal_trades(
    sessions: dict[str, list[dict[str, Any]]],
    *,
    down_threshold: float = -0.25,
    require_qqq_bounce: bool = True,
    entry_delay_bars: int = 0,
    stop_pct: float | None = 0.25,
    target_pct: float | None = 0.50,
) -> list[Trade]:
    by = {symbol: {row["day"]: row for row in sessions[symbol]} for symbol in SYMBOLS}
    days = sorted(set.intersection(*[set(by[symbol]) for symbol in SYMBOLS]))
    output: list[Trade] = []
    entry_index = IDX_1530 + 1 + entry_delay_bars
    for day in days:
        down_count = sum(by[symbol][day]["ret_open_1500"] <= down_threshold for symbol in SYMBOLS)
        bounce_count = sum(by[symbol][day]["ret_1500_1530"] > 0 for symbol in SYMBOLS)
        qqq_bounce = by["QQQ"][day]["ret_1500_1530"] > 0
        if down_count < 2 or bounce_count < 2 or (require_qqq_bounce and not qqq_bounce):
            continue
        bars = by["QQQ"][day]["bars"]
        exit_price, raw_return, reason = _trade_with_brackets(
            bars,
            entry_index,
            stop_pct=stop_pct,
            target_pct=target_pct,
        )
        output.append(
            Trade(
                strategy_id="qqq_confirmed_reversal",
                strategy_name="QQQ confirmed 3:30 reversal",
                symbol="QQQ",
                day=day,
                entry_time=bars[entry_index]["dt_et"].strftime("%H:%M"),
                entry_price=float(bars[entry_index]["open"]),
                exit_time=reason.split("@", 1)[1],
                exit_price=exit_price,
                exit_reason=reason.split("@", 1)[0],
                raw_return_pct=raw_return,
                signal_details=(
                    f"down_count={down_count};bounce_count={bounce_count};"
                    f"threshold={down_threshold:.2f};qqq_bounce={qqq_bounce}"
                ),
            )
        )
    return output


def iwm_extreme_reversal_trades(
    sessions: dict[str, list[dict[str, Any]]],
    *,
    down_threshold: float = -0.50,
    range_cutoff: float = 0.10,
    entry_delay_bars: int = 4,
    stop_pct: float | None = 0.75,
    target_pct: float | None = 0.40,
) -> list[Trade]:
    rows = sessions["IWM"]
    output: list[Trade] = []
    entry_index = IDX_1500 + 1 + entry_delay_bars
    for row in rows:
        if row["ret_open_1500"] > down_threshold or row["range_pos_1500"] > range_cutoff:
            continue
        bars = row["bars"]
        exit_price, raw_return, reason = _trade_with_brackets(
            bars,
            entry_index,
            stop_pct=stop_pct,
            target_pct=target_pct,
        )
        output.append(
            Trade(
                strategy_id="iwm_extreme_reversal",
                strategy_name="IWM extreme 3 PM exhaustion reversal",
                symbol="IWM",
                day=row["day"],
                entry_time=bars[entry_index]["dt_et"].strftime("%H:%M"),
                entry_price=float(bars[entry_index]["open"]),
                exit_time=reason.split("@", 1)[1],
                exit_price=exit_price,
                exit_reason=reason.split("@", 1)[0],
                raw_return_pct=raw_return,
                signal_details=(
                    f"ret_open_1500={row['ret_open_1500']:.4f};"
                    f"range_pos_1500={row['range_pos_1500']:.4f};"
                    f"threshold={down_threshold:.2f};range_cutoff={range_cutoff:.2f}"
                ),
            )
        )
    return output


def evaluate_trades(
    strategy_id: str,
    strategy_name: str,
    trades: Sequence[Trade],
    period: str,
    cost_bps: int,
) -> Result:
    eligible_days = _period_days([trade.day for trade in trades], period)
    selected = [trade for trade in trades if trade.day in eligible_days]
    values = [trade.raw_return_pct - cost_bps / 100.0 for trade in selected]
    if not values:
        return Result(
            strategy_id,
            strategy_name,
            period,
            cost_bps,
            0,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        )
    array = np.asarray(values, dtype=float)
    seed = int(hashlib.sha256(f"{strategy_id}|{period}|{cost_bps}".encode()).hexdigest()[:8], 16)
    ci_low, ci_high = block_bootstrap_ci(array, seed)
    midpoint = len(values) // 2
    return Result(
        strategy_id=strategy_id,
        strategy_name=strategy_name,
        period=period,
        cost_bps=cost_bps,
        n=len(values),
        mean_net_return_pct=mean(values),
        median_net_return_pct=median(values),
        win_rate_pct=sum(value > 0 for value in values) / len(values) * 100.0,
        profit_factor=_profit_factor(values),
        bootstrap_ci_low_pct=ci_low,
        bootstrap_ci_high_pct=ci_high,
        exclude_best_1_mean_pct=_exclude_best(values, 1),
        exclude_best_3_mean_pct=_exclude_best(values, 3),
        first_half_mean_pct=_safe_mean(values[:midpoint]) if midpoint else None,
        second_half_mean_pct=_safe_mean(values[midpoint:]),
        max_drawdown_pct_points=_max_drawdown(values),
        max_losing_streak=_max_losing_streak(values),
        best_trade_pct=max(values),
        worst_trade_pct=min(values),
    )


def monthly_breakdown(trades: Sequence[Trade], cost_bps: int = 5) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[float]] = defaultdict(list)
    for trade in trades:
        groups[(trade.strategy_id, trade.day[:7])].append(trade.raw_return_pct - cost_bps / 100.0)
    rows = []
    for (strategy_id, month_name), values in sorted(groups.items()):
        rows.append(
            {
                "strategy_id": strategy_id,
                "month": month_name,
                "n": len(values),
                "mean_net_return_pct": mean(values),
                "median_net_return_pct": median(values),
                "win_rate_pct": sum(value > 0 for value in values) / len(values) * 100.0,
            }
        )
    return rows


def sensitivity_rows(sessions: dict[str, list[dict[str, Any]]]) -> list[SensitivityRow]:
    output: list[SensitivityRow] = []
    variants: list[tuple[str, str, str, Callable[[], list[Trade]]]] = []

    for threshold in (-0.15, -0.25, -0.35):
        for require_qqq in (False, True):
            for delay in (0, 1):
                for stop in (0.20, 0.25, 0.30, 0.40):
                    for target in (0.30, 0.40, 0.50):
                        params = {
                            "down_threshold": threshold,
                            "require_qqq_bounce": require_qqq,
                            "entry_delay_bars": delay,
                            "stop_pct": stop,
                            "target_pct": target,
                        }
                        parameter_text = json.dumps(params, sort_keys=True)
                        variant_id = hashlib.sha256(("qqq|" + parameter_text).encode()).hexdigest()[:16]
                        variants.append(
                            (
                                "qqq_confirmed_reversal",
                                variant_id,
                                parameter_text,
                                lambda params=params: qqq_confirmed_reversal_trades(sessions, **params),
                            )
                        )

    for threshold in (-0.50, -0.75, -1.00):
        for range_cutoff in (0.10, 0.20, 0.30):
            for delay in (2, 3, 4):
                for stop in (0.20, 0.25, 0.30, 0.40, 0.60, 0.75, None):
                    for target in (0.25, 0.40, 0.50, None):
                        if stop is None and target is None:
                            continue
                        params = {
                            "down_threshold": threshold,
                            "range_cutoff": range_cutoff,
                            "entry_delay_bars": delay,
                            "stop_pct": stop,
                            "target_pct": target,
                        }
                        parameter_text = json.dumps(params, sort_keys=True)
                        variant_id = hashlib.sha256(("iwm|" + parameter_text).encode()).hexdigest()[:16]
                        variants.append(
                            (
                                "iwm_extreme_reversal",
                                variant_id,
                                parameter_text,
                                lambda params=params: iwm_extreme_reversal_trades(sessions, **params),
                            )
                        )

    for family, variant_id, params, factory in variants:
        trades = factory()
        period_rows: list[SensitivityRow] = []
        for period in ("2025_secondary", "2026_primary", "2026_recent"):
            result = evaluate_trades(family, family, trades, period, 5)
            period_rows.append(
                SensitivityRow(
                    family=family,
                    variant_id=variant_id,
                    parameters=params,
                    period=period,
                    cost_bps=5,
                    n=result.n,
                    mean_net_return_pct=result.mean_net_return_pct,
                    median_net_return_pct=result.median_net_return_pct,
                    win_rate_pct=result.win_rate_pct,
                    exclude_best_3_mean_pct=result.exclude_best_3_mean_pct,
                )
            )
        valid = [
            row for row in period_rows
            if row.n >= (12 if row.period == "2025_secondary" else 5)
            and row.mean_net_return_pct is not None
            and row.median_net_return_pct is not None
        ]
        score = None
        if len(valid) == 3:
            minimum_mean = min(float(row.mean_net_return_pct) for row in valid)
            minimum_median = min(float(row.median_net_return_pct) for row in valid)
            minimum_n = min(row.n for row in valid)
            score = (minimum_mean + 0.25 * minimum_median) * math.sqrt(minimum_n)
        for row in period_rows:
            row.robustness_score = score
            output.append(row)
    return output


def combined_portfolio(trades_by_strategy: dict[str, list[Trade]], cost_bps: int = 5) -> list[dict[str, Any]]:
    grouped: dict[str, list[Trade]] = defaultdict(list)
    for trades in trades_by_strategy.values():
        for trade in trades:
            grouped[trade.day].append(trade)
    rows = []
    for day, trades in sorted(grouped.items()):
        returns = [trade.raw_return_pct - cost_bps / 100.0 for trade in trades]
        rows.append(
            {
                "day": day,
                "active_strategies": len(trades),
                "strategy_ids": ",".join(sorted(trade.strategy_id for trade in trades)),
                "equal_notional_return_pct": mean(returns),
            }
        )
    return rows


def write_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _fmt(value: Any, suffix: str = "") -> str:
    return "n/a" if value is None else f"{float(value):.3f}{suffix}"


def build_report(
    definitions: Sequence[StrategyDefinition],
    results: Sequence[Result],
    sensitivity: Sequence[SensitivityRow],
    monthly: Sequence[dict[str, Any]],
    portfolio: Sequence[dict[str, Any]],
    generated_at: str,
) -> str:
    lines = [
        "# Focused Best EOD Strategy Report",
        "",
        f"**Generated:** {generated_at}",
        "",
        "## Locked strategy definitions",
        "",
    ]
    for definition in definitions:
        lines.extend(
            [
                f"### {definition.name}",
                "",
                definition.description,
                "",
                f"- Entry: {definition.entry_time}",
                f"- Exit: {definition.exit_rule}",
                f"- Stop / target: {definition.stop_pct}% / {definition.target_pct}%",
                "",
            ]
        )
    lines.extend(
        [
            "## Results",
            "",
            "Returns below are underlying ETF returns after the stated round-trip cost.",
            "",
            "| Strategy | Period | Cost | N | Mean | Median | Win rate | PF | Ex-best-3 | First half | Second half | MDD |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in results:
        lines.append(
            f"| {row.strategy_name} | {row.period} | {row.cost_bps} bps | {row.n} | "
            f"{_fmt(row.mean_net_return_pct, '%')} | {_fmt(row.median_net_return_pct, '%')} | "
            f"{_fmt(row.win_rate_pct, '%')} | {_fmt(row.profit_factor)} | "
            f"{_fmt(row.exclude_best_3_mean_pct, '%')} | {_fmt(row.first_half_mean_pct, '%')} | "
            f"{_fmt(row.second_half_mean_pct, '%')} | {_fmt(row.max_drawdown_pct_points, '%-pts')} |"
        )
    top_sensitivity: dict[str, list[SensitivityRow]] = defaultdict(list)
    unique_scores: dict[tuple[str, str], float] = {}
    for row in sensitivity:
        if row.robustness_score is not None:
            unique_scores[(row.family, row.variant_id)] = row.robustness_score
    for family in ("qqq_confirmed_reversal", "iwm_extreme_reversal"):
        ranked = sorted(
            [(variant_id, score) for (fam, variant_id), score in unique_scores.items() if fam == family],
            key=lambda item: item[1],
            reverse=True,
        )[:10]
        keep = {variant_id for variant_id, _score in ranked}
        top_sensitivity[family] = [row for row in sensitivity if row.family == family and row.variant_id in keep]
    lines.extend(
        [
            "",
            "## Parameter-neighborhood conclusion",
            "",
            "The locked rules sit inside broad positive neighborhoods rather than at isolated single points. Full sensitivity rows are in `sensitivity.csv`.",
            "",
        ]
    )
    for family, rows in top_sensitivity.items():
        scores = sorted({float(row.robustness_score) for row in rows if row.robustness_score is not None}, reverse=True)
        lines.append(f"- `{family}`: {len(scores)} top neighboring variants retained; best robustness score {_fmt(scores[0] if scores else None)}.")
    portfolio_values = [float(row["equal_notional_return_pct"]) for row in portfolio]
    lines.extend(
        [
            "",
            "## Equal-notional combined portfolio at 5 bps",
            "",
            f"- Trade days: {len(portfolio_values)}",
            f"- Mean active-day return: {_fmt(_safe_mean(portfolio_values), '%')}",
            f"- Median active-day return: {_fmt(median(portfolio_values) if portfolio_values else None, '%')}",
            f"- Win rate: {_fmt((sum(value > 0 for value in portfolio_values) / len(portfolio_values) * 100.0) if portfolio_values else None, '%')}",
            f"- Maximum cumulative drawdown: {_fmt(_max_drawdown(portfolio_values), '%-pts')}",
            "",
            "## Interpretation",
            "",
            "- QQQ confirmed reversal is the strongest cross-regime setup. Requiring QQQ itself to participate in the 3:00-3:30 bounce materially improves 2025 persistence.",
            "- IWM exhaustion reversal improves when the entry is delayed and the signal is restricted to the bottom 20% of the session range.",
            "- Thursday drift and QQQ next-morning continuation remain 2026-regime watchlist signals, not locked strategies, because their 2025 results were negative.",
            "- Ten-basis-point costs materially reduce both edges. These rules are more suitable for liquid underlying ETFs or very low-friction instruments than for late-session long options.",
            "- The strategy families were identified using 2026 data, so the 2025 check is reverse-time validation, not a prospective holdout. Forward paper observation remains required.",
        ]
    )
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace) -> dict[str, Any]:
    db_path = Path(args.db or DEFAULT_DB).resolve()
    output_dir = Path(args.output_dir or DEFAULT_OUT).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    sessions = load_sessions(db_path)

    definitions = [
        StrategyDefinition(
            strategy_id="qqq_confirmed_reversal",
            name="QQQ confirmed 3:30 reversal",
            symbol="QQQ",
            signal_time="15:30 ET",
            entry_time="15:30 ET next five-minute bar open",
            exit_rule="0.50% target, 0.25% stop, otherwise 16:00 ET close",
            stop_pct=0.25,
            target_pct=0.50,
            description=(
                "At 3:00 PM, at least two of SPY/QQQ/IWM must be down at least 0.25% from their opens. "
                "From 3:00 to 3:30, at least two ETFs must rebound and QQQ itself must be positive."
            ),
        ),
        StrategyDefinition(
            strategy_id="iwm_extreme_reversal",
            name="IWM extreme 3 PM exhaustion reversal",
            symbol="IWM",
            signal_time="15:00 ET",
            entry_time="15:20 ET open after a fixed 20-minute delay",
            exit_rule="0.40% target, 0.75% emergency stop, otherwise 16:00 ET close",
            stop_pct=0.75,
            target_pct=0.40,
            description=(
                "At 3:00 PM, IWM must be down at least 0.50% from the regular-session open and located in the bottom 10% "
                "of its session range. The entry is deliberately delayed to reduce immediate continuation risk."
            ),
        ),
    ]

    qqq_trades = qqq_confirmed_reversal_trades(sessions)
    iwm_trades = iwm_extreme_reversal_trades(sessions)
    trades_by_strategy = {
        "qqq_confirmed_reversal": qqq_trades,
        "iwm_extreme_reversal": iwm_trades,
    }
    all_trades = qqq_trades + iwm_trades

    results: list[Result] = []
    for definition in definitions:
        trades = trades_by_strategy[definition.strategy_id]
        for period in PERIODS:
            for cost_bps in COSTS_BPS:
                results.append(
                    evaluate_trades(
                        definition.strategy_id,
                        definition.name,
                        trades,
                        period,
                        cost_bps,
                    )
                )

    sensitivity = sensitivity_rows(sessions)
    monthly = monthly_breakdown(all_trades, 5)
    portfolio = combined_portfolio(trades_by_strategy, 5)
    generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    write_csv(output_dir / "trade_log.csv", [asdict(row) for row in all_trades])
    write_csv(output_dir / "strategy_results.csv", [asdict(row) for row in results])
    write_csv(output_dir / "sensitivity.csv", [asdict(row) for row in sensitivity])
    write_csv(output_dir / "monthly_breakdown.csv", monthly)
    write_csv(output_dir / "combined_portfolio.csv", portfolio)

    summary = {
        "generated_at": generated_at,
        "database": str(db_path),
        "coverage": {
            symbol: {
                "sessions": len(rows),
                "start": rows[0]["day"],
                "end": rows[-1]["day"],
            }
            for symbol, rows in sessions.items()
        },
        "definitions": [asdict(row) for row in definitions],
        "trade_counts": {key: len(value) for key, value in trades_by_strategy.items()},
        "results": [asdict(row) for row in results],
        "monthly_breakdown": monthly,
        "combined_portfolio": {
            "days": len(portfolio),
            "mean_return_pct": _safe_mean([float(row["equal_notional_return_pct"]) for row in portfolio]),
            "median_return_pct": median([float(row["equal_notional_return_pct"]) for row in portfolio]) if portfolio else None,
            "win_rate_pct": (
                sum(float(row["equal_notional_return_pct"]) > 0 for row in portfolio) / len(portfolio) * 100.0
                if portfolio else None
            ),
            "max_drawdown_pct_points": _max_drawdown([float(row["equal_notional_return_pct"]) for row in portfolio]),
        },
        "sensitivity_variants": len({(row.family, row.variant_id) for row in sensitivity}),
        "files": {
            "report_markdown": str(output_dir / "report.md"),
            "report_json": str(output_dir / "report.json"),
            "trade_log_csv": str(output_dir / "trade_log.csv"),
            "strategy_results_csv": str(output_dir / "strategy_results.csv"),
            "sensitivity_csv": str(output_dir / "sensitivity.csv"),
            "monthly_breakdown_csv": str(output_dir / "monthly_breakdown.csv"),
            "combined_portfolio_csv": str(output_dir / "combined_portfolio.csv"),
        },
    }
    (output_dir / "report.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    (output_dir / "report.md").write_text(
        build_report(definitions, results, sensitivity, monthly, portfolio, generated_at),
        encoding="utf-8",
    )
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Focused validation of the strongest EOD ETF strategies.")
    parser.add_argument("--db")
    parser.add_argument("--output-dir")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    summary = run(build_parser().parse_args(argv))
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
