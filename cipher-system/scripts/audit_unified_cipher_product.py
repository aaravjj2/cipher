#!/usr/bin/env python3
"""Audit Cipher's unified local product topology and runtime health."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
LEGACY_ALIAS = Path("/home/aarav/Aarav/cipher/cipher-system")
RUNTIME = Path("/home/aarav/Aarav/cipher/runtime")
GOVERNANCE = ROOT / "data" / "governance"
OUTPUT = GOVERNANCE / "unified_cipher_product_audit.json"
SYSTEMD_SERVICES = (
    "cipher-core.service",
    "cipher-web.service",
    "cipher-gex.service",
    "cipher-tradier.service",
)
REQUIRED_TIMERS = (
    "cipher-local-backup.timer",
    "cipher-fronttest-portfolios.timer",
    "cipher-prospective-fronttests.timer",
    "cipher-market-research.timer",
    "cipher-option-history.timer",
    "cipher-portfolio-discord-daily.timer",
    "cipher-operational-metrics.timer",
    "cipher-event-context.timer",
    "cipher-data-health-alert.timer",
)


def command(command: list[str], *, timeout: int = 30) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return {
            "returncode": completed.returncode,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
        }
    except Exception as exc:
        return {"returncode": 127, "stdout": "", "stderr": f"{type(exc).__name__}: {exc}"}


def pid_cmdline(pid: int) -> str:
    try:
        return (Path(f"/proc/{pid}") / "cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", errors="ignore").strip()
    except OSError:
        return ""


def pid_cwd(pid: int) -> str | None:
    try:
        return str((Path(f"/proc/{pid}") / "cwd").resolve())
    except OSError:
        return None


def systemd_service(name: str) -> dict[str, Any]:
    result = command(
        [
            "systemctl",
            "show",
            name,
            "-p",
            "MainPID",
            "-p",
            "ActiveState",
            "-p",
            "SubState",
            "-p",
            "FragmentPath",
        ]
    )
    values: dict[str, str] = {}
    for line in result["stdout"].splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    pid = int(values.get("MainPID") or 0)
    return {
        "name": name,
        "active_state": values.get("ActiveState"),
        "sub_state": values.get("SubState"),
        "main_pid": pid,
        "command": pid_cmdline(pid) if pid else "",
        "cwd": pid_cwd(pid) if pid else None,
        "unit_path": values.get("FragmentPath"),
        "query_returncode": result["returncode"],
    }


def systemd_timer(name: str) -> dict[str, Any]:
    result = command([
        "systemctl", "show", name, "-p", "ActiveState", "-p", "UnitFileState",
        "-p", "NextElapseUSecRealtime", "-p", "LastTriggerUSec",
    ])
    values: dict[str, str] = {}
    for line in result["stdout"].splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return {
        "name": name,
        "active_state": values.get("ActiveState"),
        "unit_file_state": values.get("UnitFileState"),
        "next": values.get("NextElapseUSecRealtime") or None,
        "last_trigger": values.get("LastTriggerUSec") or None,
        "query_returncode": result["returncode"],
    }


def http_json(url: str) -> dict[str, Any]:
    try:
        with urlopen(url, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return {"ok": 200 <= response.status < 300, "status": response.status, "payload": payload}
    except HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except (ValueError, OSError):
            payload = None
        return {"ok": False, "status": exc.code, "payload": payload,
                "error": f"HTTPError: {exc.code}"}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def managed_daemon(pid_path: Path, marker: str) -> dict[str, Any]:
    if not pid_path.is_file():
        return {"running": False, "pid": None, "reason": "pid_file_missing"}
    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return {"running": False, "pid": None, "reason": "invalid_pid_file"}
    cmdline = pid_cmdline(pid)
    return {
        "running": bool(cmdline and marker in cmdline),
        "pid": pid,
        "command": cmdline,
        "cwd": pid_cwd(pid),
    }


def registry_audit(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"exists": False, "path": str(path)}
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=60) as db:
        tables = [
            str(row[0])
            for row in db.execute(
                "select name from sqlite_master where type='table' and name not like 'sqlite_%' order by name"
            )
        ]
        counts = {table: int(db.execute(f'select count(*) from "{table}"').fetchone()[0]) for table in tables}
        integrity = str(db.execute("pragma integrity_check").fetchone()[0])
        pytest_raw = int(db.execute("select count(*) from raw_objects where uri like '%/tmp/pytest-of-%'").fetchone()[0])
        pytest_audit = int(
            db.execute("select count(*) from audit_events where payload_json like '%/tmp/pytest-of-%'").fetchone()[0]
        )
    return {
        "exists": True,
        "path": str(path),
        "integrity": integrity,
        "counts": counts,
        "active_pytest_raw_records": pytest_raw,
        "active_pytest_audit_records": pytest_audit,
    }


def git_status() -> dict[str, Any]:
    commit = command(["git", "rev-parse", "HEAD"])
    status = command(["git", "status", "--short"])
    return {
        "commit": commit["stdout"] if commit["returncode"] == 0 else None,
        "working_tree_clean": status["returncode"] == 0 and not status["stdout"],
        "status": status["stdout"],
    }


def path_state(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "exists": path.exists(),
        "is_symlink": path.is_symlink(),
        "resolved": str(path.resolve()) if path.exists() else None,
    }


def atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def build_audit() -> dict[str, Any]:
    canonical = ROOT.resolve()
    services = {name: systemd_service(name) for name in SYSTEMD_SERVICES}
    timers = {name: systemd_timer(name) for name in REQUIRED_TIMERS}
    health = http_json("http://127.0.0.1:8282/health")
    research_status = http_json("http://127.0.0.1:8282/api/research-status")
    web_health = http_json("http://127.0.0.1:8283/api/health")
    web_research_status = http_json("http://127.0.0.1:8283/api/research-status")
    operator = http_json("http://127.0.0.1:8282/api/operator-status")
    registry = registry_audit(ROOT / "data" / "governance" / "research_registry.sqlite")
    scheduler = managed_daemon(
        ROOT / "data" / "governance" / "safe_scheduler.pid",
        str(ROOT / "scripts" / "run_safe_scheduled_jobs.py"),
    )
    healer = managed_daemon(
        ROOT / "data" / "governance" / "build_healing" / "build_healing_loop.pid",
        str(ROOT / "scripts" / "run_build_healing_loop.py"),
    )
    paths = {
        "canonical_source": path_state(ROOT),
        "legacy_source_alias": path_state(LEGACY_ALIAS),
        "canonical_data": path_state(ROOT / "data"),
        "legacy_data": path_state(LEGACY_ALIAS / "data"),
        "canonical_logs": path_state(ROOT / "logs"),
        "unified_environment": path_state(ROOT / ".env"),
        "runtime_data": path_state(RUNTIME / "data"),
        "runtime_logs": path_state(RUNTIME / "logs"),
        "runtime_config": path_state(RUNTIME / "config" / "cipher.env"),
        "legacy_source_backup": path_state(
            RUNTIME / "backups" / "pre_unification_20260803T231700Z" / "legacy_source_original"
        ),
    }
    checks = {
        "legacy_alias_points_to_canonical": paths["legacy_source_alias"]["resolved"] == str(canonical),
        "canonical_data_points_to_runtime": paths["canonical_data"]["resolved"] == str((RUNTIME / "data").resolve()),
        "legacy_data_points_to_runtime": paths["legacy_data"]["resolved"] == str((RUNTIME / "data").resolve()),
        "canonical_logs_points_to_runtime": paths["canonical_logs"]["resolved"] == str((RUNTIME / "logs").resolve()),
        "environment_points_to_runtime": paths["unified_environment"]["resolved"] == str((RUNTIME / "config" / "cipher.env").resolve()),
        "systemd_services_active": all(item["active_state"] == "active" and item["sub_state"] == "running" for item in services.values()),
        "systemd_services_resolve_to_canonical": all(item["cwd"] == str(canonical) or item["cwd"] == str(canonical / "app") for item in services.values()),
        "core_health": bool(health.get("ok")),
        "web_health": bool(web_health.get("ok")),
        "core_research_status": bool(research_status.get("ok")),
        "web_auth_gate_enforced": web_research_status.get("status") in {401, 403},
        "registry_integrity": registry.get("integrity") == "ok",
        "active_registry_test_contamination_absent": registry.get("active_pytest_raw_records") == 0 and registry.get("active_pytest_audit_records") == 0,
        "required_timers_active": all(
            item["active_state"] == "active" and item["unit_file_state"] == "enabled"
            for item in timers.values()
        ),
        "restore_verified_backup": bool(
            operator.get("ok") and (operator.get("payload") or {}).get("backup", {}).get("status") == "VERIFIED"
        ),
        "live_execution_absent": not bool((health.get("payload") or {}).get("live_execution", False))
        and not bool((research_status.get("payload") or {}).get("live_execution_present", False)),
    }
    complete = all(checks.values())
    return {
        "schema_version": 2,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "verdict": "COMPLETE" if complete else "INCOMPLETE",
        "unified_product_complete": complete,
        "checks": checks,
        "paths": paths,
        "systemd_services": services,
        "required_timers": timers,
        "legacy_pid_daemons": {
            "safe_scheduler": scheduler,
            "build_healer": healer,
            "gate_status": "INFORMATIONAL_RETIRED",
        },
        "core_health": health,
        "web_health": web_health,
        "core_research_status": {
            "ok": research_status.get("ok"),
            "status": research_status.get("status"),
            "initialized": (research_status.get("payload") or {}).get("initialized"),
            "live_execution_present": (research_status.get("payload") or {}).get("live_execution_present"),
        },
        "web_research_status": {
            "ok": web_research_status.get("ok"),
            "status": web_research_status.get("status"),
            "authentication_required": web_research_status.get("status") in {401, 403},
        },
        "operator_status": {
            "ok": operator.get("ok"),
            "backup": (operator.get("payload") or {}).get("backup"),
            "execution_capability": (operator.get("payload") or {}).get("execution_capability"),
        },
        "registry": registry,
        "git": git_status(),
        "execution_authority": False,
        "paper_simulation_enabled": True,
        "live_execution_enabled": False,
    }


def main() -> int:
    payload = build_audit()
    atomic_write(OUTPUT, payload)
    print(json.dumps({
        "path": str(OUTPUT),
        "verdict": payload["verdict"],
        "checks": payload["checks"],
        "execution_authority": False,
    }, indent=2, sort_keys=True))
    return 0 if payload["unified_product_complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
