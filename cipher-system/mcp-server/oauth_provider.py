#!/usr/bin/env python3
"""Minimal OAuth 2.1 authorization server for the Cipher MCP bridge.

ChatGPT will not accept a static bearer token for a custom connector. It performs the MCP
authorization flow: discover metadata, register itself dynamically, run an authorization-code
exchange with PKCE, then call the server with the resulting access token. Without those
endpoints it reports "MCP server ... does not implement OAuth" and stops.

This implements exactly that flow and nothing more:

*   **RFC 9728** protected-resource metadata, so a 401 tells a client where to authorize.
*   **RFC 8414** authorization-server metadata.
*   **RFC 7591** dynamic client registration, because the client cannot be pre-registered.
*   **RFC 7636** PKCE with S256 only. `plain` is refused.

The security question this raises is who is allowed to approve an authorization request. The
answer is whoever holds the operator secret: `/authorize` renders a consent form that will
not issue a code until the bridge's own token is entered. Auto-approving would mean anyone who
found the URL could mint a token for themselves, which would be worse than the static bearer
it replaces, not better.

State lives in a 0600 file so a restart or reboot does not silently disconnect the connector.
Access tokens are short-lived and refreshable; authorization codes are single-use.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

STATE_PATH = Path(os.environ.get(
    "CIPHER_MCP_OAUTH_STATE",
    "/home/aarav/Aarav/cipher/runtime/config/mcp-oauth-state.json",
))

CODE_TTL = 600           # 10 minutes, per OAuth 2.1 guidance for authorization codes.
ACCESS_TTL = 24 * 3600
REFRESH_TTL = 90 * 24 * 3600

_LOCK = threading.Lock()


# --------------------------------------------------------------------------- state

def _blank() -> dict[str, Any]:
    return {"clients": {}, "codes": {}, "tokens": {}, "refresh": {}}


def _load() -> dict[str, Any]:
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _blank()
    for key in _blank():
        data.setdefault(key, {})
    return data


def _save(state: dict[str, Any]) -> None:
    now = time.time()
    # Drop anything expired on every write; this store never needs to grow.
    state["codes"] = {k: v for k, v in state["codes"].items() if v.get("expires", 0) > now}
    state["tokens"] = {k: v for k, v in state["tokens"].items() if v.get("expires", 0) > now}
    state["refresh"] = {k: v for k, v in state["refresh"].items() if v.get("expires", 0) > now}
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(STATE_PATH)


# --------------------------------------------------------------------------- metadata

def protected_resource_metadata(base: str) -> dict[str, Any]:
    return {
        "resource": f"{base}/mcp",
        "authorization_servers": [base],
        "bearer_methods_supported": ["header"],
        "scopes_supported": ["cipher.read"],
        "resource_documentation": f"{base}/health",
    }


def authorization_server_metadata(base: str) -> dict[str, Any]:
    return {
        "issuer": base,
        "authorization_endpoint": f"{base}/authorize",
        "token_endpoint": f"{base}/token",
        "registration_endpoint": f"{base}/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        # S256 only. `plain` defeats the point of PKCE.
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": ["cipher.read"],
        "service_documentation": f"{base}/health",
    }


# --------------------------------------------------------------------------- registration

def register_client(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    redirect_uris = body.get("redirect_uris")
    if not isinstance(redirect_uris, list) or not redirect_uris:
        return 400, {"error": "invalid_redirect_uri", "error_description": "redirect_uris is required"}
    for uri in redirect_uris:
        if not isinstance(uri, str) or not _redirect_allowed(uri):
            return 400, {
                "error": "invalid_redirect_uri",
                "error_description": f"redirect_uri must be https, or http on loopback: {uri!r}",
            }
    client_id = "cipher-" + secrets.token_urlsafe(18)
    record = {
        "client_id": client_id,
        "redirect_uris": redirect_uris,
        "client_name": str(body.get("client_name") or "")[:120],
        "created": time.time(),
        # A public client with PKCE needs no secret, and issuing one we would never verify
        # would only create a credential to leak.
        "token_endpoint_auth_method": "none",
    }
    with _LOCK:
        state = _load()
        state["clients"][client_id] = record
        _save(state)
    return 201, {
        "client_id": client_id,
        "client_id_issued_at": int(record["created"]),
        "redirect_uris": redirect_uris,
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "client_name": record["client_name"],
    }


def _redirect_allowed(uri: str) -> bool:
    if uri.startswith("https://"):
        return True
    # Loopback HTTP is permitted for native clients by OAuth 2.1.
    return uri.startswith("http://127.0.0.1") or uri.startswith("http://localhost")


# --------------------------------------------------------------------------- authorize

def _client(client_id: str) -> dict[str, Any] | None:
    with _LOCK:
        return _load()["clients"].get(client_id)


def validate_authorize(params: dict[str, str]) -> tuple[dict[str, Any] | None, str | None]:
    """Check an /authorize request. Returns (context, error_description)."""
    client_id = params.get("client_id") or ""
    client = _client(client_id)
    if not client:
        return None, "unknown client_id; register first"
    redirect_uri = params.get("redirect_uri") or ""
    if redirect_uri not in client["redirect_uris"]:
        return None, "redirect_uri does not match a registered value"
    if (params.get("response_type") or "") != "code":
        return None, "response_type must be code"
    challenge = params.get("code_challenge") or ""
    if not challenge:
        return None, "code_challenge is required (PKCE)"
    if (params.get("code_challenge_method") or "") != "S256":
        return None, "code_challenge_method must be S256"
    return {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": challenge,
        "state": params.get("state") or "",
        "scope": params.get("scope") or "cipher.read",
        "resource": params.get("resource") or "",
        "client_name": client.get("client_name") or client_id,
    }, None


def issue_code(context: dict[str, Any]) -> str:
    code = secrets.token_urlsafe(32)
    with _LOCK:
        state = _load()
        state["codes"][code] = {
            "client_id": context["client_id"],
            "redirect_uri": context["redirect_uri"],
            "code_challenge": context["code_challenge"],
            "scope": context["scope"],
            "expires": time.time() + CODE_TTL,
        }
        _save(state)
    return code


def redirect_with_code(context: dict[str, Any], code: str) -> str:
    query = {"code": code}
    if context.get("state"):
        query["state"] = context["state"]
    joiner = "&" if "?" in context["redirect_uri"] else "?"
    return f"{context['redirect_uri']}{joiner}{urlencode(query)}"


def consent_page(context: dict[str, Any], base: str, *, error: str | None = None) -> bytes:
    """The approval step. No code is issued until the operator secret is supplied."""
    fields = "".join(
        f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(str(v))}">'
        for k, v in {
            "client_id": context["client_id"],
            "redirect_uri": context["redirect_uri"],
            "response_type": "code",
            "code_challenge": context["code_challenge"],
            "code_challenge_method": "S256",
            "state": context["state"],
            "scope": context["scope"],
        }.items()
    )
    warning = f'<p class="err">{html.escape(error)}</p>' if error else ""
    body = f"""<!doctype html><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Authorize Cipher access</title>
<style>
:root {{ color-scheme: light dark; }}
body {{ font: 15px/1.55 system-ui, sans-serif; max-width: 30rem; margin: 4rem auto; padding: 0 1.25rem; }}
h1 {{ font-size: 1.2rem; margin-bottom: .35rem; }}
.sub {{ color: #6b7280; margin-top: 0; }}
form {{ margin-top: 1.5rem; display: grid; gap: .75rem; }}
input[type=password] {{ padding: .6rem .7rem; font: inherit; border: 1px solid #9ca3af; border-radius: 8px; }}
button {{ padding: .6rem 1rem; font: inherit; font-weight: 600; border: 0; border-radius: 8px;
  background: #6d28d9; color: #fff; cursor: pointer; }}
.err {{ color: #b91c1c; font-weight: 600; }}
ul {{ color: #6b7280; padding-left: 1.1rem; }}
code {{ background: rgba(127,127,127,.15); padding: .05rem .3rem; border-radius: 4px; }}
</style>
<h1>Authorize read-only Cipher access</h1>
<p class="sub"><strong>{html.escape(context['client_name'])}</strong> is requesting access to
{html.escape(base)}</p>
{warning}
<p>This grant allows:</p>
<ul>
  <li>Reading quotes, bars, gamma exposure levels and headlines</li>
  <li>Reading strategy research status</li>
</ul>
<p>It cannot place, size, modify or cancel an order. Cipher is read-only.</p>
<form method="POST" action="/authorize">
  {fields}
  <label for="secret">Operator token</label>
  <input id="secret" name="secret" type="password" autocomplete="current-password" required
         placeholder="contents of mcp-bearer-token.txt">
  <button type="submit">Approve access</button>
</form>
"""
    return body.encode("utf-8")


# --------------------------------------------------------------------------- token

def _sha256_b64url(value: str) -> str:
    digest = hashlib.sha256(value.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def exchange(form: dict[str, str]) -> tuple[int, dict[str, Any]]:
    grant = form.get("grant_type") or ""
    if grant == "authorization_code":
        return _exchange_code(form)
    if grant == "refresh_token":
        return _exchange_refresh(form)
    return 400, {"error": "unsupported_grant_type"}


def _issue_tokens(client_id: str, scope: str) -> dict[str, Any]:
    access = "cipher_at_" + secrets.token_urlsafe(32)
    refresh = "cipher_rt_" + secrets.token_urlsafe(32)
    now = time.time()
    with _LOCK:
        state = _load()
        state["tokens"][access] = {"client_id": client_id, "scope": scope, "expires": now + ACCESS_TTL}
        state["refresh"][refresh] = {"client_id": client_id, "scope": scope, "expires": now + REFRESH_TTL}
        _save(state)
    return {
        "access_token": access,
        "token_type": "Bearer",
        "expires_in": ACCESS_TTL,
        "refresh_token": refresh,
        "scope": scope,
    }


def _exchange_code(form: dict[str, str]) -> tuple[int, dict[str, Any]]:
    code = form.get("code") or ""
    verifier = form.get("code_verifier") or ""
    client_id = form.get("client_id") or ""
    with _LOCK:
        state = _load()
        record = state["codes"].pop(code, None)
        # Popped before validation: an authorization code is single-use, so even a failed
        # attempt must consume it rather than leave it available for another guess.
        _save(state)
    if not record:
        return 400, {"error": "invalid_grant", "error_description": "unknown or used code"}
    if record["expires"] < time.time():
        return 400, {"error": "invalid_grant", "error_description": "code expired"}
    if client_id and client_id != record["client_id"]:
        return 400, {"error": "invalid_grant", "error_description": "client mismatch"}
    redirect_uri = form.get("redirect_uri")
    if redirect_uri and redirect_uri != record["redirect_uri"]:
        return 400, {"error": "invalid_grant", "error_description": "redirect_uri mismatch"}
    if not verifier:
        return 400, {"error": "invalid_request", "error_description": "code_verifier is required"}
    if not hmac.compare_digest(_sha256_b64url(verifier), record["code_challenge"]):
        return 400, {"error": "invalid_grant", "error_description": "PKCE verification failed"}
    return 200, _issue_tokens(record["client_id"], record.get("scope") or "cipher.read")


def _exchange_refresh(form: dict[str, str]) -> tuple[int, dict[str, Any]]:
    token = form.get("refresh_token") or ""
    with _LOCK:
        state = _load()
        record = state["refresh"].pop(token, None)
        _save(state)
    if not record or record["expires"] < time.time():
        return 400, {"error": "invalid_grant", "error_description": "unknown or expired refresh_token"}
    return 200, _issue_tokens(record["client_id"], record.get("scope") or "cipher.read")


def token_is_valid(presented: str) -> bool:
    """Constant-time check of an issued access token."""
    if not presented:
        return False
    with _LOCK:
        tokens = _load()["tokens"]
    now = time.time()
    for issued, record in tokens.items():
        if record.get("expires", 0) > now and hmac.compare_digest(presented, issued):
            return True
    return False


def revoke_all() -> None:
    with _LOCK:
        state = _load()
        state["codes"] = {}
        state["tokens"] = {}
        state["refresh"] = {}
        _save(state)
