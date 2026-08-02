#!/usr/bin/env bash
set -euo pipefail

ROOT="/home/aarav/Aarav/cipher"
PYTHON="/home/aarav/.venvs/cipher/bin/python"
CLI="$ROOT/cipher-system/scripts/run_research_platform.py"
LOCK="$ROOT/cipher-system/data/governance/catalog.lock"
LOG_DIR="$ROOT/cipher-system/logs"

mkdir -p "$(dirname "$LOCK")" "$LOG_DIR"
exec 9>"$LOCK"
if ! flock -n 9; then
  printf '{"skipped":true,"reason":"governance catalog already running"}\n'
  exit 0
fi

cd "$ROOT"
"$PYTHON" "$CLI" init
"$PYTHON" "$CLI" import-current-evidence \
  --runtime-root /home/aarav/Aarav/cipher-system/CipherCapture

printf '{"ok":true,"completed_at":"%s","cloud_writes":false,"live_execution":false}\n' \
  "$(date --utc --iso-8601=seconds)"
