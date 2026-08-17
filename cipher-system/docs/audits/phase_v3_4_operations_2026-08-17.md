# Phase V3.4 audit — current runtime and backups

Date: 2026-08-17 UTC

## Outcome

The unified audit now measures the active systemd services and eight required
timers instead of obsolete PID-file scheduler/healer processes. A 401/403 from
the protected web research route is correctly treated as an enforced auth gate;
the loopback core route remains the direct health source.

Weekend capture freshness now compares the observation date with the latest
completed trading weekday, so Friday data remains `LAST_SESSION` through the
weekend instead of becoming falsely stale after 36 hours.

The disabled restore-verified local backup timer was repaired operationally:
its target directory was restored, a real four-store backup completed with
restore verification, and `cipher-local-backup.timer` was enabled. Its next run
is scheduled for 2026-08-17 23:31 UTC.

## Verification

- Operator/backup focused suite: 6 passed.
- Unified product audit: COMPLETE; every current gate passed.
- Latest backup manifest: restore verified, four stores.
- Core/web/GEX/Tradier services and required timers are active.
- Audit states paper simulation enabled and live execution disabled.

Phase verdict: accepted.
