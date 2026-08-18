#!/usr/bin/env python3
"""Send the daily six-portfolio delta through Cipher's existing Discord webhook."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import urllib.request
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import portfolio_daily_report as report  # noqa: E402


def send_webhook(message: str, webhook_url: str) -> None:
    """Post a plain-text message to a Discord webhook via the standard library."""
    payload = json.dumps({"content": message}).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "Cipher-Portfolio-Digest/1.0"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as response:
        if response.getcode() not in (200, 204):
            raise RuntimeError(f"Discord webhook returned HTTP {response.getcode()}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=report.DEFAULT_DB)
    parser.add_argument("--prospective-db", type=Path, default=report.DEFAULT_PROSPECTIVE_DB)
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    if args.preview:
        print(json.dumps(report.preview(args.db, prospective_db_path=args.prospective_db), indent=2))
        return 0
    webhook_url = os.environ.get("DISCORD_PROGRESS_WEBHOOK") or os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        raise SystemExit("DISCORD_PROGRESS_WEBHOOK is not configured")

    def sender(message: str) -> None:
        send_webhook(message, webhook_url)

    result = report.deliver(
        sender, db_path=args.db, prospective_db_path=args.prospective_db, force=args.force
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
