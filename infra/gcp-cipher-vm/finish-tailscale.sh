#!/usr/bin/env bash
# Complete the Cipher VM Tailscale cutover after the node has been authorized.
set -euo pipefail

DEVSPACE_PORT="${DEVSPACE_PORT:-7676}"
CIPHER_PORT="${CIPHER_PORT:-8283}"
DEVSPACE_CONFIG="/home/aarav/.devspace/config.json"

state="$(tailscale status --json | jq -r '.BackendState')"
if [[ "$state" != "Running" ]]; then
  echo "ERROR: Tailscale is not authenticated (state=$state)." >&2
  exit 1
fi

dns_name="$(tailscale status --json | jq -r '.Self.DNSName // empty' | sed 's/\.$//')"
if [[ -z "$dns_name" ]]; then
  echo "ERROR: Tailscale did not provide a MagicDNS hostname." >&2
  exit 1
fi

# Private tailnet-only Cipher UI.  Do not expose the market-data app publicly.
tailscale serve --bg --https=8443 "http://127.0.0.1:${CIPHER_PORT}"

# ChatGPT cannot join the user's tailnet, so DevSpace requires a public HTTPS
# endpoint.  Funnel exposes only the loopback-bound DevSpace process; DevSpace's
# own OAuth owner-approval flow remains mandatory.
tailscale funnel --bg --https=443 "http://127.0.0.1:${DEVSPACE_PORT}"

python3 - "$DEVSPACE_CONFIG" "https://${dns_name}" <<'PY'
import json
import os
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
public_url = sys.argv[2]
data = json.loads(path.read_text()) if path.exists() else {}
data.update({
    "host": "127.0.0.1",
    "port": 7676,
    "allowedRoots": ["/home/aarav/Aarav"],
    "publicBaseUrl": public_url,
})
tmp = path.with_suffix(".json.tmp")
tmp.write_text(json.dumps(data, indent=2) + "\n")
os.chmod(tmp, 0o600)
tmp.replace(path)
PY

chown aarav:aarav "$DEVSPACE_CONFIG"
chmod 600 "$DEVSPACE_CONFIG"
systemctl restart cipher-devspace.service

for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:${DEVSPACE_PORT}/mcp" -o /dev/null 2>/dev/null; then
    break
  fi
  # A 401 is the expected unauthenticated DevSpace response.
  code="$(curl -sS -o /dev/null -w '%{http_code}' "http://127.0.0.1:${DEVSPACE_PORT}/mcp" || true)"
  [[ "$code" == "401" ]] && break
  sleep 1
done

install -d -m 0755 /var/lib/cipher
printf 'completed_at=%s\ndns_name=%s\n' "$(date -u +%FT%TZ)" "$dns_name" > /var/lib/cipher/tailscale-ready
chmod 600 /var/lib/cipher/tailscale-ready

echo "Tailscale cutover complete."
echo "DevSpace: https://${dns_name}/mcp"
echo "Cipher UI (tailnet only): https://${dns_name}:8443/"
echo "SSH: ssh aarav@${dns_name}"
