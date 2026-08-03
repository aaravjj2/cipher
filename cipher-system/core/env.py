"""Load the repository's single local environment file without overriding the shell."""

from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"


def load_local_env(path: str | Path = ENV_PATH) -> dict[str, str]:
    """Load simple KEY=VALUE lines and set only missing process variables."""

    values: dict[str, str] = {}
    candidate = Path(path)
    if not candidate.exists():
        return values
    for raw_line in candidate.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
            os.environ.setdefault(key, value)
    return values


load_local_env()
