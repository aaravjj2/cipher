#!/usr/bin/env bash
set -euo pipefail

ROOT="/home/aarav/Aarav/cipher"
SYSTEM_ROOT="$ROOT/cipher-system"
INFRA="$ROOT/infra/gcp-cipher-vm"
PYTHON="/home/aarav/.venvs/cipher/bin/python"
UNIT="cipher-cluster-kronos-forward.service"
PIDFILE="/home/aarav/Aarav/cipher-system/CipherCapture/state/cluster_kronos_forward.pid"

if [[ ! -x "$PYTHON" ]]; then
  echo "Missing Cipher virtual-environment Python: $PYTHON" >&2
  exit 1
fi

if [[ ! -f "$SYSTEM_ROOT/core/cluster_kronos_forward.py" ]]; then
  echo "Missing Cluster Kronos watcher module." >&2
  exit 1
fi

if [[ ! -f "$SYSTEM_ROOT/config/cluster_kronos_forward_preregistered.json" ]]; then
  echo "Missing Cluster Kronos preregistration." >&2
  exit 1
fi

"$PYTHON" - <<'PY'
from pathlib import Path
from core.cluster_kronos_forward import load_registration
from core.kronos_research import status

registration = load_registration(Path("config/cluster_kronos_forward_preregistered.json"))
model_status = status()
if not model_status.get("ready_for_inference"):
    raise SystemExit(f"Kronos environment is not ready: {model_status}")
print(f"Validated preregistration {registration['config_id']}")
PY

if [[ -f "$PIDFILE" ]]; then
  old_pid="$(cat "$PIDFILE" 2>/dev/null || true)"
  if [[ -n "$old_pid" ]] && kill -0 "$old_pid" 2>/dev/null; then
    echo "Stopping temporary watcher PID $old_pid..."
    kill "$old_pid"
    for _ in {1..30}; do
      kill -0 "$old_pid" 2>/dev/null || break
      sleep 1
    done
  fi
fi

sudo install -m 0644 \
  "$INFRA/systemd/$UNIT" \
  "/etc/systemd/system/$UNIT"

sudo systemctl daemon-reload
sudo systemctl enable --now "$UNIT"
sleep 3
sudo systemctl --no-pager --full status "$UNIT"
