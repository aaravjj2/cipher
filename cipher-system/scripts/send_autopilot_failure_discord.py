#!/usr/bin/env python3
"""Send one recent, deduplicated paper-autopilot failure to Discord."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.autopilot_notifications import deliver_latest_failure  # noqa: E402
from scripts.send_portfolio_daily_discord import send_webhook  # noqa: E402

RUNTIME = Path(os.environ.get("CIPHER_PAPER_RUNTIME", "/home/aarav/Aarav/cipher/runtime/data/paper_runtime"))


def main() -> int:
    webhook = os.environ.get("DISCORD_PROGRESS_WEBHOOK") or os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook:
        print(json.dumps({"status": "disabled", "reason": "discord_webhook_not_configured"}))
        return 0
    result = deliver_latest_failure(
        lambda message: send_webhook(message, webhook),
        db_path=RUNTIME / "data/paper_trades/autopilot_shadow.sqlite",
        state_path=RUNTIME / "autopilot/notification_state.json",
    )
    cohorts = {}
    for name in ("confirmation", "cost", "exit"):
        root = RUNTIME / "cohorts" / name
        if (root / "paper.sqlite").exists():
            cohorts[name] = deliver_latest_failure(
                lambda message, name=name: send_webhook(f"[{name}] {message}", webhook),
                db_path=root / "paper.sqlite", state_path=root / "notification_state.json",
            )
    result["cohorts"] = cohorts
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
