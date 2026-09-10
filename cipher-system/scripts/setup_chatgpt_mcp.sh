#!/usr/bin/env bash
# One-shot repair/deploy for this Cipher VM; preserves existing OAuth credentials.
set -euo pipefail
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
app_dir="$(cd "$script_dir/.." && pwd)"
# Resolve against the real checkout, including when invoked through cipher-system symlink.
unit="$(realpath "$app_dir")/../infra/gcp-cipher-vm/systemd/cipher-mcp-bridge.service"
test -s /home/aarav/Aarav/cipher/runtime/config/mcp-bearer-token.txt
sudo test -s /etc/cipher/cipher.env
sudo install -m 644 "$unit" /etc/systemd/system/cipher-mcp-bridge.service
sudo systemctl daemon-reload
sudo systemctl enable --now cipher-mcp-bridge.service
sudo systemctl restart cipher-mcp-bridge.service
sudo tailscale funnel --bg --https=10000 http://127.0.0.1:8284
for attempt in 1 2 3 4 5; do
  if curl --fail --silent http://127.0.0.1:8284/health >/dev/null; then break; fi
  sleep 1
done
python3 "$app_dir/mcp-server/check_chatgpt.py" "$@"
