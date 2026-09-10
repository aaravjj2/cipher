"""Grounded diagnostic replay for Autopilot scanner signals.

Scanner captures establish *when* a signal existed. They are never used as a
future price path. The replay enters at the next complete one-minute underlying
bar and evaluates target/invalidation against subsequent OHLC bars.

This module deliberately does not estimate option prices or portfolio P&L.
Those claims require point-in-time contract selection and historical option
NBBO/trades, which the capture interval does not contain.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any, Iterable

from .capture_backtest import Observation, dedupe_entries, load_observations
from .config import ExecutorConfig, load_config
from .policy import parse_hhmm, to_et_time


MAXIMUM_ENTRY_DELAY_SECONDS = 120


def _allowed(obs: Observation, cfg: ExecutorConfig) -> bool:
    patterns = cfg.strategy.allowed_patterns
    setup_ok = (
        any(
            obs.scan_type == row.get("scanner_type")
            and obs.setup == row.get("setup")
            and obs.direction == row.get("direction")
            for row in patterns
        )
        if patterns
        else obs.setup in cfg.strategy.allowed_setups.get(obs.scan_type, ())
    )
    ticker_ok = not cfg.strategy.allowed_tickers or obs.ticker in cfg.strategy.allowed_tickers
    current = to_et_time(obs.captured_at)
    start = parse_hhmm(cfg.strategy.entry_window_et_start or "00:00")
    end = parse_hhmm(cfg.strategy.entry_window_et_end or "23:59")
    return setup_ok and ticker_ok and start <= current < end


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _sha256_files(paths: Iterable[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(set(paths), key=str):
        digest.update(str(path).encode())
        digest.update(b"\0")
        try:
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError:
            digest.update(b"<unreadable>")
        digest.update(b"\0")
    return digest.hexdigest()


def _open_bars(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(f"Underlying bar database does not exist: {path}")
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    columns = {str(row[1]) for row in connection.execute("pragma table_info(bars)").fetchall()}
    required = {"symbol", "timeframe", "timestamp", "open", "high", "low", "close"}
    if not required.issubset(columns):
        connection.close()
        raise ValueError(f"bars table is missing columns: {sorted(required - columns)}")
    return connection


def _bars_for_signal(
    connection: sqlite3.Connection,
    entry: Observation,
    maximum_hold_minutes: int,
) -> list[dict[str, Any]]:
    # A signal observed partway through minute N cannot fill at minute N's open.
    deadline = entry.captured_at + timedelta(minutes=maximum_hold_minutes)
    rows = connection.execute(
        """
        select timestamp, open, high, low, close
        from bars
        where symbol = ? and timeframe = '1Min' and timestamp > ? and timestamp <= ?
        order by timestamp
        """,
        (
            entry.ticker,
            entry.captured_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            deadline.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        ),
    ).fetchall()
    return [
        {
            "timestamp": _utc(str(row[0])),
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
        }
        for row in rows
    ]


def _bar_digest(rows: Iterable[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        normalized = (
            row["timestamp"].isoformat(),
            row["open"],
            row["high"],
            row["low"],
            row["close"],
        )
        digest.update(json.dumps(normalized, separators=(",", ":")).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def _bar_hits(entry: Observation, bar: dict[str, Any]) -> tuple[bool, bool]:
    if entry.direction == "bullish":
        targeted = entry.target is not None and bar["high"] >= entry.target
        invalidated = entry.invalidation is not None and bar["low"] <= entry.invalidation
    else:
        targeted = entry.target is not None and bar["low"] <= entry.target
        invalidated = entry.invalidation is not None and bar["high"] >= entry.invalidation
    return targeted, invalidated


def _score_signal(
    entry: Observation,
    connection: sqlite3.Connection,
    cfg: ExecutorConfig,
) -> tuple[dict[str, Any] | None, str | None, list[dict[str, Any]]]:
    path = _bars_for_signal(connection, entry, cfg.exit.maximum_hold_minutes)
    if not path:
        return None, "no_underlying_bars", []
    entry_bar = path[0]
    delay = (entry_bar["timestamp"] - entry.captured_at.astimezone(timezone.utc)).total_seconds()
    if delay <= 0 or delay > MAXIMUM_ENTRY_DELAY_SECONDS:
        return None, "next_bar_too_late", path

    entry_price = entry_bar["open"]
    if entry_price <= 0:
        return None, "invalid_entry_bar", path

    exit_bar = path[-1]
    outcome = "horizon_close"
    for bar in path:
        target_hit, invalidation_hit = _bar_hits(entry, bar)
        if target_hit and invalidation_hit:
            # Minute OHLC cannot establish which barrier happened first.
            return None, "ambiguous_barrier_order", path
        if invalidation_hit:
            exit_bar, outcome = bar, "underlying_invalidation"
            break
        if target_hit:
            exit_bar, outcome = bar, "underlying_target"
            break

    exit_price = (
        float(entry.target)
        if outcome == "underlying_target"
        else float(entry.invalidation)
        if outcome == "underlying_invalidation"
        else exit_bar["close"]
    )
    raw_return = (exit_price - entry_price) / entry_price
    directional_return = raw_return if entry.direction == "bullish" else -raw_return
    return {
        "signal_time": entry.captured_at.isoformat(),
        "entry_time": entry_bar["timestamp"].isoformat(),
        "exit_time": exit_bar["timestamp"].isoformat(),
        "ticker": entry.ticker,
        "scanner_type": entry.scan_type,
        "setup": entry.setup,
        "direction": entry.direction,
        "scanner_spot": entry.spot,
        "entry_underlying_open": round(entry_price, 6),
        "exit_underlying_price": round(exit_price, 6),
        "target": entry.target,
        "invalidation": entry.invalidation,
        "outcome": outcome,
        "directional_underlying_return_bps": round(directional_return * 10_000, 3),
        "entry_delay_seconds": round(delay, 3),
        "bars_observed": len(path),
        "bar_path_sha256": _bar_digest(path),
        "source_file": entry.source_file,
    }, None, path


def _diagnostics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    values = [float(row["directional_underlying_return_bps"]) for row in rows]
    outcomes = Counter(str(row["outcome"]) for row in rows)
    positive = sum(value > 0 for value in values)
    return {
        "evaluated_signals": len(rows),
        "outcomes": dict(sorted(outcomes.items())),
        "directionally_positive": positive,
        "directionally_nonpositive": len(values) - positive,
        "directionally_positive_rate_pct": round(positive / len(values) * 100, 2) if values else None,
        "mean_directional_underlying_return_bps": round(mean(values), 3) if values else None,
        "median_directional_underlying_return_bps": round(median(values), 3) if values else None,
    }


def run_backtest(
    root: Path,
    start: date,
    end: date,
    cfg: ExecutorConfig,
    underlying_bars_db: Path,
) -> dict[str, Any]:
    loaded: list[Observation] = []
    day = start
    while day <= end:
        loaded.extend(load_observations(root, day))
        day += timedelta(days=1)
    unique = {
        (
            row.captured_at,
            row.ticker,
            row.scan_type,
            row.setup,
            row.direction,
            row.spot,
            row.target,
            row.invalidation,
            row.score,
            row.rank,
        ): row
        for row in loaded
    }
    observations = sorted(unique.values(), key=lambda row: row.captured_at)
    by_day: dict[date, list[Observation]] = defaultdict(list)
    for row in observations:
        by_day[row.captured_at.date()].append(row)

    rejected: Counter[str] = Counter()
    candidates: list[dict[str, Any]] = []
    used_bar_rows: list[dict[str, Any]] = []
    connection = _open_bars(underlying_bars_db)
    try:
        for rows in by_day.values():
            eligible = [row for row in rows if _allowed(row, cfg)]
            for entry in dedupe_entries(eligible, cfg.scanner.episode_cooldown_minutes):
                scored, reason, bar_rows = _score_signal(entry, connection, cfg)
                used_bar_rows.extend(bar_rows)
                if scored is None:
                    rejected[reason or "unknown"] += 1
                else:
                    candidates.append(scored)
    finally:
        connection.close()

    # Reproduce only constraints that do not depend on invented option P&L.
    selected: list[dict[str, Any]] = []
    active: list[dict[str, Any]] = []
    entries_by_day: Counter[str] = Counter()
    entries_by_ticker_day: Counter[tuple[str, str]] = Counter()
    for signal in sorted(candidates, key=lambda row: row["entry_time"]):
        entered = datetime.fromisoformat(signal["entry_time"])
        active = [row for row in active if datetime.fromisoformat(row["exit_time"]) > entered]
        market_day = signal["entry_time"][:10]
        ticker_positions = sum(row["ticker"] == signal["ticker"] for row in active)
        if len(active) >= cfg.portfolio.maximum_open_positions:
            rejected["maximum_open_positions"] += 1
        elif ticker_positions >= cfg.portfolio.maximum_positions_per_ticker:
            rejected["maximum_positions_per_ticker"] += 1
        elif entries_by_day[market_day] >= cfg.portfolio.maximum_new_positions_per_day:
            rejected["maximum_new_positions_per_day"] += 1
        elif entries_by_ticker_day[(market_day, signal["ticker"])] >= cfg.portfolio.maximum_new_positions_per_ticker_per_day:
            rejected["maximum_new_positions_per_ticker_per_day"] += 1
        else:
            selected.append(signal)
            active.append(signal)
            entries_by_day[market_day] += 1
            entries_by_ticker_day[(market_day, signal["ticker"])] += 1

    dates = sorted({row["entry_time"][:10] for row in selected})
    split = max(1, int(len(dates) * 0.7)) if dates else 0
    later_dates = set(dates[split:])
    later_slice = [row for row in selected if row["entry_time"][:10] in later_dates]
    capture_paths = [Path(row.source_file) for row in observations]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "grounded_underlying_signal_replay",
        "evidence_grade": "DIAGNOSTIC_ONLY",
        "paper_only": True,
        "claims": {
            "option_pnl": False,
            "executable_option_fills": False,
            "out_of_sample_performance": False,
            "live_trading_readiness": False,
        },
        "sources": {
            "scanner_capture_root": str(root.resolve()),
            "scanner_files": len(set(capture_paths)),
            "scanner_files_sha256": _sha256_files(capture_paths),
            "underlying_bars_db": str(underlying_bars_db.resolve()),
            "underlying_timeframe": "1Min",
            "used_bar_rows": len(used_bar_rows),
            "used_bar_rows_sha256": _bar_digest(used_bar_rows),
        },
        "period": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "dates_with_observations": len(by_day),
            "dates_with_evaluated_signals": len(dates),
            "later_diagnostic_slice_dates": sorted(later_dates),
        },
        "coverage": {
            "raw_observations": len(loaded),
            "duplicate_observations_removed": len(loaded) - len(observations),
            "observations": len(observations),
            "grounded_candidates": len(candidates),
            "selected_signals": len(selected),
            "exclusions": dict(sorted(rejected.items())),
        },
        "diagnostics": _diagnostics(selected),
        "later_chronological_diagnostic_slice": _diagnostics(later_slice),
        "signals": selected,
        "invalidates": {
            "artifact_kind": "scanner_snapshot_option_proxy_replay",
            "reason": (
                "Cross-scanner spot snapshots are not a market price path, and a fixed-delta "
                "premium proxy cannot establish historical option fills or P&L."
            ),
        },
        "caveats": [
            "This is a signal diagnostic over historical one-minute underlying bars, not an options backtest.",
            "Entry uses the next one-minute bar open after the signal to prevent same-bar look-ahead.",
            "Signals whose target and invalidation both touch within one bar are excluded because event order is unknowable.",
            "No option contract, NBBO, spread, IV, Greeks, volume, open interest, fee, fill, or dollar P&L is inferred.",
            "Only setup/ticker/time/cooldown and non-P&L portfolio concurrency limits are replayed; runtime freshness, evidence, contract-liquidity, and daily-loss gates are not reproducible.",
            "The later chronological slice is descriptive, not out-of-sample: the current policy was not demonstrably frozen before the capture period.",
            "A performance backtest requires point-in-time option quotes/trades overlapping the signal dates and immutable pre-registered policy/configuration.",
            "Results are research evidence for the local paper portfolio only and cannot authorize broker orders.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay Autopilot signals against actual underlying 1-minute bars.")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--underlying-bars-db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run_backtest(
        args.root,
        args.start,
        args.end,
        load_config(args.config),
        args.underlying_bars_db,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {key: report[key] for key in ("period", "coverage", "diagnostics", "claims", "caveats")},
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
