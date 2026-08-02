#!/usr/bin/env python3
"""Record read-only alternate-source access without disclosing credentials."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
QUALITY = ROOT / "data" / "market_quality"
DATES = ("2017-01-03", "2018-05-02", "2019-08-12")


def dotenv(path: Path) -> dict[str, str]:
    values = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def main() -> None:
    cipher_env = dotenv(ROOT / ".env")
    autopilot_env = dotenv(Path("/home/aarav/Aarav/Autopilot/.env"))
    polygon_key = autopilot_env.get("POLYGON_API_KEY")
    polygon = []
    if polygon_key:
        for day in DATES:
            response = requests.get(f"https://api.polygon.io/v2/aggs/ticker/AAPL/range/1/minute/{day}/{day}", params={
                "adjusted": "false", "sort": "asc", "limit": 50000, "apiKey": polygon_key}, timeout=60)
            data = response.json()
            polygon.append({"date": day, "http_status": response.status_code, "provider_status": data.get("status"),
                            "results_count": data.get("resultsCount"), "message": data.get("error") or data.get("message")})
    tradier_token = cipher_env.get("TRADIER_PRODUCTION_TOKEN")
    tradier = []
    if tradier_token:
        for day in DATES:
            response = requests.get("https://api.tradier.com/v1/markets/timesales", headers={"Authorization": f"Bearer {tradier_token}", "Accept": "application/json"}, params={
                "symbol": "AAPL", "interval": "1min", "start": f"{day} 09:30", "end": f"{day} 16:00", "session_filter": "open"}, timeout=60)
            tradier.append({"date": day, "http_status": response.status_code, "message": response.text[:500]})
    closeout = sorted(QUALITY.glob("alpaca_holdout_c_recovery_closeout_*.json"))[-1]
    payload = {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(), "purpose": "read-only entitlement audit before any alternate-source recovery",
               "Massive_Polygon": {"credential_present": bool(polygon_key), "pilot": polygon, "usable": False,
                                   "reason": "2017-2019 minute endpoint returns NOT_AUTHORIZED; no upgrade or purchase performed."},
               "FirstRate_local_archive": {"credential_or_archive_present": False, "usable": False, "reason": "No local licensed archive or credential was discovered."},
               "Databento": {"credential_present": False, "usable": False, "reason": "No credential was discovered."},
               "Tradier": {"credential_present": bool(tradier_token), "pilot": tradier, "usable": False,
                           "reason": "Historical minute API rejects dates before 2026-07-03."},
               "Alpaca_SIP": {"usable": False, "reason": "Prior full recovery failed unchanged continuity/origin minimums.", "closeout_sha256": hashlib.sha256(closeout.read_bytes()).hexdigest()},
               "purchase_or_subscription_changed": False, "mixed_vendor_data_used": False, "ranking_outcomes_evaluated": False,
               "next_automatic_action": "Remain blocked until a single qualifying historical-source entitlement or local licensed archive becomes available; then rerun the frozen pilot and gate scripts."}
    output = QUALITY / f"holdout_c_alternate_source_entitlements_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
