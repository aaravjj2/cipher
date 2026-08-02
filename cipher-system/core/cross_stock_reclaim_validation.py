"""Frozen cross-stock validation for daily moving-average reclaim strategies.

The module consumes the repository's common daily-bar archive and applies the
AMZN rules unchanged to every ticker.  It is intentionally read-only: callers
receive an in-memory report and may choose whether to persist it.
"""
from __future__ import annotations

import json
import math
import random
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


CORE = Path(__file__).resolve().parent
DEFAULT_BARS = CORE.parents[1] / "data" / "bars" / "bars_transformed.json"
DEFAULT_TICKERS = ("NVDA", "META", "MSFT", "GOOGL", "AAPL", "AMZN", "SPY", "QQQ")


@dataclass(frozen=True, slots=True)
class Bar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def day(self) -> date:
        return self.timestamp.date()


@dataclass(frozen=True, slots=True)
class Strategy:
    name: str
    ema_period: int
    target_r: float | None
    max_hold_days: int
    fixed_horizon: bool = False


@dataclass(frozen=True, slots=True)
class Trade:
    ticker: str
    strategy: str
    signal_date: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    stop_price: float | None
    target_price: float | None
    exit_reason: str
    net_return: float
    risk_pct: float | None


STRATEGIES = (
    Strategy("ema50_cross_reclaim_r2", 50, 2.0, 20),
    Strategy("ema50_cross_reclaim_r1_5", 50, 1.5, 20),
    Strategy("ema200_cross_reclaim_r2", 200, 2.0, 20),
    Strategy("ema200_cross_hold20", 200, None, 20, True),
)


def _finite(value: Any) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("non-finite market value")
    return result


def _ema(values: Sequence[float], period: int) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    if period <= 0 or len(values) < period:
        return result
    current = sum(values[:period]) / period
    result[period - 1] = current
    alpha = 2.0 / (period + 1.0)
    for index in range(period, len(values)):
        current = alpha * values[index] + (1.0 - alpha) * current
        result[index] = current
    return result


def _atr(bars: Sequence[Bar], period: int = 14) -> list[float | None]:
    true_ranges: list[float] = []
    for index, bar in enumerate(bars):
        if index == 0:
            true_ranges.append(bar.high - bar.low)
        else:
            prior_close = bars[index - 1].close
            true_ranges.append(
                max(bar.high - bar.low, abs(bar.high - prior_close), abs(bar.low - prior_close))
            )
    result: list[float | None] = [None] * len(bars)
    if len(true_ranges) < period:
        return result
    current = sum(true_ranges[:period]) / period
    result[period - 1] = current
    for index in range(period, len(true_ranges)):
        current = ((period - 1) * current + true_ranges[index]) / period
        result[index] = current
    return result


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def load_common_daily_bars(
    path: str | Path = DEFAULT_BARS,
    tickers: Iterable[str] = DEFAULT_TICKERS,
) -> dict[str, list[Bar]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("bar archive must be an object keyed by ticker")
    output: dict[str, list[Bar]] = {}
    for raw_ticker in tickers:
        ticker = str(raw_ticker).upper()
        node = payload.get(ticker)
        if not isinstance(node, dict) or not isinstance(node.get("bars"), list):
            continue
        rows: list[Bar] = []
        for raw in node["bars"]:
            if not isinstance(raw, dict):
                continue
            rows.append(
                Bar(
                    timestamp=_parse_timestamp(str(raw["time"])),
                    open=_finite(raw["open"]),
                    high=_finite(raw["high"]),
                    low=_finite(raw["low"]),
                    close=_finite(raw["close"]),
                    volume=_finite(raw.get("volume", 0.0)),
                )
            )
        rows.sort(key=lambda row: row.timestamp)
        if rows:
            output[ticker] = rows
    return output


def _adverse(price: float, side: str, bps: float) -> float:
    adjustment = max(0.0, float(bps)) / 10_000.0
    return price * (1.0 + adjustment if side == "buy" else 1.0 - adjustment)


def simulate_strategy(
    ticker: str,
    bars: Sequence[Bar],
    strategy: Strategy,
    *,
    slippage_bps_per_side: float = 10.0,
) -> list[Trade]:
    closes = [bar.close for bar in bars]
    ema = _ema(closes, strategy.ema_period)
    atr14 = _atr(bars, 14)
    trades: list[Trade] = []
    active_through = -1
    for signal_index in range(strategy.ema_period, len(bars) - 1):
        if signal_index <= active_through:
            continue
        current_ema = ema[signal_index]
        prior_ema = ema[signal_index - 1]
        if current_ema is None or prior_ema is None:
            continue
        signal_bar = bars[signal_index]
        prior_bar = bars[signal_index - 1]
        if not (prior_bar.close < prior_ema and signal_bar.close > current_ema):
            continue
        entry_index = signal_index + 1
        entry_bar = bars[entry_index]
        raw_entry = entry_bar.open
        last_index = min(len(bars) - 1, entry_index + strategy.max_hold_days)
        if strategy.fixed_horizon:
            raw_exit = bars[last_index].close
            exit_index = last_index
            reason = "fixed_horizon"
            stop = target = risk_pct = None
        else:
            atr = atr14[signal_index]
            if atr is None:
                continue
            stop = min(signal_bar.low, raw_entry - 0.75 * atr)
            risk = raw_entry - stop
            if risk <= 0 or risk / raw_entry > 0.15:
                continue
            target = raw_entry + float(strategy.target_r) * risk
            raw_exit = bars[last_index].close
            exit_index = last_index
            reason = "time_exit"
            for index in range(entry_index, last_index + 1):
                bar = bars[index]
                if bar.open <= stop:
                    raw_exit, exit_index, reason = bar.open, index, "stop_gap"
                    break
                if bar.open >= target:
                    raw_exit, exit_index, reason = target, index, "target"
                    break
                if bar.low <= stop:
                    raw_exit, exit_index, reason = stop, index, "stop"
                    break
                if bar.high >= target:
                    raw_exit, exit_index, reason = target, index, "target"
                    break
            risk_pct = risk / raw_entry
        entry = _adverse(raw_entry, "buy", slippage_bps_per_side)
        exit_price = _adverse(raw_exit, "sell", slippage_bps_per_side)
        trades.append(
            Trade(
                ticker=ticker,
                strategy=strategy.name,
                signal_date=signal_bar.day.isoformat(),
                entry_date=entry_bar.day.isoformat(),
                exit_date=bars[exit_index].day.isoformat(),
                entry_price=entry,
                exit_price=exit_price,
                stop_price=stop,
                target_price=target,
                exit_reason=reason,
                net_return=exit_price / entry - 1.0,
                risk_pct=risk_pct,
            )
        )
        active_through = exit_index
    return trades


def _max_drawdown(returns: Sequence[float]) -> float:
    equity = peak = 1.0
    maximum = 0.0
    for value in returns:
        equity *= 1.0 + value
        peak = max(peak, equity)
        maximum = min(maximum, equity / peak - 1.0)
    return maximum


def summarize(trades: Sequence[Trade]) -> dict[str, Any]:
    returns = [trade.net_return for trade in trades]
    positives = [value for value in returns if value > 0]
    negatives = [value for value in returns if value < 0]
    return {
        "trades": len(trades),
        "total_return_sum": sum(returns),
        "compounded_return": math.prod(1.0 + value for value in returns) - 1.0 if returns else 0.0,
        "mean_return": sum(returns) / len(returns) if returns else None,
        "win_rate": len(positives) / len(returns) if returns else None,
        "profit_factor": sum(positives) / abs(sum(negatives)) if negatives else ("Infinity" if positives else None),
        "worst_trade": min(returns) if returns else None,
        "best_trade": max(returns) if returns else None,
        "maximum_compounded_drawdown": _max_drawdown(returns),
    }


def _bootstrap_mean_ci(values: Sequence[float], *, seed: int = 50, samples: int = 4000) -> list[float] | None:
    if len(values) < 8:
        return None
    rng = random.Random(seed)
    means = []
    for _ in range(samples):
        means.append(sum(rng.choice(values) for _ in values) / len(values))
    means.sort()
    return [means[int(0.025 * len(means))], means[int(0.975 * len(means))]]


def run_validation(
    path: str | Path = DEFAULT_BARS,
    tickers: Iterable[str] = DEFAULT_TICKERS,
    *,
    slippage_bps_per_side: float = 10.0,
) -> dict[str, Any]:
    datasets = load_common_daily_bars(path, tickers)
    report: dict[str, Any] = {
        "status": "CROSS_STOCK_RECENT_DAILY_BAR_APPROXIMATION_ONLY",
        "source": str(Path(path)),
        "slippage_bps_per_side": slippage_bps_per_side,
        "tickers": sorted(datasets),
        "coverage": {},
        "strategies": {},
    }
    for ticker, bars in datasets.items():
        report["coverage"][ticker] = {
            "bars": len(bars),
            "start": bars[0].day.isoformat(),
            "end": bars[-1].day.isoformat(),
        }
    for strategy in STRATEGIES:
        per_ticker: dict[str, Any] = {}
        all_trades: list[Trade] = []
        for ticker, bars in datasets.items():
            trades = simulate_strategy(
                ticker,
                bars,
                strategy,
                slippage_bps_per_side=slippage_bps_per_side,
            )
            all_trades.extend(trades)
            per_ticker[ticker] = {
                "full": summarize(trades),
                "2025": summarize([trade for trade in trades if trade.entry_date.startswith("2025-")]),
                "2026": summarize([trade for trade in trades if trade.entry_date.startswith("2026-")]),
                "trades": [asdict(trade) for trade in trades],
            }
        pooled = summarize(sorted(all_trades, key=lambda trade: (trade.entry_date, trade.ticker)))
        pooled_returns = [trade.net_return for trade in all_trades]
        pooled["bootstrap_mean_return_ci_95"] = _bootstrap_mean_ci(pooled_returns)
        positive_tickers = sum(1 for node in per_ticker.values() if node["full"]["total_return_sum"] > 0)
        eligible_tickers = sum(1 for node in per_ticker.values() if node["full"]["trades"] > 0)
        report["strategies"][strategy.name] = {
            "definition": asdict(strategy),
            "pooled": pooled,
            "positive_tickers": positive_tickers,
            "eligible_tickers": eligible_tickers,
            "per_ticker": per_ticker,
        }
    return report
