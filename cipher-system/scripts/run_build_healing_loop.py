#!/usr/bin/env python3
"""Run Cipher's bounded build/test healing cycle once or on source changes."""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = ROOT.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.research_platform.build_healing import (  # noqa: E402
    DEFAULT_GOVERNANCE_ROOT,
    HealingPolicy,
    latest_run,
    run_healing_cycle,
    source_fingerprint,
    source_snapshot,
)

STOP_REQUESTED = False


def request_stop(_signum: int, _frame: object) -> None:
    global STOP_REQUESTED
    STOP_REQUESTED = True


def compact(payload: dict) -> dict:
    suites = payload.get("validation_suites", [])
    final_suite = suites[-1] if suites else {}
    return {
        "time": datetime.now(timezone.utc).isoformat(),
        "status": payload.get("status"),
        "source_fingerprint": payload.get("source_fingerprint"),
        "completed_steps": final_suite.get("completed_step_count", 0),
        "failed_step": (final_suite.get("failed_step") or {}).get("name"),
        "repair_actions": len(payload.get("repair_actions", [])),
        "execution_authority": False,
    }


def run_once(args: argparse.Namespace) -> dict:
    return run_healing_cycle(
        repository_root=REPOSITORY_ROOT,
        system_root=ROOT,
        policy=HealingPolicy(
            max_heal_cycles=args.max_heal_cycles,
            command_retry_attempts=args.command_retry_attempts,
            output_tail_chars=args.output_tail_chars,
        ),
    )


def loop(args: argparse.Namespace) -> int:
    interval = max(30, args.interval_seconds)
    previous = latest_run(DEFAULT_GOVERNANCE_ROOT)
    last_attempted = previous.get("source_fingerprint")
    run_on_start = bool(args.run_on_start or not previous)
    while not STOP_REQUESTED:
        snapshot = source_snapshot(REPOSITORY_ROOT)
        fingerprint = source_fingerprint(snapshot)
        if run_on_start or fingerprint != last_attempted:
            payload = run_once(args)
            print(json.dumps(compact(payload), sort_keys=True), flush=True)
            # A validation-induced source mutation remains blocked at the
            # resulting fingerprint instead of immediately revalidating it.
            last_attempted = (
                payload.get("final_source_fingerprint")
                or payload.get("source_fingerprint")
                or fingerprint
            )
            run_on_start = False
        slept = 0
        while slept < interval and not STOP_REQUESTED:
            time.sleep(min(1, interval - slept))
            slept += 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="Run one bounded validation/healing cycle.")
    mode.add_argument("--loop", action="store_true", help="Watch source fingerprints and validate on changes.")
    parser.add_argument("--run-on-start", action="store_true", help="Run immediately even when the source fingerprint is unchanged.")
    parser.add_argument("--interval-seconds", type=int, default=60)
    parser.add_argument("--max-heal-cycles", type=int, default=1)
    parser.add_argument("--command-retry-attempts", type=int, default=2)
    parser.add_argument("--output-tail-chars", type=int, default=6000)
    args = parser.parse_args()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    os.environ.setdefault("CIPHER_BUILD_HEALING", "1")

    if args.loop:
        return loop(args)
    payload = run_once(args)
    print(json.dumps(compact(payload), indent=2, sort_keys=True))
    return 0 if payload.get("status") in {"passed", "healed_passed"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
