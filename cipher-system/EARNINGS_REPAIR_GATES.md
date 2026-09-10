# Earnings data-failure repair

- [x] Calendar events survive drift/model failures without fabricated forecasts.
  Evidence: scanner tests cover history exceptions, model errors, null gap/beat values and blocked entries.
- [x] Scan failures are visible in the artifact and API, not reported as an empty successful scan.
  Evidence: partial/unavailable diagnostics tests pass; artifact publication is atomic; UI renders partial data explicitly.
- [x] Missing model inference cannot train or replace production artifacts.
  Evidence: regression asserts training is never called when inference cannot load the model.
- [x] Regression tests and actual forecast refresh verified; no gate relaxed or broker order sent.
  Evidence: 113 targeted tests passed; frontend typecheck, 3 UI tests and production build passed. September 10 radar refresh completed with 11 cards; no Discord sender or paper-enter command invoked by verification.

## Notification misfires

- [x] Seven recorded menu/report messages replay as ignored in an isolated database, with zero positions created.
- [x] Unidentified closes cannot select the only open position; unpriced closes remain pending. Discord delivery uses a persistent outbox and clear symbol/P&L labels.
- [x] Legacy regime/daily cron entrypoints now return without fetching or sending. Routine portfolio sender excludes historical research books; scheduled earnings uses the fresh saved radar and condenses blocked forecasts.
- [x] Theta and core services restarted successfully; four Autopilot cohorts remain available. Historical Theta backup: runtime/data/telegram/theta-misfire-audit-821vwxgb/backup.sqlite.

Remaining: reliable image-only trade extraction is not implemented. Downloading an attachment is not parsing it. Three historical unpriced Theta closes remain explicitly unknown; no exit prices were reconstructed. Pending exits still need identifiable pricing evidence. These limitations are not marked fixed.

## Follow-up hardening

49 targeted tests pass after requiring every stated close strike, option kind and supplied expiry to match. Image-only messages persist as needs_review with attachment paths; they do not generate false trade alerts. Attachment download errors no longer stop text processing. The Theta worker was restarted with these changes. Image extraction and independent quote-driven exits remain outstanding.
