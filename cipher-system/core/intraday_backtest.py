"""Intraday momentum strategies using 5-min and 1-min bars.

Strategies:
1. orb_15min — Opening Range Breakout (first 15 min range)
2. vwap_momentum — Price crosses above VWAP with volume surge
3. intraday_rsi_momentum — RSI(14) > 70 continuation on 5-min
4. momentum_ignition — Sudden volume spike + price breakout
5. pullback_to_vwap — Buy pullbacks to VWAP in uptrend

All strategies use intraday bars (5-min or 1-min).
Research only — no order execution.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, time as dtime
from typing import Callable

from price_backtest import PriceTrade, compute_price_metrics


def _utcnow():
    from datetime import timezone
    return datetime.now(timezone.utc).isoformat()


def _parse_bar_time(bar: dict) -> datetime | None:
    """Parse bar timestamp."""
    time_str = bar.get("time", "")
    try:
        # Handle various formats
        if "T" in time_str:
            return datetime.fromisoformat(time_str.replace("Z", "+00:00"))
        return datetime.fromisoformat(time_str)
    except (ValueError, AttributeError):
        return None


def _is_market_hours(dt: datetime) -> bool:
    """Check if timestamp is during regular market hours (9:30-16:00 ET)."""
    if dt is None:
        return False
    # Alpaca returns UTC, market hours are 13:30-20:00 UTC (EDT) or 14:30-21:00 UTC (EST)
    # Approximate: 13:30-20:00 UTC
    market_open = dtime(13, 30)
    market_close = dtime(20, 0)
    return market_open <= dt.time() <= market_close


def _group_by_day(bars: list[dict], *, regular_hours_only: bool = True) -> dict[str, list[dict]]:
    """Group bars by trading day.

    Args:
        bars: List of bar dicts with 'time' key
        regular_hours_only: If True, filter to regular market hours (9:30-16:00 ET / 13:30-20:00 UTC)
    """
    days = defaultdict(list)
    for bar in bars:
        dt = _parse_bar_time(bar)
        if dt:
            # Filter to regular market hours if requested
            if regular_hours_only and not _is_market_hours(dt):
                continue
            day_key = dt.strftime("%Y-%m-%d")
            days[day_key].append(bar)
    return dict(days)


def _calc_vwap(bars: list[dict]) -> list[float | None]:
    """Calculate VWAP for a series of bars."""
    vwap_values = []
    cum_vol = 0
    cum_pv = 0
    
    for bar in bars:
        price = (bar.get("high", 0) + bar.get("low", 0) + bar.get("close", 0)) / 3
        vol = bar.get("volume", 0)
        cum_vol += vol
        cum_pv += price * vol
        vwap_values.append(cum_pv / cum_vol if cum_vol > 0 else None)
    
    return vwap_values


def _calc_rsi(closes: list[float], period: int = 14) -> list[float | None]:
    """Calculate RSI for a series of closes."""
    if len(closes) < period + 1:
        return [None] * len(closes)
    
    rsi_values = [None] * period
    gains = []
    losses = []
    
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gains.append(max(0, change))
        losses.append(max(0, -change))
    
    # Initial averages
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    
    for i in range(period, len(closes)):
        if i == period:
            rs = avg_gain / avg_loss if avg_loss != 0 else 100
        else:
            avg_gain = (avg_gain * (period - 1) + gains[i - 1]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i - 1]) / period
            rs = avg_gain / avg_loss if avg_loss != 0 else 100
        
        rsi = 100 - (100 / (1 + rs))
        rsi_values.append(rsi)
    
    return rsi_values


# ── Intraday Strategies ───────────────────────────────────────────────────


def strategy_orb_15min(
    ticker: str, bars: list[dict], *, max_trades: int = 30
) -> list[PriceTrade]:
    """Opening Range Breakout — 15 minute range.
    
    Edge: Breakouts of the opening range tend to continue intraday.
    Academic: Toby Crabel (1990) "Opening Range Breakout"
    
    Rules:
    - Define range as high/low of first 15 min (3 x 5-min bars)
    - Buy breakout above range high
    - Stop at range low
    - Exit at close or 2x range target
    """
    days = _group_by_day(bars)
    trades = []
    
    for day, day_bars in sorted(days.items()):
        if len(day_bars) < 10:  # Need at least 50 min of data
            continue
        
        # First 3 bars = 15 min opening range
        opening_bars = day_bars[:3]
        or_high = max(b.get("high", 0) for b in opening_bars)
        or_low = min(b.get("low", 0) for b in opening_bars)
        or_range = or_high - or_low
        
        if or_range <= 0:
            continue
        
        # Look for breakout in remaining bars
        for i, bar in enumerate(day_bars[3:], start=3):
            close = bar.get("close", 0)
            high = bar.get("high", 0)
            
            # Breakout above opening range high
            if high > or_high and close > or_high:
                entry = close
                target = entry + or_range * 2  # 2x range target
                stop = or_low  # Stop at range low
                
                trade = PriceTrade(
                    strategy="orb_15min",
                    ticker=ticker,
                    direction="long",
                    entry_price=entry,
                    entry_day=day,
                    target=target,
                    stop=stop,
                    max_hold_days=400,  # Full intraday session in 1-min bars
                )
                
                # Signal/entry uses this bar's close; simulate strictly later bars.
                remaining_bars = day_bars[i + 1:]
                if remaining_bars:
                    trade.simulate(remaining_bars)
                else:
                    # Exit at last close
                    trade.exit_price = day_bars[-1].get("close", entry)
                    trade.exit_day = day
                    trade.exit_reason = "time_exit"
                    trade.pnl_pct = ((trade.exit_price - entry) / entry) * 100
                
                trades.append(trade)
                break  # One trade per day
        
        if len(trades) >= max_trades:
            break
    
    return trades


def strategy_vwap_momentum(
    ticker: str, bars: list[dict], *, max_trades: int = 30
) -> list[PriceTrade]:
    """VWAP Momentum — Price crosses above VWAP with volume.
    
    Edge: VWAP acts as institutional benchmark. Crosses signal momentum.
    
    Rules:
    - Price crosses above VWAP
    - Volume > 1.5x average
    - Target: 1% above entry
    - Stop: VWAP or 0.5% below entry
    """
    days = _group_by_day(bars)
    trades = []
    
    for day, day_bars in sorted(days.items()):
        if len(day_bars) < 20:
            continue
        
        closes = [b.get("close", 0) for b in day_bars]
        volumes = [b.get("volume", 0) for b in day_bars]
        vwap = _calc_vwap(day_bars)
        avg_vol = sum(volumes) / len(volumes) if volumes else 0
        
        for i in range(1, len(day_bars)):
            if vwap[i] is None or vwap[i - 1] is None:
                continue
            
            price = closes[i]
            prev_price = closes[i - 1]
            vol = volumes[i]
            
            # Cross above VWAP with volume
            if (prev_price <= vwap[i - 1] and price > vwap[i] and 
                vol > avg_vol * 1.5):
                
                entry = price
                target = entry * 1.01  # 1% target
                stop = min(vwap[i], entry * 0.995)  # VWAP or 0.5% stop
                
                trade = PriceTrade(
                    strategy="vwap_momentum",
                    ticker=ticker,
                    direction="long",
                    entry_price=entry,
                    entry_day=day,
                    target=target,
                    stop=stop,
                    max_hold_days=400,  # Full intraday session in 1-min bars
                )
                
                remaining_bars = day_bars[i + 1:]
                if remaining_bars:
                    trade.simulate(remaining_bars)
                else:
                    trade.exit_price = day_bars[-1].get("close", entry)
                    trade.exit_day = day
                    trade.exit_reason = "time_exit"
                    trade.pnl_pct = ((trade.exit_price - entry) / entry) * 100
                
                trades.append(trade)
                break  # One trade per day
        
        if len(trades) >= max_trades:
            break
    
    return trades


def strategy_intraday_rsi_momentum(
    ticker: str, bars: list[dict], *, rsi_threshold: float = 70, max_trades: int = 30
) -> list[PriceTrade]:
    """Intraday RSI Momentum — RSI(14) > 70 continuation.
    
    Edge: Strong momentum tends to continue intraday.
    
    Rules:
    - RSI(14) on 5-min bars > 70
    - Price > VWAP (uptrend confirmation)
    - Target: 0.75% above entry
    - Stop: 0.5% below entry
    """
    days = _group_by_day(bars)
    trades = []
    
    for day, day_bars in sorted(days.items()):
        if len(day_bars) < 30:
            continue
        
        closes = [b.get("close", 0) for b in day_bars]
        rsi = _calc_rsi(closes, 14)
        vwap = _calc_vwap(day_bars)
        
        for i in range(15, len(day_bars)):
            if rsi[i] is None or vwap[i] is None:
                continue
            
            price = closes[i]
            
            # RSI > threshold and price > VWAP
            if rsi[i] > rsi_threshold and price > vwap[i]:
                entry = price
                target = entry * 1.0075  # 0.75% target
                stop = entry * 0.995     # 0.5% stop
                
                trade = PriceTrade(
                    strategy="intraday_rsi_momentum",
                    ticker=ticker,
                    direction="long",
                    entry_price=entry,
                    entry_day=day,
                    target=target,
                    stop=stop,
                    max_hold_days=400,  # Full intraday session in 1-min bars
                )
                
                remaining_bars = day_bars[i + 1:]
                if remaining_bars:
                    trade.simulate(remaining_bars)
                else:
                    trade.exit_price = day_bars[-1].get("close", entry)
                    trade.exit_day = day
                    trade.exit_reason = "time_exit"
                    trade.pnl_pct = ((trade.exit_price - entry) / entry) * 100
                
                trades.append(trade)
                break  # One trade per day
        
        if len(trades) >= max_trades:
            break
    
    return trades


def strategy_momentum_ignition(
    ticker: str, bars: list[dict], *, vol_multiplier: float = 3.0, max_trades: int = 30
) -> list[PriceTrade]:
    """Momentum Ignition — Sudden volume spike with price breakout.
    
    Edge: Volume spikes often precede continued momentum.
    
    Rules:
    - Volume > 3x 20-bar average
    - Price up > 0.3% on the bar
    - Target: 1% above entry
    - Stop: 0.5% below entry or VWAP
    """
    days = _group_by_day(bars)
    trades = []
    
    for day, day_bars in sorted(days.items()):
        if len(day_bars) < 25:
            continue
        
        closes = [b.get("close", 0) for b in day_bars]
        volumes = [b.get("volume", 0) for b in day_bars]
        vwap = _calc_vwap(day_bars)
        
        for i in range(20, len(day_bars)):
            if vwap[i] is None:
                continue
            
            # 20-bar average volume
            avg_vol_20 = sum(volumes[i-20:i]) / 20
            if avg_vol_20 == 0:
                continue
            
            vol_ratio = volumes[i] / avg_vol_20
            price_change = (closes[i] - closes[i-1]) / closes[i-1] if closes[i-1] > 0 else 0
            
            # Volume spike + price up
            if vol_ratio > vol_multiplier and price_change > 0.003:
                entry = closes[i]
                target = entry * 1.01  # 1% target
                stop = max(vwap[i], entry * 0.995)  # VWAP or 0.5% stop
                
                trade = PriceTrade(
                    strategy="momentum_ignition",
                    ticker=ticker,
                    direction="long",
                    entry_price=entry,
                    entry_day=day,
                    target=target,
                    stop=stop,
                    max_hold_days=400,  # Full intraday session in 1-min bars
                )
                
                remaining_bars = day_bars[i + 1:]
                if remaining_bars:
                    trade.simulate(remaining_bars)
                else:
                    trade.exit_price = day_bars[-1].get("close", entry)
                    trade.exit_day = day
                    trade.exit_reason = "time_exit"
                    trade.pnl_pct = ((trade.exit_price - entry) / entry) * 100
                
                trades.append(trade)
                break  # One trade per day
        
        if len(trades) >= max_trades:
            break
    
    return trades


def strategy_pullback_to_vwap(
    ticker: str, bars: list[dict], *, max_trades: int = 30
) -> list[PriceTrade]:
    """Pullback to VWAP — Buy dips to VWAP in uptrend.
    
    Edge: VWAP acts as support in uptrends.
    
    Rules:
    - Price was above VWAP (uptrend)
    - Price pulls back to within 0.1% of VWAP
    - RSI(14) < 50 (oversold within uptrend)
    - Target: 0.75% above entry
    - Stop: 0.5% below VWAP
    """
    days = _group_by_day(bars)
    trades = []
    
    for day, day_bars in sorted(days.items()):
        if len(day_bars) < 30:
            continue
        
        closes = [b.get("close", 0) for b in day_bars]
        vwap = _calc_vwap(day_bars)
        rsi = _calc_rsi(closes, 14)
        
        was_above_vwap = False
        
        for i in range(15, len(day_bars)):
            if vwap[i] is None or rsi[i] is None:
                continue
            
            price = closes[i]
            
            # Track if price was above VWAP
            if price > vwap[i] * 1.002:  # 0.2% above
                was_above_vwap = True
            
            # Pullback to VWAP after being above
            if was_above_vwap:
                distance_to_vwap = abs(price - vwap[i]) / vwap[i]
                
                if distance_to_vwap < 0.001 and rsi[i] < 50:  # Within 0.1% of VWAP
                    entry = price
                    target = entry * 1.0075  # 0.75% target
                    stop = vwap[i] * 0.995   # 0.5% below VWAP
                    
                    trade = PriceTrade(
                        strategy="pullback_to_vwap",
                        ticker=ticker,
                        direction="long",
                        entry_price=entry,
                        entry_day=day,
                        target=target,
                        stop=stop,
                        max_hold_days=400,  # Full intraday session in 1-min bars
                    )
                    
                    remaining_bars = day_bars[i + 1:]
                    if remaining_bars:
                        trade.simulate(remaining_bars)
                    else:
                        trade.exit_price = day_bars[-1].get("close", entry)
                        trade.exit_day = day
                        trade.exit_reason = "time_exit"
                        trade.pnl_pct = ((trade.exit_price - entry) / entry) * 100
                    
                    trades.append(trade)
                    break  # One trade per day
        
        if len(trades) >= max_trades:
            break
    
    return trades


# ── Strategy Registry ─────────────────────────────────────────────────────

INTRADAY_STRATEGIES = {
    "orb_15min": strategy_orb_15min,
    "vwap_momentum": strategy_vwap_momentum,
    "intraday_rsi_momentum": strategy_intraday_rsi_momentum,
    "momentum_ignition": strategy_momentum_ignition,
    "pullback_to_vwap": strategy_pullback_to_vwap,
}


# ── Main Backtest Runner ──────────────────────────────────────────────────

def run_intraday_backtest(
    bars_fn: Callable,
    tickers: list[str],
    *,
    strategies: list[str] | None = None,
    bars_limit: int = 5000,
    start_date: str | None = None,
) -> dict:
    """Run intraday strategy backtest.
    
    Args:
        bars_fn: Function to fetch bars (ticker, timeframe, limit) -> {"bars": [...]}
        tickers: List of ticker symbols
        strategies: List of strategy names (default: all)
        bars_limit: Max bars to fetch per ticker
        start_date: Filter trades to entries on/after this date (YYYY-MM-DD)
    
    Returns:
        Dict with results, ranking, and metadata
    """
    selected = strategies or list(INTRADAY_STRATEGIES.keys())
    all_trades: dict[str, list[PriceTrade]] = defaultdict(list)
    errors = []

    for ticker in tickers:
        try:
            # Fetch 5-min bars
            bar_payload = bars_fn(ticker, "5Min", bars_limit)
            bars = bar_payload.get("bars") or []
            
            if len(bars) < 100:
                continue

            for strat_name in selected:
                strat_fn = INTRADAY_STRATEGIES.get(strat_name)
                if not strat_fn:
                    continue

                try:
                    trades = strat_fn(ticker, bars)
                    # Filter trades by start_date
                    if start_date:
                        trades = [t for t in trades if t.entry_day >= start_date]
                    all_trades[strat_name].extend(trades)
                except Exception as exc:
                    errors.append({
                        "ticker": ticker,
                        "strategy": strat_name,
                        "error": str(exc),
                    })

        except Exception as exc:
            errors.append({"ticker": ticker, "error": str(exc)})

    # Compute metrics
    results = {}
    for strat_name, trades in all_trades.items():
        metrics = compute_price_metrics(trades)
        results[strat_name] = {
            "metrics": metrics,
            "trades": [t.to_dict() for t in trades],
        }

    # Rank by expectancy
    ranking = []
    for strat_name, data in results.items():
        m = data["metrics"]
        ranking.append({
            "strategy": strat_name,
            "n_trades": m.get("n_trades", 0),
            "win_rate": m.get("win_rate", 0),
            "avg_r": m.get("avg_r"),
            "sharpe": m.get("sharpe"),
            "profit_factor": m.get("profit_factor"),
            "expectancy_pct": m.get("expectancy_pct", 0),
            "metrics": m,
        })
    
    ranking.sort(key=lambda x: x.get("expectancy_pct", 0) or 0, reverse=True)
    best = ranking[0]["strategy"] if ranking else None

    return {
        "as_of": _utcnow(),
        "mode": "intraday_momentum",
        "tickers_tested": len(tickers),
        "strategies_tested": len(selected),
        "ranking": ranking,
        "best_strategy": best,
        "results": results,
        "errors": errors[:20],
    }
