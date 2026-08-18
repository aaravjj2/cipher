#!/usr/bin/env python3
"""Disciplined local experiment for the Obsidian EOD strategy.

The primary family remains 1-minute bars in the final 30 minutes. A secondary
family resamples the same cached 1-minute SIP bars into 5-minute bars and tests a
longer EOD window. No new market-data download is needed.

Protocol:
  * the 2026 development period is split into three chronological folds;
  * candidates are ranked only on those folds, using worst-fold stability and
    cross-symbol breadth;
  * the final 2026 holdout is scored once for the selected candidate per family;
  * no candidate is selected using holdout results.

This is exploratory research, not a promotion or live-trading system.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import sys
from collections import defaultdict
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
# Keep this experiment importable from its dedicated workspace. Do not add the
# repository root: a missing local module should fail loudly instead of silently
# falling back to the shared Cipher tree.
for path in (str(ROOT), str(ROOT / "core")):
    if path not in sys.path:
        sys.path.insert(0, path)

from core import obsidian_eod  # noqa: E402
from scripts.run_obsidian_pine_ytd import (  # noqa: E402

    PINE_DEFAULTS,
    REQUIRED_SYMBOLS,
    PineTrade,
    _et,
    _rth,
    _pooled_summary,
    _summary,
    backtest_symbol,
    load_rows,
)
from core.equity_history_download import EquityBarStore  # noqa: E402
from core.holdout_economics import _holdout_economics  # noqa: E402

UTC = timezone.utc
WORKSPACE = ROOT
DEFAULT_DB = WORKSPACE / "data" / "historical_equities" / "obsidian_pine_ytd_2026" / "equity_bars.sqlite"
DEFAULT_OUT = WORKSPACE / "results" / "obsidian_eod_optimization_2026.json"
DEFAULT_CHECKPOINT_DIR = WORKSPACE / "results" / "checkpoints"


def _rth_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        local = _et(str(row.get("timestamp", row.get("time"))))
        if (local.hour, local.minute) < (9, 30) or (local.hour, local.minute) >= (16, 0):
            continue
        timestamp = str(row.get("timestamp", row.get("time")))
        out.append({
            "time": timestamp,
            "timestamp": timestamp,
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row.get("volume") or 0.0),
        })
    out.sort(key=lambda row: row["time"])
    return out


def _complete_session_rows(rows: list[dict[str, Any]], expected_bars: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Keep only complete regular sessions for a time-bucketed experiment.

    Missing bars can change rolling indicators, delayed-entry offsets, and the
    mandatory EOD exit. The excluded-session counts remain in the report so the
    data decision is auditable rather than hidden.
    """
    grouped: dict[date, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[_et(str(row.get("timestamp", row.get("time")))) .date()].append(row)
    def is_complete(day: date, bucket: list[dict[str, Any]]) -> bool:
        if len(bucket) != expected_bars:
            return False
        local_minutes = {
            (_et(str(row.get("timestamp", row.get("time")))).hour,
             _et(str(row.get("timestamp", row.get("time")))).minute)
            for row in bucket
        }
        expected = {
            divmod(9 * 60 + 30 + offset, 60)
            for offset in range(expected_bars)
        }
        return local_minutes == expected

    complete_days = {day for day, bucket in grouped.items() if is_complete(day, bucket)}
    filtered = [row for row in rows if _et(str(row.get("timestamp", row.get("time")))).date() in complete_days]
    filtered.sort(key=lambda row: str(row.get("timestamp", row.get("time"))))
    return filtered, {
        "expected_bars_per_session": expected_bars,
        "sessions_seen": len(grouped),
        "complete_sessions": len(complete_days),
        "excluded_incomplete_sessions": len(grouped) - len(complete_days),
    }


# Coherent MACD-style triples rather than a full cross product of the three lengths. Sweeping
# fast/slow/sig independently produces mostly degenerate pairs (a fast length above the slow one
# inverts the histogram's meaning) and multiplies the candidate count for no additional coverage.
# These are the pasted strategy's own lengths, a faster pair, and the classic 12/26/9.
INDICATOR_VARIANTS = (
    {"fast_len": 8, "slow_len": 21, "sig_len": 5},
    {"fast_len": 5, "slow_len": 13, "sig_len": 4},
    {"fast_len": 12, "slow_len": 26, "sig_len": 9},
)

# Trend EMA length. The detector uses it only for the A/B grade (`trend_up`/`trend_down`), so it
# changes which signals count as A-grade rather than how many fire.
TREND_LENGTHS = (100, 150, 200)

# Grid keys that control entry timing rather than the signal. Everything else in a grid row is
# forwarded to the detector, so adding a new indicator parameter to a grid needs no change here.
STRATEGY_ONLY_KEYS = frozenset({"entry_delay", "min_signal_lead"})


def _detector_params(params: dict[str, Any]) -> dict[str, Any]:
    """Split a grid row into the detector's parameters.

    Everything that is not entry timing shapes the signal and must reach the detector. This
    used to be three hardcoded fields, which silently discarded any other swept parameter: a
    grid varying `mode` or the EMA lengths ran every candidate as EOD Focus with default
    indicators, producing identical results under different labels.
    """
    return {
        **PINE_DEFAULTS,
        **{key: value for key, value in params.items() if key not in STRATEGY_ONLY_KEYS},
    }


def _resolve_symbols(requested: str | None, db: Path) -> tuple[str, ...]:
    """The universe to test: an explicit list, or everything in the archive.

    Defaulting to the archive's contents means growing the pool is a download, not a code
    change. Symbols are returned sorted so a protocol fingerprint is stable across runs that
    inserted them in a different order.
    """
    if requested:
        return tuple(sorted({s.strip().upper() for s in requested.split(",") if s.strip()}))
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as connection:
        rows = connection.execute(
            "SELECT DISTINCT symbol FROM bars WHERE timeframe = '1Min' ORDER BY symbol"
        ).fetchall()
    return tuple(row[0] for row in rows)


def _protocol_fingerprint(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _write_checkpoint(path: Path, payload: dict[str, Any]) -> None:
    """Atomically persist a resumable checkpoint."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def resample_5m(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate 1-minute bars into session-aligned 5-minute bars.

    The 15:55 bar represents 15:55–15:59 and is therefore the EOD liquidation
    bar for the 5-minute family. Grouping is anchored at 09:30, not UTC midnight.
    """
    groups: dict[tuple[date, int], list[dict[str, Any]]] = defaultdict(list)
    for row in _rth_rows(rows):
        local = _et(str(row.get("timestamp", row.get("time"))))
        offset = (local.hour * 60 + local.minute) - (9 * 60 + 30)
        groups[(local.date(), offset // 5)].append(row)
    out = []
    buckets_by_day: dict[date, list[list[dict[str, Any]]]] = defaultdict(list)
    for (day, _bucket), bucket in sorted(groups.items()):
        buckets_by_day[day].append(bucket)
    out = []
    for day, day_buckets in sorted(buckets_by_day.items()):
        # A valid session must contain all 78 five-minute bars and every bar
        # must contain five one-minute constituents. Drop the entire session,
        # rather than silently evaluating a shortened day.
        if len(day_buckets) != 78 or any(len(bucket) != 5 for bucket in day_buckets):
            continue
        for bucket in day_buckets:
            first = bucket[0]
            timestamp = str(first.get("timestamp", first.get("time")))
            out.append({
                "timestamp": timestamp,
                "time": timestamp,
                "open": float(bucket[0]["open"]),
                "high": max(float(x["high"]) for x in bucket),
                "low": min(float(x["low"]) for x in bucket),
                "close": float(bucket[-1]["close"]),
                "volume": sum(float(x["volume"] or 0.0) for x in bucket),
            })
    return out


def _fixed_time_control(
    data: dict[str, list[dict[str, Any]]],
    *,
    start: date,
    end: date,
    entry_minute: int = 30,
    exit_minute: int = 59,
) -> dict[str, Any]:
    """Matched long-only 15:30-to-EOD control on the same complete sessions."""
    all_rows: list[dict[str, Any]] = []
    all_control_bars: list[dict[str, Any]] = []
    for symbol, bars in data.items():
        by_day: dict[date, list[dict[str, Any]]] = defaultdict(list)
        for bar in bars:
            local = _et(str(bar["time"]))
            if start <= local.date() <= end:
                by_day[local.date()].append(bar)
        trades = []
        symbol_control_bars = [bar for session in by_day.values() for bar in session]
        all_control_bars.extend(symbol_control_bars)
        for day, session in by_day.items():
            entry = next((b for b in session if _et(b["time"]).hour == 15 and _et(b["time"]).minute == entry_minute), None)
            exit_bar = next((b for b in session if _et(b["time"]).hour == 15 and _et(b["time"]).minute == exit_minute), None)
            if not entry or not exit_bar:
                continue
            raw_entry = float(entry["close"])
            raw_exit = float(exit_bar["close"])
            entry_px = raw_entry + 0.01
            exit_px = raw_exit - 0.01
            trades.append(PineTrade(
                symbol=symbol, direction="LONG", signal_time=entry["time"], entry_time=entry["time"],
                exit_time=exit_bar["time"], signal_price=raw_entry,
                entry_price_before_slippage=raw_entry, entry_price=entry_px,
                exit_price_before_slippage=raw_exit, exit_price=exit_px,
                gross_return_pct=round((raw_exit / raw_entry - 1.0) * 100.0, 6),
                net_return_pct=round((exit_px / entry_px - 1.0) * 100.0, 6),
                bars_held=exit_minute - entry_minute, signal_kind="FIXED_TIME_CONTROL",
            ))
        all_rows.append({
            "symbol": symbol,
            "trades": [asdict(t) for t in trades],
            "summary": _summary(trades, symbol_control_bars) if trades else {"trades": 0},
        })
    pooled = _metric_from_rows(all_rows)
    if all_control_bars:
        pooled["first_bar"] = min(row["time"] for row in all_control_bars)
        pooled["last_bar"] = max(row["time"] for row in all_control_bars)
    return pooled


def _metric_from_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    trades = [PineTrade(**trade) for row in rows for trade in row["trades"]]
    bars = [{"time": t.entry_time, "close": t.entry_price_before_slippage} for t in trades]
    pooled = _pooled_summary(trades, bars)
    symbol_stats = {row["symbol"]: row["summary"] for row in rows}
    positive_symbols = sum((stats.get("avg_trade_return_pct") or 0.0) > 0 for stats in symbol_stats.values())
    usable = [stats.get("profit_factor") for stats in symbol_stats.values() if stats.get("profit_factor") is not None]
    pooled["positive_symbols"] = positive_symbols
    pooled["symbol_count"] = len(symbol_stats)
    pooled["minimum_symbol_profit_factor"] = round(min(usable), 6) if usable else None
    pooled["symbol_stats"] = symbol_stats
    return pooled


def _evaluate(
    data: dict[str, list[dict[str, Any]]],
    *,
    start: date,
    end: date,
    params: dict[str, Any],
    bar_minutes: int,
    eod_exit_minute: int,
    precomputed: dict[str, tuple[list[dict[str, Any]], list[obsidian_eod.BarState]]] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows = []
    for symbol, bars in data.items():
        rows.append(backtest_symbol(
            symbol,
            bars,
            strategy_mode="CLPS Only",
            entry_delay=int(params["entry_delay"]),
            min_signal_lead=int(params["min_signal_lead"]),
            rls_lookback=10,
            rls_relation="Opposite",
            tick_size=0.01,
            evaluation_start=start,
            evaluation_end=end,
            # Use the exact detector configuration that the candidate was selected
            # under. Reconstructing only arm/sig/clps here silently reverted
            # Full Session and swept indicator lengths to defaults on holdout.
            detector_params=_detector_params(params),
            precomputed_states=(precomputed or {}).get(symbol, (None, None))[1],
            bars_are_rth=True,
            eod_exit_minute=eod_exit_minute,
            bar_minutes=bar_minutes,
        ))
    return _metric_from_rows(rows), rows


def _folds() -> list[tuple[date, date]]:
    return [
        (date(2026, 1, 2), date(2026, 2, 27)),
        (date(2026, 3, 2), date(2026, 4, 30)),
        (date(2026, 5, 1), date(2026, 5, 29)),
    ]


def _rank_key(row: dict[str, Any]) -> tuple:
    # Breadth and worst-fold performance precede pooled averages. Thresholds
    # scale with the tested universe; keeping the old 6-symbol preference on a
    # 41-symbol universe would reward candidates that fail on most names.
    fold_rows = row["folds"]
    avg_values = [f["avg_trade_return_pct"] for f in fold_rows if f.get("avg_trade_return_pct") is not None]
    pf_values = [f["profit_factor"] for f in fold_rows if f.get("profit_factor") is not None]
    preferred_breadth = int(row.get("preferred_positive_symbols") or 1)
    breadth = sum(f.get("positive_symbols", 0) >= preferred_breadth for f in fold_rows)
    return (
        breadth,
        min(avg_values) if avg_values else -999.0,
        min(pf_values) if pf_values else -999.0,
        sum(avg_values) / len(avg_values) if avg_values else -999.0,
        row.get("pooled_avg_trade_return_pct") or -999.0,
    )


def _params_key(params: dict[str, Any]) -> str:
    return json.dumps(params, sort_keys=True, separators=(",", ":"))


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _run_family(
    family: str,
    data: dict[str, list[dict[str, Any]]],
    grid: list[dict[str, Any]],
    *,
    holdout_start: date,
    holdout_end: date,
    bar_minutes: int,
    eod_exit_minute: int,
    checkpoint_path: Path | None = None,
    precomputed: dict[str, tuple[list[dict[str, Any]], list[obsidian_eod.BarState]]] | None = None,
    protocol_fingerprint: str | None = None,
) -> dict[str, Any]:
    candidates = []
    completed_keys: set[str] = set()
    symbol_count = max(len(data), 1)
    min_total_train_trades = max(100, 10 * symbol_count)
    min_fold_trades = max(20, 2 * symbol_count)
    min_positive_symbols = max(1, math.ceil(symbol_count * 0.50))
    preferred_positive_symbols = max(1, math.ceil(symbol_count * 0.60))
    if checkpoint_path and checkpoint_path.exists():
        try:
            saved = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            if (
                saved.get("schema_version") != 2
                or saved.get("protocol_fingerprint") != protocol_fingerprint
                or saved.get("grid_size") != len(grid)
            ):
                raise ValueError("checkpoint protocol does not match this run")
            candidates = list(saved.get("candidates") or [])
            completed_keys = set(saved.get("completed_keys") or [])
            print(f"{family}: resumed {len(completed_keys)}/{len(grid)} candidates from {checkpoint_path}")
        except (OSError, ValueError, TypeError) as exc:
            print(f"{family}: ignoring unreadable checkpoint: {exc}")
    # Grid ordering groups candidates that share detector state (usually only
    # entry_delay differs). Keep just the current detector snapshot. Retaining
    # every parameter set means millions of BarState objects per key on a
    # 41-symbol minute archive and can turn a sweep into a memory-pressure test.
    cached_detector_key: str | None = None
    cached_precomputed: dict[str, tuple[list[dict[str, Any]], list[obsidian_eod.BarState]]] | None = None
    for number, params in enumerate(grid, start=1):
        key = _params_key(params)
        if key in completed_keys:
            continue
        fold_metrics = []
        total_train_trades = 0
        # Pass every non-strategy key through to the detector rather than naming three of them.
        # This used to hardcode arm_minutes/sig_mult/clps_thresh, which silently discarded any
        # other swept parameter: a grid varying `mode` or the EMA lengths would have run every
        # candidate as EOD Focus with default indicators and produced identical results under
        # different labels. Only entry timing is the strategy's own; the rest shapes the signal.
        detector_params = _detector_params(params)
        # Indicator state is invariant across chronological fold slicing for a given candidate,
        # so it is computed once instead of three times. The cache key is the whole detector
        # parameter set: keying on a hand-picked subset means two candidates that differ only in
        # an unlisted field share states, and the second is scored with the first's indicators.
        detector_key = _params_key(detector_params)
        if precomputed is not None:
            precomputed_candidate = precomputed
        elif detector_key == cached_detector_key and cached_precomputed is not None:
            precomputed_candidate = cached_precomputed
        else:
            precomputed_candidate = {
                symbol: (bars, obsidian_eod.compute(bars, detector_params)[0])
                for symbol, bars in data.items()
            }
            cached_detector_key = detector_key
            cached_precomputed = precomputed_candidate
        for start, end in _folds():
            metrics, _ = _evaluate(
                data,
                start=start,
                end=end,
                params=params,
                bar_minutes=bar_minutes,
                eod_exit_minute=eod_exit_minute,
                precomputed=precomputed_candidate,
            )
            fold_metrics.append(metrics)
            total_train_trades += int(metrics.get("trades", 0))
        if total_train_trades < min_total_train_trades:
            candidate = {
                "family": family,
                "params": params,
                "folds": fold_metrics,
                "train_trade_count": total_train_trades,
                "selection_eligible": False,
                "skip_reason": f"fewer than {min_total_train_trades} pooled training trades",
                "minimum_total_training_trades": min_total_train_trades,
                "minimum_fold_trades": min_fold_trades,
                "minimum_positive_symbols": min_positive_symbols,
                "preferred_positive_symbols": preferred_positive_symbols,
            }
            candidates.append(candidate)
            completed_keys.add(key)
            if checkpoint_path:
                _write_checkpoint(checkpoint_path, {
                    "schema_version": 2,
                    "family": family,
                    "grid_size": len(grid),
                    "protocol_fingerprint": protocol_fingerprint,
                    "completed_keys": sorted(completed_keys),
                    "candidates": candidates,
                })
            continue
        # Use the fold means for ranking, while retaining the pooled total as a
        # sample-size diagnostic. The holdout is not touched here.
        avg_returns = [m["avg_trade_return_pct"] for m in fold_metrics if m.get("avg_trade_return_pct") is not None]
        pfs = [m["profit_factor"] for m in fold_metrics if m.get("profit_factor") is not None]
        candidate = {
            "family": family,
            "params": params,
            "folds": fold_metrics,
            "train_trade_count": total_train_trades,
            "mean_fold_avg_return_pct": round(sum(avg_returns) / len(avg_returns), 6) if avg_returns else None,
            "worst_fold_avg_return_pct": round(min(avg_returns), 6) if avg_returns else None,
            "mean_fold_profit_factor": round(sum(pfs) / len(pfs), 6) if pfs else None,
            "worst_fold_profit_factor": round(min(pfs), 6) if pfs else None,
            "eligible_all_folds_positive": bool(avg_returns and min(avg_returns) > 0),
            "minimum_total_training_trades": min_total_train_trades,
            "minimum_fold_trades": min_fold_trades,
            "minimum_positive_symbols": min_positive_symbols,
            "preferred_positive_symbols": preferred_positive_symbols,
            "eligible_breadth": all(
                f.get("positive_symbols", 0) >= min_positive_symbols for f in fold_metrics
            ),
        }
        candidate["selection_eligible"] = (
            all(int(f.get("trades", 0)) >= min_fold_trades for f in fold_metrics)
            and candidate["eligible_breadth"]
            and sum(float(v) > 0 for v in avg_returns) >= 2
        )
        candidates.append(candidate)
        completed_keys.add(key)
        if checkpoint_path:
            _write_checkpoint(checkpoint_path, {
                "schema_version": 2,
                "family": family,
                "grid_size": len(grid),
                "protocol_fingerprint": protocol_fingerprint,
                "completed_keys": sorted(completed_keys),
                "candidates": candidates,
            })
        if number % 10 == 0 or len(completed_keys) == len(grid):
            print(f"{family}: evaluated {len(completed_keys)}/{len(grid)}")
    eligible = [row for row in candidates if row.get("selection_eligible")]
    # Prefer candidates that are positive in all development folds. If that
    # strict set is empty, retain the explicit two-of-three fallback rather than
    # quietly calling a losing configuration "best".
    strict = [row for row in eligible if row.get("eligible_all_folds_positive")]
    # Never crown a losing development result. If no candidate is positive in
    # every development fold, the family has no training-selected candidate and
    # therefore receives no holdout score.
    ranked = strict
    ranked.sort(key=_rank_key, reverse=True)
    chosen = ranked[0] if ranked else None
    holdout = None
    holdout_rows = []
    if chosen:
        holdout, holdout_rows = _evaluate(
            data,
            start=holdout_start,
            end=holdout_end,
            params=chosen["params"],
            bar_minutes=bar_minutes,
            eod_exit_minute=eod_exit_minute,
            precomputed=precomputed,
        )
    return {
        "family": family,
        "grid_size": len(grid),
        "surviving_candidates": len(candidates),
        "selection_eligible_candidates": len(eligible),
        "strict_all_folds_positive_candidates": len(strict),
        "chosen_from_training": chosen,
        "holdout": holdout,
        "holdout_rows": holdout_rows,
        "top_training_candidates": ranked[:10],
        "selection_thresholds": {
            "minimum_total_training_trades": min_total_train_trades,
            "minimum_fold_trades": min_fold_trades,
            "minimum_positive_symbols": min_positive_symbols,
            "preferred_positive_symbols": preferred_positive_symbols,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--checkpoint-dir", type=Path, default=DEFAULT_CHECKPOINT_DIR)
    parser.add_argument(
        "--family",
        choices=("all", "1m", "5m", "1m_windows", "5m_windows", "1m_full", "5m_full"),
        default="all",
    )
    parser.add_argument(
        "--symbols",
        default=None,
        help="Comma-separated universe. Defaults to every symbol in the archive, so growing "
             "the test pool is a download rather than a code change.",
    )
    parser.add_argument("--holdout-start", default="2026-06-01")
    parser.add_argument("--holdout-end", default="2026-08-11")
    parser.add_argument("--risk-free-pct", type=float, default=4.0)
    args = parser.parse_args()
    holdout_start = date.fromisoformat(args.holdout_start)
    holdout_end = date.fromisoformat(args.holdout_end)

    args.db = args.db.resolve()
    if not args.db.exists():
        parser.error(f"database does not exist: {args.db}")
    store = EquityBarStore(args.db.parent, db_path=args.db)
    # The universe is whatever the archive holds unless it is named explicitly, rather than the
    # ten symbols the first run happened to download. Searching for where an edge lives needs
    # the pool to grow without editing code, and the original ten were eight mega-cap tech
    # names plus SPY/QQQ -- a universe concentrated enough that one semiconductor name supplied
    # half of the crowned candidate's positive contribution.
    symbols = _resolve_symbols(args.symbols, args.db)
    print(f"universe: {len(symbols)} symbols")
    data_1m: dict[str, list[dict[str, Any]]] = {}
    completeness: dict[str, dict[str, Any]] = {}
    for symbol in symbols:
        rth = _rth(load_rows(store, symbol, "1Min"))
        complete, summary = _complete_session_rows(rth, 390)
        data_1m[symbol] = complete
        completeness[symbol] = summary
    data_5m = {symbol: resample_5m(rows) for symbol, rows in data_1m.items()}
    print("loaded complete 1m rows:", {s: len(v) for s, v in data_1m.items()})
    print("resampled 5m rows:", {s: len(v) for s, v in data_5m.items()})

    # Treat each time window as its own family. Selecting one winner from a grid
    # that mixes 10/20/30/40-minute windows answers only "which window won this
    # search"; it does not tell us whether the effect is specific to the last ten
    # minutes or survives a wider EOD window. Each family therefore selects on
    # development folds independently and receives its own locked-holdout score.
    grid_1m_by_window = {
        arm: [
            {"arm_minutes": arm, "sig_mult": sig, "clps_thresh": clps,
             "entry_delay": delay, "min_signal_lead": max(4, delay + 2)}
            for sig in (1.0, 1.1)
            for clps in (0.5, 0.6)
            for delay in (1, 2, 3)
        ]
        for arm in (10, 20, 30, 40)
    }
    grid_5m_by_window = {
        arm: [
            {"arm_minutes": arm, "sig_mult": sig, "clps_thresh": clps,
             "entry_delay": delay, "min_signal_lead": max(10, (delay + 1) * 5)}
            for sig in (1.0, 1.1)
            for clps in (0.5, 0.6)
            for delay in (1, 2)
        ]
        for arm in (30, 60, 90, 120)
    }

    # Full-session grids. `arm_minutes` is dropped because Full Session admits every RTH bar.
    # `trend_len` is also intentionally not swept here: this experiment trades CLPS Only, and
    # trend_len affects only the A/B grade. Sweeping it produced three identical trade lists per
    # signal configuration under different labels. Indicator MACD lengths do affect raw CLPS and
    # remain a real dimension.
    grid_1m_full = [
        {"mode": "Full Session", "sig_mult": sig, "clps_thresh": clps,
         "entry_delay": delay, "min_signal_lead": max(4, delay + 2), **indicators}
        for sig in (1.0, 1.1)
        for clps in (0.5, 0.6)
        for indicators in INDICATOR_VARIANTS
        for delay in (1, 2)
    ]
    grid_5m_full = [
        {"mode": "Full Session", "sig_mult": sig, "clps_thresh": clps,
         "entry_delay": delay, "min_signal_lead": max(10, (delay + 1) * 5), **indicators}
        for sig in (1.0, 1.1)
        for clps in (0.5, 0.6)
        for indicators in INDICATOR_VARIANTS
        for delay in (1, 2)
    ]

    protocol_base = {
        "database_sha256": _file_sha256(args.db),
        # Checkpoints are valid only for the implementation that produced them.
        # Previously a backtest-code fix could silently resume old candidate
        # metrics because the fingerprint covered the data/grid but not the code.
        "implementation_sha256": {
            "detector": _file_sha256(ROOT / "core" / "obsidian_eod.py"),
            "runner": _file_sha256(ROOT / "scripts" / "run_obsidian_pine_ytd.py"),
            "experiment": _file_sha256(Path(__file__).resolve()),
        },
        "symbols": list(symbols),
        "train_folds": [[a.isoformat(), b.isoformat()] for a, b in _folds()],
        "holdout": [args.holdout_start, args.holdout_end],
        "session_filter": "complete RTH sessions only",
        "one_trade_per_day": True,
        "strategy_mode": "CLPS Only",
        "risk_free_pct": args.risk_free_pct,
    }
    result = {
        "schema_version": 2,
        "workspace": str(WORKSPACE),
        "family_requested": args.family,
        "generated_at": datetime.now(UTC).isoformat(),
        "research_role": "exploratory_development_with_locked_temporal_holdout",
        "data": {
            "database": str(args.db),
            "database_sha256": protocol_base["database_sha256"],
            "implementation_sha256": protocol_base["implementation_sha256"],
            "symbols": list(symbols),
            "risk_free_pct": args.risk_free_pct,
            "holdout": {"start": args.holdout_start, "end": args.holdout_end},
            "train_folds": [[a.isoformat(), b.isoformat()] for a, b in _folds()],
            "warmup": "pre-2026 bars are loaded for indicators; no warmup trades are eligible",
            "session_completeness": completeness,
            "effective_complete_session_end": max(
                (_et(str(row["time"])) for bars in data_1m.values() for row in bars),
                default=None,
            ).isoformat() if any(data_1m.values()) else None,
            "effective_holdout_end_date": max(
                (
                    _et(str(row["time"])).date()
                    for bars in data_1m.values()
                    for row in bars
                    if holdout_start <= _et(str(row["time"])).date() <= holdout_end
                ),
                default=None,
            ).isoformat() if any(data_1m.values()) else None,
        },
        "families": [],
        "controls": {},
    }
    # Controls are not candidates and do not affect selection. They provide a
    # same-data reference for the primary holdout and development periods.
    control_train = _fixed_time_control(
        data_1m, start=date(2026, 1, 2), end=date(2026, 5, 29)
    )
    control_holdout = _fixed_time_control(
        data_1m, start=holdout_start, end=holdout_end
    )
    result["controls"] = {
        "fixed_long_15_30_to_15_59": {
            "training": control_train,
            "holdout": control_holdout,
            "note": "Long-only fixed-time control; same complete sessions, one simulated trade per symbol per day, one-tick entry/exit slippage.",
        }
    }
    one_minute_windows = (30,) if args.family == "1m" else tuple(grid_1m_by_window)
    five_minute_windows = (60, 90, 120) if args.family == "5m" else tuple(grid_5m_by_window)
    if args.family in {"all", "1m", "1m_windows"}:
        for arm in one_minute_windows:
            family_name = "1m_final_30_primary" if arm == 30 else f"1m_last_{arm}"
            grid = grid_1m_by_window[arm]
            result["families"].append(_run_family(
                family_name, data_1m, grid,
                holdout_start=holdout_start, holdout_end=holdout_end,
                bar_minutes=1, eod_exit_minute=59,
                checkpoint_path=args.checkpoint_dir / f"{family_name}.json",
                protocol_fingerprint=_protocol_fingerprint({
                    **protocol_base, "family": family_name, "grid": grid,
                    "bar_minutes": 1, "eod_exit_minute": 59,
                }),
            ))
    if args.family in {"all", "5m", "5m_windows"}:
        for arm in five_minute_windows:
            family_name = f"5m_last_{arm}"
            grid = grid_5m_by_window[arm]
            result["families"].append(_run_family(
                family_name, data_5m, grid,
                holdout_start=holdout_start, holdout_end=holdout_end,
                bar_minutes=5, eod_exit_minute=55,
                checkpoint_path=args.checkpoint_dir / f"{family_name}.json",
                protocol_fingerprint=_protocol_fingerprint({
                    **protocol_base, "family": family_name, "grid": grid,
                    "bar_minutes": 5, "eod_exit_minute": 55,
                }),
            ))
    if args.family in {"all", "1m_full"}:
        result["families"].append(_run_family(
            "1m_full_session", data_1m, grid_1m_full,
            holdout_start=holdout_start, holdout_end=holdout_end,
            bar_minutes=1, eod_exit_minute=59,
            checkpoint_path=args.checkpoint_dir / "1m_full_session.json",
            protocol_fingerprint=_protocol_fingerprint({**protocol_base, "family": "1m_full_session", "grid": grid_1m_full, "bar_minutes": 1, "eod_exit_minute": 59}),
        ))
    if args.family in {"all", "5m_full"}:
        result["families"].append(_run_family(
            "5m_full_session", data_5m, grid_5m_full,
            holdout_start=holdout_start, holdout_end=holdout_end,
            bar_minutes=5, eod_exit_minute=55,
            checkpoint_path=args.checkpoint_dir / "5m_full_session.json",
            protocol_fingerprint=_protocol_fingerprint({**protocol_base, "family": "5m_full_session", "grid": grid_5m_full, "bar_minutes": 5, "eod_exit_minute": 55}),
        ))
    result["conclusion"] = [
        "The 1-minute final-30-minute family remains the pasted strategy's primary reference; fixed EOD windows and full-session families are evaluated separately to test time-window specificity.",
        "Incomplete regular sessions were excluded before indicator computation; the exclusion counts are recorded in data.session_completeness.",
        "Candidate selection requires positive average returns in every development fold; if no candidate meets that rule, no holdout candidate is reported.",
        "Candidate selection uses development folds only. Holdout metrics are descriptive and were not used to choose parameters.",
        "This is not a final validation: the 2026 holdout is short and the detector remains a Python reconstruction of Pine.",
    ]

    # State the economic verdict here rather than leaving it to a reader.
    #
    # Every holdout figure above is a *pooled* sum: per-trade percentages added across ten
    # symbols. That measures the signal and misstates the return by roughly the symbol count,
    # and the conclusion list previously described only the method -- so a crowned candidate
    # read as a promising result even when it loses to cash. The first run this was applied to
    # returned +6.999% pooled, which is +0.735% equal-weight, +3.84% annualized, below the 4%
    # risk-free rate. `holdout_economics` also records the leave-one-out check, because that
    # run's entire positive result disappeared when its single best symbol was excluded.
    for family in result["families"]:
        trades = [t for row in family.get("holdout_rows") or [] for t in row["trades"]]
        if not trades:
            continue
        econ = _holdout_economics(
            trades,
            holdout_start,
            holdout_end,
            universe=symbols,
            risk_free_pct=args.risk_free_pct,
        )
        family["holdout_economics"] = econ
        carried = econ["leave_one_out"][0] if econ["leave_one_out"] else None
        verdict = "beats" if econ["beats_risk_free"] else "does not beat"
        result["conclusion"].append(
            f"{family['family']}: holdout equal-weight compounded "
            f"{econ['equal_weight_pct']:+.3f}% over {econ['days']} days "
            f"({econ['annualized_pct']:+.2f}% annualized) {verdict} the "
            f"{econ['risk_free_pct']:.0f}% risk-free rate. The pooled sum "
            f"({econ['pooled_sum_pct']:+.3f}%) is {econ['overstatement_ratio']:.1f}x that and "
            "is not a return."
        )
        if carried and carried["equal_weight_pct"] < 0 < econ["equal_weight_pct"]:
            result["conclusion"].append(
                f"{family['family']}: the positive holdout result does not survive excluding "
                f"{carried['symbol']} alone ({carried['annualized_pct']:+.2f}% annualized "
                f"without it), so the edge is concentrated in one symbol rather than "
                "demonstrated across the universe."
            )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.checkpoint_dir.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    for family in result["families"]:
        chosen = family.get("chosen_from_training") or {}
        print(f"{family['family']} chosen={chosen.get('params')} holdout={family.get('holdout')}")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
