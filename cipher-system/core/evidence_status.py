"""How much evidence has accrued, and what each threshold unlocks.

Three separate questions are currently waiting on data rather than on code, and
none of them was visible anywhere in the product:

  * cluster / GEX strategies cannot be backtested until enough point-in-time open
    interest exists. GEX = gamma x OI, so replaying today's OI over past prices is
    lookahead bias that manufactures edge.
  * the fitted flash head cannot be re-enabled until the paired label corpus
    reaches the activation gate in weight_lab.ACTIVATION_GATE.
  * the filter-mode bearish partition is promising but unestablished; its holdout
    had 27 trades.

Without a surface for this, "how long until X" is unanswerable except by reading
SQLite by hand, and slow progress is indistinguishable from no progress. Every
number here is measured, and every threshold is cited from the code that enforces
it rather than invented for the display.

Read-only.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Trading days of universe-wide capture before cluster/GEX backtesting has enough
# point-in-time OI to be worth attempting. ~3 months of sessions: short enough to
# be reachable, long enough to span more than one regime. Not enforced anywhere —
# it is a stated target, and is labelled as such.
CLUSTER_BACKTEST_TARGET_DAYS = 60


def _count(db_path: Path, sql: str, default=0):
    if not db_path.exists():
        return default
    try:
        with sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=5) as db:
            row = db.execute(sql).fetchone()
        return row[0] if row and row[0] is not None else default
    except sqlite3.Error:
        return default


def _pct(have, need):
    if not need:
        return None
    return round(min(100.0, 100.0 * have / need), 1)


def gex_capture_status() -> dict:
    """Point-in-time OI accrual — the gate on cluster/GEX backtesting."""
    db = ROOT / "data" / "gex_history.sqlite"
    days = _count(db, "select count(distinct substr(captured_at,1,10)) from gex_snapshots")
    latest = _count(db, "select max(substr(captured_at,1,10)) from gex_snapshots", default=None)
    snapshots = _count(db, "select count(*) from gex_snapshots")
    tickers = _count(db, "select count(distinct ticker) from gex_snapshots")
    return {
        "name": "Point-in-time open interest",
        "unlocks": "cluster and GEX strategy backtesting",
        "have": days,
        "need": CLUSTER_BACKTEST_TARGET_DAYS,
        "unit": "capture days",
        "progress_pct": _pct(days, CLUSTER_BACKTEST_TARGET_DAYS),
        "latest_capture": latest,
        "snapshots": snapshots,
        "tickers": tickers,
        "note": (
            "GEX is gamma x open interest, so a backtest needs OI as it stood on the "
            "day. A missed session cannot be back-filled from any vendor."
        ),
    }


def flash_corpus_status() -> dict:
    """Paired label accrual — the gate on re-enabling the fitted flash head."""
    paired_dir = ROOT / "data" / "weight_lab" / "paired"
    rows, groups, tickers, days = 0, set(), set(), set()
    for path in sorted(paired_dir.glob("*.jsonl")) if paired_dir.exists() else []:
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                record = json.loads(line)
                rows += 1
                day = str(record.get("session_date") or "")[:10]
                ticker = record.get("ticker")
                if day:
                    days.add(day)
                if ticker:
                    tickers.add(ticker)
                if day and ticker:
                    groups.add((day, ticker))
        except (OSError, ValueError):
            continue

    try:
        import weight_lab

        gate = weight_lab.ACTIVATION_GATE
        active = weight_lab.is_flash_active()
        requested_active = bool(weight_lab._active_payload().get("flash_active"))
        blockers = weight_lab.activation_blockers(weight_lab.load_flash_weights())
    except Exception:  # noqa: BLE001 - status must never fail on an import
        gate = {"min_groups": 30, "min_tickers": 12, "min_days": 10}
        active = None
        requested_active = None
        blockers = ["weight lab status unavailable"]

    return {
        "name": "Paired flash labels",
        "unlocks": "re-enabling the fitted flash score",
        "have": len(groups),
        "need": gate.get("min_groups"),
        "unit": "independent (day, ticker) groups",
        "progress_pct": _pct(len(groups), gate.get("min_groups")),
        "rows": rows,
        "tickers": {"have": len(tickers), "need": gate.get("min_tickers")},
        "days": {"have": len(days), "need": gate.get("min_days")},
        "fitted_head_active": active,
        "activation_requested": requested_active,
        "activation_blockers": blockers,
        "note": (
            "Rows are not samples: intraday re-capture of the same card is "
            "pseudo-replication, so the gate counts independent (day, ticker) groups."
        ),
    }


def parity_status() -> dict:
    """Newest cell-level parity measurement against the real product."""
    reports = sorted((ROOT / "data" / "parity_reports").glob("parity_views_*.json")) \
        if (ROOT / "data" / "parity_reports").exists() else []
    if not reports:
        return {"name": "GEX parity", "measured": False,
                "note": "run scripts/capture_ticker_views.py then compare_ticker_views.py"}
    try:
        payload = json.loads(reports[-1].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"name": "GEX parity", "measured": False, "note": "latest report unreadable"}

    surfaces = {}
    for row in payload if isinstance(payload, list) else []:
        for key, stats in (row.get("surfaces") or {}).items():
            err = stats.get("median_rel_err_pct")
            if err is not None:
                surfaces.setdefault(key, []).append(err)
    medians = {
        key: round(sorted(values)[len(values) // 2], 4)
        for key, values in surfaces.items() if values
    }
    return {
        "name": "GEX parity vs the real product",
        "measured": True,
        "source": reports[-1].name,
        "median_rel_err_pct": medians,
        "tickers": len(payload) if isinstance(payload, list) else None,
    }


def status() -> dict:
    return {
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "clocks": [gex_capture_status(), flash_corpus_status()],
        "parity": parity_status(),
        "caveat": (
            "Thresholds are targets, not guarantees. Reaching one means a question "
            "becomes answerable, not that the answer will be favourable."
        ),
    }
