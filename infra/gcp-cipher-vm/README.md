# Cipher Always-On Google Cloud VM

This deployment replaces WSL2 as the machine that runs Cipher continuously.
The VM is private by default: SSH is permitted only through Google IAP. Cipher's
preferred external path is an outbound-only Cloudflare Tunnel protected by
Cloudflare Access; no inbound web port is opened on the VM.

## Defaults

- Project: current `gcloud` project
- VM: `cipher-main`
- Zone: `us-central1-a`
- Machine: `e2-standard-2`
- Disk: 100 GB balanced persistent disk
- Network: dedicated `cipher-vpc`
- Runtime identity: `cipher-vm-runtime`
- Backup bucket: `<project>-cipher-runtime`

## Deployment sequence

```bash
bash infra/gcp-cipher-vm/deploy.sh
bash infra/gcp-cipher-vm/sync-runtime.sh
gcloud compute ssh aarav@cipher-main \
  --zone us-central1-a --tunnel-through-iap \
  --command '/home/aarav/Aarav/cipher/infra/gcp-cipher-vm/configure-vm.sh'
```

Then configure the Cloudflare account, hostname, Access application, and tunnel
token as described in [`CLOUDFLARE.md`](CLOUDFLARE.md). Re-run configuration to
materialize the token and start the connector:

```bash
gcloud compute ssh aarav@cipher-main --zone us-central1-a --tunnel-through-iap \
  --command '/home/aarav/Aarav/cipher/infra/gcp-cipher-vm/configure-vm.sh'
```

The connector remains disabled when the token secret is absent. Cipher must not
be published with a Cloudflare Quick Tunnel or without an Access policy.

## Automatically managed services

- `cipher-core.service`
- `cipher-web.service`
- `cipher-devspace.service`
- `cipher-tradier.service`
- `cipher-gex.service`
- `cipher-backup.timer`
- `cipher-governance-catalog.timer`
- `cipher-cloudflared.service` (conditional on a tunnel-token secret)

The Tradier and GEX loops sleep outside their configured market-hour windows.
The governance catalog runs after each weekday market session and registers
manifests, frozen evidence, and current forward-test state. It performs no cloud
writes and has no broker or live-order capability.
The scanner browser is not enabled automatically because AccessObsidian requires
a fresh authenticated browser session and its local WebBridge/extension.

## Public access over Tailscale Funnel

Cipher is published at `https://cipher-main.tail39504f.ts.net:8443`, reachable from any
browser with no Tailscale client installed. Funnel permits only ports 443, 8443, and
10000; 443 already proxies to `127.0.0.1:7676` for `cipher-devspace`, so Cipher uses
8443 and the two do not interact.

```bash
tailscale funnel --bg --https=8443 http://127.0.0.1:8283   # publish
tailscale funnel --https=8443 off                          # unpublish
tailscale serve status --json                              # confirm AllowFunnel
```

The only thing between that URL and live market data is the password gate in
`cipher-system/app/auth.mjs`, so `CIPHER_APP_PASSWORD_HASH` must stay configured.
`cipher-web.service` refuses to start when auth is enabled without a hash, which fails
closed rather than serving a public unauthenticated app.

Rotate the password with the hash generated off stdin, never as an argument:

```bash
printf '%s' 'NEW PASSWORD' | node cipher-system/scripts/set-app-password.mjs
```

**The hash is currently stored only in `/etc/cipher/cipher.env`.** The VM service account
`cipher-vm-runtime@…` lacks `secretmanager.secrets.create`, so `cipher-app-password-hash`
could not be created in Secret Manager. Two consequences:

- `sync-secrets.py` carries the configured hash forward when the secret is unavailable,
  so a secrets sync no longer disables the login gate. Do not remove that fallback.
- The hash is **not** in any backup — `backup-to-gcs.py` copies SQLite databases only. If
  the VM or that file is lost, set a new password rather than trying to recover the old.

Grant the service account `roles/secretmanager.admin` (or create the secret from a
privileged account) to make the hash durable, then re-run `cipher-secrets.service`.

## Operational commands

```bash
gcloud compute ssh aarav@cipher-main --zone us-central1-a --tunnel-through-iap
sudo systemctl status cipher-core cipher-web cipher-devspace cipher-tradier cipher-gex
sudo systemctl status cipher-governance-catalog.timer
sudo systemctl status cipher-cloudflared.service
sudo systemctl start cipher-governance-catalog.service
sudo journalctl -u cipher-core -n 100 --no-pager
sudo journalctl -u cipher-tradier -n 100 --no-pager
sudo systemctl start cipher-backup.service
```

## What the migration excludes

The operational snapshot intentionally excludes the 32 GB duplicate `Stock
data` tree, `.git` object database, archived `cipher-system/previous-work`, local
virtual environments, Node modules, historical option bulk files, and TimesFM
model weights. These are not required for the always-on runtime and can be
copied separately later.
