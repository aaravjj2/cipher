# Earnings repair evaluation — September 8, 2026

Raw probabilities are now separate from paper-entry eligibility. Missing future
returns and missing EPS actuals remain unknown rather than becoming negative labels.
The production artifact was not replaced and no trade thresholds were relaxed.

Diagnostic refit (`train_earnings_models(save_artifacts=False)`):

- 22,784 source rows; three missing five-day outcomes.
- 22,781 complete rows; train 13,668 / validation 4,556 / test 4,557.
- Five-day direction: 52.45% accuracy versus 51.83% baseline.
- Seven confidence-qualified observations; 42.86% accuracy versus 57.14% baseline.
- Direction remains ineligible. Minimum qualifying sample is 100.
- Gap magnitude MAE: 1.972 percentage points versus 2.2465 baseline (12.22% improvement).

This reuses historical data already examined during development: it is a diagnostic
refit, not new independent proof or an options-P&L backtest. A small change in the
reported accuracy is not evidence of improved predictive skill. No candidate was promoted.
Further model work needs a frozen forward evaluation window and an audit of feature
availability and label-maturity boundaries before any promotion claim.

## Corrected estimator evaluation, 17:05 UTC

The classifier and gap evaluator previously tested a train-only fit but returned a
different train-plus-validation refit. They now score the exact returned estimator.
Report dates are grouped and a ten-calendar-day embargo separates splits, covering
the five-session response horizon conservatively at these observed boundaries.
Mixed timezone serializations are normalized to provider report dates.

- Training ends 2022-02-18; selection starts 2022-03-01.
- Selection ends 2024-05-30; test starts 2024-06-10.
- Train / selection / test samples: 13,594 / 4,548 / 4,557.
- Five-day model: random forest, selected only on selection-window Brier score.
- Overall accuracy 51.99% versus 51.83% baseline.
- Qualified sample 254; accuracy 52.76% versus 53.15% baseline.
- Brier error 0.2503 versus 0.2498 baseline (lower is better).
- Gap MAE 1.9558 versus 2.2322 baseline: 12.38% improvement.
- No promotion: sample requirement passes, accuracy, lift, and Brier requirements fail.

This supersedes the earlier diagnostic's estimator-level metrics, not the saved
production artifact. Historical test data has already been inspected, so even a
passing retest here would require new prospective evidence before promotion.

Production artifact SHA256 (unchanged):
`e8e4afa15445ede0482922787cdadf15c27fe99208ebdb1f25729a78d4fd6961`.

## Forward evaluation contract

Starting with the next scheduled radar, preserve raw five-day up probabilities
alongside gated decisions in the append-only log. Do not fill missing probabilities
in older records retrospectively. Evaluate unique (symbol, report date) events,
using the last recorded forecast strictly before the report, only after its five-day
outcome has matured. Repeated daily forecasts are not independent samples.

Keep confidence threshold 58%, minimum qualified sample 100, qualified accuracy
at least 55%, lift at least two percentage points over the frozen baseline, and
Brier error below the baseline. Freeze model artifact and baseline before scoring;
a changed artifact starts a new cohort. Historical diagnostics cannot substitute
for this future window. No new forward scores are claimed by this report.

Remaining research blockers include historical date reliability, as-of availability
of financial/news features, latest-event inference priors, and absent historical
option-price/IV evidence. This pass improves validation correctness and logging;
it does not establish a new directional edge.

## Upcoming inference correction

Upcoming forecasts previously reused the last historical row's shifted priors,
omitting the latest report from historical averages and streaks. They now rebuild
next-event priors from mature historical outcomes, excluding reports less than
eleven calendar dates before the prediction date. Missing observations are excluded
from averages; missing EPS outcomes reset streak continuity instead of becoming misses.
An empty mature history returns an explicit error.

The new `next_event_mature_priors_v1` method and last mature report date flow into
the append-only forward log, allowing separate evaluation from old-method forecasts.
Past records are not rewritten. Forty-three targeted tests pass. This corrects
input construction, not measured predictive skill; no new accuracy or promotion
claim follows from the change. The production model artifact remains unchanged.

## Forward scoring implementation

Run `python -m earnings_model.forward_scorecard` from the repository root.
This reads the append-only forecast log and earnings database without mutation.
It selects the last forecast strictly before report-day midnight, conservatively
waits eleven calendar days, and scores one observation per symbol/report/model-hash/
feature-method combination. Missing raw probabilities or provenance are excluded.
The model loader now attaches an SHA256 of the actual loaded file; new scanner
cards propagate it into future records without rewriting historical records.

Initial read-only audit: 151 legacy records lack raw probabilities, leaving zero
scoreable cohorts. No historical probabilities were reconstructed after outcomes.
The scorecard explicitly disallows promotion while a frozen baseline comparison
is absent. Thirty-one targeted regression tests passed for the scoring, model,
scanner and append-only logger paths.
