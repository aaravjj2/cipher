#!/usr/bin/env python3
"""Send read-only Cipher scanner alerts through Hermes.

This script polls the local Cipher core API, detects new cluster-style entries,
and sends a compact Telegram message through ``hermes send``.  It never calls
brokerage, account, order, preview, or trading endpoints.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hermes_delivery import send_hermes_message


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE = ROOT / "data" / "alerts" / "cluster_seen.json"
DEFAULT_CORE = os.environ.get("CIPHER_CORE_URL", "http://127.0.0.1:8282")
DEFAULT_TICKERS = (
    "SPY,QQQ,IWM,DIA,SMH,AAPL,NVDA,TSLA,AMD,MSFT,META,AMZN,"
    "GOOGL,AVGO,COIN,PLTR,BABA,NFLX"
)
SIGNAL_KINDS = {"quad", "triple", "battle"}

# Hosted-mode responses that mean the alert pass is currently dormant rather
# than broken: the core demands an internal token we should present, and/or a
# user-entered Alpaca provider session that a scheduler service cannot own.
# In that state the pass records a suspension and exits 0 instead of spamming
# failure alerts every cycle.
SUSPENDED_MARKERS = (
    "internal authentication required",
    "internal authentication failed",
    "user context required",
    "alpaca provider session is required",
)


def _core_request_headers() -> dict[str, str]:
    """Headers for the local core: internal token plus an operator/guest context.

    The core requires X-Cipher-Internal-Token when CIPHER_INTERNAL_PROXY_TOKEN
    is configured (hosted mode). The guest context is not a user session; it
    just lets the request reach the provider-session check so the pass can
    report an honest suspended state instead of a bare 401.
    """
    headers = {"Accept": "application/json"}
    token = os.environ.get("CIPHER_INTERNAL_PROXY_TOKEN")
    if token:
        headers["X-Cipher-Internal-Token"] = token
        headers["X-Cipher-Guest"] = "1"
        headers["X-Cipher-User-Id"] = "guest"
    return headers


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_state(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"seen": {}, "runs": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"seen": {}, "runs": []}
    if not isinstance(payload, dict):
        return {"seen": {}, "runs": []}
    payload.setdefault("seen", {})
    payload.setdefault("runs", [])
    return payload


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    runs = list(state.get("runs") or [])[-100:]
    state["runs"] = runs
    path.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")


def fetch_scan(core_url: str, *, tickers: str, limit: int, timeout: int) -> dict[str, Any]:
    params = {
        "strategy": "cluster",
        "mode": "short",
        "limit": str(max(1, min(limit, 50))),
        "cluster_exp": "nearest",
        "tickers": tickers,
        "fresh": "1",
    }
    url = f"{core_url.rstrip('/')}/api/scan?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers=_core_request_headers())
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def num(value: Any, digits: int = 2) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "n/a"


def setup_key(ticker: str, setup: dict[str, Any]) -> str:
    kind = str(setup.get("kind") or "").lower()
    center = setup.get("center")
    try:
        center_key = f"{float(center):.2f}"
    except (TypeError, ValueError):
        center_key = str(center or "")
    side = str(setup.get("side") or "")
    return "|".join([ticker.upper(), kind, side, center_key])


def collect_new_entries(payload: dict[str, Any], state: dict[str, Any]) -> list[dict[str, Any]]:
    seen: dict[str, str] = state.setdefault("seen", {})
    new_entries: list[dict[str, Any]] = []
    for row in payload.get("top") or []:
        ticker = str(row.get("ticker") or "").upper()
        if not ticker:
            continue
        for setup in row.get("setups") or []:
            kind = str(setup.get("kind") or "").lower()
            if kind not in SIGNAL_KINDS:
                continue
            key = setup_key(ticker, setup)
            if key in seen:
                continue
            new_entries.append({"row": row, "setup": setup, "key": key})
    return new_entries


def mark_entries_seen(state: dict[str, Any], entries: list[dict[str, Any]]) -> None:
    seen: dict[str, str] = state.setdefault("seen", {})
    marked_at = utcnow()
    for entry in entries:
        seen[str(entry["key"])] = marked_at


def format_message(payload: dict[str, Any], entries: list[dict[str, Any]]) -> str:
    as_of = str(payload.get("as_of") or utcnow())
    scanned = payload.get("scanned")
    qualified = payload.get("qualified")
    failed = payload.get("failed")
    lines = [
        "Cipher cluster alert",
        f"As of: {as_of}",
        f"Scanned: {scanned} | Qualified: {qualified} | Failed: {failed}",
        "",
    ]
    for item in entries[:12]:
        row = item["row"]
        setup = item["setup"]
        ticker = row.get("ticker")
        kind = str(setup.get("kind") or "").upper()
        center = num(setup.get("center"))
        low = num(setup.get("low"))
        high = num(setup.get("high"))
        spot = num(row.get("spot"))
        score = num(row.get("score"), 1)
        target = num(row.get("pull_target"))
        invalid = row.get("close_under") if row.get("close_under") is not None else row.get("reclaim")
        lines.append(
            f"{ticker}: {kind} score {score} spot {spot} zone {low}-{high} center {center} target {target} invalid {num(invalid)}"
        )
    if len(entries) > 12:
        lines.append(f"...and {len(entries) - 12} more new entries.")
    lines.append("")
    lines.append("Read-only alert. GEX is public-OI heuristic, not verified dealer positioning.")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send new Cipher cluster scan entries to Hermes.")
    parser.add_argument("--core-url", default=DEFAULT_CORE)
    parser.add_argument("--tickers", default=os.environ.get("CIPHER_SCAN_ALERT_TICKERS", DEFAULT_TICKERS))
    parser.add_argument("--limit", type=int, default=int(os.environ.get("CIPHER_SCAN_ALERT_LIMIT", "30")))
    parser.add_argument("--timeout", type=int, default=int(os.environ.get("CIPHER_SCAN_ALERT_TIMEOUT", "900")))
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--target", default=os.environ.get("CIPHER_HERMES_TARGET", "telegram"))
    parser.add_argument("--always-summary", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    state = load_state(args.state)
    run = {"started_at": utcnow(), "tickers": args.tickers, "ok": False}
    try:
        payload = fetch_scan(args.core_url, tickers=args.tickers, limit=args.limit, timeout=args.timeout)
        entries = collect_new_entries(payload, state)
        run.update(
            {
                "ok": True,
                "completed_at": utcnow(),
                "scanned": payload.get("scanned"),
                "qualified": payload.get("qualified"),
                "failed": payload.get("failed"),
                "new_entries": len(entries),
            }
        )
        if entries:
            message = format_message(payload, entries)
            if args.dry_run:
                print(message)
            else:
                return_code = send_hermes_message(message, target=args.target)
                run["send_returncode"] = return_code
                if return_code != 0:
                    run["ok"] = False
                else:
                    mark_entries_seen(state, entries)
        elif args.always_summary:
            message = (
                "Cipher cluster scan: no new quad/triple/battle entries. "
                f"Scanned {payload.get('scanned')}, qualified {payload.get('qualified')}, failed {payload.get('failed')}."
            )
            if args.dry_run:
                print(message)
            else:
                run["send_returncode"] = send_hermes_message(message, target=args.target)
                if run["send_returncode"] != 0:
                    run["ok"] = False
        else:
            print(
                json.dumps(
                    {
                        "ok": True,
                        "new_entries": 0,
                        "scanned": payload.get("scanned"),
                        "qualified": payload.get("qualified"),
                        "failed": payload.get("failed"),
                    }
                )
            )
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:
            pass
        if exc.code in (401, 422) and any(marker in body.lower() for marker in SUSPENDED_MARKERS):
            run.update({"ok": True, "completed_at": utcnow(), "status": "suspended_provider_session",
                        "http_code": exc.code, "detail": body[:200]})
            if not args.dry_run:
                state.setdefault("runs", []).append(run)
                save_state(args.state, state)
            print(json.dumps({"ok": True, "status": "suspended_provider_session",
                              "reason": "hosted core has no provider session; alerting suspended"},
                             sort_keys=True))
            return 0
        run.update({"ok": False, "completed_at": utcnow(), "error": f"HTTP {exc.code}: {body[:200]}"})
        message = f"Cipher cluster scan alert error at {run['completed_at']}: HTTP {exc.code}: {body[:200]}"
        if args.dry_run:
            print(message, file=sys.stderr)
        else:
            try:
                send_hermes_message(message, target=args.target)
            except Exception:
                pass
        if not args.dry_run:
            state.setdefault("runs", []).append(run)
            save_state(args.state, state)
        return 1
    except Exception as exc:
        run.update({"ok": False, "completed_at": utcnow(), "error": str(exc)})
        message = f"Cipher cluster scan alert error at {run['completed_at']}: {exc}"
        if args.dry_run:
            print(message, file=sys.stderr)
        else:
            try:
                send_hermes_message(message, target=args.target)
            except Exception:
                pass
        if not args.dry_run:
            state.setdefault("runs", []).append(run)
            save_state(args.state, state)
        return 1

    if not args.dry_run:
        state.setdefault("runs", []).append(run)
        save_state(args.state, state)
    return 0 if run.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
