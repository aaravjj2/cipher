#!/usr/bin/env python3
"""Produce one non-execution readiness report for Cipher research infrastructure."""
from __future__ import annotations

import argparse
import importlib
import json
import os
import shutil
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = ROOT.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import kronos_research  # noqa: E402
from core.research_platform.local_capabilities import build_local_capability_report  # noqa: E402
from scripts.prefetch_research_models import MODEL_SPECS, build_manifest  # noqa: E402

REQUIRED_MODULES: tuple[tuple[str, str], ...] = (
    ("transformers", "transformers"),
    ("vectorbt", "vectorbt"),
    ("qlib", "qlib"),
    ("riskfolio", "riskfolio"),
    ("rdagent", "rdagent"),
    ("torch", "torch"),
    ("timesfm", "timesfm"),
    ("duckdb", "duckdb"),
    ("huggingface_hub", "huggingface_hub"),
    ("safetensors", "safetensors"),
    ("einops", "einops"),
    ("pandas", "pandas"),
    ("pyarrow", "pyarrow"),
    ("yfinance", "yfinance"),
    ("hurst", "hurst"),
    ("lean_cli", "lean"),
)


def run_command(command: list[str], *, timeout: int = 15) -> dict[str, Any]:
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"available": False, "error": f"{type(exc).__name__}: {exc}"}
    return {
        "available": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": result.stdout.strip()[:2000],
        "stderr": result.stderr.strip()[:2000],
    }


def package_status() -> dict[str, Any]:
    records: dict[str, Any] = {}
    for label, module_name in REQUIRED_MODULES:
        try:
            module = importlib.import_module(module_name)
            records[label] = {
                "available": True,
                "version": getattr(module, "__version__", "unknown"),
            }
        except Exception as exc:
            records[label] = {
                "available": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
    return {
        "python": sys.version,
        "python_312_or_newer": sys.version_info >= (3, 12),
        "modules": records,
        "all_available": all(item["available"] for item in records.values()),
    }


def rootless_prerequisite_status() -> dict[str, Any]:
    binaries = {
        name: shutil.which(name)
        for name in ("newuidmap", "newgidmap", "slirp4netns", "fuse-overlayfs", "rootlesskit")
    }
    subuid = Path("/etc/subuid")
    subgid = Path("/etc/subgid")
    user = os.environ.get("USER", "")

    def mapping(path: Path) -> str | None:
        if not path.is_file() or not user:
            return None
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith(f"{user}:"):
                return line
        return None

    userns_path = Path("/proc/sys/kernel/unprivileged_userns_clone")
    userns_value = userns_path.read_text().strip() if userns_path.is_file() else None
    blockers: list[str] = []
    for required in ("newuidmap", "newgidmap"):
        if not binaries[required]:
            blockers.append(f"missing_setuid_prerequisite:{required}")
    if mapping(subuid) is None:
        blockers.append("missing_subuid_mapping")
    if mapping(subgid) is None:
        blockers.append("missing_subgid_mapping")
    if userns_value not in (None, "1"):
        blockers.append("unprivileged_user_namespaces_disabled")
    return {
        "binaries": binaries,
        "subuid": mapping(subuid),
        "subgid": mapping(subgid),
        "unprivileged_userns_clone": userns_value,
        "eligible_for_rootless_install": not blockers,
        "blockers": blockers,
    }


def docker_status() -> dict[str, Any]:
    docker = shutil.which("docker")
    sockets = [str(path) for path in (Path("/run/user") / str(os.getuid()), Path("/var/run")) for path in [path / "docker.sock"] if path.exists()]
    info = run_command([docker, "info", "--format", "{{json .ServerVersion}}"], timeout=10) if docker else {"available": False, "error": "docker_cli_missing"}
    return {
        "cli": docker,
        "sockets": sockets,
        "daemon": info,
        "rootless_prerequisites": rootless_prerequisite_status(),
        "local_container_execution_ready": bool(docker and info.get("available")),
    }


def lean_status(docker: dict[str, Any]) -> dict[str, Any]:
    executable = shutil.which("lean")
    version = run_command([executable, "--version"]) if executable else {"available": False, "error": "lean_cli_missing"}
    return {
        "cli": executable,
        "version": version,
        "local_engine_ready": bool(version.get("available") and docker["local_container_execution_ready"]),
        "blocker": None if version.get("available") and docker["local_container_execution_ready"] else "docker_daemon_unavailable",
        "cloud_or_account_actions_attempted": False,
    }


def github_status() -> dict[str, Any]:
    remote = run_command(["git", "remote", "get-url", "origin"])
    helper = run_command(["git", "config", "--get-all", "credential.helper"])
    gh = shutil.which("gh")
    auth_variables = sorted(
        name
        for name in os.environ
        if name in {"GITHUB_TOKEN", "GH_TOKEN", "GITHUB_ENTERPRISE_TOKEN", "GH_ENTERPRISE_TOKEN"}
    )
    gh_status = run_command([gh, "auth", "status"], timeout=10) if gh else {"available": False, "error": "gh_cli_missing"}
    authenticated = bool(auth_variables or helper.get("stdout") or gh_status.get("available"))
    return {
        "remote": remote.get("stdout"),
        "credential_helper_configured": bool(helper.get("stdout")),
        "auth_environment_variable_names": auth_variables,
        "gh_cli": gh,
        "gh_auth": gh_status,
        "push_ready": authenticated,
        "secrets_exposed": False,
    }


def network_status() -> dict[str, Any]:
    checks = {}
    for host in ("github.com", "huggingface.co"):
        try:
            address = socket.gethostbyname(host)
            checks[host] = {"resolves": True, "address": address}
        except OSError as exc:
            checks[host] = {"resolves": False, "error": str(exc)}
    return checks


def build_report(*, run_smoke: bool, offline: bool, cache_dir: Path) -> dict[str, Any]:
    packages = package_status()
    docker = docker_status()
    models = build_manifest(cache_dir=cache_dir, offline=offline, run_smoke=run_smoke)
    capabilities = build_local_capability_report(REPOSITORY_ROOT)
    blockers: list[str] = []
    if not packages["all_available"]:
        blockers.append("optional_python_runtime_incomplete")
    if not all(item.get("cached") for item in models["models"]):
        blockers.append("model_cache_incomplete")
    if run_smoke and not all(models["synthetic_smoke"].get(name, {}).get("passed") for name in ("timesfm", "kronos")):
        blockers.append("synthetic_model_smoke_failed")
    if not docker["local_container_execution_ready"]:
        blockers.append("docker_daemon_unavailable_for_local_lean")
    github = github_status()
    if not github["push_ready"]:
        blockers.append("github_push_authentication_unavailable")
    if capabilities["external_integrations"]["boundary_violations"]:
        blockers.append("external_integration_boundary_violation")
    return {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "packages": packages,
        "models": models,
        "kronos_runtime": kronos_research.status(),
        "docker": docker,
        "lean": lean_status(docker),
        "github": github,
        "network": network_status(),
        "external_integrations": {
            "available_count": capabilities["external_integrations"]["available_count"],
            "total_count": capabilities["external_integrations"]["total_count"],
            "boundary_violations": capabilities["external_integrations"]["boundary_violations"],
        },
        "research_boundaries": {
            "execution_authority": False,
            "market_data_used_by_smoke": False,
            "outcomes_evaluated": False,
            "research_verdicts_changed": False,
            "maximum_promotion_state": capabilities["execution_boundary"]["maximum_promotion_state"],
        },
        "blockers": blockers,
        "runtime_complete": not any(item in blockers for item in ("optional_python_runtime_incomplete", "model_cache_incomplete", "synthetic_model_smoke_failed", "external_integration_boundary_violation")),
        "host_and_delivery_complete": not blockers,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path.home() / ".cache" / "huggingface" / "hub",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--strict-host", action="store_true")
    args = parser.parse_args()
    payload = build_report(
        run_smoke=bool(args.smoke),
        offline=bool(args.offline),
        cache_dir=args.cache_dir.expanduser().resolve(),
    )
    output = args.output or (
        ROOT
        / "data"
        / "governance"
        / f"research_infrastructure_audit_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(output)
    if not payload["runtime_complete"]:
        return 1
    if args.strict_host and not payload["host_and_delivery_complete"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
