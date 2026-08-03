#!/usr/bin/env python3
"""Manage the unified local Cipher product and its read-only support daemons."""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = ROOT.parent
DATA = ROOT / "data"
LOGS = ROOT / "logs" / "unified"
STATE = DATA / "governance" / "unified_runtime"
PID_FILES = {
    "core": STATE / "core.pid",
    "web": STATE / "web.pid",
    "safe_scheduler": STATE / "safe_scheduler.pid",
    "build_healer": STATE / "build_healer.pid",
}
COMMAND_MARKERS = {
    "core": str(ROOT / "core" / "app.py"),
    "web": str(ROOT / "app" / "server.mjs"),
    "safe_scheduler": str(ROOT / "scripts" / "run_safe_scheduled_jobs.py"),
    "build_healer": str(ROOT / "scripts" / "run_build_healing_loop.py"),
}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_env(path: Path = ROOT / ".env") -> dict[str, str]:
    values = dict(os.environ)
    if path.is_file():
        for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    values["CIPHER_UNIFIED_PRODUCT"] = "1"
    values["CIPHER_EXECUTION_AUTHORITY"] = "0"
    return values


def read_cmdline(pid: int) -> str:
    try:
        return (Path(f"/proc/{pid}") / "cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", errors="ignore")
    except OSError:
        return ""


def pid_for(component: str) -> int | None:
    path = PID_FILES[component]
    if not path.is_file():
        return None
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    command = read_cmdline(pid)
    if command and COMMAND_MARKERS[component] in command:
        return pid
    return None


def resolve_core_python() -> str:
    candidates = [
        os.environ.get("CIPHER_CORE_PYTHON"),
        str(REPOSITORY_ROOT / ".venv-research-py312" / "bin" / "python"),
        "/home/aarav/.venvs/cipher/bin/python",
        sys.executable,
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate).absolute())
    return sys.executable


def resolve_research_python() -> str:
    candidates = [
        os.environ.get("CIPHER_RESEARCH_PYTHON"),
        str(REPOSITORY_ROOT / ".venv-research-py312" / "bin" / "python"),
        sys.executable,
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate).absolute())
    return sys.executable


def resolve_node() -> str:
    for candidate in (os.environ.get("NODE"), "/usr/bin/node", "node"):
        if candidate and (candidate == "node" or Path(candidate).is_file()):
            return candidate
    return "node"


def http_json(url: str, timeout: float = 5.0) -> dict:
    try:
        with urlopen(url, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return {"ok": 200 <= response.status < 300, "status": response.status, "payload": payload}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def wait_http(url: str, *, timeout_seconds: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if http_json(url, timeout=1.0).get("ok"):
            return True
        time.sleep(0.25)
    return False


def start_component(component: str, command: list[str], env: dict[str, str]) -> dict:
    existing = pid_for(component)
    if existing is not None:
        return {"component": component, "state": "already_running", "pid": existing}
    STATE.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    log_path = LOGS / f"{component}.log"
    log_handle = log_path.open("a", encoding="utf-8")
    process = subprocess.Popen(
        command,
        cwd=REPOSITORY_ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        close_fds=True,
    )
    PID_FILES[component].write_text(f"{process.pid}\n", encoding="utf-8")
    time.sleep(0.5)
    if process.poll() is not None:
        PID_FILES[component].unlink(missing_ok=True)
        return {
            "component": component,
            "state": "failed",
            "returncode": process.returncode,
            "log": str(log_path),
        }
    return {"component": component, "state": "running", "pid": process.pid, "log": str(log_path)}


def stop_component(component: str) -> dict:
    pid = pid_for(component)
    if pid is None:
        PID_FILES[component].unlink(missing_ok=True)
        return {"component": component, "state": "not_running"}
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        PID_FILES[component].unlink(missing_ok=True)
        return {"component": component, "state": "stopped"}
    for _ in range(100):
        if not Path(f"/proc/{pid}").exists():
            PID_FILES[component].unlink(missing_ok=True)
            return {"component": component, "state": "stopped"}
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    PID_FILES[component].unlink(missing_ok=True)
    return {"component": component, "state": "killed_after_timeout"}


def start_all(core_port: int, web_port: int, *, with_ops: bool, run_healer_on_start: bool) -> dict:
    env = load_env()
    env["CIPHER_CORE_PORT"] = str(core_port)
    env["PORT"] = str(web_port)
    env["CIPHER_CORE_URL"] = f"http://127.0.0.1:{core_port}"
    results: list[dict] = []
    results.append(
        start_component(
            "core",
            [resolve_core_python(), "-u", str(ROOT / "core" / "app.py")],
            env,
        )
    )
    if not wait_http(f"http://127.0.0.1:{core_port}/health"):
        results.append({"component": "core_health", "state": "failed"})
        return {"state": "failed", "components": results, "execution_authority": False}
    results.append(
        start_component(
            "web",
            [resolve_node(), str(ROOT / "app" / "server.mjs")],
            env,
        )
    )
    if not wait_http(f"http://127.0.0.1:{web_port}/api/health"):
        results.append({"component": "web_health", "state": "failed"})
        return {"state": "failed", "components": results, "execution_authority": False}
    if with_ops:
        research_python = resolve_research_python()
        results.append(
            start_component(
                "safe_scheduler",
                [
                    research_python,
                    str(ROOT / "scripts" / "run_safe_scheduled_jobs.py"),
                    "--loop",
                    "--interval-seconds",
                    "3600",
                ],
                env,
            )
        )
        healer_command = [
            research_python,
            str(ROOT / "scripts" / "run_build_healing_loop.py"),
            "--loop",
            "--interval-seconds",
            "60",
        ]
        if run_healer_on_start:
            healer_command.append("--run-on-start")
        results.append(start_component("build_healer", healer_command, env))
    return {
        "state": "running",
        "core_port": core_port,
        "web_port": web_port,
        "components": results,
        "health": http_json(f"http://127.0.0.1:{core_port}/health"),
        "research_status": http_json(f"http://127.0.0.1:{core_port}/api/research-status"),
        "execution_authority": False,
    }


def stop_all() -> dict:
    results = [
        stop_component("build_healer"),
        stop_component("safe_scheduler"),
        stop_component("web"),
        stop_component("core"),
    ]
    return {"state": "stopped", "components": results, "execution_authority": False}


def status(core_port: int, web_port: int) -> dict:
    components = {
        name: {"pid": pid_for(name), "running": pid_for(name) is not None}
        for name in PID_FILES
    }
    return {
        "state": "running" if components["core"]["running"] and components["web"]["running"] else "partial_or_stopped",
        "updated_at": utcnow(),
        "canonical_root": str(ROOT),
        "data_root": str(DATA.resolve()) if DATA.exists() else str(DATA),
        "log_root": str((ROOT / "logs").resolve()) if (ROOT / "logs").exists() else str(ROOT / "logs"),
        "components": components,
        "health": http_json(f"http://127.0.0.1:{core_port}/health"),
        "web_health": http_json(f"http://127.0.0.1:{web_port}/api/health"),
        "research_status": http_json(f"http://127.0.0.1:{core_port}/api/research-status"),
        "execution_authority": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("start", "stop", "restart", "status"))
    parser.add_argument("--core-port", type=int, default=8282)
    parser.add_argument("--web-port", type=int, default=8283)
    parser.add_argument("--without-ops", action="store_true")
    parser.add_argument("--run-healer-on-start", action="store_true")
    args = parser.parse_args()
    if args.action == "start":
        payload = start_all(
            args.core_port,
            args.web_port,
            with_ops=not args.without_ops,
            run_healer_on_start=args.run_healer_on_start,
        )
    elif args.action == "stop":
        payload = stop_all()
    elif args.action == "restart":
        stop_all()
        payload = start_all(
            args.core_port,
            args.web_port,
            with_ops=not args.without_ops,
            run_healer_on_start=args.run_healer_on_start,
        )
    else:
        payload = status(args.core_port, args.web_port)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 1 if payload.get("state") == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
