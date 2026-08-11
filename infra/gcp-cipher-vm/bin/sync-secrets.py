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
    values: list[str] = []
    password_hash_configured = False
    for env_name, secret_name in SECRETS.items():
        value = access(client, secret_name)
        if value:
            values.append(f"{env_name}={quote(value)}")
            if env_name == "CIPHER_APP_PASSWORD_HASH":
                password_hash_configured = True
        else:
            print(f"  skipping {env_name} (secret '{secret_name}' not available)")
    values.extend(
        [
            "ALPACA_DATA_FEED=opra",
            "ALPACA_STOCK_FEED=sip",
            "CIPHER_CORE_PORT=8282",
            "PORT=8283",
            "CIPHER_CORE_URL=http://127.0.0.1:8282",
            # The VM remains loopback-bound and tailnet-only until a password is
            # configured. A password secret automatically restores fail-closed auth.
            f"CIPHER_APP_AUTH={'on' if password_hash_configured else 'off'}",
        ]
    )
    write_private_file(OUTPUT, "\n".join(values) + "\n")
    print(f"  wrote {len(values)} variables to {OUTPUT}")

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
