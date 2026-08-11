#!/usr/bin/env bash
#
# On-demand viewable desktop for the VM's browser.
#
# Why this exists: the AccessObsidian capture path assumed a browser on the operator's own
# machine, signed in via Discord, driven by a local WebBridge on port 10086. Removing that
# machine from the loop means the VM has to hold the session, and a Discord OAuth flow cannot
# be completed without a human at a real browser. This gives that human a screen.
#
# Deliberately NOT a persistent service. It is an interactive login surface, so it runs while
# someone is using it and is torn down afterwards. Leaving an X server, a VNC daemon and a
# WebSocket bridge running permanently would be attack surface bought for nothing.
#
# Security model, in order of who can reach what:
#   * Xvfb is a virtual framebuffer with no physical output.
#   * x11vnc binds 127.0.0.1 only and always requires a password. It is never opened directly.
#   * websockify/noVNC binds 127.0.0.1 only and wraps VNC for a browser.
#   * Reachability is a separate, explicit step: `expose-tailnet` (tailnet only) or
#     `expose-public` (Tailscale Funnel, HTTPS, still password-gated). Neither is automatic.
#
# The VNC password is generated here, stored 0600 outside the repo, and never printed by the
# start command. Read it deliberately with `devview.sh password`.
set -euo pipefail

DISPLAY_NUM="${CIPHER_DEVVIEW_DISPLAY:-99}"
GEOMETRY="${CIPHER_DEVVIEW_GEOMETRY:-1600x1000x24}"
VNC_PORT=5900
WEB_PORT="${CIPHER_DEVVIEW_WEB_PORT:-6080}"
FUNNEL_PORT=10000

CONFIG_DIR="${CIPHER_DEVVIEW_CONFIG:-/home/aarav/Aarav/cipher/runtime/config}"
RUN_DIR="${CIPHER_DEVVIEW_RUN:-/home/aarav/Aarav/cipher/runtime/devview}"
PASSWD_FILE="$CONFIG_DIR/devview-vnc.passwd"
PLAIN_FILE="$CONFIG_DIR/devview-vnc.txt"
# The browser profile is the point: it outlives the viewer so a completed Discord sign-in
# persists after the screen is torn down.
PROFILE_DIR="${CIPHER_DEVVIEW_PROFILE:-/home/aarav/Aarav/cipher/runtime/browser-profiles/accessobsidian}"

mkdir -p "$RUN_DIR" "$PROFILE_DIR"
chmod 700 "$PROFILE_DIR"

log() { printf '  %s\n' "$*"; }

ensure_password() {
  if [[ -f "$PASSWD_FILE" ]]; then return; fi
  local plain
  # Generated, never typed, never echoed. x11vnc truncates beyond 8 characters, so this is
  # 8 characters from a 62-character alphabet (~47 bits) rather than a longer string that
  # would be silently cut.
  # `tr </dev/urandom | head -c 8` dies of SIGPIPE under `set -o pipefail`: head closes the
  # pipe after 8 bytes and the pipeline exits 141, aborting the script. openssl emits a
  # bounded amount and `cut` reads to EOF, so nothing gets signalled.
  plain="$(openssl rand -base64 24 | LC_ALL=C tr -dc 'A-Za-z0-9' | cut -c1-8)"
  [[ ${#plain} -eq 8 ]] || { echo "password generation failed"; exit 1; }
  ( umask 077; printf '%s\n' "$plain" > "$PLAIN_FILE" )
  x11vnc -storepasswd "$plain" "$PASSWD_FILE" >/dev/null 2>&1
  chmod 600 "$PASSWD_FILE"
  unset plain
  log "generated a VNC password at $PLAIN_FILE (0600); read it with: devview.sh password"
}

running() { pgrep -f "$1" >/dev/null 2>&1; }

start() {
  command -v Xvfb    >/dev/null || { echo "Xvfb not installed"; exit 1; }
  command -v x11vnc  >/dev/null || { echo "x11vnc not installed (apt install x11vnc)"; exit 1; }
  ensure_password

  if ! running "Xvfb :$DISPLAY_NUM"; then
    Xvfb ":$DISPLAY_NUM" -screen 0 "$GEOMETRY" -nolisten tcp >"$RUN_DIR/xvfb.log" 2>&1 &
    sleep 1
    log "Xvfb on :$DISPLAY_NUM at $GEOMETRY"
  else
    log "Xvfb already on :$DISPLAY_NUM"
  fi

  # A window manager is not cosmetic here. Without one, X has no client to honour focus,
  # resize or stacking requests, so a browser window cannot reliably be focused and popup
  # windows -- which is what a Discord OAuth flow is -- may never become visible or
  # keyboard-focusable. openbox is small and does exactly that job.
  if command -v openbox >/dev/null && ! running "openbox.*DISPLAY=:$DISPLAY_NUM\|openbox --replace"; then
    DISPLAY=":$DISPLAY_NUM" openbox >"$RUN_DIR/openbox.log" 2>&1 &
    sleep 1
    log "openbox window manager on :$DISPLAY_NUM"
  fi

  if ! running "x11vnc.*:$DISPLAY_NUM"; then
    # -localhost is the important flag: the VNC port itself is never reachable off-box.
    # -forever survives a viewer disconnecting mid sign-in; -shared allows a reconnect.
    x11vnc -display ":$DISPLAY_NUM" -rfbauth "$PASSWD_FILE" -rfbport "$VNC_PORT" \
           -localhost -forever -shared -noxdamage -quiet \
           >"$RUN_DIR/x11vnc.log" 2>&1 &
    sleep 1
    log "x11vnc on 127.0.0.1:$VNC_PORT (password required)"
  else
    log "x11vnc already running"
  fi

  if ! running "websockify.*$WEB_PORT"; then
    local web_root=""
    for candidate in /usr/share/novnc /usr/share/webapps/novnc; do
      [[ -d "$candidate" ]] && web_root="$candidate" && break
    done
    if [[ -n "$web_root" ]]; then
      websockify --web="$web_root" "127.0.0.1:$WEB_PORT" "127.0.0.1:$VNC_PORT" \
        >"$RUN_DIR/websockify.log" 2>&1 &
      sleep 1
      log "noVNC on 127.0.0.1:$WEB_PORT (web root $web_root)"
    else
      log "noVNC web assets not found; VNC still available on 127.0.0.1:$VNC_PORT"
    fi
  else
    log "websockify already running"
  fi

  log ""
  log "Nothing is reachable from outside yet. Choose one:"
  log "  devview.sh expose-tailnet   # needs a Tailscale client on your device"
  log "  devview.sh expose-public    # Funnel over HTTPS; still password-gated"
}

browser() {
  running "Xvfb :$DISPLAY_NUM" || { echo "run 'devview.sh start' first"; exit 1; }
  # Headed, with a persistent profile so the sign-in survives. --no-sandbox because this
  # runs as an unprivileged user in a container-like VM without user namespaces.
  DISPLAY=":$DISPLAY_NUM" setsid chromium \
    --user-data-dir="$PROFILE_DIR" \
    --no-sandbox --disable-dev-shm-usage --no-first-run --no-default-browser-check \
    --window-position=0,0 --window-size=1580,980 \
    "${1:-https://www.accessobsidian.com/app#CI}" \
    >"$RUN_DIR/chromium.log" 2>&1 &
  sleep 2
  log "chromium launched on :$DISPLAY_NUM with profile $PROFILE_DIR"
  log "sign in through the viewer; the session persists in that profile after teardown"
}

expose_tailnet() {
  sudo -n tailscale serve --bg --https="$FUNNEL_PORT" "http://127.0.0.1:$WEB_PORT"
  log "tailnet-only: https://$(tailscale status --json | python3 -c 'import json,sys;print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))'):$FUNNEL_PORT/vnc.html"
}

expose_public() {
  # Public, so it is HTTPS and password-gated and expected to be short-lived. Turn it off
  # with `devview.sh unexpose` the moment the sign-in is done.
  sudo -n tailscale funnel --bg --https="$FUNNEL_PORT" "http://127.0.0.1:$WEB_PORT"
  log "PUBLIC: https://$(tailscale status --json | python3 -c 'import json,sys;print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))'):$FUNNEL_PORT/vnc.html"
  log "this is internet-reachable. run 'devview.sh unexpose' as soon as you are signed in."
}

unexpose() {
  sudo -n tailscale funnel --https="$FUNNEL_PORT" off 2>/dev/null || true
  sudo -n tailscale serve  --https="$FUNNEL_PORT" off 2>/dev/null || true
  log "port $FUNNEL_PORT withdrawn from tailnet and internet"
}

stop() {
  unexpose
  pkill -f "websockify.*$WEB_PORT" 2>/dev/null || true
  pkill -f "x11vnc.*:$DISPLAY_NUM" 2>/dev/null || true
  pkill -f "chromium.*$PROFILE_DIR" 2>/dev/null || true
  pkill -f "openbox" 2>/dev/null || true
  pkill -f "Xvfb :$DISPLAY_NUM" 2>/dev/null || true
  log "torn down. the browser profile at $PROFILE_DIR is kept."
}

status() {
  for pattern in "Xvfb :$DISPLAY_NUM" "openbox" "x11vnc.*:$DISPLAY_NUM" "websockify.*$WEB_PORT" "chromium.*$PROFILE_DIR"; do
    printf '  %-34s %s\n' "${pattern%%.*}" "$(running "$pattern" && echo running || echo stopped)"
  done
  printf '  %-34s %s\n' "profile" "$PROFILE_DIR ($(du -sh "$PROFILE_DIR" 2>/dev/null | cut -f1))"
  echo "  exposure:"
  sudo -n tailscale serve status 2>/dev/null | grep -E ":$FUNNEL_PORT" | sed 's/^/    /' || echo "    not exposed"
}

case "${1:-}" in
  start)           start ;;
  browser)         browser "${2:-}" ;;
  expose-tailnet)  expose_tailnet ;;
  expose-public)   expose_public ;;
  unexpose)        unexpose ;;
  stop)            stop ;;
  status)          status ;;
  password)        cat "$PLAIN_FILE" ;;
  *)
    cat <<USAGE
usage: devview.sh <command>

  start            Xvfb + x11vnc + noVNC, all bound to loopback
  browser [url]    launch headed chromium with the persistent profile
  expose-tailnet   reachable from your tailnet only
  expose-public    reachable over HTTPS from anywhere, password-gated, short-lived
  unexpose         withdraw the port
  stop             tear everything down, keep the browser profile
  status           what is running and what is exposed
  password         print the generated VNC password
USAGE
    ;;
esac
