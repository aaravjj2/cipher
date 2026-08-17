"""Local provider/cache/storage telemetry; no secrets or response payloads."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "operational_metrics.sqlite"


def _connect(path: Path | None = None):
    target = path or DEFAULT_DB
    target.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(target, timeout=5)
    db.executescript("""
      pragma journal_mode=WAL;
      create table if not exists provider_events(
        id integer primary key,observed_at text not null,provider text not null,
        operation text not null,latency_ms real not null,status text not null,error_type text
      );
      create index if not exists idx_provider_events_time on provider_events(observed_at);
      create table if not exists cache_snapshots(
        observed_at text not null,name text not null,entries integer not null,hits integer not null,misses integer not null
      );
      create table if not exists storage_snapshots(
        observed_at text not null,category text not null,bytes integer not null,files integer not null,
        primary key(observed_at,category)
      );
    """)
    return db


def record_provider(provider: str, operation: str, latency_ms: float, status: str, error_type: str | None = None, path: Path | None = None) -> None:
    try:
        with _connect(path or DEFAULT_DB) as db:
            db.execute("insert into provider_events(observed_at,provider,operation,latency_ms,status,error_type) values(?,?,?,?,?,?)",
                       (datetime.now(timezone.utc).isoformat(), provider, operation[:160], float(latency_ms), status, error_type))
    except sqlite3.Error:
        pass  # observability must never take down market data


def record_caches(rows: list[dict], path: Path | None = None) -> None:
    now = datetime.now(timezone.utc).isoformat()
    try:
        with _connect(path or DEFAULT_DB) as db:
            db.executemany("insert into cache_snapshots values(?,?,?,?,?)", [
                (now, row["name"], int(row.get("entries") or 0), int(row.get("hits") or 0), int(row.get("misses") or 0)) for row in rows
            ])
    except sqlite3.Error:
        pass


def summary(days: int = 7, path: Path | None = None) -> dict:
    target = path or DEFAULT_DB
    if not target.exists():
        return {"status": "UNAVAILABLE", "providers": []}
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with _connect(target) as db:
        rows = db.execute("select provider,operation,latency_ms,status,error_type,observed_at from provider_events where observed_at>=? order by observed_at", (since,)).fetchall()
    groups = {}
    for provider, operation, latency, status, error, observed in rows:
        item = groups.setdefault((provider, operation), {"latencies": [], "errors": 0, "last_error": None, "last_observed_at": observed})
        item["latencies"].append(latency); item["last_observed_at"] = observed
        if status != "ok": item["errors"] += 1; item["last_error"] = error
    output = []
    for (provider, operation), item in sorted(groups.items()):
        values = sorted(item.pop("latencies")); n = len(values)
        output.append({"provider": provider, "operation": operation, "requests": n,
                       "avg_latency_ms": round(sum(values) / n, 1), "p95_latency_ms": round(values[min(n - 1, int(n * .95))], 1),
                       "error_count": item["errors"], "error_rate_pct": round(item["errors"] / n * 100, 2),
                       "last_error": item["last_error"], "last_observed_at": item["last_observed_at"]})
    return {"status": "AVAILABLE", "window_days": days, "providers": output}


def capture_storage(data_root: Path = ROOT / "data", path: Path | None = None, observed_at: str | None = None) -> dict:
    now = observed_at or datetime.now(timezone.utc).isoformat()
    categories = {
        "live_option_chains": data_root / "live_option_chains",
        "gex_snapshots": data_root / "gex_snapshots",
        "backtest_runs": data_root / "backtest_runs",
        "research_agent": data_root / "research_agent",
    }
    rows = []
    for name, root in categories.items():
        files = [item for item in root.rglob("*") if item.is_file()] if root.exists() else []
        rows.append({"category": name, "bytes": sum(item.stat().st_size for item in files), "files": len(files)})
    with _connect(path or DEFAULT_DB) as db:
        db.executemany("insert or replace into storage_snapshots values(?,?,?,?)", [(now, r["category"], r["bytes"], r["files"]) for r in rows])
    return {"observed_at": now, "categories": rows}


def storage_runway(free_bytes: int, path: Path | None = None) -> dict:
    target = path or DEFAULT_DB
    if not target.exists():
        return {"status": "INSUFFICIENT_HISTORY", "days": None}
    with _connect(target) as db:
        rows = db.execute("select observed_at,sum(bytes) from storage_snapshots group by observed_at order by observed_at").fetchall()
    if len(rows) < 2:
        return {"status": "INSUFFICIENT_HISTORY", "days": None, "samples": len(rows)}
    first_t, first_b = datetime.fromisoformat(rows[0][0]), rows[0][1]
    last_t, last_b = datetime.fromisoformat(rows[-1][0]), rows[-1][1]
    span_days = (last_t - first_t).total_seconds() / 86400
    growth = (last_b - first_b) / span_days if span_days >= 1 and last_b > first_b else None
    return {"status": "ESTIMATED" if growth else "INSUFFICIENT_HISTORY", "days": round(free_bytes / growth, 1) if growth else None,
            "samples": len(rows), "span_days": round(span_days, 2), "growth_bytes_per_day": round(growth) if growth else None}


def retention_dry_run(data_root: Path = ROOT / "data") -> dict:
    policies = (("backtest_runs", 90), ("option_history_runs", 180), ("research_agent/reports", 180))
    now = datetime.now(timezone.utc).timestamp(); candidates = []
    for relative, days in policies:
        root = data_root / relative
        for item in root.rglob("*") if root.exists() else []:
            if item.is_file() and now - item.stat().st_mtime > days * 86400:
                candidates.append({"path": str(item.relative_to(data_root)), "bytes": item.stat().st_size, "policy_days": days})
    return {"mode": "DRY_RUN_ONLY", "candidate_count": len(candidates), "candidate_bytes": sum(r["bytes"] for r in candidates),
            "candidates": candidates[:50], "destructive_action_enabled": False}
