"""Small Supabase PostgREST adapter for request-scoped RLS access."""
from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class SupabaseRepositoryError(RuntimeError):
    """A safe, non-secret storage/provider error."""


_TABLE_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


class SupabaseRestClient:
    def __init__(
        self,
        base_url: str,
        *,
        user_jwt: str,
        anon_key: str,
        timeout_seconds: float = 5.0,
        urlopen_fn: Callable = urlopen,
    ) -> None:
        self._base_url = str(base_url or "").rstrip("/")
        self._user_jwt = str(user_jwt or "")
        self._anon_key = str(anon_key or "")
        self._timeout_seconds = float(timeout_seconds)
        self._urlopen = urlopen_fn
        if not self._base_url or not self._user_jwt or not self._anon_key:
            raise ValueError("Supabase URL, user JWT, and anon key are required")
        if self._timeout_seconds <= 0:
            raise ValueError("Supabase timeout must be positive")

    def __repr__(self) -> str:
        return f"SupabaseRestClient(base_url={self._base_url!r})"

    def request(
        self,
        method: str,
        table: str,
        *,
        query: Mapping[str, str] | None = None,
        payload: object | None = None,
        prefer: str | None = None,
    ):
        name = str(table or "")
        if not _TABLE_RE.fullmatch(name):
            raise ValueError("invalid Supabase table name")
        params = urlencode({str(key): str(value) for key, value in (query or {}).items()})
        url = f"{self._base_url}/rest/v1/{name}"
        if params:
            url += f"?{params}"
        body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._user_jwt}",
            "apikey": self._anon_key,
            "Prefer": prefer or "return=representation",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = Request(url, method=str(method or "GET").upper(), headers=headers, data=body)
        try:
            with self._urlopen(request, timeout=self._timeout_seconds) as response:
                status_value = getattr(response, "status", None)
                status = int(status_value if status_value is not None else response.getcode())
                raw = response.read()
        except HTTPError as exc:
            raise SupabaseRepositoryError(f"Supabase request failed (HTTP {exc.code}).") from None
        except (URLError, TimeoutError, OSError) as exc:
            raise SupabaseRepositoryError("Supabase storage is unavailable.") from None
        if status >= 400:
            raise SupabaseRepositoryError(f"Supabase request failed (HTTP {status}).")
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise SupabaseRepositoryError("Supabase returned an invalid response.") from None
