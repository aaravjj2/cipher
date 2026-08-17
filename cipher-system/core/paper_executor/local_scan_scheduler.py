"""Run local Flash scans and submit them to the loopback shadow executor.

This is an observation scheduler, not an order runner. It calls Cipher's read-only
scanner and sends normalized cards to the local simulated paper book. The executor's
promotion gate, portfolio policy, contract filters, and synthetic fill model remain
the authority for everything after ingestion.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime, time, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo


NEW_YORK = ZoneInfo("America/New_York")
DEFAULT_TICKERS = (
    "NVDA", "MSFT", "AAPL", "AVGO", "AMZN", "IBIT",
    "GOOGL", "TSLA", "META", "MU", "AMD", "QQQ",
)
DEFAULT_STRATEGIES = ("flash", "flash_agentic")
SUPPORTED_SCHEDULED_STRATEGIES = (*DEFAULT_STRATEGIES, "cipher")


def in_entry_window(
    now: datetime | None = None,
    *,
    start: time = time(9, 35),
    end: time = time(15, 0),
) -> bool:
    """Return whether ``now`` is in the weekday entry window in New York."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    local = current.astimezone(NEW_YORK)
    return local.weekday() < 5 and start <= local.time().replace(tzinfo=None) <= end


def scanner_url(
    base_url: str,
    strategy: str,
    tickers: Iterable[str],
    *,
    workers: int = 1,
) -> str:
    if strategy not in SUPPORTED_SCHEDULED_STRATEGIES:
        raise ValueError(f"unsupported scheduled strategy: {strategy}")
    params = urllib.parse.urlencode(
        {
            "tickers": ",".join(tickers),
            "mode": "short",
            "strategy": strategy,
            "limit": 40,
            # The account's market-data rate budget is shared with GEX capture.
            "workers": max(1, min(int(workers), 1)),
        }
    )
    return f"{base_url.rstrip('/')}/api/scan?{params}"


def executor_payload(strategy: str, scan: dict[str, Any], captured_at: str) -> dict[str, Any]:
    cards = scan.get("top")
    if not isinstance(cards, list):
        raise ValueError("scanner response did not contain a top list")
    return {
        "source": "cipher_local_scanner",
        "scan_type": strategy,
        "captured_at": captured_at,
        "cards": [
            {
                **card,
                "scanner_type": strategy,
                "captured_at": captured_at,
            }
            for card in cards
            if isinstance(card, dict)
        ],
    }


def request_json(url: str, *, payload: dict[str, Any] | None = None, timeout: int = 600) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method="POST" if payload is not None else "GET",
        headers={"Content-Type": "application/json"} if payload is not None else {},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not isinstance(result, dict):
        raise ValueError(f"non-object JSON response from {url}")
    return result


def run(args: argparse.Namespace) -> int:
    now = datetime.now(timezone.utc)
    if not args.force and not in_entry_window(now):
        print(json.dumps({"status": "outside_entry_window", "as_of": now.isoformat()}))
        return 0

    summaries: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for strategy in args.strategies:
        try:
            scan = request_json(
                scanner_url(args.core_url, strategy, args.tickers, workers=args.workers),
                timeout=args.timeout_seconds,
            )
            captured_at = datetime.now(timezone.utc).isoformat()
            payload = executor_payload(strategy, scan, captured_at)
            accepted = request_json(
                args.executor_url,
                payload=payload,
                timeout=min(args.timeout_seconds, 30),
            )
            summaries.append(
                {
                    "strategy": strategy,
                    "scanned": scan.get("scanned"),
                    "qualified": scan.get("qualified"),
                    "actionable": scan.get("actionable"),
                    "cards_submitted": len(payload["cards"]),
                    "batch_id": accepted.get("batch_id"),
                }
            )
        except Exception as exc:  # one strategy must not suppress the other's observation
            errors.append({"strategy": strategy, "error": str(exc)})

    print(json.dumps({"status": "completed", "runs": summaries, "errors": errors}, sort_keys=True))
    return 1 if errors else 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core-url", default="http://127.0.0.1:8282")
    parser.add_argument("--executor-url", default="http://127.0.0.1:8787/api/scanner-ingest")
    parser.add_argument("--strategies", nargs="+", choices=DEFAULT_STRATEGIES, default=list(DEFAULT_STRATEGIES))
    parser.add_argument("--tickers", nargs="+", default=list(DEFAULT_TICKERS))
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument("--force", action="store_true", help="run outside the entry window (diagnostics only)")
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(run(parse_args()))
