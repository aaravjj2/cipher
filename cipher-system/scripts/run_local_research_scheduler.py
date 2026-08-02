#!/usr/bin/env python3
"""Evaluate due guarded research jobs and persist their readiness state."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.research_platform.local_capabilities import build_local_capability_report  # noqa: E402
from core.research_platform.local_scheduler import run_due  # noqa: E402


def main() -> int:
    capabilities = build_local_capability_report(ROOT.parent)
    result = run_due(capabilities, ROOT / "data" / "governance" / "local_research_scheduler.json")
    print(json.dumps(result["last_run"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
