# Cipher Always-On GCP Migration — Coding Agent Handoff

You are the primary infrastructure and coding agent for completing the migration of Cipher from a Windows WSL2-dependent environment to an always-on Google Cloud VM.

## Primary outcome

Make the Google Cloud VM the machine that continuously runs Cipher and its data collectors so the user's Windows computer and WSL2 can be shut down without stopping the system.

Do the work directly. Do not merely propose architecture. Inspect, repair, deploy, test, and document it. Ask the user only when a step genuinely requires personal interactive authentication, such as Tailscale approval, AccessObsidian/browser login, or an inference-provider login.

## Workspace and rules

- Local workspace: `/home/aarav/Aarav/cipher`
- Active application: `cipher-system/`
- Read and obey `/home/aarav/Aarav/cipher/AGENTS.md` before making changes.
- Work in the actual checkout, not a new worktree, because the current cloud migration state and untracked infrastructure files are in this checkout.
- The checkout is very dirty and has no Git remote. Do not reset, clean, stash, delete, or overwrite unrelated work.
- Limit repository changes to `infra/gcp-cipher-vm/` and migration documentation unless a narrowly necessary active-runtime fix is found.
- Never print, log, copy into source control, or expose API tokens, OAuth credentials, `.env` contents, browser cookies, DevSpace auth secrets, or Hermes provider secrets.
- Cipher must remain read-only. Do not add or invoke order-placement, account-trading, preview, or broker execution endpoints.
- Do not migrate or expose proprietary application internals. The AccessObsidian collector may capture only visible UI output, consistent with the existing code.
- Preserve all current local data until the VM has been validated and backed up.

## Current authenticated cloud context

- GCP project: `project-eec91607-77a2-4be6-837`
- Region: `us-central1`
- Zone: `us-central1-a`
- Billing: enabled
- Active gcloud account is already authenticated locally.

## Cloud resources already created

The following resources already exist. Inspect and reuse them; do not create duplicates.

- VM: `cipher-main`
  - Status: RUNNING
  - Zone: `us-central1-a`
  - Machine: `e2-standard-2`
  - Boot disk: 100 GB balanced persistent disk
  - Internal IP: `10.42.0.2`
  - External IP currently exists, but no general public ingress rule was created
- VPC: `cipher-vpc`
- Subnet: `cipher-subnet` (`10.42.0.0/24`)
- Firewall: `cipher-allow-iap-ssh`, allowing TCP 22 only from Google IAP range
- VM runtime service account: `cipher-vm-runtime@project-eec91607-77a2-4be6-837.iam.gserviceaccount.com`
- Runtime backup bucket: `gs://project-eec91607-77a2-4be6-837-cipher-runtime`
- Existing Tradier raw bucket: `gs://project-eec91607-77a2-4be6-837-cipher-tradier-stream`
- Existing Cloud Run service: `cipher-tradier-stream`
- Existing Cloud Scheduler job: `cipher-tradier-stream-hourly`, currently PAUSED
- Secret Manager entries were created/synchronized for available Alpaca/LSE credentials, and the existing `tradier-access-token` secret is available.

The VM startup bootstrap completed successfully. The marker exists at:

```text
/var/lib/cipher/bootstrap-complete
```

The VM currently has Debian 12, Python 3.11, a Cipher Python venv, Node 22, Chromium, Xvfb, DevSpace 1.0.4, Tailscale, gcloud-related runtime support, and common build utilities.

## Current migration state — resume here

Infrastructure files were generated locally under:

```text
infra/gcp-cipher-vm/
```

They include:

- `deploy.sh`
- `startup.sh`
- `sync-runtime.sh`
- `configure-vm.sh`
- `enable-tailscale.sh`
- `bin/sync-secrets.py`
- `bin/backup-to-gcs.py`
- `bin/run-tradier-loop.sh`
- `bin/run-gex-loop.sh`
- systemd units for Cipher core, web, DevSpace, Tradier, GEX, secrets, and backup

Treat these as an unfinished draft. Audit and repair them before relying on them.

A migration transfer was attempted over IAP. Only this artifact is confirmed on the VM:

```text
/tmp/cipher-runtime.tar.zst    approximately 717 MB
```

The following were not confirmed on the VM and must be recreated/transferred safely:

- migration manifest
- DevSpace state archive
- Hermes quick backup
- Hermes workspace archive, if actually needed

No Cipher repository tree has yet been confirmed under `/home/aarav/Aarav/cipher` on the VM. No Cipher systemd services have been confirmed installed or active.

## Existing local components to preserve

### Active Cipher app

- `cipher-system/core/app.py`
- `cipher-system/app/server.mjs`
- `cipher-system/app/public/`
- Alpaca is the active application market-data provider.
- Core port: 8282
- Web port: 8283
- Both currently bind to localhost by design.

### Local Tradier collector

- `cipher-system/core/tradier_stream_capture.py`
- Current local database: `cipher-system/data/tradier_stream.sqlite`
- Current local raw data: `cipher-system/data/tradier_stream_events/`
- It is read-only and includes a local file lock.

### GEX collector

- `cipher-system/core/gex_capture.py`
- Current database: `cipher-system/data/gex_history.sqlite`
- Raw snapshots: `cipher-system/data/gex_snapshots/`

### Scanner capture

- `cipher-system/scripts/capture_accessobsidian_scans.py`
- Current data: `cipher-system/data/accessobsidian_scans/`
- It depends on an authenticated browser plus a local WebBridge at `127.0.0.1:10086`.
- The local WebBridge appears to be provided through an OpenClaw gateway process. Inspect the actual installed OpenClaw package, config, and startup mechanism before attempting to reproduce it.
- Do not copy browser cookies or authenticated Chromium profiles automatically. Set up a fresh VM browser profile and surface a one-time remote login step.

### DevSpace

Local DevSpace state currently lives in:

- `~/.devspace/config.json`
- `~/.devspace/auth.json`
- `~/.local/share/devspace/`

Current local public URL points to the WSL machine's Tailscale hostname and cannot be reused on the VM. Preserve the owner/auth state only if it is safe and compatible; otherwise initialize a new DevSpace owner credential on the VM and report the reconnection steps.

### Hermes

- Current version: Hermes Agent v0.19.0
- Current source commit: `eb52760564dbba2e5971fa54bd67384e281cd3b8`
- Current install is large; do not copy the entire `~/.hermes/hermes-agent` tree.
- Install Hermes cleanly on the VM, preferably pinned initially to the current commit for parity.
- Generate a fresh `hermes backup --quick` locally and transfer/import it securely.
- Provider OAuth or other machine-bound credentials may require one-time reauthentication. Surface those exact steps instead of weakening security.

## Required execution plan

### 1. Audit the generated migration scripts

Inspect all files in `infra/gcp-cipher-vm/`.

Repair at least:

- idempotency
- quoting and path handling
- service dependency ordering
- systemd user/permissions
- secret rendering and file permissions
- runtime environment variables
- safe restarts
- incomplete-transfer recovery
- GCS backup behavior
- service logs and health checks

Run shell syntax checks, Python compilation, and relevant tests before deployment.

### 2. Verify the transferred runtime archive

- Identify the corresponding local archive or recreate it deterministically.
- Compute SHA-256 hashes locally and on the VM.
- Do not extract an archive whose integrity cannot be verified.
- Inspect its file list before extraction for path traversal, unexpected absolute paths, secrets, giant archives, and irrelevant caches.
- Extract under `/home/aarav/Aarav/cipher` with correct ownership.
- Preserve the current local dirty source and active data.

If the previous archive cannot be proven complete, recreate and transfer it. Prefer a resumable GCS staging object or another resumable method over a fragile single IAP SCP transfer. Remove temporary cloud staging objects only after validation.

### 3. Transfer portable runtime state

Transfer only what is needed:

- active Cipher source and its selected current data
- active SQLite databases and raw scanner/Tradier/GEX captures
- Cipher environment-variable values through Secret Manager, not plaintext archives
- DevSpace configuration/state, with secrets protected
- Hermes quick backup
- necessary Hermes workspace source only if it is part of the required runtime

Do not transfer:

- the 5.9 GB local `.git` object database unless explicitly justified
- `Stock data/` duplicate tree
- archived `cipher-system/previous-work/AAOI_intraday_lab`
- multi-gigabyte model caches unless a running service actually requires them
- Python/Node virtual environments
- browser cookies or existing authenticated browser profiles
- temporary caches and generated bytecode

Produce a migration manifest listing included paths, exclusions, sizes, timestamps, and hashes.

### 4. Configure and start the VM runtime

Install and enable systemd services for:

- `cipher-secrets.service`
- `cipher-core.service`
- `cipher-web.service`
- `cipher-devspace.service`
- `cipher-tradier.service`
- `cipher-gex.service`
- `cipher-backup.timer`

Requirements:

- Services run as user `aarav`, not root, unless a root-only action is unavoidable.
- Secrets are rendered to a root-controlled or tightly permissioned runtime file and never enter logs.
- Cipher core and web stay bound to loopback.
- Tradier stream runs as a persistent/reconnecting worker with one active session and no concurrent duplicate collector.
- Do not enable the old Cloud Scheduler stream job while the VM collector is active.
- GEX cadence must be rate-conscious and avoid Alpaca 429 storms. Start with a small explicit ticker set or a conservative schedule; do not blindly run the entire universe continuously.
- Use automatic restart with sane backoff and start limits.
- Logs must be available through `journalctl`.

### 5. Install and configure Hermes cleanly

- Install Hermes on the VM at the pinned parity commit first.
- Import the quick backup.
- Run `hermes doctor`, `hermes status`, and security checks that do not expose credentials.
- Do not start a public general-purpose shell or unauthenticated webhook.
- Document any provider login still required.

### 6. Configure private access

Initial guaranteed access must remain Google IAP SSH.

Install/configure Tailscale on the VM but do not weaken ACLs or expose services publicly. A one-time user authorization will likely be required.

After Tailscale approval:

- give the VM a stable hostname such as `cipher-main`
- enable Tailscale SSH only if appropriate under the user's tailnet policy
- expose Cipher web and DevSpace through authenticated Tailscale Serve/Funnel only after reviewing the security implications
- DevSpace must retain its own OAuth/owner approval protection
- never expose ports 8282, 8283, 7676, database ports, or SSH to unrestricted public ingress

If Funnel is necessary for ChatGPT MCP connectivity, expose only DevSpace's loopback HTTP endpoint through HTTPS and keep DevSpace OAuth enabled. Do not expose Hermes directly.

### 7. Handle the scanner/browser migration

Do not claim this is complete until it has either been validated or explicitly marked as the remaining interactive step.

Set up:

- Chromium on the VM
- a persistent VM browser profile
- Xvfb or a minimal remote desktop method for login/repair
- the correct WebBridge/OpenClaw component
- scanner capture service/timer after login

Require the user to perform a fresh AccessObsidian login on the VM. Do not copy local cookies.

After login, validate at least one scanner mode end-to-end and confirm TXT, JSON, CSV, and summary files are written on the VM and subsequently backed up.

### 8. Backups and durability

Configure periodic backups to:

```text
gs://project-eec91607-77a2-4be6-837-cipher-runtime
```

Back up at least:

- active SQLite databases using SQLite-safe backup or checkpoint logic, not unsafe copying during writes
- Tradier raw data
- scanner captures
- GEX snapshots
- operational manifests and service configuration
- selected reports/configuration needed for recovery

Use timestamped immutable backup prefixes. Add a sensible lifecycle rule only after confirming retention requirements. Test a restore into a temporary directory.

### 9. Validate independence from WSL2

Perform and record all of these tests:

1. `cipher-core` health returns success on VM loopback.
2. Cipher web loads and proxies the core correctly.
3. Alpaca-backed read-only request succeeds without exposing credentials.
4. Tradier collector creates a new run and durable raw records on the VM.
5. GEX collector completes a conservative smoke capture.
6. DevSpace process starts and passes its local doctor/health checks.
7. Hermes starts and passes doctor/status checks, with any remaining provider login clearly listed.
8. Backup timer uploads a new timestamped backup object.
9. A restore smoke test succeeds.
10. Reboot `cipher-main`, then verify required services return automatically.
11. Confirm no firewall rule exposes application ports publicly.
12. Confirm the old Cloud Scheduler Tradier job remains paused while the VM Tradier service is active.
13. Confirm the system continues collecting without relying on a process running in WSL2.

You cannot literally turn off the machine hosting your current coding session during execution. Instead, ensure no production process depends on the local WSL IP, filesystem, Tailscale hostname, or localhost bridge, and verify fresh VM-side timestamps after local collectors are stopped or left unused.

## Security and cost review

Before finalizing:

- Review VM IAM and remove unnecessary project-wide roles.
- Ensure the runtime service account has only Secret Manager access to named secrets, backup-bucket access, logging, and monitoring needed by its services.
- Check whether the VM truly needs an external IP after Tailscale/IAP setup. Remove it if feasible without breaking outbound data access; use Cloud NAT only if justified by cost/complexity.
- Keep Compute Engine automatic restart enabled.
- Report the current machine type and a realistic monthly cost range, but do not resize destructively without evidence.
- Do not enable BigQuery/Pub/Sub merely for architectural neatness. The immediate goal is reliable always-on operation. Add them only if required by an implemented pipeline.

## Required final deliverables

1. Working VM runtime and service status.
2. Updated, idempotent files under `infra/gcp-cipher-vm/`.
3. `infra/gcp-cipher-vm/MIGRATION_REPORT.md` containing:
   - resources created/reused
   - exact data migrated and excluded
   - hashes and integrity results
   - service status
   - test results
   - backup/restore result
   - security review
   - cost notes
   - interactive steps still required
   - rollback instructions
4. A concise final response with:
   - completed items
   - failures or unresolved blockers
   - exact one-time user actions, if any
   - exact commands for health/status inspection

## Reporting cadence

Provide brief progress updates after major milestones or when a real blocker is discovered. Do not report every command. Do not stop at the first recoverable error; diagnose it and continue safely.

## Definition of done

The migration is complete when Cipher core/web, Tradier collection, conservative GEX collection, backups, and private remote administration run from `cipher-main` after a reboot, with no dependence on the local WSL2 filesystem or processes. DevSpace, Hermes, and AccessObsidian scanning must either also run from the VM or be reduced to clearly documented one-time authentication steps that the user must complete securely.
