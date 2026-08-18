#!/usr/bin/env python3
"""Deep-dive report for the crowned 1-minute Obsidian EOD candidate.

Loads the completed optimization run, re-enriches every holdout trade with bar
context from the same complete-session SQLite archive (signal time-of-day,
weekday, month, prior 20-day realized volatility, same-day move at signal), and
writes a findings report plus a machine-readable detail JSON.

The trade set is the one recorded in the optimization run — this script does not
re-optimize and cannot change the selection. It exists to answer *where* the
holdout edge comes from and how fragile it looks.

Research-only. Not trading advice.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for path in (str(ROOT), str(ROOT / "core")):
    if path not in sys.path:
        sys.path.insert(0, path)

from core.equity_history_download import EquityBarStore  # noqa: E402
from core.holdout_economics import _holdout_economics  # noqa: E402
from scripts.experiment_obsidian_eod import (  # noqa: E402
    REQUIRED_SYMBOLS,
    _complete_session_rows,
    _et,
    _rth,
    load_rows,
)

UTC = timezone.utc
# Defaults preserve the original invocation. They are overridable because the sweep now runs
# four families, and a report that can only read one of them cannot answer which window or bar
# size the edge prefers -- the question the widened search exists to settle.
DEFAULT_FAMILY = "1m_final_30_primary"
DEFAULT_OPTIMIZATION = ROOT / "results" / "obsidian_eod_optimization_2026.json"
RESULTS_DIR = ROOT / "results"

# Rebound in main() from the parsed arguments; module-level names are kept so the existing
# helpers and tests continue to import cleanly.
# Resolved in main() from the run's recorded universe. The hardcoded ten-symbol tuple would
# silently cover a quarter of a 41-symbol run, reporting per-symbol tables that look complete.
UNIVERSE: tuple[str, ...] = tuple(REQUIRED_SYMBOLS)

FAMILY = DEFAULT_FAMILY
OPTIMIZATION = DEFAULT_OPTIMIZATION
REPORT_MD = RESULTS_DIR / "obsidian_eod_1m_candidate_deep_dive.md"
REPORT_JSON = RESULTS_DIR / "obsidian_eod_1m_candidate_deep_dive.json"


# ── data loading ─────────────────────────────────────────────────────────────

def _load_trades(optimization: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return (chosen candidate, its holdout trades, the family dict)."""
    for family in optimization.get("families") or []:
        if family.get("family") == FAMILY:
            chosen = family.get("chosen_from_training")
            if not chosen:
                raise SystemExit(f"{FAMILY}: no crowned candidate in {OPTIMIZATION.name}")
            trades = [trade for row in family.get("holdout_rows") or [] for trade in row["trades"]]
            return chosen, trades, family
    raise SystemExit(f"{FAMILY}: family not found in {OPTIMIZATION.name}")


def _load_daily_context(db: Path) -> dict[str, dict[date, dict[str, Any]]]:
    """Per-symbol daily summaries over complete sessions only.

    Returns {symbol: {day: {"open": float, "close": float, "bars": int}}}.
    """
    store = EquityBarStore(db.parent, db_path=db)
    out: dict[str, dict[date, dict[str, Any]]] = {}
    for symbol in UNIVERSE:
        rows = _rth(load_rows(store, symbol, "1Min"))
        complete, _summary_info = _complete_session_rows(rows, 390)
        by_day: dict[date, list[dict[str, Any]]] = defaultdict(list)
        for row in complete:
            by_day[_et(str(row["time"])).date()].append(row)
        daily: dict[date, dict[str, Any]] = {}
        for day, bars in by_day.items():
            daily[day] = {
                "open": float(bars[0]["open"]),
                "close": float(bars[-1]["close"]),
                "bars": len(bars),
            }
        out[symbol] = daily
    return out


def _prior_20d_vol_pct(daily: dict[date, dict[str, Any]], day: date) -> float | None:
    """Annualized stdev of the prior 20 daily close-to-close returns, in %."""
    ordered = [d for d in sorted(daily) if d < day][-21:]
    if len(ordered) < 21:
        return None
    closes = [daily[d]["close"] for d in ordered]
    returns = [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes))]
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / len(returns)
    daily_sd = math.sqrt(variance)
    return round(daily_sd * math.sqrt(252) * 100.0, 3)


# ── statistics helpers ───────────────────────────────────────────────────────

def _streak_stats(values: list[float]) -> dict[str, Any]:
    """Longest consecutive-losing/winning runs for a 1-D sequence of P&L."""
    best_loss = best_win = current_loss = current_win = 0
    for value in values:
        if value < 0:
            current_loss += 1
            current_win = 0
            best_loss = max(best_loss, current_loss)
        else:
            current_win += 1
            current_loss = 0
            best_win = max(best_win, current_win)
    return {"max_consecutive_losing": best_loss, "max_consecutive_winning": best_win}


def _dd_from_series(values: list[float]) -> dict[str, Any]:
    """Max drawdown (in percentage points) on a cumulative-sum P&L curve.

    The pooled line is a sum of per-share returns that can cross zero, so a
    percentage-of-peak drawdown is meaningless (it can exceed 100%). Reporting
    the peak-to-trough distance in pp is the honest framing for this line.
    """
    equity = 0.0
    peak = 0.0
    max_dd_pp = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        max_dd_pp = max(max_dd_pp, peak - equity)
    return {"final_cumulative_pct": round(equity, 6), "max_drawdown_pp": round(max_dd_pp, 6)}


def _bucket(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Summary block for an arbitrary subset of trades."""
    if not trades:
        return {"trades": 0}
    returns = [t["net_return_pct"] for t in trades]
    wins = [r for r in returns if r > 0]
    losses = [-r for r in returns if r <= 0]
    gross_profit = sum(wins)
    gross_loss = sum(losses)
    return {
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(100.0 * len(wins) / len(returns), 2),
        "avg_trade_return_pct": round(sum(returns) / len(returns), 6),
        "median_trade_return_pct": round(
            (
                sorted(returns)[len(returns) // 2]
                if len(returns) % 2
                else (
                    sorted(returns)[len(returns) // 2 - 1]
                    + sorted(returns)[len(returns) // 2]
                ) / 2.0
            ),
            6,
        ),
        # Renamed from `gross_sum_return_pct`, which summed `net_return_pct` and so published
        # a post-slippage number under a name meaning pre-cost everywhere in trading. It was
        # also the only "gross" figure emitted, so the cost drag appeared nowhere.
        "net_sum_return_pct": round(sum(returns), 6),
        "pre_cost_sum_return_pct": round(sum(t["gross_return_pct"] for t in trades), 6),
        "slippage_drag_pct": round(
            sum(t["gross_return_pct"] for t in trades) - sum(returns), 6
        ),
        "profit_factor": round(gross_profit / gross_loss, 6) if gross_loss else None,
        "avg_win_pct": round(sum(wins) / len(wins), 6) if wins else None,
        "avg_loss_pct": round(sum(losses) / len(losses), 6) if losses else None,
        "expectancy_pct": round(sum(returns) / len(returns), 6),
    }


def _md_table(headers: list[str], rows: list[list[Any]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        out.append("| " + " | ".join(str(v) for v in row) + " |")
    return "\n".join(out)


# ── report body ──────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    global FAMILY, OPTIMIZATION, REPORT_MD, REPORT_JSON, UNIVERSE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--family", default=DEFAULT_FAMILY, help="family name inside the run")
    parser.add_argument("--optimization", type=Path, default=DEFAULT_OPTIMIZATION)
    parser.add_argument(
        "--slug",
        default=None,
        help="Output basename. Defaults to the family name, so reports for different "
             "families do not overwrite each other.",
    )
    args = parser.parse_args(argv)
    FAMILY = args.family
    OPTIMIZATION = args.optimization.resolve()
    slug = args.slug or f"obsidian_eod_{args.family}_deep_dive"
    REPORT_MD = RESULTS_DIR / f"{slug}.md"
    REPORT_JSON = RESULTS_DIR / f"{slug}.json"
    if not OPTIMIZATION.exists():
        raise SystemExit(f"optimization run not found: {OPTIMIZATION}")
    optimization = json.loads(OPTIMIZATION.read_text(encoding="utf-8"))
    recorded_symbols = (optimization.get("data") or {}).get("symbols")
    if recorded_symbols:
        UNIVERSE = tuple(recorded_symbols)
    chosen, trades, family = _load_trades(optimization)
    if not trades:
        raise SystemExit("no holdout trades found for the crowned candidate")

    db = Path(optimization["data"]["database"])
    if not db.exists():
        raise SystemExit(f"recorded database is missing: {db}")
    daily_context = _load_daily_context(db)

    # Reconcile against the recorded summary before any analysis.
    recorded = family["holdout"]
    recomputed = _bucket(trades)
    assert recomputed["trades"] == recorded["trades"] == len(trades)
    assert abs(recomputed["avg_trade_return_pct"] - recorded["avg_trade_return_pct"]) < 1e-9, (
        recomputed["avg_trade_return_pct"], recorded["avg_trade_return_pct"]
    )

    # Enrich each trade with bar context.
    for trade in trades:
        signal_dt = _et(trade["signal_time"])
        day = signal_dt.date()
        trade["_day"] = day.isoformat()
        trade["_signal_minute"] = signal_dt.hour * 60 + signal_dt.minute
        trade["_weekday"] = day.strftime("%A")
        trade["_month"] = day.strftime("%Y-%m")
        daily = daily_context.get(trade["symbol"], {})
        if day in daily:
            day_open = daily[day]["open"]
            signal_close = float(trade["signal_price"])
            trade["_day_move_at_signal_pct"] = round((signal_close / day_open - 1.0) * 100.0, 4)
            trade["_prior_20d_vol_pct"] = _prior_20d_vol_pct(daily, day)
        else:
            trade["_day_move_at_signal_pct"] = None
            trade["_prior_20d_vol_pct"] = None

    parts: list[str] = []
    parts.append(f"# Obsidian EOD — {FAMILY} candidate deep dive")
    parts.append("")
    parts.append(
        f"Generated {datetime.now(UTC).isoformat()} from `{OPTIMIZATION.name}`. "
        "Research-only reconstruction; not trading advice."
    )
    parts.append("")

    # ── candidate & protocol ────────────────────────────────────────────────
    parts.append("## Candidate and protocol")
    parts.append("")
    params = chosen["params"]
    parts.append(f"- **Family**: `{FAMILY}`.")
    parts.append(f"- **Chosen parameters**: `{json.dumps(params, sort_keys=True)}`.")
    parts.append(
        "- **Selection rule**: positive average return in **all** three development folds, "
        f"≥{chosen.get('minimum_fold_trades', 20)} pooled trades per fold, breadth "
        f"≥{chosen.get('minimum_positive_symbols', 5)} positive symbols per fold. "
        "Holdout is descriptive only."
    )
    parts.append(f"- **Holdout window**: {optimization['data']['holdout']['start']} → {optimization['data']['holdout']['end']}.")
    parts.append("")
    parts.append("### Development folds (training)")
    parts.append("")
    rows = [
        [
            f"{start} – {end}",
            f_metric.get("trades", 0),
            f_metric.get("avg_trade_return_pct"),
            f_metric.get("profit_factor"),
            f_metric.get("win_rate_pct"),
            f_metric.get("positive_symbols", "-"),
        ]
        for (start, end), f_metric in zip(
            optimization["data"]["train_folds"], chosen["folds"]
        )
    ]
    parts.append(_md_table(["Fold", "Trades", "Avg %", "PF", "Win rate %", "Pos symbols"], rows))
    parts.append("")

    # ── holdout overview ────────────────────────────────────────────────────
    parts.append(f"## Holdout overview ({len(trades)} recorded trades)")
    parts.append("")
    s = _bucket(trades)
    parts.append(
        _md_table(
            ["Trades", "Win rate %", "Avg %/trade", "Median %", "PF", "Net sum %", "Avg win %", "Avg loss %"],
            [[s["trades"], s["win_rate_pct"], s["avg_trade_return_pct"], s["median_trade_return_pct"],
              s["profit_factor"], s["net_sum_return_pct"], s["avg_win_pct"], s["avg_loss_pct"]]],
        )
    )
    parts.append("")
    daily_pnl: dict[date, float] = defaultdict(float)
    for trade in trades:
        daily_pnl[date.fromisoformat(trade["_day"])] += trade["net_return_pct"]
    ordered_days = sorted(daily_pnl)
    daily_values = [daily_pnl[d] for d in ordered_days]
    curve = _dd_from_series(daily_values)
    streaks = _streak_stats(daily_values)
    trade_streaks = _streak_stats([t["net_return_pct"] for t in sorted(trades, key=lambda t: t["entry_time"])])
    parts.append(
        f"Over {len(ordered_days)} trading days: pooled daily P&L sum **{curve['final_cumulative_pct']:.3f}%**, "
        f"max drawdown on the cumulative-sum curve **{curve['max_drawdown_pp']:.2f} pp** "
        "(peak-to-trough distance of the pooled sum, which can cross zero). "
        f"Max consecutive losing days {streaks['max_consecutive_losing']}; "
        f"max consecutive losing trades {trade_streaks['max_consecutive_losing']}."
    )
    parts.append("")

    # ── what the holdout is actually worth ──────────────────────────────────
    # The pooled sum above is the sum of per-trade percentages across ten symbols. It is not
    # a return, and read as one it overstates by roughly the symbol count. This section states
    # the figure a reader is looking for -- compound each symbol, weight them equally -- and
    # compares it to the rate the capital would earn sitting still, which is the only
    # comparison that decides whether the strategy is worth running.
    hold_start = date.fromisoformat(optimization["data"]["holdout"]["start"])
    hold_end = date.fromisoformat(optimization["data"]["holdout"]["end"])
    econ = _holdout_economics(
        trades,
        hold_start,
        hold_end,
        universe=UNIVERSE,
        risk_free_pct=float((optimization.get("data") or {}).get("risk_free_pct", 4.0)),
    )
    parts.append("### What the holdout is actually worth")
    parts.append("")
    parts.append(
        f"The pooled sum above (**{econ['pooled_sum_pct']:+.3f}%**) adds per-trade percentages "
        f"across {econ['symbols']} symbols, so it is not a return. Compounding each symbol and "
        f"weighting them equally gives **{econ['equal_weight_pct']:+.3f}%** over "
        f"{econ['days']} days, or **{econ['annualized_pct']:+.2f}% annualized** — "
        f"{abs(econ['excess_vs_risk_free_pp']):.2f} pp "
        f"{'above' if econ['excess_vs_risk_free_pp'] > 0 else 'below'} the "
        f"{econ['risk_free_pct']:.0f}% risk-free rate. The pooled figure is "
        f"{econ['overstatement_ratio']:.1f}x the equal-weight one."
    )
    parts.append("")
    parts.append(
        f"Slippage cost **{econ['slippage_drag_pct']:.3f} pp** of the pooled total "
        f"({econ['slippage_share_of_pre_cost_pct']:.1f}% of the pre-cost sum of "
        f"{econ['pre_cost_sum_pct']:+.3f}%), and "
        f"{econ['positive_symbols']}/{econ['symbols']} symbols compounded positive."
    )
    parts.append("")
    parts.append(
        "**Concentration.** Dropping one symbol and re-weighting the rest equally shows how much "
        "of the result is carried by how few names. A strategy whose edge survives only with its "
        "best symbol included has not been shown to generalize across the universe it trades."
    )
    parts.append("")
    parts.append(
        _md_table(
            ["Excluded symbol", "Equal-weight % (9 symbols)", "Annualized %", "Clears 4% hurdle"],
            [
                [row["symbol"], f"{row['equal_weight_pct']:+.3f}", f"{row['annualized_pct']:+.2f}",
                 "yes" if row["clears_hurdle"] else "no"]
                for row in econ["leave_one_out"]
            ],
        )
    )
    parts.append("")

    # ── per symbol ──────────────────────────────────────────────────────────
    control_holdout = optimization["controls"]["fixed_long_15_30_to_15_59"]["holdout"]
    control_symbols = control_holdout.get("symbol_stats", {})
    parts.append("## Per-symbol holdout (vs. fixed 15:30→15:59 long control)")
    parts.append("")
    by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        by_symbol[trade["symbol"]].append(trade)
    rows = []
    for symbol in UNIVERSE:
        b = _bucket(by_symbol.get(symbol, []))
        ctrl = control_symbols.get(symbol, {})
        rows.append([
            symbol,
            b["trades"],
            b["avg_trade_return_pct"],
            b["profit_factor"],
            b["win_rate_pct"],
            ctrl.get("avg_trade_return_pct", "-"),
        ])
    parts.append(_md_table(
        ["Symbol", "Trades", "Avg %", "PF", "Win rate %", "Control avg %"], rows
    ))
    parts.append("")
    beats_control = sum(
        1 for sym in UNIVERSE
        if (by_symbol.get(sym) and (control_symbols.get(sym, {}).get("avg_trade_return_pct") or 0.0)
            < _bucket(by_symbol[sym])["avg_trade_return_pct"])
    )
    parts.append(
        f"The strategy beats the fixed-time long control on **{beats_control}/{len(UNIVERSE)}** symbols "
        "(control is long-only 15:30→close with the same complete sessions and slippage)."
    )
    parts.append("")

    # ── direction & signal kind ─────────────────────────────────────────────
    parts.append("## Direction and signal kind")
    parts.append("")
    rows = []
    for direction in ("LONG", "SHORT"):
        b = _bucket([t for t in trades if t["direction"] == direction])
        rows.append([direction, b["trades"], b["win_rate_pct"], b["avg_trade_return_pct"], b["profit_factor"]])
    parts.append(_md_table(["Direction", "Trades", "Win rate %", "Avg %", "PF"], rows))
    parts.append("")
    rows = []
    for kind in ("CLPS UP", "CLPS DOWN"):
        b = _bucket([t for t in trades if t["signal_kind"] == kind])
        rows.append([kind, b["trades"], b["win_rate_pct"], b["avg_trade_return_pct"], b["profit_factor"]])
    parts.append(_md_table(["Signal kind", "Trades", "Win rate %", "Avg %", "PF"], rows))
    parts.append("")

    # ── time-of-day ─────────────────────────────────────────────────────────
    parts.append("## Signal time-of-day (ET)")
    parts.append("")
    rows = []
    for lo, hi in ((15 * 60 + 30, 15 * 60 + 40), (15 * 60 + 40, 15 * 60 + 50), (15 * 60 + 50, 16 * 60)):
        b = _bucket([t for t in trades if lo <= t["_signal_minute"] < hi])
        label = f"{lo // 60:02d}:{lo % 60:02d}–{hi // 60:02d}:{hi % 60:02d}"
        rows.append([label, b["trades"], b["win_rate_pct"], b["avg_trade_return_pct"], b["profit_factor"]])
    parts.append(_md_table(["Signal bucket", "Trades", "Win rate %", "Avg %", "PF"], rows))
    parts.append("")

    # ── weekday & month ─────────────────────────────────────────────────────
    parts.append("## Weekday and month")
    parts.append("")
    rows = []
    for weekday in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday"):
        b = _bucket([t for t in trades if t["_weekday"] == weekday])
        rows.append([weekday, b["trades"], b["win_rate_pct"], b["avg_trade_return_pct"], b["profit_factor"]])
    parts.append(_md_table(["Weekday", "Trades", "Win rate %", "Avg %", "PF"], rows))
    parts.append("")
    rows = []
    for month in sorted({t["_month"] for t in trades}):
        b = _bucket([t for t in trades if t["_month"] == month])
        rows.append([month, b["trades"], b["win_rate_pct"], b["avg_trade_return_pct"], b["profit_factor"], b["net_sum_return_pct"]])
    parts.append(_md_table(["Month", "Trades", "Win rate %", "Avg %", "PF", "Net sum %"], rows))
    parts.append("")

    # ── volatility context ──────────────────────────────────────────────────
    parts.append("## Prior-20-day realized-volatility context")
    parts.append("")
    vol_trades = [t for t in trades if t["_prior_20d_vol_pct"] is not None]
    vol_trades.sort(key=lambda t: t["_prior_20d_vol_pct"])
    if vol_trades:
        third = max(1, len(vol_trades) // 3)
        rows = []
        for label, bucket in (
            ("Low vol (bottom third)", vol_trades[:third]),
            ("Mid vol", vol_trades[third : 2 * third]),
            ("High vol (top third)", vol_trades[2 * third :]),
        ):
            b = _bucket(bucket)
            rows.append([label, b["trades"], b["win_rate_pct"], b["avg_trade_return_pct"], b["profit_factor"]])
        parts.append(_md_table(["Bucket", "Trades", "Win rate %", "Avg %", "PF"], rows))
    else:
        parts.append("No trade had enough prior daily history for a volatility context.")
    parts.append("")

    # ── cross-symbol correlation ────────────────────────────────────────────
    parts.append("## Same-day loss clustering (cross-symbol correlation)")
    parts.append("")
    symbol_daily: dict[str, dict[date, float]] = defaultdict(dict)
    for trade in trades:
        symbol_daily[trade["symbol"]][date.fromisoformat(trade["_day"])] = trade["net_return_pct"]
    days_with_data = sorted({d for m in symbol_daily.values() for d in m})
    active_symbols = [sym for sym in UNIVERSE if symbol_daily[sym]]
    if len(active_symbols) >= 3 and len(days_with_data) >= 3:
        corr_sum = 0.0
        corr_count = 0
        for i, a in enumerate(active_symbols):
            for b in active_symbols[i + 1 :]:
                common = sorted(set(symbol_daily[a]) & set(symbol_daily[b]))
                if len(common) < 5:
                    continue
                xa = [symbol_daily[a][d] for d in common]
                xb = [symbol_daily[b][d] for d in common]
                ma, mb = sum(xa) / len(xa), sum(xb) / len(xb)
                cov = sum((x - ma) * (y - mb) for x, y in zip(xa, xb)) / len(xa)
                sa = math.sqrt(sum((x - ma) ** 2 for x in xa) / len(xa))
                sb = math.sqrt(sum((y - mb) ** 2 for y in xb) / len(xb))
                if sa > 0 and sb > 0:
                    corr_sum += cov / (sa * sb)
                    corr_count += 1
        avg_corr = round(corr_sum / corr_count, 4) if corr_count else None
        if avg_corr is None:
            parts.append("Insufficient symbol-pair overlap for a correlation estimate.")
        else:
            clustering_note = (
                "Low clustering: symbol losses are not usually the same day."
                if avg_corr < 0.1
                else "Same-day P&L moves together; portfolio drawdowns can stack."
            )
            parts.append(
                f"Average pairwise correlation of per-symbol daily P&L over {corr_count} symbol pairs: "
                f"**{avg_corr}**. {clustering_note}"
            )
        # Days where a majority of the symbols that traded that day were negative.
        losing_days = []
        for day in days_with_data:
            present = [sym for sym in active_symbols if day in symbol_daily[sym]]
            if len(present) >= 4 and sum(1 for sym in present if symbol_daily[sym][day] < 0) > len(present) / 2:
                losing_days.append(day)
        if losing_days:
            parts.append(
                f"**{len(losing_days)}** days had a majority of traded symbols negative; "
                f"most recent: {', '.join(d.isoformat() for d in losing_days[-5:])}."
            )
    else:
        parts.append("Insufficient data for correlation.")
    parts.append("")

    # ── slippage & holding ──────────────────────────────────────────────────
    parts.append("## Slippage and holding period")
    parts.append("")
    gross_sum = sum(t["gross_return_pct"] for t in trades)
    net_sum = sum(t["net_return_pct"] for t in trades)
    parts.append(
        f"One-tick adverse slippage each side costs **{round(gross_sum - net_sum, 3)} pp** pooled "
        f"({round((gross_sum - net_sum) / len(trades), 5)} pp/trade) on {len(trades)} trades."
    )
    parts.append("")
    rows = []
    for lo, hi in ((0, 10), (10, 20), (20, 30)):
        b = _bucket([t for t in trades if lo <= t["bars_held"] < hi])
        rows.append([f"{lo}–{hi}", b["trades"], b["win_rate_pct"], b["avg_trade_return_pct"], b["profit_factor"]])
    parts.append(_md_table(["Bars held", "Trades", "Win rate %", "Avg %", "PF"], rows))
    parts.append("")

    # ── daily worst days ────────────────────────────────────────────────────
    parts.append("## Worst and best days (pooled daily P&L)")
    parts.append("")
    ranked_days = sorted(ordered_days, key=lambda d: daily_pnl[d])
    rows = []
    for label, bucket in (
        ("Worst 5", ranked_days[:5]),
        ("Best 5", ranked_days[-5:][::-1]),
    ):
        for d in bucket:
            rows.append([label, d.isoformat(), round(daily_pnl[d], 4)])
    parts.append(_md_table(["Rank", "Date", "Pooled daily %"], rows))
    parts.append("")

    # ── caveats ─────────────────────────────────────────────────────────────
    parts.append("## Caveats")
    parts.append("")
    for caveat in (
        f"{len(trades)} holdout trades over {len(UNIVERSE)} symbols is still a short, regime-specific sample; per-cell counts can be small.",
        "Pooled daily P&L is a sum of independent per-share returns, not a compounded portfolio.",
        "Results are an OHLCV reconstruction of a Pine strategy, not live-trading evidence.",
    ):
        parts.append(f"- {caveat}")
    parts.append("")

    report_md = "\n".join(parts)
    REPORT_MD.write_text(report_md, encoding="utf-8")

    detail: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "family": FAMILY,
        "params": params,
        "summary": s,
        "daily": {
            "days": [d.isoformat() for d in ordered_days],
            "pooled_pnl_pct": [round(daily_pnl[d], 6) for d in ordered_days],
            "curve": curve,
            "streaks": streaks,
        },
        "per_symbol": {sym: _bucket(by_symbol[sym]) for sym in UNIVERSE},
        "per_direction": {d: _bucket([t for t in trades if t["direction"] == d]) for d in ("LONG", "SHORT")},
        "per_signal_kind": {k: _bucket([t for t in trades if t["signal_kind"] == k]) for k in ("CLPS UP", "CLPS DOWN")},
        "per_time_bucket": {
            f"{lo // 60:02d}:{lo % 60:02d}–{hi // 60:02d}:{hi % 60:02d}": _bucket(
                [t for t in trades if lo <= t["_signal_minute"] < hi]
            )
            for lo, hi in ((930, 940), (940, 950), (950, 960))
        },
        "per_weekday": {w: _bucket([t for t in trades if t["_weekday"] == w]) for w in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")},
        "per_month": {m: _bucket([t for t in trades if t["_month"] == m]) for m in sorted({t["_month"] for t in trades})},
        "per_vol_bucket": {
            label: _bucket(bucket)
            for label, bucket in (("low", vol_trades[:third]), ("mid", vol_trades[third : 2 * third]), ("high", vol_trades[2 * third :]))
        } if vol_trades else {},
        "slippage_pooled_pp": round(gross_sum - net_sum, 6),
        # The pooled figures above are signal measurements, not returns. This block carries the
        # equal-weight compounded translation, its annualized value against the risk-free rate,
        # and the leave-one-out concentration check, so a consumer of the JSON is not left to
        # rederive them or to mistake a ten-symbol sum for a return.
        "holdout_economics": econ,
        "trades": sorted(trades, key=lambda t: t["entry_time"]),
    }
    REPORT_JSON.write_text(json.dumps(detail, indent=2, sort_keys=True), encoding="utf-8")

    print(f"wrote {REPORT_MD}")
    print(f"wrote {REPORT_JSON}")
    print(f"trades={s['trades']} avg={s['avg_trade_return_pct']} pf={s['profit_factor']} "
          f"win_rate={s['win_rate_pct']} days={len(ordered_days)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
