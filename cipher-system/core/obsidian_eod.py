"""Obsidian EOD Algo Detector — faithful Python port of the Pine v6 indicator.

This is a direct translation of the original TradingView script, not a
reinterpretation. Where Pine semantics are subtle the port follows Pine rather
than what would be more natural in Python:

  * `ta.stdev` is POPULATION stdev (ddof=0), not sample.
  * `ta.atr` / `ta.rma` use Wilder smoothing (alpha = 1/length), not an SMA.
  * `var` variables persist across bars and are mutated in evaluation order, so
    the per-bar loop below assigns in exactly the order the Pine source does.
  * `firedPrev` is captured BEFORE the `if signChange` block resets
    `firedThisRun` — the flip confirmations depend on that ordering, and
    computing it after would silently disable every flip signal.
  * Series indexing `x[n]` is the value n bars back; leading bars where a
    lookback is unavailable yield `na` and are treated as non-triggering.

Vocabulary note: the event names this module emits are the ones the real
product's Flash Agentic timeline uses ("Coiling, holding the setup",
"Momentum push", "Rejection reversal", "Structure flipped to …"), which is what
the indicator's coil / release / collapse / flip states correspond to.

Research-only: momentum-structure detection over public OHLCV. Not trade advice.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

# ── defaults mirroring the Pine inputs ───────────────────────────────────────
DEFAULTS = {
    # ① EOD mode
    "mode": "EOD Focus",          # or "Full Session"
    "close_hour": 16,
    "close_minute": 0,
    "arm_minutes": 30,
    "hot_minutes": 10,
    "boost_on": True,
    # ② momentum engine
    "fast_len": 8,
    "slow_len": 21,
    "sig_len": 5,
    # ③ collapse detection
    "sig_mult": 1.2,
    "clps_thresh": 0.60,
    "max_bars": 4,
    # ④ coil / release
    "coil_amp": 0.85,
    "coil_len": 8,
    "rel_window": 6,
    "rel_hist": 0.90,
    "rel_thrust": 1.5,
    "rel_vol_min": 1.30,
    # ⑤ trend context
    "trend_len": 150,
    "slope_bars": 10,
}

# US equity RTH, used to approximate Pine's `session.ismarket`.
_RTH_OPEN = (9, 30)
_RTH_CLOSE = (16, 0)
# Exchange timezone, matching the Pine input's default. Resolved with zoneinfo so the
# session clock stays correct across DST — a fixed UTC offset was silently an hour out
# for roughly half the year, which shifts the EOD window and hot zone.
_EXCHANGE_TZ = "America/New_York"
try:
    from zoneinfo import ZoneInfo

    _TZ = ZoneInfo(_EXCHANGE_TZ)
except Exception:  # pragma: no cover - platform without tzdata
    _TZ = None
_ET_OFFSET_FALLBACK_HOURS = -4


def _ema(values, length):
    out = [None] * len(values)
    if not values:
        return out
    alpha = 2.0 / (length + 1.0)
    acc = None
    for i, v in enumerate(values):
        if v is None:
            out[i] = acc
            continue
        acc = v if acc is None else alpha * v + (1 - alpha) * acc
        out[i] = acc
    return out


def _rma(values, length):
    """Wilder smoothing — what Pine's ta.rma (and therefore ta.atr) uses."""
    out = [None] * len(values)
    alpha = 1.0 / length
    acc = None
    for i, v in enumerate(values):
        if v is None:
            out[i] = acc
            continue
        acc = v if acc is None else alpha * v + (1 - alpha) * acc
        out[i] = acc
    return out


def _sma(values, length):
    out = [None] * len(values)
    run = 0.0
    for i, v in enumerate(values):
        run += v
        if i >= length:
            run -= values[i - length]
        if i >= length - 1:
            out[i] = run / length
    return out


def _stdev(values, length):
    """Population stdev over a rolling window (Pine's ta.stdev)."""
    out = [None] * len(values)
    for i in range(len(values)):
        if i < length - 1:
            continue
        window = values[i - length + 1 : i + 1]
        mean = sum(window) / length
        var = sum((x - mean) ** 2 for x in window) / length
        out[i] = math.sqrt(var)
    return out


def _highest(values, length):
    out = [None] * len(values)
    for i in range(len(values)):
        if i < length - 1:
            continue
        out[i] = max(values[i - length + 1 : i + 1])
    return out


def _true_range(highs, lows, closes):
    out = []
    for i in range(len(closes)):
        if i == 0:
            out.append(highs[i] - lows[i])
            continue
        prev = closes[i - 1]
        out.append(max(highs[i] - lows[i], abs(highs[i] - prev), abs(lows[i] - prev)))
    return out


def _parse_time(raw):
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass
class BarState:
    index: int
    time: str | None
    hist: float
    run_sign: float
    run_peak: float
    bars_from_peak: int
    collapse_pct: float
    significant: bool
    coiling: bool
    in_window: bool
    hot: bool
    events: list[str] = field(default_factory=list)
    setup: str = ""
    # Direction the setup implies. Needed because the setup NAME is not always
    # enough: "MOMENTUM PUSH" is the label for a release in either direction (an
    # up-release in a weak uptrend and a down-release in a weak downtrend both
    # land there), so anything acting on the name alone gets half of them backwards.
    setup_direction: str = ""      # BULLISH | BEARISH | ""


# Event vocabulary, matched to the real product's timeline wording.
EVENT_COIL = "Coiling, holding the setup"
EVENT_RELEASE_UP = "Momentum push"
EVENT_RELEASE_DOWN = "Momentum push (down)"
EVENT_COLLAPSE_DOWN = "Rejection reversal"
EVENT_COLLAPSE_UP = "Floor bounce"
EVENT_FLIP_UP = "Structure flipped to Bullish"
EVENT_FLIP_DOWN = "Structure flipped to Bearish"

# Setup vocabulary and IDs, both taken from the real product's own captures rather
# than invented: across 332 captured Flash/Flash-Agentic rows each name carried
# exactly one ID, with no exceptions (BREAKOUT CONTINUATION #1 n=13,
# BREAKDOWN CONTINUATION #2 n=79, MOMENTUM PUSH #4 n=76, CEILING REJECTION #6 n=18,
# REJECTION REVERSAL #7 n=78, FLOOR BOUNCE #9 n=68).
SETUP_IDS = {
    "BREAKOUT CONTINUATION": 1,
    "BREAKDOWN CONTINUATION": 2,
    "MOMENTUM PUSH": 4,
    "CEILING REJECTION": 6,
    "REJECTION REVERSAL": 7,
    "FLOOR BOUNCE": 9,
}


def _classify_setup(*, clps_down, clps_up, rel_up, rel_down, trend_up, trend_down):
    """Map a fired event to the real product's setup name.

    Uses the indicator's own A/B trend grading: a bull-momentum collapse that runs
    WITH a downtrend is the A-grade "rejection reversal"; the same collapse against
    the trend is the weaker "ceiling rejection". Likewise a release that agrees with
    the trend is a continuation, otherwise it is a plain momentum push.
    """
    if clps_down:
        return "REJECTION REVERSAL" if trend_down else "CEILING REJECTION"
    if clps_up:
        return "FLOOR BOUNCE"
    if rel_up:
        return "BREAKOUT CONTINUATION" if trend_up else "MOMENTUM PUSH"
    if rel_down:
        return "BREAKDOWN CONTINUATION" if trend_down else "MOMENTUM PUSH"
    return ""


def _setup_direction(*, clps_down, clps_up, rel_up, rel_down):
    """Direction implied by the event that fired, independent of its label.

    Mirrors the branch order of `_classify_setup` so the two never disagree. A
    collapse of bull momentum (clps_down) is bearish; a collapse of bear momentum
    (clps_up) is the bullish floor bounce.
    """
    if clps_down:
        return "BEARISH"
    if clps_up:
        return "BULLISH"
    if rel_up:
        return "BULLISH"
    if rel_down:
        return "BEARISH"
    return ""


def compute(bars, params=None):
    """Run the detector over a list of OHLCV bar dicts (oldest first).

    Each bar needs: time, open, high, low, close, volume.
    Returns (states, summary).
    """
    p = {**DEFAULTS, **(params or {})}
    n = len(bars)
    if n < 3:
        return [], {}

    closes = [float(b.get("close") or 0.0) for b in bars]
    highs = [float(b.get("high") or 0.0) for b in bars]
    lows = [float(b.get("low") or 0.0) for b in bars]
    opens = [float(b.get("open") or 0.0) for b in bars]
    volumes = [float(b.get("volume") or 0.0) for b in bars]
    times = [b.get("time") for b in bars]

    # ── core series ──────────────────────────────────────────────────────────
    fast = _ema(closes, p["fast_len"])
    slow = _ema(closes, p["slow_len"])
    macd = [
        (f - s) if (f is not None and s is not None) else None
        for f, s in zip(fast, slow)
    ]
    signal_line = _ema([m if m is not None else 0.0 for m in macd], p["sig_len"])
    hist = [
        (m - s) if (m is not None and s is not None) else 0.0
        for m, s in zip(macd, signal_line)
    ]

    hist_sd_raw = _stdev(hist, 100)
    hist_sd = [max(v, 1e-10) if v is not None else None for v in hist_sd_raw]

    atr_raw = _rma(_true_range(highs, lows, closes), 14)
    atr = [max(v, 1e-8) if v is not None else None for v in atr_raw]

    vol_sma = _sma(volumes, 20)
    vol_ratio = [
        (v / max(s, 1e-10)) if s else None for v, s in zip(volumes, vol_sma)
    ]

    trend_ema = _ema(closes, p["trend_len"])
    abs_hist = [abs(h) for h in hist]
    amp_series = _highest(abs_hist, p["coil_len"])

    # ── stateful pass (mirrors Pine's per-bar evaluation order) ─────────────
    run_sign = 0.0
    run_peak = 0.0
    run_peak_bar = 0
    fired_this_run = False
    last_coil_bar = -99999
    rel_fired = False
    # Entry anchor for progress-to-target: the close of the bar on which the current
    # setup fired. Derived from the real product's own cards — progress there is
    # clamp((spot - entry) / (target - entry), 0, 1), which reproduced 118 of 122
    # captured values to within 4 points (the clamp is why so many read 0%).
    setup_entry_price = None
    setup_entry_index = None
    active_setup = ""

    states: list[BarState] = []
    for i in range(n):
        sd = hist_sd[i]
        h = hist[i]

        # session clock
        t = _parse_time(times[i])
        in_rth = False
        mins_to_close = None
        if t is not None:
            if _TZ is not None:
                et = t.astimezone(_TZ)
            else:
                et = t.astimezone(timezone.utc) + timedelta(hours=_ET_OFFSET_FALLBACK_HOURS)
            m_now = et.hour * 60 + et.minute
            open_m = _RTH_OPEN[0] * 60 + _RTH_OPEN[1]
            close_m = _RTH_CLOSE[0] * 60 + _RTH_CLOSE[1]
            in_rth = open_m <= m_now <= close_m
            mins_to_close = p["close_hour"] * 60 + p["close_minute"] - m_now
        window_ok = bool(
            in_rth and mins_to_close is not None and 0 <= mins_to_close <= p["arm_minutes"]
        )
        hot = bool(
            in_rth and mins_to_close is not None and 0 <= mins_to_close <= p["hot_minutes"]
        )
        gate = p["mode"] == "Full Session" or window_ok

        boost = 0.85 if (p["boost_on"] and hot) else 1.0
        eff_sig = p["sig_mult"] * boost
        eff_clps = p["clps_thresh"] * boost

        # run-peak tracking
        sgn = 1.0 if h > 0 else (-1.0 if h < 0 else 0.0)
        h_prev = hist[i - 1] if i > 0 else 0.0
        sgn_prev = 1.0 if h_prev > 0 else (-1.0 if h_prev < 0 else 0.0)
        sign_change = sgn != sgn_prev

        # Captured BEFORE the reset below — flips depend on the prior run's state.
        fired_prev = fired_this_run

        if sign_change:
            run_sign = sgn
            run_peak = abs(h)
            run_peak_bar = i
            fired_this_run = False
        elif abs(h) > run_peak:
            run_peak = abs(h)
            run_peak_bar = i

        bars_from_peak = i - run_peak_bar
        collapse_pct = (1.0 - abs(h) / run_peak) if run_peak > 0 else 0.0
        significant = bool(sd is not None and run_peak >= eff_sig * sd)

        collapse_event = (
            significant
            and not fired_this_run
            and collapse_pct >= eff_clps
            and 0 < bars_from_peak <= p["max_bars"]
        )
        if collapse_event:
            fired_this_run = True

        te = trend_ema[i]
        te_prev = trend_ema[i - p["slope_bars"]] if i >= p["slope_bars"] else None
        trend_up = bool(te is not None and te_prev is not None and te > te_prev)
        trend_down = bool(te is not None and te_prev is not None and te < te_prev)

        clps_down = collapse_event and run_sign > 0
        clps_up = collapse_event and run_sign < 0
        conf_up = sign_change and sgn > 0 and fired_prev
        conf_down = sign_change and sgn < 0 and fired_prev

        # coil + coil-memory release
        amp = amp_series[i]
        amp_prev4 = amp_series[i - 4] if i >= 4 else None
        coiling = bool(
            amp is not None
            and sd is not None
            and amp < p["coil_amp"] * sd
            and (amp_prev4 is None or amp <= amp_prev4)
        )
        if coiling:
            last_coil_bar = i
            rel_fired = False

        since_coil = i - last_coil_bar
        recent_coil = 1 <= since_coil <= p["rel_window"]
        vr = vol_ratio[i]
        vol_ok = bool(vr is not None and vr >= p["rel_vol_min"])
        a = atr[i]

        rel_up = bool(
            not rel_fired and recent_coil and h > 0 and h >= h_prev and vol_ok and sd is not None
            and (h > p["rel_hist"] * sd or (a is not None and closes[i] - opens[i] > p["rel_thrust"] * a))
        )
        rel_down = bool(
            not rel_fired and not rel_up and recent_coil and h < 0 and h <= h_prev and vol_ok and sd is not None
            and (h < -p["rel_hist"] * sd or (a is not None and opens[i] - closes[i] > p["rel_thrust"] * a))
        )
        if rel_up or rel_down:
            rel_fired = True

        events = []
        if gate:
            if coiling:
                events.append(EVENT_COIL)
            if rel_up:
                events.append(EVENT_RELEASE_UP)
            if rel_down:
                events.append(EVENT_RELEASE_DOWN)
            if clps_down:
                events.append(EVENT_COLLAPSE_DOWN)
            if clps_up:
                events.append(EVENT_COLLAPSE_UP)
            if conf_up:
                events.append(EVENT_FLIP_UP)
            if conf_down:
                events.append(EVENT_FLIP_DOWN)

        setup_name_now = _classify_setup(
            clps_down=clps_down, clps_up=clps_up, rel_up=rel_up, rel_down=rel_down,
            trend_up=trend_up, trend_down=trend_down,
        ) if gate else ""
        setup_dir_now = _setup_direction(
            clps_down=clps_down, clps_up=clps_up, rel_up=rel_up, rel_down=rel_down,
        ) if gate else ""
        if setup_name_now:
            active_setup = setup_name_now
            setup_entry_price = closes[i]
            setup_entry_index = i
        setup_name = setup_name_now

        states.append(
            BarState(
                index=i,
                time=times[i],
                hist=h,
                run_sign=run_sign,
                run_peak=run_peak,
                bars_from_peak=bars_from_peak,
                collapse_pct=collapse_pct,
                significant=significant,
                coiling=coiling,
                in_window=window_ok,
                hot=hot,
                events=events,
                setup=setup_name,
                setup_direction=setup_dir_now,
            )
        )

    last = states[-1]
    # A/B grading is trend-relative: "A" means the collapse runs with the trend.
    summary = {
        "hist": round(last.hist, 6),
        "run_sign": last.run_sign,
        "run_peak": round(last.run_peak, 6),
        "collapse_pct": round(last.collapse_pct, 4),
        "significant": last.significant,
        "coiling": last.coiling,
        "in_window": last.in_window,
        "hot": last.hot,
        "bias": "BULLISH" if last.run_sign > 0 else ("BEARISH" if last.run_sign < 0 else "NEUTRAL"),
        "trend_up": bool(trend_up),
        "trend_down": bool(trend_down),
        "latest_event": next(
            (e for s in reversed(states) for e in reversed(s.events)), ""
        ),
    }
    summary["entry_price"] = setup_entry_price
    summary["entry_bars_ago"] = (n - 1 - setup_entry_index) if setup_entry_index is not None else None
    latest_setup = next((s.setup for s in reversed(states) if s.setup), "")
    summary["setup"] = latest_setup
    summary["setup_id"] = SETUP_IDS.get(latest_setup)
    summary["setup_label"] = (
        f"{latest_setup}#{SETUP_IDS[latest_setup]}" if latest_setup in SETUP_IDS else latest_setup
    )
    return states, summary


def timeline(states, limit=6, bar_minutes=5):
    """Recent events, newest first, aged like the real product's timeline.

    Consecutive repeats of the same event are collapsed: `coiling` is a state
    that stays true for many bars, so emitting one row per bar would bury the
    actual transitions. The real product's timeline shows distinct events
    ("Coiling, holding the setup" once, then "Now Momentum push"), so a run of
    identical events is reported at the bar it began.
    """
    if not states:
        return []
    last_index = states[-1].index
    flat = []
    for s in states:
        for ev in s.events:
            flat.append((s.index, ev))
    # collapse consecutive duplicates, keeping the FIRST bar of each run
    collapsed = []
    for idx, ev in flat:
        if collapsed and collapsed[-1][1] == ev and idx - collapsed[-1][0] <= 3:
            continue
        collapsed.append((idx, ev))

    out = []
    for idx, ev in reversed(collapsed):
        mins = (last_index - idx) * bar_minutes
        age = "now" if mins <= 0 else (f"{mins}m" if mins < 600 else f"{mins // 60}h")
        out.append({"age": age, "event": ("Now " + ev) if mins <= 0 else ev})
        if len(out) >= limit:
            break
    return out
