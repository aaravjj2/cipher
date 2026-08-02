#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.research_platform.external_integrations import DEFAULT_EXTERNAL_ROOT, integration_status  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Describe Cipher's gated external repository integrations.")
    parser.add_argument("--root", default=str(DEFAULT_EXTERNAL_ROOT), help="External repository root to inspect.")
    parser.add_argument("--json", action="store_true", help="Emit status as JSON.")
    args = parser.parse_args(argv)
    status = integration_status(args.root)
    if args.json:
        print(json.dumps(status, indent=2, sort_keys=True))
    else:
        print("Cipher external integrations")
        print(f"root: {status['root']}")
        print(f"available: {status['available_count']}/{status['total_count']}")
        print(f"boundary violations: {len(status['boundary_violations'])}")
        for item in status["integrations"]:
            marker = "available" if item["available"] else "missing"
            blocked = ", ".join(item["blocked_capabilities"]) or "none"
            print(f"- {item['name']} [{marker}] layer={item['layer']} activation={item['activation']}")
            print(f"  role: {item['role']}")
            print(f"  blocked: {blocked}")
    return 1 if status["boundary_violations"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
