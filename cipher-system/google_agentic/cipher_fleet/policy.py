"""Fleet-wide policy enforcement and privacy-preserving ADK audit events."""
from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .tools import TOOL_NAMES


DEFAULT_AUDIT_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "agent_fleet" / "audit.jsonl"
)
_LOCK = threading.Lock()


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def content_fingerprint(value: Any) -> dict[str, Any]:
    raw = _canonical(value)
    return {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}


class AuditTrail:
    """Append metadata-only events without retaining private model/tool content."""

    def __init__(self, path: str | Path | None = None):
        configured = path or os.environ.get("CIPHER_AGENT_AUDIT_PATH") or DEFAULT_AUDIT_PATH
        self.path = Path(configured)

    def append(self, event: str, **metadata: Any) -> None:
        row = {"timestamp": utcnow(), "event": event, **metadata}
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with _LOCK:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")
            try:
                self.path.chmod(0o600)
            except OSError:
                pass


def tool_is_allowed(name: str) -> bool:
    return str(name or "") in TOOL_NAMES


try:
    from google.adk.plugins.base_plugin import BasePlugin
except ImportError:  # Unit tests for the hard boundary do not require ADK installed.
    BasePlugin = object  # type: ignore[assignment,misc]


class CipherPolicyAuditPlugin(BasePlugin):  # type: ignore[misc]
    """ADK plugin that enforces the tool allowlist and records execution evidence."""

    def __init__(self, audit: AuditTrail | None = None):
        if BasePlugin is not object:
            super().__init__(name="cipher_policy_audit")
        self.audit = audit or AuditTrail()

    async def on_user_message_callback(self, *, invocation_context: Any, user_message: Any) -> None:
        self.audit.append(
            "user_message_received",
            invocation_id=str(getattr(invocation_context, "invocation_id", "")),
            content=content_fingerprint(user_message),
        )
        return None

    async def before_run_callback(self, *, invocation_context: Any) -> None:
        self.audit.append(
            "run_started",
            invocation_id=str(getattr(invocation_context, "invocation_id", "")),
        )
        return None

    async def after_run_callback(self, *, invocation_context: Any) -> None:
        self.audit.append(
            "run_completed",
            invocation_id=str(getattr(invocation_context, "invocation_id", "")),
        )

    async def before_model_callback(self, *, callback_context: Any, llm_request: Any) -> None:
        self.audit.append(
            "model_requested",
            agent=str(getattr(callback_context, "agent_name", "")),
            model=str(getattr(llm_request, "model", "")),
        )
        return None

    async def after_model_callback(self, *, callback_context: Any, llm_response: Any) -> None:
        self.audit.append(
            "model_completed",
            agent=str(getattr(callback_context, "agent_name", "")),
            response=content_fingerprint(llm_response),
        )
        return None

    async def before_tool_callback(
        self,
        *,
        tool: Any,
        tool_args: dict[str, Any],
        tool_context: Any,
    ) -> dict[str, Any] | None:
        name = str(getattr(tool, "name", ""))
        self.audit.append(
            "tool_requested",
            tool=name,
            agent=str(getattr(tool_context, "agent_name", "")),
            arguments=content_fingerprint(tool_args),
            allowed=tool_is_allowed(name),
        )
        if not tool_is_allowed(name):
            self.audit.append("tool_blocked", tool=name, reason="not_allowlisted")
            return {
                "status": "blocked",
                "reason": "Tool is outside Cipher's research-only allowlist.",
                "human_review_required": True,
            }
        return None

    async def after_tool_callback(
        self,
        *,
        tool: Any,
        tool_args: dict[str, Any],
        tool_context: Any,
        result: dict[str, Any],
    ) -> None:
        self.audit.append(
            "tool_completed",
            tool=str(getattr(tool, "name", "")),
            agent=str(getattr(tool_context, "agent_name", "")),
            result=content_fingerprint(result),
        )
        return None

    async def on_tool_error_callback(
        self,
        *,
        tool: Any,
        tool_args: dict[str, Any],
        tool_context: Any,
        error: Exception,
    ) -> None:
        self.audit.append(
            "tool_failed",
            tool=str(getattr(tool, "name", "")),
            agent=str(getattr(tool_context, "agent_name", "")),
            error_type=type(error).__name__,
        )
        return None
