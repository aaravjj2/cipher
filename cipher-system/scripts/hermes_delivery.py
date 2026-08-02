#!/usr/bin/env python3
"""Reliable Hermes CLI delivery helpers.

Hermes can print a successful delivery acknowledgement and leave its wrapper
process alive. Treating that as a timeout failure causes false service alarms.
This helper watches stdout, recognizes an explicit delivery acknowledgement,
and terminates only the lingering local wrapper after success is proven.
"""
from __future__ import annotations

import os
import selectors
import shutil
import subprocess
import time
from pathlib import Path
from typing import Iterable

DEFAULT_SUCCESS_MARKERS = (
    "sent to telegram",
    "message sent",
    "delivery successful",
)


def hermes_binary() -> str:
    return (
        os.environ.get("HERMES_BIN")
        or shutil.which("hermes")
        or str(Path.home() / ".local" / "bin" / "hermes")
    )


def _contains_success(output: str, markers: Iterable[str]) -> bool:
    lowered = output.lower()
    return any(marker.lower() in lowered for marker in markers)


def send_hermes_message(
    message: str,
    *,
    target: str,
    timeout_seconds: float | None = None,
    success_markers: Iterable[str] = DEFAULT_SUCCESS_MARKERS,
) -> int:
    """Send through Hermes and return a process-style status code.

    A zero return code means either the CLI exited successfully or Hermes
    emitted an explicit delivery acknowledgement. If the acknowledgement is
    seen while the wrapper remains open, the wrapper is terminated locally;
    the already-delivered message is not retried.
    """

    timeout = float(
        timeout_seconds
        if timeout_seconds is not None
        else os.environ.get("HERMES_SEND_TIMEOUT_SECONDS", "90")
    )
    command = [hermes_binary(), "send", "--to", target, message]
    process = subprocess.Popen(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
    )
    if process.stdout is None:
        process.terminate()
        return 1

    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    deadline = time.monotonic() + max(1.0, timeout)
    output_parts: list[str] = []
    success = False

    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            events = selector.select(timeout=min(0.5, remaining))
            for key, _ in events:
                line = key.fileobj.readline()
                if line:
                    output_parts.append(line)
                    print(line.rstrip(), flush=True)
                    if _contains_success("".join(output_parts), success_markers):
                        success = True
                        break
            if success:
                break
            return_code = process.poll()
            if return_code is not None:
                remainder = process.stdout.read()
                if remainder:
                    output_parts.append(remainder)
                    print(remainder.rstrip(), flush=True)
                return 0 if return_code == 0 or _contains_success("".join(output_parts), success_markers) else return_code

        if success:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            return 0

        process.terminate()
        try:
            remainder, _ = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            remainder, _ = process.communicate(timeout=5)
        if remainder:
            output_parts.append(remainder)
            print(remainder.rstrip(), flush=True)
        return 0 if _contains_success("".join(output_parts), success_markers) else 124
    finally:
        selector.close()
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
