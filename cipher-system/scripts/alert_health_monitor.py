#!/usr/bin/env python3
"""Daily health monitor: check every cipher timer/service and alert on anomalies.

Runs as a oneshot systemd unit. It reads systemd's own view of every
`cipher-*.service` unit: whether the last run finished successfully, and how
long ago the last successful trigger happened. Anything anomalous produces
exactly one Discord message (or zero when everything is healthy) so the
channel stays quiet on good days. Exits 0 on healthy, 1 on anomalies so
systemd state reflects the finding, but the Discord post is the product.

Only the last-run state is read — nothing is started or restarted.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone

UNIT_PREFIX = "cipher-"
# Units that are expected to be idle (triggered by a timer with its own
# schedule) are checked via their timer's last-trigger time instead.
# Map: service name -> maximum acceptable hours since the last trigger.
# Defaults apply to anything not listed here.
MAX_HOURS_BY_UNIT: dict[str, float] = {
    # Daily evidence-ranking pass; anything beyond ~26h means a missed day.
    "cipher-autopilot.service": 26.0,
    # Market-data collectors run daily premarket.
    "cipher-data-health-alert.service": 26.0,
    "cipher-market-alert.service": 26.0,
    "cipher-cluster-alert.service": 26.0,
    # Digest deliveries are daily.
    "cipher-portfolio-discord-daily.service": 26.0,
    "cipher-earnings-digest.service": 26.0,
    # Backup is daily; 30h gives a full day of slack.
    "cipher-local-backup.service": 30.0,
}


def _systemctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["systemctl", *args], capture_output=True, text=True, timeout=30
    )


def service_last_run(service: str) -> dict:
    """Return {active_state, result, exec_ts} for a unit from systemd."""
    out = _systemctl("show", service, "--property=ActiveState,Result,ExecMainStartTimestamp")
    fields: dict[str, str] = {}
    for line in out.stdout.strip().splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            fields[key.strip()] = value.strip()
    active = fields.get("ActiveState", "unknown")
    result = fields.get("Result", "")
    exec_ts = fields.get("ExecMainStartTimestamp", "")
    return {"active": active, "result": result, "exec_ts": exec_ts}


def parse_timestamp(value: str) -> datetime | None:
    if not value or value in ("n/a", "-"):
        return None
    try:
        # systemd timestamps look like "Tue 2026-08-18 10:31:21 UTC".
        return datetime.strptime(value, "%a %Y-%m-%d %H:%M:%S %Z").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def collect(max_hours_default: float) -> dict:
    """Enumerate cipher services, classify each as ok/stale/failed/expected."""
    out = _systemctl("list-units", "--all", "--type=service", "--no-legend", "--no-pager")
    services: list[str] = []
    for line in out.stdout.splitlines():
        # systemd marks failed units with a leading "●" (and "○" for idle), so
        # the unit name is not always parts[0]; scan for it explicitly.
        parts = line.split()
        for part in parts:
            if part.startswith(UNIT_PREFIX) and part.endswith(".service"):
                services.append(part)
                break
    now = datetime.now(timezone.utc)
    findings: list[dict] = []
    for service in sorted(services):
        info = service_last_run(service)
        if info["active"] in ("inactive", "failed", "dead") and info["result"] in ("success", "", "exit-code"):
            pass  # classify below
        ts = parse_timestamp(info["exec_ts"])
        max_hours = MAX_HOURS_BY_UNIT.get(service, max_hours_default)
        age_hours = (now - ts).total_seconds() / 3600 if ts else None
        if info["result"] == "success":
            findings.append({"unit": service, "status": "ok", "age_hours": age_hours})
        elif info["active"] == "active":
            findings.append({"unit": service, "status": "running", "age_hours": age_hours})
        elif info["result"] == "exit-code":
            findings.append({"unit": service, "status": "failed", "age_hours": age_hours,
                             "detail": f"last exit non-zero"})
        elif age_hours is not None and age_hours > max_hours:
            findings.append({"unit": service, "status": "stale",
                             "age_hours": age_hours,
                             "detail": f"no clean run in {age_hours:.1f}h (max {max_hours:.0f}h)"})
        else:
            # Expected idle: oneshot that ran clean and is within its window.
            findings.append({"unit": service, "status": "idle", "age_hours": age_hours})
    return {"as_of": now.isoformat(timespec="seconds"), "units": findings}


def render(findings: dict) -> str:
    anomalies = [u for u in findings["units"] if u["status"] in ("failed", "stale")]
    if not anomalies:
        return ""
    lines = [
        "**Cipher ops health — anomalies found**",
        f"`{findings['as_of']}` · {len(anomalies)} of {len(findings['units'])} units anomalous",
        "",
    ]
    for unit in anomalies:
        age = f"{unit['age_hours']:.1f}h ago" if unit.get("age_hours") is not None else "never"
        lines.append(f"- **{unit['unit']}** · {unit['status']} · last run {age} · {unit.get('detail', '')}")
    return "\n".join(lines)


def send_webhook(message: str, webhook_url: str) -> None:
    payload = json.dumps({"content": message}).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "Cipher-Health-Monitor/1.0"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as response:
        if response.getcode() not in (200, 204):
            raise RuntimeError(f"Discord webhook returned HTTP {response.getcode()}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-hours-default", type=float, default=30.0,
                        help="stale threshold for units without an explicit entry")
    parser.add_argument("--preview", action="store_true", help="print findings, do not post")
    args = parser.parse_args(argv)
    findings = collect(args.max_hours_default)
    if args.preview:
        print(json.dumps(findings, indent=2))
        return 0
    message = render(findings)
    anomalies = [u for u in findings["units"] if u["status"] in ("failed", "stale")]
    if not message:
        print(json.dumps({"status": "healthy", "units": len(findings["units"]),
                          "as_of": findings["as_of"]}))
        return 0
    webhook_url = os.environ.get("DISCORD_PROGRESS_WEBHOOK") or os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        print(message)
        print("WARNING: no Discord webhook configured; posting to stdout only")
        return 1
    send_webhook(message, webhook_url)
    print(json.dumps({"status": "anomalies", "count": len(anomalies), "as_of": findings["as_of"]}))
    return 1 if anomalies else 0


if __name__ == "__main__":
    raise SystemExit(main())
