"""Options strategy backtesting engine for Cipher research terminal.

Backtests multiple GEX-derived options strategies against historical daily bars.
Each strategy defines entry/exit rules based on GEX profile structure, then
simulates trades and scores performance.

Strategies:
1. wall_bounce — Buy at put wall support, target call wall resistance
2. gamma_squeeze — Long calls when squeeze probability is high
3. vacuum_breakout — Enter when price enters GEX vacuum zone
4. divergence_reversal — Fade when VEX-GEX divergence is extreme
5. gex_momentum — Follow delta-GEX momentum direction
6. cluster_magnet — Trade toward cluster center levels
7. term_aligned — Only trade when term structure is aligned
8. flow_confluence — Trade when flow and GEX agree

All strategies are research-only. GEX is a public-OI heuristic,
not verified dealer positioning. No order execution.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timezone
from typing import Callable

from signals import (
    delta_gex_momentum,
    vex_gex_divergence,
    gex_vacuum,
    gamma_squeeze_probability,
    term_structure,
    cluster_collision,
    flow_gex_confluence,
)
from walk_forward import option_pnl_estimate, statistical_significance


def _utcnow():
    return datetime.now(timezone.utc).isoformat()


# ── Trade Simulation ──────────────────────────────────────────────────────

class Trade:
    """A single simulated trade."""

    def __init__(
        self,
        strategy: str,
        ticker: str,
        direction: str,
        entry_price: float,
        entry_day: str,
        strike: float,
        option_type: str,
        target: float | None = None,
        stop: float | None = None,
        max_hold_days: int = 5,
        iv: float = 0.25,
        dte: float = 30,
    ):
        self.strategy = strategy
        self.ticker = ticker
        self.direction = direction  # "long" or "short"
        self.entry_price = entry_price
        self.entry_day = entry_day
        self.strike = strike
        self.option_type = option_type
        self.target = target
        self.stop = stop
        self.max_hold_days = max_hold_days
        self.iv = iv
        self.dte = dte

        self.exit_price: float | None = None
        self.exit_day: str | None = None
        self.exit_reason: str | None = None
        self.hold_days: int = 0
        self.pnl_pct: float | None = None
        self.mfe_pct: float | None = None
        self.mae_pct: float | None = None
        self.r_multiple: float | None = None  # P&L in units of initial risk

    def simulate(self, bars: list[dict]) -> None:
        """Simulate the trade against forward bars."""
        if not bars:
            self.exit_reason = "no_bars"
            return

        entry = self.entry_price
        mfe = 0.0
        mae = 0.0

        for i, bar in enumerate(bars[: self.max_hold_days]):
            hi = bar.get("high")
            lo = bar.get("low")
            cl = bar.get("close")
            if hi is None or lo is None or cl is None:
                continue

            self.hold_days = i + 1

            # Track MFE/MAE
            if self.direction == "long":
                mfe = max(mfe, (hi - entry) / entry)
                mae = min(mae, (lo - entry) / entry)
            else:
                mfe = max(mfe, (entry - lo) / entry)
                mae = min(mae, (entry - hi) / entry)

            # Check stop first (conservative)
            if self.stop is not None:
                if self.direction == "long" and lo <= self.stop:
                    self.exit_price = self.stop
                    self.exit_day = str(bar.get("time", ""))[:10]
                    self.exit_reason = "stop"
                    break
                elif self.direction == "short" and hi >= self.stop:
                    self.exit_price = self.stop
                    self.exit_day = str(bar.get("time", ""))[:10]
                    self.exit_reason = "stop"
                    break

            # Check target
            if self.target is not None:
                if self.direction == "long" and hi >= self.target:
                    self.exit_price = self.target
                    self.exit_day = str(bar.get("time", ""))[:10]
                    self.exit_reason = "target"
                    break
                elif self.direction == "short" and lo <= self.target:
                    self.exit_price = self.target
                    self.exit_day = str(bar.get("time", ""))[:10]
                    self.exit_reason = "target"
                    break

        # If no exit triggered, exit at last close
        if self.exit_price is None:
            last_bar = bars[min(self.max_hold_days - 1, len(bars) - 1)]
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

        # R-multiple: P&L divided by initial risk (distance to stop)
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
            "strike": self.strike,
            "option_type": self.option_type,
            "target": self.target,
            "stop": self.stop,
            "exit_price": self.exit_price,
            "exit_day": self.exit_day,
            "exit_reason": self.exit_reason,
            "hold_days": self.hold_days,
            "pnl_pct": round(self.pnl_pct, 3) if self.pnl_pct is not None else None,
            "r_multiple": self.r_multiple,
            "mfe_pct": self.mfe_pct,
            "mae_pct": self.mae_pct,
            "risk_pct": round(abs(self.entry_price - self.stop) / self.entry_price * 100, 3)
                if self.stop and self.entry_price else None,
            "reward_pct": round(abs(self.target - self.entry_price) / self.entry_price * 100, 3)
                if self.target and self.entry_price else None,
        }


# ── Strategy Definitions ──────────────────────────────────────────────────

def strategy_wall_bounce(
    ticker: str,
    profile: list[dict],
    spot: float,
    summary: dict,
    bars: list[dict],
    *,
    iv: float = 0.25,
    dte: float = 30,
) -> list[Trade]:
    """Buy at put wall support, target call wall resistance.

    Entry: spot within 1% of put wall (support bounce).
    Target: call wall or nearest resistance.
    Stop: 1.5% below put wall.
    """
    put_wall = summary.get("put_wall_strike")
    call_wall = summary.get("call_wall_strike")
    if not put_wall or not call_wall or not spot:
        return []

    # Only enter if spot is near put wall (within 1.5%)
    dist_to_wall = abs(spot - put_wall) / spot
    if dist_to_wall > 0.015:
        return []

    stop = put_wall * 0.985
    target = call_wall

    trade = Trade(
        strategy="wall_bounce",
        ticker=ticker,
        direction="long",
        entry_price=spot,
        entry_day=str(bars[0].get("time", ""))[:10] if bars else "",
        strike=put_wall,
        option_type="call",
        target=target,
        stop=stop,
        max_hold_days=5,
        iv=iv,
        dte=dte,
    )
    trade.simulate(bars[1:])  # Forward bars after entry
    return [trade]


def strategy_gamma_squeeze(
    ticker: str,
    profile: list[dict],
    spot: float,
    summary: dict,
    bars: list[dict],
    *,
    iv: float = 0.25,
    dte: float = 30,
    threshold: float = 40.0,
) -> list[Trade]:
    """Long calls when gamma squeeze probability exceeds threshold.

    Entry: squeeze score > threshold.
    Target: nearest call wall or 3% above spot.
    Stop: 2% below entry.
    """
    squeeze = gamma_squeeze_probability(profile, spot)
    if squeeze.get("score") is None or squeeze["score"] < threshold:
        return []

    call_wall = summary.get("call_wall_strike")
    target = call_wall if call_wall and call_wall > spot else spot * 1.03
    stop = spot * 0.98

    trade = Trade(
        strategy="gamma_squeeze",
        ticker=ticker,
        direction="long",
        entry_price=spot,
        entry_day=str(bars[0].get("time", ""))[:10] if bars else "",
        strike=spot,
        option_type="call",
        target=target,
        stop=stop,
        max_hold_days=5,
        iv=iv,
        dte=dte,
    )
    trade.simulate(bars[1:])
    return [trade]


def strategy_vacuum_breakout(
    ticker: str,
    profile: list[dict],
    spot: float,
    summary: dict,
    bars: list[dict],
    *,
    iv: float = 0.25,
    dte: float = 30,
) -> list[Trade]:
    """Enter when price is at the edge of a GEX vacuum zone.

    Vacuum zones have thin dealer liquidity → price moves through quickly.
    Entry: spot within 0.5% of vacuum zone start.
    Target: vacuum zone end.
    Stop: 1% behind entry.
    """
    vacuum = gex_vacuum(profile, spot, threshold_pct=0.15, window_pct=0.08)
    if not vacuum.get("zones"):
        return []

    zone = vacuum["zones"][0]
    zone_start = zone["start"]
    zone_end = zone["end"]

    # Only enter if spot is near the start of a vacuum zone
    if abs(spot - zone_start) / spot > 0.005:
        return []

    direction = "long" if zone_end > zone_start else "short"
    target = zone_end
    stop = spot * (0.99 if direction == "long" else 1.01)

    trade = Trade(
        strategy="vacuum_breakout",
        ticker=ticker,
        direction=direction,
        entry_price=spot,
        entry_day=str(bars[0].get("time", ""))[:10] if bars else "",
        strike=spot,
        option_type="call" if direction == "long" else "put",
        target=target,
        stop=stop,
        max_hold_days=3,
        iv=iv,
        dte=dte,
    )
    trade.simulate(bars[1:])
    return [trade]


def strategy_divergence_reversal(
    ticker: str,
    profile: list[dict],
    spot: float,
    summary: dict,
    bars: list[dict],
    *,
    iv: float = 0.25,
    dte: float = 30,
    threshold: float = 50.0,
) -> list[Trade]:
    """Fade when VEX-GEX divergence is extreme.

    High divergence = regime change likely → mean reversion.
    Entry: divergence score > threshold.
    Direction: opposite of current GEX polarity.
    Target: 2% mean reversion.
    Stop: 1.5% against.
    """
    div = vex_gex_divergence(profile)
    if div.get("score") is None or div["score"] < threshold:
        return []

    gex_norm = div.get("gex_normalized", 0)
    # If GEX is positive, expect reversion down (short); if negative, expect up (long)
    direction = "short" if gex_norm > 0 else "long"
    target = spot * (0.98 if direction == "short" else 1.02)
    stop = spot * (1.015 if direction == "short" else 0.985)

    trade = Trade(
        strategy="divergence_reversal",
        ticker=ticker,
        direction=direction,
        entry_price=spot,
        entry_day=str(bars[0].get("time", ""))[:10] if bars else "",
        strike=spot,
        option_type="put" if direction == "short" else "call",
        target=target,
        stop=stop,
        max_hold_days=5,
        iv=iv,
        dte=dte,
    )
    trade.simulate(bars[1:])
    return [trade]


def strategy_gex_momentum(
    ticker: str,
    profile: list[dict],
    spot: float,
    summary: dict,
    bars: list[dict],
    *,
    iv: float = 0.25,
    dte: float = 30,
    prev_profile: list[dict] | None = None,
) -> list[Trade]:
    """Follow delta-GEX momentum direction.

    Entry: momentum score > 15 (stabilizing) or < -15 (destabilizing).
    Direction: stabilizing = long, destabilizing = short.
    Target: 2.5% in direction.
    Stop: 1.5% against.
    """
    momentum = delta_gex_momentum(profile, prev_profile)
    score = momentum.get("score")
    if score is None or abs(score) < 15:
        return []

    direction = "long" if score > 0 else "short"
    target = spot * (1.025 if direction == "long" else 0.975)
    stop = spot * (0.985 if direction == "long" else 1.015)

    trade = Trade(
        strategy="gex_momentum",
        ticker=ticker,
        direction=direction,
        entry_price=spot,
        entry_day=str(bars[0].get("time", ""))[:10] if bars else "",
        strike=spot,
        option_type="call" if direction == "long" else "put",
        target=target,
        stop=stop,
        max_hold_days=5,
        iv=iv,
        dte=dte,
    )
    trade.simulate(bars[1:])
    return [trade]


def strategy_cluster_magnet(
    ticker: str,
    profile: list[dict],
    spot: float,
    summary: dict,
    bars: list[dict],
    setups: list[dict] | None = None,
    *,
    iv: float = 0.25,
    dte: float = 30,
) -> list[Trade]:
    """Trade toward cluster center levels.

    Entry: spot within 2% of a cluster center.
    Target: cluster center (magnet effect).
    Stop: 1.5% behind entry.
    """
    if not setups:
        return []

    trades = []
    for setup in setups[:2]:  # Max 2 trades per ticker
        center = setup.get("center") or setup.get("strike")
        if not center:
            continue
        dist = abs(spot - center) / spot
        if dist > 0.02 or dist < 0.001:
            continue

        direction = "long" if center > spot else "short"
        target = center
        stop = spot * (0.985 if direction == "long" else 1.015)

        trade = Trade(
            strategy="cluster_magnet",
            ticker=ticker,
            direction=direction,
            entry_price=spot,
            entry_day=str(bars[0].get("time", ""))[:10] if bars else "",
            strike=center,
            option_type="call" if direction == "long" else "put",
            target=target,
            stop=stop,
            max_hold_days=5,
            iv=iv,
            dte=dte,
        )
        trade.simulate(bars[1:])
        trades.append(trade)

    return trades


def strategy_term_aligned(
    ticker: str,
    profile: list[dict],
    spot: float,
    summary: dict,
    bars: list[dict],
    *,
    profile_by_exp: dict[str, list[dict]] | None = None,
    iv: float = 0.25,
    dte: float = 30,
) -> list[Trade]:
    """Only trade when term structure is aligned.

    Entry: term structure classification == "aligned".
    Direction: toward peak strike.
    Target: peak strike.
    Stop: 1.5% against.
    """
    if not profile_by_exp:
        return []

    ts = term_structure(profile_by_exp, spot)
    if ts.get("classification") != "aligned":
        return []

    # Find the dominant peak strike
    peak_strike = None
    for exp_data in ts.get("expirations", []):
        if exp_data.get("peak_strike"):
            peak_strike = exp_data["peak_strike"]
            break

    if not peak_strike or abs(peak_strike - spot) / spot < 0.002:
        return []

    direction = "long" if peak_strike > spot else "short"
    target = peak_strike
    stop = spot * (0.985 if direction == "long" else 1.015)

    trade = Trade(
        strategy="term_aligned",
        ticker=ticker,
        direction=direction,
        entry_price=spot,
        entry_day=str(bars[0].get("time", ""))[:10] if bars else "",
        strike=peak_strike,
        option_type="call" if direction == "long" else "put",
        target=target,
        stop=stop,
        max_hold_days=5,
        iv=iv,
        dte=dte,
    )
    trade.simulate(bars[1:])
    return [trade]


def strategy_flow_confluence(
    ticker: str,
    profile: list[dict],
    spot: float,
    summary: dict,
    bars: list[dict],
    *,
    flow_direction: str | None = None,
    iv: float = 0.25,
    dte: float = 30,
) -> list[Trade]:
    """Trade when flow and GEX structure agree.

    Entry: confluence score > 50.
    Direction: flow direction.
    Target: 2.5% in direction.
    Stop: 1.5% against.
    """
    if not flow_direction:
        return []

    conf = flow_gex_confluence(flow_direction, profile, spot)
    if conf.get("score") is None or conf["score"] < 50:
        return []

    direction = "long" if conf["flow_bias"] == "bullish" else "short"
    target = spot * (1.025 if direction == "long" else 0.975)
    stop = spot * (0.985 if direction == "long" else 1.015)

    trade = Trade(
        strategy="flow_confluence",
        ticker=ticker,
        direction=direction,
        entry_price=spot,
        entry_day=str(bars[0].get("time", ""))[:10] if bars else "",
        strike=spot,
        option_type="call" if direction == "long" else "put",
        target=target,
        stop=stop,
        max_hold_days=5,
        iv=iv,
        dte=dte,
    )
    trade.simulate(bars[1:])
    return [trade]


# ── Strategy Registry ─────────────────────────────────────────────────────

STRATEGIES = {
    "wall_bounce": strategy_wall_bounce,
    "gamma_squeeze": strategy_gamma_squeeze,
    "vacuum_breakout": strategy_vacuum_breakout,
    "divergence_reversal": strategy_divergence_reversal,
    "gex_momentum": strategy_gex_momentum,
    "cluster_magnet": strategy_cluster_magnet,
    "term_aligned": strategy_term_aligned,
    "flow_confluence": strategy_flow_confluence,
}


# ── Performance Metrics ───────────────────────────────────────────────────

def compute_metrics(trades: list[Trade]) -> dict:
    """Compute comprehensive performance metrics from completed trades.

    Includes: win rate, R-multiples, expectancy, streaks, profit factor,
    Sharpe, drawdown, MFE/MAE efficiency, and per-ticker breakdown.
    """
    if not trades:
        return {
            "n_trades": 0,
            "win_rate": None,
            "avg_pnl_pct": None,
            "total_pnl_pct": None,
            "profit_factor": None,
            "sharpe": None,
            "max_drawdown_pct": None,
            "avg_hold_days": None,
            "avg_mfe_pct": None,
            "avg_mae_pct": None,
        }

    pnls = [t.pnl_pct for t in trades if t.pnl_pct is not None]
    if not pnls:
        return {"n_trades": len(trades), "win_rate": None, "avg_pnl_pct": None}

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_profit = sum(wins) if wins else 0
    gross_loss = abs(sum(losses)) if losses else 0.001

    # Sharpe-like ratio (mean / std)
    mean_pnl = sum(pnls) / len(pnls)
    if len(pnls) > 1:
        variance = sum((p - mean_pnl) ** 2 for p in pnls) / (len(pnls) - 1)
        std_pnl = math.sqrt(variance)
        sharpe = mean_pnl / std_pnl if std_pnl > 0 else 0
    else:
        sharpe = 0

    # Max drawdown from cumulative P&L
    cumulative = 0
    peak = 0
    max_dd = 0
    for p in pnls:
        cumulative += p
        peak = max(peak, cumulative)
        dd = peak - cumulative
        max_dd = max(max_dd, dd)

    hold_days = [t.hold_days for t in trades if t.hold_days > 0]
    mfe = [t.mfe_pct for t in trades if t.mfe_pct is not None]
    mae = [t.mae_pct for t in trades if t.mae_pct is not None]

    # R-multiple statistics
    r_multiples = [t.r_multiple for t in trades if t.r_multiple is not None]
    avg_r = round(sum(r_multiples) / len(r_multiples), 3) if r_multiples else None
    r_wins = [r for r in r_multiples if r > 0]
    r_losses = [r for r in r_multiples if r <= 0]

    # Expectancy: (win_rate * avg_win) - (loss_rate * avg_loss)
    win_rate = len(wins) / len(pnls) if pnls else 0
    avg_win = sum(wins) / len(wins) if wins else 0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 0
    expectancy = round(win_rate * avg_win - (1 - win_rate) * avg_loss, 3)

    # R-expectancy
    r_expectancy = round(
        win_rate * (sum(r_wins) / len(r_wins) if r_wins else 0)
        - (1 - win_rate) * (abs(sum(r_losses) / len(r_losses)) if r_losses else 0),
        3
    ) if r_multiples else None

    # Consecutive win/loss streaks
    max_win_streak = 0
    max_loss_streak = 0
    current_streak = 0
    for p in pnls:
        if p > 0:
            if current_streak > 0:
                current_streak += 1
            else:
                current_streak = 1
            max_win_streak = max(max_win_streak, current_streak)
        else:
            if current_streak < 0:
                current_streak -= 1
            else:
                current_streak = -1
            max_loss_streak = max(max_loss_streak, abs(current_streak))

    # Exit reason breakdown
    exit_reasons = defaultdict(int)
    for t in trades:
        if t.exit_reason:
            exit_reasons[t.exit_reason] += 1

    # Per-ticker breakdown
    ticker_stats = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0})
    for t in trades:
        if t.pnl_pct is not None:
            ts = ticker_stats[t.ticker]
            ts["trades"] += 1
            if t.pnl_pct > 0:
                ts["wins"] += 1
            ts["pnl"] += t.pnl_pct
    per_ticker = {
        tk: {
            "trades": v["trades"],
            "win_rate": round(v["wins"] / v["trades"], 4) if v["trades"] else None,
            "total_pnl_pct": round(v["pnl"], 3),
        }
        for tk, v in sorted(ticker_stats.items())
    }

    # MFE/MAE efficiency (how much of the move was captured)
    capture_ratios = []
    for t in trades:
        if t.mfe_pct and t.mfe_pct > 0 and t.pnl_pct is not None:
            capture_ratios.append(t.pnl_pct / t.mfe_pct)
    avg_capture = round(sum(capture_ratios) / len(capture_ratios), 3) if capture_ratios else None

    return {
        "n_trades": len(pnls),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 4),
        "avg_pnl_pct": round(mean_pnl, 3),
        "median_pnl_pct": round(sorted(pnls)[len(pnls) // 2], 3),
        "total_pnl_pct": round(sum(pnls), 3),
        "avg_win_pct": round(avg_win, 3) if wins else None,
        "avg_loss_pct": round(-avg_loss, 3) if losses else None,
        "largest_win_pct": round(max(pnls), 3),
        "largest_loss_pct": round(min(pnls), 3),
        "profit_factor": round(gross_profit / gross_loss, 3) if gross_loss > 0 else None,
        "expectancy_pct": expectancy,
        "sharpe": round(sharpe, 3),
        "max_drawdown_pct": round(max_dd, 3),
        "avg_hold_days": round(sum(hold_days) / len(hold_days), 2) if hold_days else None,
        "max_hold_days": max(hold_days) if hold_days else None,
        "min_hold_days": min(hold_days) if hold_days else None,
        # R-multiple stats
        "avg_r": avg_r,
        "r_expectancy": r_expectancy,
        "best_r": round(max(r_multiples), 3) if r_multiples else None,
        "worst_r": round(min(r_multiples), 3) if r_multiples else None,
        "pct_r_positive": round(len(r_wins) / len(r_multiples), 4) if r_multiples else None,
        # Streaks
        "max_win_streak": max_win_streak,
        "max_loss_streak": max_loss_streak,
        # Efficiency
        "avg_mfe_pct": round(sum(mfe) / len(mfe), 3) if mfe else None,
        "avg_mae_pct": round(sum(mae) / len(mae), 3) if mae else None,
        "avg_capture_ratio": avg_capture,
        # Breakdowns
        "exit_reasons": dict(exit_reasons),
        "per_ticker": per_ticker,
    }


def rank_strategies(results: dict[str, dict]) -> list[dict]:
    """Rank strategies by composite score.

    Composite = 0.25 * sharpe + 0.20 * win_rate + 0.15 * profit_factor_norm
              + 0.15 * r_expectancy_norm + 0.10 * (1 - max_dd_norm)
              + 0.10 * expectancy_norm + 0.05 * capture_ratio_norm
    """
    ranked = []
    for name, data in results.items():
        metrics = data.get("metrics", {})
        if metrics.get("n_trades", 0) < 3:
            continue  # Skip strategies with too few trades

        sharpe = metrics.get("sharpe") or 0
        win_rate = metrics.get("win_rate") or 0
        pf = metrics.get("profit_factor") or 0
        max_dd = metrics.get("max_drawdown_pct") or 0
        avg_pnl = metrics.get("avg_pnl_pct") or 0
        r_exp = metrics.get("r_expectancy") or 0
        expectancy = metrics.get("expectancy_pct") or 0
        capture = metrics.get("avg_capture_ratio") or 0

        # Normalize to [0, 1] range
        sharpe_norm = max(0, min(1, (sharpe + 2) / 4))  # [-2, 2] → [0, 1]
        pf_norm = max(0, min(1, pf / 3))  # [0, 3] → [0, 1]
        dd_norm = max(0, min(1, max_dd / 10))  # [0, 10%] → [0, 1]
        pnl_norm = max(0, min(1, (avg_pnl + 5) / 10))  # [-5, 5] → [0, 1]
        r_norm = max(0, min(1, (r_exp + 1) / 3))  # [-1, 2] → [0, 1]
        exp_norm = max(0, min(1, (expectancy + 3) / 6))  # [-3, 3] → [0, 1]
        cap_norm = max(0, min(1, capture))  # [0, 1] already

        composite = (
            0.25 * sharpe_norm
            + 0.20 * win_rate
            + 0.15 * pf_norm
            + 0.15 * r_norm
            + 0.10 * (1 - dd_norm)
            + 0.10 * exp_norm
            + 0.05 * cap_norm
        )

        ranked.append({
            "strategy": name,
            "composite_score": round(composite * 100, 2),
            "metrics": metrics,
            "n_trades": metrics.get("n_trades", 0),
            "avg_r": metrics.get("avg_r"),
            "r_expectancy": metrics.get("r_expectancy"),
            "expectancy_pct": metrics.get("expectancy_pct"),
        })

    ranked.sort(key=lambda r: r["composite_score"], reverse=True)
    return ranked


# ── Main Backtest Runner ──────────────────────────────────────────────────

def run_strategy_backtest(
    matrix_fn: Callable,
    bars_fn: Callable,
    tickers: list[str],
    *,
    feed: str = "opra",
    strategies: list[str] | None = None,
    iv: float = 0.25,
    dte: float = 30,
    bars_limit: int = 60,
) -> dict:
    """Run all (or selected) strategies across tickers and rank results.

    For each ticker:
    1. Fetch matrix (GEX profile) and bars
    2. Run each strategy to generate trades
    3. Simulate trades against forward bars
    4. Aggregate metrics per strategy
    5. Rank strategies by composite score
    """
    selected = strategies or list(STRATEGIES.keys())
    all_trades: dict[str, list[Trade]] = defaultdict(list)
    errors = []

    for ticker in tickers:
        try:
            # Fetch matrix for GEX profile
            payload = matrix_fn(ticker, feed, 0.06, 1, force=False)
            spot = (payload.get("quote") or {}).get("price_context")
            summary = payload.get("summary") or {}
            rows = payload.get("rows") or []

            # Build profile from rows
            profile = []
            for row in rows:
                cells = row.get("cells") or []
                if not any(c.get("available") for c in cells):
                    continue
                call = sum(c.get("call_gex") or 0 for c in cells)
                put = sum(c.get("put_gex") or 0 for c in cells)
                net = call + put
                vex = sum((c.get("net_vex") or 0) for c in cells)
                oi = sum((c.get("call_oi") or 0) + (c.get("put_oi") or 0) for c in cells)
                profile.append({
                    "strike": float(row["strike"]),
                    "call": call,
                    "put": put,
                    "net": net,
                    "abs": abs(net),
                    "vex": vex,
                    "net_vex": vex,
                    "oi": oi,
                    "volume": sum(c.get("volume") or 0 for c in cells),
                })
            profile.sort(key=lambda p: p["strike"])

            if not profile or not spot:
                continue

            # Fetch bars
            bar_payload = bars_fn(ticker, "1d", bars_limit)
            bars = bar_payload.get("bars") or []
            if len(bars) < 10:
                continue

            # Get setups from scanner if available
            setups = None
            try:
                from scanner import _strike_profile, _local_peaks, classify_setup
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
                        trades = strat_fn(ticker, profile, spot, summary, bars, setups, **kwargs)
                    elif strat_name == "term_aligned":
                        # Build profile_by_exp from matrix rows
                        profile_by_exp = {}
                        expirations = payload.get("expirations") or []
                        for i, exp in enumerate(expirations):
                            exp_label = exp if isinstance(exp, str) else (exp.get("expiration") or "")
                            exp_profile = []
                            for row in rows:
                                cells = row.get("cells") or []
                                if i < len(cells) and cells[i].get("available"):
                                    c = cells[i]
                                    exp_profile.append({
                                        "strike": float(row["strike"]),
                                        "net": (c.get("call_gex") or 0) + (c.get("put_gex") or 0),
                                        "abs": abs((c.get("call_gex") or 0) + (c.get("put_gex") or 0)),
                                    })
                            if exp_profile:
                                profile_by_exp[str(exp_label)[:10]] = exp_profile
                        trades = strat_fn(
                            ticker, profile, spot, summary, bars,
                            profile_by_exp=profile_by_exp, **kwargs
                        )
                    elif strat_name == "flow_confluence":
                        # Infer flow from recent bar direction
                        if len(bars) >= 3:
                            recent = bars[-3:]
                            up = sum(1 for b in recent if (b.get("close") or 0) > (b.get("open") or 0))
                            flow_dir = "bullish" if up >= 2 else "bearish"
                        else:
                            flow_dir = None
                        trades = strat_fn(
                            ticker, profile, spot, summary, bars,
                            flow_direction=flow_dir, **kwargs
                        )
                    else:
                        trades = strat_fn(ticker, profile, spot, summary, bars, **kwargs)

                    all_trades[strat_name].extend(trades)
                except Exception as exc:
                    errors.append({"ticker": ticker, "strategy": strat_name, "error": str(exc)})

        except Exception as exc:
            errors.append({"ticker": ticker, "error": str(exc)})

    # Compute metrics per strategy
    results = {}
    for strat_name, trades in all_trades.items():
        metrics = compute_metrics(trades)
        sig = statistical_significance(
            metrics.get("wins", 0),
            metrics.get("n_trades", 0),
            null_rate=0.5,
        )
        results[strat_name] = {
            "metrics": metrics,
            "significance": sig,
            "trades": [t.to_dict() for t in trades[:50]],
        }

    # Rank
    ranked = rank_strategies(results)

    return {
        "as_of": _utcnow(),
        "tickers_tested": len(tickers),
        "strategies_tested": len(selected),
        "ranking": ranked,
        "best_strategy": ranked[0]["strategy"] if ranked else None,
        "results": results,
        "errors": errors[:30],
        "caveat": (
            "Strategy backtest from GEX-derived signals on daily bars. "
            "Past performance does not guarantee future results. "
            "GEX is a public-OI heuristic, not verified dealer positioning. "
            "Research only — no order execution."
        ),
    }
