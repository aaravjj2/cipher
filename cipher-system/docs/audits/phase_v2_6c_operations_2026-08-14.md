# Phase V2.6c audit — operations, telemetry, and retention safety

Date: 2026-08-14 UTC

## Outcome

Cipher now records bounded provider performance and local dataset growth without
storing market payloads, credentials, or request headers. Operator Status exposes
the results alongside cache behavior, restore readiness, and verified archive
receipts. Retention remains a dry-run report and has no deletion path.

## Implemented

- Extracted provider/cache/storage telemetry into `core/provider_telemetry.py`
  rather than expanding the HTTP route monolith.
- Added Alpaca operation latency, status, typed failure, p95, request count, and
  error-rate recording. Query strings, headers, credentials, and response bodies
  are deliberately excluded.
- Added per-cache hit/miss counters and hit-rate output while preserving existing
  bounded TTL caches.
- Added daily storage snapshots for option-chain, GEX, backtest, and research
  artifacts. Runway is withheld until at least one full day of measured growth
  exists.
- Added explicit 90/180-day retention candidates in `DRY_RUN_ONLY` mode. The
  response permanently reports `destructive_action_enabled: false`.
- Exposed the existing off-host chain archive receipt ledger. A receipt is only
  counted after the GCS object checksum is verified; local pruning remains part
  of the separately configured archive job.
- Added a daily systemd storage snapshot timer and an operator UI for latency,
  cache effectiveness, retention candidates, and archive receipts.

## Live bounded verification

- Operational snapshot captured 67 live-chain files (7.87 GB), 63,430 GEX
  snapshots (534.0 MB), two backtest artifacts, and three research artifacts.
- Alpaca telemetry recorded successful quote, trade, and bar requests at
  132–156 ms in the bounded smoke run.
- Archive ledger reported 144 checksum-verified receipts and 144 verified local
  prunes.
- Latest small-state backup reported `VERIFIED` with four readable stores.
- Disk had 28.44% free. Runway correctly remained `INSUFFICIENT_HISTORY` rather
  than inventing an estimate from a single snapshot.

## Verification

- Python compilation: passed.
- Provider telemetry and operator status tests: 7 passed.
- Operator Status TypeScript typecheck and focused lint: passed.
- `cipher-operational-metrics.timer`: installed, enabled, and waiting.
- Core restarted cleanly and the live `/api/operator-status` contract returned
  no exceptions and `execution_capability: false`.

## Remaining limitations

- Runway needs a second storage sample at least one day later before it can be
  estimated.
- Provider timings cover active Alpaca calls from the core; background Tradier
  collector metrics remain in its own service logs.
- Archive receipts prove object upload and checksum verification, not a full
  disaster-recovery rehearsal. The local-state backup remains the restore-tested
  path for irreplaceable small state.

Phase verdict: accepted. Operations are observable without weakening the local,
read-only, secret-boundary design.
