"""Small, bounded, GET-only client for the local Cipher research API."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


DEFAULT_BASE_URL = "http://127.0.0.1:8282"
DEFAULT_TIMEOUT_SECONDS = 45.0
MAX_RESPONSE_BYTES = 2_000_000

# A path must be present here before an agent can reach it. All are read-only
# GET surfaces in core/app.py. This list intentionally excludes mutable routes.
ALLOWED_PATHS = frozenset(
    {
        "/api/bars",
        "/api/evidence-status",
        "/api/flow",
        "/api/gex-replay",
        "/api/governance",
        "/api/health",
        "/api/night-vision",
        "/api/quote",
        "/api/research-status",
        "/api/standing",
        "/api/strategies",
    }
)


class CipherCoreError(RuntimeError):
    """A bounded, user-safe failure from the Cipher core transport."""


def _allowed_hosts() -> set[str]:
    configured = os.environ.get("CIPHER_AGENT_ALLOWED_CORE_HOSTS", "")
    return {
        "127.0.0.1",
        "localhost",
        "::1",
        *(part.strip().lower() for part in configured.split(",") if part.strip()),
    }


def validate_base_url(value: str) -> str:
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Cipher core URL must use http or https")
    if not parsed.hostname or parsed.hostname.lower() not in _allowed_hosts():
        raise ValueError(
            "Cipher core host is not approved; add the exact hostname to "
            "CIPHER_AGENT_ALLOWED_CORE_HOSTS after reviewing the network boundary"
        )
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Cipher core URL may not contain credentials, query text, or a fragment")
    return value.rstrip("/")


class CipherCoreClient:
    """Issue bounded GET requests to an explicitly approved Cipher core."""

    def __init__(self, base_url: str | None = None, timeout: float | None = None):
        self.base_url = validate_base_url(
            base_url or os.environ.get("CIPHER_CORE_URL", DEFAULT_BASE_URL)
        )
        self.timeout = float(timeout or os.environ.get("CIPHER_AGENT_TIMEOUT", DEFAULT_TIMEOUT_SECONDS))

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if path not in ALLOWED_PATHS:
            raise ValueError(f"core path is not in the read-only allowlist: {path}")
        query = {
            str(key): value
            for key, value in (params or {}).items()
            if value not in (None, "")
        }
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        request = urllib.request.Request(
            url,
            method="GET",
            headers={"Accept": "application/json", "User-Agent": "cipher-google-agentic/0.1"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            detail = exc.read(600).decode("utf-8", "replace")
            raise CipherCoreError(f"Cipher core returned HTTP {exc.code} for {path}: {detail}") from None
        except urllib.error.URLError as exc:
            raise CipherCoreError(f"Cipher core is unavailable for {path}: {exc.reason}") from None
        if len(raw) > MAX_RESPONSE_BYTES:
            raise CipherCoreError(
                f"Cipher core response for {path} exceeded the {MAX_RESPONSE_BYTES}-byte safety limit"
            )
        try:
            payload = json.loads(raw)
        except ValueError as exc:
            raise CipherCoreError(f"Cipher core returned invalid JSON for {path}") from exc
        if not isinstance(payload, dict):
            raise CipherCoreError(f"Cipher core returned a non-object payload for {path}")
        return payload
