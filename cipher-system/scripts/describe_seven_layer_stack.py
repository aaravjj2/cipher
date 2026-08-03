#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.research_platform.seven_layer_stack import EightLayerStackSpec  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Describe Cipher's guarded eight-layer research stack.")
    parser.add_argument("--json", action="store_true", help="Emit the full offline orchestration plan as JSON.")
    parser.add_argument("--tables", action="store_true", help="List canonical warehouse tables required by the stack.")
    args = parser.parse_args(argv)

    spec = EightLayerStackSpec.default()
    violations = [item.to_dict() for item in spec.validate_boundaries()]
    if args.tables:
        for table in spec.warehouse_tables:
            print(table)
        return 1 if violations else 0
    payload = spec.offline_orchestration_plan()
    payload["boundary_violations"] = violations
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print("Cipher eight-layer guarded research stack")
        print(f"promotion ceiling: {payload['maximum_promotion_state']}")
        print(f"boundary violations: {len(violations)}")
        for step in payload["steps"]:
            writes = ", ".join(step["writes"])
            print(f"{step['layer']}. {step['name']} [{step['mode']}] -> {writes}")
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
