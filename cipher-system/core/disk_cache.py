"""Disk-backed spillover for the in-process option-chain cache.

The chain fetch is the expensive call in this service (a multi-page Alpaca snapshot
request), and it was previously memory-only: every restart re-paid it for every
ticker, and a 580-ticker scan started completely cold. Persisting it means a restart
mid-session — or a second scan shortly after the first — reuses what was already
fetched.

Deliberately narrow in scope:
  * Only the option chain is persisted. Quotes move constantly and matrices are cheap
    to rebuild once the chain is in hand.
  * Entries carry their own expiry and are ignored once stale, so a crash can never
    resurrect old market data.
  * Every failure path is swallowed. A cache is an optimisation; it must never be the
    reason a request fails.

Files live under data/http_cache/ (gitignored, like the rest of data/).
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "data" / "http_cache"
# Chain contents are only meaningful for a short window; this bounds how stale a
# post-restart hit can be. Kept in step with option_chain's in-memory TTL.
DEFAULT_TTL_SECONDS = 60
# Keep the directory from growing without bound across long sessions.
MAX_FILES = 4000


def _key_to_path(key: str) -> Path:
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]
    return CACHE_DIR / f"{digest}.json"


def get(key: str, ttl: int = DEFAULT_TTL_SECONDS):
    """Return the cached payload, or None when absent/stale/unreadable."""
    path = _key_to_path(key)
    try:
        if not path.is_file():
            return None
        if time.time() - path.stat().st_mtime > ttl:
            return None
        with path.open("r", encoding="utf-8") as handle:
            record = json.load(handle)
        if record.get("key") != key:  # digest collision guard
            return None
        return record.get("payload")
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def put(key: str, payload) -> None:
    """Persist a payload. Written atomically so a reader never sees a partial file."""
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = _key_to_path(key)
        fd, tmp = tempfile.mkstemp(dir=str(CACHE_DIR), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"key": key, "payload": payload}, handle, default=str)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        _prune()
    except (OSError, TypeError, ValueError):
        pass


def _prune() -> None:
    try:
        files = list(CACHE_DIR.glob("*.json"))
        if len(files) <= MAX_FILES:
            return
        files.sort(key=lambda p: p.stat().st_mtime)
        for stale in files[: len(files) - MAX_FILES]:
            stale.unlink(missing_ok=True)
    except OSError:
        pass


def clear() -> int:
    removed = 0
    try:
        for path in CACHE_DIR.glob("*.json"):
            path.unlink(missing_ok=True)
            removed += 1
    except OSError:
        pass
    return removed
