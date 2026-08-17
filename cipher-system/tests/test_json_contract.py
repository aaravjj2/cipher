from __future__ import annotations

import json
import math

from core.app import _json_safe
from core.finviz_discovery import normalize_ticker_rows


def test_http_json_boundary_maps_nonfinite_provider_values_to_unknown() -> None:
    payload = _json_safe({"rows": [{"pe": math.nan, "ratio": math.inf}], "ok": 1.25})
    encoded = json.dumps(payload, allow_nan=False)
    assert json.loads(encoded) == {"rows": [{"pe": None, "ratio": None}], "ok": 1.25}


def test_finviz_normalization_does_not_persist_nan() -> None:
    rows = normalize_ticker_rows([{"Ticker": "AAPL", "P/E": math.nan}])
    assert rows == [{"Ticker": "AAPL", "P/E": None}]
    json.dumps(rows, allow_nan=False)
