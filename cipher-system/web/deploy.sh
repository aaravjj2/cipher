#!/usr/bin/env bash
# Build the Cipher frontend and publish it to the directory server.mjs serves.
#
# Replaces the manual "npm run build && cp -r out public_new && mv public
# public_backup_<stamp> && mv public_new public" sequence that was run by hand on
# every change. That sequence left 17 timestamped backup directories (22 MB of
# regenerable build output) and, because the frontend source lived in a separate
# git repository, there was no single command that took source to running app.
#
# Usage:
#   ./deploy.sh              build + publish
#   ./deploy.sh --no-build   publish an existing out/ (skip npm run build)
#   ./deploy.sh --restart    also restart the launcher afterwards
set -euo pipefail

WEB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "$WEB_DIR/../app" && pwd)"
PUBLIC_DIR="$APP_DIR/public"
STAGING_DIR="$APP_DIR/public_new"
# Keep a small rolling window so a bad deploy can be reverted, without letting
# build snapshots accumulate indefinitely.
KEEP_BACKUPS=2

DO_BUILD=1
DO_RESTART=0
for arg in "$@"; do
  case "$arg" in
    --no-build) DO_BUILD=0 ;;
    --restart)  DO_RESTART=1 ;;
    *) echo "unknown flag: $arg" >&2; exit 2 ;;
  esac
done

cd "$WEB_DIR"

if [[ "$DO_BUILD" == "1" ]]; then
  echo "==> building"
  npm run build
fi

if [[ ! -d "$WEB_DIR/out" ]]; then
  echo "no out/ directory — build first (or drop --no-build)" >&2
  exit 1
fi

# index.html is the smoke test that the export actually produced a site.
if [[ ! -f "$WEB_DIR/out/index.html" ]]; then
  echo "out/ has no index.html — refusing to publish a broken build" >&2
  exit 1
fi

echo "==> staging"
rm -rf "$STAGING_DIR"
cp -r "$WEB_DIR/out" "$STAGING_DIR"

STAMP="$(date +%Y%m%d_%H%M%S)"
if [[ -d "$PUBLIC_DIR" ]]; then
  mv "$PUBLIC_DIR" "$APP_DIR/public_backup_$STAMP"
fi
mv "$STAGING_DIR" "$PUBLIC_DIR"
echo "==> published (previous build kept as public_backup_$STAMP)"

# Prune old snapshots — these are pure build output, regenerable from source.
mapfile -t OLD < <(ls -1dt "$APP_DIR"/public_backup_* 2>/dev/null | tail -n +$((KEEP_BACKUPS + 1)))
if [[ ${#OLD[@]} -gt 0 ]]; then
  printf '==> pruning %d old backup(s)\n' "${#OLD[@]}"
  rm -rf "${OLD[@]}"
fi

if [[ "$DO_RESTART" == "1" ]]; then
  echo "==> restarting launcher"
  pkill -f "core/app.py" 2>/dev/null || true
  pkill -f "app/server.mjs" 2>/dev/null || true
  pkill -f "launcher.mjs" 2>/dev/null || true
  sleep 2
  cd "$WEB_DIR/.."
  nohup node app/launcher.mjs > logs/launcher.log 2>&1 &
  disown
  echo "==> launcher restarted (logs/launcher.log)"
fi
