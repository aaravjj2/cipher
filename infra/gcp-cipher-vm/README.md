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

### Tradier stream subscription is now at its cap

`/etc/cipher/cipher.env` sets `TRADIER_OPTION_UNDERLYINGS` explicitly. Before
2026-08-11 it was unset, so `core/tradier_stream_capture.py` fell back to
`DEFAULT_UNDERLYINGS` — fourteen mega-caps, none of which the leveraged-ETF wheel
trades. `data/execution_costs/spread_profile.json` therefore had no measured spread
for NVDL, TSLL, SOXL, or TQQQ, and `equity_half_spread_bps` returned
`assumed:symbol-not-captured` for the entire wheel universe. Those four are now
appended so a measured spread accrues for the symbols the strategy actually trades.
See the corresponding section in `docs/backtest-findings.md`.

The capacity arithmetic matters before anything else is added:

| | base symbols | option slots | contracts taken | total |
|---|---|---|---|---|
| before (14 underlyings) | 23 | 137 | 112 | **135** of 160 |
| after (18 underlyings) | 27 | 133 | 133 | **160** of 160 |

`TRADIER_MAX_STREAM_SYMBOLS` defaults to 160, so the subscription is now exactly at
the cap with no headroom. Nothing is dropped: `_round_robin_contracts` allocates by
depth across underlyings, so the cost is that seven of the existing underlyings hold
seven contracts instead of eight, and every underlying is still captured. Adding a
nineteenth underlying will start reducing depth further, so raise
`TRADIER_MAX_STREAM_SYMBOLS` at the same time — and check the vendor's per-session
symbol limit before doing so, which is not documented here because it has not been
established.

`TRADIER_OPTION_UNDERLYINGS` is not in `MANAGED_NAMES`, so `sync-secrets.py` carries
it forward untouched rather than rewriting it.

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

- **`data/historical_options` (9.8 GB)** — excluded because it is reproducible from Alpaca via
  `core/historical_options_download.py`. Rebuilding costs API quota and hours, not data.

  **The recipe is not in the manifest.** `download_manifest.json` carries
  `latest_run_config`, which is the *last* run only — for `leveraged_etf_wheel` that is a
  single day for a single underlying, out of the 205 runs that built it. The full recipe is
  `download_runs.config_json` inside each dataset's own `historical_options.sqlite`, i.e.
  inside the directory this backup skips, so losing the directory would lose the data *and*
  the instructions for rebuilding it.

  `scripts/export_options_rebuild_recipes.py` closes that gap: it exports every recorded run
  config to `data/options_rebuild_recipes/`, which **is** in `TAR_INCLUDES`. 772 KB covering
  678 runs across 28 datasets. `backup-to-gcs.py` refreshes it before building the tar, and
  treats a failure there as a warning rather than an error — a stale recipe weakens a
  recovery path for reproducible data, while a failed backup loses irreplaceable data, and
  those are not the same severity.
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

## The login password exists in exactly one place

`CIPHER_APP_PASSWORD_HASH` is listed in `sync-secrets.py`'s `SECRETS` map against the Secret
Manager name `cipher-app-password-hash`, which reads as though a rebuild would restore it. It
would not. The VM service account cannot read any secret:

```
$ gcloud secrets versions access latest --secret=cipher-app-password-hash
ERROR: PERMISSION_DENIED: Permission 'secretmanager.versions.access' denied on resource
(or it may not exist).  … authenticated as cipher-vm-runtime@…
```

The denial is indistinguishable from the secret not existing, so treat the hash as living
**only** in `/etc/cipher/cipher.env` on this VM. Nothing else has a copy.

What this does and does not mean:

- **It is not an exposure — but it was.** `createAuthGate` defaults `enabled` to true unless
  `CIPHER_APP_AUTH` is explicitly `off`, and `app/server.mjs` throws on startup when auth is
  enabled with no hash configured, so a VM rebuilt without the env file now refuses to serve
  rather than serving unauthenticated. Until 2026-08-11 `sync-secrets.py` wrote
  `CIPHER_APP_AUTH=off` whenever no hash resolved, and `off` is the one value that disables
  the gate: the startup guard never fired and `isAuthenticated()` returned true for every
  request. That turned this exact rebuild case into a wide-open server on a published port.
  It now writes `on` unconditionally, which is what its comment had always claimed.
- **It is an availability risk.** That same rebuild leaves `cipher-web.service` unable to
  start, and there is no restore path from Secret Manager.
- `sync-secrets.py` degrades correctly in the meantime: on a Secret Manager failure it keeps
  the existing values rather than writing blanks, so a sync run on the current IAM does not
  destroy the hash.

Recovery needs no cloud access, because the password is chosen rather than recovered:

```bash
printf '%s' "$NEW_PASSWORD" | node cipher-system/scripts/set-app-password.mjs
# then put the printed hash in /etc/cipher/cipher.env as
#   CIPHER_APP_PASSWORD_HASH=<hash>
# and: sudo systemctl restart cipher-web.service
```

Rotating the hash invalidates every existing session, because `sessionSecretFor` derives the
signing secret from the hash itself.

To make a rebuild self-healing, a privileged account has to create the secret and grant the
VM read access — the same account that runs the bucket-lifecycle command above:

```bash
printf '%s' "$PASSWORD" | node cipher-system/scripts/set-app-password.mjs \
  | gcloud secrets create cipher-app-password-hash --data-file=-
gcloud secrets add-iam-policy-binding cipher-app-password-hash \
  --member=serviceAccount:cipher-vm-runtime@project-eec91607-77a2-4be6-837.iam.gserviceaccount.com \
  --role=roles/secretmanager.secretAccessor
```

Pipe the password rather than passing it as an argument: an argv password is visible in `ps`
and lands in shell history, which is why `set-app-password.mjs` reads stdin only.

## Accepted weaknesses on the public URL

Reviewed 2026-08-11 after publishing. These are decisions, not oversights.

**Login rate limiting is in-memory and resets on restart.** `app/auth.mjs` tracks failed
attempts per client with a doubling lockout, but the map lives in the process. Persisting it
was considered and rejected: the password is 23 characters from a 31-character alphabet, about
99 bits, so online guessing is not the threat model — no feasible number of attempts finds
it. The limiter's real job is capping the scrypt CPU an unauthenticated caller can spend
(~60 ms per attempt) and keeping the journal readable, and it does both within a process
lifetime. Restarts are rare and reset nothing an attacker could have made progress on.
If the password is ever shortened, this reasoning stops holding and the limiter must
become persistent.

> This entry said "per IP" and, until 2026-08-11, that was not true in this deployment.
> The key was `req.socket.remoteAddress`, and the Funnel proxies to `127.0.0.1:8283`, so every
> internet client shared one bucket. The consequence was not weaker guessing protection — that
> was never the point — but an **availability** hole the entry had not considered: five wrong
> guesses from anyone locked the real user out, renewable indefinitely for up to 15 minutes at
> a time. The key is now the last `X-Forwarded-For` hop, which Tailscale sets and overwrites.
> `X-Real-IP` is not consulted: a probe through the Funnel showed a spoofed value passing
> through verbatim, so keying on it would let one client mint unlimited buckets.

**`/accessobsidian-browser-logger.js` is served ungated with `access-control-allow-origin: *`.**
Necessary: it is injected into a third-party page, so it cannot sit behind the session
cookie. Audited for disclosure — it contains one host reference,
`http://127.0.0.1:8787/api/scanner-ingest`, which is loopback and not reachable from
outside. No credentials, tailnet hostname, bucket, or project identifier. It stays public.

**`/api/health` answers anonymously with `{"status":"ok"}` and nothing else.** The core's
full health (service name, `market_data_configured`, both feed names) is served only to an
authenticated session, because `verify-cloudflare-access.py` correctly treats
`market_data_configured` in an unauthenticated body as disclosure.

**Served-build drift is possible, but now detectable.** `npm run build` writes `web/out`;
only `scripts/sync_web_build.sh` copies it into `app/public`, which is what the server reads.
Building without syncing leaves a newer bundle unserved, and because the symptom looks like
a browser cache problem it is expensive to diagnose. Run the sync script rather than
`npm run build` directly, and to answer "is the served build current?" without a browser:

```bash
scripts/sync_web_build.sh --check   # exits 0 in sync, 1 on drift and lists the paths
```

The check is an `rsync -ain --delete` dry-run with the same flags as the real publish, so it
cannot disagree with the copy that would actually happen. Directory entries differing only in
mtime are ignored, since a directory's timestamp moves whenever anything inside it is written.
It is worth running before verifying any UI change in a browser — confirming a change against
a stale bundle costs far more than the check.

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
