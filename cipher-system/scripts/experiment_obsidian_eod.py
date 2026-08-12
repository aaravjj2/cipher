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
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
for path in (str(ROOT), str(ROOT / "core"), str(REPO)):
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
    backtest_symbol,
    load_rows,
)
from core.equity_history_download import EquityBarStore  # noqa: E402

UTC = timezone.utc
DEFAULT_DB = Path("runtime/data/historical_equities/obsidian_pine_ytd_2026/equity_bars.sqlite")
DEFAULT_OUT = Path("runtime/data/backtests/obsidian_eod_optimization_2026.json")


def _rth_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        local = _et(str(row.get("timestamp", row.get("time"))))
        if (local.hour, local.minute) < (9, 30) or (local.hour, local.minute) >= (16, 0):
            continue
        out.append(row)
    return out


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
    for (_day, _bucket), bucket in sorted(groups.items()):
        first = bucket[0]
        out.append({
            "timestamp": str(first.get("timestamp", first.get("time"))),
            "open": float(bucket[0]["open"]),
            "high": max(float(x["high"]) for x in bucket),
            "low": min(float(x["low"]) for x in bucket),
            "close": float(bucket[-1]["close"]),
            "volume": sum(float(x["volume"] or 0.0) for x in bucket),
        })
    return out


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
            detector_params={
                **PINE_DEFAULTS,
                "arm_minutes": int(params["arm_minutes"]),
                "sig_mult": float(params["sig_mult"]),
                "clps_thresh": float(params["clps_thresh"]),
            },
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
    # Breadth and worst-fold performance precede pooled averages. This avoids
    # selecting a candidate carried by one volatile ticker or one regime.
    fold_rows = row["folds"]
    avg_values = [f["avg_trade_return_pct"] for f in fold_rows if f.get("avg_trade_return_pct") is not None]
    pf_values = [f["profit_factor"] for f in fold_rows if f.get("profit_factor") is not None]
    breadth = sum(f.get("positive_symbols", 0) >= 6 for f in fold_rows)
    return (
        breadth,
        min(avg_values) if avg_values else -999.0,
        min(pf_values) if pf_values else -999.0,
        sum(avg_values) / len(avg_values) if avg_values else -999.0,
        row.get("pooled_avg_trade_return_pct") or -999.0,
    )


def _run_family(
    family: str,
    data: dict[str, list[dict[str, Any]]],
    grid: list[dict[str, Any]],
    *,
    holdout_start: date,
    holdout_end: date,
    bar_minutes: int,
    eod_exit_minute: int,
    precomputed: dict[str, tuple[list[dict[str, Any]], list[obsidian_eod.BarState]]] | None = None,
) -> dict[str, Any]:
    candidates = []
    state_cache: dict[tuple[int, float, float], dict[str, tuple[list[dict[str, Any]], list[obsidian_eod.BarState]]]] = {}
    for number, params in enumerate(grid, start=1):
        fold_metrics = []
        total_train_trades = 0
        detector_params = {
            **PINE_DEFAULTS,
            "arm_minutes": int(params["arm_minutes"]),
            "sig_mult": float(params["sig_mult"]),
            "clps_thresh": float(params["clps_thresh"]),
        }
        # Indicator state is invariant across chronological fold slicing for a
        # given candidate. Compute it once instead of three times per candidate.
        detector_key = (
            int(params["arm_minutes"]),
            float(params["sig_mult"]),
            float(params["clps_thresh"]),
        )
        if precomputed is not None:
            precomputed_candidate = precomputed
        else:
            precomputed_candidate = state_cache.get(detector_key)
            if precomputed_candidate is None:
                precomputed_candidate = {
                    symbol: (bars, obsidian_eod.compute(bars, detector_params)[0])
                    for symbol, bars in data.items()
                }
                state_cache[detector_key] = precomputed_candidate
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
        if total_train_trades < 100:
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
            "eligible_breadth": all(f.get("positive_symbols", 0) >= 5 for f in fold_metrics),
        }
        candidate["selection_eligible"] = (
            all(int(f.get("trades", 0)) >= 20 for f in fold_metrics)
            and candidate["eligible_breadth"]
            and sum(float(v) > 0 for v in avg_returns) >= 2
        )
        candidates.append(candidate)
        if number % 10 == 0:
            print(f"{family}: evaluated {number}/{len(grid)}")
    eligible = [row for row in candidates if row.get("selection_eligible")]
    # Prefer candidates that are positive in all development folds. If that
    # strict set is empty, retain the explicit two-of-three fallback rather than
    # quietly calling a losing configuration "best".
    strict = [row for row in eligible if row.get("eligible_all_folds_positive")]
    ranked = strict or eligible
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
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--holdout-start", default="2026-06-01")
    parser.add_argument("--holdout-end", default="2026-08-11")
    args = parser.parse_args()
    holdout_start = date.fromisoformat(args.holdout_start)
    holdout_end = date.fromisoformat(args.holdout_end)

    store = EquityBarStore(args.db.parent, db_path=args.db)
    data_1m = {symbol: _rth(load_rows(store, symbol, "1Min")) for symbol in REQUIRED_SYMBOLS}
    data_5m = {symbol: resample_5m(rows) for symbol, rows in data_1m.items()}
    print("loaded 1m rows:", {s: len(v) for s, v in data_1m.items()})
    print("resampled 5m rows:", {s: len(v) for s, v in data_5m.items()})

    grid_1m = [
        {"arm_minutes": arm, "sig_mult": sig, "clps_thresh": clps,
         "entry_delay": delay, "min_signal_lead": max(4, delay + 2)}
        for arm in (20, 30, 40)
        for sig in (1.0, 1.1)
        for clps in (0.5, 0.6)
        for delay in (1, 2, 3)
    ]
    grid_5m = [
        {"arm_minutes": arm, "sig_mult": sig, "clps_thresh": clps,
         "entry_delay": delay, "min_signal_lead": max(10, (delay + 1) * 5)}
        for arm in (60, 90, 120)
        for sig in (1.0, 1.1)
        for clps in (0.5, 0.6)
        for delay in (1, 2)
    ]

    result = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "research_role": "exploratory_development_with_locked_temporal_holdout",
        "data": {
            "database": str(args.db),
            "symbols": list(REQUIRED_SYMBOLS),
            "holdout": {"start": args.holdout_start, "end": args.holdout_end},
            "train_folds": [[a.isoformat(), b.isoformat()] for a, b in _folds()],
            "warmup": "pre-2026 bars are loaded for indicators; no warmup trades are eligible",
        },
        "families": [],
    }
    result["families"].append(_run_family(
        "1m_final_30_primary", data_1m, grid_1m,
        holdout_start=holdout_start, holdout_end=holdout_end,
        bar_minutes=1, eod_exit_minute=59,
    ))
    result["families"].append(_run_family(
        "5m_longer_eod_secondary", data_5m, grid_5m,
        holdout_start=holdout_start, holdout_end=holdout_end,
        bar_minutes=5, eod_exit_minute=55,
    ))
    result["conclusion"] = [
        "Primary objective is the 1-minute final-30-minute family; the 5-minute family is secondary.",
        "Candidate selection uses development folds only. Holdout metrics are descriptive and were not used to choose parameters.",
        "This is not a final validation: the 2026 holdout is short and the detector remains a Python reconstruction of Pine.",
    ]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    for family in result["families"]:
        chosen = family.get("chosen_from_training") or {}
        print(f"{family['family']} chosen={chosen.get('params')} holdout={family.get('holdout')}")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
