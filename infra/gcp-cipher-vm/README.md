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
