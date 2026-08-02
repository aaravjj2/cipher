"""Nested walk-forward validation for SPY/QQQ/IWM EOD option patterns.

The broad EOD option lab deliberately enumerates many pattern/contract variants.
This module asks the harder question: could a rule-selection process using only
past data have selected variants that worked in the next unseen month?

Methodology
-----------
* Uses the fixed 429 point-in-time underlying patterns from ``eod_pattern_lab``.
* Reuses the immutable contract-level outcomes produced by
  ``eod_option_pattern_lab``; it does not resimulate or mutate the archive.
* For each holdout month, all earlier sessions are training data.
* Contract bucket, structure, and direction are selected on training data only.
* Pattern variants are deduplicated by training-signal overlap before selection.
* Portfolio results allow at most one option trade per ETF per signal day.
* Base, worse, and severe execution outcomes are reported for the same selected
  rules; worse/severe results are never used to repair a failed holdout.

Research only. Historical option NBBO, IV, and Greeks are unavailable, so the
underlying outcome archive remains a conservative trade-bar approximation.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any, Iterable, Sequence

import numpy as np

from eod_pattern_lab import SYMBOLS, build_patterns, load_sessions
from eod_option_pattern_lab import (
    BUCKETS,
    EXECUTION_MODELS,
    STRUCTURES,
    RECENT_START_DAY,
    _result_id,
    resolve_timing,
)


CORE = Path(__file__).resolve().parent
ROOT = CORE.parent
DEFAULT_EQUITY_DB = ROOT / "data" / "historical_equities" / "alpaca_eod_indices" / "equity_bars.sqlite"
DEFAULT_OUTCOMES = ROOT / "data" / "eod_option_pattern_lab" / "daily_option_outcomes.csv"
DEFAULT_OUT = ROOT / "data" / "eod_option_walkforward"
ANALYSIS_START = date(2026, 1, 26)
EXECUTION_NAMES = tuple(model.name for model in EXECUTION_MODELS)


@dataclass(frozen=True, slots=True)
class CandidateKey:
    pattern_id: str
    symbol: str
    family: str
    name: str
    signal_time: str
    holding_period: str
    tested_side: str
    direction_mode: str
    actual_side: str
    bucket: str
    structure: str

    @property
    def variant_id(self) -> str:
        raw = "|".join(
            (
                self.pattern_id,
                self.direction_mode,
                self.actual_side,
                self.bucket,
                self.structure,
            )
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


@dataclass(slots=True)
class CandidateHistory:
    key: CandidateKey
    signal_days: set[str]
    outcomes: dict[str, dict[str, dict[str, Any]]]


@dataclass(slots=True)
class TrainScore:
    policy: str
    holdout_month: str
    variant_id: str
    pattern_id: str
    symbol: str
    family: str
    name: str
    signal_time: str
    holding_period: str
    tested_side: str
    direction_mode: str
    actual_side: str
    bucket: str
    structure: str
    signal_n: int
    executed_n: int
    coverage_pct: float
    mean_return_pct: float
    median_return_pct: float
    trimmed_mean_return_pct: float
    winsorized_mean_return_pct: float
    exclude_best_1_mean_pct: float | None
    exclude_best_3_mean_pct: float | None
    win_rate_pct: float
    profit_factor: float | None
    worse_mean_return_pct: float | None
    severe_mean_return_pct: float | None
    training_months: int
    positive_training_months: int
    median_month_mean_pct: float | None
    last_month_mean_pct: float | None
    score: float
    selected_rank: int | None = None
    overlap_cluster: int | None = None


@dataclass(slots=True)
class OosTrade:
    policy: str
    holdout_month: str
    selected_rank: int
    variant_id: str
    pattern_id: str
    symbol: str
    family: str
    name: str
    actual_side: str
    bucket: str
    structure: str
    signal_day: str
    entry_day: str
    entry_time_et: str
    exit_day: str
    exit_time_et: str
    execution_model: str
    contract: str | None
    risk_capital_dollars: float
    pnl_dollars: float
    return_on_risk_pct: float
    training_score: float


def _opposite(side: str) -> str:
    return "short" if side == "long" else "long"


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _bool(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes"}


def _month(day: str) -> str:
    return day[:7]


def _safe_mean(values: Sequence[float]) -> float | None:
    return mean(values) if values else None


def _trimmed_mean(values: Sequence[float], fraction: float = 0.10) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    trim = int(len(ordered) * fraction)
    if trim and len(ordered) > trim * 2:
        ordered = ordered[trim:-trim]
    return mean(ordered)


def _winsorized_mean(values: Sequence[float], fraction: float = 0.10) -> float | None:
    if not values:
        return None
    array = np.asarray(values, dtype=float)
    if len(array) < 4:
        return float(array.mean())
    low, high = np.quantile(array, [fraction, 1.0 - fraction])
    return float(np.clip(array, low, high).mean())


def _exclude_best(values: Sequence[float], count: int) -> float | None:
    if len(values) <= count:
        return None
    return mean(sorted(values)[:-count])


def _profit_factor(pnls: Sequence[float]) -> float | None:
    gains = sum(value for value in pnls if value > 0)
    losses = -sum(value for value in pnls if value < 0)
    if losses > 0:
        return gains / losses
    return math.inf if gains > 0 else None


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def load_outcomes(path: Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for raw in csv.DictReader(handle):
            row: dict[str, Any] = dict(raw)
            for key in (
                "entry_debit",
                "exit_value",
                "fees_dollars",
                "risk_capital_dollars",
                "pnl_dollars",
                "return_on_risk_pct",
                "entry_delay_minutes",
                "exit_delay_minutes",
            ):
                row[key] = _finite(row.get(key))
            row["close_entry_hypothetical"] = _bool(row.get("close_entry_hypothetical"))
            rows[str(row["cache_key"])] = row
    return rows


def build_candidate_histories(
    sessions: dict[str, list[dict[str, Any]]],
    outcome_map: dict[str, dict[str, Any]],
) -> list[CandidateHistory]:
    patterns = build_patterns(sessions)
    rows_by_symbol_day = {
        symbol: {row["day"]: row for row in sessions[symbol] if row["day"] >= ANALYSIS_START.isoformat()}
        for symbol in SYMBOLS
    }
    ordered_by_symbol = {
        symbol: [row for row in sessions[symbol] if row["day"] >= ANALYSIS_START.isoformat()]
        for symbol in SYMBOLS
    }
    next_by_symbol_day: dict[str, dict[str, dict[str, Any] | None]] = {}
    for symbol, rows in ordered_by_symbol.items():
        next_by_symbol_day[symbol] = {
            row["day"]: (rows[index + 1] if index + 1 < len(rows) else None)
            for index, row in enumerate(rows)
        }

    histories: list[CandidateHistory] = []
    for spec in patterns:
        valid_days = {
            observation.day
            for observation in spec.observations
            if observation.day >= ANALYSIS_START.isoformat()
        }
        if not valid_days:
            continue
        for direction_mode in ("as_tested", "inverse"):
            actual_side = spec.side if direction_mode == "as_tested" else _opposite(spec.side)
            for bucket in BUCKETS:
                for structure in STRUCTURES:
                    key = CandidateKey(
                        pattern_id=spec.pattern_id,
                        symbol=spec.symbol,
                        family=spec.family,
                        name=spec.name,
                        signal_time=spec.signal_time,
                        holding_period=spec.holding_period,
                        tested_side=spec.side,
                        direction_mode=direction_mode,
                        actual_side=actual_side,
                        bucket=bucket,
                        structure=structure,
                    )
                    outcomes: dict[str, dict[str, dict[str, Any]]] = {}
                    for day in sorted(valid_days):
                        row = rows_by_symbol_day[spec.symbol].get(day)
                        if row is None:
                            continue
                        timing = resolve_timing(
                            spec,
                            row,
                            next_by_symbol_day[spec.symbol].get(day),
                        )
                        if timing is None:
                            continue
                        by_execution: dict[str, dict[str, Any]] = {}
                        for execution_name in EXECUTION_NAMES:
                            cache_key = _result_id(
                                (
                                    spec.symbol,
                                    day,
                                    actual_side,
                                    bucket,
                                    structure,
                                    execution_name,
                                    timing.entry_day,
                                    timing.entry_clock.isoformat(timespec="minutes"),
                                    timing.exit_day,
                                    timing.exit_clock.isoformat(timespec="minutes"),
                                    timing.selection_checkpoint,
                                )
                            )
                            outcome = outcome_map.get(cache_key)
                            if outcome is not None:
                                by_execution[execution_name] = outcome
                        if by_execution:
                            outcomes[day] = by_execution
                    histories.append(CandidateHistory(key=key, signal_days=valid_days, outcomes=outcomes))
    return histories


def training_score(
    history: CandidateHistory,
    train_days: set[str],
    policy: str,
    holdout_month: str,
) -> TrainScore | None:
    signal_days = history.signal_days & train_days
    base_rows = [
        history.outcomes[day]["base"]
        for day in sorted(signal_days)
        if day in history.outcomes
        and "base" in history.outcomes[day]
        and history.outcomes[day]["base"].get("status") == "executed"
        and not history.outcomes[day]["base"].get("close_entry_hypothetical")
        and history.outcomes[day]["base"].get("return_on_risk_pct") is not None
    ]
    signal_n = len(signal_days)
    executed_n = len(base_rows)
    if not signal_n or not executed_n:
        return None
    returns = [float(row["return_on_risk_pct"]) for row in base_rows]
    pnls = [float(row["pnl_dollars"]) for row in base_rows if row.get("pnl_dollars") is not None]
    coverage = executed_n / signal_n
    mean_return = mean(returns)
    median_return = median(returns)
    trimmed = float(_trimmed_mean(returns) or 0.0)
    winsorized = float(_winsorized_mean(returns) or 0.0)
    exclude_best_1 = _exclude_best(returns, 1)
    exclude_best_3 = _exclude_best(returns, 3)

    def execution_mean(name: str) -> float | None:
        values = [
            float(history.outcomes[day][name]["return_on_risk_pct"])
            for day in sorted(signal_days)
            if day in history.outcomes
            and name in history.outcomes[day]
            and history.outcomes[day][name].get("status") == "executed"
            and not history.outcomes[day][name].get("close_entry_hypothetical")
            and history.outcomes[day][name].get("return_on_risk_pct") is not None
        ]
        return _safe_mean(values)

    worse_mean = execution_mean("worse")
    severe_mean = execution_mean("severe")
    monthly_values: dict[str, list[float]] = defaultdict(list)
    for row in base_rows:
        monthly_values[_month(str(row["day"]))].append(float(row["return_on_risk_pct"]))
    monthly_means = {
        month_name: mean(month_values)
        for month_name, month_values in sorted(monthly_values.items())
        if len(month_values) >= 2
    }
    median_month_mean = median(monthly_means.values()) if monthly_means else None
    last_month_mean = monthly_means[max(monthly_means)] if monthly_means else None
    positive_months = sum(value > 0 for value in monthly_means.values())

    if policy == "permissive":
        eligible = (
            executed_n >= 8
            and coverage >= 0.60
            and mean_return > 0
            and winsorized > 0
            and (exclude_best_1 or -math.inf) > -5.0
        )
        score = min(mean_return, winsorized) * math.sqrt(executed_n)
    elif policy == "robust":
        eligible = (
            executed_n >= 10
            and coverage >= 0.70
            and mean_return > 0
            and median_return > 0
            and trimmed > 0
            and winsorized > 0
            and (exclude_best_1 or -math.inf) > 0
            and (worse_mean or -math.inf) > 0
        )
        score = min(trimmed, winsorized, float(worse_mean or -math.inf)) * math.sqrt(executed_n)
    elif policy == "monthly_stable":
        eligible = (
            executed_n >= 10
            and coverage >= 0.70
            and mean_return > 0
            and median_return > 0
            and trimmed > 0
            and winsorized > 0
            and (exclude_best_1 or -math.inf) > 0
            and (worse_mean or -math.inf) > 0
            and len(monthly_means) >= 2
            and positive_months >= max(2, math.ceil(len(monthly_means) * 0.60))
            and (median_month_mean or -math.inf) > 0
            and (last_month_mean or -math.inf) > 0
        )
        score = min(
            trimmed,
            winsorized,
            float(worse_mean or -math.inf),
            float(median_month_mean or -math.inf),
            float(last_month_mean or -math.inf),
        ) * math.sqrt(executed_n)
    elif policy == "strict":
        eligible = (
            executed_n >= 12
            and coverage >= 0.80
            and median_return > 0
            and trimmed > 0
            and winsorized > 0
            and (exclude_best_3 or -math.inf) > 0
            and (severe_mean or -math.inf) > 0
            and len(monthly_means) >= 2
            and positive_months >= max(2, math.ceil(len(monthly_means) * 0.67))
            and (median_month_mean or -math.inf) > 0
            and (last_month_mean or -math.inf) > 0
        )
        score = min(
            median_return,
            trimmed,
            winsorized,
            float(exclude_best_3 or -math.inf),
            float(severe_mean or -math.inf),
            float(median_month_mean or -math.inf),
            float(last_month_mean or -math.inf),
        ) * math.sqrt(executed_n)
    else:
        raise ValueError(f"unknown policy {policy!r}")
    if not eligible or not math.isfinite(score) or score <= 0:
        return None

    return TrainScore(
        policy=policy,
        holdout_month=holdout_month,
        variant_id=history.key.variant_id,
        pattern_id=history.key.pattern_id,
        symbol=history.key.symbol,
        family=history.key.family,
        name=history.key.name,
        signal_time=history.key.signal_time,
        holding_period=history.key.holding_period,
        tested_side=history.key.tested_side,
        direction_mode=history.key.direction_mode,
        actual_side=history.key.actual_side,
        bucket=history.key.bucket,
        structure=history.key.structure,
        signal_n=signal_n,
        executed_n=executed_n,
        coverage_pct=coverage * 100.0,
        mean_return_pct=mean_return,
        median_return_pct=median_return,
        trimmed_mean_return_pct=trimmed,
        winsorized_mean_return_pct=winsorized,
        exclude_best_1_mean_pct=exclude_best_1,
        exclude_best_3_mean_pct=exclude_best_3,
        win_rate_pct=sum(value > 0 for value in returns) / len(returns) * 100.0,
        profit_factor=_profit_factor(pnls),
        worse_mean_return_pct=worse_mean,
        severe_mean_return_pct=severe_mean,
        training_months=len(monthly_means),
        positive_training_months=positive_months,
        median_month_mean_pct=median_month_mean,
        last_month_mean_pct=last_month_mean,
        score=score,
    )


def select_candidates(
    scored: list[tuple[TrainScore, CandidateHistory]],
    *,
    max_candidates: int,
    overlap_threshold: float,
    train_days: set[str],
) -> list[tuple[TrainScore, CandidateHistory]]:
    # First retain only the best contract variant for each pattern/direction.
    best_by_pattern_direction: dict[tuple[str, str], tuple[TrainScore, CandidateHistory]] = {}
    for item in sorted(scored, key=lambda pair: pair[0].score, reverse=True):
        score, _history = item
        key = (score.pattern_id, score.direction_mode)
        best_by_pattern_direction.setdefault(key, item)

    selected: list[tuple[TrainScore, CandidateHistory]] = []
    cluster_index = 0
    for score, history in sorted(best_by_pattern_direction.values(), key=lambda pair: pair[0].score, reverse=True):
        days = history.signal_days & train_days
        overlaps = []
        for chosen_score, chosen_history in selected:
            if chosen_score.symbol != score.symbol:
                continue
            if chosen_score.actual_side != score.actual_side:
                continue
            if chosen_score.holding_period != score.holding_period:
                continue
            overlaps.append(_jaccard(days, chosen_history.signal_days & train_days))
        if overlaps and max(overlaps) >= overlap_threshold:
            continue
        cluster_index += 1
        score.selected_rank = len(selected) + 1
        score.overlap_cluster = cluster_index
        selected.append((score, history))
        if len(selected) >= max_candidates:
            break
    return selected


def holdout_trades(
    selected: Sequence[tuple[TrainScore, CandidateHistory]],
    holdout_days: set[str],
    execution_name: str,
) -> list[OosTrade]:
    possible: list[tuple[TrainScore, dict[str, Any]]] = []
    for score, history in selected:
        for day in sorted(history.signal_days & holdout_days):
            row = history.outcomes.get(day, {}).get(execution_name)
            if (
                row is None
                or row.get("status") != "executed"
                or row.get("close_entry_hypothetical")
                or row.get("return_on_risk_pct") is None
                or row.get("pnl_dollars") is None
                or row.get("risk_capital_dollars") is None
            ):
                continue
            possible.append((score, row))

    # One trade per ETF and signal day. If overlapping selected patterns point to
    # the same event, use the highest training score, never the holdout return.
    chosen: dict[tuple[str, str], tuple[TrainScore, dict[str, Any]]] = {}
    for score, row in sorted(possible, key=lambda pair: pair[0].score, reverse=True):
        event_key = (score.symbol, str(row["day"]))
        chosen.setdefault(event_key, (score, row))

    trades: list[OosTrade] = []
    for score, row in sorted(chosen.values(), key=lambda pair: (pair[1]["entry_day"], pair[1]["entry_time_et"], pair[0].symbol)):
        contract = row.get("long_contract")
        if row.get("short_contract"):
            contract = f"{contract}/{row.get('short_contract')}"
        trades.append(
            OosTrade(
                policy=score.policy,
                holdout_month=score.holdout_month,
                selected_rank=int(score.selected_rank or 0),
                variant_id=score.variant_id,
                pattern_id=score.pattern_id,
                symbol=score.symbol,
                family=score.family,
                name=score.name,
                actual_side=score.actual_side,
                bucket=score.bucket,
                structure=score.structure,
                signal_day=str(row["day"]),
                entry_day=str(row["entry_day"]),
                entry_time_et=str(row["entry_time_et"]),
                exit_day=str(row["exit_day"]),
                exit_time_et=str(row["exit_time_et"]),
                execution_model=execution_name,
                contract=str(contract) if contract else None,
                risk_capital_dollars=float(row["risk_capital_dollars"]),
                pnl_dollars=float(row["pnl_dollars"]),
                return_on_risk_pct=float(row["return_on_risk_pct"]),
                training_score=score.score,
            )
        )
    return trades


def summarize_trades(trades: Sequence[OosTrade]) -> dict[str, Any]:
    returns = [row.return_on_risk_pct for row in trades]
    pnls = [row.pnl_dollars for row in trades]
    risk = [row.risk_capital_dollars for row in trades]
    if not trades:
        return {
            "trades": 0,
            "mean_return_pct": None,
            "median_return_pct": None,
            "win_rate_pct": None,
            "total_pnl_dollars": 0.0,
            "total_risk_deployed_dollars": 0.0,
            "pnl_on_deployed_risk_pct": None,
            "profit_factor": None,
            "max_drawdown_dollars": None,
        }
    equity = peak = 0.0
    max_drawdown = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity - peak)
    total_risk = sum(risk)
    return {
        "trades": len(trades),
        "mean_return_pct": mean(returns),
        "median_return_pct": median(returns),
        "win_rate_pct": sum(value > 0 for value in returns) / len(returns) * 100.0,
        "total_pnl_dollars": sum(pnls),
        "total_risk_deployed_dollars": total_risk,
        "pnl_on_deployed_risk_pct": sum(pnls) / total_risk * 100.0 if total_risk else None,
        "profit_factor": _profit_factor(pnls),
        "max_drawdown_dollars": max_drawdown,
    }


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


def build_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# EOD Options Nested Walk-Forward Validation",
        "",
        f"**Generated:** {summary['generated_at']}",
        "",
        "## Method",
        "",
        "Each holdout month is unseen when variants are selected. Training uses every earlier session, chooses at most one contract variant per pattern/direction, removes highly overlapping patterns, and permits at most one trade per ETF per signal day.",
        "",
        "The same selected rules are then repriced under base, worse, and severe execution assumptions. This is still a historical trade-bar approximation because historical NBBO, IV, and Greeks are unavailable.",
        "",
        "## Aggregate out-of-sample result",
        "",
        "| Policy | Execution | Months | Selected rules | Trades | Mean | Median | Wins | P/L | P/L on deployed risk | MDD |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summary["aggregate_results"]:
        def fmt(value: Any, suffix: str = "") -> str:
            return "n/a" if value is None else f"{float(value):.2f}{suffix}"
        lines.append(
            f"| {row['policy']} | {row['execution_model']} | {row['months']} | {row['selected_rules']} | "
            f"{row['trades']} | {fmt(row['mean_return_pct'], '%')} | {fmt(row['median_return_pct'], '%')} | "
            f"{fmt(row['win_rate_pct'], '%')} | ${row['total_pnl_dollars']:.2f} | "
            f"{fmt(row['pnl_on_deployed_risk_pct'], '%')} | {fmt(row['max_drawdown_dollars'])} |"
        )
    lines.extend(
        [
            "",
            "## Fold results",
            "",
            "| Policy | Holdout | Execution | Training sessions | Eligible variants | Selected | Trades | Mean | Median | P/L |",
            "|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in summary["fold_results"]:
        def fmt2(value: Any) -> str:
            return "n/a" if value is None else f"{float(value):.2f}%"
        lines.append(
            f"| {row['policy']} | {row['holdout_month']} | {row['execution_model']} | {row['train_sessions']} | "
            f"{row['eligible_variants']} | {row['selected_rules']} | {row['trades']} | "
            f"{fmt2(row['mean_return_pct'])} | {fmt2(row['median_return_pct'])} | ${row['total_pnl_dollars']:.2f} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- A policy selecting zero rules is a valid result: the training evidence did not clear its gate.",
            "- Positive permissive results do not override robust or strict failures; they quantify the cost of looser selection.",
            "- Monthly folds are sequential but still drawn from one recent six-month regime. Continued forward observation is required.",
            "- Results are one-contract event studies and do not imply suitable position sizing or live deployment.",
            "",
            "## Output files",
            "",
            f"- `{summary['files']['selected_candidates_csv']}`",
            f"- `{summary['files']['oos_trades_csv']}`",
            f"- `{summary['files']['fold_results_csv']}`",
            f"- `{summary['files']['report_json']}`",
        ]
    )
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace) -> dict[str, Any]:
    equity_db = Path(args.equity_db or DEFAULT_EQUITY_DB).resolve()
    outcomes_path = Path(args.outcomes or DEFAULT_OUTCOMES).resolve()
    output_dir = Path(args.output_dir or DEFAULT_OUT).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    sessions = load_sessions(equity_db)
    for symbol in SYMBOLS:
        sessions[symbol] = [row for row in sessions[symbol] if row["day"] >= ANALYSIS_START.isoformat()]
    outcome_map = load_outcomes(outcomes_path)
    histories = build_candidate_histories(sessions, outcome_map)
    history_by_id = {history.key.variant_id: history for history in histories}

    common_days = sorted(set.intersection(*[{row["day"] for row in sessions[symbol]} for symbol in SYMBOLS]))
    months = sorted({_month(day) for day in common_days})
    holdout_months = []
    for month_name in months:
        train_days = {day for day in common_days if day < f"{month_name}-01"}
        if len(train_days) >= args.min_train_sessions:
            holdout_months.append(month_name)

    policies = ("permissive", "robust", "monthly_stable", "strict")
    all_selected: list[TrainScore] = []
    all_trades: list[OosTrade] = []
    fold_rows: list[dict[str, Any]] = []

    for holdout_month in holdout_months:
        prior_days = [day for day in common_days if day < f"{holdout_month}-01"]
        if args.train_window_sessions and args.train_window_sessions > 0:
            prior_days = prior_days[-args.train_window_sessions :]
        train_days = set(prior_days)
        holdout_days = {day for day in common_days if _month(day) == holdout_month}
        for policy in policies:
            scored_pairs: list[tuple[TrainScore, CandidateHistory]] = []
            for history in histories:
                score = training_score(history, train_days, policy, holdout_month)
                if score is not None:
                    scored_pairs.append((score, history))
            selected = select_candidates(
                scored_pairs,
                max_candidates=args.max_candidates,
                overlap_threshold=args.overlap_threshold,
                train_days=train_days,
            )
            all_selected.extend(score for score, _history in selected)
            for execution_name in EXECUTION_NAMES:
                trades = holdout_trades(selected, holdout_days, execution_name)
                all_trades.extend(trades)
                metrics = summarize_trades(trades)
                fold_rows.append(
                    {
                        "policy": policy,
                        "holdout_month": holdout_month,
                        "execution_model": execution_name,
                        "train_sessions": len(train_days),
                        "holdout_sessions": len(holdout_days),
                        "eligible_variants": len(scored_pairs),
                        "selected_rules": len(selected),
                        **metrics,
                    }
                )

    aggregate_rows: list[dict[str, Any]] = []
    for policy in policies:
        selected_rules = len([row for row in all_selected if row.policy == policy])
        for execution_name in EXECUTION_NAMES:
            trades = [
                row for row in all_trades
                if row.policy == policy and row.execution_model == execution_name
            ]
            metrics = summarize_trades(trades)
            aggregate_rows.append(
                {
                    "policy": policy,
                    "execution_model": execution_name,
                    "months": len(holdout_months),
                    "selected_rules": selected_rules,
                    **metrics,
                }
            )

    selected_csv = output_dir / "selected_candidates_by_fold.csv"
    trades_csv = output_dir / "out_of_sample_trades.csv"
    fold_csv = output_dir / "fold_results.csv"
    write_csv(selected_csv, [asdict(row) for row in all_selected])
    write_csv(trades_csv, [asdict(row) for row in all_trades])
    write_csv(fold_csv, fold_rows)

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "analysis_start": ANALYSIS_START.isoformat(),
        "analysis_end": common_days[-1] if common_days else None,
        "holdout_months": holdout_months,
        "patterns": len(build_patterns(sessions)),
        "candidate_variants": len(histories),
        "outcome_rows_loaded": len(outcome_map),
        "selection_policies": list(policies),
        "max_candidates_per_fold": args.max_candidates,
        "overlap_threshold": args.overlap_threshold,
        "train_window_sessions": args.train_window_sessions,
        "recent_reference_start": RECENT_START_DAY.isoformat(),
        "fold_results": fold_rows,
        "aggregate_results": aggregate_rows,
        "selected_candidates": [asdict(row) for row in all_selected],
        "files": {
            "selected_candidates_csv": str(selected_csv),
            "oos_trades_csv": str(trades_csv),
            "fold_results_csv": str(fold_csv),
            "report_markdown": str(output_dir / "report.md"),
            "report_json": str(output_dir / "report.json"),
        },
        "research_grade": False,
        "research_grade_reason": "Historical NBBO, IV, and Greeks are unavailable; selection is walk-forward but execution remains a conservative trade-bar approximation.",
    }
    (output_dir / "report.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, default=str), encoding="utf-8"
    )
    (output_dir / "report.md").write_text(build_markdown(summary), encoding="utf-8")
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Nested walk-forward validation for all EOD option pattern variants.")
    parser.add_argument("--equity-db")
    parser.add_argument("--outcomes")
    parser.add_argument("--output-dir")
    parser.add_argument("--min-train-sessions", type=int, default=40)
    parser.add_argument("--max-candidates", type=int, default=5)
    parser.add_argument("--overlap-threshold", type=float, default=0.70)
    parser.add_argument(
        "--train-window-sessions",
        type=int,
        default=0,
        help="Use only the most recent N prior sessions; zero uses expanding history.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    summary = run(build_parser().parse_args(argv))
    print(json.dumps({
        "holdout_months": summary["holdout_months"],
        "candidate_variants": summary["candidate_variants"],
        "aggregate_results": summary["aggregate_results"],
        "files": summary["files"],
    }, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
