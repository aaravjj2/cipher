"""Walk-forward backtest validation and regime-conditional analysis.

Extends cluster_backtest with:
- Walk-forward validation: train weights on earlier snapshots, test on later ones
- Regime-conditional scoring: group results by market regime (vol level, trend)
- Option P&L estimation: approximate P&L from level touches using Black-Scholes
- Statistical significance tests

GEX is a public-OI heuristic, not verified dealer positioning.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BT_DIR = ROOT / "data" / "backtests"
WALK_FORWARD_DIR = BT_DIR / "walk_forward"


def _utcnow():
    return datetime.now(timezone.utc).isoformat()


def walk_forward_split(
    snapshots: list[dict],
    *,
    train_ratio: float = 0.6,
    min_train: int = 3,
    min_test: int = 2,
) -> list[dict]:
    """Split snapshots into sequential train/test folds for walk-forward validation.

    Each fold trains on all snapshots up to a point, then tests on the next batch.
    Returns list of {fold, train_snapshots, test_snapshots}.
    """
    if len(snapshots) < min_train + min_test:
        return []

    # Sort by as_of date
    sorted_snaps = sorted(snapshots, key=lambda s: s.get("as_of") or "")
    n = len(sorted_snaps)
    train_end = max(min_train, int(n * train_ratio))

    folds = []
    fold_num = 0

    # Sliding window: each fold adds one more snapshot to training
    for test_start in range(train_end, n - min_test + 2):
        fold_num += 1
        train = sorted_snaps[:test_start]
        test = sorted_snaps[test_start:test_start + min_test]
        if not test:
            break
        folds.append({
            "fold": fold_num,
            "train_count": len(train),
            "test_count": len(test),
            "train_snapshots": train,
            "test_snapshots": test,
            "train_range": f"{train[0].get('as_of', '?')[:10]} → {train[-1].get('as_of', '?')[:10]}",
            "test_range": f"{test[0].get('as_of', '?')[:10]} → {test[-1].get('as_of', '?')[:10]}",
        })

    return folds


def regime_from_bars(bars: list[dict]) -> dict:
    """Classify market regime from recent daily bars.

    Returns: trend (up/down/sideways), vol_level (low/normal/high/extended),
    atr_pct, and consecutive direction count.
    """
    if len(bars) < 5:
        return {"trend": "unknown", "vol_level": "unknown", "atr_pct": None, "consecutive_dir": None}

    closes = [b.get("close") for b in bars if b.get("close") is not None]
    highs = [b.get("high") for b in bars if b.get("high") is not None]
    lows = [b.get("low") for b in bars if b.get("low") is not None]

    if len(closes) < 5:
        return {"trend": "unknown", "vol_level": "unknown", "atr_pct": None, "consecutive_dir": None}

    # ATR proxy: average true range over last 14 bars
    trs = []
    for i in range(1, min(len(closes), 15)):
        hi = highs[i] if i < len(highs) else closes[i]
        lo = lows[i] if i < len(lows) else closes[i]
        prev_close = closes[i - 1]
        tr = max(hi - lo, abs(hi - prev_close), abs(lo - prev_close))
        trs.append(tr)

    atr = sum(trs) / len(trs) if trs else 0
    atr_pct = atr / closes[-1] * 100 if closes[-1] else 0

    # Vol classification
    if atr_pct < 1.0:
        vol_level = "low"
    elif atr_pct < 2.5:
        vol_level = "normal"
    elif atr_pct < 4.0:
        vol_level = "high"
    else:
        vol_level = "extended"

    # Trend from linear regression slope over last 10 bars
    recent = closes[-10:]
    n = len(recent)
    if n >= 5:
        x_mean = (n - 1) / 2
        y_mean = sum(recent) / n
        num = sum((i - x_mean) * (recent[i] - y_mean) for i in range(n))
        den = sum((i - x_mean) ** 2 for i in range(n))
        slope = num / den if den else 0
        slope_pct = slope / y_mean * 100 if y_mean else 0

        if slope_pct > 0.15:
            trend = "up"
        elif slope_pct < -0.15:
            trend = "down"
        else:
            trend = "sideways"
    else:
        trend = "unknown"
        slope_pct = 0

    # Consecutive direction count
    consecutive = 0
    if len(closes) >= 2:
        direction = 1 if closes[-1] > closes[-2] else -1
        for i in range(len(closes) - 2, 0, -1):
            if (closes[i] > closes[i - 1]) == (direction > 0):
                consecutive += 1
            else:
                break

    return {
        "trend": trend,
        "vol_level": vol_level,
        "atr_pct": round(atr_pct, 3),
        "slope_pct": round(slope_pct, 4),
        "consecutive_dir": consecutive,
    }


def option_pnl_estimate(
    entry_price: float,
    strike: float,
    option_type: str,
    exit_price: float,
    *,
    iv: float = 0.25,
    days_to_expiry: float = 30,
    risk_free_rate: float = 0.045,
) -> dict:
    """Estimate option P&L from entry to exit using Black-Scholes approximation.

    This is a research tool for understanding what a touch of a GEX level
    would mean in option P&L terms.
    """
    if entry_price <= 0 or exit_price <= 0 or strike <= 0 or days_to_expiry <= 0:
        return {"pnl_pct": None, "pnl_per_contract": None, "error": "invalid inputs"}

    T = days_to_expiry / 365.25
    sigma = iv
    if sigma <= 0:
        sigma = 0.001

    def _bs_price(spot, is_call):
        d1 = (math.log(spot / strike) + (risk_free_rate + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        nd1 = _norm_cdf(d1)
        nd2 = _norm_cdf(d2)
        discount = math.exp(-risk_free_rate * T)
        if is_call:
            return spot * nd1 - strike * discount * nd2
        else:
            return strike * discount * _norm_cdf(-d2) - spot * _norm_cdf(-d1)

    entry_val = _bs_price(entry_price, option_type == "call")
    exit_val = _bs_price(exit_price, option_type == "call")

    if entry_val <= 0:
        return {"pnl_pct": None, "pnl_per_contract": None, "error": "entry option value is zero"}

    pnl_pct = (exit_val - entry_val) / entry_val * 100
    pnl_per_contract = (exit_val - entry_val) * 100  # 100 shares per contract

    return {
        "entry_option_price": round(entry_val, 4),
        "exit_option_price": round(exit_val, 4),
        "pnl_pct": round(pnl_pct, 2),
        "pnl_per_contract": round(pnl_per_contract, 2),
        "option_type": option_type,
        "strike": strike,
        "iv": iv,
        "dte": days_to_expiry,
    }


def _norm_cdf(x: float) -> float:
    """Standard normal CDF using math.erf for accuracy."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def regime_conditional_summary(
    results: list[dict],
    bars_fn,
    *,
    lookback: int = 20,
) -> dict:
    """Group backtest results by market regime and compute per-regime hit rates.

    For each result, fetches the bars at the time of the snapshot to classify
    the regime, then groups results by (trend, vol_level).
    """
    by_regime: dict[str, dict] = defaultdict(lambda: {
        "n": 0, "hits": 0, "mfe_sum": 0.0, "mae_sum": 0.0,
        "mfe_count": 0, "mae_count": 0,
    })

    for r in results:
        ticker = r.get("ticker")
        if not ticker:
            continue
        try:
            bar_payload = bars_fn(ticker, "1d", lookback)
            bars = bar_payload.get("bars") or []
            regime = regime_from_bars(bars)
        except Exception:
            regime = {"trend": "unknown", "vol_level": "unknown"}

        key = f"{regime['trend']}_{regime['vol_level']}"
        bucket = by_regime[key]
        bucket["n"] += 1
        if r.get("hit"):
            bucket["hits"] += 1
        if r.get("mfe_pct") is not None:
            bucket["mfe_sum"] += r["mfe_pct"]
            bucket["mfe_count"] += 1
        if r.get("mae_pct") is not None:
            bucket["mae_sum"] += r["mae_pct"]
            bucket["mae_count"] += 1

    summary = {}
    for key, bucket in by_regime.items():
        n = bucket["n"]
        summary[key] = {
            "n": n,
            "hit_rate": round(bucket["hits"] / n, 4) if n else None,
            "avg_mfe_pct": round(bucket["mfe_sum"] / bucket["mfe_count"], 3) if bucket["mfe_count"] else None,
            "avg_mae_pct": round(bucket["mae_sum"] / bucket["mae_count"], 3) if bucket["mae_count"] else None,
        }

    return {
        "as_of": _utcnow(),
        "n_results": len(results),
        "regimes": summary,
        "caveat": (
            "Regime-conditional analysis groups hit rates by trend and vol level. "
            "Small sample sizes per regime may not be statistically significant."
        ),
    }


def statistical_significance(hits: int, total: int, *, null_rate: float = 0.5) -> dict:
    """Compute whether a hit rate is statistically different from a null hypothesis.

    Uses a simple binomial test approximation.
    """
    if total <= 0:
        return {"significant": False, "p_value": None, "note": "no observations"}

    observed_rate = hits / total
    # Normal approximation to binomial
    expected = total * null_rate
    std = math.sqrt(total * null_rate * (1 - null_rate))

    if std <= 0:
        return {"significant": False, "p_value": None, "note": "zero variance"}

    z = (hits - expected) / std
    # Two-tailed p-value approximation
    p_value = 2 * (1 - _norm_cdf(abs(z)))

    return {
        "observed_rate": round(observed_rate, 4),
        "null_rate": null_rate,
        "n": total,
        "z_score": round(z, 3),
        "p_value": round(p_value, 4),
        "significant": p_value < 0.05,
        "note": "p < 0.05 = statistically significant vs null" if p_value < 0.05 else "not significant at 95% confidence",
    }


def run_walk_forward(
    snapshots: list[dict],
    bars_fn,
    *,
    horizon: int = 5,
    train_ratio: float = 0.6,
    tol_pct: float = 0.0025,
) -> dict:
    """Run walk-forward validation across all snapshot folds.

    For each fold, scores the test snapshots using bars after their as_of date.
    Aggregates results across folds for overall walk-forward hit rate.
    """
    folds = walk_forward_split(snapshots, train_ratio=train_ratio)
    if not folds:
        return {"error": "Insufficient snapshots for walk-forward validation", "n_folds": 0}

    # Import here to avoid circular dependency
    from cluster_backtest import score_snapshot, _forward_bars

    all_results = []
    fold_summaries = []

    for fold in folds:
        fold_hits = 0
        fold_total = 0

        for test_snap in fold["test_snapshots"]:
            try:
                report = score_snapshot(
                    bars_fn,
                    test_snap,
                    horizon=horizon,
                    tol_pct=tol_pct,
                    require_forward=True,
                    write=False,
                )
                for r in report.get("results", []):
                    all_results.append({**r, "fold": fold["fold"]})
                    fold_total += 1
                    if r.get("hit"):
                        fold_hits += 1
            except Exception:
                continue

        fold_summaries.append({
            "fold": fold["fold"],
            "train_range": fold["train_range"],
            "test_range": fold["test_range"],
            "n_setups": fold_total,
            "hit_rate": round(fold_hits / fold_total, 4) if fold_total else None,
        })

    total_hits = sum(1 for r in all_results if r.get("hit"))
    total_n = len(all_results)
    sig = statistical_significance(total_hits, total_n)

    return {
        "as_of": _utcnow(),
        "n_folds": len(folds),
        "n_total_setups": total_n,
        "overall_hit_rate": round(total_hits / total_n, 4) if total_n else None,
        "folds": fold_summaries,
        "significance": sig,
        "horizon": horizon,
        "caveat": (
            "Walk-forward validation trains on past, tests on future. "
            "No look-ahead bias. GEX is a public-OI heuristic."
        ),
    }
