# Cipher product and hackathon roadmap

Date: 2026-08-17 UTC
Scope: private, read-only stocks/options research plus auditable paper simulation

## Outcome and product thesis

Cipher should become a calm daily workstation for an active stocks-and-options
trader: discover an opportunity, inspect the evidence, compare an option
structure, record a thesis, and observe the result. Its differentiator is not
another buy/sell score. It is the traceable link from market observation to
research decision to prospective outcome.

The hackathon version should be the same product with a clearer demonstration
layer—not a separate throwaway application. The agent fleet autonomously gathers
and challenges evidence; a human owns every capital decision. No broker or order
surface belongs in this roadmap.

```text
Morning Brief → Scanner → Ticker Workbench → Option Structure → Paper/Journal
       ↑              shared freshness, provenance, and event-time truth       ↓
       └──────────── prospective outcomes + research agent review ─────────────┘
```

## Current evidence-backed state

### What is already strong

- A single same-origin browser app fronts a loopback-only Python research API.
- Alpaca OPRA/SIP is the active source, with explicit IEX/indicative fallbacks.
- Options chain, quotes/trades, Greeks, IV, volume, OI/date, underlying bars,
  GEX/VEX, flow inference, news/context, screens, journal, manual holdings, paper
  portfolios, and experiment governance already exist.
- GEX capture runs as a managed service and preserves missing gamma/OI as
  unknown. It remains explicitly labelled a public-OI heuristic.
- The Next.js UI has a coherent workflow, typed API layer, request coordination,
  authenticated access, atomic static publication, mobile tests, and saved
  workspaces.
- A Google ADK prototype already defines market-structure, flow, historical,
  validation, and adversarial agents over an allowlisted read-only client.
- Backups, restore verification, provider telemetry, retention jobs, scheduled
  research, Discord summaries, and operator status already exist.
- Live execution is absent. Promotion stops at `LIVE_REVIEW_REQUIRED`.

### New prospective evidence registered today

Two rules are frozen before the August 17 session:

1. `tsla_stable_wall_rejection_v1` — the descriptive TSLA wall-rejection
   cluster, unchanged, requiring positive/stable public-OI GEX, a qualified wick,
   a session impulse, modeled 1.5R target, conservative stop-first handling, and
   at least 20 closed prospective observations before promotion can be discussed.
2. `spartan_weekly_radar_2026_08_17` — the supplied SPY, AAPL, AMZN, NFLX, META,
   GOOGL, MU, TSLA, NBIS, and ARTW conditional levels, with the stated August 21
   option strikes where contracts and executable quotes are actually observed.

Both use only fully closed five-minute candles with a three-minute observation
window. They do not backfill missed signals. Option entries cross the observed
ask plus slippage; exits cross the observed bid plus slippage and commission.
Unavailable contracts or quotes stay unavailable. They have no execution
authority and now appear in Paper Portfolios as prospective programs.

### Highest-impact gaps

| Gap | Why it matters | Correct solution | Acceptance evidence |
|---|---|---|---|
| Strategy truth is fragmented | Labs can look impressive while using different fills, clocks, costs, and samples | One versioned strategy specification and one event-time evaluator for historical, replay, and prospective modes | Same fixture produces identical signals; parity report explains any fill differences |
| Prospective samples are tiny | Four selected historical TSLA examples are not validation | Pre-register rules, count every eligible opportunity and non-opportunity, retain blocked/missing cases, and require walk-forward plus prospective gates | 20+ TSLA observations; confidence intervals; no rule edits within a cohort |
| Option history is captured but uneven | A 70 GB Tradier store and large JSONL chain archives are valuable but expensive and difficult to query | Normalize hot observations into partitioned Parquet/SQLite indexes, immutable manifests, retention tiers, and coverage tables | Query latency, disk runway, per-ticker/session/contract coverage, restore test |
| UI still exposes too many peer tools | A normal trader must decide which panel to open before knowing the workflow | Promote five daily jobs; place specialist labs behind progressive disclosure; use one ticker workspace with tabs and shared context | First-use task tests, mobile/keyboard passes, reduced navigation choices |
| Night Vision and Scanner can disagree | Separate computations weaken trust | Share levels, session calendar, signal IDs, snapshots, and explanation schema; clicking a scan replays the exact feature snapshot | Scanner-to-chart parity fixture and linked provenance ID |
| Backtests can encourage selection bias | Searching many niche variants makes the best result likely to be noise | Experiment registry, frozen train/validation/test periods, walk-forward results, cost sensitivity, bootstrap/deflated selection metrics, and prospective quarantine | Every displayed result names universe, period, costs, trial family, and holdout status |
| Agent prototype is not yet a product workflow | A demo fleet without evaluated tasks is architectural theater | Let the supervisor produce a bounded research brief inside the existing workspace, with tool traces, citations, conflict resolution, and approval state | Golden task set, groundedness checks, failure injection, latency/cost report |
| External demo access raises security risk | The private terminal contains credentials and private captured data | Keep production private over authenticated Tailscale; publish only a sanitized demo mode backed by frozen fixtures | Secret scan, route allowlist, auth test, no private artifacts in demo image |

## Delivery phases and audit gates

Every phase ends with: Python and Node suites, lint/typecheck/build, authenticated
browser journeys, live read-only API smokes, provider-failure tests, safety grep,
storage/integrity checks, and a dated audit that carries unresolved findings into
the next phase.

### Phase 0 — prospective truth and operations (now through the first sessions)

Deliver:

- Keep the TSLA and August 17 radar monitors active and immutable.
- Show registered/monitoring/collecting/completed state, signals, option-selection
  failures, targets, stops, marks, exits, and cohort progress inside Cipher.
- Add Discord daily deltas for these programs, explicitly separating underlying
  outcomes from option P/L.
- Record monitor heartbeat, last closed candle, provider latency, missing-input
  reason, and last successful mark.
- Add a frozen hash of each program configuration to every signal.
- Resolve the week on August 21 without retrospective rule changes.

Gate:

- No pre-registration signal is present.
- Every market session has a run/coverage record even when signals are zero.
- Replaying the ledger is deterministic.
- The monitor resumes safely after restart without duplicating signals.
- Safety scan confirms no order or broker client in active code.

### Phase 1 — simplify the daily trader experience

Deliver:

- Make the default navigation task-based: **Today, Discover, Analyze, Plan,
  Review**. Keep individual engines available under Labs/System.
- Build one Ticker Workbench that combines chart, key levels, Night Vision,
  relevant flow, GEX coverage, chain/structure research, active alerts, and notes.
- Replace dense card grids with a prioritized feed: urgent data problems, active
  paper observations, high-quality setups, scheduled events, then watchlists.
- Use one consistent status language: observed, inferred, modeled, stale,
  unavailable, research-only.
- Add empty, loading, stale, provider-error, and partial-coverage states to every
  primary surface; meet keyboard, contrast, and 390 px layout checks.

Gate:

- A new user can complete discover → validate → compare structure → journal in
  five minutes without opening a lab.
- No primary page shows an unqualified “confidence” percentage as win probability.
- Browser tests cover the five task paths and failure states.

### Phase 2 — unify Scanner, Night Vision, and research evidence

Status on 2026-08-17: shared evidence identity, quality/rejection funnel,
three-setup comparison, and frozen scanner-to-Night-Vision exposure replay are
implemented and deployed. Bar-derived session-level capture, provider-backed
expected move/catalyst/spread evidence, and recorded performance budgets remain
open; see `docs/audits/phase_v4_2_shared_evidence_and_comparison_2026-08-17.md`.

Deliver:

- Define a shared `EvidenceSnapshot` with event/capture time, provider, session,
  freshness, coverage, feature IDs, and missing reasons.
- Move scanner candidates and Night Vision overlays onto the same session/level
  calculation and stable signal ID.
- Redesign Scanner as a funnel: universe → data-qualified → liquidity-qualified
  → setup-qualified → ranked. Preserve rejection reasons and denominator counts.
- Redesign Night Vision around price first: lightweight OHLCV, visible RTH/PM
  context, legible level bands, optional overlays, exact signal replay, and a
  compact evidence drawer rather than simultaneous charts/cards.
- Add a comparison tray for up to three setups with regime, liquidity, expected
  move, invalidation, catalyst, option spread, and evidence gaps.

Gate:

- Scanner and chart agree on all golden fixtures.
- No level moves when replaying a frozen snapshot.
- Performance budgets hold for first render, ticker switch, and a 500-name scan.

### Phase 3 — make backtesting scientifically defensible

Deliver:

- Create a versioned strategy schema for eligibility, trigger, entry clock,
  target, invalidation, hold, sizing, costs, data requirements, and ticker/regime
  scope.
- Use the same evaluator for historical bar tests, captured-option replay, and
  prospective fronttests. Historical mode may not consume future information.
- Add corporate-action adjustment policy, exchange calendar/DST handling,
  conservative same-bar collision rules, quote-age limits, spread/slippage
  sensitivity, and missing-data exclusions.
- Require train/validation/test manifests, rolling walk-forward folds, per-ticker
  and per-regime breakdowns, bootstrap intervals, drawdown/run concentration,
  exposure/time-in-market, and multiple-testing controls.
- Maintain an experiment lineage graph: hypothesis → configuration hash → data
  manifest → result → prospective cohort → governance status.
- Search niche strategies by economically motivated families rather than blind
  parameter grids. TSLA wall rejection remains quarantined until its cohort gate.

Gate:

- Look-ahead, survivorship, session-boundary, split, option-expiration, crossed
  market, quote-staleness, and both-hit-same-bar fixtures pass.
- A result without holdout and trial-family metadata cannot be promoted or shown
  as validated.
- Historical and prospective evaluators demonstrate signal parity on identical
  frozen inputs.

### Phase 4 — data plane and storage hardening

Deliver:

- Publish a coverage catalog for equities, option quotes/trades/chains, OI dates,
  Greeks/IV, GEX snapshots, flow, earnings, news, and corporate actions.
- Separate hot operational SQLite from immutable partitioned archives; compact
  raw Tradier/chain data and index by session/ticker/contract/event time.
- Enforce raw/warm/derived retention policies with checksummed manifests and a
  tested restore path. Never delete the only copy of a captured observation.
- Add provider budgets, adaptive polling, 429 backoff, circuit breakers, data
  reconciliation, and lag/coverage alerts.
- Make Yahoo/Finviz earnings context explicitly supplemental; Alpaca remains the
  market-data authority. Conflicts remain visible.

Gate:

- Disk runway is measurable and above the declared threshold.
- Representative historical queries meet latency budgets.
- A clean restore reconstructs indexes and reproduces a selected experiment hash.

### Phase 5 — dedicated research agent inside the product

Deliver:

- Embed the existing specialist fleet into Research Desk and Ticker Workbench.
- Schedule a premarket universe pass, an intraday change review, and an after-close
  audit for large/liquid and volatile names; make the universe and rationale visible.
- Store a bounded research memo, evidence references, contradictions, missing
  inputs, confidence basis, invalidation, and human-review outcome—not hidden
  reasoning.
- Add deterministic tools for market structure, flow, history, options, events,
  validation, and adversarial risk. The agent cannot invoke mutations or orders.
- Evaluate with golden research questions, stale/missing/provider-conflict cases,
  prompt injection attempts, unsupported-number detection, tool error recovery,
  latency, and token cost.

Gate:

- Every material claim maps to an internal evidence timestamp or external source.
- Withheld evidence produces “unknown,” not a fabricated conclusion.
- Policy tests prove the fleet cannot reach a write/order path.

### Phase 6 — deployment-quality application and hackathon package

Deliver:

- Keep the real installation private at the authenticated Tailscale URL and its
  core bound to loopback.
- Add a separate demo profile using sanitized/frozen market fixtures, seeded
  prospective records, disposable auth, and no credentials/private archives.
- Produce one-command environment validation, build, migration, seed, deploy,
  smoke, rollback, and teardown scripts.
- Add structured logs, request/tool traces, health/readiness, audit exports,
  crash recovery, backup status, and a visible “demo data” badge.
- Package the Google ADK supervisor, policy/audit plugin, architecture diagram,
  threat model, evidence lineage walkthrough, three-minute demo script, and
  reproducible judge checklist.

Gate:

- Clean-machine deployment and rollback succeed from documentation.
- Authenticated end-to-end journeys, visual regression, accessibility, load,
  dependency/license, secret, and route-allowlist tests pass.
- Demo image contains no `.env`, credentials, private databases, email contents,
  or raw commercial/vendor artifacts.

## Product metrics that matter

- Time from opening Cipher to first review-worthy candidate.
- Percentage of candidate cards with fresh, complete required evidence.
- Scanner-to-chart parity rate and option-selection availability rate.
- Prospective signals, eligible non-signals, closed samples, missing-input rate,
  and rule changes per cohort (target: zero).
- Research memo grounded-claim rate and contradiction-detection rate.
- First-render/ticker-switch latency, provider error rate, archive growth/day,
  disk runway, backup age, and restore success.
- User task completion and navigation count—not page or panel count.

## Immediate execution order

1. Observe and audit today’s TSLA/radar session; do not tune during the cohort.
2. Add prospective deltas to the existing daily Discord report and operator health.
3. Freeze configuration hashes and per-run coverage/absence reasons.
4. Start Phase 1 information-architecture prototypes while data accumulates.
5. Build the shared evidence/signal schema before another large strategy search.
6. Compact/index the 70 GB Tradier store only after checksum and restore tests.
7. Treat the ADK fleet and public demo packaging as consumers of the hardened
   product, not as the foundation of the product.

## Explicit exclusions

- Live trading, broker adapters, order endpoints, autonomous capital actions.
- Reconstructing option quotes that were never captured.
- Treating missing OI/gamma/quotes as zero.
- Presenting public-OI GEX as known dealer inventory.
- Optimizing the TSLA or weekly-radar rules after prospective observation begins.
- Publishing the private production dataset or credentials for a demo.

---

# Consolidated execution plan — revision 2

This section is the current implementation contract for the remainder of 2026.
It supersedes ambiguous “add another strategy/panel/model” work while
preserving the original evidence and safety rules above. A task is complete
only when its acceptance evidence exists; code that merely renders a happy-path
card is not complete.

## 1. North-star product definition

Cipher is a local-first, read-only stocks-and-options decision workstation for a
normal active trader. It should answer five questions in order:

1. **What deserves attention now?** — session, data health, events, watchlists,
   and ranked research candidates.
2. **What actually happened?** — price, volume, levels, flow, exposure, news,
   and the exact event-time evidence behind the candidate.
3. **What is the tradable expression?** — liquid single-leg or defined-risk
   structure, cost, spread, Greeks/IV/OI coverage, payoff, and invalidation.
4. **What is my plan?** — thesis, trigger, size, risk, alert, and review state.
5. **Did the idea survive contact with the market?** — paper fills, marks,
   exits, skipped opportunities, prospective outcomes, and sample sufficiency.

The product wins by making those answers trustworthy and connected. It does not
win by producing the most aggressive score, the most panels, or the most
confident language.

## 2. Shipped baseline as of 2026-08-17

The following are implemented and deployed, not future promises:

| Area | Current capability | Remaining qualification |
|---|---|---|
| Runtime | Loopback Python core, authenticated same-origin Node proxy, Next UI, systemd health/restart | Clean-machine install and rollback rehearsal |
| Market data | Alpaca OPRA options; SIP/IEX stock fallback; bars, chain, quote/trade, Greeks/IV/OI/date | Coverage catalog and long-run provider-lag budgets |
| Evidence | Source/event/capture time, freshness, coverage, missing reasons, stable IDs | Full scanner/chart/evaluator parity report |
| GEX/VEX | Captured history and public-OI formula with unknown-value preservation | More independent sessions; never promote to “dealer truth” |
| Scanner | Presets, rejection funnel, quality gates, comparison tray, evidence links | Performance budget at larger universes |
| Night Vision | Price-first chart, ranges, volume, regime, crosshair, replay context | Full snapshot replay parity and interaction polish |
| Options | Chain/structure research, liquidity checks, defined-risk spread fallback | Payoff/assignment/event-risk coverage for more structures |
| Backtests | Event-time research engine, cost controls, holdout protocol, strategy labs | One shared evaluator and multiple-testing controls |
| Paper portfolios | Six isolated studies, realized/mid/liquidation marks, risk locks, spread fallback | Larger comparable samples and cohort reports |
| Autopilot | Premarket/confirmation architecture, paper executor, append-only trace | First complete scheduled market-day audit on 2026-08-18 |
| Research agent | Scheduled desk, evidence-bounded context, FinBERT advisory feature | Golden task evaluation and agent cost/latency report |
| Governance | Provenance, experiment registration, promotion gate, prospective validation | Unified lineage graph across every evaluator |
| UI QA | Authenticated desktop/mobile/keyboard browser journeys | Visual baseline review and accessibility pass on frozen release |
| Hackathon | Draft, architecture, demo script, screenshots, sanitized export and audit | Eligibility, public-repo authorization, recording, submission |

## 3. Priority system

Every new request is assigned one priority:

- **P0 — safety/correctness:** data truth, accounting, auth, route allowlists,
  paper boundary, reproducibility, backup/restore, or a broken core workflow.
- **P1 — daily trader value:** Morning Brief, scanner, chart, options structure,
  planning, journal, alerts, and review loop.
- **P2 — validation quality:** evaluator parity, cohort design, coverage,
  cost/slippage, statistics, and model leakage controls.
- **P3 — scale/polish:** performance, storage compaction, accessibility,
  visual refinement, deployment ergonomics, and agent latency/cost.
- **P4 — exploratory:** new indicators, niche strategy families, model training,
  or external integrations. P4 cannot interrupt an open P0/P1/P2 gate.

## 4. Immediate sequence: next 72 hours

### P0-A — complete the first installed autopilot day

Observe the full premarket plan, regular-session closed-bar confirmation,
paper-fill/skip, mark, exit, and daily report path. Do not tune rules during the
observation window.

Required records:

- plan ID and configuration hash;
- candidate universe and every rejection reason;
- last closed bar and provider freshness;
- confirmation decision and reason;
- simulated fill, spread, slippage, stop/target, and exit;
- daily risk-lock state and no-entry decisions;
- append-only trace and Discord delta;
- post-close reconciliation and restart/idempotency check.

Exit gate: a complete trace exists even if there are zero eligible trades. A
missing phase is reported as missing—not backfilled or inferred.

### P0-B — freeze the QuantumHacks package

- Confirm student/age eligibility and team roster.
- Review the 709-file sanitized export and seven screenshots.
- Install-test the standalone bundle from a clean temporary checkout.
- Record the 3:30 demo and verify no credentials, local paths, emails, or private
  data appear.
- Obtain explicit authorization before creating or pushing a public repository.
- Submit early on August 20, leaving recovery time before the official deadline.

Exit gate: signed-out reviewer can understand the product from the repository,
README, screenshots, and video without private infrastructure access.

### P1-A — stabilize the daily surface

Fix only high-value issues exposed by the first full day:

- Morning Brief ordering and data-health language;
- scanner-to-chart navigation and stable snapshot identity;
- Paper Portfolios marks/risk/trace clarity;
- mobile overflow, keyboard focus, loading/error/empty states;
- Discord report consistency with the on-screen ledger.

Exit gate: five-minute workflow test succeeds: Today → Discover → Analyze →
Options → Plan/Journal → Review.

## 5. First 30 days after the submission

### Workstream A — one evidence contract (P0/P2)

Create a canonical `EvidenceSnapshot`/`SignalRecord` contract used by scanner,
Night Vision, Options Terminal, research agent, historical evaluator, and
prospective fronttests. It must include:

- ticker, instrument, session, event time, capture time, provider/feed;
- freshness threshold and calculated age;
- coverage state (`complete`, `partial`, `stale`, `missing`, `unknown`);
- feature values and feature provenance;
- missing/invalid reasons;
- strategy/configuration hash and stable signal ID;
- parent snapshot/replay ID and software version.

Implementation order:

1. Define the schema and fixture corpus.
2. Add adapters around existing payloads; do not rewrite all engines at once.
3. Make scanner and chart consume the same session/level snapshot.
4. Make evaluator and paper executor consume the same signal record.
5. Emit a parity report for historical/replay/prospective modes.

Exit gate: a frozen fixture produces byte-equivalent signal decisions across all
three modes, or differences are explained as explicit fill-model differences.

### Workstream B — trader information architecture (P1)

Default navigation remains task-based:

```text
Today | Discover | Analyze | Plan | Review
                         └── Labs / System for specialist work
```

Build the Ticker Workbench as the main context-preserving surface:

- Overview: thesis, session, freshness, events, alerts;
- Chart: Night Vision and key levels;
- Flow/Exposure: prints, GEX/VEX, caveats;
- Options: chain, structure builder, payoff and liquidity;
- Agent: bounded memo, sources, conflicts, missing inputs;
- Review: journal, paper result, invalidation, and next action.

Interaction rules:

- preserve ticker, timeframe, date, snapshot, and workspace state across tabs;
- use progressive disclosure for specialist fields;
- show one primary action per card;
- label every value observed/inferred/modeled/stale/unavailable;
- never use “confidence” without its evidence basis and meaning.

Exit gate: task-based usability test with a new user and an expert; measure time
to first candidate, navigation count, abandoned tasks, and mobile/keyboard
completion.

### Workstream C — scanner and Night Vision parity (P0/P1)

Scanner funnel:

```text
Universe → data-qualified → liquidity-qualified → setup-qualified → ranked
```

Required behavior:

- count every candidate entering each stage;
- preserve rejection reason and data age;
- link candidate to exact chart snapshot;
- display expected move, catalyst, invalidation, spread, and evidence gaps;
- allow comparison of at most three setups without hiding the denominator.

Night Vision requirements:

- price and session context first;
- overlays opt-in and visually subordinate;
- no repainting after a frozen snapshot is opened;
- chart marker opens the originating signal and evidence drawer;
- crosshair displays OHLCV, event time, and source state.

Exit gate: 100% parity on golden scanner/chart fixtures, no stale-level drift,
and measured first-render/ticker-switch/500-name scan budgets.

### Workstream D — options structure and risk (P1/P0)

Expand the read-only builder in this order:

1. single long call/put;
2. vertical debit spreads;
3. covered call/cash-secured put research;
4. collars and calendars only when both legs have valid observations.

Every structure shows:

- observed bid/ask/trade and age per leg;
- spread percentage and liquidity flags;
- IV/Greeks/OI/date availability;
- debit/credit, max gain/loss, breakevens, payoff path;
- sizing against defined risk;
- earnings, ex-dividend, assignment, and expiration warnings;
- midpoint mark and conservative liquidation estimate separately.

No structure may become an order ticket. A missing leg makes the structure
unavailable, not free or zero-risk.

Exit gate: payoff fixtures, wide-market fixtures, stale-quote fixtures,
assignment/event fixtures, and multi-leg mark/accounting tests pass.

### Workstream E — unified backtest/evaluation plane (P0/P2)

Implement one versioned strategy specification:

- universe and session;
- trigger and confirmation clock;
- entry/exit/fill model;
- stop/target/hold rules;
- sizing, fees, spread, slippage;
- missing-data policy;
- ticker/regime scope;
- evaluator version and configuration hash.

Every result must report:

- count of opportunities, eligible signals, blocked signals, and missing cases;
- per-ticker, direction, regime, time-of-day, and expiry breakdown;
- win rate, expectancy, payoff ratio, drawdown, MFE/MAE, time in trade;
- spread/slippage sensitivity and conservative fill variant;
- train/validation/test and walk-forward folds;
- trial-family ID and multiple-testing warning;
- prospective cohort link and governance status.

The rare-strategy search protocol is:

1. preregister economically motivated families;
2. use historical data only for hypothesis formation;
3. freeze candidate rules and run holdout;
4. register every eligible/non-eligible opportunity;
5. run prospective observation without tuning;
6. require minimum sample and confidence interval before comparison;
7. promote only to `LIVE_REVIEW_REQUIRED`, never to live execution.

Exit gate: look-ahead, survivorship, DST/session, same-bar collision, stale
quote, crossed-market, option-expiry, and missing-data fixtures pass.

### Workstream F — prospective portfolios and Discord (P0/P2)

Maintain six isolated portfolios, then add comparable cohorts by strategy family,
not by whichever ticker looked best after the fact. Each account exposes:

- starting/equity/realized/mid/liquidation values;
- daily P/L, wins/losses, entries, loss-lock state;
- open positions, marks, fill provenance, and exits;
- skipped-signal counterfactuals;
- sample status and rank eligibility.

Discord daily report must include:

- account deltas and open marks;
- new entries/exits/skips;
- daily locks and feed degradation;
- prospective cohort changes;
- trace health and no-trade reasons.

Exit gate: a restart cannot duplicate a signal or Discord delta, and the report
reconciles to the API ledger.

## 6. Days 30–90: harden the research platform

### Data plane

- Build a coverage catalog for bars, chains, quotes/trades, OI, Greeks/IV, GEX,
  flow, earnings, news, and corporate actions.
- Split hot SQLite operational state from immutable warm/cold archives.
- Add checksummed manifests, retention tiers, restore tests, and disk runway.
- Keep Alpaca authoritative; Yahoo/Finviz may enrich earnings/context only with
  source labels and conflict preservation.
- Add rate budgets, exponential backoff, circuit breakers, and lag alerts.

### Research agent

- Premarket large/liquid universe scan;
- intraday change review;
- post-close attribution and journal review;
- bounded memo with evidence references, contradictions, uncertainty,
  invalidation, and human-review state;
- deterministic allowlisted tools and no mutation/order capability.

Evaluation set:

- golden ticker questions;
- missing/stale/provider-conflict fixtures;
- unsupported-number detection;
- prompt-injection and hostile-source tests;
- tool failure/retry behavior;
- latency, token, and source coverage budget.

FinBERT remains advisory until leakage-safe prospective samples justify testing.
Fine-tuning is blocked until the existing dataset gate reports sufficient market
dates, closed samples, chronological split, ticker holdout, and embargo.

### Operations and security

- consolidate operator incidents into one queue;
- add structured request/tool/decision traces;
- test auth expiry, rate limiting, proxy route allowlists, and Tailscale access;
- automate backup/restore verification;
- isolate or retire stale legacy capture jobs without touching active Alpaca data;
- produce clean demo profile with frozen fixtures and a visible demo-data badge.

Exit gate: restore, auth, route, secret, dependency/license, accessibility,
load, and crash-recovery checks pass on a clean machine.

## 7. Days 90–180: product depth without boundary drift

Prioritized additions:

1. payoff-aware options planner with portfolio Greeks and concentration;
2. event-aware alerts and deduplicated notification ledger;
3. linked chart saves, thesis journal, MFE/MAE review, and invalidation history;
4. company/event context with cited earnings and filings;
5. personalized research ranking based on observed user decisions, gated by
   holdout and safety constraints;
6. performance and storage compaction once measured budgets justify it;
7. public demo/education mode only after licensing, data rights, onboarding,
   and support decisions are explicit.

Do not add a live broker adapter as a “next obvious step.” Any future live
execution proposal requires a separate authorization, threat model, codebase
boundary, human approval process, and revised project identity.

## 8. Definition of done for every phase

Before marking a phase complete:

1. implementation is linked to the evidence contract and configuration hash;
2. deterministic fixtures cover success, missing, stale, partial, and failure;
3. active Python tests, compileall, Node checks, lint/typecheck/build pass;
4. authenticated browser desktop/mobile/keyboard journeys pass;
5. read-only API smoke and service health pass;
6. safety grep confirms no order endpoint/client/scheduler;
7. storage, backup, and integrity checks pass when relevant;
8. a dated audit records metrics, known limitations, and carry-forward work;
9. documentation and UI claims match observed evidence;
10. no strategy is called validated/best/profitable before its declared gate.

## 9. Risk register and mitigations

| Risk | Trigger | Mitigation | Owner/gate |
|---|---|---|---|
| Data gaps look like zero | Missing gamma/OI/quote/bar | Preserve unknown; show coverage and rejection reason | P0 evidence tests |
| Backtest overfitting | Many variants or favorable ticker selection | Preregistration, trial family, holdout, prospective quarantine | P2 evaluator gate |
| Midpoint P/L overstates exits | Wide/stale spread | Conservative liquidation mark and spread-crossed paper fills | P0 accounting |
| Autopilot acts on stale plan | Plan/confirmation age or clock mismatch | Closed-bar confirmation, entry window, trace, fail-closed | P0 daily audit |
| UI becomes another dashboard | More panels without task value | Task IA, progressive disclosure, usability metrics | P1 workflow gate |
| Agent invents a claim | Missing/contradictory evidence | Evidence citations, unknown state, adversarial tests | P2 agent gate |
| Storage silently degrades | Disk growth, WAL corruption, provider lag | runway alerts, manifests, restore drills, retention tiers | P0 operations |
| Hackathon leaks private data | Export includes runtime artifacts | allowlist builder, secret audit, human file review | P0 release gate |
| Public release changes project boundary | Repository becomes redistributed | explicit authorization and sanitized demo profile | user decision |

## 10. Canonical execution order

```text
Aug 18 complete-day audit
        ↓
QuantumHacks freeze/submission
        ↓
EvidenceSnapshot + evaluator parity
        ↓
Scanner/Night Vision/Options workflow depth
        ↓
Prospective sample accumulation + Discord reconciliation
        ↓
Data/storage/restore hardening
        ↓
Research-agent evaluation
        ↓
Measured personalization and optional demo distribution
```

If a later request conflicts with this sequence, resolve the conflict by
priority (P0 → P1 → P2 → P3 → P4), sample integrity, and execution-boundary
rules—not by adding a parallel subsystem.

## 11. Merged detailed backlog from the earlier terminal plans

The following items are retained from the former terminal V2/V3 plans. They are
now subordinate tasks in this roadmap, not separate phases or competing plans.

### Options-flow tape and freshness contract — P0

- Prefer current-session captured option prints when coverage exists.
- Keep provider, source/session, capture mode, oldest/newest event, and `as_of`
  separate from request/generation time.
- Use the Alpaca latest-trade reconstruction only as an explicitly labelled
  fallback; never silently mix prior sessions.
- Ensure call/put and buy/sell filters use the browser's actual query values.
- Show the full print date wherever a prior-session or fallback observation is
  involved.
- Define a bounded market-session status for quote, chain, flow, GEX, scanner,
  research, and archive inputs.

Acceptance: deterministic before-session, regular-session, after-session,
stale, partial, and provider-failure fixtures produce truthful UI states.

### Options history and event context — P2

- Capture ATM/skew/term/OI/liquidity snapshots on a rate-conscious schedule.
- Build rolling IV rank/percentile only after the declared coverage minimum.
- Keep earnings estimates, confirmed results, dividends, splits, and corporate
  actions distinct, sourced, and revision-aware.
- Use Yahoo/Finviz only as supplemental context; preserve conflicts and source
  timestamps while keeping Alpaca authoritative for market data.
- Never reconstruct option quotes, IV history, or event data that was not
  captured.

Acceptance: coverage-window, revision, source-conflict, and unavailable-state
tests pass; IV rank never appears as a fabricated number.

### Option-premium journal and chart evidence — P1/P2

- Store exact contract symbol, underlying, expiry, strike, call/put, side,
  quantity, multiplier, observed quote/trade, mark source, and capture time.
- Replay bid/mid/ask excursions for single and multi-leg paper observations.
- Track option MFE/MAE only when quote coverage supports it.
- Attach a rendered chart/evidence image to journal records only as an
  observation artifact, never as proof of an executed fill.
- Label incomplete capture windows and distinguish paper fills from marks.

Acceptance: synthetic contract replay, multiplier/side accounting, spread
crossing, missing-quote, and screenshot-restore fixtures pass.

### Backtest statistical hardening — P2

- Add moving-block bootstrap intervals alongside independent-trade intervals.
- Record method, block length, seed, sample size, and confidence level in the
  experiment manifest.
- Fail loudly on invalid block sizes or insufficient observations.
- Preserve chronological holdout, next-bar fill, conservative same-bar ordering,
  cost sensitivity, and deterministic rerun hashes.
- Include benchmark, exposure/time-in-market, drawdown concentration, and
  multiple-testing/deflated-selection warnings.

Acceptance: clustered outcomes cannot be represented only by IID statistics;
same manifest and seed reproduce the same result.

### Counterfactual opportunity ledger — P0/P2

- Record every detected signal, including entries blocked by daily limits,
  existing positions, allocation, option liquidity, missing quotes, or stale
  data.
- Score underlying target/invalidation ordering, MFE, MAE, bars observed, and
  terminal status prospectively.
- Keep counterfactual underlying outcomes separate from option fills and P/L.
- Surface opened/skipped, resolved/tracking, target/invalidation, and missing
  reasons in Paper Portfolios and Discord.
- Feed these records into research review only; never silently alter scanner
  weights or promote a rule.

Acceptance: every skipped signal has a truthful classification when enough bars
exist, and replay is restart-safe and idempotent.

### Operations, telemetry, archive, and backend seams — P0/P3

- Replace obsolete PID assumptions with active systemd service/timer checks.
- Record provider latency, error, cache age, capture freshness, database growth,
  disk runway, backup age, and restore result.
- Separate hot SQLite state from immutable raw/warm/derived archives.
- Add checksummed manifests, dry-run retention, WAL/integrity checks, and
  restore verification before any retention deletion.
- Keep secrets server-side and vendor access behind normalized adapters while
  preserving the standard-library HTTP-server boundary.
- Isolate or retire legacy non-Alpaca capture jobs only after their data and
  operational dependencies are audited.

Acceptance: operator status measures the deployed runtime, authenticated
endpoints are tested under their actual auth contract, and a representative
restore reproduces an experiment hash.

### Product integration and QA — P0/P1

- Connect research reports, scanner, Night Vision, options, alerts, chart,
  journal, paper portfolios, and review into one daily flow.
- Keep task navigation as the default; specialist labs remain discoverable but
  do not compete with the primary workflow.
- Run authenticated Chromium at desktop and 390px, keyboard navigation,
  contrast/accessibility, visual regression, API contract, dependency/license,
  secret, route-allowlist, and load checks.
- Publish the frontend atomically with manifest comparison, rollback, and stale
  bundle detection.
- Record a dated audit after every phase and carry unresolved findings forward.

Acceptance: a normal trader can complete research → validation → structure →
plan/journal → paper review without disconnected labs or hidden state.

## 12. Merged QuantumHacks runbook

The hackathon work is a delivery track inside this roadmap, not a separate
product plan.

### Before the first complete market-day audit

- Confirm eligibility and roster.
- Keep the sanitized export local until public-repository authorization.
- Verify the seven deterministic screenshots, architecture source, README,
  installation instructions, and claim checklist.
- Do not change prospective strategy rules during the observation window.

### First complete market day

- Verify premarket plan, candidate/rejection trace, closed-bar confirmation,
  paper fill/skip, risk lock, mark/exit, Discord delta, and restart idempotency.
- Update quantitative claims only from observed records.
- If a phase is missing, present that as an honest fail-closed result.

### Freeze and submit

- Install-test the sanitized export on a clean checkout.
- Freeze commit/configuration hash and audit all claims.
- Record a 2–5 minute video using the Scanner → Night Vision → Options → Paper
  Portfolios → Research Desk path.
- Submit internally by 14:00 EDT on August 20, 2026, leaving recovery margin.
- Verify signed-out repository access, video playback, screenshots, and links.

### Judge-facing proof

1. It works as one usable workflow.
2. Its evidence envelope, rejection ledger, evaluator lineage, liquidation marks,
   and decision trace are technically distinctive.
3. It is safe and honest: AI is advisory, missing data stays unknown, GEX is a
   public-OI heuristic, and no broker order capability exists.

## 13. One canonical status vocabulary

Use these statuses everywhere—UI, API, Discord, audits, and submission copy:

- `SHIPPED` — implemented, deployed, and verified by its acceptance gate.
- `OBSERVING` — immutable prospective rule collecting evidence.
- `BLOCKED` — an explicit dependency or safety gate prevents progress.
- `INSUFFICIENT_SAMPLE` — not enough data to rank, train, or promote.
- `RESEARCH_ONLY` — may inform a human but cannot mutate or authorize capital.
- `STALE`, `PARTIAL`, `MISSING`, `UNKNOWN` — data quality states, never zeroes.
- `LIVE_REVIEW_REQUIRED` — governance checkpoint only; never execution authority.

No document, UI label, or agent memo may introduce a competing meaning for these
states.
