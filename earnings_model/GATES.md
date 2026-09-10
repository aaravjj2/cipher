# Gates: Earnings model diagnosis and repair

- [x] E1: Raw forecasts are separate from entry eligibility and are explicitly unvalidated in the digest.
  EVIDENCE: test_unvalidated_raw_probability_is_visible_without_authorizing_entry passes: 73% raw probability remains UNVALIDATED and NO TRADE. Live artifact inference for COO, ADBE, ORCL returns distinct probabilities with strategy_eligible false.
- [x] E2: Missing outcomes remain unknown; refreshed training is evaluated without replacing the production artifact or weakening gates.
  EVIDENCE: test_missing_outcomes_are_not_negative_training_labels passes. Diagnostic refit results recorded in EVALUATION_2026-09-08.md; direction remains ineligible and save_artifacts=False preserves deployed model.
- [x] E3: Relevant regression tests pass and the radar refresh path is verified without sending Discord messages.
  EVIDENCE: 35 targeted earnings tests pass; lint, typecheck, build and EarningsRadar recovery test pass. Radar command completed and wrote seven cards with raw forecast fields; existing seven prospective records preserved. Local static manifest matches built output. No Discord sender invoked.

- [ ] E4: Public frontend deployed.
  EVIDENCE: Vercel production deployment failed with Not authorized; local static build published successfully.
ABANDON: E4 Vercel credentials require reauthorization before public deployment can proceed.

- [x] E5: Validation evaluates the exact saved estimator and purges label-overlap boundaries.
  EVIDENCE: 41 tests passed, including returned-estimator metric equality, duplicate-date embargo, and raw-probability forward logging. Observed boundaries are 2022-02-18 to 2022-03-01 and 2024-05-30 to 2024-06-10.
- [x] E6: Corrected candidate is evaluated and promotion blockers are reported without replacing production.
  EVIDENCE: save_artifacts=False diagnostic: 254 qualified, 52.76% accuracy versus 53.15% baseline, Brier 0.2503 versus 0.2498. Gate stays closed; frozen forward criteria and artifact hash recorded in EVALUATION_2026-09-08.md.

- [x] E7: Upcoming-event priors include the latest mature report, exclude future/unmatured reports, and preserve missing values.
  EVIDENCE: 43 targeted tests pass, including latest-report inclusion, immature/future exclusion, and unknown-outcome handling. Forecast feature method and last mature date flow into prospective records; old records remain untouched.

- [x] E8: Read-only forward scoring deduplicates events, rejects late forecasts, separates immutable model cohorts, and waits for mature outcomes.
  EVIDENCE: 31 targeted tests passed; actual log audit finds 151 legacy records without raw probabilities and zero scoreable cohorts. No records rewritten and no promotion granted. New records carry loaded artifact SHA256 and feature method.
