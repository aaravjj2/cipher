#!/usr/bin/env python3
"""Alert when read-only Cipher market-data collectors become stale.

Checks local SQLite capture databases and sends state-change notifications via
Hermes.  This is monitoring only; it never calls brokerage trading endpoints.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from hermes_delivery import send_hermes_message


ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "core"
DATA = ROOT / "data"
DEFAULT_STATE = DATA / "alerts" / "data_health_state.json"
GOVERNANCE_RUNNER = ROOT.parent / "infra" / "gcp-cipher-vm" / "bin" / "run-governance-catalog.sh"
ARCHIVE_RUNNER = ROOT / "scripts" / "archive_live_option_chains.py"
LIVE_OPTION_CHAINS_DIR = DATA / "live_option_chains"
NY = ZoneInfo("America/New_York")

SCANNER_TICKERS = (
    "NVDA", "MSFT", "AAPL", "AVGO", "AMZN", "IBIT",
    "GOOGL", "TSLA", "META", "MU", "AMD", "QQQ",
)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def market_window(kind: str, now: datetime | None = None) -> bool:
    """Whether a collector should be producing data right now.

    This must track when *data flows*, not when the process is alive. The two are
    different for tradier: `run-tradier-loop.sh` starts the capture process at 07:30 ET,
    but `core/tradier_stream_capture.py:regular_session_open` only records events between
    09:30 and 16:00 ET, deliberately, because extended-hours quotes are excluded from the
    validation dataset.

    This function previously claimed 07:30-17:00 for tradier, so for roughly three hours of
    every trading day it expected data that by design does not exist, reported `stale`, and
    pushed that to Telegram. An alert that is wrong on a schedule is worse than no alert:
    it trains the reader to ignore the channel that also carries the real outages.

    The capture window is therefore read from the collector itself rather than restated
    here, so the two cannot drift apart again.
    """
    current = now or datetime.now(NY)
    if current.weekday() >= 5:
        return False
    if kind == "tradier":
        return _tradier_session_open(current)
    # GEX and chain snapshots are captured on the regular session with a short tail for
    # the closing snapshot to land.
    return time(9, 30) <= current.time() <= time(16, 10)


def _tradier_session_open(current: datetime) -> bool:
    """Defer to the collector's own session gate, falling back to its literal bounds."""
    try:
        sys.path.insert(0, str(CORE))
        from tradier_stream_capture import regular_session_open

        return bool(regular_session_open(current))
    except Exception:
        # A failed import must not silence the check entirely; mirror the collector's
        # documented 09:30-16:00 ET regular session instead of the old 07:30-17:00.
        return time(9, 30) <= current.time() < time(16, 0)


def latest_tradier(db_path: Path) -> dict[str, Any]:
    if not db_path.is_file():
        return {"ok": False, "reason": "missing_db", "path": str(db_path)}
    with sqlite3.connect(db_path) as db:
        rows = db.execute(
            "select symbol, updated_at from tradier_latest_quotes order by updated_at desc limit 5"
        ).fetchall()
        # IDs are assigned by the table's INTEGER PRIMARY KEY and collector run
        # reconciliation verifies the sequence. COUNT(*) scanned the entire 47 GB
        # event index every 15 minutes, delaying the post-market archive by minutes.
        count = db.execute("select coalesce(max(id), 0) from tradier_stream_events").fetchone()[0]
    latest = max((parse_dt(row[1]) for row in rows), default=None)
    return {"ok": bool(latest), "latest": latest, "rows": rows, "events": count, "path": str(db_path)}


def latest_gex(db_path: Path) -> dict[str, Any]:
    if not db_path.is_file():
        return {"ok": False, "reason": "missing_db", "path": str(db_path)}
    with sqlite3.connect(db_path) as db:
        rows = db.execute(
            "select ticker, captured_at from gex_snapshots order by captured_at desc limit 5"
        ).fetchall()
        count = db.execute("select count(*) from gex_snapshots").fetchone()[0]
    latest = max((parse_dt(row[1]) for row in rows), default=None)
    return {"ok": bool(latest), "latest": latest, "rows": rows, "snapshots": count, "path": str(db_path)}


def latest_live_option_chains(chain_dir: Path, tickers: tuple[str, ...] = SCANNER_TICKERS) -> dict[str, Any]:
    """Read latest captured option-chain timestamps without contacting a vendor."""

    if not chain_dir.is_dir():
        return {"ok": False, "reason": "missing_dir", "path": str(chain_dir)}
    per_ticker: dict[str, datetime] = {}
    for ticker in tickers:
        latest_path = chain_dir / f"latest_{ticker}.json"
        if not latest_path.is_file():
            continue
        try:
            payload = json.loads(latest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        observed = parse_dt(payload.get("as_of") or payload.get("timestamp"))
        if observed is not None:
            per_ticker[ticker] = observed
    latest = max(per_ticker.values(), default=None)
    return {
        "ok": bool(latest),
        "latest": latest,
        "per_ticker": per_ticker,
        "missing": [ticker for ticker in tickers if ticker not in per_ticker],
        "path": str(chain_dir),
        "reason": None if latest else "no_latest_snapshots",
    }


def status_from_latest(info: dict[str, Any], *, max_age_minutes: int, active: bool) -> tuple[str, str]:
    if not active:
        return "off_hours", "outside capture window"
    latest = info.get("latest")
    if not latest:
        return "stale", info.get("reason") or "no latest timestamp"
    age = (datetime.now(timezone.utc) - latest).total_seconds() / 60
    if age > max_age_minutes:
        return "stale", f"latest {age:.1f} minutes old"
    return "ok", f"latest {age:.1f} minutes old"


def load_state(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")


def run_post_market_maintenance(
    state: dict[str, Any],
    *,
    force: bool = False,
    dry_run: bool = False,
    now_et: datetime | None = None,
) -> dict[str, Any]:
    now = now_et or datetime.now(NY)
    day_key = now.date().isoformat()
    maintenance = state.setdefault("maintenance", {})
    if not force:
        if now.weekday() >= 5 or now.time() < time(16, 30):
            return {"status": "not_due", "day": day_key}
        if maintenance.get("last_successful_day") == day_key:
            return {"status": "already_completed", "day": day_key}
    if dry_run:
        return {"status": "dry_run", "day": day_key}

    commands = [
        ["/bin/bash", str(GOVERNANCE_RUNNER)],
        [
            sys.executable,
            str(ARCHIVE_RUNNER),
            "--keep-dates",
            "2",
            "--max-files",
            "4",
        ],
    ]
    results: list[dict[str, Any]] = []
    failed = False
    for command in commands:
        completed = subprocess.run(
            command,
            cwd=ROOT.parent,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=1800,
            check=False,
        )
        result = {
            "command": command,
            "returncode": completed.returncode,
            "output_tail": (completed.stdout or "")[-6000:],
        }
        results.append(result)
        if completed.returncode != 0:
            # Governance cataloguing and cold-data archival are independent safety
            # boundaries. A registry conflict must alert, but it must not prevent
            # verified archival and allow the capture disk to fill.
            failed = True

    maintenance.update(
        {
            "last_attempted_day": day_key,
            "last_attempted_at": utcnow(),
            "status": "failed" if failed else "ok",
            "results": results,
        }
    )
    if not failed:
        maintenance["last_successful_day"] = day_key
    return {"status": "failed" if failed else "ok", "day": day_key, "results": results}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send Cipher collector freshness alerts.")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--target", default=os.environ.get("CIPHER_HERMES_TARGET", "telegram"))
    parser.add_argument("--tradier-db", type=Path, default=DATA / "tradier_stream.sqlite")
    parser.add_argument("--gex-db", type=Path, default=DATA / "gex_history.sqlite")
    parser.add_argument("--tradier-max-age", type=int, default=int(os.environ.get("TRADIER_HEALTH_MAX_AGE_MIN", "20")))
    parser.add_argument("--gex-max-age", type=int, default=int(os.environ.get("GEX_HEALTH_MAX_AGE_MIN", "45")))
    parser.add_argument(
        "--option-chains-max-age",
        type=int,
        default=int(os.environ.get("OPTION_CHAINS_HEALTH_MAX_AGE_MIN", "15")),
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-maintenance", action="store_true")
    parser.add_argument("--force-maintenance", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    state = load_state(args.state)
    exit_code = 0
    checks = {
        "tradier": (
            latest_tradier(args.tradier_db),
            args.tradier_max_age,
            market_window("tradier"),
        ),
        "gex": (
            latest_gex(args.gex_db),
            args.gex_max_age,
            market_window("gex"),
        ),
        "live_option_chains": (
            latest_live_option_chains(LIVE_OPTION_CHAINS_DIR),
            args.option_chains_max_age,
            market_window("gex"),
        ),
    }
    changes = []
    now = utcnow()
    for name, (info, max_age, active) in checks.items():
        status, detail = status_from_latest(info, max_age_minutes=max_age, active=active)
        previous = state.get(name, {}).get("status")
        state[name] = {
            "status": status,
            "detail": detail,
            "checked_at": now,
            "latest": info.get("latest").isoformat() if info.get("latest") else None,
        }
        if status != previous and status != "off_hours":
            changes.append((name, status, detail, info))

    if changes:
        lines = ["Cipher data health change", f"Checked: {now}", ""]
        for name, status, detail, info in changes:
            lines.append(f"{name.upper()}: {status.upper()} - {detail}")
            if name == "live_option_chains":
                per_ticker = info.get("per_ticker") or {}
                if per_ticker:
                    lines.append(
                        "Latest: "
                        + ", ".join(
                            f"{ticker} {observed.isoformat()}"
                            for ticker, observed in list(per_ticker.items())[:5]
                        )
                    )
                missing = info.get("missing") or []
                if missing:
                    lines.append("Missing: " + ", ".join(missing))
            else:
                rows = info.get("rows") or []
                if rows:
                    lines.append("Latest: " + ", ".join(f"{r[0]} {r[1]}" for r in rows[:3]))
        message = "\n".join(lines)
        if args.dry_run:
            print(message)
        else:
            rc = send_hermes_message(message, target=args.target)
            if rc != 0:
                exit_code = rc
    else:
        print(json.dumps({"ok": True, "changes": 0, "checked_at": now, "state": state}))
    if not args.skip_maintenance:
        maintenance = run_post_market_maintenance(
            state,
            force=args.force_maintenance,
            dry_run=args.dry_run,
        )
        print(json.dumps({"maintenance": maintenance}, default=str))
        if maintenance.get("status") == "failed":
            exit_code = exit_code or 1
            if not args.dry_run:
                try:
                    send_hermes_message(
                        "Cipher post-market maintenance failed. Check cipher-data-health-alert journal.",
                        target=args.target,
                    )
                except Exception:
                    pass
    if not args.dry_run:
        save_state(args.state, state)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
