# Cipher Signal Complete Observations — August 6, 2026

## Scope and cutoff

This report summarizes the governed Flash, Flash Agentic, and Cluster research state generated at **2026-08-06 10:52:33 ET**.

- Latest completed market session used for completed-session evidence: **August 5, 2026**
- August 6 observations are stored separately as an unfinished prospective session.
- Full runtime artifact: `data/governance/cipher_signal_only/latest_complete_observations.json`
- Cluster's primary horizon is the scanner's reconstructed second-listed option expiration, not the intraday move.
- No strategy promotion, paper execution, live execution, or broker authority is enabled.

## Population

| Population | Count |
|---|---:|
| Unique Flash, Agentic, and Cluster episodes | 5,253 |
| Eligible regular-session episodes | 4,771 |
| Deduplicated terminal source/ticker/session states | 726 |
| Flash/Agentic fixed-horizon records | 441 |
| Cluster expiration-defined records | 579 |
| Cluster records from completed sessions | 525 |
| Cluster records from the current partial session | 54 |

Repeated scanner heartbeats are not counted as independent signals. The report preserves every unique episode but uses one terminal state per source, ticker, and market session for outcome scoring.

## Cluster reconstruction contract

For each Cluster terminal state, the research system reconstructs and stores:

1. the provider's second listed expiration after the scan session;
2. a call for bullish states and a put for bearish states;
3. the listed strike nearest captured spot as the ATM leg;
4. the listed strike nearest the Cluster target as the target leg;
5. the first traded one-minute option bar at or after the alert as entry;
6. underlying, ATM option, target option, and ATM-to-target debit-spread outcomes through expiration;
7. maximum favorable and adverse movement;
8. whether the Cluster target was reached;
9. the actual mark timestamp, market session, and whether the mark is an intraday mark or a daily close.

Finalized and pending records are never blended without an explicit label.

## Finalized Cluster evidence

Only **13 of 525 completed-session Cluster cases** had reached their reconstructed expiration by the August 5 close.

| Metric | Result |
|---|---:|
| Underlying direction correct | 84.6% |
| Average directional underlying move | +7.64% |
| Median directional underlying move | +4.08% |
| Target reached by expiration | 69.2% |
| ATM option profitable | 61.5% |
| ATM option median return | +211.24% |
| Target option profitable | 46.2% |
| Target option median return | -93.33% |
| Debit spread profitable | 76.9% |
| Debit spread median return | +164.43% |

The finalized sample is too small for validation. It does, however, show why the target-strike option should not be judged by average return: its negative median and sub-50% profitable fraction were masked by a few extreme winners.

## Completed-session Cluster evidence: final plus pending marks

The completed-session population contains **525** cases, including 13 finalized and 512 pending cases marked through August 5.

| Structure or outcome | Available | Positive | Median return |
|---|---:|---:|---:|
| Underlying directional move | 525 | 57.5% | +0.51% |
| ATM directional option | 335 | 49.0% | 0.00% |
| Target-strike option | 341 | 39.9% | -14.29% |
| ATM-to-target debit spread | 273 | 53.1% | +5.95% |

These are not final expiration results. Pending marks can change materially before expiration.

## Direction asymmetry

Across the completed-session population, bullish and bearish Cluster states should not be treated as equivalent.

| Cluster direction | Observations | Direction correct | Average directional move | ATM median | Spread median |
|---|---:|---:|---:|---:|---:|
| Bullish | 335 | 68.1% | +2.92% | +8.26% | +21.96% |
| Bearish | 190 | 38.9% | -1.49% | -44.09% | -35.78% |

The current evidence supports prospective emphasis on bullish Cluster states and continued observation of bearish states rather than treating bearish scans as symmetric put signals.

## Rank, strength, and target distance

Rank remains more monotonic than displayed strength. The strongest populated post-hoc filters remain:

| Descriptive hypothesis | Observations | Finalized | Direction correct | ATM median | Spread median | Profitable spreads |
|---|---:|---:|---:|---:|---:|---:|
| Bullish rank 1–10 | 75 | 4 | 77.3% | +41.09% | +55.49% | 75.6% |
| Bullish rank 1–10, strength 200–299 | 58 | 3 | 81.0% | +49.91% | +52.63% | 80.6% |
| Bullish rank 1–10, target 2–10% away | 59 | 3 | 81.4% | +67.14% | +89.13% | 75.0% |
| Bullish rank 1–10, strength 200–299, target 2–10% away | 46 | 3 | 84.8% | +66.92% | +67.84% | 76.9% |
| Bullish Cluster confirmed by another source | 8 | 7 | 100.0% | +201.67% | +147.33% | 87.5% |

The first four filters were selected after observing outcomes and are frozen only for prospective tracking. Their marked option results are mostly pending: only three or four cases in each cohort had expired. Cross-source confirmation was a predefined structural hypothesis, but its sample remains only eight observations.

## Confirmation-time entry recalculation

The original eight-record confirmation statistic entered each option at the Cluster timestamp. That overstated what could be earned by a trader who waits for the other source to confirm. A cache-only follow-up therefore re-entered the same contracts using two later timing rules.

### Entry after the selected terminal states all agree

This is the requested strict timing: entry occurs on the first traded one-minute bar after the last selected agreeing source state appears.

| Metric | Original Cluster entry | Post-terminal-confirmation entry |
|---|---:|---:|
| Underlying direction correct | 100.0% | 87.5% |
| Median directional underlying return | +5.53% | +5.05% |
| ATM option profitable | 75.0% | 75.0% |
| ATM option median return | +201.67% | +176.87% |
| Target option profitable | 62.5% | 62.5% |
| Target option median return | +254.46% | +149.75% |
| Debit spread profitable | 87.5% | 87.5% |
| Debit spread median return | +147.33% | +97.23% |

Waiting reduced returns materially but did not change the spread win count: seven of eight spreads remained positive. The sole spread loser remained AAPL.

| Ticker/session | Confirmation trigger | Post-confirmation underlying | ATM option | Target option | Debit spread |
|---|---|---:|---:|---:|---:|
| AMZN Jul. 28 | Flash floor bounce near 3:55 PM ET | +17.50% | +382.99% | +477.22% | +119.21% |
| MSFT Jul. 28 | Flash momentum push near 3:57 PM ET | +23.89% | +577.26% | +1,533.43% | +253.91% |
| AVGO Jul. 30 | Final Flash momentum push near 3:56 PM ET | +1.25% | -40.67% | -99.56% | +48.23% |
| NVDA Jul. 30 | Final Flash floor bounce near 3:54 PM ET | +6.03% | +226.75% | +583.53% | +120.42% |
| TSLA Jul. 30 | Flash breakout attempt near 3:49 PM ET | +4.08% | +145.67% | -98.94% | +204.84% |
| AAPL Jul. 31 | Flash breakout continuation near 3:55 PM ET | -1.55% | -85.65% | -99.09% | -83.14% |
| AVGO Aug. 3 | Flash momentum push near 3:58 PM ET | +6.61% | +208.08% | +293.55% | +75.25% |
| META Aug. 4 | Cluster completed prior Flash agreement near 10:36 AM ET | +0.98% | +16.49% | +5.94% | +20.63% pending |

### First prospective one-source confirmation

A second timing rule uses only information available in sequence: at the selected Cluster timestamp, it checks the latest observed Flash and Agentic states; otherwise it waits for the next same-direction alert. This avoids waiting for the end-of-session terminal state, although the eight-record cohort itself is still identified from terminal states and therefore retains selection look-ahead.

- Underlying direction correct: **8/8**
- ATM options profitable: **6/8**; median return **+201.67%**
- Target options profitable: **5/8**; median return **+254.46%**
- Debit spreads profitable: **7/8**; median return **+134.96%**

For the exact strict Cluster subset—rank 1–10, strength 200–299, and target 2–10% away—only MSFT, TSLA, and META qualified. All three post-confirmation spreads were positive. Their median spread return was **+204.84%** under terminal-confirmation timing and **+190.29%** under first prospective one-source timing. This is only three observations, including one pending record.

The cache-only artifact is `data/governance/cipher_signal_only/latest_confirmation_entry_research.json`.

## Option-path giveback

Option paths contain substantially more favorable movement than expiration/latest marks retain.

### ATM options

- 335 tradeable completed-session cases
- 86.9% reached a positive return at some point
- 49.0% were still positive at the latest/final mark
- 184 reached at least +25%; 52 of those later ended nonpositive
- 145 reached at least +50%; 29 later ended nonpositive
- 91 reached at least +100%; 10 later ended nonpositive

### Target-strike options

- 341 tradeable completed-session cases
- 85.0% reached a positive return at some point
- 39.9% were still positive at the latest/final mark
- 207 reached at least +25%; 86 later ended nonpositive
- 165 reached at least +50%; 56 later ended nonpositive
- 108 reached at least +100%; 24 later ended nonpositive

This justifies formal exit-policy research. It does not yet establish that a fixed +25%, +50%, or +100% exit is optimal because those paths have not been tested under a governed competing-policy family.

## Flash and Agentic completed one-session evidence

| Source and direction | Observations | Positive directional returns | Average directional return | Average excess vs SPY |
|---|---:|---:|---:|---:|
| Flash bullish | 39 | 69.2% | +1.50% | +0.70% |
| Flash bearish | 33 | 51.5% | -0.75% | -0.22% |
| Agentic bullish | 27 | 66.7% | +0.96% | -0.25% |
| Agentic bearish | 13 | 53.8% | +0.52% | +0.85% |

Bullish Flash remains the strongest populated standalone direction rule. Agentic bullish states remain more useful as confirmation than as an unrestricted standalone rule because their average return is positive but their average SPY-relative return is slightly negative.

### Stronger setup families

| Source | Setup | Observations | Positive directional returns | Average return | Average excess vs SPY |
|---|---|---:|---:|---:|---:|
| Flash | Rejection reversal | 8 | 87.5% | +2.25% | +1.78% |
| Flash | Floor bounce | 15 | 73.3% | +2.11% | +1.14% |
| Flash | Momentum push | 21 | 66.7% | +1.36% | +1.25% |
| Agentic | Floor bounce | 5 | 80.0% | +3.62% | +2.16% |
| Agentic | Momentum push | 11 | 72.7% | +3.39% | +2.52% |

### Weak diagnostics

- Flash breakdown attempt: 5 observations, 20.0% positive, -2.14% average
- Flash ceiling rejection: 11 observations, 45.5% positive, -1.12% average
- Agentic rejection reversal: 13 observations, 46.2% positive, -0.96% average

All setup samples remain exploratory.

## August 6 prospective state

The August 6 session is deliberately excluded from completed-session statistics.

### Strict post-hoc Cluster cohort

The current partial-session states satisfying bullish, rank 1–10, strength 200–299, and target 2–10% away are:

| Ticker | Rank | Strength | Spot | Target | Target distance | Reconstructed expiration |
|---|---:|---:|---:|---:|---:|---|
| CVNA | 1 | 299 | 69.40 | 73.00 | 5.19% | Aug. 14, 2026 |
| ZS | 5 | 261 | 160.38 | 165.00 | 2.88% | Aug. 14, 2026 |
| GEV | 7 | 273 | 1,027.02 | 1,050.00 | 2.24% | Aug. 14, 2026 |
| BURL | 8 | 272 | 370.33 | 380.00 | 2.61% | Aug. 14, 2026 |
| CRWD | 9 | 267 | 206.37 | 217.50 | 5.39% | Aug. 14, 2026 |
| BIDU | 10 | 280 | 109.31 | 117.00 | 7.04% | Aug. 14, 2026 |
| LMND | 10 | 267 | 52.63 | 55.00 | 4.50% | Aug. 14, 2026 |

Only a small subset had a tradeable post-alert option bar at the report cutoff. These are prospective observations, not completed trades or validated recommendations.

### Current cross-source Cluster states

- **MSFT:** Cluster, Flash, and Agentic bullish; Cluster rank 23; Aug. 10 reconstructed expiration; still pending.
- **META:** Cluster and Flash bullish; Cluster rank 33; Aug. 10 reconstructed expiration; still pending.
- **GOOGL:** Cluster bullish versus Flash bearish; rank 11; unresolved conflict; Aug. 10 reconstructed expiration.
- **NVDA:** Cluster and Flash bearish; rank 6; Aug. 10 reconstructed expiration. The broader historical bearish-Cluster weakness remains an important counterweight.

These terminal states can change during the session. The immutable prospective record is tied to its report cutoff.

## Governance and limitations

- Cluster expiration is reconstructed from provider contract metadata because the captured Cluster cards do not directly store expiration.
- Entry uses the first traded one-minute bar after the alert, not bid/ask midpoint or a guaranteed executable fill.
- Missing post-alert trades remain unavailable rather than being imputed.
- Percentage returns on low-premium target options are highly skewed.
- Current-session intraday marks are separated from completed-session evidence.
- Post-hoc filters are labeled and frozen for future observation; they are not promoted as validated historical strategies.
- Only 13 Cluster cases are finalized at expiration.
- No result authorizes a paper or live order.

## Implementation artifacts

- `scripts/run_cipher_complete_observations.py`
- `scripts/run_cipher_confirmation_entry_research.py`
- `scripts/run_cipher_signal_only_loop.py`
- `scripts/manage_cipher_signal_only_loop.py`
- `core/research_platform/cipher_signal_overlay.py`
- `core/historical_options_download.py`
- `tests/test_cipher_signal_only.py`
- Runtime report: `data/governance/cipher_signal_only/latest_complete_observations.json`
