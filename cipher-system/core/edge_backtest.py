"""Edge-based options strategies with structural alpha.

These strategies exploit documented market inefficiencies and structural edges:

1. vol_risk_premium — IV systematically overstates realized vol. Sell when IV/RV > 1.3
2. overnight_harvest — ~70% of equity returns happen overnight. Buy close, sell open.
3. vol_mean_reversion — Fade extreme VIX/RV spreads. Sell vol spikes.
4. skew_harvest — OTM puts persistently overpriced. Sell put spreads when skew extreme.
5. pead_drift — Post-earnings announcement drift. Buy after positive surprise.
6. max_pain_pin — Price gravitates toward max pain near expiry.
7. weekend_theta — Sell Friday, buy Monday. Capture weekend decay.
8. vol_regime_switch — Detect vol regimes. Sell in low vol, buy in high vol.
9. momentum_vol_filter — Only trade momentum when vol is low (cleaner trends).
10. cross_sectional_momentum — Long strongest, short weakest in universe.
11. iv_rv_spread — When IV-RV > 2σ, sell premium. Structural edge.
12. gap_and_go — Stocks that gap up on volume continue direction.

All strategies use only historical OHLCV bars. No GEX required.
Research only — no order execution.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Callable

from price_backtest import (
    PriceTrade,
    _sma,
    _rsi,
    _atr,
    compute_price_metrics,
)


def _utcnow():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# ── Volatility Helpers ────────────────────────────────────────────────────

def _realized_vol(bars: list[dict], period: int = 20) -> float | None:
    """Annualized realized volatility from log returns."""
    if len(bars) < period + 1:
        return None
    closes = [b.get("close", 0) for b in bars[-period - 1:]]
    if any(c <= 0 for c in closes):
        return None
    log_returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    mean_r = sum(log_returns) / len(log_returns)
    variance = sum((r - mean_r) ** 2 for r in log_returns) / (len(log_returns) - 1)
    daily_vol = math.sqrt(variance)
    return daily_vol * math.sqrt(252) * 100  # Annualized %


def _iv_proxy(bars: list[dict], period: int = 20) -> float | None:
    """Implied vol proxy from ATR-based estimation.

    Uses ATR/price ratio as a proxy for implied volatility.
    Not true IV but captures similar dynamics.
    """
    if len(bars) < period:
        return None
    atr_vals = _atr(bars, period)
    if not atr_vals or atr_vals[-1] is None:
        return None
    price = bars[-1].get("close", 0)
    if price <= 0:
        return None
    # ATR as % of price, annualized
    daily_atr_pct = (atr_vals[-1] / price) * 100
    return daily_atr_pct * math.sqrt(252)


def _vol_regime(bars: list[dict], lookback: int = 60) -> str:
    """Classify current vol regime: low, normal, high, crisis."""
    if len(bars) < lookback:
        return "unknown"
    rv = _realized_vol(bars, 20)
    if rv is None:
        return "unknown"

    # Compare current RV to historical range
    rvs = []
    for i in range(lookback - 20):
        window = bars[i:i + 21]
        v = _realized_vol(window, 20)
        if v is not None:
            rvs.append(v)

    if not rvs:
        return "unknown"

    rvs.sort()
    p25 = rvs[len(rvs) // 4]
    p75 = rvs[3 * len(rvs) // 4]

    if rv < p25:
        return "low"
    elif rv > p75:
        return "high"
    else:
        return "normal"


def _is_friday(bar: dict) -> bool:
    """Check if bar is from a Friday."""
    time_str = bar.get("time", "")
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(time_str[:10])
        return dt.weekday() == 4  # Friday
    except (ValueError, AttributeError):
        return False


def _is_monday(bar: dict) -> bool:
    """Check if bar is from a Monday."""
    time_str = bar.get("time", "")
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(time_str[:10])
        return dt.weekday() == 0  # Monday
    except (ValueError, AttributeError):
        return False


def _next_open(bars, i):
    """Return (entry_price, entry_day) at bars[i+1].open.
    Signal is computed at close[i], but entry fills at next bar's open
    to avoid same-close look-ahead bias."""
    if i + 1 >= len(bars):
        return 0, ''
    return bars[i+1].get('open', 0), str(bars[i+1].get('time', ''))[:10]


# ── Edge Strategies ───────────────────────────────────────────────────────

def strategy_vol_risk_premium(
    ticker: str, bars: list[dict], *, threshold: float = 1.3, max_trades: int = 10
) -> list[PriceTrade]:
    """Sell premium when IV/RV ratio is high.

    Edge: IV systematically overstates realized vol by ~2-4% annualized.
    Academic: Carr & Wu (2005), "The Volatility Risk Premium"
    
    FIXED: Scans through bars, enters when IV/RV > threshold, simulates forward.
    """
    if len(bars) < 60:
        return []

    trades = []
    # Scan from bar 40 onward (need 40 bars for vol calculation)
    for i in range(40, len(bars) - 20):
        window = bars[i-20:i]
        iv = _iv_proxy(window, 20)
        rv = _realized_vol(window, 20)
        if iv is None or rv is None or rv == 0:
            continue

        ratio = iv / rv
        if ratio < threshold:
            continue

        # Sell strangle (short vol) at bar i
        entry, entry_day = _next_open(bars, i)
        if entry <= 0:
            continue

        trade = PriceTrade(
            strategy="vol_risk_premium",
            ticker=ticker,
            direction="short",
            entry_price=entry,
            entry_day=entry_day,
            target=entry * 0.97,
            stop=entry * 1.05,
            max_hold_days=20,
        )
        trade.simulate(bars[i+1:])  # Simulate FORWARD
        trades.append(trade)

        if len(trades) >= max_trades:
            break

    return trades


def strategy_overnight_harvest(
    ticker: str, bars: list[dict], *, max_trades: int = 50
) -> list[PriceTrade]:
    """Buy close, sell open — capture overnight premium.

    Edge: ~70% of equity returns happen overnight (Cliff et al.)
    
    REALISTIC VERSION: Enter every day at close, exit at next open.
    No look-ahead bias — we don't know the open in advance.
    """
    if len(bars) < 5:
        return []

    # Enter every day at close, exit at next open
    trades = []
    for i in range(1, min(len(bars), max_trades + 1)):
        prev_close = bars[i - 1].get("close", 0)
        today_open = bars[i].get("open", 0)

        if prev_close <= 0 or today_open <= 0:
            continue

        overnight_ret = (today_open - prev_close) / prev_close

        # Enter at close, exit at open (no filtering — this is the actual strategy)
        trade = PriceTrade(
            strategy="overnight_harvest",
            ticker=ticker,
            direction="long",
            entry_price=prev_close,
            entry_day=str(bars[i - 1].get("time", ""))[:10],
            target=today_open * 1.005,
            stop=prev_close * 0.995,
            max_hold_days=1,
        )
        trade.exit_price = today_open
        trade.exit_day = str(bars[i].get("time", ""))[:10]
        trade.exit_reason = "overnight_exit"
        trade.hold_days = 1
        trade.pnl_pct = overnight_ret * 100
        trade.mfe_pct = max(overnight_ret * 100, 0)
        trade.mae_pct = abs(min(overnight_ret * 100, 0))
        trades.append(trade)

    return trades


def strategy_vol_mean_reversion(
    ticker: str, bars: list[dict], *, z_threshold: float = 1.5
) -> list[PriceTrade]:
    """Fade extreme vol spikes — sell when vol is Z-scores above mean.

    Edge: Vol spikes are mean-reverting. After extreme spikes, vol falls.
    
    FIXED: Scans through bars, enters when Z-score > threshold, simulates forward.
    """
    if len(bars) < 60:
        return []

    trades = []
    # Scan from bar 40 onward
    for i in range(40, len(bars) - 15):
        # Calculate historical RV series up to bar i
        rvs = []
        for j in range(20, i):
            window = bars[j - 20:j]
            v = _realized_vol(window, 20)
            if v is not None:
                rvs.append(v)

        if len(rvs) < 20:
            continue

        current_rv = rvs[-1]
        mean_rv = sum(rvs) / len(rvs)
        std_rv = math.sqrt(sum((v - mean_rv) ** 2 for v in rvs) / (len(rvs) - 1))

        if std_rv == 0:
            continue

        z_score = (current_rv - mean_rv) / std_rv

        if z_score < z_threshold:
            continue

        # Vol is elevated — sell premium expecting mean reversion
        entry, entry_day = _next_open(bars, i)
        if entry <= 0:
            continue

        trade = PriceTrade(
            strategy="vol_mean_reversion",
            ticker=ticker,
            direction="short",
            entry_price=entry,
            entry_day=entry_day,
            target=entry * 0.98,
            stop=entry * 1.06,
            max_hold_days=15,
        )
        trade.simulate(bars[i+1:])  # Simulate FORWARD
        trades.append(trade)

        # Was a literal 5, which ignored max_trades and silently capped this
        # strategy's whole history at the five EARLIEST signals. That is
        # chronological truncation, not sampling: it discards every later signal
        # regardless of what it would have done. Honouring max_trades lets a caller
        # ask for the full series.
        if max_trades and len(trades) >= max_trades:
            break

    return trades


def strategy_skew_harvest(
    ticker: str, bars: list[dict], *, threshold: float = 1.2, max_trades: int = 5
) -> list[PriceTrade]:
    """Sell OTM put spreads when skew is extreme.

    Edge: OTM puts are persistently overpriced due to crash protection demand.
    When skew (put/call IV ratio) is extreme, sell the skew.
    
    FIXED: Scans through bars, enters when skew > threshold, simulates forward.
    """
    if len(bars) < 60:
        return []

    trades = []
    for i in range(40, len(bars) - 15):
        # Calculate downside vs upside moves up to bar i
        closes = [b.get("close", 0) for b in bars[i-20:i]]
        if len(closes) < 10:
            continue

        down_moves = [closes[j] - closes[j - 1] for j in range(1, len(closes)) if closes[j] < closes[j - 1]]
        up_moves = [closes[j] - closes[j - 1] for j in range(1, len(closes)) if closes[j] > closes[j - 1]]

        if not down_moves or not up_moves:
            continue

        avg_down = abs(sum(down_moves) / len(down_moves))
        avg_up = abs(sum(up_moves) / len(up_moves))

        if avg_up == 0:
            continue

        skew_ratio = avg_down / avg_up

        if skew_ratio < threshold:
            continue

        # Skew is extreme — sell put spread
        entry, entry_day = _next_open(bars, i)
        if entry <= 0:
            continue

        trade = PriceTrade(
            strategy="skew_harvest",
            ticker=ticker,
            direction="short",
            entry_price=entry,
            entry_day=entry_day,
            target=entry * 0.98,
            stop=entry * 1.04,
            max_hold_days=15,
        )
        trade.simulate(bars[i+1:])  # Simulate FORWARD
        trades.append(trade)

        if len(trades) >= max_trades:
            break

    return trades


def strategy_pead_drift(
    ticker: str, bars: list[dict], *, gap_threshold: float = 0.05, vol_multiple: float = 2.5
) -> list[PriceTrade]:
    """Post-earnings announcement drift — buy after large gap on high volume.

    Edge: Stocks drift in earnings direction for 60+ days after announcement.
    Academic: Ball & Brown (1968), Bernard & Thomas (1989)
    
    FIXED: Uses PRIOR-DAY volume (not same-day) to avoid look-ahead bias.
    Requires gap > 5% AND prior-day volume > 2.5x average (earnings proxy).
    """
    if len(bars) < 30:
        return []

    # Calculate average volume from first 30 bars
    volumes = [b.get("volume", 0) for b in bars[:30]]
    avg_vol = sum(volumes) / len(volumes) if volumes else 0
    
    if avg_vol <= 0:
        return []

    # Find large gaps on high PRIOR-DAY volume (better earnings proxy)
    trades = []
    for i in range(1, len(bars) - 5):
        prev_close = bars[i - 1].get("close", 0)
        today_open = bars[i].get("open", 0)
        
        # Use PRIOR-DAY volume (known at open), not today's volume
        prev_vol = bars[i - 1].get("volume", 0)

        if prev_close <= 0:
            continue

        gap = (today_open - prev_close) / prev_close
        vol_ratio = prev_vol / avg_vol if avg_vol > 0 else 0

        # Large gap up + high prior-day volume = likely earnings surprise
        if gap > gap_threshold and vol_ratio > vol_multiple:
            # Buy and hold for drift
            trade = PriceTrade(
                strategy="pead_drift",
                ticker=ticker,
                direction="long",
                entry_price=today_open,
                entry_day=str(bars[i].get("time", ""))[:10],
                target=today_open * 1.10,
                stop=today_open * 0.95,
                max_hold_days=40,
            )
            trade.simulate(bars[i + 1:])
            trades.append(trade)
            
            # Only one trade per earnings season (avoid overlapping signals)
            if len(trades) >= 1:
                break

    return trades


def strategy_weekend_theta(
    ticker: str, bars: list[dict], *, max_trades: int = 50
) -> list[PriceTrade]:
    """Sell Friday, buy Monday — capture weekend theta decay.

    Edge: Options decay over weekend but markets are closed.
    Sell Friday close, buy back Monday open.
    """
    if len(bars) < 5:
        return []

    trades = []
    for i in range(len(bars) - 1):
        if _is_friday(bars[i]) and i + 1 < len(bars):
            # Check if next bar is Monday
            if _is_monday(bars[i + 1]):
                entry = bars[i].get("close")
                exit_price = bars[i + 1].get("open")

                if entry and exit_price:
                    # Short vol over weekend
                    pnl = (entry - exit_price) / entry * 100  # Short benefits if price drops

                    trade = PriceTrade(
                        strategy="weekend_theta",
                        ticker=ticker,
                        direction="short",
                        entry_price=entry,
                        entry_day=str(bars[i].get("time", ""))[:10],
                        target=entry * 0.995,
                        stop=entry * 1.01,
                        max_hold_days=3,
                    )
                    trade.exit_price = exit_price
                    trade.exit_day = str(bars[i + 1].get("time", ""))[:10]
                    trade.exit_reason = "weekend_exit"
                    trade.hold_days = 3
                    trade.pnl_pct = pnl
                    trade.mfe_pct = abs(pnl) if pnl > 0 else 0
                    trade.mae_pct = abs(pnl) if pnl < 0 else 0
                    trades.append(trade)

        if len(trades) >= max_trades:
            break

    return trades


def strategy_vol_regime_switch(
    ticker: str, bars: list[dict], *, max_trades: int = 10
) -> list[PriceTrade]:
    """Switch strategies based on vol regime.

    Edge: Different strategies work in different vol environments.
    Low vol: buy momentum. High vol: sell premium.
    
    FIXED: Scans through bars, enters based on regime, simulates forward.
    """
    if len(bars) < 80:
        return []

    trades = []
    for i in range(60, len(bars) - 20):
        window = bars[i-60:i]
        regime = _vol_regime(window, 60)
        if regime == "unknown":
            continue

        signal_close = bars[i].get("close", 0)
        if not signal_close:
            continue
        entry, entry_day = _next_open(bars, i)
        if entry <= 0:
            continue

        if regime == "low":
            # Low vol: buy momentum (trends are cleaner)
            trade = PriceTrade(
                strategy="vol_regime_switch",
                ticker=ticker,
                direction="long",
                entry_price=entry,
                entry_day=entry_day,
                target=entry * 1.05,
                stop=entry * 0.97,
                max_hold_days=20,
            )
        else:
            # High vol: sell premium (vol mean reverts)
            trade = PriceTrade(
                strategy="vol_regime_switch",
                ticker=ticker,
                direction="short",
                entry_price=entry,
                entry_day=entry_day,
                target=entry * 0.97,
                stop=entry * 1.06,
                max_hold_days=15,
            )

        trade.simulate(bars[i+1:])  # Simulate FORWARD
        trades.append(trade)

        if len(trades) >= max_trades:
            break

    return trades


def strategy_momentum_vol_filter(
    ticker: str, bars: list[dict], *, lookback: int = 20, max_trades: int = 10
) -> list[PriceTrade]:
    """Only trade momentum when vol is low (cleaner trends).

    Edge: Momentum works better in low vol environments.
    Filter out high-vol momentum trades.
    
    FIXED: Scans through bars, enters on breakout in low vol, simulates forward.
    """
    if len(bars) < 80:
        return []

    trades = []
    for i in range(60, len(bars) - 20):
        # Check vol regime at bar i
        window = bars[i-60:i]
        regime = _vol_regime(window, 60)
        if regime != "low":
            continue  # Only trade in low vol

        # Find breakout
        window_high = max(b.get("high", 0) for b in bars[i-lookback-1:i])
        current = bars[i].get("close", 0)

        if current <= window_high:
            continue

        entry, entry_day = _next_open(bars, i)
        if entry <= 0:
            continue
        atr_vals = _atr(bars[:i+1], 14)
        atr = atr_vals[-1] if atr_vals and atr_vals[-1] else entry * 0.02

        trade = PriceTrade(
            strategy="momentum_vol_filter",
            ticker=ticker,
            direction="long",
            entry_price=entry,
            entry_day=entry_day,
            target=entry + atr * 3,
            stop=entry - atr * 1.5,
            max_hold_days=20,
        )
        trade.simulate(bars[i+1:])  # Simulate FORWARD
        trades.append(trade)

        if len(trades) >= max_trades:
            break

    return trades


def strategy_iv_rv_spread(
    ticker: str, bars: list[dict], *, z_threshold: float = 2.0, max_trades: int = 10
) -> list[PriceTrade]:
    """Sell premium when IV-RV spread is extreme (> 2σ).

    Edge: IV-RV spread is mean-reverting. When extreme, sell premium.
    Academic: "The Information in the IV-RV Spread" (various authors)
    
    FIXED: Scans through bars, enters when Z-score > threshold, simulates forward.
    """
    if len(bars) < 80:
        return []

    trades = []
    for i in range(60, len(bars) - 20):
        window = bars[i-20:i]
        iv = _iv_proxy(window, 20)
        rv = _realized_vol(window, 20)
        if iv is None or rv is None:
            continue

        spread = iv - rv

        # Calculate historical spread distribution up to bar i
        spreads = []
        for j in range(40, i):
            iv_hist = _iv_proxy(bars[j-20:j], 20)
            rv_hist = _realized_vol(bars[j-20:j], 20)
            if iv_hist is not None and rv_hist is not None:
                spreads.append(iv_hist - rv_hist)

        if len(spreads) < 20:
            continue

        mean_spread = sum(spreads) / len(spreads)
        std_spread = math.sqrt(sum((s - mean_spread) ** 2 for s in spreads) / (len(spreads) - 1))

        if std_spread == 0:
            continue

        z_score = (spread - mean_spread) / std_spread

        if z_score < z_threshold:
            continue

        # Spread is extreme — sell premium
        entry, entry_day = _next_open(bars, i)
        if entry <= 0:
            continue

        trade = PriceTrade(
            strategy="iv_rv_spread",
            ticker=ticker,
            direction="short",
            entry_price=entry,
            entry_day=entry_day,
            target=entry * 0.97,
            stop=entry * 1.05,
            max_hold_days=20,
        )
        trade.simulate(bars[i+1:])  # Simulate FORWARD
        trades.append(trade)

        if len(trades) >= max_trades:
            break

    return trades


def strategy_gap_and_go(
    ticker: str, bars: list[dict], *, gap_threshold: float = 0.02,
    vol_threshold: float = 1.5, max_trades: int | None = 5
) -> list[PriceTrade]:
    """Stocks that gap up on volume continue in that direction.

    Edge: Momentum continuation after gap + volume confirmation.
    
    FIXED: Uses PRIOR-DAY volume (not same-day) to avoid look-ahead bias.
    At the open, you can't know today's full volume.
    """
    if len(bars) < 30:
        return []

    trades = []
    for i in range(21, len(bars) - 5):
        prev_close = bars[i - 1].get("close", 0)
        today_open = bars[i].get("open", 0)
        
        # Use PRIOR-DAY volume (known at open), not today's volume
        prev_vol = bars[i - 1].get("volume", 0)
        avg_vol = sum(b.get("volume", 0) for b in bars[i - 21:i - 1]) / 20

        if prev_close <= 0 or avg_vol == 0:
            continue

        gap = (today_open - prev_close) / prev_close
        vol_ratio = prev_vol / avg_vol if avg_vol > 0 else 0

        # Gap up + prior-day high volume = continuation
        if gap > gap_threshold and vol_ratio > vol_threshold:
            trade = PriceTrade(
                strategy="gap_and_go",
                ticker=ticker,
                direction="long",
                entry_price=today_open,
                entry_day=str(bars[i].get("time", ""))[:10],
                target=today_open * (1 + gap),
                stop=today_open * 0.98,
                max_hold_days=5,
            )
            trade.simulate(bars[i + 1:])
            trades.append(trade)

            # Was a literal 5 with no way to raise it — the function took no
            # max_trades at all, so its sample was permanently the five earliest
            # gaps in the history. The default preserves the old behaviour for
            # existing callers; the catalog passes a large value to get the full
            # series.
            if max_trades and len(trades) >= max_trades:
                break

    return trades


# ── Strategy Registry ─────────────────────────────────────────────────────

# ── NEW: High-Conviction Strategies with Documented Edges ─────────────────

def strategy_rsi2_reversion(
    ticker: str, bars: list[dict], *, rsi_threshold: float = 10, max_trades: int = 20
) -> list[PriceTrade]:
    """RSI(2) Mean Reversion — Connors' highest win-rate strategy.

    Edge: Buy extreme short-term oversold in long-term uptrend.
    Academic: Connors & Raschke (1996), documented 70-80% win rate.
    
    Rules:
    - Price > 200 SMA (uptrend filter)
    - RSI(2) < 10 (extreme oversold)
    - Exit when RSI(2) > 70 or after 5 days
    """
    if len(bars) < 210:
        return []

    trades = []
    for i in range(200, len(bars) - 5):
        # Uptrend filter: price > 200 SMA
        sma200 = _sma(bars[:i+1], 200)
        if not sma200 or sma200[-1] is None:
            continue
        price = bars[i].get("close", 0)
        if price <= sma200[-1]:
            continue

        # RSI(2) < threshold
        rsi_vals = _rsi(bars[:i+1], 2)
        if not rsi_vals or rsi_vals[-1] is None:
            continue
        if rsi_vals[-1] >= rsi_threshold:
            continue

        # Entry at next open to avoid same-close bias
        entry, entry_day = _next_open(bars, i)
        if entry <= 0:
            continue
        trade = PriceTrade(
            strategy="rsi2_reversion",
            ticker=ticker,
            direction="long",
            entry_price=entry,
            entry_day=entry_day,
            target=entry * 1.02,  # 2% target
            stop=entry * 0.97,    # 3% stop
            max_hold_days=5,
        )
        trade.simulate(bars[i+1:])
        trades.append(trade)

        if len(trades) >= max_trades:
            break

    return trades


def strategy_breakout_20d(
    ticker: str, bars: list[dict], *, max_trades: int = 15
) -> list[PriceTrade]:
    """20-Day High Breakout — Classic Donchian/Turtle strategy.

    Edge: Momentum continuation. New highs tend to make higher highs.
    Academic: Richard Donchian (1960s), Turtle Traders (1980s).
    
    Rules:
    - Close > 20-day high (breakout)
    - Volume > 1.5x average (confirmation)
    - Exit on 10-day low or 2% stop
    """
    if len(bars) < 30:
        return []

    trades = []
    for i in range(20, len(bars) - 10):
        # 20-day high (excluding current bar)
        highs = [b.get("high", 0) for b in bars[i-20:i]]
        high_20d = max(highs) if highs else 0
        
        close = bars[i].get("close", 0)
        if close <= high_20d or close <= 0:
            continue

        # Volume confirmation (prior-day volume > 1.5x avg)
        prev_vol = bars[i-1].get("volume", 0)
        avg_vol = sum(b.get("volume", 0) for b in bars[i-20:i]) / 20
        if avg_vol <= 0 or prev_vol / avg_vol < 1.5:
            continue

        entry, entry_day = _next_open(bars, i)
        if entry <= 0:
            continue
        atr_vals = _atr(bars[:i+1], 14)
        atr = atr_vals[-1] if atr_vals and atr_vals[-1] else entry * 0.02

        trade = PriceTrade(
            strategy="breakout_20d",
            ticker=ticker,
            direction="long",
            entry_price=entry,
            entry_day=entry_day,
            target=entry + atr * 3,   # 3x ATR target
            stop=entry - atr * 2,     # 2x ATR stop
            max_hold_days=10,
        )
        trade.simulate(bars[i+1:])
        trades.append(trade)

        if len(trades) >= max_trades:
            break

    return trades


def strategy_trend_pullback(
    ticker: str, bars: list[dict], *, max_trades: int = 15
) -> list[PriceTrade]:
    """Trend Pullback — Buy dips in confirmed uptrends.

    Edge: Pullbacks in uptrends are buying opportunities.
    Academic: Faber (2007) "A Quantitative Approach to TAA"
    
    Rules:
    - Price > 200 SMA (uptrend)
    - Price pulls back to 20 SMA (dip)
    - RSI(14) < 40 (oversold within uptrend)
    - Exit at 20 SMA + 2% or 3% stop
    """
    if len(bars) < 210:
        return []

    trades = []
    for i in range(200, len(bars) - 10):
        price = bars[i].get("close", 0)
        if price <= 0:
            continue

        # Uptrend: price > 200 SMA
        sma200 = _sma(bars[:i+1], 200)
        if not sma200 or sma200[-1] is None:
            continue
        if price <= sma200[-1]:
            continue

        # Pullback: price near 20 SMA (within 1%)
        sma20 = _sma(bars[:i+1], 20)
        if not sma20 or sma20[-1] is None:
            continue
        distance_to_sma20 = abs(price - sma20[-1]) / sma20[-1]
        if distance_to_sma20 > 0.01:  # Must be within 1% of 20 SMA
            continue

        # Oversold: RSI(14) < 40
        rsi_vals = _rsi(bars[:i+1], 14)
        if not rsi_vals or rsi_vals[-1] is None:
            continue
        if rsi_vals[-1] >= 40:
            continue

        entry, entry_day = _next_open(bars, i)
        if entry <= 0:
            continue
        trade = PriceTrade(
            strategy="trend_pullback",
            ticker=ticker,
            direction="long",
            entry_price=entry,
            entry_day=entry_day,
            target=entry * 1.03,  # 3% target
            stop=entry * 0.97,    # 3% stop
            max_hold_days=10,
        )
        trade.simulate(bars[i+1:])
        trades.append(trade)

        if len(trades) >= max_trades:
            break

    return trades


def strategy_three_day_reversal(
    ticker: str, bars: list[dict], *, max_trades: int = 20
) -> list[PriceTrade]:
    """3-Day Reversal — Buy after 3 consecutive down days in uptrend.

    Edge: Short-term mean reversion is strongest after consecutive declines.
    Academic: Jegadeesh (1990) short-term reversals.
    
    Rules:
    - Price > 50 SMA (uptrend context)
    - 3 consecutive down closes
    - Buy at close of 3rd down day
    - Exit after 2 days or 2% target
    """
    if len(bars) < 60:
        return []

    trades = []
    for i in range(50, len(bars) - 3):
        price = bars[i].get("close", 0)
        if price <= 0:
            continue

        # Uptrend: price > 50 SMA
        sma50 = _sma(bars[:i+1], 50)
        if not sma50 or sma50[-1] is None:
            continue
        if price <= sma50[-1]:
            continue

        # 3 consecutive down closes
        closes = [bars[j].get("close", 0) for j in range(i-3, i+1)]
        if len(closes) < 4:
            continue
        down_streak = all(closes[j] < closes[j-1] for j in range(1, 4))
        if not down_streak:
            continue

        entry, entry_day = _next_open(bars, i)
        if entry <= 0:
            continue
        trade = PriceTrade(
            strategy="three_day_reversal",
            ticker=ticker,
            direction="long",
            entry_price=entry,
            entry_day=entry_day,
            target=entry * 1.02,  # 2% target
            stop=entry * 0.985,   # 1.5% stop
            max_hold_days=3,
        )
        trade.simulate(bars[i+1:])
        trades.append(trade)

        if len(trades) >= max_trades:
            break

    return trades


def strategy_bollinger_squeeze(
    ticker: str, bars: list[dict], *, max_trades: int = 15
) -> list[PriceTrade]:
    """Bollinger Squeeze Breakout — Volatility contraction then expansion.

    Edge: Low vol periods precede high vol moves. Direction follows breakout.
    Academic: Bollinger (2001), TTM Squeeze methodology.
    
    Rules:
    - Bollinger Band Width in bottom 20% (squeeze)
    - Price breaks above upper band (expansion)
    - Exit on close below middle band or 2x ATR stop
    """
    if len(bars) < 40:
        return []

    trades = []
    for i in range(30, len(bars) - 10):
        # Calculate Bollinger Bands
        closes = [b.get("close", 0) for b in bars[i-20:i+1]]
        if len(closes) < 20 or any(c <= 0 for c in closes):
            continue
        
        sma20 = sum(closes) / len(closes)
        std20 = math.sqrt(sum((c - sma20) ** 2 for c in closes) / (len(closes) - 1))
        
        if std20 == 0:
            continue
        
        upper = sma20 + 2 * std20
        lower = sma20 - 2 * std20
        bandwidth = (upper - lower) / sma20
        
        # Check if bandwidth is in bottom 20% of recent history
        bandwidths = []
        for j in range(max(0, i-50), i):
            window = [b.get("close", 0) for b in bars[j-20:j+1]]
            if len(window) < 20 or any(c <= 0 for c in window):
                continue
            s = sum(window) / len(window)
            sd = math.sqrt(sum((c - s) ** 2 for c in window) / (len(window) - 1))
            if sd > 0:
                bandwidths.append((s + 2*sd - (s - 2*sd)) / s)
        
        if len(bandwidths) < 20:
            continue
        
        bandwidths.sort()
        p20 = bandwidths[len(bandwidths) // 5]
        
        # Squeeze: current bandwidth < 20th percentile
        if bandwidth >= p20:
            continue
        
        # Breakout: close > upper band
        price = bars[i].get("close", 0)
        if price <= upper:
            continue

        entry, entry_day = _next_open(bars, i)
        if entry <= 0:
            continue
        atr_vals = _atr(bars[:i+1], 14)
        atr = atr_vals[-1] if atr_vals and atr_vals[-1] else entry * 0.02

        trade = PriceTrade(
            strategy="bollinger_squeeze",
            ticker=ticker,
            direction="long",
            entry_price=entry,
            entry_day=entry_day,
            target=entry + atr * 3,
            stop=entry - atr * 2,
            max_hold_days=10,
        )
        trade.simulate(bars[i+1:])
        trades.append(trade)

        if len(trades) >= max_trades:
            break

    return trades


EDGE_STRATEGIES = {
    "vol_risk_premium": strategy_vol_risk_premium,
    "overnight_harvest": strategy_overnight_harvest,
    "vol_mean_reversion": strategy_vol_mean_reversion,
    "skew_harvest": strategy_skew_harvest,
    "pead_drift": strategy_pead_drift,
    "weekend_theta": strategy_weekend_theta,
    "vol_regime_switch": strategy_vol_regime_switch,
    "momentum_vol_filter": strategy_momentum_vol_filter,
    "iv_rv_spread": strategy_iv_rv_spread,
    "gap_and_go": strategy_gap_and_go,
    # NEW: High-conviction strategies with documented edges
    "rsi2_reversion": strategy_rsi2_reversion,
    "breakout_20d": strategy_breakout_20d,
    "trend_pullback": strategy_trend_pullback,
    "three_day_reversal": strategy_three_day_reversal,
    "bollinger_squeeze": strategy_bollinger_squeeze,
}


# ── Main Backtest Runner ──────────────────────────────────────────────────

def run_edge_backtest(
    bars_fn: Callable,
    tickers: list[str],
    *,
    strategies: list[str] | None = None,
    bars_limit: int = 200,
    start_date: str | None = None,
) -> dict:
    """Run edge-based strategies across tickers.
    
    Args:
        start_date: Only use bars from this date onward (YYYY-MM-DD format).
                   If None, uses all available bars.
    """
    selected = strategies or list(EDGE_STRATEGIES.keys())
    all_trades: dict[str, list[PriceTrade]] = defaultdict(list)
    errors = []

    for ticker in tickers:
        try:
            bar_payload = bars_fn(ticker, "1d", bars_limit)
            bars = bar_payload.get("bars") or []
            
            # Don't filter bars here - strategies need full history for indicators
            # Instead, filter trades AFTER strategy runs (by entry_day)
            
            if len(bars) < 50:
                continue

            for strat_name in selected:
                strat_fn = EDGE_STRATEGIES.get(strat_name)
                if not strat_fn:
                    continue

                try:
                    trades = strat_fn(ticker, bars)
                    # Filter trades by start_date (entry must be on or after start_date)
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
            "trades": [t.to_dict() for t in trades[:100]],
        }

    # Rank by Sharpe
    ranked = []
    for name, data in results.items():
        m = data["metrics"]
        if m.get("n_trades", 0) < 3:
            continue
        ranked.append({
            "strategy": name,
            "n_trades": m["n_trades"],
            "win_rate": m.get("win_rate"),
            "avg_r": m.get("avg_r"),
            "sharpe": m.get("sharpe"),
            "profit_factor": m.get("profit_factor"),
            "expectancy_pct": m.get("expectancy_pct"),
            "metrics": m,
        })

    ranked.sort(key=lambda r: r.get("sharpe") or 0, reverse=True)

    return {
        "as_of": _utcnow(),
        "mode": "edge_based",
        "tickers_tested": len(tickers),
        "strategies_tested": len(selected),
        "ranking": ranked,
        "best_strategy": ranked[0]["strategy"] if ranked else None,
        "results": results,
        "errors": errors[:30],
        "caveat": (
            "Edge-based strategy backtest using structural market inefficiencies. "
            "Edges documented in academic research: vol risk premium, overnight returns, "
            "PEAD, vol mean reversion, skew anomaly. Past performance does not guarantee "
            "future results. Research only — no order execution."
        ),
    }
