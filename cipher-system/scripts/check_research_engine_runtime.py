#!/usr/bin/env python3
"""Verify optional research engine imports without starting a backtest or agent."""

from __future__ import annotations

import importlib
import json
import sys


MODULES = {
    "transformers": "transformers",
    "vectorbt": "vectorbt",
    "qlib": "qlib",
    "riskfolio": "riskfolio",
    "rdagent": "rdagent",
    "torch": "torch",
    "timesfm": "timesfm",
    "duckdb": "duckdb",
    "huggingface_hub": "huggingface_hub",
    "safetensors": "safetensors",
    "einops": "einops",
    "pandas": "pandas",
    "pyarrow": "pyarrow",
    "yfinance": "yfinance",
    "hurst": "hurst",
    "lean_cli": "lean",
}


def main() -> int:
    results = {}
    for label, module_name in MODULES.items():
        try:
            module = importlib.import_module(module_name)
            results[label] = {"available": True, "version": getattr(module, "__version__", "unknown")}
        except Exception as exc:
            results[label] = {"available": False, "error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps({"python": sys.version, "engines": results, "execution_authority": False}, indent=2))
    return 0 if all(item["available"] for item in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
