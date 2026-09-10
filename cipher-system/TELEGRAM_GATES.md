# Gates: Telegram local paper worker

- [x] T1: Polls @ThetaWireMid_bot every 10 seconds, downloads media privately, and resumes without duplicates.
  EVIDENCE: service caught message 4114 after cursor initialization at 4113; cursor persisted in isolated SQLite. Media downloads chmod 0600.
- [x] T2: Deterministically parses complete 1–4 leg entries and matching close/P&L updates while rejecting ambiguity.
  EVIDENCE: parser tests cover single, vertical, four-leg iron fly, explicit close, TP/SL mark, duplicate and ambiguous close. Live message 4114 correctly blocked for missing side and price.
- [x] T3: Uses a separate local-only SQLite portfolio, quantity one, +50% take-profit, -25% stop-loss, and no broker-order capability.
  EVIDENCE: database /home/aarav/Aarav/cipher/runtime/data/telegram/theta_paper.sqlite reports paper_only true, external_order_capability false, quantity 1, TP 50, SL 25; four-leg atomic insert test passes.
- [x] T4: Discord posts entry/exit/block alerts through the existing webhook without leaking credentials.
  EVIDENCE: service environment uses configured DISCORD_PROGRESS_WEBHOOK; live block event emitted without discord_error and output contains only bounded result JSON, never webhook or Telegram credentials.
- [x] T5: Service is installed, enabled, active, and verified against a replay fixture plus live read-only ingestion.
  EVIDENCE: cipher-theta-paper.service enabled and active since 2026-09-09T17:31:05Z; live cursor advanced to 4114 and isolated ledger stayed valid.
