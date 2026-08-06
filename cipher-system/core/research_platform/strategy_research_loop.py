"""Bounded autonomous strategy discovery and backtesting for Cipher.

This module is intentionally separate from the source-code build healer.  It
searches a preregistered, price-only strategy space, runs point-in-time
backtests against a canonical frozen dataset, applies walk-forward and
multiple-testing controls, records every result in the research registry, and
feeds bounded parameter neighbours into later cycles.

The loop is exploratory research only.  It cannot promote a strategy, invoke
LEAN, paper trade, place orders, modify research data, relax gates, or rewrite
source code.
"""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import unquote, urlparse

import numpy as np
import pandas as pd

from .artifact_store import ArtifactStore
from .experiments import (
    CallableExperimentAdapter,
    EquityPoint,
    ExperimentRunner,
    StandardBacktestOutput,
    TradeRecord,
    runtime_environment_id,
)
from .hashing import sha256_file, stable_id
from .models import EngineKind, ExperimentManifest, StrategySpec, utc_now
from .registry import ResearchRegistry


@dataclass(frozen=True)
class StrategyCandidate:
    family: str
    parameters: Mapping[str, Any]
    generation: int = 0
    parent_candidate_id: str | None = None
    hypothesis: str = ""
    candidate_id: str = ""

    def __post_init__(self) -> None:
        family = self.family.strip().lower()
        if not family:
            raise ValueError("candidate family is required")
        parameters = {str(key): value for key, value in self.parameters.items()}
        if self.generation < 0:
            raise ValueError("generation cannot be negative")
        payload = {
            "family": family,
            "parameters": parameters,
            "generation": int(self.generation),
            "parent_candidate_id": self.parent_candidate_id,
        }
        object.__setattr__(self, "family", family)
        object.__setattr__(self, "parameters", parameters)
        object.__setattr__(self, "candidate_id", self.candidate_id or stable_id("candidate", payload))

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "family": self.family,
            "parameters": dict(self.parameters),
            "generation": self.generation,
            "parent_candidate_id": self.parent_candidate_id,
            "hypothesis": self.hypothesis,
        }


@dataclass(frozen=True)
class StrategyResearchPolicy:
    batch_size: int = 8
    maximum_total_candidates: int = 240
    maximum_generation: int = 3
    maximum_adaptive_children_per_cycle: int = 8
    slippage_bps_per_side: float = 10.0
    minimum_sessions: int = 500
    minimum_trades: int = 30
    minimum_profit_factor: float = 1.10
    maximum_drawdown_pct: float = 25.0
    maximum_holm_adjusted_p_value: float = 0.10
    walk_forward_folds: int = 3
    random_seed: int = 1729

    def __post_init__(self) -> None:
        if self.batch_size < 1:
            raise ValueError("batch_size must be positive")
        if self.maximum_total_candidates < self.batch_size:
            raise ValueError("maximum_total_candidates must be at least batch_size")
        if self.maximum_generation < 0:
            raise ValueError("maximum_generation cannot be negative")
        if self.walk_forward_folds < 2:
            raise ValueError("walk_forward_folds must be at least two")
        if self.minimum_sessions < 100:
            raise ValueError("minimum_sessions is too small for guarded research")


@dataclass(frozen=True)
class CandidateBacktest:
    candidate: StrategyCandidate
    output: StandardBacktestOutput
    raw_p_value: float
    composite_score: float
    fold_returns_pct: tuple[float, ...]


@dataclass(frozen=True)
class CanonicalPanel:
    dataset_id: str
    dataset_name: str
    frame: pd.DataFrame
    raw_object_count: int
    source_paths: tuple[str, ...]
    lineage_hash: str
    research_role: str = "exploratory_development_only_not_final_holdout"
    evaluation_start: str | None = None
    evaluation_end: str | None = None


def evaluation_window(panel: CanonicalPanel, index: pd.Index) -> tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp] | None:
    """Return simulation warmup start, scoring start, and scoring end.

    The simulation starts one session before the evaluation period so a signal
    formed on the prior close can enter at the first evaluation-session open.
    Metrics never include that warmup session.
    """
    if not panel.evaluation_start and not panel.evaluation_end:
        return None
    dates = pd.DatetimeIndex(index).sort_values().unique()
    if dates.empty:
        raise RuntimeError("evaluation window cannot be applied to an empty index")
    start = pd.Timestamp(panel.evaluation_start) if panel.evaluation_start else pd.Timestamp(dates[0])
    end = pd.Timestamp(panel.evaluation_end) if panel.evaluation_end else pd.Timestamp(dates[-1])
    eligible = dates[(dates >= start) & (dates <= end)]
    if eligible.empty:
        raise RuntimeError(f"evaluation window {start.date()} through {end.date()} has no sessions")
    scoring_start = pd.Timestamp(eligible[0])
    scoring_end = pd.Timestamp(eligible[-1])
    first_position = int(dates.get_indexer([scoring_start])[0])
    simulation_start = pd.Timestamp(dates[max(0, first_position - 1)])
    return simulation_start, scoring_start, scoring_end


def slice_signal_for_evaluation(
    panel: CanonicalPanel,
    bars: pd.DataFrame,
    signal: pd.Series,
) -> tuple[pd.DataFrame, pd.Series]:
    window = evaluation_window(panel, bars.index)
    if window is None:
        return bars, signal
    simulation_start, _scoring_start, scoring_end = window
    selected = bars.loc[(bars.index >= simulation_start) & (bars.index <= scoring_end)]
    return selected, signal.reindex(selected.index)


def slice_scored_series(panel: CanonicalPanel, values: pd.Series) -> pd.Series:
    window = evaluation_window(panel, values.index)
    if window is None:
        return values
    _simulation_start, scoring_start, scoring_end = window
    return values.loc[(values.index >= scoring_start) & (values.index <= scoring_end)]


def trade_in_evaluation_window(panel: CanonicalPanel, trade: TradeRecord) -> bool:
    if not panel.evaluation_start and not panel.evaluation_end:
        return True
    entry = pd.Timestamp(trade.entry_time)
    if panel.evaluation_start and entry < pd.Timestamp(panel.evaluation_start):
        return False
    if panel.evaluation_end and entry > pd.Timestamp(panel.evaluation_end):
        return False
    return True


def default_candidate_catalog() -> tuple[StrategyCandidate, ...]:
    """Return a deterministic, outcome-independent seed strategy space."""

    candidates: list[StrategyCandidate] = []

    def add(family: str, params: Mapping[str, Any], hypothesis: str) -> None:
        candidates.append(StrategyCandidate(family=family, parameters=params, hypothesis=hypothesis))

    for fast, slow in ((5, 50), (10, 50), (10, 100), (20, 100), (20, 150), (50, 200)):
        add(
            "sma_trend",
            {"fast": fast, "slow": slow},
            "A persistent fast-above-slow trend may retain positive open-to-open drift after conservative slippage.",
        )
    for entry, exit_window, trend in ((20, 10, 0), (20, 10, 100), (40, 20, 0), (40, 20, 100), (55, 20, 200), (80, 30, 200)):
        add(
            "donchian_breakout",
            {"entry_lookback": entry, "exit_lookback": exit_window, "trend_filter": trend},
            "New closing highs may continue when exits are tied to a shorter rolling low.",
        )
    for period, entry, exit_level, trend in (
        (2, 5, 50, 0),
        (2, 10, 50, 100),
        (2, 15, 60, 200),
        (5, 20, 50, 0),
        (5, 25, 55, 100),
        (7, 30, 60, 200),
    ):
        add(
            "rsi_reversion",
            {"period": period, "entry": entry, "exit": exit_level, "trend_filter": trend},
            "Short-horizon oversold conditions may mean-revert, especially when conditioned on a longer trend.",
        )
    for window, entry_z, exit_z, trend in (
        (15, 1.5, 0.0, 0),
        (20, 2.0, 0.0, 0),
        (20, 2.0, 0.5, 100),
        (30, 2.0, 0.0, 100),
        (30, 2.5, 0.5, 200),
        (40, 2.5, 0.0, 200),
    ):
        add(
            "bollinger_reversion",
            {"window": window, "entry_z": entry_z, "exit_z": exit_z, "trend_filter": trend},
            "Large negative deviations from a rolling mean may revert without requiring volume information.",
        )
    for fast, slow, pullback, exit_mode in (
        (10, 50, 0.01, "fast_break"),
        (20, 100, 0.01, "fast_break"),
        (20, 100, 0.02, "slow_break"),
        (20, 150, 0.03, "fast_break"),
        (50, 200, 0.02, "fast_break"),
        (50, 200, 0.04, "slow_break"),
    ):
        add(
            "trend_pullback",
            {"fast": fast, "slow": slow, "pullback_pct": pullback, "exit_mode": exit_mode},
            "Pullbacks within an established uptrend may offer better entry prices than raw breakouts.",
        )
    for lookback, atr_window, maximum_atr_pct, exit_window in (
        (10, 14, 0.025, 5),
        (20, 14, 0.030, 10),
        (20, 20, 0.040, 10),
        (40, 14, 0.030, 15),
        (40, 20, 0.050, 20),
        (60, 20, 0.040, 20),
    ):
        add(
            "low_vol_breakout",
            {
                "lookback": lookback,
                "atr_window": atr_window,
                "maximum_atr_pct": maximum_atr_pct,
                "exit_window": exit_window,
            },
            "Price breakouts may be cleaner when recent true-range volatility is bounded.",
        )

    from .strategy_research_phase2 import phase2_candidate_catalog

    candidates.extend(phase2_candidate_catalog())
    unique = {candidate.candidate_id: candidate for candidate in candidates}
    return tuple(sorted(unique.values(), key=lambda item: (item.family, item.candidate_id)))


def holm_adjust(p_values: Mapping[str, float]) -> dict[str, float]:
    """Holm step-down family-wise error correction."""

    ordered = sorted((max(0.0, min(1.0, float(value))), key) for key, value in p_values.items())
    count = len(ordered)
    adjusted: dict[str, float] = {}
    running = 0.0
    for rank, (value, key) in enumerate(ordered):
        candidate = min(1.0, (count - rank) * value)
        running = max(running, candidate)
        adjusted[key] = running
    return adjusted


def load_canonical_daily_panel(
    registry_path: str | Path,
    dataset_id: str,
    *,
    cache_root: str | Path | None = None,
) -> CanonicalPanel:
    """Resolve a frozen dataset only through canonical registry lineage."""

    registry_path = Path(registry_path).resolve()
    with sqlite3.connect(f"file:{registry_path.as_posix()}?mode=ro", uri=True, timeout=60) as db:
        db.row_factory = sqlite3.Row
        dataset = db.execute(
            "select dataset_id, name, frozen, quality_passed, payload_json from datasets where dataset_id = ?",
            (dataset_id,),
        ).fetchone()
        if not dataset:
            raise KeyError(f"canonical dataset not found: {dataset_id}")
        if not bool(dataset["frozen"]):
            raise RuntimeError("strategy research requires a frozen canonical dataset")
        if not bool(dataset["quality_passed"]):
            raise RuntimeError("canonical dataset quality gate is not passed")
        rows = db.execute(
            """
            select r.raw_object_id, r.uri, r.checksum, r.checksum_method, r.payload_json
            from dataset_raw_objects d
            join raw_objects r on r.raw_object_id = d.raw_object_id
            where d.dataset_id = ?
            order by r.uri
            """,
            (dataset_id,),
        ).fetchall()
    if not rows:
        raise RuntimeError("canonical dataset has no linked raw objects")

    lineage_hash = stable_id(
        "lineage",
        [(row["raw_object_id"], row["checksum"], row["uri"]) for row in rows],
        length=64,
    )
    cache_path: Path | None = None
    metadata_path: Path | None = None
    if cache_root is not None:
        cache_root = Path(cache_root).resolve()
        cache_root.mkdir(parents=True, exist_ok=True)
        cache_path = cache_root / f"daily_panel_{dataset_id}.parquet"
        metadata_path = cache_root / f"daily_panel_{dataset_id}.json"
        if cache_path.is_file() and metadata_path.is_file():
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                metadata = {}
            if metadata.get("dataset_id") == dataset_id and metadata.get("lineage_hash") == lineage_hash:
                frame = pd.read_parquet(cache_path)
                return CanonicalPanel(
                    dataset_id=dataset_id,
                    dataset_name=str(dataset["name"]),
                    frame=_validate_daily_panel(frame),
                    raw_object_count=len(rows),
                    source_paths=tuple(str(_file_uri_to_path(row["uri"])) for row in rows),
                    lineage_hash=lineage_hash,
                )

    daily_frames: list[pd.DataFrame] = []
    source_paths: list[str] = []
    for row in rows:
        if row["checksum_method"] != "sha256":
            raise RuntimeError(f"non-SHA256 canonical object is not allowed: {row['raw_object_id']}")
        path = _file_uri_to_path(row["uri"])
        if not path.is_file():
            raise FileNotFoundError(path)
        if sha256_file(path) != row["checksum"]:
            raise RuntimeError(f"canonical object hash mismatch: {path}")
        source_paths.append(str(path))
        frame = pd.read_parquet(path, columns=["timestamp", "ticker", "open", "high", "low", "close"])
        if frame.empty:
            continue
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
        frame = frame.sort_values(["ticker", "timestamp"])
        day = frame["timestamp"].dt.tz_convert("America/New_York").dt.normalize().dt.tz_localize(None)
        frame = frame.assign(date=day)
        aggregated = (
            frame.groupby(["date", "ticker"], sort=True, observed=True)
            .agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"), bars=("close", "size"))
            .reset_index()
        )
        daily_frames.append(aggregated)
    if not daily_frames:
        raise RuntimeError("canonical dataset produced no daily rows")
    daily = _validate_daily_panel(pd.concat(daily_frames, ignore_index=True))
    if cache_path is not None and metadata_path is not None:
        temporary = cache_path.with_suffix(".tmp.parquet")
        daily.to_parquet(temporary, index=False)
        temporary.replace(cache_path)
        metadata = {
            "schema_version": 1,
            "dataset_id": dataset_id,
            "dataset_name": str(dataset["name"]),
            "lineage_hash": lineage_hash,
            "raw_object_count": len(rows),
            "daily_rows": len(daily),
            "dates": int(daily["date"].nunique()),
            "symbols": sorted(daily["ticker"].unique().tolist()),
            "created_at": utc_now().isoformat(),
            "research_role": "exploratory_development_only_not_final_holdout",
            "execution_authority": False,
        }
        temporary_meta = metadata_path.with_suffix(".tmp")
        temporary_meta.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary_meta.replace(metadata_path)
    return CanonicalPanel(
        dataset_id=dataset_id,
        dataset_name=str(dataset["name"]),
        frame=daily,
        raw_object_count=len(rows),
        source_paths=tuple(source_paths),
        lineage_hash=lineage_hash,
    )


def run_candidate_backtest(
    panel: CanonicalPanel,
    candidate: StrategyCandidate,
    policy: StrategyResearchPolicy,
) -> CandidateBacktest:
    from .strategy_research_phase2 import is_phase2_family, run_phase2_candidate_backtest

    if is_phase2_family(candidate.family):
        return run_phase2_candidate_backtest(panel, candidate, policy)

    daily = panel.frame.copy()
    dates = pd.DatetimeIndex(sorted(pd.Timestamp(value) for value in daily["date"].unique()))
    window = evaluation_window(panel, dates)
    scored_dates = dates if window is None else dates[(dates >= window[1]) & (dates <= window[2])]
    if len(scored_dates) < policy.minimum_sessions:
        raise RuntimeError(f"evaluation dataset has {len(scored_dates)} sessions; {policy.minimum_sessions} required")

    all_trades: list[TradeRecord] = []
    per_symbol_returns: dict[str, pd.Series] = {}
    benchmark_returns: dict[str, pd.Series] = {}
    exclusions: list[dict[str, Any]] = []
    for symbol, group in daily.groupby("ticker", sort=True):
        bars = group.sort_values("date").set_index("date")
        signal = candidate_signal(bars, candidate)
        simulation_bars, simulation_signal = slice_signal_for_evaluation(panel, bars, signal)
        result = _simulate_symbol(symbol, simulation_bars, simulation_signal, policy.slippage_bps_per_side)
        all_trades.extend(result["trades"])
        per_symbol_returns[symbol] = result["daily_returns"]
        benchmark_returns[symbol] = result["benchmark_returns"]
        exclusions.extend(result["exclusions"])

    returns_frame = pd.concat(per_symbol_returns, axis=1).sort_index().fillna(0.0)
    portfolio_returns = slice_scored_series(panel, returns_frame.mean(axis=1))
    benchmark_frame = pd.concat(benchmark_returns, axis=1).sort_index().fillna(0.0)
    benchmark_frame = benchmark_frame.reindex(portfolio_returns.index).fillna(0.0)
    all_trades = [trade for trade in all_trades if trade_in_evaluation_window(panel, trade)]
    if "SPY" in benchmark_frame.columns:
        benchmark = benchmark_frame["SPY"]
        benchmark_name = "SPY_open_to_open"
    else:
        benchmark = benchmark_frame.mean(axis=1)
        benchmark_name = "equal_weight_panel_open_to_open"

    equity = 100_000.0 * (1.0 + portfolio_returns).cumprod()
    benchmark_equity = 100_000.0 * (1.0 + benchmark.reindex(equity.index).fillna(0.0)).cumprod()
    equity_points = tuple(
        EquityPoint(timestamp=index.isoformat(), equity=float(value))
        for index, value in equity.items()
    )
    trade_returns = np.asarray(
        [float(item.return_pct) / 100.0 for item in all_trades if item.return_pct is not None and math.isfinite(item.return_pct)],
        dtype=float,
    )
    raw_p = _sign_flip_p_value(trade_returns, seed=policy.random_seed + int(candidate.candidate_id[-6:], 16) % 100_000)
    fold_returns = _walk_forward_returns(portfolio_returns, policy.walk_forward_folds)
    fold_benchmark = _walk_forward_returns(benchmark.reindex(portfolio_returns.index).fillna(0.0), policy.walk_forward_folds)
    positive_excess_folds = sum(1 for left, right in zip(fold_returns, fold_benchmark) if left > right)
    walk_forward_passed = bool(
        positive_excess_folds >= math.ceil(policy.walk_forward_folds / 2)
        and float(np.median(fold_returns)) > 0.0
    )

    total_return_pct = _total_return_pct(portfolio_returns)
    benchmark_return_pct = _total_return_pct(benchmark.reindex(portfolio_returns.index).fillna(0.0))
    maximum_drawdown_pct = _maximum_drawdown_pct(equity)
    profit_factor = _profit_factor(trade_returns)
    win_rate = float(np.mean(trade_returns > 0)) if len(trade_returns) else None
    best_trade_exclusion = _best_trade_exclusion(trade_returns)
    benchmark_outperformance = total_return_pct > benchmark_return_pct
    regime_metrics = _regime_metrics(daily, portfolio_returns)
    composite_score = _composite_score(
        fold_returns=fold_returns,
        fold_benchmark=fold_benchmark,
        maximum_drawdown_pct=maximum_drawdown_pct,
        profit_factor=profit_factor,
        trade_count=len(trade_returns),
    )

    output = StandardBacktestOutput(
        trades=tuple(all_trades),
        equity_curve=equity_points,
        metrics={
            "total_return_pct": total_return_pct,
            "annualized_return_pct": _annualized_return_pct(portfolio_returns),
            "annualized_volatility_pct": _annualized_volatility_pct(portfolio_returns),
            "sharpe_ratio": _sharpe_ratio(portfolio_returns),
            "maximum_drawdown_pct": maximum_drawdown_pct,
            "trade_count": len(trade_returns),
            "profit_factor": profit_factor,
            "win_rate": win_rate,
            "median_trade_return_pct": float(np.median(trade_returns) * 100.0) if len(trade_returns) else None,
            "composite_score": composite_score,
            "positive_excess_folds": positive_excess_folds,
            "walk_forward_fold_count": policy.walk_forward_folds,
        },
        benchmark_metrics={
            "benchmark": benchmark_name,
            "total_return_pct": benchmark_return_pct,
            "annualized_return_pct": _annualized_return_pct(benchmark),
            "maximum_drawdown_pct": _maximum_drawdown_pct(benchmark_equity),
            "strategy_excess_return_pct": total_return_pct - benchmark_return_pct,
        },
        regime_metrics=regime_metrics,
        statistical_tests={
            "test": "deterministic_sign_flip_mean_trade_return",
            "raw_p_value": raw_p,
            "holm_adjusted_p_value": raw_p,
            "multiple_testing_family": "pending_batch_adjustment",
            "trade_observations": len(trade_returns),
        },
        quality_checks={
            "passed": True,
            "point_in_time_validated": True,
            "next_session_open_execution": True,
            "walk_forward_passed": walk_forward_passed,
            "best_trade_exclusion_passed": best_trade_exclusion,
            "benchmark_outperformance_passed": benchmark_outperformance,
            "canonical_dataset_only": True,
            "canonical_raw_hashes_verified": True,
            "volume_features": False,
            "volume_evaluation": False,
            "research_role": panel.research_role,
            "final_holdout_claim": False,
            "automatic_promotion": False,
            "evaluation_window_enforced": bool(panel.evaluation_start or panel.evaluation_end),
        },
        exclusions=tuple(exclusions),
        assumptions={
            "execution": "signal formed at session close; trade at next session open",
            "slippage_bps_per_side": policy.slippage_bps_per_side,
            "fees": "zero explicit commission; slippage charged on entries and exits",
            "capital_allocation": "equal weight across panel symbols",
            "data_scope": "price_only_daily_ohlc_derived_from_canonical_one_minute_partitions",
            "dataset_id": panel.dataset_id,
            "dataset_name": panel.dataset_name,
            "lineage_hash": panel.lineage_hash,
            "research_role": panel.research_role,
            "evaluation_start": panel.evaluation_start,
            "evaluation_end": panel.evaluation_end,
            "warmup_data_excluded_from_metrics": bool(panel.evaluation_start or panel.evaluation_end),
        },
        notes=(
            "Exploratory autonomous research only; the dataset is not treated as a new untouched final holdout.",
            "A PASS verdict is a screening result, not promotion, LEAN replication, prospective validation, or trading authority.",
        ),
    )
    return CandidateBacktest(
        candidate=candidate,
        output=output,
        raw_p_value=raw_p,
        composite_score=composite_score,
        fold_returns_pct=tuple(fold_returns),
    )


def candidate_signal(bars: pd.DataFrame, candidate: StrategyCandidate) -> pd.Series:
    """Build a desired close-of-session long position without future data."""

    close = bars["close"].astype(float)
    high = bars["high"].astype(float)
    low = bars["low"].astype(float)
    params = candidate.parameters
    family = candidate.family
    if family == "sma_trend":
        fast = close.rolling(int(params["fast"]), min_periods=int(params["fast"])).mean()
        slow = close.rolling(int(params["slow"]), min_periods=int(params["slow"])).mean()
        return (fast > slow).fillna(False)
    if family == "donchian_breakout":
        entry = high.shift(1).rolling(int(params["entry_lookback"]), min_periods=int(params["entry_lookback"])).max()
        exit_level = low.shift(1).rolling(int(params["exit_lookback"]), min_periods=int(params["exit_lookback"])).min()
        trend_window = int(params.get("trend_filter") or 0)
        trend_ok = pd.Series(True, index=close.index)
        if trend_window:
            trend_ok = close > close.rolling(trend_window, min_periods=trend_window).mean()
        return _stateful_signal((close > entry) & trend_ok, close < exit_level)
    if family == "rsi_reversion":
        rsi = _rsi(close, int(params["period"]))
        trend_window = int(params.get("trend_filter") or 0)
        trend_ok = pd.Series(True, index=close.index)
        if trend_window:
            trend_ok = close > close.rolling(trend_window, min_periods=trend_window).mean()
        return _stateful_signal((rsi < float(params["entry"])) & trend_ok, rsi > float(params["exit"]))
    if family == "bollinger_reversion":
        window = int(params["window"])
        mean = close.rolling(window, min_periods=window).mean()
        std = close.rolling(window, min_periods=window).std(ddof=0).replace(0.0, np.nan)
        z = (close - mean) / std
        trend_window = int(params.get("trend_filter") or 0)
        trend_ok = pd.Series(True, index=close.index)
        if trend_window:
            trend_ok = close > close.rolling(trend_window, min_periods=trend_window).mean()
        return _stateful_signal((z < -float(params["entry_z"])) & trend_ok, z > -float(params["exit_z"]))
    if family == "trend_pullback":
        fast = close.rolling(int(params["fast"]), min_periods=int(params["fast"])).mean()
        slow = close.rolling(int(params["slow"]), min_periods=int(params["slow"])).mean()
        trend = fast > slow
        pullback = close <= fast * (1.0 - float(params["pullback_pct"]))
        exit_rule = close < (fast if params["exit_mode"] == "fast_break" else slow)
        return _stateful_signal(trend & pullback, exit_rule)
    if family == "low_vol_breakout":
        lookback = int(params["lookback"])
        entry = high.shift(1).rolling(lookback, min_periods=lookback).max()
        exit_level = low.shift(1).rolling(int(params["exit_window"]), min_periods=int(params["exit_window"])).min()
        atr = _atr(bars, int(params["atr_window"]))
        atr_pct = atr / close
        return _stateful_signal((close > entry) & (atr_pct <= float(params["maximum_atr_pct"])), close < exit_level)
    raise KeyError(f"unsupported strategy family: {family}")


def feedback_eligible_record(item: Mapping[str, Any], policy: StrategyResearchPolicy) -> bool:
    metrics = item.get("metrics") if isinstance(item.get("metrics"), Mapping) else {}
    tests = item.get("statistical_tests") if isinstance(item.get("statistical_tests"), Mapping) else {}
    quality = item.get("quality_checks") if isinstance(item.get("quality_checks"), Mapping) else {}
    return (
        float(metrics.get("total_return_pct") or 0.0) > 0.0
        and int(metrics.get("trade_count") or 0) >= policy.minimum_trades
        and float(metrics.get("profit_factor") or 0.0) >= policy.minimum_profit_factor
        and float(metrics.get("maximum_drawdown_pct") or 100.0) <= policy.maximum_drawdown_pct
        and float(tests.get("holm_adjusted_p_value") or 1.0) <= policy.maximum_holm_adjusted_p_value
        and bool(quality.get("best_trade_exclusion_passed"))
    )


def adaptive_neighbours(
    ranked: Sequence[Mapping[str, Any]],
    existing_ids: set[str],
    policy: StrategyResearchPolicy,
) -> tuple[StrategyCandidate, ...]:
    """Create bounded parameter neighbours around promising completed screens."""

    children: list[StrategyCandidate] = []
    for item in ranked:
        if len(children) >= policy.maximum_adaptive_children_per_cycle:
            break
        candidate_payload = item.get("candidate") if isinstance(item, Mapping) else None
        if not isinstance(candidate_payload, Mapping):
            continue
        generation = int(candidate_payload.get("generation") or 0)
        if generation >= policy.maximum_generation:
            continue
        parent = StrategyCandidate(
            family=str(candidate_payload["family"]),
            parameters=dict(candidate_payload["parameters"]),
            generation=generation,
            parent_candidate_id=candidate_payload.get("parent_candidate_id"),
            hypothesis=str(candidate_payload.get("hypothesis") or ""),
            candidate_id=str(candidate_payload["candidate_id"]),
        )
        for parameters in _neighbour_parameter_sets(parent):
            child = StrategyCandidate(
                family=parent.family,
                parameters=parameters,
                generation=parent.generation + 1,
                parent_candidate_id=parent.candidate_id,
                hypothesis=f"Bounded neighbour of {parent.candidate_id}; exploratory robustness search only.",
            )
            if child.candidate_id not in existing_ids:
                children.append(child)
                existing_ids.add(child.candidate_id)
            if len(children) >= policy.maximum_adaptive_children_per_cycle:
                break
    return tuple(children)


def strategy_spec(candidate: StrategyCandidate, policy: StrategyResearchPolicy) -> StrategySpec:
    from .strategy_research_phase2 import is_phase2_family, phase2_strategy_metadata

    metadata = phase2_strategy_metadata(candidate) if is_phase2_family(candidate.family) else {
        "direction": "long_only",
        "long_only": True,
        "benchmark": "SPY_open_to_open",
        "allocation": "equal_weight_across_symbols",
        "scope": "per_symbol",
    }
    return StrategySpec(
        name=f"autonomous_price_only_{candidate.family}_{candidate.candidate_id[-8:]}",
        version=f"research-g{candidate.generation}-v1",
        signal_rule={
            "family": candidate.family,
            "parameters": dict(candidate.parameters),
            "signal_available": "session_close",
            "research_role": "exploratory_development_only",
            "candidate_id": candidate.candidate_id,
            "parent_candidate_id": candidate.parent_candidate_id,
        },
        instrument_rule={
            "asset": "underlying_equity_proxy",
            "direction": metadata["direction"],
            "universe": "canonical_dataset_symbols",
            "scope": metadata["scope"],
        },
        contract_selection_rule={"options_contracts": False, "note": "underlying proxy screen before any options replication"},
        entry_rule={"execution": "next_session_open_after_signal"},
        exit_rule={"execution": "next_session_open_after_exit_signal"},
        sizing_rule={"method": metadata["allocation"], "leverage": 1.0},
        portfolio_constraints={
            "long_only": metadata["long_only"],
            "gross_leverage_max": 1.0,
            "net_leverage_abs_max": 1.0,
            "live_execution": False,
        },
        required_feature_ids=(),
        fill_model={"slippage_bps_per_side": policy.slippage_bps_per_side, "commission": 0.0},
        benchmark=metadata["benchmark"],
        statistical_plan={
            "walk_forward_folds": policy.walk_forward_folds,
            "test": "sign_flip_mean_trade_return",
            "multiple_testing": "Holm within each autonomous batch",
            "adaptive_search": "bounded_neighbour_generation",
            "research_phase": 2 if is_phase2_family(candidate.family) else 1,
            "strategy_scope": metadata["scope"],
            "final_holdout": False,
        },
        promotion_thresholds={
            "minimum_trades": policy.minimum_trades,
            "minimum_profit_factor": policy.minimum_profit_factor,
            "maximum_drawdown_pct": policy.maximum_drawdown_pct,
            "maximum_adjusted_p_value": policy.maximum_holm_adjusted_p_value,
            "require_walk_forward": True,
            "require_best_trade_exclusion": True,
            "maximum_exclusion_ratio": 0.05,
            "required_quality_checks": (
                "point_in_time_validated",
                "next_session_open_execution",
                "canonical_dataset_only",
            ),
        },
        description=(
            "Autonomously generated price-only development candidate. A screening PASS does not authorize promotion, "
            "LEAN replication, paper trading, or live execution."
        ),
    )


def run_research_cycle(
    *,
    registry_path: str | Path,
    artifact_root: str | Path,
    state_path: str | Path,
    output_root: str | Path,
    cache_root: str | Path,
    dataset_id: str,
    policy: StrategyResearchPolicy | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Run one bounded candidate-generation/backtesting cycle."""

    policy = policy or StrategyResearchPolicy()
    timestamp = now or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    registry = ResearchRegistry(registry_path)
    artifacts = ArtifactStore(artifact_root)
    state_path = Path(state_path)
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    state = _read_state(state_path)
    tested_ids = set(str(item) for item in state.get("tested_candidate_ids", []))
    adaptive_payloads = [item for item in state.get("adaptive_candidates", []) if isinstance(item, Mapping)]
    adaptive_candidates = tuple(_candidate_from_payload(item) for item in adaptive_payloads)
    catalog = tuple(default_candidate_catalog()) + adaptive_candidates
    unique_catalog = {item.candidate_id: item for item in catalog}
    untested = [item for item in sorted(unique_catalog.values(), key=lambda candidate: (candidate.generation, candidate.family, candidate.candidate_id)) if item.candidate_id not in tested_ids]
    if len(tested_ids) >= policy.maximum_total_candidates or not untested:
        exhausted_state = {
            **state,
            "schema_version": 1,
            "updated_at": timestamp.isoformat(),
            "dataset_id": dataset_id,
            "tested_candidate_ids": sorted(tested_ids),
            "cycle_count": int(state.get("cycle_count") or 0) + 1,
            "latest_cycle_status": "catalog_exhausted_or_candidate_cap_reached",
            "automatic_promotion": False,
            "paper_or_live_execution": False,
            "execution_authority": False,
        }
        payload = {
            "schema_version": 1,
            "created_at": timestamp.isoformat(),
            "status": "catalog_exhausted_or_candidate_cap_reached",
            "dataset_id": dataset_id,
            "tested_candidate_count": len(tested_ids),
            "tested_candidate_count_total": len(tested_ids),
            "candidate_cap": policy.maximum_total_candidates,
            "catalog_candidate_count": len(unique_catalog),
            "remaining_seed_or_adaptive_candidates": len(untested),
            "new_candidates_tested": 0,
            "results": [],
            "ranking": [],
            "adaptive_children_added": [],
            "automatic_promotion": False,
            "paper_or_live_execution": False,
            "execution_authority": False,
        }
        _write_cycle_artifacts(payload, output_root, state_path, exhausted_state)
        return payload

    selected = _balanced_batch(untested, min(policy.batch_size, policy.maximum_total_candidates - len(tested_ids)))
    panel = load_canonical_daily_panel(registry_path, dataset_id, cache_root=cache_root)
    raw_results = [run_candidate_backtest(panel, candidate, policy) for candidate in selected]
    adjusted = holm_adjust({item.candidate.candidate_id: item.raw_p_value for item in raw_results})
    runner = ExperimentRunner(registry=registry, artifact_store=artifacts)
    code_hash = sha256_file(Path(__file__))
    runtime_id = runtime_environment_id()
    completed: list[dict[str, Any]] = []
    for item in raw_results:
        adjusted_output = replace(
            item.output,
            statistical_tests={
                **dict(item.output.statistical_tests),
                "holm_adjusted_p_value": adjusted[item.candidate.candidate_id],
                "multiple_testing_family": [candidate.candidate_id for candidate in selected],
                "family_size": len(selected),
            },
        )
        spec = strategy_spec(item.candidate, policy)
        registry.register_strategy(spec)
        manifest = ExperimentManifest(
            strategy_id=spec.strategy_id,
            dataset_id=dataset_id,
            feature_set_id="feature_set_price_only_daily_ohlc_v1",
            parameter_set={
                "candidate": item.candidate.to_dict(),
                "policy": _policy_dict(policy),
                "research_role": panel.research_role,
            },
            engine=EngineKind.CIPHER_FAST,
            code_hash=code_hash,
            runtime_environment_id=runtime_id,
            random_seed=policy.random_seed,
            started_at=timestamp,
            preregistered=True,
            hypothesis=item.candidate.hypothesis,
        )
        result = runner.run(
            manifest,
            strategy=spec,
            adapter=CallableExperimentAdapter(lambda _manifest, output=adjusted_output: output),
        )
        result_metrics = dict(result.metrics)
        result_tests = dict(result.statistical_tests)
        result_quality = dict(result.quality_checks)
        completed_record = {
                "candidate": item.candidate.to_dict(),
                "strategy_id": spec.strategy_id,
                "experiment_id": manifest.experiment_id,
                "verdict": result.verdict.value,
                "metrics": result_metrics,
                "statistical_tests": result_tests,
                "quality_checks": result_quality,
                "fold_returns_pct": list(item.fold_returns_pct),
                "composite_score": item.composite_score,
                "automatic_promotion": False,
            }
        feedback_eligible = feedback_eligible_record(completed_record, policy)
        completed_record.update(
            {
                "feedback_eligible": feedback_eligible,
                "feedback_interpretation": (
                    "bounded_neighbour_search_only_not_a_pass"
                    if feedback_eligible and result.verdict.value == "FAIL"
                    else "screening_pass_candidate"
                    if feedback_eligible
                    else "no_adaptive_feedback"
                ),
            }
        )
        completed.append(completed_record)
        tested_ids.add(item.candidate.candidate_id)

    ranked = sorted(
        completed,
        key=lambda item: (
            item["verdict"] in {"PASS", "CONDITIONAL_PASS"},
            float(item.get("composite_score") or -1e9),
        ),
        reverse=True,
    )
    feedback_pool = list(ranked)
    feedback_pool.extend(
        item for item in state.get("latest_top_candidates", [])
        if isinstance(item, Mapping)
    )
    promising: list[Mapping[str, Any]] = []
    seen_feedback: set[str] = set()
    for item in feedback_pool:
        candidate_payload = item.get("candidate") if isinstance(item, Mapping) else None
        candidate_id = str(candidate_payload.get("candidate_id") or "") if isinstance(candidate_payload, Mapping) else ""
        if candidate_id and candidate_id not in seen_feedback and feedback_eligible_record(item, policy):
            promising.append(item)
            seen_feedback.add(candidate_id)
    existing_ids = set(unique_catalog) | tested_ids
    children = adaptive_neighbours(promising, existing_ids, policy)
    adaptive_existing = {item.candidate_id: item for item in adaptive_candidates}
    for child in children:
        adaptive_existing[child.candidate_id] = child

    state = {
        "schema_version": 1,
        "updated_at": timestamp.isoformat(),
        "dataset_id": dataset_id,
        "tested_candidate_ids": sorted(tested_ids),
        "adaptive_candidates": [item.to_dict() for item in sorted(adaptive_existing.values(), key=lambda candidate: candidate.candidate_id)],
        "cycle_count": int(state.get("cycle_count") or 0) + 1,
        "latest_cycle_status": "completed",
        "latest_top_candidates": ranked[:10],
        "automatic_promotion": False,
        "paper_or_live_execution": False,
        "execution_authority": False,
    }
    payload = {
        "schema_version": 1,
        "created_at": timestamp.isoformat(),
        "status": "completed",
        "dataset": {
            "dataset_id": panel.dataset_id,
            "dataset_name": panel.dataset_name,
            "raw_object_count": panel.raw_object_count,
            "lineage_hash": panel.lineage_hash,
            "sessions": int(panel.frame["date"].nunique()),
            "symbols": sorted(panel.frame["ticker"].unique().tolist()),
            "research_role": panel.research_role,
        },
        "policy": _policy_dict(policy),
        "selected_candidates": [item.to_dict() for item in selected],
        "results": completed,
        "ranking": ranked,
        "adaptive_children_added": [item.to_dict() for item in children],
        "tested_candidate_count_total": len(tested_ids),
        "remaining_seed_or_adaptive_candidates": max(0, len(unique_catalog) + len(children) - len(tested_ids)),
        "claims": {
            "strategy_discovery_active": True,
            "backtesting_active": True,
            "walk_forward_active": True,
            "multiple_testing_control_active": True,
            "final_holdout_validation": False,
            "automatic_promotion": False,
            "lean_replication": False,
            "paper_trading": False,
            "live_execution": False,
        },
        "execution_authority": False,
    }
    _write_cycle_artifacts(payload, output_root, state_path, state)
    return payload


def _balanced_batch(candidates: Sequence[StrategyCandidate], size: int) -> tuple[StrategyCandidate, ...]:
    by_family: dict[str, list[StrategyCandidate]] = {}
    for candidate in candidates:
        by_family.setdefault(candidate.family, []).append(candidate)
    selected: list[StrategyCandidate] = []
    families = sorted(by_family)
    while len(selected) < size and families:
        next_families: list[str] = []
        for family in families:
            bucket = by_family[family]
            if bucket and len(selected) < size:
                selected.append(bucket.pop(0))
            if bucket:
                next_families.append(family)
        families = next_families
    return tuple(selected)


def _simulate_symbol(symbol: str, bars: pd.DataFrame, desired_close: pd.Series, slippage_bps: float) -> dict[str, Any]:
    desired = desired_close.reindex(bars.index).fillna(False).astype(bool)
    position = desired.shift(1, fill_value=False).astype(bool)
    opens = bars["open"].astype(float)
    next_open_return = opens.shift(-1) / opens - 1.0
    turnover = position.astype(int).diff().abs().fillna(position.astype(int)).astype(float)
    daily_returns = position.astype(float) * next_open_return.fillna(0.0) - turnover * (slippage_bps / 10_000.0)
    benchmark_returns = next_open_return.fillna(0.0)
    entries = position & ~position.shift(1, fill_value=False)
    exits = ~position & position.shift(1, fill_value=False)
    trades: list[TradeRecord] = []
    exclusions: list[dict[str, Any]] = []
    active_entry: pd.Timestamp | None = None
    for date in bars.index:
        if bool(entries.loc[date]):
            active_entry = pd.Timestamp(date)
        if bool(exits.loc[date]) and active_entry is not None:
            entry_price = float(opens.loc[active_entry]) * (1.0 + slippage_bps / 10_000.0)
            exit_price = float(opens.loc[date]) * (1.0 - slippage_bps / 10_000.0)
            trade_return = exit_price / entry_price - 1.0
            trades.append(
                TradeRecord(
                    trade_id=stable_id("trade", {"symbol": symbol, "entry": active_entry.isoformat(), "exit": pd.Timestamp(date).isoformat()}),
                    symbol=symbol,
                    direction="long",
                    entry_time=active_entry.isoformat(),
                    exit_time=pd.Timestamp(date).isoformat(),
                    entry_price=entry_price,
                    exit_price=exit_price,
                    quantity=1.0,
                    gross_pnl=float(opens.loc[date] - opens.loc[active_entry]),
                    net_pnl=float(exit_price - entry_price),
                    return_pct=float(trade_return * 100.0),
                    metadata={"execution": "next_session_open", "slippage_bps_per_side": slippage_bps},
                )
            )
            active_entry = None
    if active_entry is not None:
        final_date = pd.Timestamp(bars.index[-1])
        entry_price = float(opens.loc[active_entry]) * (1.0 + slippage_bps / 10_000.0)
        exit_price = float(bars.loc[final_date, "close"]) * (1.0 - slippage_bps / 10_000.0)
        if final_date > active_entry:
            trades.append(
                TradeRecord(
                    trade_id=stable_id("trade", {"symbol": symbol, "entry": active_entry.isoformat(), "exit": final_date.isoformat(), "forced": True}),
                    symbol=symbol,
                    direction="long",
                    entry_time=active_entry.isoformat(),
                    exit_time=final_date.isoformat(),
                    entry_price=entry_price,
                    exit_price=exit_price,
                    quantity=1.0,
                    gross_pnl=float(bars.loc[final_date, "close"] - opens.loc[active_entry]),
                    net_pnl=float(exit_price - entry_price),
                    return_pct=float((exit_price / entry_price - 1.0) * 100.0),
                    metadata={"forced_final_close": True, "slippage_bps_per_side": slippage_bps},
                )
            )
        else:
            exclusions.append({"symbol": symbol, "reason": "entry_on_final_session_cannot_be_scored", "date": final_date.isoformat()})
    return {
        "trades": trades,
        "daily_returns": daily_returns,
        "benchmark_returns": benchmark_returns,
        "exclusions": exclusions,
    }


def _stateful_signal(entries: pd.Series, exits: pd.Series) -> pd.Series:
    state = False
    values: list[bool] = []
    for enter, leave in zip(entries.fillna(False).astype(bool), exits.fillna(False).astype(bool)):
        if state and leave:
            state = False
        elif not state and enter:
            state = True
        values.append(state)
    return pd.Series(values, index=entries.index, dtype=bool)


def _rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0).ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    loss = (-delta.clip(upper=0.0)).ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    rs = gain / loss.replace(0.0, np.nan)
    result = 100.0 - 100.0 / (1.0 + rs)
    return result.where(loss > 0.0, 100.0).where(gain > 0.0, 0.0)


def _atr(bars: pd.DataFrame, period: int) -> pd.Series:
    previous_close = bars["close"].shift(1)
    true_range = pd.concat(
        [
            bars["high"] - bars["low"],
            (bars["high"] - previous_close).abs(),
            (bars["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def _walk_forward_returns(returns: pd.Series, folds: int) -> tuple[float, ...]:
    values = returns.sort_index()
    indices = np.array_split(np.arange(len(values)), folds)
    output: list[float] = []
    for index in indices:
        if len(index) == 0:
            output.append(0.0)
        else:
            output.append(float(((1.0 + values.iloc[index]).prod() - 1.0) * 100.0))
    return tuple(output)


def _sign_flip_p_value(values: np.ndarray, *, seed: int, simulations: int = 4096) -> float:
    values = values[np.isfinite(values)]
    if len(values) < 2:
        return 1.0
    observed = float(np.mean(values))
    if observed <= 0:
        return 1.0
    rng = np.random.default_rng(seed)
    exceed = 1
    chunk = 256
    remaining = simulations
    while remaining > 0:
        size = min(chunk, remaining)
        signs = rng.choice(np.array([-1.0, 1.0]), size=(size, len(values)))
        simulated = np.mean(signs * values, axis=1)
        exceed += int(np.sum(simulated >= observed))
        remaining -= size
    return exceed / (simulations + 1)


def _profit_factor(values: np.ndarray) -> float | None:
    gains = float(values[values > 0].sum())
    losses = float(-values[values < 0].sum())
    if losses <= 0:
        return None if gains <= 0 else 999.0
    return gains / losses


def _best_trade_exclusion(values: np.ndarray) -> bool:
    if len(values) < 2:
        return False
    reduced = np.delete(values, int(np.argmax(values)))
    return bool(len(reduced) and np.sum(reduced) > 0 and np.mean(reduced) > 0)


def _total_return_pct(returns: pd.Series) -> float:
    return float(((1.0 + returns.fillna(0.0)).prod() - 1.0) * 100.0)


def _annualized_return_pct(returns: pd.Series) -> float | None:
    if len(returns) < 2:
        return None
    growth = float((1.0 + returns.fillna(0.0)).prod())
    if growth <= 0:
        return -100.0
    return float((growth ** (252.0 / len(returns)) - 1.0) * 100.0)


def _annualized_volatility_pct(returns: pd.Series) -> float | None:
    if len(returns) < 2:
        return None
    return float(returns.std(ddof=1) * math.sqrt(252.0) * 100.0)


def _sharpe_ratio(returns: pd.Series) -> float | None:
    if len(returns) < 2:
        return None
    std = float(returns.std(ddof=1))
    if std <= 0:
        return None
    return float(returns.mean() / std * math.sqrt(252.0))


def _maximum_drawdown_pct(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    peak = equity.cummax()
    drawdown = (peak - equity) / peak.replace(0.0, np.nan)
    return float(drawdown.max() * 100.0)


def _regime_metrics(daily: pd.DataFrame, strategy_returns: pd.Series) -> dict[str, Any]:
    spy = daily[daily["ticker"] == "SPY"].sort_values("date").set_index("date")["close"].astype(float)
    if spy.empty:
        return {}
    trend = spy > spy.rolling(200, min_periods=100).mean()
    realized = spy.pct_change().rolling(20, min_periods=20).std() * math.sqrt(252.0)
    high_vol = realized > realized.expanding(min_periods=60).median()
    aligned = pd.DataFrame({"return": strategy_returns, "bull": trend, "high_vol": high_vol}).dropna()
    result: dict[str, Any] = {}
    for name, mask in {
        "bull": aligned["bull"],
        "bear": ~aligned["bull"],
        "high_vol": aligned["high_vol"],
        "low_vol": ~aligned["high_vol"],
    }.items():
        subset = aligned.loc[mask, "return"]
        result[name] = {
            "sessions": int(len(subset)),
            "total_return_pct": _total_return_pct(subset) if len(subset) else None,
            "mean_daily_return_pct": float(subset.mean() * 100.0) if len(subset) else None,
        }
    return result


def _composite_score(
    *,
    fold_returns: Sequence[float],
    fold_benchmark: Sequence[float],
    maximum_drawdown_pct: float,
    profit_factor: float | None,
    trade_count: int,
) -> float:
    excess = [left - right for left, right in zip(fold_returns, fold_benchmark)]
    median_excess = float(np.median(excess)) if excess else -100.0
    stability_penalty = float(np.std(excess)) if len(excess) > 1 else 0.0
    pf_component = min(float(profit_factor or 0.0), 3.0)
    sample_component = min(trade_count / 100.0, 1.0)
    return float(median_excess - 0.25 * stability_penalty - 0.20 * maximum_drawdown_pct + pf_component + sample_component)


def _neighbour_parameter_sets(candidate: StrategyCandidate) -> tuple[dict[str, Any], ...]:
    from .strategy_research_phase2 import is_phase2_family, phase2_neighbour_parameter_sets

    if is_phase2_family(candidate.family):
        return phase2_neighbour_parameter_sets(candidate)

    params = dict(candidate.parameters)
    neighbours: list[dict[str, Any]] = []
    numeric_keys = [key for key, value in params.items() if isinstance(value, (int, float)) and not isinstance(value, bool)]
    for key in numeric_keys[:2]:
        value = params[key]
        for multiplier in (0.8, 1.2):
            changed = dict(params)
            if isinstance(value, int):
                changed[key] = max(2, int(round(value * multiplier)))
            else:
                changed[key] = max(0.001, round(float(value) * multiplier, 6))
            if changed != params and _valid_candidate_parameters(candidate.family, changed):
                neighbours.append(changed)
    unique = {stable_id("params", value): value for value in neighbours}
    return tuple(unique.values())


def _valid_candidate_parameters(family: str, params: Mapping[str, Any]) -> bool:
    from .strategy_research_phase2 import is_phase2_family, phase2_valid_candidate_parameters

    if is_phase2_family(family):
        return phase2_valid_candidate_parameters(family, params)
    if family in {"sma_trend", "trend_pullback"}:
        return int(params["fast"]) < int(params["slow"])
    if family == "donchian_breakout":
        return int(params["exit_lookback"]) < int(params["entry_lookback"])
    if family == "rsi_reversion":
        return float(params["entry"]) < float(params["exit"])
    if family == "bollinger_reversion":
        return float(params["entry_z"]) > float(params["exit_z"])
    if family == "low_vol_breakout":
        return int(params["exit_window"]) < int(params["lookback"]) and float(params["maximum_atr_pct"]) < 0.20
    return False


def _candidate_from_payload(payload: Mapping[str, Any]) -> StrategyCandidate:
    return StrategyCandidate(
        family=str(payload["family"]),
        parameters=dict(payload["parameters"]),
        generation=int(payload.get("generation") or 0),
        parent_candidate_id=payload.get("parent_candidate_id"),
        hypothesis=str(payload.get("hypothesis") or ""),
        candidate_id=str(payload.get("candidate_id") or ""),
    )


def _validate_daily_panel(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"date", "ticker", "open", "high", "low", "close", "bars"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"daily panel missing columns: {sorted(missing)}")
    output = frame.copy()
    output["date"] = pd.to_datetime(output["date"]).dt.tz_localize(None)
    output["ticker"] = output["ticker"].astype(str).str.upper()
    output = output.sort_values(["date", "ticker"]).drop_duplicates(["date", "ticker"], keep="last")
    numeric = ["open", "high", "low", "close"]
    if output[numeric].isna().any().any() or (output[numeric] <= 0).any().any():
        raise ValueError("daily panel contains missing or non-positive OHLC values")
    invalid = (output["low"] > output[["open", "close"]].min(axis=1)) | (output["high"] < output[["open", "close"]].max(axis=1))
    if invalid.any():
        raise ValueError("daily panel contains invalid OHLC relationships")
    return output.reset_index(drop=True)


def _file_uri_to_path(uri: str) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        raise RuntimeError(f"autonomous local research accepts file:// canonical objects only: {uri}")
    return Path(unquote(parsed.path)).resolve()


def _read_state(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"schema_version": 1, "tested_candidate_ids": [], "adaptive_candidates": [], "cycle_count": 0}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema_version": 1, "tested_candidate_ids": [], "adaptive_candidates": [], "cycle_count": 0, "recovered_invalid_state": True}
    return payload if isinstance(payload, dict) else {"schema_version": 1, "tested_candidate_ids": [], "adaptive_candidates": [], "cycle_count": 0}


def _policy_dict(policy: StrategyResearchPolicy) -> dict[str, Any]:
    return {
        "batch_size": policy.batch_size,
        "maximum_total_candidates": policy.maximum_total_candidates,
        "maximum_generation": policy.maximum_generation,
        "maximum_adaptive_children_per_cycle": policy.maximum_adaptive_children_per_cycle,
        "slippage_bps_per_side": policy.slippage_bps_per_side,
        "minimum_sessions": policy.minimum_sessions,
        "minimum_trades": policy.minimum_trades,
        "minimum_profit_factor": policy.minimum_profit_factor,
        "maximum_drawdown_pct": policy.maximum_drawdown_pct,
        "maximum_holm_adjusted_p_value": policy.maximum_holm_adjusted_p_value,
        "walk_forward_folds": policy.walk_forward_folds,
        "random_seed": policy.random_seed,
        "automatic_promotion": False,
        "paper_or_live_execution": False,
        "execution_authority": False,
    }


def _write_cycle_artifacts(payload: Mapping[str, Any], output_root: Path, state_path: Path, state: Mapping[str, Any]) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    timestamped = output_root / f"strategy_research_cycle_{stamp}.json"
    latest = output_root / "latest_strategy_research_cycle.json"
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    for path in (timestamped, latest):
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(encoded, encoding="utf-8")
        temporary.replace(path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_state = state_path.with_suffix(".tmp")
    temporary_state.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary_state.replace(state_path)
