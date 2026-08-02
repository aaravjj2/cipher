#!/usr/bin/env bash
set -euo pipefail

exec > >(tee -a /var/log/cipher-bootstrap.log) 2>&1
export DEBIAN_FRONTEND=noninteractive

if [[ -f /var/lib/cipher/bootstrap-complete ]]; then
  echo "Cipher VM bootstrap already completed."
  exit 0
fi

apt-get update
apt-get install -y \
  ca-certificates curl gnupg git jq rsync sqlite3 tmux unzip zip zstd \
  build-essential python3 python3-dev python3-pip python3-venv \
  chromium xvfb xauth fonts-liberation dbus-x11 procps lsof

if ! id aarav >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash aarav
fi
usermod -aG sudo aarav
printf 'aarav ALL=(ALL) NOPASSWD:ALL\n' >/etc/sudoers.d/90-aarav
chmod 0440 /etc/sudoers.d/90-aarav

if ! command -v node >/dev/null 2>&1 || [[ "$(node -p 'process.versions.node.split(`.`)[0]' 2>/dev/null || echo 0)" -lt 22 ]]; then
  curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
  apt-get install -y nodejs
fi
npm install -g @waishnav/devspace@1.0.4

if ! command -v tailscale >/dev/null 2>&1; then
  curl -fsSL https://tailscale.com/install.sh | sh
fi
systemctl enable --now tailscaled

install -d -o aarav -g aarav \
  /home/aarav/Aarav \
  /home/aarav/.venvs \
  /home/aarav/.devspace \
  /home/aarav/.local/share/devspace \
  /var/lib/cipher \
  /var/log/cipher \
  /etc/cipher

if [[ ! -x /home/aarav/.venvs/cipher/bin/python ]]; then
  sudo -u aarav python3 -m venv /home/aarav/.venvs/cipher
fi
sudo -u aarav /home/aarav/.venvs/cipher/bin/pip install --upgrade pip wheel
sudo -u aarav /home/aarav/.venvs/cipher/bin/pip install \
  numpy scipy pytest google-cloud-storage google-cloud-secret-manager

mkdir -p /var/lib/cipher
printf 'completed_at=%s\n' "$(date -u +%FT%TZ)" >/var/lib/cipher/bootstrap-complete

echo "Cipher VM bootstrap complete."
