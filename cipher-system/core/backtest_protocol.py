"""Reproducible experiment protocol for Cipher's bar backtests.

This module does not choose strategies or place orders.  It makes the evidence
contract around a simulation explicit: parameters are hashed before execution,
data is split chronologically with an embargo, costs cannot be omitted, and
uncertainty is reported without pretending a small sample is conclusive.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
from datetime import datetime, timezone
from typing import Iterable


SCHEMA_VERSION = "cipher-backtest-protocol-v2"
ENGINE_RULES = {
    "signal_evaluation": "confirmed_bar_close",
    "entry_fill": "next_bar_open",
    "same_bar_stop_target": "stop_first",
    "position_overlap": "one_position_per_symbol",
    "cost_application": "per_side_on_entry_and_exit",
    "research_only": True,
    "live_order_authority": False,
}


def _canonical(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def experiment_spec(
    *,
    mode: str,
    symbols: list[str],
    timeframe: str,
    years: float,
    detector_mode: str,
    lookback_bars: int,
    entry_every: int,
    control_repeats: int,
    stop_atr: float,
    target_atr: float,
    max_hold_bars: int,
    slippage_bps_per_side: float | None,
    commission_bps_per_side: float | None,
    holdout_fraction: float,
    embargo_bars: int,
    seed: int,
    max_concurrent_positions: int = 4,
    position_fraction: float = 0.10,
    bootstrap_block_length: int = 5,
) -> dict:
    """Return the immutable, hashable experiment definition.

    Costs are deliberately required here.  The low-level simulator retains a
    fallback for old scripts, but any result launched from the product must say
    what it charged.
    """
    if slippage_bps_per_side is None or commission_bps_per_side is None:
        raise ValueError("explicit slippage and commission costs are required")
    if slippage_bps_per_side < 0 or commission_bps_per_side < 0:
        raise ValueError("cost inputs cannot be negative")
    if not 0.1 <= holdout_fraction <= 0.5:
        raise ValueError("holdout_fraction must be between 0.10 and 0.50")
    if embargo_bars < 1:
        raise ValueError("embargo_bars must be at least 1")
    if mode not in {"filter", "standalone"}:
        raise ValueError("mode must be filter or standalone")
    if max_concurrent_positions < 1:
        raise ValueError("max_concurrent_positions must be positive")
    if not 0 < position_fraction <= 1:
        raise ValueError("position_fraction must be in (0, 1]")
    if bootstrap_block_length < 2:
        raise ValueError("bootstrap_block_length must be at least 2")
    total_cost = float(slippage_bps_per_side) + float(commission_bps_per_side)
    return {
        "schema_version": SCHEMA_VERSION,
        "strategy": "obsidian_signal_detector",
        "mode": mode,
        "symbols": sorted(set(symbols)),
        "timeframe": timeframe,
        "requested_years": float(years),
        "detector": {"mode": detector_mode},
        "parameters": {
            "lookback_bars": int(lookback_bars),
            "entry_every": int(entry_every),
            "stop_atr": float(stop_atr),
            "target_atr": float(target_atr),
            "max_hold_bars": int(max_hold_bars),
        },
        "cost_model": {
            "source": "explicit_user_or_product_default",
            "slippage_bps_per_side": float(slippage_bps_per_side),
            "commission_bps_per_side": float(commission_bps_per_side),
            "total_bps_per_side": total_cost,
            "round_trip_bps": total_cost * 2.0,
        },
        "portfolio": {
            "starting_equity": 100000.0,
            "max_concurrent_positions": int(max_concurrent_positions),
            "position_fraction": float(position_fraction),
            "sizing": "fixed_fraction_of_current_equity",
        },
        "validation": {
            "method": "chronological_locked_holdout",
            "holdout_fraction": float(holdout_fraction),
            "embargo_bars": int(embargo_bars),
            "minimum_bars_per_partition": 120,
            "control": "matched_random_entry",
            "control_repeats": int(control_repeats),
            "seed": int(seed),
            "uncertainty": {
                "iid_method": "percentile_bootstrap_mean",
                "serial_method": "circular_moving_block_bootstrap_mean",
                "block_length_trades": int(bootstrap_block_length),
                "repeats": 1000,
            },
        },
        "engine_rules": ENGINE_RULES,
    }


def parameter_lock_hash(spec: dict) -> str:
    """Stable hash; timestamps and fetched data never enter the parameter lock."""
    return hashlib.sha256(_canonical(spec).encode("utf-8")).hexdigest()


def _time(row: dict) -> str:
    return str(row.get("time") or row.get("timestamp") or "")


def _bars_fingerprint(rows: list[dict]) -> str:
    return hashlib.sha256(_canonical({"bars": rows}).encode("utf-8")).hexdigest()


def split_bars(
    bars_by_symbol: dict[str, list[dict]],
    *,
    holdout_fraction: float,
    embargo_bars: int = 1,
    minimum_bars: int = 120,
) -> tuple[dict[str, list[dict]], dict[str, list[dict]], dict]:
    """Chronologically split each symbol; boundary bars are purged as embargo."""
    train: dict[str, list[dict]] = {}
    test: dict[str, list[dict]] = {}
    coverage: dict[str, dict] = {}
    for symbol, source in sorted(bars_by_symbol.items()):
        bars = sorted(source, key=_time)
        n = len(bars)
        cut = int(n * (1.0 - holdout_fraction))
        train_end = max(0, cut - embargo_bars)
        test_start = min(n, cut + embargo_bars)
        tr, te = bars[:train_end], bars[test_start:]
        eligible = len(tr) >= minimum_bars and len(te) >= minimum_bars
        if eligible:
            train[symbol] = tr
            test[symbol] = te
        coverage[symbol] = {
            "all": {"bars": n, "first": _time(bars[0]) if bars else None,
                    "last": _time(bars[-1]) if bars else None,
                    "sha256": _bars_fingerprint(bars)},
            "train": {"bars": len(tr), "first": _time(tr[0]) if tr else None,
                      "last": _time(tr[-1]) if tr else None},
            "holdout": {"bars": len(te), "first": _time(te[0]) if te else None,
                        "last": _time(te[-1]) if te else None},
            "embargo_bars": min(n, test_start) - train_end,
            "eligible": eligible,
            "blocker": None if eligible else (
                f"needs at least {minimum_bars} bars in both train and holdout"
            ),
        }
    return train, test, coverage


def bootstrap_mean_interval(
    returns: Iterable[float], *, seed: int, repeats: int = 1000
) -> dict:
    """Deterministic percentile bootstrap for mean per-trade return."""
    values = [float(v) for v in returns]
    if len(values) < 10:
        return {
            "method": "bootstrap_mean_95pct",
            "n": len(values),
            "interval": None,
            "blocker": "at least 10 trades required",
        }
    rng = random.Random(seed)
    means = []
    for _ in range(repeats):
        means.append(sum(rng.choice(values) for _ in values) / len(values))
    means.sort()
    lo = means[math.floor(0.025 * (repeats - 1))]
    hi = means[math.ceil(0.975 * (repeats - 1))]
    return {
        "method": "bootstrap_mean_95pct",
        "n": len(values),
        "repeats": repeats,
        "seed": seed,
        "interval": [round(lo, 4), round(hi, 4)],
        "contains_zero": lo <= 0 <= hi,
        "blocker": None,
    }


def moving_block_bootstrap_mean_interval(
    returns: Iterable[float], *, seed: int, block_length: int = 5,
    repeats: int = 1000,
) -> dict:
    """Deterministic circular moving-block interval for serial trade outcomes.

    Consecutive returns are sampled in blocks so short regime clusters are not
    erased as they are under independent trade resampling.  The method remains
    descriptive: it does not make a sparse or non-stationary history conclusive.
    """
    values = [float(value) for value in returns]
    if block_length < 2:
        raise ValueError("block_length must be at least 2")
    if repeats < 100:
        raise ValueError("repeats must be at least 100")
    if len(values) < 10:
        return {
            "method": "circular_moving_block_bootstrap_mean_95pct",
            "n": len(values), "block_length": block_length,
            "interval": None, "blocker": "at least 10 trades required",
        }
    if block_length > len(values):
        raise ValueError("block_length cannot exceed the number of trades")
    rng = random.Random(seed)
    means: list[float] = []
    n = len(values)
    for _ in range(repeats):
        sample: list[float] = []
        while len(sample) < n:
            start = rng.randrange(n)
            sample.extend(values[(start + offset) % n] for offset in range(block_length))
        sample = sample[:n]
        means.append(sum(sample) / n)
    means.sort()
    lo = means[math.floor(0.025 * (repeats - 1))]
    hi = means[math.ceil(0.975 * (repeats - 1))]
    return {
        "method": "circular_moving_block_bootstrap_mean_95pct",
        "n": n, "repeats": repeats, "seed": seed,
        "block_length": block_length,
        "interval": [round(lo, 4), round(hi, 4)],
        "contains_zero": lo <= 0 <= hi,
        "blocker": None,
    }


def portfolio_summary(
    trades: Iterable,
    *,
    starting_equity: float = 100000.0,
    max_concurrent_positions: int = 4,
    position_fraction: float = 0.10,
) -> dict:
    """Apply deterministic cross-symbol concurrency and sizing constraints."""
    rows = sorted(list(trades), key=lambda t: (str(t.entry_time), str(t.symbol)))
    active_exits: list[str] = []
    equity = float(starting_equity)
    taken = skipped = 0
    peak, max_drawdown = equity, 0.0
    for trade in rows:
        entry_time = str(trade.entry_time)
        active_exits = [value for value in active_exits if value > entry_time]
        if len(active_exits) >= max_concurrent_positions:
            skipped += 1
            continue
        taken += 1
        active_exits.append(str(trade.exit_time or trade.entry_time))
        equity *= 1.0 + (float(trade.return_pct) / 100.0) * position_fraction
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, (equity - peak) / peak * 100.0)
    return {
        "starting_equity": round(starting_equity, 2),
        "ending_equity": round(equity, 2),
        "profit_loss": round(equity - starting_equity, 2),
        "return_pct": round((equity / starting_equity - 1.0) * 100.0, 3),
        "max_drawdown_pct": round(max_drawdown, 3),
        "trades_taken": taken,
        "trades_skipped_at_capacity": skipped,
        "max_concurrent_positions": max_concurrent_positions,
        "position_fraction": position_fraction,
        "note": "Equal fixed-fraction simulation; not a broker fill or account statement.",
    }


def build_manifest(spec: dict, coverage: dict) -> dict:
    eligible = sorted(k for k, v in coverage.items() if v.get("eligible"))
    blocked = sorted(k for k, v in coverage.items() if not v.get("eligible"))
    data_fingerprint = hashlib.sha256(_canonical({
        symbol: value.get("all", {}).get("sha256")
        for symbol, value in sorted(coverage.items())
    }).encode("utf-8")).hexdigest()
    experiment_id = parameter_lock_hash(spec)
    run_id = hashlib.sha256(f"{experiment_id}:{data_fingerprint}".encode("utf-8")).hexdigest()
    return {
        "schema_version": SCHEMA_VERSION,
        "experiment_id": experiment_id,
        "run_id": run_id,
        "data_fingerprint": data_fingerprint,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "spec": spec,
        "data_coverage": coverage,
        "validation_eligible_symbols": eligible,
        "blocked_symbols": blocked,
        "validation_status": "eligible" if eligible else "insufficient_holdout",
        "promotion_authority": "RESEARCH_ONLY",
        "live_order_authority": False,
    }
