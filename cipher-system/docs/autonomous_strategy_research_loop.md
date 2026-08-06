# Autonomous Strategy Research System

## Purpose

Cipher separates research automation from infrastructure healing:

1. `build_test_healing` validates source changes and performs bounded mechanical recovery.
2. `strategy_research_loop` generates, backtests, rejects, and records strategy candidates.
3. `locked_temporal_validation` evaluates candidate families frozen before new data was downloaded.
4. `locked_2026_ytd_validation` uses pre-period warmup bars while scoring only the declared 2026 window.
5. `robustness_stress` applies higher costs, leave-one-symbol-out checks, block bootstrap, and monthly consistency tests.
6. `annual_regime_stability` evaluates priority candidates independently in each calendar year from 2016 through 2026 YTD.
7. `cross_period_consensus` compares the same immutable candidate IDs across market periods.
8. `factor_rotation_research` evaluates factor and macro ETF rotation without single-stock survivorship dependence.
9. `regime_allocator_research` combines fixed strategy components using trailing-only allocation rules.
10. `research_failure_attribution` explains rejections by benchmark, risk, statistical, temporal, cost, and universe gates.
11. `auxiliary_research_refresh` reruns expensive auxiliary branches only when their exact data or source inputs change.
12. `option_research_refresh` maintains execution-aware historical-options evidence where the available data supports it.

None of these components can promote a strategy, paper trade, place an order, modify research gates, or authorize live execution.

## Equity data architecture

### Original exploratory panel: 2023–2025

- Dataset: `holdout_c_price_only_original_nine_2023_2025`
- Dataset ID: `ds_380c76da95f0c3787529c6b8`
- Symbols: AAPL, GE, IWM, MSFT, NVDA, QQQ, SPY, XLE, XLF
- Source: Alpaca SIP one-minute partitions aggregated to daily OHLC
- Role: `exploratory_development_only_not_final_holdout`

This period was already explored before the autonomous loop existed. It can support strategy development and rejection, but not an untouched final-holdout claim.

### Broad development panel: 2020–2022

- Dataset: `alpaca_broad_daily_2020_2022_development_v1`
- Dataset ID: `ds_532bf7c42462c24a7c1a0a1f`
- 38 assets
- 28,728 daily rows
- 756 sessions
- Alpaca SIP adjusted daily bars
- Role: `broad_phase3_development_only`

The universe includes major indexes, sector ETFs, bonds, credit, commodities, international ETFs, and large liquid equities. It is maintained in a separate state directory so its candidate feedback does not alter the original branch.

### Locked temporal validation: 2016–2019

- Dataset: `alpaca_broad_daily_2016_2019_locked_validation_v1`
- Dataset ID: `ds_fb1e8d9aeb51f12407b08123`
- 38 assets
- 38,228 daily rows
- 1,006 sessions
- Role: `locked_temporal_validation_fixed_pre_download_candidates_only`

Before this dataset was downloaded, the exact 92-candidate family was frozen by count and content hash. The locked run:

- evaluates all 92 candidates together;
- applies Holm correction across all 92;
- uses four chronological folds;
- generates no adaptive children;
- does not feed results back into development;
- cannot promote or execute.

The original request began at 2010, but the available adjusted Alpaca SIP response started on January 4, 2016. The misleading 2010 label was superseded in the registry by the corrected dataset and an immutable audit event.

## Strategy families

The deterministic seed catalog contains 84 candidates across 17 families, with bounded descendants generated from qualifying near-misses:

- SMA and EMA trend;
- time-series momentum;
- Donchian and Keltner breakouts;
- RSI and Bollinger mean reversion;
- trend pullback;
- low-volatility breakout;
- short-term reversal;
- regime switching;
- multi-signal voting ensembles;
- cross-sectional momentum;
- cross-sectional reversal;
- cross-sectional low-volatility selection;
- risk-on rotation;
- pair-spread z-score mean reversion.

The original branch has tested 160 candidates. The broad 2020–2022 branch has tested at least 104 candidates and can continue through its separate adaptive queue.

## Backtest contract

Each equity candidate uses:

- information available through the session close;
- execution at the next session open;
- explicit slippage on entry and exit;
- no future contract or outcome information;
- chronological walk-forward folds;
- deterministic sign-flip tests;
- Holm multiple-testing correction;
- minimum sample, profit-factor, and drawdown gates;
- best-trade exclusion;
- benchmark-relative walk-forward checks;
- permanent registration of both passing and failing experiments.

Market-neutral candidates use a cash benchmark. Long-only panel candidates use SPY or the defined panel benchmark. A screening PASS remains research evidence only.

## Locked validation outcome

The complete 92-candidate locked run produced:

- PASS: 3
- FAIL: 89
- ERROR: 0

The passing candidates were:

1. SMA 50/200 trend
2. SMA 20/150 trend
3. A slow ensemble requiring two votes among a 50/200 trend, an 80-session breakout, and RSI-based evidence

The five-session cross-sectional reversal that passed in 2023–2025 did not survive locked validation. It exceeded the drawdown threshold and failed family-wide adjusted significance.

### Missing-year bridge and 2026 YTD holdout

A separate 2023 broad panel closes the calendar gap:

- Dataset ID: `ds_3e9b83d533c645ea23e1abf8`
- 38 assets
- 9,500 rows
- 250 sessions
- Role: cross-universe development only, not an independent holdout

The 2026 YTD holdout contains 2024–2025 warmup plus a locked evaluation window:

- Dataset ID: `ds_f20f2e15e7d1041ce6a1858d`
- 38 assets
- 24,662 total rows
- 649 total sessions
- Evaluation window: January 2, 2026 through August 4, 2026
- Scoreable sessions: 147
- Frozen candidate identities: 194

Positions reset at the start of the evaluation window. Warmup bars may form indicators but are excluded from positions, trades, equity curves, walk-forward folds, and statistical tests.

## 2026 YTD locked outcome and stress tests

The 194-way family-wide validation produced:

- PASS: 5
- FAIL: 189
- ERROR: 0

The five screening winners were two Bollinger-reversion configurations, two RSI-reversion configurations, and one regime-switch configuration. The previous 2016–2022 ensemble leader returned positively in 2026 but failed benchmark-relative fold consistency.

A stricter stress suite tested the five winners plus the prior consensus leader under 10, 25, and 50 basis points per side, 38 leave-one-symbol-out runs, monthly consistency, and deterministic five-session block bootstrap. Only one candidate cleared all four stress gates: RSI(2), entry below 5, exit above 50, without a trend filter. This remains a short-horizon robustness result, not promotion evidence.

Calendar-year testing across continuous 2016–2026 data then evaluated 15 priority candidates. No candidate met the all-weather stability rule. The RSI(2) candidate and other 2026 winners are therefore treated as regime-specific rather than durable universal strategies.

## Focused 2025/2026 recent-regime branch

A dedicated branch now prioritizes the recent market regime rather than continuing to spend most adaptive capacity on 2020–2022. It uses 2024 only to form component and selector history, then runs the same monthly point-in-time selection process continuously through 2025 and 2026 YTD:

- January 2025 decisions use only 2024 returns;
- each later month uses only sessions before that month;
- component signals execute at the next session open with 10 basis points per side;
- selector turnover is separately stressed at 10, 25, and 50 basis points;
- SPY is the fallback when no candidate has a positive trailing score.

The component pool contains 14 governed candidates: every strategy that had passed the 2023–2025 or 2026-YTD screen at the August 4, 2026 freeze, plus the two candidates that had passed two older periods. Their exact IDs and parameters are frozen; unrelated phase-three discoveries cannot alter the pool or trigger a rerun. The pool and selector family are outcome-informed and are not treated as an untouched holdout.

Eight monthly selectors are tested across 63- and 126-session trailing windows, including top-one, top-two, family-balanced trend/reversion, and SPY core-satellite configurations.

The stricter two-year result rejects the apparent 2026 leader:

- Leader: `monthly_top1_63d_return`
- Rolling 2025 return: approximately -1.7%
- 2025 excess versus SPY: approximately -19.4 percentage points
- 2026 YTD return through August 5: approximately 34.8%
- 2026 excess versus SPY: approximately 21.0 percentage points
- Combined 2025–2026 return: approximately 32.5%
- Combined excess versus SPY: approximately -1.4 percentage points
- Full selector passes: 0

The strategy is therefore a 2026 regime effect rather than a durable recent two-year system. It also exceeds the 15% combined drawdown ceiling, fails monthly consistency, fails 2025 cost stress, and does not establish family-wide block-adjusted significance.

A second fixed family tests 16 prior-month market gates: two base selectors crossed with eight conventional conditions using only prior-session SPY trend, drawdown, realized volatility, and cross-sectional dispersion. No adaptive descendants are generated.

The strongest gate is `active_when_dispersion_high` applied to `monthly_top1_63d_return`:

- Rolling 2025 return: approximately 9.6%
- Rolling 2026 YTD return through August 5: approximately 22.3%
- Combined excess versus SPY: approximately 0.1 percentage points
- Combined maximum drawdown: approximately 18.7%
- Gate passes: 0 of 16

It still trails SPY in 2025, exceeds the drawdown ceiling, fails month consistency, and lacks adjusted significance. The current August gate is active because prior 21-session cross-sectional dispersion is above its trailing median; its effective selector remains the ten-session cross-sectional reversal candidate `candidate_450ab714a604e63bc221ccfb`, with CAT and META active at the August 5 cutoff. This is a read-only research observation, not an order recommendation.

The underlying ten-session reversal component was separately rerun at 25 basis points per side. It returned approximately 27.1% in 2025 and 50.5% in 2026 YTD. All 38 exact leave-one-symbol-out reruns remained positive in both periods; the worst returns were approximately 4.5% in 2025 and 33.0% in 2026 YTD. Removing every single stock still left positive returns, although combined drawdown rose to approximately 25.7%. Removing mega-cap technology also remained profitable but raised combined drawdown to approximately 26.4%. The top symbol, SOXX, supplied about 19.2% of positive normalized trade-return points, below the 35% concentration flag. This supports a broad component effect but not an adequately controlled portfolio construction.

Every completed market session now receives one canonical prospective snapshot containing all selector decisions, gate decisions, component definitions, and active symbols. The first snapshot for a session is immutable. Later code changes preserve the original and write a separate conflict artifact rather than rewriting history.

Prospective outcomes are scored separately from the backtest. A one-session score requires two future opens after the snapshot session: the next open is the frozen-basket entry and the following open is the exit. Five- and 21-session horizons use the same rule. Closes and partial intraday marks are never substituted. As of the August 5 dataset, two snapshots produce nine pending horizon observations and zero matured observations; the August 4 one-session result cannot mature until an August 6 open is available.

### Flash, Agentic, and Cluster overlay

The normalized Cipher browser capture can help, but its defensible role is prospective confirmation and risk context rather than historical alpha generation. Globally deduplicated signal episodes currently cover eight market sessions from late July through August 5, 2026. Raw CSV retry rows are not treated as independent observations: the immutable `signal_id` episode is the unit of analysis, and only its earliest regular-session observation defines direction, geometry, and setup.

Flash and Agentic episodes must be directional, geometry-valid, and actionable. Cluster is context-only because its normalized cards do not supply the same invalidation/actionability contract. The short legacy pools remain separate: the July 23 Flash-Agentic simulation has only 23 closed trades, while the frozen Cluster/Kronos sample has 50 outcomes. Cluster directional accuracy is approximately 44% overall, but rises to approximately 52% with positive average directional return when Cluster and Kronos/context agree. This supports agreement-filter research, not a standalone Cluster strategy.

Six non-adaptive policies are frozen for every recent reversal basket: the unfiltered baseline, Agentic bearish-conflict avoidance, Flash bearish-conflict avoidance, Cluster bearish-conflict avoidance, strict bullish/no-bearish cross-source consensus, and bearish-pressure confirmation for a long reversal interpretation.

For the August 5 CAT/META basket, Agentic conflict avoidance retains CAT only; strict consensus falls back to SPY; bearish-pressure confirmation retains META only; Flash and Cluster conflict avoidance currently match the unfiltered CAT/META basket. All six policies are scored at 1-, 5-, and 21-session future-open horizons. The initial overlay therefore has 18 pending observations and zero matured observations. No 2025 overlay result is reported because these normalized captures did not exist then.

The overlay has an independent fingerprint consisting of the frozen recent snapshot, the exact session's normalized signal files, the canonical price dataset, policy code, and the shared future-open evaluator. Later intraday files cannot rewrite an earlier market session; changed content is preserved as an immutable conflict artifact.

After 5 p.m. New York time on weekdays, the loop checks whether the recent daily panel has advanced. It makes at most one download attempt per local calendar day. New data snapshots are immutable and separately registered; holidays or unchanged provider data are recorded as `not_advanced`. The older 2020–2022 adaptive branch receives only two candidates per hourly cycle by default, while recent-regime refresh runs first.

## Cross-period consensus

Cross-period identity is the immutable `candidate_id`, not `strategy_id`, because each dataset may use a different preregistered walk-forward policy and therefore a different StrategySpec.

Implementation snapshot captured after the latest activation cycle; the hourly matrix continues advancing, so `data/governance/cross_period_strategy_matrix.json` is authoritative:

- Candidate identities: 206
- Tested in at least three periods: 108
- Tested in all four periods: 58
- Passed locked 2016–2019: 3
- Passed broad 2020–2022: 54
- Passed original 2023–2025: 7
- Passed locked 2026 YTD: 5
- Passed at least two periods: 2
- Passed at least three periods: 0
- Passed all four periods: 0

The two two-period survivors are the slow ensemble and the plain SMA 50/200 trend rule. Both passed 2016–2019 and 2020–2022, and both failed 2023–2025 and 2026 YTD.

The slow ensemble parameters are:

```text
fast SMA: 50
slow SMA: 200
breakout lookback: 80
RSI period: 7
RSI entry: 30
minimum votes: 2
```

The ensemble passed 2016–2019 and 2020–2022, but failed 2023–2025 because the recent-period sample contained only 21 trades and did not pass adjusted significance or walk-forward requirements. It also failed the 2026 YTD fold-consistency gate despite positive return and strong profit factor.

The SMA 50/200 rule shows the same regime boundary: approximately 41.1% in 2016–2019 and 25.5% in 2020–2022, followed by failures in 2023–2025 and 2026 YTD. Neither candidate is a validated universal strategy.

## Factor and macro ETF rotation

A separate ETF-only dataset removes dependence on a hand-picked surviving single-stock universe:

- Dataset: `alpaca_factor_macro_etf_daily_2016_2026_development_v1`
- Dataset ID: `ds_796df562a29d2b01d2e1ca24`
- 41 ETFs
- 109,101 adjusted daily rows
- 2,661 sessions
- January 4, 2016 through August 4, 2026
- 11 immutable provider pages
- Role: `factor_macro_etf_rotation_development_only_not_independent_holdout`

The universe covers broad equity indexes, style and factor ETFs, sectors, international markets, Treasury duration, credit, cash equivalents, commodities, metals, real estate, and the U.S. dollar. The initial 16-rule strategy grid was content-hashed before the download. The exact IDs and full grid are verified against canonical raw registry metadata before every factor run; the runner aborts if the initial grid or freeze hash changes. Twenty-four bounded descendants were then added only from structural near-misses, producing 40 specifications and 38 unique return paths.

The branch evaluates:

- 63-, 126-, and 252-session momentum;
- 12-minus-1 momentum;
- risk-adjusted momentum;
- low-volatility selection;
- absolute and dual momentum;
- trend-filtered rotation;
- category-balanced rotation;
- risk-on/risk-off allocation;
- SPY core-satellite structures;
- trailing relative-strength overlays;
- market-trend overlays;
- portfolio-level volatility controls.

Each rule uses close information and executes at the next open, with 10 basis points per side in the primary run and 50 basis points per side in the stress run. It also faces four chronological folds and leave-one-category-out tests.

Current result:

- Specifications: 40
- Effective unique return paths: 38
- Screening passes: 0
- Leader: `momentum_252_top3_relative126`
- Leader total return: approximately 347%
- Leader excess over SPY: approximately 125 percentage points
- Leader positive excess folds: 3 of 4
- Leader maximum drawdown: approximately 36.6%

The leader remains rejected because drawdown exceeds the 25% ceiling, its 21-session block test does not establish stable excess-return confidence, and category-removal stress remains too fragile. High compounded return alone is not treated as validation.

## Trailing-only regime allocator

A second auxiliary branch combines eight fixed component strategies: slow trend, slow ensemble, RSI and Bollinger mean reversion, regime switching, Donchian and Keltner breakout, and cross-sectional reversal. The allocator uses only trailing component returns and may hold cash or a passive baseline. Component execution costs remain embedded, and allocation changes incur a separate switching cost.

The branch tests static blends, trailing Sharpe/Sortino/return-to-drawdown selection, passive-regime switches, benchmark-aware selection, and SPY core-satellite structures across the full, ETF-only, and equity-only universes.

Current result:

- Allocator specifications: 22
- Effective unique return paths: 22
- Screening passes: 0
- Leader: `benchmark_aware_126d_21d_top1_total_return`

Most allocator variants were profitable and survived 50-basis-point switching stress, but they underperformed SPY too consistently. The branch therefore rejects defensive profitability that does not clear the benchmark contract.

## Multiple-testing deduplication and failure attribution

Both auxiliary branches hash their complete daily return paths. Identical paths count as one effective hypothesis for Holm correction, even when different configuration names produce the same trades. This prevents aliases from inflating the multiple-testing family while preserving every specification in the audit record.

The failure-attribution artifact currently identifies `benchmark_consistency` as the dominant blocker, followed by risk quality and statistical confidence. The correct next evidence dependency is more genuinely out-of-sample sessions or independent universes—not additional local parameter descendants or weaker gates.

## Fingerprinted auxiliary refresh

The expensive factor and allocator studies are guarded by an operational fingerprint over:

- the registered canonical Parquet inputs;
- the exact factor and allocator source files;
- the research runners;
- the failure-attribution logic.

Each auxiliary branch has its own fingerprint. A factor-only change therefore cannot trigger an unrelated regime-allocation rerun. Failure attribution has a third fingerprint over the completed branch reports. An hourly cycle reruns only the branches whose inputs changed; otherwise each branch returns `not_due_inputs_unchanged`. It cannot edit source, promote a result, paper trade, or execute.

## Historical-options expansion

Two bounded SPY weekly archives were added for 2025:

### Calls

- Near-the-money calls
- Approximately 5–10 DTE
- 75,113 one-minute option bars
- 40 point-in-time contract selections
- 105 immutable provider pages

### Puts

- Near-the-money puts
- Approximately 5–10 DTE
- 70,339 one-minute option bars
- 40 point-in-time contract selections
- 104 immutable provider pages

Historical NBBO, IV, and Greeks are absent. These archives are trade-bar execution approximations only.

The existing put strategy lab evaluated 39 fixed cash-secured-put and put-vertical variants under base, worse, and severe execution assumptions. Strict point-in-time and fill requirements left only five distinct usable decision snapshots, so the output is explicitly marked:

`NO_FORWARD_VALIDATED_EXPLORATORY_CANDIDATE`

Several rows were profitable on three to five trades, but that sample is too sparse for a research claim.

The broader pre-existing EOD options branch continues to evaluate 10,296 variants over monthly walk-forward folds. Its current evidence includes one base/worse degradation survivor and zero severe-execution survivors.

## Automation

Each daemon cycle now performs:

1. Original-panel candidate batch, if candidates remain.
2. Recent 2025/2026 data-freshness check, rolling-selector/gate refresh, immutable prospective snapshot update, and Flash/Agentic/Cluster overlay refresh.
3. Future-open prospective scoring when snapshot or market-data inputs change.
4. Exact recent-component concentration audit when its dataset or code fingerprint changes.
5. Broad 2020–2022 phase-three batch, reduced to two candidates by default.
6. Historical-options refresh when source inputs change.
7. Factor-rotation and regime-allocation refresh only when their data/code fingerprint changes.
8. Gate-level failure-attribution refresh when auxiliary evidence changes.
9. Cross-period matrix rebuild using candidate identity.
10. Status and immutable experiment updates.

Run one combined cycle:

```bash
.venv-research-py312/bin/python \
  cipher-system/scripts/run_strategy_research_loop.py --once --batch-size 8
```

Start the hourly daemon:

```bash
.venv-research-py312/bin/python \
  cipher-system/scripts/manage_strategy_research_loop.py start \
  --run-on-start --interval-seconds 3600 --batch-size 8
```

Check status:

```bash
.venv-research-py312/bin/python \
  cipher-system/scripts/manage_strategy_research_loop.py status
```

## Evidence locations

- Original latest cycle: `data/governance/strategy_research/latest_strategy_research_cycle.json`
- Broad phase-three latest cycle: `data/governance/strategy_research_phase3/latest_strategy_research_cycle.json`
- Locked 2016–2019 validation: `data/governance/strategy_research_validation/latest_locked_broad_validation.json`
- Locked 2026 YTD validation: `data/governance/strategy_research_2026_ytd/latest_2026_ytd_locked_validation.json`
- 2026 YTD robustness: `data/governance/strategy_research_2026_ytd/latest_2026_ytd_robustness.json`
- Annual regime stability: `data/governance/annual_regime_stability.json`
- Cross-period matrix: `data/governance/cross_period_strategy_matrix.json`
- Recent 2025/2026 research: `data/governance/recent_regime_research.json`
- Recent branch status: `data/governance/strategy_research/latest_recent_regime_status.json`
- Recent canonical data snapshots: `data/historical_equities/recent_regime_snapshots/`
- Recent immutable prospective decisions: `data/governance/recent_regime_prospective/snapshots/`
- Recent prospective conflicts: `data/governance/recent_regime_prospective/conflicts/`
- Recent prospective evaluation summary: `data/governance/recent_regime_prospective/latest_evaluation_summary.json`
- Recent immutable prospective outcomes: `data/governance/recent_regime_prospective/evaluations/`
- Recent component robustness: `data/governance/recent_component_robustness.json`
- Flash/Agentic/Cluster overlay report: `data/governance/cipher_signal_overlay_research.json`
- Immutable signal-overlay snapshots and outcomes: `data/governance/cipher_signal_overlay/`
- Factor/macro ETF registration: `data/historical_equities/factor_etf_panel_v1/registration.json`
- Factor rotation research: `data/governance/factor_rotation_research.json`
- Regime allocator research: `data/governance/regime_allocator_research.json`
- Auxiliary failure attribution: `data/governance/research_failure_attribution.json`
- Auxiliary refresh status: `data/governance/strategy_research/latest_auxiliary_research_status.json`
- Broad-panel registration: `data/historical_equities/broad_research_panel_v1/broad_panel_registration.json`
- Corrected registration: `data/historical_equities/broad_research_panel_v1/broad_panel_registration_correction.json`
- SPY call archive: `data/historical_options/spy_weekly_7d_call_2025/`
- SPY put archive: `data/historical_options/spy_weekly_7d_put_2025/`
- Put strategy report: `data/historical_options/spy_weekly_7d_put_2025/strategy_lab/historical_option_strategy_report.json`
- Canonical registry: `data/governance/research_registry.sqlite`
- Daemon log: `logs/strategy_research_loop.log`

## Safety boundary

The research system cannot:

- submit broker orders;
- access account or order endpoints;
- automatically promote strategies;
- start paper or prospective execution;
- invoke live LEAN execution;
- relax statistical gates;
- relabel explored data as untouched;
- generate adaptive candidates from the locked validation dataset;
- rewrite its own source code;
- commit or push Git changes.
