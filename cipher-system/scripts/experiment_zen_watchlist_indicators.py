#!/usr/bin/env python3
"""Feature experiments for Zen watchlist alerts."""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.historical_options_download import (  # noqa: E402
    DATA_BASE,
    HistoricalOptionsStore,
    JsonHttpClient,
    alpaca_credentials,
    number,
)

NY = ZoneInfo("America/New_York")
UTC = timezone.utc


@dataclass(frozen=True)
class ExperimentRow:
    key: str
    underlying: str
    option_type: str
    alert_at_et: str
    dte: int
    alert_hour: int
    entry_mark: float
    max_profit_pct: float
    zen_update_win: bool
    hit_30_pct: bool
    hit_50_pct: bool
    hit_100_pct: bool
    post_1030_hit_30_pct: bool
    first_15m_high_pct: float | None
    first_30m_high_pct: float | None
    first_30m_low_pct: float | None
    first_hour_close_pct: float | None
    first_hour_high_pct: float | None
    option_bars_first_hour: int
    option_trades_first_hour: int
    underlying_intraday_dir_pct: float | None
    underlying_follow_30m_dir_pct: float | None
    underlying_follow_60m_dir_pct: float | None
    underlying_vs_vwap_dir_pct: float | None
    underlying_5d_dir_pct: float | None
    underlying_prior_day_dir_pct: float | None


def load_local_env() -> None:
    for path in (ROOT / ".env",):
        if not path.exists():
            continue
        for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def parse_bool(value: str) -> bool:
    return str(value).strip().lower() == "true"


def parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def iso_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def directional(option_type: str, pct: float | None) -> float | None:
    if pct is None:
        return None
    return pct if option_type == "call" else -pct


def pct(new: float | None, old: float | None) -> float | None:
    if new is None or old in (None, 0):
        return None
    return (new / old - 1.0) * 100.0


def median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def load_report(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


class UnderlyingDownloader:
    def __init__(self, store: HistoricalOptionsStore, client: JsonHttpClient, stock_feed: str) -> None:
        self.store = store
        self.client = client
        self.stock_feed = stock_feed

    def ensure(self, symbol: str, start: datetime, end: datetime) -> None:
        with self.store.connect() as db:
            count = db.execute(
                "select count(*) from underlying_bars where symbol=? and timestamp>=? and timestamp<=? and timeframe='1Min'",
                (symbol, iso_utc(start), iso_utc(end)),
            ).fetchone()[0]
        if count and int(count) > 100:
            return
        endpoint = f"/v2/stocks/{symbol}/bars"
        token = None
        while True:
            query = {
                "timeframe": "1Min",
                "start": iso_utc(start),
                "end": iso_utc(end),
                "limit": 10000,
                "feed": self.stock_feed,
                "adjustment": "raw",
                "sort": "asc",
            }
            if token:
                query["page_token"] = token
            payload, _raw, _status = self.client.get(f"{DATA_BASE}{endpoint}", query)
            rows = payload.get("bars") or []
            if isinstance(rows, dict):
                rows = rows.get(symbol) or []
            self.store.upsert_underlying_bars(symbol, rows, "1Min")
            token = payload.get("next_page_token")
            if not token:
                return


def bars_between(
    store: HistoricalOptionsStore,
    table: str,
    symbol: str,
    start: datetime,
    end: datetime,
) -> list[tuple[str, float | None, float | None, float | None, float | None, float | None]]:
    with store.connect() as db:
        if table == "underlying_bars":
            return db.execute(
                """select timestamp, open, high, low, close, volume from underlying_bars
                   where symbol=? and timeframe='1Min' and timestamp>=? and timestamp<=?
                   order by timestamp""",
                (symbol, iso_utc(start), iso_utc(end)),
            ).fetchall()
        return db.execute(
            """select timestamp, open, high, low, close, volume from option_bars
               where symbol=? and timeframe='1Min' and timestamp>=? and timestamp<=?
               order by timestamp""",
            (symbol, iso_utc(start), iso_utc(end)),
        ).fetchall()


def option_trade_count(store: HistoricalOptionsStore, symbol: str, start: datetime, end: datetime) -> int:
    with store.connect() as db:
        return int(
            db.execute(
                "select count(*) from option_trades where symbol=? and timestamp>=? and timestamp<=?",
                (symbol, iso_utc(start), iso_utc(end)),
            ).fetchone()[0]
            or 0
        )


def price_at_or_after(rows: list[tuple], when: datetime) -> float | None:
    target = when.astimezone(UTC)
    for row in rows:
        if parse_dt(row[0]).astimezone(UTC) >= target:
            return number(row[4])
    return number(rows[-1][4]) if rows else None


def close_before(rows: list[tuple], when: datetime) -> float | None:
    target = when.astimezone(UTC)
    out = None
    for row in rows:
        if parse_dt(row[0]).astimezone(UTC) >= target:
            break
        close = number(row[4])
        if close is not None:
            out = close
    return out


def session_vwap(rows: list[tuple], until: datetime) -> float | None:
    target = until.astimezone(UTC)
    total_pv = total_v = 0.0
    for row in rows:
        if parse_dt(row[0]).astimezone(UTC) > target:
            break
        close = number(row[4])
        volume = number(row[5]) or 0.0
        if close is None or volume <= 0:
            continue
        total_pv += close * volume
        total_v += volume
    return total_pv / total_v if total_v > 0 else None


def build_rows(report_rows: list[dict[str, Any]], store: HistoricalOptionsStore) -> list[ExperimentRow]:
    out: list[ExperimentRow] = []
    for raw in report_rows:
        symbol = raw["contract_symbol"]
        entry = number(raw["entry_mark"])
        max_profit = number(raw["max_profit_pct"])
        if not symbol or entry is None or max_profit is None:
            continue
        alert_at = parse_dt(raw["alert_at_et"])
        window_end = parse_dt(raw["window_end_et"])
        key_parts = raw["key"].split()
        underlying = key_parts[0]
        expiration = datetime.fromisoformat(key_parts[1]).date()
        option_type = "call" if key_parts[2].endswith("C") else "put"
        dte = (expiration - alert_at.date()).days

        opt_15 = bars_between(store, "option_bars", symbol, alert_at, alert_at + timedelta(minutes=15))
        opt_30 = bars_between(store, "option_bars", symbol, alert_at, alert_at + timedelta(minutes=30))
        opt_60 = bars_between(store, "option_bars", symbol, alert_at, alert_at + timedelta(minutes=60))
        opt_high_15 = max((number(row[2]) for row in opt_15 if number(row[2]) is not None), default=None)
        opt_high_30 = max((number(row[2]) for row in opt_30 if number(row[2]) is not None), default=None)
        opt_high_60 = max((number(row[2]) for row in opt_60 if number(row[2]) is not None), default=None)
        opt_low_30 = min((number(row[3]) for row in opt_30 if number(row[3]) is not None), default=None)
        opt_close_60 = next((number(row[4]) for row in reversed(opt_60) if number(row[4]) is not None), None)

        start_lookback = alert_at - timedelta(days=8)
        under_rows = bars_between(store, "underlying_bars", underlying, start_lookback, alert_at + timedelta(minutes=75))
        session_open_time = datetime.combine(alert_at.astimezone(NY).date(), datetime.min.time(), NY).replace(hour=9, minute=30)
        prior_close_time = datetime.combine((alert_at - timedelta(days=1)).astimezone(NY).date(), datetime.min.time(), NY).replace(hour=16, minute=0)
        alert_price = price_at_or_after(under_rows, alert_at)
        session_open_price = price_at_or_after(under_rows, session_open_time)
        follow_30 = price_at_or_after(under_rows, alert_at + timedelta(minutes=30))
        follow_60 = price_at_or_after(under_rows, alert_at + timedelta(minutes=60))
        lookback_price = price_at_or_after(under_rows, alert_at - timedelta(days=5))
        prior_close_price = close_before(under_rows, session_open_time)
        vwap = session_vwap([row for row in under_rows if parse_dt(row[0]).astimezone(NY).date() == alert_at.astimezone(NY).date()], alert_at)

        out.append(
            ExperimentRow(
                key=raw["key"],
                underlying=underlying,
                option_type=option_type,
                alert_at_et=raw["alert_at_et"],
                dte=dte,
                alert_hour=alert_at.astimezone(NY).hour,
                entry_mark=entry,
                max_profit_pct=max_profit,
                zen_update_win=parse_bool(raw["posted_green"]),
                hit_30_pct=parse_bool(raw["hit_30_pct"]),
                hit_50_pct=parse_bool(raw["hit_50_pct"]),
                hit_100_pct=parse_bool(raw["hit_100_pct"]),
                post_1030_hit_30_pct=parse_bool(raw["post_1030_hit_30_pct"]),
                first_15m_high_pct=pct(opt_high_15, entry),
                first_30m_high_pct=pct(opt_high_30, entry),
                first_30m_low_pct=pct(opt_low_30, entry),
                first_hour_close_pct=pct(opt_close_60, entry),
                first_hour_high_pct=pct(opt_high_60, entry),
                option_bars_first_hour=len(opt_60),
                option_trades_first_hour=option_trade_count(store, symbol, alert_at, alert_at + timedelta(minutes=60)),
                underlying_intraday_dir_pct=directional(option_type, pct(alert_price, session_open_price)),
                underlying_follow_30m_dir_pct=directional(option_type, pct(follow_30, alert_price)),
                underlying_follow_60m_dir_pct=directional(option_type, pct(follow_60, alert_price)),
                underlying_vs_vwap_dir_pct=directional(option_type, pct(alert_price, vwap)),
                underlying_5d_dir_pct=directional(option_type, pct(alert_price, lookback_price)),
                underlying_prior_day_dir_pct=directional(option_type, pct(alert_price, prior_close_price)),
            )
        )
    return out


def evaluate_rule(rows: list[ExperimentRow], name: str, fn: Callable[[ExperimentRow], bool]) -> dict[str, Any]:
    selected = [row for row in rows if fn(row)]
    missed = [row for row in rows if not fn(row)]
    if not selected:
        return {"rule": name, "selected": 0}
    wins = sum(1 for row in selected if row.zen_update_win)
    hit30 = sum(1 for row in selected if row.hit_30_pct)
    hit50 = sum(1 for row in selected if row.hit_50_pct)
    avg = math.fsum(row.max_profit_pct for row in selected) / len(selected)
    med = median([row.max_profit_pct for row in selected])
    return {
        "rule": name,
        "selected": len(selected),
        "zen_wins": wins,
        "zen_win_rate": wins / len(selected) * 100.0,
        "hit30": hit30,
        "hit30_rate": hit30 / len(selected) * 100.0,
        "hit50": hit50,
        "hit50_rate": hit50 / len(selected) * 100.0,
        "avg_max_profit_pct": avg,
        "median_max_profit_pct": med,
        "missed_zen_wins": sum(1 for row in missed if row.zen_update_win),
    }


def compare_feature(rows: list[ExperimentRow], feature: str) -> dict[str, Any]:
    winners = [getattr(row, feature) for row in rows if row.zen_update_win and getattr(row, feature) is not None]
    losers = [getattr(row, feature) for row in rows if not row.zen_update_win and getattr(row, feature) is not None]
    return {
        "feature": feature,
        "winner_median": median([float(x) for x in winners]),
        "loser_median": median([float(x) for x in losers]),
        "winner_count": len(winners),
        "loser_count": len(losers),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", default=str(ROOT / "data" / "historical_options" / "zen_watchlist_202607" / "zen_watchlist_profitability_report.csv"))
    parser.add_argument("--output-root", default=str(ROOT / "data" / "historical_options" / "zen_watchlist_202607"))
    args = parser.parse_args()

    load_local_env()
    if not os.environ.get("ALPACA_API_SECRET") and os.environ.get("ALPACA_SECRET_KEY"):
        os.environ["ALPACA_API_SECRET"] = os.environ["ALPACA_SECRET_KEY"]
    key, secret, stock_feed = alpaca_credentials()
    store = HistoricalOptionsStore(Path(args.output_root))
    client = JsonHttpClient({"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}, timeout=60, retries=4)
    report_rows = load_report(Path(args.report))
    downloader = UnderlyingDownloader(store, client, stock_feed)
    for raw in report_rows:
        alert_at = parse_dt(raw["alert_at_et"])
        underlying = raw["key"].split()[0]
        downloader.ensure(underlying, alert_at - timedelta(days=8), alert_at + timedelta(minutes=75))

    rows = build_rows(report_rows, store)
    rules = [
        ("Option +10% in first 30m", lambda r: (r.first_30m_high_pct or -999) >= 10),
        ("Option +20% in first 30m", lambda r: (r.first_30m_high_pct or -999) >= 20),
        ("Option first-hour close green", lambda r: (r.first_hour_close_pct or -999) > 0),
        ("Option no worse than -25% first 30m", lambda r: (r.first_30m_low_pct or -999) > -25),
        ("Underlying favorable from open", lambda r: (r.underlying_intraday_dir_pct or -999) > 0),
        ("Underlying favorable vs VWAP", lambda r: (r.underlying_vs_vwap_dir_pct or -999) > 0),
        ("Underlying 30m follow-through", lambda r: (r.underlying_follow_30m_dir_pct or -999) > 0),
        ("DTE 1-7", lambda r: 1 <= r.dte <= 7),
        ("DTE 8+", lambda r: r.dte >= 8),
        ("After 10:30 ET", lambda r: r.alert_hour >= 10),
        ("Post-10:30 +30% option confirmation", lambda r: r.post_1030_hit_30_pct),
    ]
    rule_results = [evaluate_rule(rows, name, fn) for name, fn in rules]
    feature_results = [
        compare_feature(rows, feature)
        for feature in (
            "dte",
            "entry_mark",
            "first_30m_high_pct",
            "first_30m_low_pct",
            "first_hour_close_pct",
            "option_trades_first_hour",
            "underlying_intraday_dir_pct",
            "underlying_vs_vwap_dir_pct",
            "underlying_follow_30m_dir_pct",
            "underlying_5d_dir_pct",
        )
    ]
    output_root = Path(args.output_root)
    rows_csv = output_root / "zen_watchlist_indicator_rows.csv"
    rules_json = output_root / "zen_watchlist_indicator_experiments.json"
    with rows_csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(asdict(rows[0]).keys()))
        writer.writeheader()
        writer.writerows(asdict(row) for row in rows)
    payload = {
        "rows": len(rows),
        "zen_update_wins": sum(1 for row in rows if row.zen_update_win),
        "hit30_count": sum(1 for row in rows if row.hit_30_pct),
        "rules": rule_results,
        "feature_medians": feature_results,
    }
    rules_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps({"rows_csv": str(rows_csv), "experiments_json": str(rules_json), **payload}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
