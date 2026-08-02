#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = ROOT.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.research_platform.local_capabilities import build_local_capability_report  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Describe Cipher's local guarded research capabilities.")
    parser.add_argument("--external-root", help="External repository root to inspect.")
    parser.add_argument("--json", action="store_true", help="Emit the complete capability report as JSON.")
    args = parser.parse_args(argv)
    kwargs = {"external_root": args.external_root} if args.external_root else {}
    report = build_local_capability_report(REPOSITORY_ROOT, **kwargs)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        kronos = report["models"]["kronos"]
        timesfm = report["models"]["timesfm"]
        external = report["external_integrations"]
        print("Cipher local research capabilities")
        print(f"Kronos inference ready: {kronos['ready_for_inference']}")
        print(f"TimesFM prospective ready: {timesfm['ready_for_prospective_forecast']}")
        print(f"External repositories: {external['available_count']}/{external['total_count']}")
        print(f"Boundary violations: {len(external['boundary_violations'])}")
        print(f"Promotion ceiling: {report['execution_boundary']['maximum_promotion_state']}")
    return 1 if report["external_integrations"]["boundary_violations"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
