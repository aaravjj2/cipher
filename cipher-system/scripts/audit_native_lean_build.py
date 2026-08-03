#!/usr/bin/env python3
"""Audit a native LEAN build without launching an algorithm or brokerage path."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LEAN_ROOT = Path(
    os.environ.get("CIPHER_NATIVE_LEAN_ROOT", "/home/aarav/Aarav/Autopilot/external/Lean")
)


def run(command: list[str], *, timeout: int = 120) -> dict[str, Any]:
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def flatten_vulnerabilities(payload: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for project in payload.get("projects", []):
        for framework in project.get("frameworks", []):
            packages = [
                *framework.get("topLevelPackages", []),
                *framework.get("transitivePackages", []),
            ]
            for package in packages:
                for vulnerability in package.get("vulnerabilities", []):
                    rows.append(
                        {
                            "package": str(package.get("id")),
                            "version": str(package.get("resolvedVersion")),
                            "severity": str(vulnerability.get("severity")),
                            "advisory_url": str(vulnerability.get("advisoryurl")),
                        }
                    )
    rows.sort(key=lambda item: (item["package"], item["severity"], item["advisory_url"]))
    return rows


def build_audit(lean_root: Path, *, dotnet: Path | None = None) -> dict[str, Any]:
    lean_root = lean_root.expanduser().resolve()
    dotnet_path = dotnet or Path.home() / ".dotnet" / "dotnet"
    if not dotnet_path.is_file():
        discovered = shutil.which("dotnet")
        dotnet_path = Path(discovered) if discovered else dotnet_path

    launcher_project = lean_root / "Launcher" / "QuantConnect.Lean.Launcher.csproj"
    launcher_dll = lean_root / "Launcher" / "bin" / "Release" / "QuantConnect.Lean.Launcher.dll"

    commit = (
        run(["git", "-C", str(lean_root), "rev-parse", "HEAD"])
        if lean_root.is_dir()
        else {"ok": False, "error": "lean_root_missing"}
    )
    dotnet_version = (
        run([str(dotnet_path), "--version"])
        if dotnet_path.is_file()
        else {"ok": False, "error": "dotnet_missing"}
    )

    vulnerability_result: dict[str, Any]
    if dotnet_path.is_file() and launcher_project.is_file():
        vulnerability_result = run(
            [
                str(dotnet_path),
                "list",
                str(launcher_project),
                "package",
                "--vulnerable",
                "--include-transitive",
                "--format",
                "json",
            ],
            timeout=240,
        )
    else:
        vulnerability_result = {"ok": False, "error": "dotnet_or_launcher_project_missing"}

    vulnerability_payload: dict[str, Any] = {}
    if vulnerability_result.get("ok"):
        try:
            vulnerability_payload = json.loads(vulnerability_result.get("stdout") or "{}")
        except json.JSONDecodeError:
            vulnerability_result = {
                **vulnerability_result,
                "ok": False,
                "error": "invalid_vulnerability_json",
            }

    vulnerabilities = flatten_vulnerabilities(vulnerability_payload)
    severity_counts: dict[str, int] = {}
    for item in vulnerabilities:
        severity_counts[item["severity"]] = severity_counts.get(item["severity"], 0) + 1

    build_ready = bool(
        commit.get("ok")
        and dotnet_version.get("ok")
        and launcher_project.is_file()
        and launcher_dll.is_file()
    )

    return {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "lean_root": str(lean_root),
        "source_commit": commit.get("stdout") if commit.get("ok") else None,
        "dotnet": {
            "path": str(dotnet_path),
            "version": dotnet_version.get("stdout") if dotnet_version.get("ok") else None,
            "available": bool(dotnet_version.get("ok")),
        },
        "launcher": {
            "project": str(launcher_project),
            "dll": str(launcher_dll),
            "exists": launcher_dll.is_file(),
            "bytes": launcher_dll.stat().st_size if launcher_dll.is_file() else None,
            "sha256": sha256(launcher_dll) if launcher_dll.is_file() else None,
        },
        "native_build_ready": build_ready,
        "vulnerability_audit": {
            "completed": bool(vulnerability_result.get("ok")),
            "severity_counts": severity_counts,
            "findings": vulnerabilities,
            "security_clean": bool(vulnerability_result.get("ok") and not vulnerabilities),
            "command_error": vulnerability_result.get("error") or vulnerability_result.get("stderr"),
        },
        "allowed_use": "offline_engine_build_validation_only_pending_frozen_replication_job",
        "strategy_or_backtest_run": False,
        "brokerage_connection_attempted": False,
        "cloud_login_attempted": False,
        "live_execution": False,
        "promotion_eligible": False,
        "execution_authority": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lean-root", type=Path, default=DEFAULT_LEAN_ROOT)
    parser.add_argument("--dotnet", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    payload = build_audit(args.lean_root, dotnet=args.dotnet)
    output = args.output or (
        ROOT
        / "data"
        / "governance"
        / f"native_lean_build_audit_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(output)
    return 0 if payload["native_build_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
