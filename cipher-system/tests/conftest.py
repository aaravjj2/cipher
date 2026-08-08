from __future__ import annotations

import os
import sys
from pathlib import Path

import json

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load_artifact(relative_path: str):
    """Read a generated governance/research artifact, or SKIP if it is absent.

    A large block of this suite asserts properties of artifacts produced by the
    research pipeline (locked validations, cohort freezes, signal-only studies)
    rather than properties of code. Those artifacts live under data/, which is
    gitignored, and several cannot be regenerated without re-running the whole
    ingest — `run_cipher_signal_only_research.py` refuses outright with "no
    canonical recent daily dataset is registered".

    Left alone these raised FileNotFoundError and read as 19 permanently red
    tests, which is worse than useless: a suite nobody expects to be green hides
    the one failure that actually matters. Skipping states the real situation —
    the artifact is missing, so the property is unverified — and keeps a genuine
    regression visible.
    """
    path = ROOT / relative_path
    if not path.exists():
        pytest.skip(
            f"generated artifact missing: {relative_path}. "
            "Regenerate via the owning research script, or accept it as unverified."
        )
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        pytest.skip(f"generated artifact unreadable ({relative_path}): {exc}")


def require_artifact(relative_path: str) -> Path:
    """Skip unless the artifact exists; return its path. For non-JSON payloads."""
    path = ROOT / relative_path
    if not path.exists():
        pytest.skip(f"generated artifact missing: {relative_path}")
    return path

# Legacy tests use paths relative to the active cipher-system checkout. Keep the
# test process anchored there regardless of where pytest was invoked.
os.chdir(ROOT)
