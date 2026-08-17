"""Small, truthful history store for daily option-surface observations.

The source snapshot remains Alpaca OPRA.  This store keeps derived surface
metrics and their coverage; it never fills missing IV/OI/quotes with zero.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import statistics
import math
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "option_history.sqlite"
MIN_SESSIONS = 20
METHODOLOGY_VERSION = "constant_maturity_variance_v2"
ET = ZoneInfo("America/New_York")


def _number(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _median(values):
    known = [float(v) for v in values if v is not None]
    return statistics.median(known) if known else None


def _moment(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def _constant_maturity(rows: list[dict], target_days: int = 30) -> tuple[float | None, str]:
    valid = sorted((row for row in rows if row.get("atm_iv") is not None and (row.get("dte") or 0) > 0), key=lambda row: row["dte"])
    exact = next((row for row in valid if row["dte"] == target_days), None)
    if exact:
        return float(exact["atm_iv"]), "exact"
    lower = max((row for row in valid if row["dte"] < target_days), key=lambda row: row["dte"], default=None)
    upper = min((row for row in valid if row["dte"] > target_days), key=lambda row: row["dte"], default=None)
    if not lower or not upper:
        return None, "insufficient_expiration_bracket"
    t0, t1, target = lower["dte"] / 365.0, upper["dte"] / 365.0, target_days / 365.0
    w0, w1 = float(lower["atm_iv"]) ** 2 * t0, float(upper["atm_iv"]) ** 2 * t1
    weight = (target - t0) / (t1 - t0)
    variance = w0 + weight * (w1 - w0)
    return (math.sqrt(max(variance, 0.0) / target), "interpolated_total_variance")


def derive_snapshot(payload: dict) -> dict:
    contracts = [row for row in (payload.get("contracts") or []) if isinstance(row, dict)]
    observed_at = str(payload.get("timestamp") or payload.get("as_of") or datetime.now(timezone.utc).isoformat())
    observed_moment = _moment(observed_at)
    market_session_date = observed_moment.astimezone(ET).date().isoformat()
    ticker = str(payload.get("ticker") or "").upper()
    expiries = sorted({str(row.get("expiry")) for row in contracts if row.get("expiry")})
    expiration_rows = []
    for expiry in expiries:
        rows = [row for row in contracts if str(row.get("expiry")) == expiry]
        calls = [r for r in rows if r.get("type") == "call"]
        puts = [r for r in rows if r.get("type") == "put"]
        atm_call = min((r for r in calls if _number(r.get("delta")) is not None), key=lambda r: abs(_number(r["delta"]) - .5), default=None)
        atm_put = min((r for r in puts if _number(r.get("delta")) is not None), key=lambda r: abs(_number(r["delta"]) + .5), default=None)
        call25 = min((r for r in calls if _number(r.get("delta")) is not None), key=lambda r: abs(_number(r["delta"]) - .25), default=None)
        put25 = min((r for r in puts if _number(r.get("delta")) is not None), key=lambda r: abs(_number(r["delta"]) + .25), default=None)
        atm_iv = _median([_number((atm_call or {}).get("iv")), _number((atm_put or {}).get("iv"))])
        call25_iv, put25_iv = _number((call25 or {}).get("iv")), _number((put25 or {}).get("iv"))
        try:
            dte = (date.fromisoformat(expiry) - observed_moment.astimezone(ET).date()).days
        except ValueError:
            dte = None
        expiration_rows.append({
            "expiration": expiry,
            "dte": dte,
            "atm_iv": atm_iv,
            "put_call_25d_skew": put25_iv - call25_iv if put25_iv is not None and call25_iv is not None else None,
        })
    front = next((row for row in expiration_rows if row["atm_iv"] is not None), expiration_rows[0] if expiration_rows else None)
    back = next((row for row in reversed(expiration_rows) if row["atm_iv"] is not None), None)
    spreads = []
    for row in contracts:
        bid, ask = _number(row.get("bid")), _number(row.get("ask"))
        if bid is not None and ask is not None and ask >= bid and bid + ask > 0:
            spreads.append((ask - bid) / ((ask + bid) / 2) * 100)
    def coverage(field):
        return sum(_number(row.get(field)) is not None for row in contracts) / len(contracts) if contracts else 0.0
    oi = [_number(row.get("open_interest")) for row in contracts]
    volume = [_number(row.get("volume")) for row in contracts]
    raw_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    iv_30d, iv_30d_quality = _constant_maturity(expiration_rows)
    return {
        "ticker": ticker, "observed_at": observed_at, "market_session_date": market_session_date,
        "methodology_version": METHODOLOGY_VERSION, "feed": payload.get("feed"),
        "contract_count": len(contracts), "front_expiry": (front or {}).get("expiration"),
        "front_atm_iv": (front or {}).get("atm_iv"), "front_skew_25d": (front or {}).get("put_call_25d_skew"),
        "iv_30d": iv_30d, "iv_30d_quality": iv_30d_quality,
        "term_slope": ((back or {}).get("atm_iv") - (front or {}).get("atm_iv")) if back and front and back.get("atm_iv") is not None and front.get("atm_iv") is not None else None,
        "total_open_interest": sum(v for v in oi if v is not None) if any(v is not None for v in oi) else None,
        "total_volume": sum(v for v in volume if v is not None) if any(v is not None for v in volume) else None,
        "median_spread_pct": _median(spreads), "iv_coverage": coverage("iv"),
        "oi_coverage": coverage("open_interest"), "quote_coverage": len(spreads) / len(contracts) if contracts else 0.0,
        "raw_sha256": raw_hash, "expirations": expiration_rows,
    }


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.executescript("""
    pragma journal_mode=WAL;
    create table if not exists snapshots(
      ticker text not null, observed_at text not null, feed text, contract_count integer not null,
      front_expiry text, front_atm_iv real, front_skew_25d real, term_slope real,
      total_open_interest real, total_volume real, median_spread_pct real,
      iv_coverage real not null, oi_coverage real not null, quote_coverage real not null,
      raw_sha256 text not null, primary key(ticker, observed_at)
    );
    create table if not exists expiration_metrics(
      ticker text not null, observed_at text not null, expiration text not null,
      atm_iv real, put_call_25d_skew real, dte integer,
      primary key(ticker, observed_at, expiration)
    );
    """)
    snapshot_columns = {row[1] for row in db.execute("pragma table_info(snapshots)")}
    for name, kind in (("market_session_date", "text"), ("methodology_version", "text"),
                       ("iv_30d", "real"), ("iv_30d_quality", "text")):
        if name not in snapshot_columns:
            db.execute(f"alter table snapshots add column {name} {kind}")
    expiration_columns = {row[1] for row in db.execute("pragma table_info(expiration_metrics)")}
    if "dte" not in expiration_columns:
        db.execute("alter table expiration_metrics add column dte integer")
    db.execute("create index if not exists idx_snapshots_session on snapshots(ticker,market_session_date,methodology_version)")
    return db


def record_snapshot(payload: dict, db_path: Path = DEFAULT_DB) -> dict:
    row = derive_snapshot(payload)
    if not row["ticker"] or not row["contract_count"]:
        raise ValueError("snapshot requires ticker and contracts")
    with _connect(db_path) as db:
        # One canonical close-adjacent observation per New York market session and
        # methodology. Re-runs replace that session rather than inflate history.
        db.execute("delete from snapshots where ticker=? and market_session_date=? and methodology_version=?",
                   (row["ticker"], row["market_session_date"], row["methodology_version"]))
        db.execute("""insert or replace into snapshots(
            ticker,observed_at,feed,contract_count,front_expiry,front_atm_iv,front_skew_25d,term_slope,
            total_open_interest,total_volume,median_spread_pct,iv_coverage,oi_coverage,quote_coverage,raw_sha256,
            market_session_date,methodology_version,iv_30d,iv_30d_quality)
            values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            row["ticker"], row["observed_at"], row["feed"], row["contract_count"], row["front_expiry"],
            row["front_atm_iv"], row["front_skew_25d"], row["term_slope"], row["total_open_interest"],
            row["total_volume"], row["median_spread_pct"], row["iv_coverage"], row["oi_coverage"],
            row["quote_coverage"], row["raw_sha256"], row["market_session_date"], row["methodology_version"],
            row["iv_30d"], row["iv_30d_quality"],
        ))
        db.execute("delete from expiration_metrics where ticker=? and observed_at=?", (row["ticker"], row["observed_at"]))
        db.executemany("insert into expiration_metrics(ticker,observed_at,expiration,atm_iv,put_call_25d_skew,dte) values(?,?,?,?,?,?)", [
            (row["ticker"], row["observed_at"], item["expiration"], item["atm_iv"], item["put_call_25d_skew"], item["dte"])
            for item in row["expirations"]
        ])
    return row


def history_status(ticker: str, db_path: Path = DEFAULT_DB, min_sessions: int = MIN_SESSIONS) -> dict:
    if not db_path.exists():
        return {"iv_rank": None, "iv_percentile": None, "iv_history_status": "UNAVAILABLE_NO_HISTORY", "sessions": 0}
    with _connect(db_path) as db:
        rows = db.execute("""select observed_at,front_atm_iv,front_skew_25d,term_slope,iv_coverage,oi_coverage,quote_coverage,
                          market_session_date,methodology_version,iv_30d,iv_30d_quality
                          from snapshots where ticker=? order by observed_at""", (ticker.upper(),)).fetchall()
    # Prefer homogeneous 30-day constant-maturity observations. Existing legacy
    # front-IV rows remain readable but are never mixed into the new rank.
    use_30d = any(row[9] is not None for row in rows)
    daily = {}
    for row in rows:
        metric = row[9] if use_30d else row[1]
        if metric is not None:
            daily[str(row[7] or str(row[0])[:10])] = row
    values = [row[9] if use_30d else row[1] for row in daily.values()]
    current = values[-1] if values else None
    enough = len(values) >= min_sessions
    rank = None
    percentile = None
    if enough and current is not None:
        lo, hi = min(values), max(values)
        rank = (current - lo) / (hi - lo) * 100 if hi > lo else None
        percentile = sum(value <= current for value in values) / len(values) * 100
    latest = list(daily.values())[-1] if daily else None
    return {
        "iv_rank": round(rank, 1) if rank is not None else None,
        "iv_percentile": round(percentile, 1) if percentile is not None else None,
        "iv_history_status": "AVAILABLE" if enough else "UNAVAILABLE_INSUFFICIENT_HISTORY",
        "sessions": len(values), "minimum_sessions": min_sessions,
        "readiness": "MATURE" if len(values) >= 252 else "ESTABLISHED" if len(values) >= 126 else "USABLE" if len(values) >= 60 else "PROVISIONAL" if enough else "COLLECTING",
        "metric": "iv_30d_constant_maturity" if use_30d else "front_atm_iv_legacy",
        "methodology_version": latest[8] if latest else None,
        "as_of": latest[0] if latest else None, "current_atm_iv": current,
        "current_skew_25d": latest[2] if latest else None, "current_term_slope": latest[3] if latest else None,
        "coverage": {"iv": latest[4], "oi": latest[5], "quotes": latest[6]} if latest else None,
        "iv_30d_quality": latest[10] if latest else None,
        "caveat": "IV rank uses one stored OPRA surface observation per New York session and never treats missing IV as zero; fewer than 60 sessions is provisional.",
    }
