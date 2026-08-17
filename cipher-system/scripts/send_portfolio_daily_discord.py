#!/usr/bin/env python3
"""Send the daily six-portfolio delta through Cipher's existing Discord webhook."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import portfolio_daily_report as report  # noqa: E402

NOTIFIER = Path("/home/aarav/Aarav/agent-stack/discord-notify.sh")


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
    if not os.environ.get("DISCORD_PROGRESS_WEBHOOK"):
        raise SystemExit("DISCORD_PROGRESS_WEBHOOK is not configured")
    if not NOTIFIER.is_file():
        raise SystemExit(f"existing Discord notifier is missing: {NOTIFIER}")

    def sender(message: str) -> None:
        subprocess.run(["/usr/bin/bash", str(NOTIFIER), message], check=True, timeout=30)

    result = report.deliver(
        sender, db_path=args.db, prospective_db_path=args.prospective_db, force=args.force
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
