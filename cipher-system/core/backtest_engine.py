"""Event-driven backtester for bar-based Cipher strategies.

Scope is deliberately narrow: this backtests signals derived from OHLCV bars only
(the Obsidian EOD detector's collapse / coil-release / flip events). It does NOT
backtest GEX or cluster strategies, and that is a data constraint, not an
oversight — see `data_availability_note()`.

Design choices that exist to stop the backtest lying:

  * Signals are evaluated on bar i, entries fill on bar i+1's open. Filling on the
    signal bar's close uses information that was not available when the signal
    formed, which is the single most common way an intraday backtest manufactures
    edge.
  * Stops and targets are checked against the bar's high/low, not its close, and
    when both are touched in the same bar the STOP is assumed first. Real fills
    resolve intrabar and assuming the favourable one inflates results.
  * A per-bar cost in basis points is charged on entry and exit. Equity trading is
    cheap; the same strategy expressed in options is not, so the default is
    deliberately non-zero.
  * Walk-forward with a locked holdout is first-class, because with six setup
    families and a dozen tunable parameters, in-sample optimisation will find
    something regardless of whether an edge exists.

Research-only. Simulated fills over historical bars. Places no orders.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone


# Per side, in basis points of notional. This is a fallback, not a finding: it is
# the number every verdict in docs/backtest-findings.md turns on, and a guess in
# that position is not defensible. `core/execution_cost.py` measures it from the
# captured quote corpus; pass that profile as `cost_profile=` to charge the
# measured per-symbol half-spread instead. Measurement over 2026-07-22..07-31 put
# the median liquid-name half-spread at ~1.0bp, so this fallback is conservative
# for large caps and optimistic for wider names such as AMD (3.7bp).
DEFAULT_COST_BPS = 2.0
DEFAULT_STOP_ATR = 1.0
DEFAULT_TARGET_ATR = 1.5
DEFAULT_MAX_HOLD_BARS = 24


def _cost_for(symbol: str, fallback: float, profile: dict | None) -> float:
    """Per-symbol half-spread when a measured profile is supplied, else `fallback`.

    With no profile this returns `fallback` unchanged, so every existing caller
    and every recorded result keeps its exact prior behaviour.
    """
    if not profile:
        return fallback
    try:
        from . import execution_cost
    except ImportError:  # core/ has no __init__.py; app.py imports these flat
        import execution_cost
    value, _provenance = execution_cost.equity_half_spread_bps(
        symbol, profile=profile, fallback=fallback
    )
    return value


def data_availability_note() -> dict:
    """What can and cannot honestly be backtested with the data on disk.

    Kept in code rather than a README so it travels with the engine.
    """
    return {
        "bar_strategies": "backtestable — Alpaca serves ~2500 daily bars (back to 2016) "
                          "and ~13 months of 15-minute bars, and the Obsidian detector "
                          "needs only OHLCV. An earlier 288-bar figure was the window the "
                          "driver requested, not a feed limit.",
        "gex_cluster_strategies": "NOT backtestable at present. GEX = gamma x open "
                                  "interest, so it needs point-in-time OI. As of "
                                  "2026-08-08 the GCP VM's data/gex_history.sqlite "
                                  "holds 12 capture days (2026-07-22..08-07, 42,752 "
                                  "snapshots over 545 tickers) against the 60 the "
                                  "evidence gate asks for. Backtesting clusters "
                                  "against today's OI over past prices is lookahead "
                                  "bias — which is exactly what core/strategy_backtest.py "
                                  "does, and why its results are not usable.",
        "remedy": "cipher-gex.service captures daily on the VM and the count rises on "
                  "its own. Nothing here can be accelerated by back-filling: a missed "
                  "session's OI cannot be reconstructed from any vendor.",
    }


@dataclass
class Trade:
    symbol: str
    setup: str
    direction: str              # LONG | SHORT
    entry_time: str
    entry_price: float
    exit_time: str | None = None
    exit_price: float | None = None
    exit_reason: str | None = None
    bars_held: int = 0
    return_pct: float = 0.0
    mae_pct: float = 0.0        # max adverse excursion
    mfe_pct: float = 0.0        # max favourable excursion
    # Bar index the signal was evaluated on. Filter-mode needs it to look up the
    # detector's state at entry; a dataclass field rather than a stuck-on attribute
    # so asdict() carries it into the JSON report.
    entry_index: int | None = None


@dataclass
class BacktestResult:
    strategy: str
    symbols: list[str]
    trades: list[Trade] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    params: dict = field(default_factory=dict)
    caveat: str = (
        "Simulated fills on historical bars. Next-bar-open entries, stop-before-target "
        "intrabar assumption, costs charged both sides. Not trade advice."
    )

    def to_dict(self):
        d = asdict(self)
        d["trades"] = [asdict(t) for t in self.trades]
        return d


def _atr(bars, length=14):
    out = [None] * len(bars)
    acc = None
    for i, b in enumerate(bars):
        h, l = float(b["high"]), float(b["low"])
        if i == 0:
            tr = h - l
        else:
            pc = float(bars[i - 1]["close"])
            tr = max(h - l, abs(h - pc), abs(l - pc))
        acc = tr if acc is None else (acc * (length - 1) + tr) / length
        out[i] = acc
    return out


# Fallback only. The detector now reports `setup_direction` per bar, which is the
# authority: "MOMENTUM PUSH" is the label for a release in EITHER direction, so a
# name lookup gets roughly half of those trades backwards. This table is kept for
# states produced by older detector versions that lack the field.
_LONG_SETUPS = {"FLOOR BOUNCE", "BREAKOUT CONTINUATION"}
_SHORT_SETUPS = {"REJECTION REVERSAL", "CEILING REJECTION", "BREAKDOWN CONTINUATION"}


def _direction_of(state, setup: str) -> str | None:
    bias = (getattr(state, "setup_direction", "") or "").upper()
    if bias == "BULLISH":
        return "LONG"
    if bias == "BEARISH":
        return "SHORT"
    if setup in _LONG_SETUPS:
        return "LONG"
    if setup in _SHORT_SETUPS:
        return "SHORT"
    return None


def _simulate(bars, atr, i, symbol, setup, direction, *,
              stop_atr, target_atr, max_hold_bars, cost_bps):
    """Simulate one trade signalled on bar i. Returns (Trade, exit_idx).

    Shared by the detector backtest and the random-entry control so the two are
    compared under identical fill, stop, cost and holding rules — any difference
    between them is then attributable to entry timing alone.
    """
    n = len(bars)
    entry_idx = i + 1
    entry = float(bars[entry_idx]["open"])
    a = atr[i]
    if direction == "LONG":
        stop, target = entry - stop_atr * a, entry + target_atr * a
    else:
        stop, target = entry + stop_atr * a, entry - target_atr * a

    trade = Trade(
        symbol=symbol, setup=setup, direction=direction,
        entry_time=str(bars[entry_idx].get("time")), entry_price=entry,
    )
    mae = mfe = 0.0
    exit_idx = None
    for j in range(entry_idx, min(entry_idx + max_hold_bars, n)):
        hi, lo = float(bars[j]["high"]), float(bars[j]["low"])
        if direction == "LONG":
            mfe = max(mfe, (hi - entry) / entry * 100)
            mae = min(mae, (lo - entry) / entry * 100)
            hit_stop, hit_tgt = lo <= stop, hi >= target
        else:
            mfe = max(mfe, (entry - lo) / entry * 100)
            mae = min(mae, (entry - hi) / entry * 100)
            hit_stop, hit_tgt = hi >= stop, lo <= target
        # Both touched in one bar: assume the stop filled first. Assuming the
        # target instead is the classic way to invent edge.
        if hit_stop:
            trade.exit_price, trade.exit_reason, exit_idx = stop, "stop", j
            break
        if hit_tgt:
            trade.exit_price, trade.exit_reason, exit_idx = target, "target", j
            break
    if exit_idx is None:
        exit_idx = min(entry_idx + max_hold_bars, n) - 1
        trade.exit_price = float(bars[exit_idx]["close"])
        trade.exit_reason = "time"

    trade.exit_time = str(bars[exit_idx].get("time"))
    trade.bars_held = exit_idx - entry_idx
    gross = ((trade.exit_price - entry) / entry * 100) if direction == "LONG" \
        else ((entry - trade.exit_price) / entry * 100)
    trade.return_pct = round(gross - (cost_bps / 100.0) * 2, 4)
    trade.mae_pct, trade.mfe_pct = round(mae, 4), round(mfe, 4)
    return trade, exit_idx


def run_signals(
    bars_by_symbol: dict[str, list[dict]],
    signal_fn,
    *,
    strategy: str = "signals",
    stop_atr: float = DEFAULT_STOP_ATR,
    target_atr: float = DEFAULT_TARGET_ATR,
    max_hold_bars: int = DEFAULT_MAX_HOLD_BARS,
    cost_bps: float = DEFAULT_COST_BPS,
    cost_profile: dict | None = None,
    params: dict | None = None,
    min_bars: int = 120,
) -> BacktestResult:
    """Simulate any strategy that can name its entry bars.

    `signal_fn(symbol, bars)` returns an iterable of `(index, direction, tag)`:
    the bar the signal was evaluated on, "LONG" or "SHORT", and a label carried
    through to `Trade.setup` for per-setup breakdowns.

    This exists so that every strategy in the repository can be measured by the
    same code. The five older engines each carry their own simulate-and-score
    half, and those halves disagree with this one on the things that decide a
    verdict: they charge no transaction cost, several stop after a fixed number of
    trades, and one reads today's open interest while trading past bars. Their
    entry logic is worth keeping and their measurement is not, so an adapter
    reduces each to a `signal_fn` and the answer comes from here.

    Entries fill on the next bar's open, stops resolve before targets intrabar,
    and cost is charged both sides — identical to `run_backtest`, because it is
    now the same code path.

    Signals landing inside an open position are skipped rather than stacked, so a
    strategy that fires continuously cannot accumulate correlated copies of one
    idea and report them as independent evidence.
    """
    result = BacktestResult(
        strategy=strategy, symbols=sorted(bars_by_symbol), params=params or {},
    )

    for symbol, bars in bars_by_symbol.items():
        if len(bars) < min_bars:
            continue
        signals = signal_fn(symbol, bars)
        if not signals:
            continue
        atr = _atr(bars)
        n = len(bars)
        blocked_until = -1

        for index, direction, tag in sorted(signals, key=lambda s: s[0]):
            if index <= blocked_until or index >= n - 1 or index < 0:
                continue
            if direction not in ("LONG", "SHORT"):
                continue
            if atr[index] is None or atr[index] <= 0:
                continue
            trade, exit_idx = _simulate(
                bars, atr, index, symbol, tag, direction,
                stop_atr=stop_atr, target_atr=target_atr,
                max_hold_bars=max_hold_bars,
                cost_bps=_cost_for(symbol, cost_bps, cost_profile),
            )
            trade.entry_index = index
            result.trades.append(trade)
            blocked_until = exit_idx

    result.stats = summarize(result.trades)
    return result


def run_backtest(
    bars_by_symbol: dict[str, list[dict]],
    *,
    strategy: str = "obsidian",
    setups: set[str] | None = None,
    stop_atr: float = DEFAULT_STOP_ATR,
    target_atr: float = DEFAULT_TARGET_ATR,
    max_hold_bars: int = DEFAULT_MAX_HOLD_BARS,
    cost_bps: float = DEFAULT_COST_BPS,
    cost_profile: dict | None = None,
    detector_params: dict | None = None,
    invert: bool = False,
    states_by_symbol: dict[str, list] | None = None,
) -> BacktestResult:
    """Run the Obsidian detector over each symbol's bars and simulate the trades.

    `invert` flips every trade's direction. It is a DIAGNOSTIC, not a strategy: if
    the inverted run beats the control while the normal run loses to it by a
    similar margin, the setup-to-direction mapping is backwards. If instead both
    directions lose, the signal carries no directional information at all and the
    exit rule is doing the damage.
    """
    try:
        from . import obsidian_eod
    except ImportError:
        import obsidian_eod

    params = {
        "strategy": strategy, "setups": sorted(setups) if setups else "all",
        "stop_atr": stop_atr, "target_atr": target_atr,
        "max_hold_bars": max_hold_bars, "cost_bps": cost_bps,
        "detector": detector_params or {"mode": "Full Session"},
        "invert": invert,
    }
    def _signals(symbol, bars):
        # Detector output depends only on the bars and detector params, never on
        # the exit rule, so an exit sweep can compute it once and reuse it.
        if states_by_symbol is not None and symbol in states_by_symbol:
            states = states_by_symbol[symbol]
        else:
            states, _ = obsidian_eod.compute(bars, detector_params or {"mode": "Full Session"})
        out = []
        for index, state in enumerate(states or []):
            setup = (state.setup or "").strip()
            if not setup or (setups and setup not in setups):
                continue
            direction = _direction_of(state, setup)
            if invert and direction is not None:
                direction = "SHORT" if direction == "LONG" else "LONG"
            if direction is None:
                continue
            out.append((index, direction, setup))
        return out

    return run_signals(
        bars_by_symbol, _signals, strategy=strategy,
        stop_atr=stop_atr, target_atr=target_atr, max_hold_bars=max_hold_bars,
        cost_bps=cost_bps, cost_profile=cost_profile, params=params,
    )

    result.stats = summarize(result.trades)
    return result


def summarize(trades: list[Trade]) -> dict:
    if not trades:
        return {"trades": 0}
    rets = [t.return_pct for t in trades]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    total = sum(rets)
    mean = total / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / len(rets)
    sd = var ** 0.5
    # Equity curve on equal-weight, one-at-a-time sizing.
    equity, peak, max_dd = 0.0, 0.0, 0.0
    for r in rets:
        equity += r
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
    gross_win = sum(wins)
    gross_loss = -sum(losses)
    by_setup: dict[str, list[float]] = {}
    for t in trades:
        by_setup.setdefault(t.setup, []).append(t.return_pct)
    return {
        "trades": len(trades),
        "win_rate": round(100 * len(wins) / len(rets), 1),
        "total_return_pct": round(total, 2),
        "avg_return_pct": round(mean, 4),
        "median_return_pct": round(sorted(rets)[len(rets) // 2], 4),
        "stdev_pct": round(sd, 4),
        # Per-trade Sharpe-like ratio; NOT annualised, since trade spacing is irregular.
        "return_per_unit_risk": round(mean / sd, 3) if sd else None,
        "profit_factor": round(gross_win / gross_loss, 3) if gross_loss else None,
        "max_drawdown_pct": round(max_dd, 2),
        "avg_bars_held": round(sum(t.bars_held for t in trades) / len(trades), 1),
        "exit_mix": {
            r: sum(1 for t in trades if t.exit_reason == r) for r in ("stop", "target", "time")
        },
        "by_setup": {
            k: {"n": len(v), "avg_pct": round(sum(v) / len(v), 4),
                "win_rate": round(100 * sum(1 for x in v if x > 0) / len(v), 1)}
            for k, v in sorted(by_setup.items())
        },
    }


def run_control(
    reference: BacktestResult,
    bars_by_symbol: dict[str, list[dict]],
    *,
    seed: int = 0,
    repeats: int = 20,
    stop_atr: float = DEFAULT_STOP_ATR,
    target_atr: float = DEFAULT_TARGET_ATR,
    max_hold_bars: int = DEFAULT_MAX_HOLD_BARS,
    cost_bps: float = DEFAULT_COST_BPS,
    cost_profile: dict | None = None,
    **_ignored,
) -> dict:
    """Random-entry control matched to `reference` trade-for-trade.

    This is the question the raw backtest cannot answer. Over 2016-2026 a long
    trade with a 1.5-ATR target beats a short one whether or not any signal was
    involved, because the market rose. So for every detector trade we draw a
    random entry bar in the SAME symbol with the SAME direction, hold under the
    same stop/target/time rules, and repeat the draw many times. If the detector
    is only picking direction, the control matches it and the edge is zero.

    Returns the control's mean stats plus the detector-minus-control deltas.
    """
    import random

    per_symbol_dir: dict[tuple[str, str], int] = {}
    for t in reference.trades:
        per_symbol_dir[(t.symbol, t.direction)] = per_symbol_dir.get((t.symbol, t.direction), 0) + 1

    rng = random.Random(seed)
    runs = []
    for _ in range(repeats):
        trades: list[Trade] = []
        for (symbol, direction), count in per_symbol_dir.items():
            bars = bars_by_symbol.get(symbol)
            if not bars or len(bars) < 120:
                continue
            atr = _atr(bars)
            # Entries only where the detector could also have fired: past the
            # 100-bar warmup and with room for the holding period.
            lo, hi = 120, len(bars) - max_hold_bars - 2
            if hi <= lo:
                continue
            for _ in range(count):
                i = rng.randint(lo, hi)
                if atr[i] is None or atr[i] <= 0:
                    continue
                trade, _exit = _simulate(
                    bars, atr, i, symbol, "RANDOM", direction,
                    stop_atr=stop_atr, target_atr=target_atr,
                    max_hold_bars=max_hold_bars,
                cost_bps=_cost_for(symbol, cost_bps, cost_profile),
                )
                trades.append(trade)
        if trades:
            runs.append(summarize(trades))

    if not runs:
        return {"control": None, "note": "control produced no trades"}

    def avg(key):
        vals = [r[key] for r in runs if r.get(key) is not None]
        return round(sum(vals) / len(vals), 4) if vals else None

    control = {
        "repeats": len(runs),
        "trades_per_run": round(sum(r["trades"] for r in runs) / len(runs), 1),
        "win_rate": avg("win_rate"),
        "avg_return_pct": avg("avg_return_pct"),
        "median_return_pct": avg("median_return_pct"),
        "profit_factor": avg("profit_factor"),
    }
    # Where the control's own run-to-run spread sits, so a small delta can be
    # read against the noise floor rather than treated as a result.
    means = sorted(r["avg_return_pct"] for r in runs)
    control["avg_return_pct_range"] = [means[0], means[-1]]

    ref = reference.stats
    delta = {}
    for k in ("win_rate", "avg_return_pct", "median_return_pct", "profit_factor"):
        a, b = ref.get(k), control.get(k)
        delta[k] = round(a - b, 4) if (a is not None and b is not None) else None
    # The detector beats chance only if its mean return clears the best random draw.
    beats = (
        ref.get("avg_return_pct") is not None
        and ref["avg_return_pct"] > means[-1]
    )
    return {
        "control": control,
        "detector_minus_control": delta,
        "detector_beats_control_range": beats,
    }


def baseline_trades(
    bars_by_symbol: dict[str, list[dict]],
    *,
    entry_every: int = 12,
    direction: str = "LONG",
    stop_atr: float = DEFAULT_STOP_ATR,
    target_atr: float = DEFAULT_TARGET_ATR,
    max_hold_bars: int = DEFAULT_MAX_HOLD_BARS,
    cost_bps: float = DEFAULT_COST_BPS,
    cost_profile: dict | None = None,
) -> list[Trade]:
    """A deliberately dumb base strategy: enter every `entry_every` bars.

    Filter-mode needs a base to gate, and the base must carry no edge of its own —
    otherwise an improvement could come from the base rather than the filter. A
    fixed-cadence entry is the cleanest such base: it has no view on price, so any
    difference between its filtered and unfiltered partitions is attributable to
    the filter.
    """
    trades: list[Trade] = []
    for symbol, bars in bars_by_symbol.items():
        if len(bars) < 120:
            continue
        atr = _atr(bars)
        i = 120
        n = len(bars)
        while i < n - max_hold_bars - 2:
            if atr[i] and atr[i] > 0:
                trade, exit_idx = _simulate(
                    bars, atr, i, symbol, "BASELINE", direction,
                    stop_atr=stop_atr, target_atr=target_atr,
                    max_hold_bars=max_hold_bars,
                cost_bps=_cost_for(symbol, cost_bps, cost_profile),
                )
                trade.entry_index = i
                trades.append(trade)
                i = max(exit_idx + 1, i + entry_every)
            else:
                i += entry_every
    return trades


def run_filter(
    bars_by_symbol: dict[str, list[dict]],
    *,
    signal_states: dict[str, list] | None = None,
    detector_params: dict | None = None,
    base_trades: list[Trade] | None = None,
    lookback_bars: int = 6,
    control_repeats: int = 20,
    seed: int = 0,
    **base_kw,
) -> dict:
    """Evaluate a signal as a FILTER on a base strategy, not as an entry trigger.

    Every measurement in this engine so far asked the hardest possible question:
    can the signal, alone, beat random entry on timing, direction and selection at
    once? Three strategies failed that test — the Obsidian detector on 0 of 36 exit
    configurations, the Structural Fib rules at 84.5% against a published 98%. But
    a failure there cannot distinguish "this signal carries no information" from
    "this signal carries information that is not an entry trigger", and those call
    for opposite decisions.

    Filter-mode asks the weaker, likelier question instead: given trades you were
    going to take anyway, does the signal's state at entry separate the good ones
    from the bad? Trades are partitioned by whether the detector fired within
    `lookback_bars` before entry, and the partitions are compared to each other.

    Each partition gets its OWN matched random control, because partitioning is a
    multiple-comparison machine: split any trade set enough ways and one slice
    looks good. A partition is only interesting if it clears its own control's best
    draw, the same bar `run_control` already applies.
    """
    if base_trades is None:
        base_trades = baseline_trades(bars_by_symbol, **{
            k: v for k, v in base_kw.items()
            if k in {"entry_every", "direction", "stop_atr", "target_atr",
                     "max_hold_bars", "cost_bps", "cost_profile"}
        })
    if not base_trades:
        return {"error": "base strategy produced no trades"}

    states = signal_states
    if states is None:
        try:
            from . import obsidian_eod
        except ImportError:
            import obsidian_eod
        states = {}
        for symbol, bars in bars_by_symbol.items():
            if len(bars) < 120:
                continue
            computed, _ = obsidian_eod.compute(bars, detector_params or {"mode": "EOD Focus"})
            if computed:
                states[symbol] = computed

    def signal_at(symbol: str, index: int) -> str:
        """Signal state in the `lookback_bars` window ending at the entry bar.

        Looks BACKWARD only. A forward-looking window would leak the outcome into
        the partition and manufacture separation out of nothing.
        """
        rows = states.get(symbol) or []
        if not rows:
            return "none"
        lo = max(0, index - lookback_bars)
        for j in range(lo, min(index + 1, len(rows))):
            setup = (getattr(rows[j], "setup", "") or "").strip()
            if setup:
                bias = (getattr(rows[j], "setup_direction", "") or "").upper()
                return "bullish" if bias == "BULLISH" else "bearish" if bias == "BEARISH" else "fired"
        return "none"

    partitions: dict[str, list[Trade]] = {}
    for trade in base_trades:
        index = getattr(trade, "entry_index", None)
        key = signal_at(trade.symbol, index) if index is not None else "none"
        partitions.setdefault(key, []).append(trade)

    base_stats = summarize(base_trades)
    report = {
        "base": base_stats,
        "lookback_bars": lookback_bars,
        "partitions": {},
        "caveat": (
            "Filter evaluation. Partitions are compared against their own matched "
            "random controls, not only against each other — partitioning inflates "
            "multiple-comparison risk."
        ),
    }
    for key, trades in sorted(partitions.items()):
        stats = summarize(trades)
        entry = {"stats": stats, "share_of_base": round(100.0 * len(trades) / len(base_trades), 1)}
        if len(trades) >= 20:
            holder = BacktestResult(strategy="filter", symbols=sorted(bars_by_symbol))
            holder.trades = trades
            holder.stats = stats
            control = run_control(
                holder, bars_by_symbol, seed=seed, repeats=control_repeats,
                **{k: v for k, v in base_kw.items()
                   if k in {"stop_atr", "target_atr", "max_hold_bars", "cost_bps",
                            "cost_profile"}},
            )
            entry["control"] = control.get("control")
            entry["beats_control_range"] = control.get("detector_beats_control_range")
            entry["vs_control"] = control.get("detector_minus_control")
        else:
            entry["note"] = f"only {len(trades)} trades — too few to control"
        # The question filter-mode exists to answer.
        if base_stats.get("avg_return_pct") is not None and stats.get("avg_return_pct") is not None:
            entry["lift_vs_base_pp"] = round(
                stats["avg_return_pct"] - base_stats["avg_return_pct"], 4
            )
        report["partitions"][key] = entry
    return report


def walk_forward(bars_by_symbol, *, folds=3, holdout_frac=0.25, signal_fn=None, **kw):
    """Split chronologically: a locked holdout, then folds over the remainder.

    The holdout is carved off FIRST and never touched by fold evaluation, so a
    parameter chosen by looking at fold results can still be checked against data
    that had no influence on it. With six setup families in play, an unsplit
    backtest will always find a flattering configuration.

    `signal_fn` selects the strategy. Without it this runs the Obsidian detector,
    as it always has; with it, any catalogued strategy gets the same locked-holdout
    treatment rather than only the one the engine was originally written for.
    """
    def _run(sub):
        if signal_fn is not None:
            return run_signals(sub, signal_fn, **kw)
        return run_backtest(sub, **kw)

    MIN_BARS = 120  # detector needs a 100-bar stdev window before it emits anything

    def slice_bars(frac_lo, frac_hi):
        out, dropped = {}, 0
        for sym, bars in bars_by_symbol.items():
            n = len(bars)
            lo, hi = int(n * frac_lo), int(n * frac_hi)
            if hi - lo >= MIN_BARS:
                out[sym] = bars[lo:hi]
            else:
                dropped += 1
        return out, dropped

    train_end = 1.0 - holdout_frac
    report = {"folds": [], "holdout": None, "holdout_frac": holdout_frac, "warnings": []}

    # Fail loudly rather than returning an empty report that reads like "no trades".
    shortest = min((len(b) for b in bars_by_symbol.values()), default=0)
    per_fold = int(shortest * train_end / max(folds, 1))
    if per_fold < MIN_BARS:
        max_folds = max(1, int(shortest * train_end / MIN_BARS))
        report["warnings"].append(
            f"insufficient history for {folds} folds: shortest series is {shortest} bars, "
            f"which gives {per_fold} per fold but the detector needs {MIN_BARS}. "
            f"Reduce to <= {max_folds} fold(s), lengthen the history, or use a finer timeframe."
        )
        folds = max_folds

    for f in range(folds):
        lo = train_end * f / folds
        hi = train_end * (f + 1) / folds
        sub, dropped = slice_bars(lo, hi)
        if not sub:
            report["warnings"].append(f"fold {f + 1} skipped: every symbol below {MIN_BARS} bars")
            continue
        r = _run(sub)
        report["folds"].append({
            "fold": f + 1, "range": [round(lo, 3), round(hi, 3)],
            "symbols": len(sub), "symbols_dropped": dropped, "stats": r.stats,
        })

    hold, dropped = slice_bars(train_end, 1.0)
    if hold:
        report["holdout"] = _run(hold).stats
        report["holdout_symbols"] = len(hold)
    else:
        report["warnings"].append(
            f"holdout skipped: {holdout_frac:.0%} of the shortest series is under {MIN_BARS} bars"
        )
    return report
