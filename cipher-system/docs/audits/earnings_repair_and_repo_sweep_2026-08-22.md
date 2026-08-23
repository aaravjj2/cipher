# Earnings repair and repository sweep — 2026-08-22

## Outcome

The active Cipher product passes its Python, Node, web, build, and research-only
invariants. The earnings automation now uses a live schedule, current pre-event
price drift, schedule-aware freshness, deterministic expiry settlement, complete
paper-book reporting, Discord-compliant message splitting, and observable service
failures. No live-order authority was added.

Ponytail was applied as an evidence-driven refactor: normalize data once at the
SQLite/Pandas boundary, reuse the existing scanner and paper-book paths, and fix
tests that encoded stale behavior. A cosmetic whole-repository rewrite was
deliberately excluded because it would increase regression risk without fixing a
measured defect.

## Repaired defects

- Removed the hardcoded August earnings week and expiry from Discord output.
- Split large Discord embeds without dropping positions or exceeding the 6,000
  character/25-field limits.
- Made Discord/CLI failures return a non-zero status and made the systemd unit
  preserve partial-stage failures.
- Added idempotent paper entries and printed the complete active book after each
  run rather than only newly inserted rows.
- Settled expired defined-risk positions from the underlying expiry close; a
  missing close remains open and is reported as an error.
- Fixed the unreachable `$10` strike increment for underlyings above `$500`.
- Replaced historical-event price drift in upcoming predictions with current
  5/20-session price drift.
- Made Friday artifacts remain current over weekends while still detecting a
  missed weekday 08:15 ET run.
- Published model validation metadata and explicit paper-only limitations in the
  radar API, UI, and Discord output.
- Normalized nullable news columns to numeric values at ingestion, eliminating
  repeated Pandas downcast warnings.

## Measured evidence

- Current radar: 34 cards; all 34 report `market_drift_source=current`.
- Historical artifact holdout: N=4,537, day-5 direction accuracy 51.73%, expected
  absolute-gap MAE 1.9793 percentage points.
- Separate three-month strategy holdout: N=513, simulated win rate 58.67%, profit
  factor 0.71, average P&L -10.88%, gated direction accuracy 46.15% (N=65).
- Paper database after first deterministic settlement: 33 total, 20 open, 13
  settled, 2 positive, estimated P&L -$14,245 (15.38% win rate).

The paper result is **not captured option-market P&L**. Entry debit/credit values
were heuristics and settlement uses underlying intrinsic value. It is useful as a
failure signal, not evidence of tradable performance. The feature must remain
`UNVALIDATED_FOR_LIVE_OPTIONS_PNL`.

## Verification

- Python 3.12: 1,085 passed, 1 explicit skip.
- Python 3.11 production environment: 1,084 passed, 2 explicit skips.
- Node proxy/server: 33 passed.
- Web invariants: 61 passed.
- Research-only guard: 11 passed.
- ESLint, strict TypeScript, Python compilation, Next.js production build, and
  `git diff --check`: passed.
- Published `app/public` exactly matches `web/out`.
- `cipher-core`, `cipher-web`, and `cipher-earnings-digest.timer`: active.
- Core and web health endpoints: healthy; radar reads `current` with 34 cards.
- Next scheduled earnings run: Monday 2026-08-24 at 12:15 UTC (08:15 ET).

## Ranked next action plan

1. **Capture real option quotes for prospective paper fills.** Persist bid, ask,
   midpoint, timestamp, contract identifier, IV, and spread at entry and at each
   mark. Without this, strategy P&L cannot be validated.
2. **Build a prospective promotion gate.** Require at least 100 non-overlapping
   observations per strategy, positive net expectancy after slippage, profit
   factor above 1.2, controlled drawdown, and a lift over a documented baseline.
   Failed strategies stay visible as rejected research, never silently promoted.
3. **Strengthen current-event inputs.** Refresh point-in-time earnings news and
   sentiment, cross-check calendar dates across Yahoo and Alpaca, and record source
   disagreements rather than choosing one silently.
4. **Expose the paper scorecard in-product.** Add open/settled counts, realized
   estimated P&L, win rate, data-quality coverage, and daily deltas to Earnings
   Radar and the operator view. Discord now carries the compact scorecard.
5. **Calibrate only after prospective data exists.** Refit probability outputs,
   compare against majority/naive baselines, and delete recommendations that do
   not show out-of-sample economic edge.
6. **Continue targeted cleanup.** Use coverage, static analysis, runtime warnings,
   and failing tests to select the next refactors; avoid a high-risk cosmetic
   rewrite of stable modules.
