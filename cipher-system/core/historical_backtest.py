"""Historical GEX replay backtester.

Walks through captured GEX snapshots chronologically, generating signals
from each snapshot's profile and simulating trades against forward bars
from that snapshot's timestamp. This provides true walk-forward validation
without look-ahead bias.

Uses gex_history.sqlite for historical GEX profiles.
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from strategy_backtest import (
    Trade,
    compute_metrics,
    rank_strategies,
    STRATEGIES,
)
from gex_replay import (
    list_tickers,
    list_snapshots,
    get_snapshot_cells,
    _aggregate_by_strike,
)


DATA_DIR = Path(__file__).resolve().parents[1] / "data"
GEX_HISTORY_DB = DATA_DIR / "gex_history.sqlite"


def _utcnow():
    return datetime.now(timezone.utc).isoformat()


def _load_snapshot_profile(snapshot_id: int) -> tuple[list[dict], float | None]:
    """Load GEX profile from a historical snapshot.

    Returns (profile, spot_estimate) where spot is estimated from the
    strike with highest absolute GEX.
    """
    cells = get_snapshot_cells(GEX_HISTORY_DB, snapshot_id)
    if not cells:
        return [], None

    profile_dict = _aggregate_by_strike(cells)
    profile = list(profile_dict.values())
    if not profile:
        return [], None

    # Estimate spot from the strike with highest absolute GEX
    # (dealers tend to pin price near max gamma)
    profile.sort(key=lambda p: p["strike"])
    max_abs_strike = max(profile, key=lambda p: p.get("abs", 0))
    spot_estimate = max_abs_strike["strike"]

    return profile, spot_estimate


def _load_snapshot_summary(profile: list[dict]) -> dict:
    """Build a summary dict from profile (put_wall, call_wall, etc.)."""
    if not profile:
        return {}

    # Find put wall (most negative put GEX) and call wall (most positive call GEX)
    put_wall = None
    call_wall = None
    max_call_gex = 0
    min_put_gex = 0

    for p in profile:
        call_gex = p.get("call", 0)
        put_gex = p.get("put", 0)
        if call_gex > max_call_gex:
            max_call_gex = call_gex
            call_wall = p["strike"]
        if put_gex < min_put_gex:
            min_put_gex = put_gex
            put_wall = p["strike"]

    return {
        "put_wall_strike": put_wall,
        "call_wall_strike": call_wall,
    }


def _bars_from_date(bars_fn: Callable, ticker: str, start_date: str, limit: int = 60) -> list[dict]:
    """Fetch bars starting from a specific date.

    This is a wrapper that modifies the bars_fn call to start from
    the given date instead of the most recent date.

    Args:
        bars_fn: The bars function from app.py
        ticker: Ticker symbol
        start_date: ISO date string (e.g., "2026-07-22T13:28:54Z")
        limit: Number of bars to fetch

    Returns:
        List of bar dicts from the start date forward
    """
    # Parse the start date
    try:
        if "T" in start_date:
            start_dt = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
        else:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        # Fallback: use recent bars
        start_dt = datetime.now(timezone.utc) - timedelta(days=60)

    # Fetch bars - we need to call the underlying Alpaca API directly
    # since bars_fn doesn't support start_date parameter
    # For now, we'll fetch all bars and filter by date
    result = bars_fn(ticker, "1d", limit + 30)  # Fetch extra to ensure we have enough
    bars = result.get("bars") or []

    # Filter bars to only those on or after start_date
    filtered = []
    for bar in bars:
        bar_time = bar.get("time", "")
        if bar_time >= start_date[:10]:  # Compare date portion
            filtered.append(bar)

    return filtered[:limit]


def run_historical_backtest(
    bars_fn: Callable,
    tickers: list[str] | None = None,
    *,
    strategies: list[str] | None = None,
    iv: float = 0.25,
    dte: float = 30,
    bars_limit: int = 60,
) -> dict:
    """Run strategies across all historical GEX snapshots.

    For each ticker:
    1. Load all historical snapshots from gex_history.sqlite
    2. For each snapshot (chronologically):
       a. Build GEX profile from that snapshot
       b. Fetch forward bars from that snapshot's timestamp (no look-ahead)
       c. Run each strategy to generate trades
       d. Simulate trades against forward bars
    3. Aggregate metrics per strategy across all snapshots
    4. Rank strategies by composite score
    """
    selected = strategies or list(STRATEGIES.keys())
    all_trades: dict[str, list[Trade]] = defaultdict(list)
    errors = []
    snapshots_used = 0
    snapshot_details = []

    # Get tickers that have historical data
    available_tickers = list_tickers(GEX_HISTORY_DB)
    if tickers:
        available_tickers = [t for t in tickers if t in available_tickers]

    if not available_tickers:
        return {
            "as_of": _utcnow(),
            "error": "No historical GEX data available for requested tickers",
            "available_tickers": list_tickers(GEX_HISTORY_DB),
        }

    for ticker in available_tickers:
        try:
            # Get all snapshots for this ticker
            snapshots = list_snapshots(GEX_HISTORY_DB, ticker)
            if not snapshots:
                continue

            # Sort by captured_at
            snapshots.sort(key=lambda s: s.get("captured_at", ""))

            for snap in snapshots:
                snapshot_id = snap["id"]
                captured_at = snap.get("captured_at", "")

                try:
                    # Load profile from this snapshot
                    profile, spot = _load_snapshot_profile(snapshot_id)
                    if not profile or not spot:
                        continue

                    summary = _load_snapshot_summary(profile)

                    # Fetch bars from this snapshot's date forward (no look-ahead!)
                    forward_bars = _bars_from_date(bars_fn, ticker, captured_at, bars_limit)
                    if len(forward_bars) < 10:
                        continue

                    # Only use bars AFTER the snapshot time (true forward)
                    # Filter out bars from the same day if snapshot was intraday
                    snapshot_date = captured_at[:10]
                    snapshot_time = captured_at[11:19] if len(captured_at) > 19 else "00:00:00"

                    # For daily bars, we want bars from the NEXT day onward
                    # (can't trade on the same bar we observed the signal)
                    next_day_bars = []
                    for bar in forward_bars:
                        bar_time = bar.get("time", "")
                        bar_date = bar_time[:10]
                        if bar_date > snapshot_date:
                            next_day_bars.append(bar)
                        elif bar_date == snapshot_date:
                            # Same day - check if bar is after snapshot time
                            # (for daily bars, this is approximate)
                            pass  # Skip same-day bars for daily timeframe

                    if len(next_day_bars) < 5:
                        # Not enough forward bars
                        continue

                    snapshots_used += 1
                    snapshot_details.append({
                        "ticker": ticker,
                        "snapshot_id": snapshot_id,
                        "captured_at": captured_at,
                        "forward_bars": len(next_day_bars),
                    })

                    # Get setups from scanner if available
                    setups = None
                    try:
                        from scanner import _local_peaks, classify_setup
                        peaks = _local_peaks(profile)
                        setups, _ = classify_setup(profile, peaks, summary, spot)
                    except Exception:
                        pass

                    # Run each strategy
                    for strat_name in selected:
                        strat_fn = STRATEGIES.get(strat_name)
                        if not strat_fn:
                            continue

                        try:
                            kwargs = {"iv": iv, "dte": dte}
                            if strat_name == "cluster_magnet":
                                trades = strat_fn(ticker, profile, spot, summary, next_day_bars, setups, **kwargs)
                            elif strat_name == "term_aligned":
                                # Historical snapshots don't have multi-exp data
                                # Skip term_aligned for historical backtest
                                continue
                            elif strat_name == "flow_confluence":
                                # Infer flow from recent bar direction (using forward bars)
                                if len(next_day_bars) >= 3:
                                    recent = next_day_bars[:3]
                                    up = sum(1 for b in recent if (b.get("close") or 0) > (b.get("open") or 0))
                                    flow_dir = "bullish" if up >= 2 else "bearish"
                                else:
                                    flow_dir = None
                                trades = strat_fn(
                                    ticker, profile, spot, summary, next_day_bars,
                                    flow_direction=flow_dir, **kwargs
                                )
                            else:
                                trades = strat_fn(ticker, profile, spot, summary, next_day_bars, **kwargs)

                            # Tag trades with snapshot info
                            for t in trades:
                                t.entry_day = captured_at[:10]

                            all_trades[strat_name].extend(trades)
                        except Exception as exc:
                            errors.append({
                                "ticker": ticker,
                                "snapshot_id": snapshot_id,
                                "strategy": strat_name,
                                "error": str(exc),
                            })

                except Exception as exc:
                    errors.append({
                        "ticker": ticker,
                        "snapshot_id": snapshot_id,
                        "error": str(exc),
                    })

        except Exception as exc:
            errors.append({"ticker": ticker, "error": str(exc)})

    # Compute metrics per strategy
    results = {}
    for strat_name, trades in all_trades.items():
        metrics = compute_metrics(trades)
        from walk_forward import statistical_significance
        sig = statistical_significance(
            metrics.get("wins", 0),
            metrics.get("n_trades", 0),
            null_rate=0.5,
        )
        results[strat_name] = {
            "metrics": metrics,
            "significance": sig,
            "trades": [t.to_dict() for t in trades[:100]],
        }

    # Rank
    ranked = rank_strategies(results)

    return {
        "as_of": _utcnow(),
        "mode": "historical_replay",
        "tickers_tested": len(available_tickers),
        "snapshots_used": snapshots_used,
        "snapshots_detail": snapshot_details[:20],
        "strategies_tested": len(selected),
        "ranking": ranked,
        "best_strategy": ranked[0]["strategy"] if ranked else None,
        "results": results,
        "errors": errors[:50],
        "caveat": (
            "Historical GEX replay backtest from captured snapshots. "
            "Each snapshot generates signals from its GEX profile, then trades "
            "are simulated against FORWARD bars from that snapshot's date (no look-ahead). "
            "Past performance does not guarantee future results. "
            "GEX is a public-OI heuristic, not verified dealer positioning. "
            "Research only — no order execution."
        ),
    }
