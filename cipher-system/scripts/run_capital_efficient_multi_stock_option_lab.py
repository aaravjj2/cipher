#!/usr/bin/env python3
"""CLI entrypoint for the frozen capital-efficient multi-stock option study."""
from __future__ import annotations

import sys
from pathlib import Path

CORE = Path(__file__).resolve().parents[1] / "core"
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

from capital_efficient_multi_stock_option_lab import main


if __name__ == "__main__":
    raise SystemExit(main())
