#!/usr/bin/env bash
# ── Tailscale setup for Cipher VM ───────────────────────────────────────
# Run once after initial configuration.  Requires interactive auth.
#
# • Brings up Tailscale with the VM hostname
# • Enables Tailscale SSH for admin access over the tailnet
# • Exposes DevSpace through Tailscale Serve (NOT Funnel) by default
# • Funnel is only enabled if CIPHER_FUNNEL=1 is explicitly set
# ─────────────────────────────────────────────────────────────────────────
set -euo pipefail

HOSTNAME="${TAILSCALE_HOSTNAME:-cipher-main}"
DEVSPACE_PORT="${DEVSPACE_PORT:-7676}"
CIPHER_PORT="${CIPHER_PORT:-8283}"
ENABLE_FUNNEL="${CIPHER_FUNNEL:-0}"

sudo systemctl enable --now tailscaled

if ! tailscale status >/dev/null 2>&1; then
  echo "▶ Bringing up Tailscale (interactive auth required)..."
  sudo tailscale up --hostname "$HOSTNAME" --ssh
else
  echo "▶ Tailscale already authenticated."
fi

# Tailscale Serve: expose Cipher web on the tailnet only (HTTPS 8443)
sudo tailscale serve --bg --https=8443 "http://127.0.0.1:${CIPHER_PORT}"
echo "  Cipher web: https://${HOSTNAME}.tailnet:8443/"

# DevSpace: serve on the tailnet
sudo tailscale serve --bg --https=7676 "http://127.0.0.1:${DEVSPACE_PORT}"
echo "  DevSpace:   https://${HOSTNAME}.tailnet:7676/"

if [[ "$ENABLE_FUNNEL" == "1" ]]; then
  echo "⚠  Enabling Tailscale Funnel for DevSpace (public HTTPS)..."
  sudo tailscale funnel --bg --https=7676 "http://127.0.0.1:${DEVSPACE_PORT}"
  echo "  DevSpace Funnel: https://${HOSTNAME}.tailnet:7676/ (public)"
else
  echo "  Funnel disabled (set CIPHER_FUNNEL=1 to enable for ChatGPT MCP)."
fi

echo ""
echo "Done.  Verify with:  tailscale status"
