# Experiment Spec — EXP-001: require a triggered setup for primary Cipher confirmations

Status: PROPOSED — do not enable until the sample gate below is met.

## Hypothesis

The entry-confirmation planner exempts the primary (regular-session Cipher)
scan from the `setup_not_triggered` check that every secondary source must
pass (`autopilot_planner.py`, `is_primary` branch). The exemption means the
autopilot can enter on a setup level that price has not actually touched in
the regular session. The Aug 24–25 ledger shows 4 of 6 entries were
dead-on-arrival (MFE never reached +5%), and 3 of 4 losses clustered in the
first confirmation passes after the open. Removing the exemption should
reduce dead-on-arrival entries without proportionally removing winners.

## Mechanism

In `confirmation_payload`, apply the same `state == "triggered"` requirement
to the primary scan that secondary scans already face. Cards whose agent
state is absent/untriggered move to `rejected` with reason
`setup_not_triggered`. No other gate changes.

## Sample gate

Do not flip this on before the decision-quality report
(`scripts/autopilot_decision_quality.py`) covers **>= 30 closed trades**.
At n=6 any verdict is noise; at n=30 the DOA share and expectancy have a
chance of meaning something.

## Evaluation

Run as a single-variable A/B against the accumulating prospective record:

1. Baseline window: current behavior, >= 30 trades.
2. Experiment window: >= 30 trades with the trigger requirement enabled.
3. Primary metrics: dead-on-arrival share, expectancy per trade,
   win rate. Secondary: entries/day (the trigger will reject more cards;
   fewer trades is an acceptable cost only if expectancy improves).

Decision rule: adopt only if experiment-window expectancy per trade exceeds
baseline by a margin that survives the repo's standard caution about small
samples, and DOA share falls. Otherwise revert to the exemption and record
the result.

## Rollback

One-line revert in `confirmation_payload` (restore the `is_primary`
exemption). No schema or data migration involved.
