#!/usr/bin/env bash
# Launch Alpaca's official MCP server with paper trading locked on.
#
# Two things this wrapper exists to guarantee, neither of which the upstream server
# does by itself:
#
#   1. ALPACA_PAPER_TRADE is forced to "true" here and cannot be overridden by the
#      environment. Upstream defaults it to true, but a default is a thing that can be
#      changed by whoever edits the MCP config next; this is not.
#   2. The key must be a paper key. Alpaca paper keys are prefixed "PK", live keys "AK".
#      We require the PK prefix rather than merely rejecting AK, so an unrecognised
#      prefix fails closed. Absent or surprising configuration disables the server, it
#      never silently widens what the server can reach -- the CIPHER_APP_AUTH=off lesson.
#
# Credentials live in runtime/config, not /etc/cipher/cipher.env: that file is rebuilt
# from scratch by sync-secrets.py and lost every credential during the 2026-08-12 reboot,
# while runtime/config secrets survived it.
#
# Usage: alpaca_paper_mcp.sh [toolsets]
#   toolsets defaults to a read-only set with no order capability at all.
set -euo pipefail

ENV_FILE="${ALPACA_ENV_FILE:-/home/aarav/Aarav/cipher/runtime/config/alpaca-paper.env}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "alpaca_paper_mcp: no credential file at $ENV_FILE" >&2
  echo "Create it with mode 0600 containing:" >&2
  echo "  ALPACA_API_KEY=PK..." >&2
  echo "  ALPACA_SECRET_KEY=..." >&2
  exit 1
fi

# Read only the two keys we expect, so an unrelated line in the file cannot inject
# environment into the server process.
ALPACA_API_KEY="$(sed -n 's/^ALPACA_API_KEY=//p' "$ENV_FILE" | head -1)"
ALPACA_SECRET_KEY="$(sed -n 's/^ALPACA_SECRET_KEY=//p' "$ENV_FILE" | head -1)"

if [[ -z "$ALPACA_API_KEY" || -z "$ALPACA_SECRET_KEY" ]]; then
  echo "alpaca_paper_mcp: ALPACA_API_KEY or ALPACA_SECRET_KEY missing from $ENV_FILE" >&2
  exit 1
fi

if [[ "$ALPACA_API_KEY" != PK* ]]; then
  echo "alpaca_paper_mcp: refusing to start -- key does not carry the paper 'PK' prefix." >&2
  echo "This server is paper-only by construction. Live keys are not supported here." >&2
  exit 1
fi

# No 'trading' toolset by default: the order, position and exercise tools are simply not
# registered, so no prompt can reach them. Pass "…,trading" explicitly to opt in, which
# is still paper-locked by the checks above. Crypto is omitted -- out of scope for Cipher.
TOOLSETS="${1:-account,assets,stock-data,options-data,news,index-data,corporate-actions}"

export ALPACA_API_KEY ALPACA_SECRET_KEY
export ALPACA_PAPER_TRADE=true
export ALPACA_TOOLSETS="$TOOLSETS"

exec uvx alpaca-mcp-server
