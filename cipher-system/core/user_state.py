"""User-owned state repository primitives backed by Supabase RLS."""
from __future__ import annotations

import os
from collections.abc import Mapping

from core.request_context import current
from core.supabase_rest import SupabaseRestClient


def repository_for_context() -> "UserStateRepository | None":
    context = current()
    if context is None:
        return None
    if not context.access_token:
        raise RuntimeError("authenticated Supabase token is unavailable")
    base_url = os.environ.get("SUPABASE_URL", "")
    anon_key = os.environ.get("SUPABASE_ANON_KEY", "")
    if not base_url or not anon_key:
        raise RuntimeError("Supabase hosted storage is not configured")
    return UserStateRepository(
        SupabaseRestClient(base_url, user_jwt=context.access_token, anon_key=anon_key),
        context.user_id,
    )


class UserStateRepository:
    def __init__(self, client, user_id: str) -> None:
        owner = str(user_id or "").strip()
        if not owner or len(owner) > 128:
            raise ValueError("authenticated user_id is required")
        self._client = client
        self.user_id = owner

    def _owned_payload(self, payload: Mapping) -> dict:
        if not isinstance(payload, Mapping):
            raise ValueError("user state payload must be an object")
        if "user_id" in payload and str(payload["user_id"]) != self.user_id:
            raise ValueError("user_id cannot be overridden")
        result = dict(payload)
        result["user_id"] = self.user_id
        return result

    def list_rows(self, table: str, *, query: Mapping[str, str] | None = None):
        return self._client.request("GET", table, query=query or {})

    def get_row(self, table: str, row_id: str):
        rows = self._client.request("GET", table, query={"id": f"eq.{row_id}"}) or []
        return rows[0] if rows else None

    def insert_row(self, table: str, payload: Mapping):
        return self._client.request("POST", table, payload=self._owned_payload(payload))

    def upsert_row(self, table: str, payload: Mapping, *, conflict_column: str):
        body = self._owned_payload(payload)
        return self._client.request(
            "POST",
            table,
            query={"on_conflict": conflict_column},
            payload=body,
            prefer="resolution=merge-duplicates,return=representation",
        )

    def update_row(self, table: str, row_id: str, payload: Mapping):
        body = self._owned_payload(payload)
        return self._client.request(
            "PATCH",
            table,
            query={"id": f"eq.{row_id}", "user_id": f"eq.{self.user_id}"},
            payload=body,
        )

    def delete_row(self, table: str, row_id: str):
        return self._client.request(
            "DELETE",
            table,
            query={"id": f"eq.{row_id}", "user_id": f"eq.{self.user_id}"},
        )
