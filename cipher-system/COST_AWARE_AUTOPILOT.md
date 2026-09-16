# Prospective morning Autopilot: cost-aware v3

This is a fifth, independently recorded simulated experiment, not a change to
baseline, confirmation, cost, or exit v2. Their configuration and implementation
hashes, historical records, and promotion requirements remain unchanged.

Enable alongside `CIPHER_AUTOPILOT_COHORTS=1` with
`CIPHER_AUTOPILOT_COST_AWARE=1`. Its ledger lives under
`paper_runtime/cohorts/cost_aware_v3/paper.sqlite`; its loopback port is the base
port plus four (8791). It appears in cohort summaries, failure alerts and daily
portfolio reports. It does not become the primary strategy by being enabled.

The experiment evaluates every structurally eligible debit spread against the
same shared observation snapshot. Both legs require known bid and ask sizes
covering quantity one, exact quote identity, fresh synchronized executable
quotes, and the existing contract, capital and session constraints.

Immediate round-trip cost includes bid/ask crossings, configured slippage and
configured fees. It must not exceed one third of the entry-debit stop budget;
this reuses the frozen cost-v2 limit rather than fitting a threshold to losses.
Survivors rank by cost/stop ratio, then the existing structural score and stable
contract identity. The selected exact pair passes the existing transactional
fill writer and exit machinery. There is no fallback to fabricated prices.

Candidate decisions are stored in `cost_aware_candidates`; position entry
evidence records costs, stop budget, policy and candidate counts. A durable
registration timestamp rejects older source signals, including recovered inbox
batches. Restart preserves that timestamp. Changing implementation or
configuration requires another version, not replacing this experiment's history.

Validation covers cost rejection, alternative candidates, missing sizes,
missing/stale/asynchronous/wrong-identity quotes, registration and restart.
Real forward quote coverage and performance remain to be measured. Existing
fees default to zero where configured; the experiment does not claim those
settings reproduce a particular broker's charges. Historical losses are not
rewritten, and passing these tests is not evidence of profitability or promotion.

Before disabling the fifth experiment, ensure its positions are flat; disabling
its worker while positions are open would also disable their monitoring.
