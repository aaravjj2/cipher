# Phase 4 audit — architecture, operations, and release quality

Date: 2026-08-14 UTC
Scope: request coordination, service boundaries, operational/storage safety,
dependency hygiene, authenticated browser verification, and production release
Boundary: private/local/read-only research; no live broker execution

## Outcome

Phase 4 is complete and deployed. The terminal now coordinates duplicate browser
reads, exposes a source-of-truth operator panel, performs daily restore-verified
small-state backups, has a first explicit HTTP-to-provider use-case boundary,
and is tested through its actual authentication gate in desktop and mobile
Chromium. The active package is identified as Cipher rather than its historical
UI scaffold.

## Delivered

- Same-tab GET coordinator for every typed browser API helper:
  - one network request per identical in-flight resource;
  - short result reuse with regular-session vs. off-hours TTLs;
  - subscriber abort detaches only that subscriber;
  - successful mutations invalidate cached read models.
- `terminal_service.py` boundary for option-chain and manual-portfolio use cases.
  HTTP routing supplies Alpaca-backed quote/chain callables; calculations remain
  provider-independent and no order authority crosses the boundary.
- Operator Status API/panel:
  - filesystem capacity with an explicit `NOT_ESTIMATED` runway state;
  - read-only database schema/integrity probes;
  - capture age classified as `CURRENT`, `LAST_SESSION`, `STALE`, or
    `UNAVAILABLE` using New York session state;
  - core cache inventory;
  - latest backup/restore-verification state;
  - consolidated actionable exceptions.
- `backup_local_state.py`:
  - SQLite online backups for alerts, watchlists, journal, and six paper
    fronttest portfolios;
  - SHA-256 manifest;
  - isolated restore copy plus SQLite integrity check;
  - `.partial-*` staging so an interrupted run cannot look complete;
  - large reproducible market archives deliberately excluded and labelled.
- Hardened systemd daily backup service/timer, enabled for 23:30 UTC with
  persistent scheduling and narrowly scoped writable paths.
- Product/release cleanup:
  - package identity `cipher-local-trader-terminal`, private and unlicensed;
  - Node engine aligned with the deployed Node 22 runtime;
  - Next.js upgraded from 16.2.1 to 16.3.1;
  - all transitive production advisories resolved (`npm audit`: zero).
- Authenticated Playwright/Chromium gate at desktop and 390×844 mobile, with
  screenshots saved as ignored test artifacts.

## Audit findings resolved during the phase

1. Every panel fetched independently, so identical quote/matrix reads could fan
   out. All typed GETs now pass through one in-flight/cache coordinator.
2. Package metadata still advertised a website-clone template and third-party
   author/repository. Active metadata now describes the private Cipher terminal.
3. Fifteen production-tree npm advisories were present, including a direct
   Next.js advisory. Supported patch/transitive updates reduced the audit to zero
   without breaking the static export.
4. The first real systemd backup test failed on the active fronttest database:
   SQLite needed to access its shared-memory sidecar while reading WAL state.
   The unit now grants that one runtime directory sidecar access while the source
   connection remains `mode=ro`; the subsequent service run exited successfully.
5. The failed backup run had already created three valid copies and one empty
   file. That incomplete directory was retained as `.failed-*` for diagnosis,
   not treated as a backup. Future runs use `.partial-*` until fully verified.
6. The first authenticated mobile browser run found ACCOUNT navigation below the
   viewport and not scrollable. Desktop/mobile navigation regions now have an
   explicit bounded vertical scrollport. The rerun passed at 390 px.
7. Capture health initially called any artifact `AVAILABLE`, which concealed
   session semantics. Closed-market captures now read `LAST_SESSION`; a genuinely
   late regular-session capture becomes `STALE` and raises an exception.

## Verification evidence

- Full Python suite: **870 passed, 2 skipped**.
- Node auth/proxy/ingest suite: **18 passed**.
- Web geometry/accessibility/truth/source suite: **44 passed**.
- Focused service/operator tests: 12 passed during implementation; all included
  again in the full result.
- TypeScript and ESLint: passed.
- Next.js 16.3.1 production static export: passed.
- `npm audit`: **0 vulnerabilities**.
- systemd unit verification: passed.
- Real backup service: exit status 0; four state stores hash-checked and
  restore-verified. Daily timer is active.
- Authenticated Chromium:
  - desktop Operator Status navigation/render: passed;
  - 390×844 drawer navigation, layout width, and render: passed.
- Atomic static publication completed and served tree matches the build.
- Live Operator Status: HTTP 200, all small databases OK, large GEX database
  schema-readable with expensive whole-file check explicitly not run, market
  captures `LAST_SESSION`, saved scans `CURRENT`, backup `VERIFIED`, zero
  exceptions, execution capability false.
- `cipher-core`, `cipher-web`, and `cipher-local-backup.timer`: active.

## Safety and truth review

- No `/v2/orders`, order submission client, live runner, or browser order ticket
  was added.
- Operator and backup APIs both report execution capability false.
- Large databases are not synchronously scanned in an HTTP request; skipped
  whole-file checks say `NOT_RUN_LARGE_FILE`, not `OK`.
- Disk days-of-runway is not guessed without historical growth samples.
- Market captures are judged with exchange-session context, preventing normal
  overnight quiet from being called an outage.
- Client caching never converts missing option/GEX inputs to zero and does not
  alter the server's provenance/caveat payloads.

## Remaining honest gaps

- Upcoming earnings and a complete splits/ex-dividend feed remain unavailable or
  partial. SEC filings are not a substitute for an event-calendar provider.
- Option-premium journal MFE/MAE and broker-synced tax lots remain unavailable;
  the journal is manual and uses underlying 5-minute excursions.
- Browser request coordination is same-tab memory, not cross-tab coordination or
  a persistent offline cache.
- `core/app.py` is still large. The new terminal service is a useful seam, not a
  completed route/provider decomposition.
- Operator status does not yet retain provider latency/error-rate time series.
- Backups cover small irreplaceable user state only. The large Tradier/GEX/history
  archive still needs a separately sized retention and off-host recovery policy.
- No backup-retention deletion policy is enabled, so verified copies will
  accumulate until that policy is chosen.
- Chart journal links store reconstructable chart state, not a rendered image.
- Read-only simulation remains the deliberate product boundary. Any live broker
  adapter would require a separately authorized package and boundary review.

These are the next product backlog, not hidden completion claims.
