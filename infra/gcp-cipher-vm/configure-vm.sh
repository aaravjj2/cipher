#!/usr/bin/env bash
# ── Cipher VM configuration ─────────────────────────────────────────────
# Runs ON the VM after the runtime archive has been extracted.
# Idempotent: safe to re-run.
# ─────────────────────────────────────────────────────────────────────────
set -euo pipefail

ROOT="/home/aarav/Aarav/cipher"
INFRA="$ROOT/infra/gcp-cipher-vm"
PROJECT_ID="project-eec91607-77a2-4be6-837"
BUCKET="${PROJECT_ID}-cipher-runtime"
HERMES_COMMIT="${HERMES_COMMIT:-eb52760564dbba2e5971fa54bd67384e281cd3b8}"

# ── 0. Pre-flight ───────────────────────────────────────────────────────
if [[ ! -d "$ROOT/cipher-system" ]]; then
  echo "ERROR: Cipher runtime has not been extracted to $ROOT" >&2
  exit 1
fi

echo "▶ Installing Cipher operational scripts..."

if ! command -v cloudflared >/dev/null 2>&1; then
  echo "▶ Installing Cloudflare Tunnel connector..."
  curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg -o /tmp/cloudflare-main.gpg
  sudo install -d -m 0755 /usr/share/keyrings
  sudo install -m 0644 /tmp/cloudflare-main.gpg /usr/share/keyrings/cloudflare-main.gpg
  echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main' \
    | sudo tee /etc/apt/sources.list.d/cloudflared.list >/dev/null
  sudo apt-get update -qq
  sudo apt-get install -y cloudflared
fi

# Stop the managed collector and terminate the obsolete detached wrapper that
# previously competed for the same stream-capture lock. Exact command patterns
# avoid touching unrelated Python or shell processes.
sudo systemctl stop cipher-tradier.service 2>/dev/null || true
pkill -TERM -f '^bash /home/aarav/Aarav/cipher/infra/gcp-cipher-vm/bin/run-tradier-loop\.sh$' 2>/dev/null || true
pkill -TERM -f '^/bin/bash -lic set \+m; cd /home/aarav/Aarav/cipher && /home/aarav/Aarav/cipher/infra/gcp-cipher-vm/bin/run-tradier-loop\.sh$' 2>/dev/null || true
sleep 2

# ── 1. Install helper scripts ───────────────────────────────────────────
sudo install -d -m 0755 /usr/local/lib/cipher /etc/systemd/system /etc/cipher
sudo install -m 0755 "$INFRA/bin/sync-secrets.py"      /usr/local/lib/cipher/sync-secrets.py
sudo install -m 0755 "$INFRA/bin/backup-to-gcs.py"     /usr/local/lib/cipher/backup-to-gcs.py
sudo install -m 0755 "$INFRA/bin/run-tradier-loop.sh"  /usr/local/lib/cipher/run-tradier-loop.sh
sudo install -m 0755 "$INFRA/bin/run-gex-loop.sh"      /usr/local/lib/cipher/run-gex-loop.sh
sudo install -m 0755 "$INFRA/bin/run-governance-catalog.sh" /usr/local/lib/cipher/run-governance-catalog.sh

# ── 2. Install systemd units ────────────────────────────────────────────
sudo install -m 0644 "$INFRA"/systemd/*.service /etc/systemd/system/
sudo install -m 0644 "$INFRA"/systemd/*.timer   /etc/systemd/system/

# ── 3. Install Python dependencies into the venv ────────────────────────
echo "▶ Installing Python dependencies..."
if [[ -f "$ROOT/requirements.txt" ]]; then
  sudo -u aarav /home/aarav/.venvs/cipher/bin/pip install -q -r "$ROOT/requirements.txt"
fi
# Ensure cloud libs are present for sync-secrets.py and backup-to-gcs.py
sudo -u aarav /home/aarav/.venvs/cipher/bin/pip install -q \
  google-cloud-storage google-cloud-secret-manager

# ── 4. DevSpace configuration ───────────────────────────────────────────
echo "▶ Configuring DevSpace..."
install -d -m 0700 -o aarav -g aarav /home/aarav/.devspace /home/aarav/.local/share/devspace

# Download DevSpace state from GCS if available and not yet configured
if [[ ! -f /home/aarav/.devspace/config.json ]] && \
   gsutil -q stat "gs://${BUCKET}/staging/devspace-state.tar.zst" 2>/dev/null; then
  echo "  Downloading DevSpace state from GCS..."
  gsutil cp "gs://${BUCKET}/staging/devspace-state.tar.zst" /tmp/devspace-state.tar.zst
  tar --zstd -xf /tmp/devspace-state.tar.zst -C /home/aarav 2>/dev/null || true
  rm -f /tmp/devspace-state.tar.zst
fi

# Patch DevSpace config for VM loopback binding
python3 -c "
import json, pathlib
p = pathlib.Path('/home/aarav/.devspace/config.json')
d = json.loads(p.read_text()) if p.exists() else {}
d.update({'host': '127.0.0.1', 'port': 7676, 'allowedRoots': ['/home/aarav/Aarav']})
p.write_text(json.dumps(d, indent=2) + '\n')
p.chmod(0o600)
" 2>/dev/null || true
chmod 600 /home/aarav/.devspace/auth.json 2>/dev/null || true
chown -R aarav:aarav /home/aarav/.devspace /home/aarav/.local/share/devspace

# ── 5. Hermes installation ──────────────────────────────────────────────
HERMES_REAL="/home/aarav/.hermes/hermes-agent/venv/bin/hermes"
HERMES_LINK="/home/aarav/.local/bin/hermes"
HERMES_IMPORT_MARKER="/home/aarav/.hermes/.cipher-quick-backup-imported"

# Validate the real CLI entry point, not merely the convenience launcher.  A
# previous migration left a launcher that invoked run_agent.py directly and
# therefore hid all Hermes management commands.
if [[ ! -x "$HERMES_REAL" ]] || ! sudo -u aarav "$HERMES_REAL" --version >/dev/null 2>&1; then
  echo "▶ Installing Hermes (pinned to ${HERMES_COMMIT:0:12})..."
  sudo -u aarav bash -lc \
    "curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash -s -- --non-interactive --skip-setup --skip-browser --commit $HERMES_COMMIT"
fi

if [[ ! -x "$HERMES_REAL" ]]; then
  echo "ERROR: Hermes CLI entry point was not installed at $HERMES_REAL" >&2
  exit 1
fi

# Always repair the user-facing launcher to the actual Hermes CLI.
install -d -m 0755 -o aarav -g aarav /home/aarav/.local/bin
ln -sfn "$HERMES_REAL" "$HERMES_LINK"
chown -h aarav:aarav "$HERMES_LINK"

# Import a Hermes-native quick backup exactly once.  Do not key this decision
# on ~/.hermes existing because the installer itself creates that directory.
if [[ ! -f "$HERMES_IMPORT_MARKER" ]] && \
   gsutil -q stat "gs://${BUCKET}/staging/hermes-quick.zip" 2>/dev/null; then
  echo "  Downloading Hermes quick backup from GCS..."
  gsutil cp "gs://${BUCKET}/staging/hermes-quick.zip" /tmp/hermes-quick.zip
  sudo -u aarav "$HERMES_REAL" import --force /tmp/hermes-quick.zip
  rm -f /tmp/hermes-quick.zip
  install -m 0600 -o aarav -g aarav /dev/null "$HERMES_IMPORT_MARKER"
fi

# ── 6. Fix ownership ────────────────────────────────────────────────────
echo "▶ Setting ownership..."
chown -R aarav:aarav /home/aarav/Aarav/cipher

# ── 7. Make scripts executable ──────────────────────────────────────────
chmod +x "$INFRA"/*.sh "$INFRA"/bin/* 2>/dev/null || true

# ── 8. Enable and start services ────────────────────────────────────────
echo "▶ Enabling and starting Cipher services..."
sudo systemctl daemon-reload
sudo systemctl enable \
  cipher-secrets.service \
  cipher-core.service \
  cipher-web.service \
  cipher-devspace.service \
  cipher-tradier.service \
  cipher-gex.service \
  cipher-backup.timer \
  cipher-governance-catalog.timer

sudo systemctl restart cipher-secrets.service
sleep 2

# Start application services
for svc in cipher-core cipher-web cipher-devspace cipher-tradier cipher-gex; do
  sudo systemctl restart "$svc" || echo "WARN: $svc failed to start" >&2
done

# External routing is last. A tunnel token alone is insufficient: the fallback
# password gate must be configured and the newly restarted web process must be
# healthy before cloudflared is allowed to connect.
if [[ -s /etc/cipher/cloudflare-tunnel.token ]] \
   && sudo grep -q '^CIPHER_APP_PASSWORD_HASH=' /etc/cipher/cipher.env \
   && systemctl is-active --quiet cipher-web.service; then
  sudo systemctl enable --now cipher-cloudflared.service
else
  sudo systemctl disable --now cipher-cloudflared.service 2>/dev/null || true
  echo "Cloudflare connector disabled: tunnel token, password gate, or web service is not ready."
fi

sudo systemctl start cipher-backup.timer
sudo systemctl start cipher-governance-catalog.timer

echo ""
echo "══════════════════════════════════════════════════════════════"
echo " Cipher VM configuration complete"
echo "══════════════════════════════════════════════════════════════"
echo ""
systemctl --no-pager --plain --type=service --state=running | grep -E 'cipher-' || true
echo ""
echo "Cloudflare is the preferred external-access path; see infra/gcp-cipher-vm/CLOUDFLARE.md."
