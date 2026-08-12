#!/usr/bin/env python3
"""Pre-register an OAuth client for a known callback URL.

ChatGPT offers two ways to obtain a client: Dynamic Client Registration, which needs the
server's `registration_endpoint` to be discovered and working, and a User-Defined OAuth
Client, where you paste a client_id you already hold. DCR is the smoother path when it works,
but it is one more moving part that can fail silently inside a host's connector wizard.

This registers a client directly against the same store the running bridge reads, so the
User-Defined path is available as a fallback that depends on nothing but the token endpoint.

    python3 register_oauth_client.py https://chatgpt.com/connector/oauth/XXXX

Prints the client_id to paste into the connector's "OAuth Client ID" field. Leave the client
secret blank: this is a public client using PKCE, and the token endpoint auth method is
`none`, which is what the server advertises.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import oauth_provider  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        sys.stderr.write(__doc__ or "")
        return 2
    redirect_uris = argv[1:]
    status, payload = oauth_provider.register_client({
        "redirect_uris": redirect_uris,
        "client_name": "ChatGPT connector",
    })
    if status != 201:
        sys.stderr.write(json.dumps(payload, indent=2) + "\n")
        return 1
    print(f"client_id:     {payload['client_id']}")
    print(f"redirect_uris: {', '.join(payload['redirect_uris'])}")
    print("client_secret: (leave blank — public client, PKCE, token auth method 'none')")
    print(f"state file:    {oauth_provider.STATE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
