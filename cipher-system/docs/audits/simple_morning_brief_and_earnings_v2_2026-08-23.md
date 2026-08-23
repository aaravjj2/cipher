# Simple Morning Brief and earnings model v2 audit — 2026-08-23

## Outcome

The Morning Brief now answers four daily questions in one screen: what the
market is doing, what matters for the selected ticker, whether paper systems
are healthy, and which setups deserve review. It no longer fetches model or
watchlist administration data, and opening the panel no longer triggers an LLM
synthesis request.

The earnings paper engine now uses a versioned, independently gated model. The
change does not revise prior paper outcomes. It prevents new trades when the
direction model has not demonstrated useful out-of-sample lift.

## Model design

- Version: `earnings-v2.0-2026-08-23`
- Dataset: 22,688 historical earnings observations.
- Split: chronological 60% training, 20% model selection, 20% untouched final
  holdout.
- Direction candidates: logistic regression, shallow gradient boosting, and a
  conservatively regularized random forest. Selection uses validation Brier
  score.
- Gap candidates: ridge and Huber-loss gradient boosting. Selection uses
  validation MAE.
- Production estimators are refit on training plus validation only after final
  holdout metrics have been recorded.
- A direction trade requires at least 100 final holdout observations, at least
  55% accuracy, at least two percentage points of lift over the naive majority
  class, and a better Brier score than the naive probability.

## Independent holdout result

The final holdout contains 4,538 observations.

| Output | Selected model | Result | Naive baseline | Decision |
| --- | --- | ---: | ---: | --- |
| Day-5 direction | Random forest | 52.03% accuracy | 51.76% | Reject |
| Day-5 confidence gate | Random forest | 42.86%, N=7 | 57.14% | Reject |
| Day-1 direction | Selected classifier | 50.77% | 50.88% | Reject |
| Reversal | Selected classifier | 74.31% | 74.31% | Reject |
| Absolute gap | Gradient boosting | 1.9777% MAE | 2.2523% | Descriptive only |

The absolute-gap model improved MAE by 12.19%, but a size estimate alone does
not validate a directional option trade. The active strategy status is
`NO_TRADE_DIRECTION_FAILED_HOLDOUT`, so every current radar card is neutral and
new earnings paper entries are blocked.

## Paper scorecard integrity

The scorecard still contains 33 legacy estimated-entry positions: 20 open and
13 settled. Two settled positions are wins, estimated realized P&L is
`-$14,245`, and settled win rate is 15.38%. These records were not relabeled or
recalculated. Schema migration only added nullable `model_version` and
`validation_status` fields; old records report as
`legacy-unversioned / LEGACY_ESTIMATED_ENTRY`.

Future eligible entries must persist their model version and validation status.
The UI and Discord scorecard separate cohorts and continue to state that legacy
option premiums are estimates, not captured live fills.

Recoverable pre-change backups are stored outside the repository under
`runtime/backups/earnings/`.

## Data-quality limits

- Current earnings dates come from Yahoo Finance only and are displayed as
  `single source / unconfirmed`. Alpaca does not supply a dependable earnings
  calendar in the current integration.
- No missing historical option quote is reconstructed. Legacy entry premiums
  remain explicitly estimated.
- Expected gap is descriptive while the direction gate is closed.
- This remains research and paper simulation; no live-order authority was
  added.

## Deployment and verification

- Python 3.12 full suite: 1,088 passed, one skipped.
- Focused model, paper, radar/Discord, Morning Brief, and research-only gates
  pass.
- Node application suite: 33 passed.
- Web invariant suite: 61 passed; lint, TypeScript, and production build pass.
- Published frontend and source build were synchronized; core and web services
  were restarted and healthy.
- A headed Chromium run through the normal Tailscale browser URL passed the
  guest Strike Matrix flow with no browser or API failures.
- The audit found and fixed a hosted configuration omission that had disabled
  guest sessions. Guest access remains origin-restricted and read-only.

## Highest-impact next work

1. Capture real OPRA bid/ask snapshots for any future model-approved earnings
   setup and refuse entry when a contemporaneous quote is absent. Do not
   backfill uncaptured historical premiums.
2. Add a second earnings-calendar source and require agreement or visibly flag
   conflicts before a scheduled run.
3. Settle the remaining 20 legacy positions and keep that cohort separate from
   every versioned prospective cohort.
4. Accumulate prospective predictions, including no-trade decisions, and only
   retrain or reopen the gate after a scheduled chronological revalidation.
5. Explore ticker/setup-specific rare cohorts only with nested walk-forward
   selection and minimum sample gates; reject improvements that disappear on
   the untouched window.
