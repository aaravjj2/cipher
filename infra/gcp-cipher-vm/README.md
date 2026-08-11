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

## What is backed up, and by which of the two mechanisms

Backup is split in two, and reading only one of them gives a false picture of coverage.

**`cipher-backup.timer` (03:15 ET)** — `backup-to-gcs.py`. Handles *hot, small, mutable*
state: the five SQLite databases via the SQLite backup API, plus a zstd tar of
`gex_snapshots`, `tradier_stream_events`, `accessobsidian_scans`, `backtest_results`,
`flash_agentic`, `forward_tests`, `governance/artifacts`, `research_snapshots`,
`warehouse_exports`, and `reports`. Every object is uploaded with a sha256 in its metadata
and its size verified remotely.

**`cipher-chain-archive.timer` (06:15 ET)** — `archive_live_option_chains.py`. Handles the
*cold, huge, append-only* corpus: `data/live_option_chains/` under the `cold/` prefix.
Files are compressed, uploaded, verified, ledgered in
`data/live_option_chains_archive.sqlite`, and only then deleted locally. `--keep-dates 2`
leaves the two newest days hot.

**`cipher-parquet-retention.timer` (05:15 ET)** — a mirror, not a backup. It builds verified
Parquet copies of completed `tradier_stream_events` days and never prunes the source.

Deliberately **not** backed up:

- **`data/historical_options` (9.8 GB)** — excluded because it is reproducible. It was built
  by `core/historical_options_download.py` from Alpaca and can be rebuilt from the
  `download_manifest.json` in each dataset directory. Rebuilding costs API quota and hours,
  not data.
- **`web/node_modules`, `.venv*`, `app/public`, `web/out`** — build artifacts.

The distinction that matters: the live chains are *captured observations* that no vendor
will sell back, so they are the one corpus where a missing backup is permanent loss. The
2026-08-01 hand-run of the archiver stalled and left 43.5 GiB unarchived until the timer
above existed. The archiver working is not the same as the archiver running.

Verify recoverability rather than trusting the ledger — download an archived object,
decompress it, and hash the result against the receipt's `source_sha256`:

```bash
sqlite3 data/live_option_chains_archive.sqlite \
  'select object_uri, source_sha256, source_size_bytes from archive_receipts limit 1'
gcloud storage cp <object_uri> /tmp/o.zst && zstd -d -f -o /tmp/o.jsonl /tmp/o.zst
sha256sum /tmp/o.jsonl   # must equal source_sha256
```

## Bucket lifecycle — required, and blocked on IAM

`gs://project-eec91607-77a2-4be6-837-cipher-runtime` held **378.90 GiB** with one full daily
copy per day and nothing pruned. The VM service account
`cipher-vm-runtime@…` lacks `storage.buckets.get`, so this cannot be read or set from the
VM — `gcloud storage buckets describe` is denied. Run the following from an account holding
`roles/storage.admin`:

```bash
cat > /tmp/lifecycle.json <<'JSON'
{"rule": [
  {"action": {"type": "Delete"},
   "condition": {"age": 30, "matchesPrefix": ["backups/"]}},
  {"action": {"type": "SetStorageClass", "storageClass": "NEARLINE"},
   "condition": {"age": 30, "matchesPrefix": ["cold/"]}}
]}
JSON
gcloud storage buckets update gs://project-eec91607-77a2-4be6-837-cipher-runtime \
  --lifecycle-file=/tmp/lifecycle.json
```

**The prefixes are not interchangeable.** `backups/` is a daily full copy where only recent
generations matter, so deleting at 30 days is the point. `cold/` is the only copy of the
live option chains — it must never carry a Delete rule. Transitioning it to NEARLINE cuts
storage cost while keeping the data, which is why the two prefixes get different actions.

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
