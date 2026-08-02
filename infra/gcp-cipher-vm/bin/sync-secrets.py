#!/usr/bin/env python3
"""Materialize Cipher runtime secrets into a root-owned environment file.

Secrets are fetched from Google Secret Manager and written to a 0600
file that systemd services load via EnvironmentFile=.  Missing secrets
are logged and skipped — the service may still function with partial
credentials (e.g. no Tradier token when using Alpaca only).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from google.cloud import secretmanager


PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GCP_PROJECT")
OUTPUT = Path(os.environ.get("CIPHER_ENV_OUTPUT", "/etc/cipher/cipher.env"))
SECRETS = {
    "ALPACA_ALGO_KEY": "cipher-alpaca-algo-key",
    "ALPACA_ALGO_SECRET": "cipher-alpaca-algo-secret",
    "ALPACA_ALGO_PLUS_KEY": "cipher-alpaca-algo-plus-key",
    "ALPACA_ALGO_PLUS_SECRET": "cipher-alpaca-algo-plus-secret",
    "LSE_API_KEY": "cipher-lse-api-key",
    "TRADIER_ACCESS_TOKEN": "tradier-access-token",
}


def access(client: secretmanager.SecretManagerServiceClient, secret: str) -> str | None:
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


def main() -> int:
    if not PROJECT_ID:
        raise SystemExit("GOOGLE_CLOUD_PROJECT or GCP_PROJECT is required")
    client = secretmanager.SecretManagerServiceClient()
    values: list[str] = []
    for env_name, secret_name in SECRETS.items():
        value = access(client, secret_name)
        if value:
            values.append(f"{env_name}={quote(value)}")
        else:
            print(f"  skipping {env_name} (secret '{secret_name}' not available)")
    values.extend(
        [
            "ALPACA_DATA_FEED=opra",
            "ALPACA_STOCK_FEED=sip",
            "CIPHER_CORE_PORT=8282",
            "PORT=8283",
            "CIPHER_CORE_URL=http://127.0.0.1:8282",
        ]
    )
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUTPUT.with_suffix(".tmp")
    temporary.write_text("\n".join(values) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(OUTPUT)
    print(f"  wrote {len(values)} variables to {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
