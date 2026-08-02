"""Underlying-price proxy backtester for strategy hypothesis screening.

This module does **not** backtest option contracts or option P&L. It applies
option-inspired directional/range rules to historical underlying OHLCV bars.
Use it only for signal screening; claims about options performance require a
separate point-in-time option-chain engine with executable bid/ask data.
No GEX profile is required, so the proxies work with any price data source.

Strategies:
1. long_straddle — Buy ATM straddle before expected move
2. long_strangle — Buy OTM strangle for volatility play
3. iron_condor — Sell OTM strangle + buy wings for defined risk
4. covered_call — Hold stock + sell OTM call for income
5. bull_call_spread — Buy ATM call + sell OTM call
6. bear_put_spread — Buy ATM put + sell OTM put
7. butterfly — Buy 1 ITM + 1 OTM, sell 2 ATM (same type)
8. momentum_long — Go long when price breaks above N-day high
9. mean_reversion — Buy when RSI < 30, sell when RSI > 70
10. bollinger_squeeze — Trade breakouts from Bollinger Band squeeze
11. gap_fill — Fade overnight gaps (buy gap down, sell gap up)
12. trend_follow — Follow 20/50 day MA crossover

All strategies use Black-Scholes P&L estimation for options.
Research only — no order execution.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timezone
from typing import Callable


def _utcnow():
    return datetime.now(timezone.utc).isoformat()


# ── Black-Scholes Helpers ─────────────────────────────────────────────────

def _norm_cdf(x: float) -> float:
    """Standard normal CDF."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_price(spot: float, strike: float, dte: float, iv: float, option_type: str) -> float:
    """Black-Scholes option price."""
    if dte <= 0 or iv <= 0 or spot <= 0:
        return max(0, (spot - strike) if option_type == "call" else (strike - spot))

    dte_years = dte / 365.0
    d1 = (math.log(spot / strike) + 0.5 * iv * iv * dte_years) / (iv * math.sqrt(dte_years))
    d2 = d1 - iv * math.sqrt(dte_years)

    if option_type == "call":
        return spot * _norm_cdf(d1) - strike * math.exp(-0.05 * dte_years) * _norm_cdf(d2)
    else:
        return strike * math.exp(-0.05 * dte_years) * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


# ── Trade Simulation ──────────────────────────────────────────────────────

class PriceTrade:
    """A single simulated price-based trade."""

    def __init__(
        self,
        strategy: str,
        ticker: str,
        direction: str,
        entry_price: float,
        entry_day: str,
        *,
        target: float | None = None,
        stop: float | None = None,
        max_hold_days: int = 20,
        legs: list[dict] | None = None,
    ):
        self.strategy = strategy
        self.ticker = ticker
        self.direction = direction
        self.entry_price = entry_price
        self.entry_day = entry_day
        self.target = target
        self.stop = stop
        self.max_hold_days = max_hold_days
        self.legs = legs or []  # For multi-leg options strategies

        self.exit_price: float | None = None
        self.exit_day: str | None = None
        self.exit_reason: str | None = None
        self.hold_days: int = 0
        self.pnl_pct: float | None = None
        self.mfe_pct: float | None = None
        self.mae_pct: float | None = None
        self.r_multiple: float | None = None

    def simulate(self, bars: list[dict], *, intrabar_policy: str = "stop_first") -> None:
        """Simulate a trade over the supplied bars after the entry decision.

        Callers control timing by choosing the first supplied bar:
        - close[t] entry: pass bars beginning at t+1;
        - open[t+1] entry: pass bars beginning at t+1, including the entry bar;
        - intraday entry: pass only bars strictly after the entry timestamp.

        Every supplied bar is processed. Gap-through stops/targets fill at the
        bar open rather than at an unattainable trigger price. When both stop
        and target are touched within one OHLC bar, ``intrabar_policy`` resolves
        the unknowable path. ``stop_first`` is the conservative default;
        ``target_first`` is available for sensitivity analysis.
        """
        if intrabar_policy not in {"stop_first", "target_first"}:
            raise ValueError("intrabar_policy must be 'stop_first' or 'target_first'")
        if not bars:
            self.exit_reason = "no_bars"
            return

        entry = self.entry_price
        mfe = 0.0
        mae = 0.0

        # Validate target/stop direction
        if self.target is not None and entry > 0:
            if self.direction == "long" and self.target <= entry:
                self.exit_reason = "invalid_target"
                self.exit_price = entry
                self.exit_day = self.entry_day
                self.pnl_pct = 0.0
                return
            if self.direction == "short" and self.target >= entry:
                self.exit_reason = "invalid_target"
                self.exit_price = entry
                self.exit_day = self.entry_day
                self.pnl_pct = 0.0
                return
        if self.stop is not None and entry > 0:
            if self.direction == "long" and self.stop >= entry:
                self.exit_reason = "invalid_stop"
                self.exit_price = entry
                self.exit_day = self.entry_day
                self.pnl_pct = 0.0
                return
            if self.direction == "short" and self.stop <= entry:
                self.exit_reason = "invalid_stop"
                self.exit_price = entry
                self.exit_day = self.entry_day
                self.pnl_pct = 0.0
                return

        processed_bars: list[dict] = []
        for i, bar in enumerate(bars[: self.max_hold_days]):
            op = bar.get("open")
            hi = bar.get("high")
            lo = bar.get("low")
            cl = bar.get("close")
            if hi is None or lo is None or cl is None:
                continue

            processed_bars.append(bar)
            self.hold_days = i + 1
            bar_day = str(bar.get("time", ""))[:10]

            if self.direction == "long":
                mfe = max(mfe, (hi - entry) / entry)
                mae = min(mae, (lo - entry) / entry)
            else:
                mfe = max(mfe, (entry - lo) / entry)
                mae = min(mae, (entry - hi) / entry)

            # Opening gaps are executable at the open, not the trigger level.
            if op is not None:
                if self.direction == "long":
                    if self.stop is not None and op <= self.stop:
                        self.exit_price = op
                        self.exit_day = bar_day
                        self.exit_reason = "stop_gap"
                        break
                    if self.target is not None and op >= self.target:
                        self.exit_price = op
                        self.exit_day = bar_day
                        self.exit_reason = "target_gap"
                        break
                else:
                    if self.stop is not None and op >= self.stop:
                        self.exit_price = op
                        self.exit_day = bar_day
                        self.exit_reason = "stop_gap"
                        break
                    if self.target is not None and op <= self.target:
                        self.exit_price = op
                        self.exit_day = bar_day
                        self.exit_reason = "target_gap"
                        break

            if self.direction == "long":
                stop_hit = self.stop is not None and lo <= self.stop
                target_hit = self.target is not None and hi >= self.target
            else:
                stop_hit = self.stop is not None and hi >= self.stop
                target_hit = self.target is not None and lo <= self.target

            if stop_hit and target_hit:
                choose_stop = intrabar_policy == "stop_first"
                self.exit_price = self.stop if choose_stop else self.target
                self.exit_reason = "stop" if choose_stop else "target"
                self.exit_day = bar_day
                break
            if stop_hit:
                self.exit_price = self.stop
                self.exit_day = bar_day
                self.exit_reason = "stop"
                break
            if target_hit:
                self.exit_price = self.target
                self.exit_day = bar_day
                self.exit_reason = "target"
                break

        # Time exit
        if self.exit_price is None:
            last_bar = processed_bars[-1] if processed_bars else None
            if last_bar is None:
                self.exit_reason = "no_valid_bars"
                return
            self.exit_price = last_bar.get("close") or entry
            self.exit_day = str(last_bar.get("time", ""))[:10]
            self.exit_reason = "time_exit"

        # Compute P&L
        if self.direction == "long":
            self.pnl_pct = (self.exit_price - entry) / entry * 100
        else:
            self.pnl_pct = (entry - self.exit_price) / entry * 100

        self.mfe_pct = round(mfe * 100, 3)
        self.mae_pct = round(mae * 100, 3)

        # R-multiple
        if self.stop is not None and entry != 0:
            risk_pct = abs(entry - self.stop) / entry * 100
            if risk_pct > 0:
                self.r_multiple = round(self.pnl_pct / risk_pct, 3)

    def to_dict(self) -> dict:
        return {
            "strategy": self.strategy,
            "ticker": self.ticker,
            "direction": self.direction,
            "entry_price": self.entry_price,
            "entry_day": self.entry_day,
            "exit_price": self.exit_price,
            "exit_day": self.exit_day,
            "exit_reason": self.exit_reason,
            "hold_days": self.hold_days,
            "pnl_pct": round(self.pnl_pct, 3) if self.pnl_pct is not None else None,
            "r_multiple": self.r_multiple,
            "mfe_pct": self.mfe_pct,
            "mae_pct": self.mae_pct,
            "legs": self.legs,
        }


# ── Technical Indicators ──────────────────────────────────────────────────

def _sma(bars: list[dict], period: int, field: str = "close") -> list[float]:
    """Simple moving average. Accepts list of dicts or list of floats."""
    if bars and isinstance(bars[0], (int, float)):
        values = list(bars)
    else:
        values = [b.get(field, 0) for b in bars]
    result = []
    for i in range(len(values)):
        if i < period - 1:
            result.append(None)
        else:
            result.append(sum(values[i - period + 1:i + 1]) / period)
    return result


def _rsi(bars: list[dict], period: int = 14) -> list[float]:
    """Relative Strength Index. Accepts list of dicts or list of floats."""
    if bars and isinstance(bars[0], (int, float)):
        closes = list(bars)
    else:
        closes = [b.get("close", 0) for b in bars]
    rsi_values = []

    for i in range(len(closes)):
        if i < period:
            rsi_values.append(None)
            continue

        gains = 0
        losses = 0
        for j in range(i - period + 1, i + 1):
            change = closes[j] - closes[j - 1]
            if change > 0:
                gains += change
            else:
                losses -= change

        avg_gain = gains / period
        avg_loss = losses / period

        if avg_loss == 0:
            rsi_values.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsi_values.append(100 - (100 / (1 + rs)))

    return rsi_values


def _bollinger(bars: list[dict], period: int = 20, std_dev: float = 2.0) -> dict:
    """Bollinger Bands."""
    closes = [b.get("close", 0) for b in bars]
    upper = []
    middle = []
    lower = []

    for i in range(len(closes)):
        if i < period - 1:
            upper.append(None)
            middle.append(None)
            lower.append(None)
            continue

        window = closes[i - period + 1:i + 1]
        mean = sum(window) / period
        variance = sum((x - mean) ** 2 for x in window) / period
        std = math.sqrt(variance)

        middle.append(mean)
        upper.append(mean + std_dev * std)
        lower.append(mean - std_dev * std)

    return {"upper": upper, "middle": middle, "lower": lower}


def _atr(bars: list[dict], period: int = 14) -> list[float]:
    """Average True Range."""
    tr_values = []
    for i in range(len(bars)):
        if i == 0:
            tr_values.append(bars[i].get("high", 0) - bars[i].get("low", 0))
        else:
            hi = bars[i].get("high", 0)
            lo = bars[i].get("low", 0)
            prev_cl = bars[i - 1].get("close", 0)
            tr = max(hi - lo, abs(hi - prev_cl), abs(lo - prev_cl))
            tr_values.append(tr)

    atr_values = []
    for i in range(len(tr_values)):
        if i < period - 1:
            atr_values.append(None)
        else:
            atr_values.append(sum(tr_values[i - period + 1:i + 1]) / period)

    return atr_values


# ── Strategy Functions ────────────────────────────────────────────────────

def strategy_long_straddle(
    ticker: str, bars: list[dict], *, iv: float = 0.25, dte: float = 30
) -> list[PriceTrade]:
    """Buy ATM straddle — profits from large moves in either direction."""
    if len(bars) < 5:
        return []

    entry = bars[0].get("close")
    if not entry:
        return []

    # Straddle P&L: long call + long put at same strike
    # Approximate as: profit if move > premium paid
    trade = PriceTrade(
        strategy="long_straddle",
        ticker=ticker,
        direction="long",
        entry_price=entry,
        entry_day=str(bars[0].get("time", ""))[:10],
        target=entry * 1.05,  # 5% move target
        stop=entry * 0.97,    # 3% stop
        max_hold_days=min(dte, 20),
    )
    trade.simulate(bars[1:])
    return [trade]


def strategy_long_strangle(
    ticker: str, bars: list[dict], *, iv: float = 0.25, dte: float = 30
) -> list[PriceTrade]:
    """Buy OTM strangle — cheaper than straddle, needs bigger move."""
    if len(bars) < 5:
        return []

    entry = bars[0].get("close")
    if not entry:
        return []

    trade = PriceTrade(
        strategy="long_strangle",
        ticker=ticker,
        direction="long",
        entry_price=entry,
        entry_day=str(bars[0].get("time", ""))[:10],
        target=entry * 1.08,  # 8% move target
        stop=entry * 0.95,    # 5% stop
        max_hold_days=min(dte, 20),
    )
    trade.simulate(bars[1:])
    return [trade]


def strategy_iron_condor(
    ticker: str, bars: list[dict], *, iv: float = 0.25, dte: float = 30
) -> list[PriceTrade]:
    """Sell OTM strangle + buy wings — profits from range-bound price."""
    if len(bars) < 5:
        return []

    entry = bars[0].get("close")
    if not entry:
        return []

    # Iron condor profits if price stays in range
    # Simulate as short straddle with defined risk
    trade = PriceTrade(
        strategy="iron_condor",
        ticker=ticker,
        direction="short",  # Short volatility
        entry_price=entry,
        entry_day=str(bars[0].get("time", ""))[:10],
        target=entry * 0.98,  # Profits if price stays near entry
        stop=entry * 1.04,    # Loss if price breaks out
        max_hold_days=min(dte, 20),
    )
    trade.simulate(bars[1:])
    return [trade]


def strategy_covered_call(
    ticker: str, bars: list[dict], *, iv: float = 0.25, dte: float = 30
) -> list[PriceTrade]:
    """Hold stock + sell OTM call — income strategy."""
    if len(bars) < 5:
        return []

    entry = bars[0].get("close")
    if not entry:
        return []

    # Covered call: long stock + short call
    # Profits from stock appreciation + premium, capped upside
    trade = PriceTrade(
        strategy="covered_call",
        ticker=ticker,
        direction="long",
        entry_price=entry,
        entry_day=str(bars[0].get("time", ""))[:10],
        target=entry * 1.03,  # 3% upside target (capped by short call)
        stop=entry * 0.95,    # 5% downside protection
        max_hold_days=min(dte, 20),
    )
    trade.simulate(bars[1:])
    return [trade]


def strategy_bull_call_spread(
    ticker: str, bars: list[dict], *, iv: float = 0.25, dte: float = 30
) -> list[PriceTrade]:
    """Buy ATM call + sell OTM call — bullish with defined risk."""
    if len(bars) < 5:
        return []

    entry = bars[0].get("close")
    if not entry:
        return []

    trade = PriceTrade(
        strategy="bull_call_spread",
        ticker=ticker,
        direction="long",
        entry_price=entry,
        entry_day=str(bars[0].get("time", ""))[:10],
        target=entry * 1.05,  # 5% upside
        stop=entry * 0.97,    # 3% downside
        max_hold_days=min(dte, 20),
    )
    trade.simulate(bars[1:])
    return [trade]


def strategy_bear_put_spread(
    ticker: str, bars: list[dict], *, iv: float = 0.25, dte: float = 30
) -> list[PriceTrade]:
    """Buy ATM put + sell OTM put — bearish with defined risk."""
    if len(bars) < 5:
        return []

    entry = bars[0].get("close")
    if not entry:
        return []

    trade = PriceTrade(
        strategy="bear_put_spread",
        ticker=ticker,
        direction="short",
        entry_price=entry,
        entry_day=str(bars[0].get("time", ""))[:10],
        target=entry * 0.95,  # 5% downside
        stop=entry * 1.03,    # 3% upside
        max_hold_days=min(dte, 20),
    )
    trade.simulate(bars[1:])
    return [trade]


def strategy_momentum_long(
    ticker: str, bars: list[dict], *, lookback: int = 20
) -> list[PriceTrade]:
    """Go long when price breaks above N-day high."""
    if len(bars) < lookback + 5:
        return []

    # Find entry point: price breaks above lookback high
    entry_idx = None
    for i in range(lookback, len(bars) - 1):
        window_high = max(b.get("high", 0) for b in bars[i - lookback:i])
        if bars[i].get("close", 0) > window_high:
            entry_idx = i
            break

    if entry_idx is None:
        return []

    entry = bars[entry_idx].get("close")
    atr_vals = _atr(bars[:entry_idx + 1], 14)
    atr = atr_vals[-1] if atr_vals and atr_vals[-1] else entry * 0.02

    trade = PriceTrade(
        strategy="momentum_long",
        ticker=ticker,
        direction="long",
        entry_price=entry,
        entry_day=str(bars[entry_idx].get("time", ""))[:10],
        target=entry + atr * 3,  # 3x ATR target
        stop=entry - atr * 1.5,  # 1.5x ATR stop
        max_hold_days=20,
    )
    trade.simulate(bars[entry_idx + 1:])
    return [trade]


def strategy_mean_reversion(
    ticker: str, bars: list[dict]
) -> list[PriceTrade]:
    """Buy when RSI < 30, sell when RSI > 70."""
    rsi_vals = _rsi(bars, 14)

    entry_idx = None
    for i in range(14, len(bars) - 1):
        if rsi_vals[i] is not None and rsi_vals[i] < 30:
            entry_idx = i
            break

    if entry_idx is None:
        return []

    entry = bars[entry_idx].get("close")
    if not entry:
        return []

    # Find exit: RSI > 70 or time exit
    exit_idx = None
    for i in range(entry_idx + 1, len(bars)):
        if rsi_vals[i] is not None and rsi_vals[i] > 70:
            exit_idx = i
            break

    trade = PriceTrade(
        strategy="mean_reversion",
        ticker=ticker,
        direction="long",
        entry_price=entry,
        entry_day=str(bars[entry_idx].get("time", ""))[:10],
        target=entry * 1.05,
        stop=entry * 0.95,
        max_hold_days=20,
    )
    trade.simulate(bars[entry_idx + 1:])
    return [trade]


def strategy_bollinger_squeeze(
    ticker: str, bars: list[dict]
) -> list[PriceTrade]:
    """Trade breakouts from Bollinger Band squeeze."""
    bb = _bollinger(bars, 20, 2.0)

    # Find squeeze: bands narrow (low bandwidth)
    entry_idx = None
    for i in range(20, len(bars) - 1):
        if bb["upper"][i] and bb["lower"][i] and bb["middle"][i]:
            bandwidth = (bb["upper"][i] - bb["lower"][i]) / bb["middle"][i]
            if bandwidth < 0.05:  # Squeeze threshold
                # Check for breakout
                if bars[i].get("close", 0) > bb["upper"][i]:
                    entry_idx = i
                    break

    if entry_idx is None:
        return []

    entry = bars[entry_idx].get("close")
    if not entry:
        return []

    trade = PriceTrade(
        strategy="bollinger_squeeze",
        ticker=ticker,
        direction="long",
        entry_price=entry,
        entry_day=str(bars[entry_idx].get("time", ""))[:10],
        target=entry * 1.08,
        stop=entry * 0.96,
        max_hold_days=15,
    )
    trade.simulate(bars[entry_idx + 1:])
    return [trade]


def strategy_gap_fill(
    ticker: str, bars: list[dict]
) -> list[PriceTrade]:
    """Fade overnight gaps — buy gap down, sell gap up."""
    if len(bars) < 3:
        return []

    # Find a gap
    for i in range(1, len(bars) - 1):
        prev_close = bars[i - 1].get("close", 0)
        today_open = bars[i].get("open", 0)
        if not prev_close or not today_open:
            continue

        gap_pct = (today_open - prev_close) / prev_close

        # Gap down > 2%: buy expecting fill
        if gap_pct < -0.02:
            trade = PriceTrade(
                strategy="gap_fill",
                ticker=ticker,
                direction="long",
                entry_price=today_open,
                entry_day=str(bars[i].get("time", ""))[:10],
                target=prev_close,  # Target: fill the gap
                stop=today_open * 0.98,
                max_hold_days=3,
            )
            # Entry is the current day's open, so include this day for exits.
            trade.simulate(bars[i:])
            return [trade]

        # Gap up > 2%: short expecting fill
        elif gap_pct > 0.02:
            trade = PriceTrade(
                strategy="gap_fill",
                ticker=ticker,
                direction="short",
                entry_price=today_open,
                entry_day=str(bars[i].get("time", ""))[:10],
                target=prev_close,
                stop=today_open * 1.02,
                max_hold_days=3,
            )
            # Entry is the current day's open, so include this day for exits.
            trade.simulate(bars[i:])
            return [trade]

    return []


def strategy_trend_follow(
    ticker: str, bars: list[dict]
) -> list[PriceTrade]:
    """Follow 20/50 day MA crossover."""
    if len(bars) < 55:
        return []

    sma20 = _sma(bars, 20)
    sma50 = _sma(bars, 50)

    # Find golden cross (20 crosses above 50)
    entry_idx = None
    for i in range(50, len(bars) - 1):
        if sma20[i] and sma50[i] and sma20[i - 1] and sma50[i - 1]:
            if sma20[i] > sma50[i] and sma20[i - 1] <= sma50[i - 1]:
                entry_idx = i
                break

    if entry_idx is None:
        return []

    entry = bars[entry_idx].get("close")
    if not entry:
        return []

    trade = PriceTrade(
        strategy="trend_follow",
        ticker=ticker,
        direction="long",
        entry_price=entry,
        entry_day=str(bars[entry_idx].get("time", ""))[:10],
        target=entry * 1.10,  # 10% target
        stop=entry * 0.95,    # 5% stop
        max_hold_days=40,
    )
    trade.simulate(bars[entry_idx + 1:])
    return [trade]


# ── Strategy Registry ─────────────────────────────────────────────────────

PRICE_STRATEGIES = {
    "long_straddle": strategy_long_straddle,
    "long_strangle": strategy_long_strangle,
    "iron_condor": strategy_iron_condor,
    "covered_call": strategy_covered_call,
    "bull_call_spread": strategy_bull_call_spread,
    "bear_put_spread": strategy_bear_put_spread,
    "momentum_long": strategy_momentum_long,
    "mean_reversion": strategy_mean_reversion,
    "bollinger_squeeze": strategy_bollinger_squeeze,
    "gap_fill": strategy_gap_fill,
    "trend_follow": strategy_trend_follow,
}


# ── Metrics (reuse from strategy_backtest) ────────────────────────────────

def compute_price_metrics(trades: list[PriceTrade]) -> dict:
    """Compute performance metrics for price-based trades."""
    if not trades:
        return {"n_trades": 0, "win_rate": None, "avg_pnl_pct": None}

    pnls = [t.pnl_pct for t in trades if t.pnl_pct is not None]
    if not pnls:
        return {"n_trades": len(trades), "win_rate": None, "avg_pnl_pct": None}

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_profit = sum(wins) if wins else 0
    gross_loss = abs(sum(losses)) if losses else 0.001

    mean_pnl = sum(pnls) / len(pnls)
    if len(pnls) > 1:
        variance = sum((p - mean_pnl) ** 2 for p in pnls) / (len(pnls) - 1)
        std_pnl = math.sqrt(variance)
        sharpe = mean_pnl / std_pnl if std_pnl > 0 else 0
    else:
        sharpe = 0

    cumulative = 0
    peak = 0
    max_dd = 0
    for p in pnls:
        cumulative += p
        peak = max(peak, cumulative)
        dd = peak - cumulative
        max_dd = max(max_dd, dd)

    r_multiples = [t.r_multiple for t in trades if t.r_multiple is not None]
    avg_r = round(sum(r_multiples) / len(r_multiples), 3) if r_multiples else None

    win_rate = len(wins) / len(pnls) if pnls else 0
    avg_win = sum(wins) / len(wins) if wins else 0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 0
    expectancy = round(win_rate * avg_win - (1 - win_rate) * avg_loss, 3)

    return {
        "n_trades": len(pnls),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 4),
        "avg_pnl_pct": round(mean_pnl, 3),
        "total_pnl_pct": round(sum(pnls), 3),
        "avg_win_pct": round(avg_win, 3) if wins else None,
        "avg_loss_pct": round(-avg_loss, 3) if losses else None,
        "largest_win_pct": round(max(pnls), 3),
        "largest_loss_pct": round(min(pnls), 3),
        "profit_factor": round(gross_profit / gross_loss, 3) if gross_loss > 0 else None,
        "expectancy_pct": expectancy,
        "sharpe": round(sharpe, 3),
        "max_drawdown_pct": round(max_dd, 3),
        "avg_r": avg_r,
    }


# ── Main Backtest Runner ──────────────────────────────────────────────────

def run_price_backtest(
    bars_fn: Callable,
    tickers: list[str],
    *,
    strategies: list[str] | None = None,
    iv: float = 0.25,
    dte: float = 30,
    bars_limit: int = 200,
) -> dict:
    """Run price-based strategies across tickers using historical bars."""
    selected = strategies or list(PRICE_STRATEGIES.keys())
    all_trades: dict[str, list[PriceTrade]] = defaultdict(list)
    errors = []

    for ticker in tickers:
        try:
            bar_payload = bars_fn(ticker, "1d", bars_limit)
            bars = bar_payload.get("bars") or []
            if len(bars) < 50:
                continue

            for strat_name in selected:
                strat_fn = PRICE_STRATEGIES.get(strat_name)
                if not strat_fn:
                    continue

                try:
                    # Some strategies take iv/dte, others don't
                    import inspect
                    sig = inspect.signature(strat_fn)
                    if "iv" in sig.parameters:
                        trades = strat_fn(ticker, bars, iv=iv, dte=dte)
                    else:
                        trades = strat_fn(ticker, bars)

                    all_trades[strat_name].extend(trades)
                except Exception as exc:
                    errors.append({
                        "ticker": ticker,
                        "strategy": strat_name,
                        "error": str(exc),
                    })

        except Exception as exc:
            errors.append({"ticker": ticker, "error": str(exc)})

    # Compute metrics per strategy
    results = {}
    for strat_name, trades in all_trades.items():
        metrics = compute_price_metrics(trades)
        results[strat_name] = {
            "metrics": metrics,
            "trades": [t.to_dict() for t in trades[:100]],
        }

    # Rank by Sharpe ratio
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
        "mode": "pure_price",
        "tickers_tested": len(tickers),
        "strategies_tested": len(selected),
        "ranking": ranked,
        "best_strategy": ranked[0]["strategy"] if ranked else None,
        "results": results,
        "errors": errors[:30],
        "caveat": (
            "Pure price-based options strategy backtest using historical OHLCV bars. "
            "No GEX data required. P&L is approximated from price moves. "
            "Past performance does not guarantee future results. "
            "Research only — no order execution."
        ),
    }
