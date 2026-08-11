#!/usr/bin/env python3
"""Materialize Cipher runtime secrets into root-owned runtime files.

Secrets are fetched from Google Secret Manager and written to a 0600
file that systemd services load via EnvironmentFile=.  Missing secrets
are logged and skipped — the service may still function with partial
credentials (e.g. no Tradier token when using Alpaca only).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any


PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GCP_PROJECT")
OUTPUT = Path(os.environ.get("CIPHER_ENV_OUTPUT", "/etc/cipher/cipher.env"))
CLOUDFLARE_TOKEN_OUTPUT = Path(
    os.environ.get("CIPHER_CLOUDFLARE_TOKEN_OUTPUT", "/etc/cipher/cloudflare-tunnel.token")
)
SECRETS = {
    "ALPACA_ALGO_KEY": "cipher-alpaca-algo-key",
    "ALPACA_ALGO_SECRET": "cipher-alpaca-algo-secret",
    "ALPACA_ALGO_PLUS_KEY": "cipher-alpaca-algo-plus-key",
    "ALPACA_ALGO_PLUS_SECRET": "cipher-alpaca-algo-plus-secret",
    "LSE_API_KEY": "cipher-lse-api-key",
    "TRADIER_ACCESS_TOKEN": "tradier-access-token",
    "CIPHER_APP_PASSWORD_HASH": "cipher-app-password-hash",
}
# Non-secret settings this script owns outright and rewrites on every run.
MANAGED_STATIC = {
    "ALPACA_DATA_FEED": "opra",
    "ALPACA_STOCK_FEED": "sip",
    "CIPHER_CORE_PORT": "8282",
    "PORT": "8283",
    "CIPHER_CORE_URL": "http://127.0.0.1:8282",
}
MANAGED_NAMES = frozenset(SECRETS) | frozenset(MANAGED_STATIC) | {"CIPHER_APP_AUTH"}


def access(client: Any, secret: str) -> str | None:
    assert PROJECT_ID
    name = f"projects/{PROJECT_ID}/secrets/{secret}/versions/latest"
    try:
        response = client.access_secret_version(request={"name": name})
    except Exception as exc:
        print(f"  secret {secret}: not available ({type(exc).__name__})", file=sys.stderr)
        return None
    return response.payload.data.decode("utf-8").strip()


def quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def unquote(raw: str) -> str:
    """Invert `quote` so a value this script wrote can be read back unchanged."""
    if len(raw) >= 2 and raw[0] == "'" and raw[-1] == "'":
        return raw[1:-1].replace("'\"'\"'", "'")
    return raw


def parse_env_file(path: Path) -> dict[str, str]:
    """Read the current environment file into a mapping, ignoring comments."""
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, raw = stripped.partition("=")
        key = key.strip()
        if key:
            values[key] = unquote(raw.strip())
    return values


def resolve_secrets(client: Any, existing: dict[str, str]) -> dict[str, str]:
    """Fetch each managed secret, retaining the configured value if a fetch fails.

    A Secret Manager outage used to blank every credential in the output file, and for
    CIPHER_APP_PASSWORD_HASH that meant the login gate turned itself off while the app
    stayed reachable. Falling back to the value already on disk makes an unreachable
    Secret Manager a no-op instead of a downgrade.
    """
    resolved: dict[str, str] = {}
    for env_name, secret_name in SECRETS.items():
        value = access(client, secret_name)
        if value:
            resolved[env_name] = value
        elif existing.get(env_name):
            print(f"  keeping configured {env_name} (secret '{secret_name}' unavailable)")
            resolved[env_name] = existing[env_name]
        else:
            print(f"  skipping {env_name} (secret '{secret_name}' not available)")
    return resolved


def render_env(resolved: dict[str, str], existing: dict[str, str]) -> str:
    """Render the environment file, preserving keys this script does not own.

    The output is rebuilt from scratch on every run, so anything absent from SECRETS or
    MANAGED_STATIC used to be deleted. TRADIER_STREAM_SYMBOLS was set by hand and vanished
    that way, narrowing the Tradier capture from 23 underlyings to the wrapper's 3-symbol
    fallback with nothing in the logs to say so. Unmanaged keys are now carried forward.
    """
    lines = [f"{name}={quote(value)}" for name, value in resolved.items()]
    lines += [f"{name}={value}" for name, value in MANAGED_STATIC.items()]
    # Auth follows the password, in both directions: configured means on, absent means the
    # server refuses to start rather than quietly serving market data to anyone.
    lines.append(f"CIPHER_APP_AUTH={'on' if resolved.get('CIPHER_APP_PASSWORD_HASH') else 'off'}")
    carried = {name: value for name, value in existing.items() if name not in MANAGED_NAMES}
    if carried:
        lines.append("# Preserved across syncs; set outside this script.")
        lines += [f"{name}={quote(value)}" for name, value in sorted(carried.items())]
    return "\n".join(lines) + "\n"


def write_private_file(path: Path, value: str) -> None:
    """Atomically replace a secret file without opening a permissive mode window."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(value, encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def main() -> int:
    from google.cloud import secretmanager

    if not PROJECT_ID:
        raise SystemExit("GOOGLE_CLOUD_PROJECT or GCP_PROJECT is required")
    client = secretmanager.SecretManagerServiceClient()
    existing = parse_env_file(OUTPUT)
    resolved = resolve_secrets(client, existing)
    rendered = render_env(resolved, existing)
    write_private_file(OUTPUT, rendered)
    preserved = sorted(name for name in existing if name not in MANAGED_NAMES)
    print(f"  wrote {len(rendered.splitlines())} lines to {OUTPUT}")
    if preserved:
        print(f"  preserved unmanaged keys: {', '.join(preserved)}")

    tunnel_token = access(client, "cipher-cloudflare-tunnel-token")
    if tunnel_token:
        # cloudflared consumes this through systemd LoadCredential; it never appears
        # in ExecStart, process arguments, the shared Cipher environment, or logs.
        write_private_file(CLOUDFLARE_TOKEN_OUTPUT, tunnel_token + "\n")
        print(f"  wrote Cloudflare tunnel credential to {CLOUDFLARE_TOKEN_OUTPUT}")
    else:
        print("  Cloudflare tunnel credential unavailable; connector remains disabled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
