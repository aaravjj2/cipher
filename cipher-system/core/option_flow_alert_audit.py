"""Audit a CSV of historical option-flow alerts against Alpaca OPRA data.

Read-only research utility. It reconstructs an approximate entry VWAP by matching
an alert's displayed premium to a contiguous cluster of OPRA prints near the
alert timestamp, then evaluates actual option bars after the alert. It never
assumes that an alert proves buy-to-open activity; the long-debit P/L fields are
explicitly hypothetical because flow side/opening intent is not observable from
trade prints alone.

Tradier is intentionally not used for expired contracts because its historical
option endpoint does not provide expired-option history. Active-contract marks
can be compared separately, but Alpaca OPRA remains the canonical source here.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
import statistics
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence
from zoneinfo import ZoneInfo

CORE = Path(__file__).resolve().parent
CIPHER_ROOT = CORE.parent
DEFAULT_INPUT = CIPHER_ROOT / "data" / "input" / "option_flow_alerts_20260715_20260724.csv"
DEFAULT_OUTPUT = CIPHER_ROOT / "data" / "option_flow_audits"
DATA_BASE = "https://data.alpaca.markets"
NY = ZoneInfo("America/New_York")
UTC = timezone.utc

sys.path.insert(0, str(CORE))
from historical_options_download import JsonHttpClient, alpaca_credentials  # noqa: E402


def num(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def parse_money(value: str) -> float:
    text = str(value).strip().replace("$", "").replace(",", "")
    multiplier = 1.0
    if text.upper().endswith("K"):
        multiplier = 1_000.0
        text = text[:-1]
    elif text.upper().endswith("M"):
        multiplier = 1_000_000.0
        text = text[:-1]
    return float(text) * multiplier


def parse_pct(value: str) -> float:
    return float(str(value).strip().rstrip("%"))


def iso_z(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def parse_timestamp(day_text: str, time_text: str) -> datetime:
    value = datetime.strptime(f"{day_text.strip()} {time_text.strip()}", "%m/%d/%y %I:%M %p")
    return value.replace(tzinfo=NY)


def occ_symbol(root: str, expiration: date, option_type: str, strike: float) -> str:
    side = "C" if option_type.lower().startswith("c") else "P"
    strike_code = int(round(float(strike) * 1000))
    return f"{root.upper()}{expiration:%y%m%d}{side}{strike_code:08d}"


def contract_candidates(symbol: str, expiration: date, option_type: str, strike: float) -> list[str]:
    # Alpaca's historical options API primarily covers equity/ETF OPRA symbols.
    # VIX index options may be absent; try common roots and preserve missing state.
    roots = [symbol.upper()]
    if symbol.upper() == "VIX":
        roots = ["VIX", "VIXW"]
    return [occ_symbol(root, expiration, option_type, strike) for root in roots]


@dataclass(slots=True)
class Alert:
    row_id: int
    alert_at: datetime
    symbol: str
    strike: float
    option_type: str
    expiration: date
    target_premium: float
    confidence: float
    candidates: list[str]

    @property
    def dte(self) -> int:
        return (self.expiration - self.alert_at.date()).days


@dataclass(slots=True)
class Trade:
    timestamp: datetime
    price: float
    size: float
    exchange: str | None
    conditions: Any

    @property
    def notional(self) -> float:
        return self.price * self.size * 100.0


def load_alerts(path: Path) -> list[Alert]:
    alerts: list[Alert] = []
    with path.open(newline="", encoding="utf-8-sig") as fh:
        for index, row in enumerate(csv.DictReader(fh), start=1):
            alert_at = parse_timestamp(row["Date"], row["Time"])
            expiration = datetime.strptime(row["Expiration"].strip(), "%m/%d/%Y").date()
            option_type = row["C/P"].strip().lower()
            symbol = row["Symbol"].strip().upper()
            strike = float(row["Strike"])
            alerts.append(
                Alert(
                    row_id=index,
                    alert_at=alert_at,
                    symbol=symbol,
                    strike=strike,
                    option_type=option_type,
                    expiration=expiration,
                    target_premium=parse_money(row["Prems Spent"]),
                    confidence=parse_pct(row["AI Confidence"]),
                    candidates=contract_candidates(symbol, expiration, option_type, strike),
                )
            )
    return alerts


def paged_get(
    client: JsonHttpClient,
    url: str,
    query: dict[str, Any],
    data_key: str,
    *,
    pause: float = 0.04,
) -> dict[str, list[dict[str, Any]]]:
    merged: dict[str, list[dict[str, Any]]] = defaultdict(list)
    token: str | None = None
    while True:
        current = dict(query)
        if token:
            current["page_token"] = token
        payload, _raw, _status = client.get(url, current)
        data = payload.get(data_key) or {}
        if isinstance(data, dict):
            for symbol, rows in data.items():
                merged[str(symbol).upper()].extend(rows or [])
        token = payload.get("next_page_token")
        if not token:
            break
        time.sleep(pause)
    return dict(merged)


def fetch_nearby_trades(client: JsonHttpClient, alert: Alert, minutes: int = 45) -> tuple[str | None, list[Trade], str | None]:
    start = alert.alert_at - timedelta(minutes=minutes)
    end = alert.alert_at + timedelta(minutes=minutes)
    last_error: str | None = None
    for candidate in alert.candidates:
        try:
            rows_by_symbol = paged_get(
                client,
                f"{DATA_BASE}/v1beta1/options/trades",
                {
                    "symbols": candidate,
                    "start": iso_z(start),
                    "end": iso_z(end),
                    "limit": 10000,
                    "sort": "asc",
                },
                "trades",
            )
        except Exception as exc:  # provider gaps are expected
            last_error = str(exc)
            continue
        raw_rows = rows_by_symbol.get(candidate, [])
        trades: list[Trade] = []
        for row in raw_rows:
            price = num(row.get("p"))
            size = num(row.get("s"))
            stamp = str(row.get("t") or "")
            if price is None or size is None or price <= 0 or size <= 0 or not stamp:
                continue
            try:
                when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            except ValueError:
                continue
            trades.append(
                Trade(
                    timestamp=when.astimezone(UTC),
                    price=price,
                    size=size,
                    exchange=row.get("x"),
                    conditions=row.get("c"),
                )
            )
        if trades:
            trades.sort(key=lambda row: row.timestamp)
            return candidate, trades, None
    return None, [], last_error


def best_cluster(trades: Sequence[Trade], alert: Alert, max_window_minutes: int = 30) -> dict[str, Any] | None:
    """Find a positive-notional contiguous print cluster closest to alert premium.

    Prefix sums plus binary search make the search O(n log n). Candidate score
    combines premium mismatch with a modest timestamp penalty. The timestamp
    penalty is deliberately secondary because vendor alerts may label the end of
    a sweep, a rounded minute, or a delayed detection time.
    """
    if not trades:
        return None
    values = [row.notional for row in trades]
    prefixes = [0.0]
    for value in values:
        prefixes.append(prefixes[-1] + value)
    times = [row.timestamp.timestamp() for row in trades]
    target = alert.target_premium
    alert_ts = alert.alert_at.astimezone(UTC).timestamp()
    max_seconds = max_window_minutes * 60
    best: tuple[float, int, int, float, float] | None = None

    for right in range(1, len(trades) + 1):
        min_time = times[right - 1] - max_seconds
        left_floor = bisect.bisect_left(times, min_time, 0, right)
        desired_prefix = prefixes[right] - target
        insertion = bisect.bisect_left(prefixes, desired_prefix, left_floor, right)
        for left in {left_floor, max(left_floor, insertion - 2), max(left_floor, insertion - 1), insertion, min(right - 1, insertion + 1)}:
            if left < left_floor or left >= right:
                continue
            matched = prefixes[right] - prefixes[left]
            if matched <= 0:
                continue
            rel_error = abs(matched - target) / target
            start_ts = times[left]
            end_ts = times[right - 1]
            # Alert may be logged at cluster start/end; use nearest boundary.
            lag_seconds = min(abs(start_ts - alert_ts), abs(end_ts - alert_ts))
            time_penalty = min(lag_seconds / 2700.0, 1.5) * 0.035
            duration_penalty = ((end_ts - start_ts) / max_seconds) * 0.005
            score = rel_error + time_penalty + duration_penalty
            if best is None or score < best[0]:
                best = (score, left, right, matched, lag_seconds)

    if best is None:
        return None
    score, left, right, matched, lag_seconds = best
    selected = trades[left:right]
    total_size = sum(row.size for row in selected)
    vwap = sum(row.price * row.size for row in selected) / total_size if total_size else None
    premium_error_pct = (matched / target - 1.0) * 100.0
    abs_error = abs(premium_error_pct)
    if abs_error <= 1.0:
        error_quality = "excellent"
    elif abs_error <= 5.0:
        error_quality = "good"
    elif abs_error <= 15.0:
        error_quality = "approximate"
    else:
        error_quality = "weak"
    duration_seconds = (selected[-1].timestamp - selected[0].timestamp).total_seconds()
    if abs_error <= 5.0 and lag_seconds <= 120 and duration_seconds <= 900:
        validation_quality = "validated"
    elif abs_error <= 15.0 and lag_seconds <= 300 and duration_seconds <= 1200:
        validation_quality = "approximate"
    else:
        validation_quality = "weak"
    return {
        "cluster_vwap": vwap,
        "matched_premium": matched,
        "premium_error_pct": premium_error_pct,
        "cluster_contracts": total_size,
        "cluster_print_count": len(selected),
        "cluster_start": iso_z(selected[0].timestamp),
        "cluster_end": iso_z(selected[-1].timestamp),
        "cluster_duration_seconds": duration_seconds,
        "alert_boundary_lag_seconds": lag_seconds,
        "match_score": score,
        "premium_error_quality": error_quality,
        "match_quality": validation_quality,
        "cluster_min_price": min(row.price for row in selected),
        "cluster_max_price": max(row.price for row in selected),
    }


def post_alert_entry(
    trades: Sequence[Trade],
    alert: Alert,
    *,
    window_seconds: int = 60,
    max_delay_seconds: int = 900,
) -> dict[str, Any] | None:
    """Model an observable entry using OPRA prints after the alert.

    The first post-alert print starts a short VWAP window. This avoids using the
    premium-matching cluster as a fill because that cluster can include prints
    after the alert and is only suitable for validating the vendor notional.
    """
    alert_utc = alert.alert_at.astimezone(UTC)
    after = [row for row in trades if row.timestamp >= alert_utc]
    if not after:
        return None
    first = after[0]
    delay = (first.timestamp - alert_utc).total_seconds()
    if delay > max_delay_seconds:
        return None
    cutoff = first.timestamp + timedelta(seconds=window_seconds)
    selected = [row for row in after if row.timestamp <= cutoff]
    total_size = sum(row.size for row in selected)
    if total_size <= 0:
        return None
    price = sum(row.price * row.size for row in selected) / total_size
    if delay <= 60:
        quality = "high"
    elif delay <= 300:
        quality = "acceptable"
    else:
        quality = "stale"
    return {
        "execution_entry_price": price,
        "execution_entry_at": iso_z(first.timestamp),
        "execution_entry_delay_seconds": delay,
        "execution_entry_window_seconds": window_seconds,
        "execution_entry_prints": len(selected),
        "execution_entry_contracts": total_size,
        "execution_entry_quality": quality,
    }


def chunks(values: Sequence[str], size: int) -> Iterable[list[str]]:
    for index in range(0, len(values), size):
        yield list(values[index : index + size])


def fetch_option_bars(
    client: JsonHttpClient,
    symbols: Sequence[str],
    start_at: datetime,
    end_at: datetime,
    timeframe: str = "5Min",
) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    merged: dict[str, list[dict[str, Any]]] = defaultdict(list)
    errors: list[str] = []
    for batch in chunks(sorted(set(symbols)), 10):
        try:
            data = paged_get(
                client,
                f"{DATA_BASE}/v1beta1/options/bars",
                {
                    "symbols": ",".join(batch),
                    "start": iso_z(start_at),
                    "end": iso_z(end_at),
                    "timeframe": timeframe,
                    "limit": 10000,
                    "sort": "asc",
                },
                "bars",
            )
        except Exception as exc:
            errors.append(f"{','.join(batch)}: {exc}")
            continue
        for symbol, rows in data.items():
            merged[symbol].extend(rows)
        time.sleep(0.08)
    for rows in merged.values():
        rows.sort(key=lambda row: str(row.get("t") or ""))
    return dict(merged), errors


def fetch_stock_daily_bars(
    client: JsonHttpClient,
    symbols: Sequence[str],
    start_day: date,
    end_day: date,
    preferred_feed: str,
) -> tuple[dict[str, list[dict[str, Any]]], str | None]:
    equity_symbols = sorted({symbol for symbol in symbols if symbol != "VIX"})
    for feed in (preferred_feed, "sip", "iex"):
        if not feed:
            continue
        try:
            data = paged_get(
                client,
                f"{DATA_BASE}/v2/stocks/bars",
                {
                    "symbols": ",".join(equity_symbols),
                    "start": f"{start_day.isoformat()}T00:00:00Z",
                    "end": f"{(end_day + timedelta(days=1)).isoformat()}T00:00:00Z",
                    "timeframe": "1Day",
                    "limit": 10000,
                    "sort": "asc",
                    "feed": feed,
                    "adjustment": "raw",
                },
                "bars",
            )
            return data, feed
        except Exception:
            continue
    return {}, None


def fetch_active_snapshots(
    client: JsonHttpClient,
    symbols: Sequence[str],
) -> tuple[dict[str, dict[str, Any]], str | None, list[str]]:
    output: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    used_feed: str | None = None
    for feed in ("opra", "indicative"):
        pending = [symbol for symbol in sorted(set(symbols)) if symbol not in output]
        if not pending:
            break
        for batch in chunks(pending, 100):
            try:
                payload, _raw, _status = client.get(
                    f"{DATA_BASE}/v1beta1/options/snapshots",
                    {"symbols": ",".join(batch), "feed": feed, "limit": 1000},
                )
            except Exception as exc:
                errors.append(f"{feed}:{','.join(batch)}: {exc}")
                continue
            snapshots = payload.get("snapshots") or {}
            for symbol, item in snapshots.items():
                output[str(symbol).upper()] = item or {}
            if snapshots and used_feed is None:
                used_feed = feed
        if output:
            break
    return output, used_feed, errors


def normalized_bars(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in rows:
        stamp = str(row.get("t") or "")
        if not stamp:
            continue
        try:
            when = datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(UTC)
        except ValueError:
            continue
        values = {name: num(row.get(key)) for name, key in (("open", "o"), ("high", "h"), ("low", "l"), ("close", "c"), ("vwap", "vw"), ("volume", "v"))}
        if values["close"] is None:
            continue
        output.append({"timestamp": when, **values, "trades": num(row.get("n"))})
    return sorted(output, key=lambda row: row["timestamp"])


def bar_at_or_after(rows: Sequence[dict[str, Any]], when: datetime) -> dict[str, Any] | None:
    timestamps = [row["timestamp"] for row in rows]
    index = bisect.bisect_left(timestamps, when.astimezone(UTC))
    return rows[index] if index < len(rows) else None


def bar_at_or_before(rows: Sequence[dict[str, Any]], when: datetime) -> dict[str, Any] | None:
    timestamps = [row["timestamp"] for row in rows]
    index = bisect.bisect_right(timestamps, when.astimezone(UTC)) - 1
    return rows[index] if index >= 0 else None


def bar_near_target(
    rows: Sequence[dict[str, Any]],
    when: datetime,
    *,
    max_lag: timedelta,
) -> dict[str, Any] | None:
    bar = bar_at_or_after(rows, when)
    if bar is None or bar["timestamp"] > when.astimezone(UTC) + max_lag:
        return None
    return bar


def pct_return(value: float | None, entry: float | None) -> float | None:
    if value is None or entry is None or entry <= 0:
        return None
    return (value / entry - 1.0) * 100.0


def snapshot_mark(item: dict[str, Any]) -> tuple[float | None, str | None]:
    quote = item.get("latestQuote") or item.get("latest_quote") or {}
    bid = num(quote.get("bp", quote.get("bid_price")))
    ask = num(quote.get("ap", quote.get("ask_price")))
    if bid is not None and ask is not None and ask >= bid and (bid > 0 or ask > 0):
        return (bid + ask) / 2.0, "snapshot_mid"
    trade = item.get("latestTrade") or item.get("latest_trade") or {}
    last = num(trade.get("p", trade.get("price")))
    if last is not None and last >= 0:
        return last, "snapshot_last"
    return None, None


def expiration_close(stock_rows: Sequence[dict[str, Any]], expiration: date) -> float | None:
    for row in stock_rows:
        stamp = str(row.get("t") or "")
        close = num(row.get("c"))
        if not stamp or close is None:
            continue
        try:
            bar_day = datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(NY).date()
        except ValueError:
            continue
        if bar_day == expiration:
            return close
    return None


def pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    mx = statistics.mean(xs)
    my = statistics.mean(ys)
    numerator = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    denominator = math.sqrt(sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys))
    return numerator / denominator if denominator else None


def quality_usable(quality: str | None) -> bool:
    return quality in {"excellent", "good", "approximate"}


def evaluate_alert(
    alert: Alert,
    contract: str | None,
    cluster: dict[str, Any] | None,
    execution: dict[str, Any] | None,
    bars_by_symbol: dict[str, list[dict[str, Any]]],
    stock_bars: dict[str, list[dict[str, Any]]],
    snapshots: dict[str, dict[str, Any]],
    as_of: datetime,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "row_id": alert.row_id,
        "alert_at_et": alert.alert_at.isoformat(),
        "symbol": alert.symbol,
        "strike": alert.strike,
        "option_type": alert.option_type,
        "expiration": alert.expiration.isoformat(),
        "dte": alert.dte,
        "target_premium": alert.target_premium,
        "ai_confidence_pct": alert.confidence,
        "contract": contract,
        "entry_side_known": False,
        "opening_trade_known": False,
        "pnl_assumption": "hypothetical long-debit position entered at the first post-alert OPRA trade VWAP",
    }
    if cluster:
        row.update(cluster)
    else:
        row.update(
            {
                "cluster_vwap": None,
                "matched_premium": None,
                "premium_error_pct": None,
                "cluster_contracts": None,
                "cluster_print_count": 0,
                "cluster_start": None,
                "cluster_end": None,
                "premium_error_quality": "missing",
                "match_quality": "missing",
            }
        )
    if execution:
        row.update(execution)
    else:
        row.update(
            {
                "execution_entry_price": None,
                "execution_entry_at": None,
                "execution_entry_delay_seconds": None,
                "execution_entry_prints": 0,
                "execution_entry_contracts": None,
                "execution_entry_quality": "missing",
            }
        )

    entry = num(row.get("execution_entry_price"))
    bars = normalized_bars(bars_by_symbol.get(contract or "", []))
    alert_utc = alert.alert_at.astimezone(UTC)
    entry_at = alert_utc
    if row.get("execution_entry_at"):
        entry_at = datetime.fromisoformat(str(row["execution_entry_at"]).replace("Z", "+00:00")).astimezone(UTC)
    after = [bar for bar in bars if bar["timestamp"] >= entry_at]
    row["option_bar_count_after_alert"] = len(after)
    row["first_option_bar_at"] = iso_z(after[0]["timestamp"]) if after else None
    row["last_option_bar_at"] = iso_z(after[-1]["timestamp"]) if after else None

    horizon_specs = (
        ("15m", timedelta(minutes=15), timedelta(minutes=10)),
        ("1h", timedelta(hours=1), timedelta(minutes=15)),
        # "1d" is a next-session comparison; allow weekends but not a long data gap.
        ("1d", timedelta(days=1), timedelta(days=3)),
    )
    for label, delta, tolerance in horizon_specs:
        target_at = entry_at + delta
        bar = bar_near_target(after, target_at, max_lag=tolerance) if after else None
        value = num(bar.get("close")) if bar else None
        row[f"mark_{label}"] = value
        row[f"return_{label}_pct"] = pct_return(value, entry)

    eod_et = datetime.combine(alert.alert_at.date(), dt_time(15, 59), NY).astimezone(UTC)
    eod_bar = bar_at_or_before(after, eod_et) if after else None
    eod_value = num(eod_bar.get("close")) if eod_bar else None
    row["mark_eod"] = eod_value
    row["return_eod_pct"] = pct_return(eod_value, entry)

    if after and entry and entry > 0:
        max_high = max((num(bar.get("high")) or num(bar.get("close")) or 0.0) for bar in after)
        min_low = min((num(bar.get("low")) if num(bar.get("low")) is not None else num(bar.get("close")) or entry) for bar in after)
        row["max_option_price_after_alert"] = max_high
        row["min_option_price_after_alert"] = min_low
        row["mfe_pct"] = pct_return(max_high, entry)
        row["mae_pct"] = pct_return(min_low, entry)
    else:
        row["max_option_price_after_alert"] = None
        row["min_option_price_after_alert"] = None
        row["mfe_pct"] = None
        row["mae_pct"] = None

    final_mark: float | None = None
    final_source: str | None = None
    expiration_underlying_close: float | None = None
    status: str
    if alert.expiration < as_of.astimezone(NY).date():
        status = "expired"
        if alert.symbol == "VIX":
            # VIX options are AM-settled and need the official SOQ; do not fake it
            # from VIX spot or an equity-style closing price.
            final_mark = None
            final_source = "missing_vix_official_settlement"
        else:
            expiration_underlying_close = expiration_close(stock_bars.get(alert.symbol, []), alert.expiration)
            if expiration_underlying_close is not None:
                if alert.option_type.startswith("c"):
                    final_mark = max(expiration_underlying_close - alert.strike, 0.0)
                else:
                    final_mark = max(alert.strike - expiration_underlying_close, 0.0)
                final_source = "expiration_intrinsic_from_underlying_close"
            elif after:
                final_mark = num(after[-1].get("close"))
                final_source = "last_observed_option_bar_fallback"
    else:
        status = "active"
        item = snapshots.get(contract or "", {})
        final_mark, final_source = snapshot_mark(item)
        if final_mark is None and after:
            final_mark = num(after[-1].get("close"))
            final_source = "last_observed_option_bar_fallback"

    row["contract_status_as_of"] = status
    row["expiration_underlying_close"] = expiration_underlying_close
    row["final_or_current_mark"] = final_mark
    row["final_mark_source"] = final_source
    row["final_or_current_return_pct"] = pct_return(final_mark, entry)
    row["hypothetical_pnl_on_alert_notional"] = (
        alert.target_premium * row["final_or_current_return_pct"] / 100.0
        if row["final_or_current_return_pct"] is not None
        else None
    )
    row["hypothetical_value_on_alert_notional"] = (
        alert.target_premium + row["hypothetical_pnl_on_alert_notional"]
        if row["hypothetical_pnl_on_alert_notional"] is not None
        else None
    )
    row["usable_for_aggregate_pnl"] = bool(
        row.get("execution_entry_quality") in {"high", "acceptable"}
        and entry is not None
        and final_mark is not None
        and alert.symbol != "VIX"
    )
    row["premium_validated"] = row.get("match_quality") == "validated"
    blockers: list[str] = []
    if row.get("execution_entry_quality") not in {"high", "acceptable"}:
        blockers.append(f"post-alert entry quality={row.get('execution_entry_quality')}")
    if row.get("match_quality") != "validated":
        blockers.append(f"displayed premium validation={row.get('match_quality')}")
    if not after:
        blockers.append("no OPRA option bars after alert")
    if final_mark is None:
        blockers.append("no defensible current/terminal mark")
    blockers.extend(["trade side is unknown", "buy-to-open versus close is unknown", "historical bid/ask is absent"])
    row["research_grade_blockers"] = "; ".join(blockers)
    return row


def bucket_dte(dte: int) -> str:
    if dte == 0:
        return "0DTE"
    if dte <= 2:
        return "1-2 DTE"
    if dte <= 7:
        return "3-7 DTE"
    return ">7 DTE"


def bucket_confidence(value: float) -> str:
    if value < 60:
        return "<60%"
    if value < 70:
        return "60-69.99%"
    return ">=70%"


def horizon_stats(rows: Sequence[dict[str, Any]], field: str) -> dict[str, Any]:
    observed = [row for row in rows if row.get("usable_for_aggregate_pnl") and num(row.get(field)) is not None]
    values = [float(row[field]) for row in observed]
    premiums = [float(row["target_premium"]) for row in observed]
    weighted = (
        sum(value * premium for value, premium in zip(values, premiums)) / sum(premiums)
        if premiums and sum(premiums)
        else None
    )
    return {
        "observations": len(values),
        "wins": sum(value > 0 for value in values),
        "win_rate_pct": (sum(value > 0 for value in values) / len(values) * 100.0) if values else None,
        "mean_return_pct": statistics.mean(values) if values else None,
        "median_return_pct": statistics.median(values) if values else None,
        "premium_weighted_return_pct": weighted,
    }


def aggregate_group(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    usable = [row for row in rows if row.get("usable_for_aggregate_pnl")]
    returns = [float(row["final_or_current_return_pct"]) for row in usable]
    pnls = [float(row["hypothetical_pnl_on_alert_notional"]) for row in usable]
    premiums = [float(row["target_premium"]) for row in usable]
    return {
        "alerts": len(rows),
        "usable": len(usable),
        "premium_validated_usable": sum(bool(row.get("premium_validated")) for row in usable),
        "premium_usable": sum(premiums),
        "wins": sum(value > 0 for value in returns),
        "losses": sum(value < 0 for value in returns),
        "win_rate_pct": (sum(value > 0 for value in returns) / len(returns) * 100.0) if returns else None,
        "mean_return_pct": statistics.mean(returns) if returns else None,
        "median_return_pct": statistics.median(returns) if returns else None,
        "premium_weighted_return_pct": (sum(pnls) / sum(premiums) * 100.0) if premiums and sum(premiums) else None,
        "hypothetical_pnl": sum(pnls),
        "exit_horizons": {
            "15m": horizon_stats(rows, "return_15m_pct"),
            "1h": horizon_stats(rows, "return_1h_pct"),
            "eod": horizon_stats(rows, "return_eod_pct"),
            "1d": horizon_stats(rows, "return_1d_pct"),
            "hold_to_expiry_or_current": horizon_stats(rows, "final_or_current_return_pct"),
        },
        "mfe_ge_25_pct": sum((num(row.get("mfe_pct")) or -math.inf) >= 25 for row in usable),
        "mfe_ge_50_pct": sum((num(row.get("mfe_pct")) or -math.inf) >= 50 for row in usable),
        "mfe_ge_100_pct": sum((num(row.get("mfe_pct")) or -math.inf) >= 100 for row in usable),
    }


def summarize(rows: Sequence[dict[str, Any]], bars_errors: Sequence[str], snapshot_errors: Sequence[str], snapshot_feed: str | None, stock_feed: str | None) -> dict[str, Any]:
    quality_counts = Counter(str(row.get("match_quality")) for row in rows)
    execution_counts = Counter(str(row.get("execution_entry_quality")) for row in rows)
    usable = [row for row in rows if row.get("usable_for_aggregate_pnl")]
    confidence_x = [float(row["ai_confidence_pct"]) for row in usable]
    return_y = [float(row["final_or_current_return_pct"]) for row in usable]

    by_confidence: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_dte: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_confidence[bucket_confidence(float(row["ai_confidence_pct"]))].append(row)
        by_dte[bucket_dte(int(row["dte"]))].append(row)
        by_type[str(row["option_type"])].append(row)

    ranked = sorted(
        usable,
        key=lambda row: float(row["final_or_current_return_pct"]),
        reverse=True,
    )
    return {
        "generated_at": iso_z(datetime.now(UTC)),
        "method": {
            "premium_validation": "best contiguous OPRA trade-print cluster within +/-45 minutes, capped at 30-minute cluster duration",
            "entry": "first post-alert OPRA print starts a 60-second trade VWAP window",
            "path": "Alpaca OPRA 5-minute historical option bars",
            "expired_terminal": "intrinsic value from raw underlying expiration-day close; VIX excluded without official SOQ",
            "active_terminal": "Alpaca option snapshot midpoint, then last trade/bar fallback",
            "pnl": "hypothetical long-debit P/L applied to displayed alert premium",
        },
        "data_quality": {
            "premium_validation_quality_counts": dict(quality_counts),
            "execution_entry_quality_counts": dict(execution_counts),
            "usable_for_aggregate_pnl": len(usable),
            "premium_validated_usable": sum(bool(row.get("premium_validated")) for row in usable),
            "excluded_from_aggregate": len(rows) - len(usable),
            "option_bar_batch_errors": list(bars_errors),
            "snapshot_errors": list(snapshot_errors),
            "snapshot_feed": snapshot_feed,
            "stock_feed": stock_feed,
            "research_grade": False,
            "research_grade_reason": "Historical quote side and opening/closing intent are not available; entry is reconstructed from prints.",
        },
        "overall": aggregate_group(rows),
        "confidence_return_pearson": pearson(confidence_x, return_y),
        "by_confidence": {key: aggregate_group(value) for key, value in sorted(by_confidence.items())},
        "by_dte": {key: aggregate_group(value) for key, value in sorted(by_dte.items())},
        "by_option_type": {key: aggregate_group(value) for key, value in sorted(by_type.items())},
        "top_winners": [
            {
                "symbol": row["symbol"],
                "contract": row["contract"],
                "alert_at_et": row["alert_at_et"],
                "return_pct": row["final_or_current_return_pct"],
                "hypothetical_pnl": row["hypothetical_pnl_on_alert_notional"],
                "match_quality": row["match_quality"],
            }
            for row in ranked[:8]
        ],
        "top_losers": [
            {
                "symbol": row["symbol"],
                "contract": row["contract"],
                "alert_at_et": row["alert_at_et"],
                "return_pct": row["final_or_current_return_pct"],
                "hypothetical_pnl": row["hypothetical_pnl_on_alert_notional"],
                "match_quality": row["match_quality"],
            }
            for row in list(reversed(ranked[-8:]))
        ],
    }


def fmt_money(value: Any) -> str:
    parsed = num(value)
    return "n/a" if parsed is None else f"${parsed:,.2f}"


def fmt_pct(value: Any) -> str:
    parsed = num(value)
    return "n/a" if parsed is None else f"{parsed:+.2f}%"


def write_outputs(output_dir: Path, rows: Sequence[dict[str, Any]], summary: dict[str, Any]) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    base = output_dir / f"option_flow_alert_audit_{stamp}"
    json_path = base.with_suffix(".json")
    csv_path = base.with_suffix(".csv")
    md_path = base.with_suffix(".md")
    latest_json = output_dir / "latest_option_flow_alert_audit.json"
    latest_csv = output_dir / "latest_option_flow_alert_audit.csv"
    latest_md = output_dir / "latest_option_flow_alert_audit.md"

    payload = {"summary": summary, "alerts": list(rows)}
    encoded = json.dumps(payload, indent=2, sort_keys=True, default=str)
    json_path.write_text(encoded, encoding="utf-8")
    latest_json.write_text(encoded, encoding="utf-8")

    fieldnames = sorted({key for row in rows for key in row})
    for path in (csv_path, latest_csv):
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    overall = summary["overall"]
    lines = [
        "# Option Flow Alert Audit",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        "## Verdict",
        "",
        "This is an **exploratory reconstructed P/L audit**, not research-grade trade attribution. "
        "The alert field says premium was spent, so P/L is modeled as a long-debit position, but OPRA prints alone do not prove buy-to-open activity.",
        "",
        "## Overall",
        "",
        f"- Alerts: {overall['alerts']}",
        f"- Usable reconstructed entries: {overall['usable']}",
        f"- Win rate: {fmt_pct(overall['win_rate_pct'])}",
        f"- Mean return: {fmt_pct(overall['mean_return_pct'])}",
        f"- Median return: {fmt_pct(overall['median_return_pct'])}",
        f"- Premium-weighted return: {fmt_pct(overall['premium_weighted_return_pct'])}",
        f"- Hypothetical P/L on usable displayed premium: {fmt_money(overall['hypothetical_pnl'])}",
        f"- Confidence/return Pearson correlation: {summary['confidence_return_pearson'] if summary['confidence_return_pearson'] is not None else 'n/a'}",
        "",
        "## Exit-horizon behavior",
        "",
    ]
    for horizon, stats in overall["exit_horizons"].items():
        lines.append(
            f"- {horizon}: n={stats['observations']}, win rate={fmt_pct(stats['win_rate_pct'])}, "
            f"median={fmt_pct(stats['median_return_pct'])}, premium-weighted={fmt_pct(stats['premium_weighted_return_pct'])}"
        )
    lines.extend(["", "## Premium-validation quality", ""])
    for key, value in sorted(summary["data_quality"]["premium_validation_quality_counts"].items()):
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Post-alert entry quality", ""])
    for key, value in sorted(summary["data_quality"]["execution_entry_quality_counts"].items()):
        lines.append(f"- {key}: {value}")
    lines.extend(
        [
            "",
            "## Alert results",
            "",
            "| Alert | Contract | Confidence | Match | Entry | Final/current | Return | MFE | MAE | Hypothetical P/L |",
            "|---|---|---:|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in rows:
        lines.append(
            f"| {row['alert_at_et']} {row['symbol']} | {row.get('contract') or 'n/a'} | "
            f"{row['ai_confidence_pct']:.2f}% | {row.get('match_quality')}/{row.get('execution_entry_quality')} | "
            f"{fmt_money(row.get('execution_entry_price'))} | {fmt_money(row.get('final_or_current_mark'))} | "
            f"{fmt_pct(row.get('final_or_current_return_pct'))} | {fmt_pct(row.get('mfe_pct'))} | "
            f"{fmt_pct(row.get('mae_pct'))} | {fmt_money(row.get('hypothetical_pnl_on_alert_notional'))} |"
        )
    lines.extend(
        [
            "",
            "## Method and limitations",
            "",
            "- The displayed premium is validated against a contiguous OPRA print cluster near the alert time; it is not used as the fill.",
            "- Modeled entry uses a 60-second VWAP beginning with the first OPRA print after the alert.",
            "- Historical bid/ask, aggressor side, and opening/closing intent are unavailable, so executable fill quality is unknown.",
            "- Active contracts use snapshot midpoint when available. Expired equity/ETF options use intrinsic value from the underlying expiration close.",
            "- VIX options require the official AM settlement/SOQ and are excluded when that value is unavailable.",
            "- Dollar P/L scales the reconstructed option return by the alert's displayed premium; it is not evidence that one trader actually held that position.",
        ]
    )
    markdown = "\n".join(lines) + "\n"
    md_path.write_text(markdown, encoding="utf-8")
    latest_md.write_text(markdown, encoding="utf-8")
    return {"json": str(json_path), "csv": str(csv_path), "markdown": str(md_path)}


def run(input_path: Path, output_dir: Path, *, as_of: datetime | None = None) -> dict[str, Any]:
    as_of = as_of or datetime.now(UTC)
    alerts = load_alerts(input_path)
    key, secret, stock_feed_preference = alpaca_credentials()
    client = JsonHttpClient(
        {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
        timeout=60,
        retries=5,
        base_sleep=0.8,
    )

    resolved_contracts: dict[int, str | None] = {}
    clusters: dict[int, dict[str, Any] | None] = {}
    executions: dict[int, dict[str, Any] | None] = {}
    trade_errors: dict[int, str | None] = {}
    for index, alert in enumerate(alerts, start=1):
        contract, trades, error = fetch_nearby_trades(client, alert)
        resolved_contracts[alert.row_id] = contract
        clusters[alert.row_id] = best_cluster(trades, alert) if trades else None
        executions[alert.row_id] = post_alert_entry(trades, alert) if trades else None
        trade_errors[alert.row_id] = error
        print(
            json.dumps(
                {
                    "progress": f"{index}/{len(alerts)}",
                    "symbol": alert.symbol,
                    "contract": contract,
                    "trades": len(trades),
                    "premium_validation": (clusters[alert.row_id] or {}).get("match_quality"),
                    "premium_error_pct": (clusters[alert.row_id] or {}).get("premium_error_pct"),
                    "entry_quality": (executions[alert.row_id] or {}).get("execution_entry_quality"),
                    "entry_delay_seconds": (executions[alert.row_id] or {}).get("execution_entry_delay_seconds"),
                },
                sort_keys=True,
            ),
            flush=True,
        )
        time.sleep(0.04)

    resolved_symbols = sorted({value for value in resolved_contracts.values() if value})
    bars_by_symbol, bars_errors = fetch_option_bars(
        client,
        resolved_symbols,
        min(alert.alert_at for alert in alerts) - timedelta(minutes=5),
        as_of + timedelta(minutes=5),
        timeframe="5Min",
    )
    stock_bars, used_stock_feed = fetch_stock_daily_bars(
        client,
        [alert.symbol for alert in alerts],
        min(alert.alert_at.date() for alert in alerts) - timedelta(days=2),
        max(as_of.astimezone(NY).date(), max(alert.expiration for alert in alerts if alert.expiration < as_of.astimezone(NY).date())),
        stock_feed_preference,
    )
    active_symbols = [
        resolved_contracts[alert.row_id]
        for alert in alerts
        if alert.expiration >= as_of.astimezone(NY).date() and resolved_contracts[alert.row_id]
    ]
    snapshots, snapshot_feed, snapshot_errors = fetch_active_snapshots(client, active_symbols)

    rows: list[dict[str, Any]] = []
    for alert in alerts:
        row = evaluate_alert(
            alert,
            resolved_contracts[alert.row_id],
            clusters[alert.row_id],
            executions[alert.row_id],
            bars_by_symbol,
            stock_bars,
            snapshots,
            as_of,
        )
        if trade_errors[alert.row_id]:
            row["trade_fetch_error"] = trade_errors[alert.row_id]
        rows.append(row)

    summary = summarize(rows, bars_errors, snapshot_errors, snapshot_feed, used_stock_feed)
    files = write_outputs(output_dir, rows, summary)
    return {"summary": summary, "files": files}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit historical option-flow alerts with Alpaca OPRA data.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--as-of", help="Optional RFC3339/ISO timestamp; defaults to now")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    as_of = datetime.fromisoformat(args.as_of.replace("Z", "+00:00")) if args.as_of else None
    result = run(args.input, args.output_dir, as_of=as_of)
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
