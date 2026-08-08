#!/usr/bin/env python3
"""Export the first qualifying Tier A Cluster episode per ticker/session.

Read-only research export. The modeled spread uses the first traded minute for
each leg after the signal and is not a broker fill or proof of simultaneous
execution.
"""
from __future__ import annotations

import argparse
import base64
import csv
import gzip
import io
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "governance" / "cipher_signal_only" / "latest_cluster_individual_analysis.json"
NY = ZoneInfo("America/New_York")


def nested(row: Mapping[str, Any], *keys: str) -> Any:
    value: Any = row
    for key in keys:
        value = value.get(key) if isinstance(value, Mapping) else None
    return value


def eastern(value: Any) -> str | None:
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(NY).isoformat()


def rows() -> list[dict[str, Any]]:
    payload = json.loads(SOURCE.read_text(encoding="utf-8"))
    candidates = [
        row
        for row in payload["records"]
        if nested(row, "standalone_assessment", "research_tier") == "tier_a_cluster_only"
    ]
    candidates.sort(
        key=lambda row: (
            str(row.get("market_session") or ""),
            str(row.get("ticker") or ""),
            str(row.get("first_seen_at") or ""),
        )
    )
    selected: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in candidates:
        key = (str(row.get("market_session")), str(row.get("ticker")))
        if key in seen:
            continue
        seen.add(key)
        preferred: list[str] = []
        if float(row.get("target_distance_pct") or 0.0) >= 5.0:
            preferred.append("target_5_10_pct")
        if 200.0 <= float(row.get("strength") or -1.0) < 250.0:
            preferred.append("strength_200_249")
        if row.get("signal_time_bucket") == "1030_1159_et":
            preferred.append("1030_1159_et")
        feature_count = len(preferred)
        priority = {3: "Priority 1", 2: "Priority 2", 1: "Priority 3", 0: "Base Tier A"}[feature_count]
        selected.append(
            {
                "trade_no": len(selected) + 1,
                "market_session": row.get("market_session"),
                "ticker": row.get("ticker"),
                "signal_time_et": eastern(row.get("first_seen_at")),
                "signal_id": row.get("signal_id"),
                "appearance_number": row.get("episode_number_for_ticker_session"),
                "ticker_day_cluster_episodes": row.get("episodes_for_ticker_session"),
                "rank": row.get("rank"),
                "strength": row.get("strength"),
                "spot": row.get("spot"),
                "target": row.get("target"),
                "target_distance_pct": row.get("target_distance_pct"),
                "signal_time_bucket": row.get("signal_time_bucket"),
                "cluster_expiration": row.get("cluster_expiration"),
                "long_atm_symbol": nested(row, "atm_contract", "symbol"),
                "long_atm_strike": nested(row, "atm_contract", "strike_price"),
                "short_target_symbol": nested(row, "target_contract", "symbol"),
                "short_target_strike": nested(row, "target_contract", "strike_price"),
                "long_entry_time_et": eastern(nested(row, "atm_option", "entry_at")),
                "long_entry_price": nested(row, "atm_option", "entry_price"),
                "short_entry_time_et": eastern(nested(row, "target_option", "entry_at")),
                "short_entry_price": nested(row, "target_option", "entry_price"),
                "modeled_entry_debit": nested(row, "debit_spread", "entry_debit"),
                "status": row.get("status"),
                "mark_session": nested(row, "underlying", "mark_session"),
                "underlying_mark_price": nested(row, "underlying", "mark_price"),
                "underlying_directional_return_pct": nested(row, "underlying", "directional_return_pct"),
                "maximum_favorable_move_pct": nested(row, "underlying", "maximum_favorable_move_pct"),
                "maximum_adverse_move_pct": nested(row, "underlying", "maximum_adverse_move_pct"),
                "target_hit": nested(row, "underlying", "target_hit_by_mark"),
                "atm_option_return_pct": nested(row, "atm_option", "end_return_pct"),
                "atm_option_max_return_pct": nested(row, "atm_option", "maximum_return_pct"),
                "target_option_return_pct": nested(row, "target_option", "end_return_pct"),
                "target_option_max_return_pct": nested(row, "target_option", "maximum_return_pct"),
                "debit_spread_return_pct": nested(row, "debit_spread", "end_return_pct"),
                "priority_feature_count": feature_count,
                "research_priority": priority,
                "preferred_features": "|".join(preferred),
                "source_file": row.get("source_file"),
            }
        )
    return selected


def encoded_csv() -> str:
    values = rows()
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(values[0]))
    writer.writeheader()
    writer.writerows(values)
    return base64.b64encode(gzip.compress(buffer.getvalue().encode("utf-8"))).decode("ascii")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--chunk", type=int)
    parser.add_argument("--chunk-size", type=int, default=6000)
    parser.add_argument("--count", action="store_true")
    args = parser.parse_args()
    encoded = encoded_csv()
    if args.count:
        print(json.dumps({"rows": len(rows()), "encoded_characters": len(encoded)}))
        return 0
    if args.chunk is not None:
        start = args.chunk * args.chunk_size
        print(encoded[start : start + args.chunk_size])
        return 0
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
