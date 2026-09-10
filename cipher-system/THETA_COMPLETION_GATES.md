# Gates: Theta execution and model evidence

- [x] G1: Strict execution, OCR, review authentication and baseline tests pass.
  CHECK: /home/aarav/.venvs/cipher/bin/python -m pytest -q tests/test_theta_quote_portfolio.py tests/test_theta_review_auth.py tests/test_earnings_forward_scorecard.py tests/test_earnings_model_selection.py tests/test_paper_portfolio_api.py
  EXPECT: passed
  EVIDENCE: Final full Python suite: 1374 passed, 2 skipped in 105.08 seconds. Focused Theta/earnings suite: 51 passed. Additional earnings/Autopilot regression suite: 38 passed.
- [x] G2: Browser tests, typechecking and production build pass.
  EVIDENCE: 155 Node tests passed; lint/typecheck passed; production build and atomic publish passed. One concurrent build attempt hit the build lock; the publication build completed successfully.
- [x] G3: New worker runs in observation mode and reconciles across restart; original ledger backed up.
  EVIDENCE: 2026-09-10 worker status reports observe, current execution/data/notification health, zero positions, restart reconciliation satisfied. SQLite integrity-checked backups retained privately outside Git.
- [ ] G4: Complete regular-session observation and quote coverage qualify activation.
  EVIDENCE: Real observation began before the September 10 regular session. The required complete session has not elapsed; activation remains disabled.
ABANDON: G4 Activation is deferred pending the real full-session observation requirement; offline tests cannot satisfy this gate.
- [x] G5: Saved earnings gate reasons explain the reported blocked digest.
  EVIDENCE: Saved radar 2026-09-10T12:16:55Z: 11 forecasts, 447/447 calendars current, all entries blocked by independent holdout failure. Direction accuracy 52.03%, baseline 51.76%, qualified subset 7 events at 42.86%. Existing artifact baseline remains null; no forecasts backfilled.
- [x] G6: Sanitized changes committed and pushed with no runtime data or credentials.
  EVIDENCE: Implementation commit 7d4bf1e pushed to origin/master; local and remote refs matched. All 170 staged files scanned against secret patterns and 21 configured credential values with zero matches. No runtime databases, Telegram sessions or secrets staged. Guest screenshot visually checked. Staged whitespace checks passed.

Deployment limitation: authenticated review routes deny all writes until the
existing owner's user ID is configured as CIPHER_THETA_REVIEW_USER_ID. The user
has been asked for that ID; no credentials are requested or stored in this file.

Stored-evidence replay: 97 messages produced 93 review candidates, 4 ignored
administrative messages, and zero fills. Twelve unique stored screenshots all
retained uncertain OCR in review. Clean complete/incomplete synthetic screenshot
tests exercise real Tesseract and parser validation.
