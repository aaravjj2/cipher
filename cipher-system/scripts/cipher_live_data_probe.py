#!/usr/bin/env python3
"""
Cipher live data stream health probe for cron execution.

Checks freshness of three streams:
1. Live option chains (24/7, 15-min threshold)
2. Tradier equity stream (market hours only, 5-min threshold)
3. Alpaca GEX snapshots (market hours only, 15-min threshold)

Returns structured result for alerting.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

DATA = Path('/home/aarav/Aarav/cipher/cipher-system/data')
LIVE_OPTION_CHAINS_DIR = DATA / 'live_option_chains'
TRADIER_DB = DATA / 'tradier_stream.sqlite'
GEX_DB = DATA / 'gex_history.sqlite'

SCANNER_TICKERS = (
    'NVDA', 'MSFT', 'AAPL', 'AVGO', 'AMZN', 'IBIT',
    'GOOGL', 'TSLA', 'META', 'MU', 'AMD', 'QQQ',
)

MARKET_OPEN_UTC = 13 * 60 + 30  # 9:30 ET = 13:30 UTC
MARKET_CLOSE_UTC = 20 * 60      # 16:00 ET = 20:00 UTC


@dataclass
class StreamStatus:
    name: str
    fresh_min: float  # minutes since last update
    threshold_min: int
    is_stale: bool
    is_market_hours: bool
    detail: str


def parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).astimezone(timezone.utc)
    except ValueError:
        return None


def is_market_hours(now: datetime) -> bool:
    """Check if current time is within regular market hours (9:30-16:00 ET, Mon-Fri)."""
    if now.weekday() >= 5:  # Sat/Sun
        return False
    minutes_since_midnight = now.hour * 60 + now.minute
    return MARKET_OPEN_UTC <= minutes_since_midnight <= MARKET_CLOSE_UTC


def check_live_option_chains(now: datetime) -> StreamStatus:
    """Check live option chains freshness (24/7 stream)."""
    per_ticker = {}
    for ticker in SCANNER_TICKERS:
        latest_path = LIVE_OPTION_CHAINS_DIR / f'latest_{ticker}.json'
        if not latest_path.is_file():
            continue
        try:
            payload = json.loads(latest_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            continue
        observed = parse_dt(payload.get('as_of') or payload.get('timestamp') or payload.get('captured_at') or payload.get('updated_at'))
        if observed is not None:
            per_ticker[ticker] = observed

    if not per_ticker:
        return StreamStatus(
            name='LIVE_OPTION_CHAINS',
            fresh_min=float('inf'),
            threshold_min=15,
            is_stale=True,
            is_market_hours=True,  # 24/7 stream
            detail='No latest_<ticker>.json files found for any scanner ticker',
        )

    live_latest = max(per_ticker.values())
    age_min = (now - live_latest).total_seconds() / 60
    is_stale = age_min > 15

    ticker_ages = ', '.join(f'{t}: {(now - ts).total_seconds()/60:.1f}min' for t, ts in per_ticker.items())
    return StreamStatus(
        name='LIVE_OPTION_CHAINS',
        fresh_min=age_min,
        threshold_min=15,
        is_stale=is_stale,
        is_market_hours=True,
        detail=f'All {len(per_ticker)}/12 tickers: {ticker_ages}. Last update: {live_latest.isoformat()}',
    )


def check_tradier_equity(now: datetime) -> StreamStatus:
    """Check Tradier equity stream freshness (market hours only)."""
    market_hours = is_market_hours(now)

    with sqlite3.connect(f"file:{TRADIER_DB}?mode=ro", uri=True, timeout=5) as db:
        rows = db.execute(
            "SELECT symbol, updated_at FROM tradier_latest_quotes WHERE asset_class = 'underlying' ORDER BY updated_at DESC LIMIT 30"
        ).fetchall()

    if not rows:
        return StreamStatus(
            name='TRADIER_EQUITY',
            fresh_min=float('inf'),
            threshold_min=5,
            is_stale=market_hours,
            is_market_hours=market_hours,
            detail='No quotes in tradier_latest_quotes',
        )

    tradier_latest = max((parse_dt(row[1]) for row in rows), default=None)
    if tradier_latest is None:
        return StreamStatus(
            name='TRADIER_EQUITY',
            fresh_min=float('inf'),
            threshold_min=5,
            is_stale=market_hours,
            is_market_hours=market_hours,
            detail='Could not parse timestamps',
        )

    age_min = (now - tradier_latest).total_seconds() / 60
    is_stale = market_hours and age_min > 5

    return StreamStatus(
        name='TRADIER_EQUITY',
        fresh_min=age_min,
        threshold_min=5,
        is_stale=is_stale,
        is_market_hours=market_hours,
        detail=f'Latest: {tradier_latest.isoformat()} ({len(rows)} underlyings)',
    )


def check_gex_snapshots(now: datetime) -> StreamStatus:
    """Check Alpaca GEX snapshots freshness (market hours only)."""
    market_hours = is_market_hours(now)

    with sqlite3.connect(f"file:{GEX_DB}?mode=ro", uri=True, timeout=5) as db:
        rows = db.execute(
            "SELECT ticker, captured_at FROM gex_snapshots ORDER BY captured_at DESC LIMIT 30"
        ).fetchall()

    if not rows:
        return StreamStatus(
            name='ALPACA_GEX',
            fresh_min=float('inf'),
            threshold_min=15,
            is_stale=market_hours,
            is_market_hours=market_hours,
            detail='No snapshots in gex_snapshots',
        )

    gex_latest = max((parse_dt(row[1]) for row in rows), default=None)
    if gex_latest is None:
        return StreamStatus(
            name='ALPACA_GEX',
            fresh_min=float('inf'),
            threshold_min=15,
            is_stale=market_hours,
            is_market_hours=market_hours,
            detail='Could not parse timestamps',
        )

    age_min = (now - gex_latest).total_seconds() / 60
    is_stale = market_hours and age_min > 15

    return StreamStatus(
        name='ALPACA_GEX',
        fresh_min=age_min,
        threshold_min=15,
        is_stale=is_stale,
        is_market_hours=market_hours,
        detail=f'Latest: {gex_latest.isoformat()}',
    )


def format_alert(streams: list[StreamStatus], now: datetime) -> str:
    """Format health check result for Telegram alert."""
    lines = [
        'Cipher data health check',
        f'Checked: {now.isoformat()} ({now.strftime("%a %H:%M ET")})',
        '',
    ]

    any_stale = any(s.is_stale for s in streams)

    for s in streams:
        status = 'STALE' if s.is_stale else 'OK'
        hours_note = '' if s.is_market_hours else ' (off-hours, expected)'
        lines.append(f'{s.name}: {status} - {s.fresh_min:.0f} min old (threshold {s.threshold_min} min){hours_note}')
        lines.append(f'  {s.detail}')

    lines.append('')
    et_hour = (now.hour - 4) % 24  # rough ET from UTC
    lines.append(f'Current time: {et_hour:02d}:{now.minute:02d} ET (market {"open" if any(s.is_market_hours for s in streams) else "closed"})')

    return '\n'.join(lines)


def main() -> int:
    now = datetime.now(timezone.utc)

    streams = [
        check_live_option_chains(now),
        check_tradier_equity(now),
        check_gex_snapshots(now),
    ]

    alert_message = format_alert(streams, now)
    print(alert_message)

    # Exit code: 1 if any actionable staleness, 0 otherwise
    actionable_stale = any(s.is_stale for s in streams)
    return 1 if actionable_stale else 0


if __name__ == '__main__':
    exit(main())