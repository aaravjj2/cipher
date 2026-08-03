"""Historical flow-cluster downloader and backtester.

This module intentionally does not reconstruct historical GEX.  It builds a
separate signal family from historical option flow:

    flow_call =  gamma * call_volume * 100 * spot**2 * 0.01
    flow_put  = -gamma * put_volume  * 100 * spot**2 * 0.01

Premium-weighted mode uses print premium as the weighting input instead of
contract count.  Both are flow proxies, not public-OI GEX and not verified
dealer positioning.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

CORE_DIR = Path(__file__).resolve().parent
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from cluster_backtest import _direction_agree, _touch

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
FLOW_DIR = DATA_DIR / "flow_clusters"
DEFAULT_DB = DATA_DIR / "flow_cluster_history.sqlite"
LSE_BASE = "https://api.londonstrategicedge.com/vault"
STOCK_DATA_ROOT = ROOT.parents[0] / "Stock data" / "data"

DEFAULT_MIN_PREMIUM = 25_000.0
DEFAULT_MAX_DTE = 45
DEFAULT_HORIZONS = (1, 3, 5)
STOCK_BARS_MEMO: dict[tuple, tuple[list[dict], str]] = {}


def scanner_tools():
    """Load scanner helpers only when cluster construction is requested."""
    from scanner import _local_peaks, _score_cluster_strategy, cipher_model_from_profile, classify_setup

    return _local_peaks, _score_cluster_strategy, cipher_model_from_profile, classify_setup


def default_universe() -> list[str]:
    universe_json = DATA_DIR / "optionable_universe_by_cap.json"
    if universe_json.is_file():
        payload = json.loads(universe_json.read_text(encoding="utf-8"))
        sorted_tickers = payload.get("sorted_tickers") or {}
        out = []
        seen = set()
        for tier in ("mega", "large", "medium"):
            for raw in sorted_tickers.get(tier) or []:
                ticker = str(raw).upper().strip()
                if ticker and ticker not in seen:
                    seen.add(ticker)
                    out.append(ticker)
        if out:
            return out
    return ["SPY", "QQQ", "IWM", "AAPL", "MSFT", "NVDA", "TSLA", "AMD", "AMZN", "META"]


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def number(value):
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def load_dotenv() -> dict:
    env = {}
    path = ROOT / ".env"
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            raw = line.strip()
            if not raw or raw.startswith("#") or "=" not in raw:
                continue
            key, value = raw.split("=", 1)
            env[key.strip()] = value.strip().strip('"').strip("'")
    for key, value in os.environ.items():
        env[key] = value
    return env


def lse_key() -> str:
    env = load_dotenv()
    key = env.get("LSE_API_KEY") or env.get("LONDON_STRATEGIC_EDGE_KEY")
    if not key:
        raise ValueError("Set LSE_API_KEY in the environment or cipher-system/.env.")
    return key


def request_json(url: str, *, headers: dict | None = None, timeout: int = 60):
    req = Request(url, headers=headers or {"Accept": "application/json"})
    try:
        with urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise ValueError(f"HTTP {exc.code} from {url.split('?')[0]}") from exc
    except URLError as exc:
        raise ValueError(f"Unable to reach {url.split('?')[0]}") from exc


def lse_get(path: str, params: dict) -> list[dict] | dict:
    clean = {k: v for k, v in params.items() if v not in (None, "", [])}
    url = f"{LSE_BASE}{path}?{urlencode(clean)}"
    return request_json(
        url,
        headers={
            "x-api-key": lse_key(),
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 CipherLocalResearch/1.0",
            "Origin": "https://londonstrategicedge.com",
            "Referer": "https://londonstrategicedge.com/data/",
        },
    )


def iter_days(start: str, end: str) -> Iterable[str]:
    cur = date.fromisoformat(start[:10])
    stop = date.fromisoformat(end[:10])
    while cur <= stop:
        yield cur.isoformat()
        cur += timedelta(days=1)


def ensure_schema(db_path: Path = DEFAULT_DB) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as db:
        db.executescript(
            """
            create table if not exists flow_download_runs (
                id integer primary key autoincrement,
                started_at text not null,
                completed_at text,
                provider text not null,
                ticker_count integer not null,
                start_date text not null,
                end_date text not null,
                min_premium real not null,
                max_dte integer,
                row_count integer not null default 0,
                error_count integer not null default 0,
                caveat text not null
            );

            create table if not exists option_flow_prints (
                provider text not null,
                provider_id text not null,
                ts text not null,
                trade_date text not null,
                underlying text not null,
                contract text not null,
                strike real not null,
                expiry text not null,
                contract_type text not null,
                last_price real,
                volume real,
                premium real,
                underlying_price real,
                dte real,
                iv real,
                delta real,
                gamma real,
                theta real,
                vega real,
                rho real,
                raw_json text not null,
                primary key (provider, provider_id)
            );

            create index if not exists idx_flow_prints_underlying_date
                on option_flow_prints(underlying, trade_date);
            create index if not exists idx_flow_prints_contract_time
                on option_flow_prints(contract, ts);

            create table if not exists flow_cluster_snapshots (
                id integer primary key autoincrement,
                provider text not null,
                ticker text not null,
                as_of text not null,
                signal_source text not null,
                weight_mode text not null,
                spot real,
                print_count integer not null,
                total_premium real not null,
                setup_count integer not null,
                payload_json text not null,
                caveat text not null
            );

            create index if not exists idx_flow_clusters_ticker_asof
                on flow_cluster_snapshots(ticker, as_of);

            create table if not exists flow_strategy_results (
                id integer primary key autoincrement,
                snapshot_id integer not null references flow_cluster_snapshots(id),
                evaluated_at text not null,
                ticker text not null,
                as_of text not null,
                horizon integer not null,
                kind text not null,
                level real,
                spot real,
                side text,
                representative_contract text,
                hit integer,
                hit_day text,
                direction_agree integer,
                underlying_return_toward_pct real,
                option_entry real,
                option_exit real,
                option_return_pct real,
                mode text not null,
                payload_json text not null
            );
            """
        )


def insert_download_run(
    db_path: Path,
    *,
    provider: str,
    tickers: list[str],
    start: str,
    end: str,
    min_premium: float,
    max_dte: int | None,
) -> int:
    with sqlite3.connect(db_path) as db:
        cur = db.execute(
            """
            insert into flow_download_runs
                (started_at, provider, ticker_count, start_date, end_date, min_premium, max_dte, caveat)
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                utcnow(),
                provider,
                len(tickers),
                start[:10],
                end[:10],
                float(min_premium),
                max_dte,
                "Historical option-flow rows. Flow clusters are not OI-based GEX.",
            ),
        )
        return int(cur.lastrowid)


def finish_download_run(db_path: Path, run_id: int, *, row_count: int, error_count: int) -> None:
    with sqlite3.connect(db_path) as db:
        db.execute(
            """
            update flow_download_runs
            set completed_at = ?, row_count = ?, error_count = ?
            where id = ?
            """,
            (utcnow(), int(row_count), int(error_count), int(run_id)),
        )


def normalize_lse_flow_row(row: dict) -> dict | None:
    underlying = str(row.get("underlying") or "").upper().strip()
    contract = str(row.get("ticker") or "").upper().strip()
    ts = str(row.get("ts") or "")
    strike = number(row.get("strike"))
    expiry = str(row.get("expiry") or "")[:10]
    ctype = str(row.get("contract_type") or "").lower()
    provider_id = str(row.get("id") or f"{contract}|{ts}|{row.get('premium')}|{row.get('volume')}")
    if not underlying or not contract or not ts or strike is None or not expiry or ctype not in {"call", "put"}:
        return None
    return {
        "provider": "lse",
        "provider_id": provider_id,
        "ts": ts,
        "trade_date": ts[:10],
        "underlying": underlying,
        "contract": contract,
        "strike": strike,
        "expiry": expiry,
        "contract_type": ctype,
        "last_price": number(row.get("last_price")),
        "volume": number(row.get("volume")),
        "premium": number(row.get("premium")),
        "underlying_price": number(row.get("underlying_price")),
        "dte": number(row.get("dte")),
        "iv": number(row.get("iv")),
        "delta": number(row.get("delta")),
        "gamma": number(row.get("gamma")),
        "theta": number(row.get("theta")),
        "vega": number(row.get("vega")),
        "rho": number(row.get("rho")),
        "raw_json": json.dumps(row, separators=(",", ":"), default=str),
    }


def insert_flow_rows(db_path: Path, rows: list[dict]) -> int:
    if not rows:
        return 0
    cols = [
        "provider",
        "provider_id",
        "ts",
        "trade_date",
        "underlying",
        "contract",
        "strike",
        "expiry",
        "contract_type",
        "last_price",
        "volume",
        "premium",
        "underlying_price",
        "dte",
        "iv",
        "delta",
        "gamma",
        "theta",
        "vega",
        "rho",
        "raw_json",
    ]
    with sqlite3.connect(db_path) as db:
        before = db.total_changes
        db.executemany(
            f"""
            insert or ignore into option_flow_prints ({", ".join(cols)})
            values ({", ".join("?" for _ in cols)})
            """,
            [tuple(row.get(c) for c in cols) for row in rows],
        )
        return db.total_changes - before


def download_lse_flow(
    tickers: list[str],
    *,
    start: str,
    end: str,
    db_path: Path = DEFAULT_DB,
    min_premium: float = DEFAULT_MIN_PREMIUM,
    max_dte: int | None = DEFAULT_MAX_DTE,
    limit_per_call: int = 5000,
    sleep_ms: int = 350,
) -> dict:
    ensure_schema(db_path)
    tickers = [t.upper().strip() for t in tickers if t.strip()]
    run_id = insert_download_run(
        db_path,
        provider="lse",
        tickers=tickers,
        start=start,
        end=end,
        min_premium=min_premium,
        max_dte=max_dte,
    )
    total_inserted = 0
    errors = []
    for ticker in tickers:
        for day in iter_days(start, end):
            next_day = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
            for ctype in ("call", "put"):
                params = {
                    "underlying": ticker,
                    "type": ctype,
                    "start": day,
                    "end": next_day,
                    "min_premium": min_premium,
                    "max_dte": max_dte,
                    "order": "asc",
                    "limit": min(int(limit_per_call), 5000),
                }
                try:
                    payload = lse_get("/options/flow", params)
                    rows = [normalize_lse_flow_row(r) for r in (payload or [])]
                    clean = [r for r in rows if r is not None]
                    total_inserted += insert_flow_rows(db_path, clean)
                    if isinstance(payload, list) and len(payload) >= int(limit_per_call):
                        errors.append(
                            {
                                "ticker": ticker,
                                "date": day,
                                "type": ctype,
                                "warning": "page_cap_reached",
                                "limit": int(limit_per_call),
                            }
                        )
                except Exception as exc:
                    errors.append({"ticker": ticker, "date": day, "type": ctype, "error": str(exc)})
                time.sleep(max(0, int(sleep_ms)) / 1000.0)
    finish_download_run(db_path, run_id, row_count=total_inserted, error_count=len(errors))
    return {
        "run_id": run_id,
        "provider": "lse",
        "tickers": tickers,
        "start": start[:10],
        "end": end[:10],
        "rows_inserted": total_inserted,
        "errors": errors[:100],
        "db_path": str(db_path),
        "caveat": "Downloaded historical option-flow rows. This is not historical OI/GEX.",
    }


def load_flow_rows(db_path: Path, ticker: str, trade_date: str) -> list[dict]:
    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute(
            """
            select * from option_flow_prints
            where underlying = ? and trade_date = ?
            order by ts asc
            """,
            (ticker.upper(), trade_date[:10]),
        ).fetchall()
    return [dict(r) for r in rows]


def summarize_profile(profile: list[dict]) -> dict:
    if not profile:
        return {"global_max_strike": None, "call_wall_strike": None, "put_wall_strike": None}
    global_max = max(profile, key=lambda p: p["abs"])
    calls = [p for p in profile if p["call"] > 0]
    puts = [p for p in profile if p["put"] < 0]
    return {
        "global_max_strike": global_max["strike"],
        "call_wall_strike": max(calls, key=lambda p: p["call"])["strike"] if calls else None,
        "put_wall_strike": min(puts, key=lambda p: p["put"])["strike"] if puts else None,
        "gamma_flip_level": None,
    }


def representative_contract(rows: list[dict], setup: dict, spot: float | None) -> str | None:
    if not rows or not setup:
        return None
    center = number(setup.get("center"))
    side = setup.get("side")
    wanted_type = "call" if side == "above" else "put"
    candidates = [r for r in rows if r.get("contract_type") == wanted_type]
    if center is not None:
        candidates = [r for r in candidates if abs(float(r["strike"]) - center) <= max(1.0, (spot or center) * 0.01)] or candidates
    if not candidates:
        return None
    best = max(
        candidates,
        key=lambda r: (
            float(r.get("premium") or 0.0),
            -abs(float(r.get("strike") or 0.0) - (center or spot or 0.0)),
        ),
    )
    return best.get("contract")


def build_flow_snapshot(
    db_path: Path,
    ticker: str,
    trade_date: str,
    *,
    weight_mode: str = "gamma_volume",
    min_prints: int = 20,
    write: bool = True,
) -> dict | None:
    _local_peaks, _score_cluster_strategy, cipher_model_from_profile, classify_setup = scanner_tools()
    rows = load_flow_rows(db_path, ticker, trade_date)
    usable = [r for r in rows if number(r.get("gamma")) is not None and number(r.get("underlying_price"))]
    if len(usable) < int(min_prints):
        return None
    spots = [number(r.get("underlying_price")) for r in usable if number(r.get("underlying_price"))]
    spot = sorted(spots)[len(spots) // 2] if spots else None
    by_strike = defaultdict(lambda: {"call": 0.0, "put": 0.0, "volume": 0.0, "premium": 0.0})
    for row in usable:
        gamma = abs(number(row.get("gamma")) or 0.0)
        row_spot = number(row.get("underlying_price")) or spot
        if not row_spot:
            continue
        volume = number(row.get("volume")) or 0.0
        premium = number(row.get("premium")) or 0.0
        last_price = number(row.get("last_price")) or 0.0
        if weight_mode == "gamma_premium":
            # Premium / last_price / 100 approximates contract count when volume is unreliable.
            contracts = premium / max(last_price * 100.0, 1.0)
        else:
            contracts = volume
        exposure = gamma * contracts * 100.0 * row_spot * row_spot * 0.01
        strike = float(row["strike"])
        bucket = by_strike[strike]
        if row.get("contract_type") == "call":
            bucket["call"] += exposure
        else:
            bucket["put"] -= exposure
        bucket["volume"] += volume
        bucket["premium"] += premium

    profile = []
    for strike, bucket in by_strike.items():
        net = bucket["call"] + bucket["put"]
        if not math.isfinite(net):
            continue
        profile.append(
            {
                "strike": float(strike),
                "call": bucket["call"],
                "put": bucket["put"],
                "net": net,
                "abs": abs(net),
                "oi": bucket["volume"],
                "volume": bucket["volume"],
                "premium": bucket["premium"],
            }
        )
    profile.sort(key=lambda p: p["strike"])
    peaks = _local_peaks(profile)
    summary = summarize_profile(profile)
    model = cipher_model_from_profile(ticker.upper(), profile, peaks, summary, spot) if spot else None
    setups, primary = classify_setup(profile, peaks, summary, spot)
    for setup in setups:
        setup["signal_source"] = f"flow_{weight_mode}"
        setup["representative_contract"] = representative_contract(usable, setup, spot)
    score_payload = _score_cluster_strategy(setups, spot=spot, model=model or {}, peaks=peaks) if setups else {}
    payload = {
        "provider": "lse",
        "ticker": ticker.upper(),
        "as_of": trade_date[:10],
        "signal_source": f"flow_{weight_mode}",
        "weight_mode": weight_mode,
        "spot": spot,
        "print_count": len(usable),
        "total_premium": sum(number(r.get("premium")) or 0.0 for r in usable),
        "profile": profile,
        "peaks": peaks,
        "summary": summary,
        "model": model,
        "setups": setups,
        "cluster": primary,
        "score": score_payload.get("score"),
        "score_source": score_payload.get("score_source"),
        "caveat": "Flow cluster uses historical gamma-weighted option flow, not open-interest GEX.",
    }
    if write:
        ensure_schema(db_path)
        with sqlite3.connect(db_path) as db:
            cur = db.execute(
                """
                insert into flow_cluster_snapshots (
                    provider, ticker, as_of, signal_source, weight_mode, spot,
                    print_count, total_premium, setup_count, payload_json, caveat
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "lse",
                    ticker.upper(),
                    trade_date[:10],
                    payload["signal_source"],
                    weight_mode,
                    spot,
                    len(usable),
                    payload["total_premium"],
                    len(setups),
                    json.dumps(payload, separators=(",", ":"), default=str),
                    payload["caveat"],
                ),
            )
            payload["snapshot_id"] = int(cur.lastrowid)
    return payload


def load_lse_stock_bars(ticker: str, start: str, end: str, timeframe: str = "1d") -> list[dict]:
    base = {
        "symbol": ticker.upper(),
        "timeframe": timeframe,
        "start": start[:10],
        "end": end[:10],
        "order": "asc",
        "limit": 5000,
    }
    rows = []
    last_error = None
    for dataset in (None, "stocks", "etf", "index"):
        try:
            params = dict(base)
            if dataset:
                params["dataset"] = dataset
            rows = lse_get("/candles", params)
            if rows:
                break
        except Exception as exc:
            last_error = exc
    if not rows and last_error:
        raise last_error
    out = []
    for row in rows or []:
        out.append(
            {
                "time": row.get("ts"),
                "open": number(row.get("open")),
                "high": number(row.get("high")),
                "low": number(row.get("low")),
                "close": number(row.get("close")),
                "volume": number(row.get("volume")),
            }
        )
    return out


def parse_local_dt(raw: str) -> datetime | None:
    raw = str(raw or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        try:
            return datetime.fromisoformat(raw.replace(" ", "T"))
        except ValueError:
            return None


def local_stock_csv_path(ticker: str, timeframe: str = "5m", root: Path = STOCK_DATA_ROOT) -> Path | None:
    ticker = ticker.upper().strip()
    choices = [timeframe, "5m", "15m", "1m"] if timeframe != "daily" else ["5m", "15m", "1m"]
    seen = set()
    for tf in choices:
        if tf in seen:
            continue
        seen.add(tf)
        path = root / tf / f"{ticker}.csv"
        if path.is_file():
            return path
    return None


def load_local_stock_bars(
    ticker: str,
    start: str,
    end: str,
    *,
    timeframe: str = "5m",
    root: Path = STOCK_DATA_ROOT,
) -> list[dict]:
    """Load local intraday stock CSVs and resample to daily bars.

    The folder currently stores `Datetime,Open,High,Low,Close,Volume` files under
    `Stock data/data/{1m,5m,15m}`.  We use all bars in each local session date;
    that means extended-hours highs/lows can count when the CSV includes them.
    """
    path = local_stock_csv_path(ticker, timeframe=timeframe, root=root)
    if not path:
        return []
    start_day = start[:10]
    end_day = end[:10]
    by_day: dict[str, dict] = {}
    with path.open(newline="", encoding="utf-8", errors="ignore") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            dt = parse_local_dt(row.get("Datetime"))
            if not dt:
                continue
            day = dt.date().isoformat()
            if day < start_day or day > end_day:
                continue
            op = number(row.get("Open"))
            hi = number(row.get("High"))
            lo = number(row.get("Low"))
            cl = number(row.get("Close"))
            vol = number(row.get("Volume")) or 0.0
            if op is None or hi is None or lo is None or cl is None:
                continue
            bucket = by_day.get(day)
            if bucket is None:
                by_day[day] = {
                    "time": day,
                    "open": op,
                    "high": hi,
                    "low": lo,
                    "close": cl,
                    "volume": vol,
                    "source": str(path),
                }
            else:
                bucket["high"] = max(bucket["high"], hi)
                bucket["low"] = min(bucket["low"], lo)
                bucket["close"] = cl
                bucket["volume"] += vol
    return [by_day[day] for day in sorted(by_day)]


def load_stock_bars(
    ticker: str,
    start: str,
    end: str,
    *,
    provider: str = "local_then_lse",
    local_timeframe: str = "5m",
    local_root: Path = STOCK_DATA_ROOT,
) -> tuple[list[dict], str]:
    cache_key = (
        ticker.upper(),
        start[:10],
        end[:10],
        provider,
        local_timeframe,
        str(local_root),
    )
    if cache_key in STOCK_BARS_MEMO:
        bars, source = STOCK_BARS_MEMO[cache_key]
        return [dict(b) for b in bars], source
    if provider in {"local", "local_then_lse"}:
        bars = load_local_stock_bars(ticker, start, end, timeframe=local_timeframe, root=local_root)
        if bars or provider == "local":
            STOCK_BARS_MEMO[cache_key] = ([dict(b) for b in bars], "local_csv")
            return bars, "local_csv"
    bars = load_lse_stock_bars(ticker, start, end)
    STOCK_BARS_MEMO[cache_key] = ([dict(b) for b in bars], "lse_candles")
    return bars, "lse_candles"


def load_lse_option_candles(contract: str, start: str, end: str) -> list[dict]:
    rows = lse_get(
        "/options/candles",
        {
            "ticker": contract.upper(),
            "start": start[:10],
            "end": end[:10],
            "order": "asc",
            "limit": 5000,
        },
    )
    out = []
    for row in rows or []:
        out.append(
            {
                "time": row.get("minute") or row.get("ts"),
                "open": number(row.get("open")),
                "high": number(row.get("high")),
                "low": number(row.get("low")),
                "close": number(row.get("close")),
                "volume": number(row.get("volume")),
            }
        )
    return out


def next_calendar_day(day: str, n: int) -> str:
    return (date.fromisoformat(day[:10]) + timedelta(days=int(n))).isoformat()


def simulate_level_trade(
    *,
    spot: float | None,
    level: float | None,
    bars: list[dict],
    stop_pct: float = 0.02,
    min_target_pct: float = 0.0,
    max_target_pct: float | None = None,
) -> dict:
    """Simulate next-open entry toward a cluster level.

    The rule is deliberately simple and conservative: enter at the next bar open,
    target the cluster level if it remains in the trade direction, use a fixed
    percent stop, and if target and stop both print in the same daily bar assume
    the stop was hit first.
    """
    empty = {
        "entry": None,
        "exit": None,
        "exit_reason": None,
        "trade_return_pct": None,
        "direction": None,
        "target": None,
        "stop": None,
        "target_distance_pct": None,
        "skip_reason": None,
    }
    if not bars or spot is None or level is None:
        return empty
    first = bars[0]
    entry = number(first.get("open")) or number(first.get("close"))
    if not entry:
        return empty
    direction = 1.0 if float(level) >= float(spot) else -1.0
    target = float(level)
    target_distance_pct = abs(target - entry) / entry if entry else None
    if (target - entry) * direction <= 0:
        empty.update(
            {
                "entry": round(entry, 4),
                "direction": "long" if direction > 0 else "short",
                "target": round(target, 4),
                "target_distance_pct": round(target_distance_pct * 100.0, 4) if target_distance_pct is not None else None,
                "skip_reason": "target_not_ahead_at_entry",
            }
        )
        return empty
    if target_distance_pct is not None and target_distance_pct < float(min_target_pct):
        empty.update(
            {
                "entry": round(entry, 4),
                "direction": "long" if direction > 0 else "short",
                "target": round(target, 4),
                "target_distance_pct": round(target_distance_pct * 100.0, 4),
                "skip_reason": "target_too_close",
            }
        )
        return empty
    if max_target_pct is not None and target_distance_pct is not None and target_distance_pct > float(max_target_pct):
        empty.update(
            {
                "entry": round(entry, 4),
                "direction": "long" if direction > 0 else "short",
                "target": round(target, 4),
                "target_distance_pct": round(target_distance_pct * 100.0, 4),
                "skip_reason": "target_too_far",
            }
        )
        return empty
    stop = entry * (1.0 - stop_pct * direction)
    exit_price = number(bars[-1].get("close")) or entry
    exit_reason = "time"
    for bar in bars:
        hi = number(bar.get("high"))
        lo = number(bar.get("low"))
        if hi is None or lo is None:
            continue
        if direction > 0:
            stop_hit = lo <= stop
            target_hit = target is not None and hi >= target
            if stop_hit:
                exit_price = stop
                exit_reason = "stop"
                break
            if target_hit:
                exit_price = target
                exit_reason = "target"
                break
        else:
            stop_hit = hi >= stop
            target_hit = target is not None and lo <= target
            if stop_hit:
                exit_price = stop
                exit_reason = "stop"
                break
            if target_hit:
                exit_price = target
                exit_reason = "target"
                break
    trade_return = (exit_price - entry) / entry * direction * 100.0
    return {
        "entry": round(entry, 4),
        "exit": round(exit_price, 4),
        "exit_reason": exit_reason,
        "trade_return_pct": round(trade_return, 4),
        "direction": "long" if direction > 0 else "short",
        "target": round(target, 4) if target is not None else None,
        "stop": round(stop, 4),
        "target_distance_pct": round(target_distance_pct * 100.0, 4) if target_distance_pct is not None else None,
        "skip_reason": None,
    }


def evaluate_snapshot(
    db_path: Path,
    snapshot: dict,
    *,
    horizons: Iterable[int] = DEFAULT_HORIZONS,
    price_options: bool = False,
    tol_pct: float = 0.0025,
    stop_pct: float = 0.02,
    min_target_pct: float = 0.0,
    max_target_pct: float | None = None,
    bar_provider: str = "local_then_lse",
    local_timeframe: str = "5m",
    local_stock_root: Path = STOCK_DATA_ROOT,
) -> list[dict]:
    ticker = snapshot["ticker"]
    as_of = snapshot["as_of"][:10]
    max_h = max(int(h) for h in horizons)
    bars, bar_source = load_stock_bars(
        ticker,
        next_calendar_day(as_of, 1),
        next_calendar_day(as_of, max_h + 10),
        provider=bar_provider,
        local_timeframe=local_timeframe,
        local_root=local_stock_root,
    )
    results = []
    for horizon in sorted({int(h) for h in horizons if int(h) > 0}):
        eval_bars = bars[:horizon]
        for setup_rank, setup in enumerate(snapshot.get("setups") or []):
            level = setup.get("center") or setup.get("high") or setup.get("low")
            touch = _touch(level, eval_bars, spot=snapshot.get("spot"), tol_pct=tol_pct)
            trade = simulate_level_trade(
                spot=snapshot.get("spot"),
                level=level,
                bars=eval_bars,
                stop_pct=stop_pct,
                min_target_pct=min_target_pct,
                max_target_pct=max_target_pct,
            )
            agree = _direction_agree(snapshot.get("spot"), level, eval_bars)
            direction = 1.0 if (level or 0) >= (snapshot.get("spot") or 0) else -1.0
            underlying_return = None
            if eval_bars and snapshot.get("spot") and eval_bars[-1].get("close"):
                underlying_return = (eval_bars[-1]["close"] - snapshot["spot"]) / snapshot["spot"] * direction * 100.0

            option_entry = option_exit = option_return = None
            contract = setup.get("representative_contract")
            if price_options and contract:
                try:
                    candles = load_lse_option_candles(contract, next_calendar_day(as_of, 1), next_calendar_day(as_of, horizon + 1))
                    if candles:
                        option_entry = candles[0].get("open") or candles[0].get("close")
                        option_exit = candles[-1].get("close")
                        if option_entry and option_exit is not None:
                            option_return = (option_exit - option_entry) / option_entry * 100.0
                except Exception:
                    pass

            row = {
                "snapshot_id": snapshot.get("snapshot_id"),
                "ticker": ticker,
                "as_of": as_of,
                "horizon": horizon,
                "kind": f"flow_{setup.get('kind') or 'unknown'}",
                "level": level,
                "spot": snapshot.get("spot"),
                "side": setup.get("side"),
                "setup_rank": setup_rank,
                "is_primary": setup_rank == 0,
                "representative_contract": contract,
                "hit": touch["hit"],
                "hit_day": touch["hit_day"],
                "direction_agree": agree,
                "underlying_return_toward_pct": round(underlying_return, 3) if underlying_return is not None else None,
                "trade_entry": trade["entry"],
                "trade_exit": trade["exit"],
                "trade_exit_reason": trade["exit_reason"],
                "trade_return_pct": trade["trade_return_pct"],
                "trade_direction": trade["direction"],
                "trade_target": trade["target"],
                "trade_stop": trade["stop"],
                "trade_target_distance_pct": trade["target_distance_pct"],
                "trade_skip_reason": trade["skip_reason"],
                "option_entry": option_entry,
                "option_exit": option_exit,
                "option_return_pct": round(option_return, 3) if option_return is not None else None,
                "mode": "historical_flow_forward",
                "bar_source": bar_source,
            }
            results.append(row)
            if snapshot.get("snapshot_id"):
                with sqlite3.connect(db_path) as db:
                    db.execute(
                        """
                        insert into flow_strategy_results (
                            snapshot_id, evaluated_at, ticker, as_of, horizon, kind, level, spot,
                            side, representative_contract, hit, hit_day, direction_agree,
                            underlying_return_toward_pct, option_entry, option_exit,
                            option_return_pct, mode, payload_json
                        )
                        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            snapshot.get("snapshot_id"),
                            utcnow(),
                            ticker,
                            as_of,
                            horizon,
                            row["kind"],
                            level,
                            snapshot.get("spot"),
                            setup.get("side"),
                            contract,
                            1 if touch["hit"] else 0,
                            touch["hit_day"],
                            None if agree is None else (1 if agree else 0),
                            row["underlying_return_toward_pct"],
                            option_entry,
                            option_exit,
                            row["option_return_pct"],
                            row["mode"],
                            json.dumps(row, separators=(",", ":"), default=str),
                        ),
                    )
    return results


def summarize_results(results: list[dict]) -> dict:
    by_kind = defaultdict(lambda: {"n": 0, "hits": 0, "agree": 0, "agree_n": 0, "rets": [], "opt_rets": []})
    for row in results:
        b = by_kind[row["kind"]]
        b["n"] += 1
        if row.get("hit"):
            b["hits"] += 1
        if row.get("direction_agree") is not None:
            b["agree_n"] += 1
            if row.get("direction_agree"):
                b["agree"] += 1
        if row.get("underlying_return_toward_pct") is not None:
            b["rets"].append(row["underlying_return_toward_pct"])
        if row.get("option_return_pct") is not None:
            b["opt_rets"].append(row["option_return_pct"])
    summary = {}
    for kind, b in sorted(by_kind.items()):
        summary[kind] = {
            "n": b["n"],
            "hit_rate": round(b["hits"] / b["n"], 4) if b["n"] else None,
            "direction_agree_rate": round(b["agree"] / b["agree_n"], 4) if b["agree_n"] else None,
            "avg_underlying_return_toward_pct": round(sum(b["rets"]) / len(b["rets"]), 3) if b["rets"] else None,
            "avg_option_return_pct": round(sum(b["opt_rets"]) / len(b["opt_rets"]), 3) if b["opt_rets"] else None,
        }
    total = sum(b["n"] for b in by_kind.values())
    hits = sum(b["hits"] for b in by_kind.values())
    return {
        "n": total,
        "overall_hit_rate": round(hits / total, 4) if total else None,
        "by_kind": summary,
    }


def median(values: list[float]) -> float | None:
    clean = sorted(v for v in values if v is not None)
    if not clean:
        return None
    mid = len(clean) // 2
    if len(clean) % 2:
        return clean[mid]
    return (clean[mid - 1] + clean[mid]) / 2.0


def profit_factor(returns: list[float]) -> float | None:
    gains = sum(r for r in returns if r > 0)
    losses = -sum(r for r in returns if r < 0)
    if losses <= 0:
        return None if gains <= 0 else 999.0
    return gains / losses


def summarize_strategy_edges(results: list[dict], *, min_n: int = 5, primary_only: bool = False) -> dict:
    """Rank simple strategy hypotheses from flow-cluster rows.

    `follow_toward_cluster` means long if the cluster level is above spot and
    short if below.  `fade_cluster` is the exact opposite.  Returns are
    underlying close-to-horizon returns, signed by that rule.
    """
    buckets = defaultdict(list)
    for row in results:
        if primary_only and not row.get("is_primary"):
            continue
        base = row.get("underlying_return_toward_pct")
        if base is None:
            continue
        for rule, ret in (
            ("follow_toward_cluster", float(base)),
            ("fade_cluster", -float(base)),
        ):
            keys = [
                (rule, "all", "all", int(row.get("horizon") or 0)),
                (rule, row.get("kind") or "unknown", "all", int(row.get("horizon") or 0)),
                (rule, row.get("kind") or "unknown", row.get("side") or "unknown", int(row.get("horizon") or 0)),
            ]
            for key in keys:
                buckets[key].append(
                    {
                        "return_pct": ret,
                        "hit": bool(row.get("hit")),
                        "ticker": row.get("ticker"),
                        "as_of": row.get("as_of"),
                    }
                )

    rows = []
    for (rule, kind, side, horizon), samples in buckets.items():
        returns = [s["return_pct"] for s in samples]
        n = len(returns)
        wins = [r for r in returns if r > 0]
        losses = [r for r in returns if r < 0]
        avg = sum(returns) / n if n else None
        med = median(returns)
        pf = profit_factor(returns)
        hit_rate = sum(1 for s in samples if s["hit"]) / n if n else None
        win_rate = len(wins) / n if n else None
        avg_win = sum(wins) / len(wins) if wins else None
        avg_loss = sum(losses) / len(losses) if losses else None
        edge_score = None
        if avg is not None and n:
            # Tiny-sample penalty: useful for ranking hypotheses without
            # pretending a two-trade pocket is durable.
            edge_score = avg * min(math.sqrt(n / max(int(min_n), 1)), 1.0)
        rows.append(
            {
                "rule": rule,
                "kind": kind,
                "side": side,
                "horizon": horizon,
                "n": n,
                "hit_rate": round(hit_rate, 4) if hit_rate is not None else None,
                "win_rate": round(win_rate, 4) if win_rate is not None else None,
                "avg_return_pct": round(avg, 4) if avg is not None else None,
                "median_return_pct": round(med, 4) if med is not None else None,
                "avg_win_pct": round(avg_win, 4) if avg_win is not None else None,
                "avg_loss_pct": round(avg_loss, 4) if avg_loss is not None else None,
                "profit_factor": round(pf, 4) if pf is not None else None,
                "edge_score": round(edge_score, 4) if edge_score is not None else None,
                "qualified": n >= int(min_n),
            }
        )

    qualified = [r for r in rows if r["qualified"]]
    top = sorted(
        qualified,
        key=lambda r: (
            r["edge_score"] if r["edge_score"] is not None else -999,
            r["win_rate"] if r["win_rate"] is not None else 0,
            r["n"],
        ),
        reverse=True,
    )
    fade_vs_follow = {
        "follow_toward_cluster": next(
            (r for r in rows if r["rule"] == "follow_toward_cluster" and r["kind"] == "all" and r["side"] == "all"),
            None,
        ),
        "fade_cluster": next(
            (r for r in rows if r["rule"] == "fade_cluster" and r["kind"] == "all" and r["side"] == "all"),
            None,
        ),
    }
    return {
        "min_n": int(min_n),
        "primary_only": bool(primary_only),
        "rows": sorted(rows, key=lambda r: (r["rule"], r["horizon"], r["kind"], r["side"])),
        "top_edges": top[:20],
        "headline": fade_vs_follow,
        "caveat": "Exploratory underlying-return edge scan. Needs larger samples and out-of-sample validation before trust.",
    }


def summarize_trade_edges(results: list[dict], *, min_n: int = 5, primary_only: bool = False) -> dict:
    buckets = defaultdict(list)
    for row in results:
        if primary_only and not row.get("is_primary"):
            continue
        ret = row.get("trade_return_pct")
        if ret is None:
            continue
        key = (row.get("kind") or "unknown", row.get("side") or "unknown", int(row.get("horizon") or 0))
        buckets[("all", "all", int(row.get("horizon") or 0))].append(row)
        buckets[key].append(row)

    rows = []
    for (kind, side, horizon), samples in buckets.items():
        returns = [float(s["trade_return_pct"]) for s in samples]
        n = len(returns)
        wins = [r for r in returns if r > 0]
        losses = [r for r in returns if r < 0]
        avg = sum(returns) / n if n else None
        med = median(returns)
        pf = profit_factor(returns)
        exits = defaultdict(int)
        for s in samples:
            exits[s.get("trade_exit_reason") or "unknown"] += 1
        edge_score = avg * min(math.sqrt(n / max(int(min_n), 1)), 1.0) if avg is not None else None
        rows.append(
            {
                "rule": "next_open_to_cluster_stop_time",
                "kind": kind,
                "side": side,
                "horizon": horizon,
                "n": n,
                "win_rate": round(len(wins) / n, 4) if n else None,
                "avg_return_pct": round(avg, 4) if avg is not None else None,
                "median_return_pct": round(med, 4) if med is not None else None,
                "avg_win_pct": round(sum(wins) / len(wins), 4) if wins else None,
                "avg_loss_pct": round(sum(losses) / len(losses), 4) if losses else None,
                "profit_factor": round(pf, 4) if pf is not None else None,
                "target_rate": round(exits.get("target", 0) / n, 4) if n else None,
                "stop_rate": round(exits.get("stop", 0) / n, 4) if n else None,
                "time_exit_rate": round(exits.get("time", 0) / n, 4) if n else None,
                "edge_score": round(edge_score, 4) if edge_score is not None else None,
                "qualified": n >= int(min_n),
            }
        )
    top = sorted(
        [r for r in rows if r["qualified"]],
        key=lambda r: (
            r["edge_score"] if r["edge_score"] is not None else -999,
            r["profit_factor"] if r["profit_factor"] is not None else 0,
            r["n"],
        ),
        reverse=True,
    )
    return {
        "min_n": int(min_n),
        "primary_only": bool(primary_only),
        "rows": sorted(rows, key=lambda r: (r["horizon"], r["kind"], r["side"])),
        "top_edges": top[:20],
        "caveat": "Next-open cluster-target simulation with fixed percent stop. Conservative same-bar stop-first assumption.",
    }


def summarize_trade_by_ticker(results: list[dict], *, min_n: int = 3, primary_only: bool = True) -> dict:
    """Expose which symbols are carrying or diluting the aggregate signal."""
    buckets = defaultdict(list)
    for row in results:
        if primary_only and not row.get("is_primary"):
            continue
        ret = row.get("trade_return_pct")
        if ret is None:
            continue
        buckets[(row.get("ticker") or "UNKNOWN", int(row.get("horizon") or 0))].append(row)

    rows = []
    for (ticker, horizon), samples in buckets.items():
        returns = [float(s["trade_return_pct"]) for s in samples]
        n = len(returns)
        wins = [r for r in returns if r > 0]
        losses = [r for r in returns if r < 0]
        exits = defaultdict(int)
        setups = defaultdict(int)
        for s in samples:
            exits[s.get("trade_exit_reason") or "unknown"] += 1
            setups[s.get("kind") or "unknown"] += 1
        avg = sum(returns) / n if n else None
        pf = profit_factor(returns)
        rows.append(
            {
                "ticker": ticker,
                "horizon": horizon,
                "n": n,
                "win_rate": round(len(wins) / n, 4) if n else None,
                "avg_return_pct": round(avg, 4) if avg is not None else None,
                "median_return_pct": round(median(returns), 4) if returns else None,
                "avg_win_pct": round(sum(wins) / len(wins), 4) if wins else None,
                "avg_loss_pct": round(sum(losses) / len(losses), 4) if losses else None,
                "profit_factor": round(pf, 4) if pf is not None else None,
                "target_rate": round(exits.get("target", 0) / n, 4) if n else None,
                "stop_rate": round(exits.get("stop", 0) / n, 4) if n else None,
                "time_exit_rate": round(exits.get("time", 0) / n, 4) if n else None,
                "top_setup": max(setups.items(), key=lambda item: item[1])[0] if setups else None,
                "qualified": n >= int(min_n),
            }
        )
    top = sorted(
        [r for r in rows if r["qualified"]],
        key=lambda r: (
            r["avg_return_pct"] if r["avg_return_pct"] is not None else -999,
            r["profit_factor"] if r["profit_factor"] is not None else 0,
            r["n"],
        ),
        reverse=True,
    )
    return {
        "min_n": int(min_n),
        "primary_only": bool(primary_only),
        "rows": sorted(rows, key=lambda r: (r["horizon"], r["ticker"])),
        "top_tickers": top[:30],
        "caveat": "Ticker split is exploratory and can overfit quickly when per-symbol samples are small.",
    }


def _simple_return_stats(values: list[float]) -> dict | None:
    if not values:
        return None
    wins = [v for v in values if v > 0]
    losses = [v for v in values if v < 0]
    pf = profit_factor(values)
    return {
        "n": len(values),
        "win_rate": round(len(wins) / len(values), 4),
        "avg_return_pct": round(sum(values) / len(values), 4),
        "median_return_pct": round(median(values), 4),
        "profit_factor": round(pf, 4) if pf is not None else None,
    }


def summarize_ticker_train_test(
    results: list[dict],
    *,
    horizon: int = 2,
    primary_only: bool = True,
    train_fraction: float = 0.6,
) -> dict:
    """Chronological split to flag symbol filters that may be overfit."""
    days = sorted({r.get("as_of") for r in results if r.get("as_of")})
    if len(days) < 3:
        return {
            "horizon": int(horizon),
            "primary_only": bool(primary_only),
            "train_days": days,
            "test_days": [],
            "rows": [],
            "caveat": "Not enough dates for a train/test split.",
        }
    split = max(1, min(len(days) - 1, int(round(len(days) * float(train_fraction)))))
    train_days = set(days[:split])
    test_days = set(days[split:])
    buckets = defaultdict(lambda: {"train": [], "test": []})
    for row in results:
        if primary_only and not row.get("is_primary"):
            continue
        if int(row.get("horizon") or 0) != int(horizon):
            continue
        ret = row.get("trade_return_pct")
        if ret is None:
            continue
        bucket = "train" if row.get("as_of") in train_days else "test" if row.get("as_of") in test_days else None
        if bucket:
            buckets[row.get("ticker") or "UNKNOWN"][bucket].append(float(ret))

    rows = []
    for ticker, parts in buckets.items():
        train = _simple_return_stats(parts["train"])
        test = _simple_return_stats(parts["test"])
        rows.append(
            {
                "ticker": ticker,
                "train": train,
                "test": test,
                "survived": bool(
                    train
                    and test
                    and train["n"] >= 2
                    and test["n"] >= 2
                    and train["avg_return_pct"] > 0
                    and test["avg_return_pct"] > 0
                ),
            }
        )
    survivors = [r for r in rows if r["survived"]]
    survivors.sort(
        key=lambda r: (
            min(r["train"]["avg_return_pct"], r["test"]["avg_return_pct"]),
            r["test"]["n"],
        ),
        reverse=True,
    )
    return {
        "horizon": int(horizon),
        "primary_only": bool(primary_only),
        "train_days": sorted(train_days),
        "test_days": sorted(test_days),
        "survivors": survivors,
        "rows": sorted(rows, key=lambda r: r["ticker"]),
        "caveat": "Chronological ticker diagnostic only; the sample is still too small for statistical confidence.",
    }


def write_candidate_trades_csv(report_path: Path, results: list[dict]) -> Path:
    """Write full candidate trade rows for spreadsheet-level inspection."""
    csv_path = report_path.with_suffix(".trades.csv")
    fields = [
        "ticker",
        "as_of",
        "horizon",
        "is_primary",
        "setup_rank",
        "kind",
        "side",
        "spot",
        "level",
        "trade_direction",
        "trade_entry",
        "trade_exit",
        "trade_return_pct",
        "trade_exit_reason",
        "trade_target",
        "trade_stop",
        "trade_target_distance_pct",
        "trade_skip_reason",
        "hit",
        "hit_day",
        "direction_agree",
        "underlying_return_toward_pct",
        "bar_source",
        "representative_contract",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in results:
            writer.writerow(row)
    return csv_path


def run_backtest(
    tickers: list[str],
    *,
    start: str,
    end: str,
    db_path: Path = DEFAULT_DB,
    weight_mode: str = "gamma_volume",
    horizons: Iterable[int] = DEFAULT_HORIZONS,
    min_prints: int = 20,
    price_options: bool = False,
    stop_pct: float = 0.02,
    min_target_pct: float = 0.0,
    max_target_pct: float | None = None,
    bar_provider: str = "local_then_lse",
    local_timeframe: str = "5m",
    local_stock_root: Path = STOCK_DATA_ROOT,
    min_edge_n: int = 5,
) -> dict:
    ensure_schema(db_path)
    snapshots = []
    results = []
    for ticker in [t.upper().strip() for t in tickers if t.strip()]:
        for day in iter_days(start, end):
            snap = build_flow_snapshot(db_path, ticker, day, weight_mode=weight_mode, min_prints=min_prints)
            if not snap or not snap.get("setups"):
                continue
            snapshots.append(
                {
                    "snapshot_id": snap.get("snapshot_id"),
                    "ticker": snap["ticker"],
                    "as_of": snap["as_of"],
                    "setups": len(snap.get("setups") or []),
                    "score": snap.get("score"),
                    "print_count": snap.get("print_count"),
                    "total_premium": snap.get("total_premium"),
                }
            )
            results.extend(
                evaluate_snapshot(
                    db_path,
                    snap,
                    horizons=horizons,
                    price_options=price_options,
                    stop_pct=stop_pct,
                    min_target_pct=min_target_pct,
                    max_target_pct=max_target_pct,
                    bar_provider=bar_provider,
                    local_timeframe=local_timeframe,
                    local_stock_root=local_stock_root,
                )
            )
    report = {
        "as_of": utcnow(),
        "mode": "historical_flow_forward",
        "signal_source": f"flow_{weight_mode}",
        "tickers": [t.upper().strip() for t in tickers if t.strip()],
        "start": start[:10],
        "end": end[:10],
        "horizons": list(sorted({int(h) for h in horizons})),
        "price_options": bool(price_options),
        "stop_pct": float(stop_pct),
        "min_target_pct": float(min_target_pct),
        "max_target_pct": None if max_target_pct is None else float(max_target_pct),
        "bar_provider": bar_provider,
        "local_timeframe": local_timeframe,
        "local_stock_root": str(local_stock_root),
        "snapshots_n": len(snapshots),
        "setups_n": len(results),
        "summary": summarize_results(results),
        "edge_summary": summarize_strategy_edges(results, min_n=min_edge_n),
        "edge_summary_primary": summarize_strategy_edges(results, min_n=min_edge_n, primary_only=True),
        "trade_edge_summary": summarize_trade_edges(results, min_n=min_edge_n),
        "trade_edge_summary_primary": summarize_trade_edges(results, min_n=min_edge_n, primary_only=True),
        "trade_by_ticker_primary": summarize_trade_by_ticker(results, min_n=3, primary_only=True),
        "ticker_train_test_primary_h2": summarize_ticker_train_test(results, horizon=2, primary_only=True),
        "snapshots": snapshots[:200],
        "results": results[:500],
        "db_path": str(db_path),
        "caveat": (
            "This evaluates historical flow-cluster signals. It is not a historical GEX/OI backtest. "
            "Option returns, when enabled, use the representative contract's future candles and a simple hold-to-horizon exit."
        ),
    }
    FLOW_DIR.mkdir(parents=True, exist_ok=True)
    out = FLOW_DIR / f"flow_backtest_{stamp()}.json"
    trades_csv = write_candidate_trades_csv(out, results)
    report["candidate_trades_csv"] = str(trades_csv)
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    report["path"] = str(out)
    return report


def _parse_float_grid(raw: str, *, allow_none: bool = False) -> list[float | None]:
    values: list[float | None] = []
    for part in str(raw or "").split(","):
        token = part.strip().lower()
        if not token:
            continue
        if allow_none and token in {"none", "null", "off", "inf"}:
            values.append(None)
        else:
            values.append(float(token))
    return values


def run_sweep(
    tickers: list[str],
    *,
    start: str,
    end: str,
    db_path: Path = DEFAULT_DB,
    weight_mode: str = "gamma_volume",
    horizons: Iterable[int] = DEFAULT_HORIZONS,
    min_prints: int = 20,
    stop_grid: Iterable[float] = (0.02, 0.03),
    min_target_grid: Iterable[float] = (0.0, 0.0025, 0.005),
    max_target_grid: Iterable[float | None] = (None, 0.03, 0.05),
    bar_provider: str = "local_then_lse",
    local_timeframe: str = "5m",
    local_stock_root: Path = STOCK_DATA_ROOT,
    min_edge_n: int = 20,
) -> dict:
    """Run a compact parameter sweep over cached flow data."""
    runs = []
    for stop_pct in stop_grid:
        for min_target_pct in min_target_grid:
            for max_target_pct in max_target_grid:
                if max_target_pct is not None and float(max_target_pct) <= float(min_target_pct):
                    continue
                report = run_backtest(
                    tickers,
                    start=start,
                    end=end,
                    db_path=db_path,
                    weight_mode=weight_mode,
                    horizons=horizons,
                    min_prints=min_prints,
                    stop_pct=float(stop_pct),
                    min_target_pct=float(min_target_pct),
                    max_target_pct=max_target_pct,
                    bar_provider=bar_provider,
                    local_timeframe=local_timeframe,
                    local_stock_root=local_stock_root,
                    min_edge_n=min_edge_n,
                )
                primary = report.get("trade_edge_summary_primary", {}).get("top_edges") or []
                all_edges = report.get("trade_edge_summary", {}).get("top_edges") or []
                best_primary = primary[0] if primary else None
                best_all = all_edges[0] if all_edges else None
                runs.append(
                    {
                        "stop_pct": float(stop_pct),
                        "min_target_pct": float(min_target_pct),
                        "max_target_pct": max_target_pct,
                        "report_path": report.get("path"),
                        "snapshots_n": report.get("snapshots_n"),
                        "setups_n": report.get("setups_n"),
                        "best_primary_trade": best_primary,
                        "best_all_trade": best_all,
                    }
                )
    ranked = sorted(
        runs,
        key=lambda r: (
            ((r.get("best_primary_trade") or {}).get("edge_score") if r.get("best_primary_trade") else -999),
            ((r.get("best_primary_trade") or {}).get("profit_factor") if r.get("best_primary_trade") else 0) or 0,
        ),
        reverse=True,
    )
    payload = {
        "as_of": utcnow(),
        "mode": "flow_strategy_sweep",
        "tickers": [t.upper().strip() for t in tickers if t.strip()],
        "start": start[:10],
        "end": end[:10],
        "horizons": list(sorted({int(h) for h in horizons})),
        "weight_mode": weight_mode,
        "min_prints": int(min_prints),
        "bar_provider": bar_provider,
        "local_timeframe": local_timeframe,
        "min_edge_n": int(min_edge_n),
        "runs": runs,
        "top_runs": ranked[:20],
        "caveat": "Parameter sweep over historical flow clusters. Use for hypothesis ranking, not final edge validation.",
    }
    FLOW_DIR.mkdir(parents=True, exist_ok=True)
    out = FLOW_DIR / f"flow_sweep_{stamp()}.json"
    out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    payload["path"] = str(out)
    return payload


def parse_tickers(args) -> list[str]:
    if args.all:
        tickers = default_universe()
    else:
        tickers = []
        for part in args.ticker or []:
            tickers.extend([p.strip().upper() for p in part.replace(";", ",").split(",") if p.strip()])
    if args.limit:
        tickers = tickers[: int(args.limit)]
    return tickers


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Download and backtest historical flow clusters.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    dl = sub.add_parser("download-lse", help="Download LSE historical option-flow rows into SQLite.")
    dl.add_argument("--ticker", action="append", default=[])
    dl.add_argument("--all", action="store_true")
    dl.add_argument("--start", required=True)
    dl.add_argument("--end", required=True)
    dl.add_argument("--min-premium", type=float, default=DEFAULT_MIN_PREMIUM)
    dl.add_argument("--max-dte", type=int, default=DEFAULT_MAX_DTE)
    dl.add_argument("--limit", type=int, default=0, help="Limit ticker count after universe selection.")
    dl.add_argument("--limit-per-call", type=int, default=5000)
    dl.add_argument("--sleep-ms", type=int, default=350)
    dl.add_argument("--db", default=str(DEFAULT_DB))

    bt = sub.add_parser("backtest", help="Build flow clusters from cached rows and score forward behavior.")
    bt.add_argument("--ticker", action="append", default=[])
    bt.add_argument("--all", action="store_true")
    bt.add_argument("--start", required=True)
    bt.add_argument("--end", required=True)
    bt.add_argument("--weight-mode", choices=("gamma_volume", "gamma_premium"), default="gamma_volume")
    bt.add_argument("--horizons", default="1,3,5")
    bt.add_argument("--min-prints", type=int, default=20)
    bt.add_argument("--price-options", action="store_true")
    bt.add_argument("--stop-pct", type=float, default=0.02)
    bt.add_argument("--min-target-pct", type=float, default=0.0)
    bt.add_argument("--max-target-pct", type=float)
    bt.add_argument("--bar-provider", choices=("local_then_lse", "local", "lse"), default="local_then_lse")
    bt.add_argument("--local-timeframe", default="5m", choices=("1m", "5m", "15m"))
    bt.add_argument("--local-stock-root", default=str(STOCK_DATA_ROOT))
    bt.add_argument("--min-edge-n", type=int, default=5)
    bt.add_argument("--limit", type=int, default=0)
    bt.add_argument("--db", default=str(DEFAULT_DB))

    sw = sub.add_parser("sweep", help="Sweep stop and target-distance filters over cached flow clusters.")
    sw.add_argument("--ticker", action="append", default=[])
    sw.add_argument("--all", action="store_true")
    sw.add_argument("--start", required=True)
    sw.add_argument("--end", required=True)
    sw.add_argument("--weight-mode", choices=("gamma_volume", "gamma_premium"), default="gamma_volume")
    sw.add_argument("--horizons", default="1,2")
    sw.add_argument("--min-prints", type=int, default=20)
    sw.add_argument("--stop-grid", default="0.02,0.03")
    sw.add_argument("--min-target-grid", default="0,0.0025,0.005")
    sw.add_argument("--max-target-grid", default="none,0.03,0.05")
    sw.add_argument("--bar-provider", choices=("local_then_lse", "local", "lse"), default="local_then_lse")
    sw.add_argument("--local-timeframe", default="5m", choices=("1m", "5m", "15m"))
    sw.add_argument("--local-stock-root", default=str(STOCK_DATA_ROOT))
    sw.add_argument("--min-edge-n", type=int, default=20)
    sw.add_argument("--limit", type=int, default=0)
    sw.add_argument("--db", default=str(DEFAULT_DB))

    args = parser.parse_args(argv)
    tickers = parse_tickers(args)
    if not tickers:
        raise SystemExit("No tickers selected. Use --ticker SPY or --all.")

    if args.cmd == "download-lse":
        result = download_lse_flow(
            tickers,
            start=args.start,
            end=args.end,
            db_path=Path(args.db),
            min_premium=args.min_premium,
            max_dte=args.max_dte,
            limit_per_call=args.limit_per_call,
            sleep_ms=args.sleep_ms,
        )
    elif args.cmd == "backtest":
        horizons = [int(x.strip()) for x in args.horizons.split(",") if x.strip()]
        result = run_backtest(
            tickers,
            start=args.start,
            end=args.end,
            db_path=Path(args.db),
            weight_mode=args.weight_mode,
            horizons=horizons,
            min_prints=args.min_prints,
            price_options=args.price_options,
            stop_pct=args.stop_pct,
            min_target_pct=args.min_target_pct,
            max_target_pct=args.max_target_pct,
            bar_provider=args.bar_provider,
            local_timeframe=args.local_timeframe,
            local_stock_root=Path(args.local_stock_root),
            min_edge_n=args.min_edge_n,
        )
    else:
        horizons = [int(x.strip()) for x in args.horizons.split(",") if x.strip()]
        result = run_sweep(
            tickers,
            start=args.start,
            end=args.end,
            db_path=Path(args.db),
            weight_mode=args.weight_mode,
            horizons=horizons,
            min_prints=args.min_prints,
            stop_grid=[float(x) for x in _parse_float_grid(args.stop_grid)],
            min_target_grid=[float(x) for x in _parse_float_grid(args.min_target_grid)],
            max_target_grid=_parse_float_grid(args.max_target_grid, allow_none=True),
            bar_provider=args.bar_provider,
            local_timeframe=args.local_timeframe,
            local_stock_root=Path(args.local_stock_root),
            min_edge_n=args.min_edge_n,
        )
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
