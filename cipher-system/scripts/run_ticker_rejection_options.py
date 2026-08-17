#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.ticker_rejection_options import confirm_report  # noqa: E402


if __name__ == "__main__":
    result = confirm_report()
    print(json.dumps({ticker: value.get("summary", value) for ticker, value in result["by_ticker"].items()}, indent=2))
