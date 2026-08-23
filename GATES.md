# Gates: hosted product repair and audit

Scope: Repair guest/authenticated product failures, Groq budgeting, and option visibility, then prove them in headed Chromium.

- [x] G1: Focused Python and Node invariants pass.
  CHECK: ./.venv-research-py312/bin/pytest -q cipher-system/tests/test_ask_cipher_resilience.py cipher-system/tests/test_operator_status.py && node --test cipher-system/app/test/*.test.mjs
  EXPECT: /27 passed[\s\S]*# fail 0/
  EVIDENCE: # todo 0 | # duration_ms 6242.112441

- [x] G2: Frontend lint, types, production build, and published-tree sync pass.
  CHECK: cd cipher-system/web && npm run lint >/dev/null && npm run typecheck >/dev/null && cd ../.. && ./cipher-system/scripts/sync_web_build.sh --check
  EXPECT: In sync: app/public matches web/out.
  EVIDENCE: In sync: app/public matches web/out.

- [x] G3: The live headed Chromium suite passes guest Strike Matrix, GEX Replay, Trident, Earnings Radar, saved scans, Holdings options, and Ask Cipher/Groq.
  EVIDENCE: `xvfb-run ... playwright ... --headed` completed 4/4 in 19.4s; seven panel screenshots are under `cipher-system/web/test-results/headed-product-audit-*`.

- [x] G4: Unlazy and Ponytail are installed with readable skill instructions.
  CHECK: test -f /home/aarav/.codex/skills/unlazy/SKILL.md && test -f /home/aarav/.codex/skills/ponytail/SKILL.md && echo skills-installed
  EXPECT: skills-installed
  EVIDENCE: skills-installed

---

# Gates: earnings automation and accuracy audit

Scope: Trace the earnings bot from scheduler through artifact and API/UI, and measure its accuracy only where outcomes exist.

- [x] E1: The earnings pipeline inventory identifies its generator, scheduler, artifact, API route, and UI consumer with current runtime evidence.
  EVIDENCE: `earnings_model/cli.py radar` writes `runtime/data/earnings_radar.json`; `cipher-earnings-digest.timer` is active for weekdays 08:15 ET and last triggered 2026-08-21; core `app.py:320` serves it through `/api/earnings-radar`; `EarningsRadar.tsx` consumes it. The same service runs `paper-enter` and `notify-discord --type all`.

- [x] E2: The currently deployed earnings artifact is structurally valid and its freshness/status agrees across disk, operator status, and API.
  CHECK: ./.venv-research-py312/bin/pytest -q cipher-system/tests/test_earnings_radar_endpoint.py cipher-system/tests/test_operator_status.py
  EXPECT: /passed/
  EVIDENCE: ..........                                                               [100%] | 10 passed in 2.76s

- [x] E3: Earnings accuracy is measured only against genuinely realized outcomes, with sample size, date range, metric definitions, and leakage caveats recorded.
  EVIDENCE: Re-run 3-month chronological holdout: 513 reports, 2026-05-18..2026-08-14, strategy win rate 58.67%, PF 0.71, avg P&L -10.88%, gated direction 46.15% (N=65), gap MAE 2.33%. Reaction artifact test N=4,538: beat 77.68%, gap direction 53.04%, day-5 direction 51.76%, reversal 74.13%; corresponding majority baselines are 75.69%, 53.00%, 51.76%, 74.31%. Paper DB has 33 OPEN, 0 SETTLED, 0 realized rows, so prospective accuracy is not measurable.

- [x] E4: Existing earnings integration and paper-book regression checks pass, and diagnosed defects are explicitly reported rather than silently repaired outside this diagnostic request.
  CHECK: ./.venv-research-py312/bin/pytest -q cipher-system/tests/test_earnings_radar_endpoint.py cipher-system/tests/test_operator_status.py cipher-system/tests/test_morning_brief.py cipher-system/tests/test_earnings_paper_portfolio.py
  EXPECT: /18 passed/
  EVIDENCE: -- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html | 18 passed, 2 warnings in 3.44s

---

# Gates: earnings repair and evidence-driven codebase cleanup

Scope: Repair every diagnosed earnings automation defect, reduce proven duplication without speculative rewrites, and drive remaining active-code defects from comprehensive automated checks.

- [x] R1: Earnings Discord preview uses the live radar schedule, contains no hardcoded report week/expiry, and every payload stays within Discord limits.
  CHECK: ./.venv-research-py312/bin/pytest -q cipher-system/tests/test_earnings_discord_bot.py
  EXPECT: /passed/
  EVIDENCE: Discord-focused suite passed; 33 positions split across complete, compliant payloads.

- [x] R2: Paper earnings entries remain idempotent, the CLI reports the complete active book, and expired positions settle deterministically with realized results.
  CHECK: ./.venv-research-py312/bin/pytest -q cipher-system/tests/test_earnings_paper_portfolio.py
  EXPECT: /passed/
  EVIDENCE: paper suite passed; 13/13 eligible live paper rows settled after a recoverable database backup.

- [x] R3: The scheduled service fails observably when a required stage fails and performs refresh, settlement, entry, and notification in the correct order.
  CHECK: rg -o "earnings_model (radar|paper-settle|paper-enter|notify-discord)" infra/gcp-cipher-vm/systemd/cipher-earnings-digest.service | wc -l
  EXPECT: /4/
  EVIDENCE: 4 stages found; installed unit settles, refreshes, enters, then notifies while preserving any stage failure.

- [x] R4: Radar freshness is schedule-aware over weekends and upcoming cards use current pre-event price drift rather than silently reusing the last historical event's drift.
  CHECK: ./.venv-research-py312/bin/pytest -q cipher-system/tests/test_earnings_radar_endpoint.py cipher-system/tests/test_earnings_scanner.py
  EXPECT: /passed/
  EVIDENCE: focused suite passed; regenerated artifact contains 34/34 cards with market_drift_source=current and API status=current.

- [x] R5: Earnings output states its measured validation and limitations without presenting simulated payoff accuracy as live option performance.
  EVIDENCE: API/UI/Discord expose UNVALIDATED_FOR_LIVE_OPTIONS_PNL plus N, accuracy, MAE, holdout method, and estimated-entry caveat.

- [x] R6: Active Python, Node, web, and safety suites pass after the cleanup.
  CHECK: ./.venv-research-py312/bin/python -m compileall -q earnings_model cipher-system/core cipher-system/tests && node --test cipher-system/app/test/*.test.mjs && cd cipher-system/web && npm run lint && npm run typecheck && npm run build
  EXPECT: /Compiled successfully/
  EVIDENCE: compilation passed; Node 33/33; web 61/61; research guard 11/11; lint/typecheck/build passed.

- [x] R7: The full Python suite passes, or every external/environment-only skip is explicit and no test fails.
  CHECK: ./.venv-research-py312/bin/pytest -q cipher-system/tests
  EXPECT: /passed/
  EVIDENCE: Python 3.12: 1085 passed, 1 skipped; production Python 3.11: 1084 passed, 2 skipped; 0 failures.

- [x] R8: The deployed services and web artifact are synchronized and healthy after focused changes.
  EVIDENCE: published tree in sync; core/web/timer active; both health endpoints OK; installed earnings unit byte-identical to source.

- [x] R9: A measured next-action plan ranks remaining product work by impact and evidence.
  EVIDENCE: cipher-system/docs/audits/earnings_repair_and_repo_sweep_2026-08-22.md

---

# Gates: simple morning brief and evidence-gated earnings model v2

Scope: Reduce the Morning Brief to decision-useful information, improve future
earnings paper selection only where chronological validation supports it, and
preserve prior paper outcomes as immutable evidence.

- [x] M1: Morning Brief presents a compact daily decision hierarchy without duplicated integrity or workflow prose.
  CHECK: node --test cipher-system/web/test/product-hardening.test.mjs
  EXPECT: /fail 0/
  EVIDENCE: Product-hardening suite passed 9/9, including the compact Morning Brief invariant.

- [x] M2: Earnings model candidates are compared on chronological holdout data against explicit naive baselines, with deterministic selection.
  CHECK: ./.venv-research-py312/bin/pytest -q cipher-system/tests/test_earnings_model_selection.py
  EXPECT: /passed/
  EVIDENCE: Model-selection suite passed 2/2. The 22,688-row 60/20/20 run rejected day-5 direction at 52.03% versus 51.76% baseline and retained gap MAE 1.9777% versus 2.2523% baseline as descriptive only.

- [x] M3: New paper entries record the exact model/version and validation gate, while settled and existing entries are never rewritten.
  CHECK: ./.venv-research-py312/bin/pytest -q cipher-system/tests/test_earnings_paper_portfolio.py
  EXPECT: /passed/
  EVIDENCE: Paper suite passed 7/7; migration adds nullable provenance fields, legacy rows remain legacy-unversioned, and failed validation returns a skip rather than an order.

- [x] M4: The scorecard separates legacy estimated outcomes from each prospective model cohort and never claims live option performance.
  CHECK: ./.venv-research-py312/bin/pytest -q cipher-system/tests/test_earnings_discord_bot.py cipher-system/tests/test_earnings_radar_endpoint.py
  EXPECT: /passed/
  EVIDENCE: Radar/Discord suite passed 10/10. Current scorecard is 33 legacy positions, 20 open, 13 settled, 2 wins, estimated P&L -$14,245; API/UI/Discord expose the cohort and estimation caveat.

- [x] M5: Full Python, Node, web, safety, build, deployment, and published-tree checks pass.
  EVIDENCE: Python 3.12 1088 passed/1 skipped; research-only 11/11; Node 33/33; web 61/61 plus lint/type/build; git diff check clean; published tree in sync; core/web/timer active; core and web API health OK; headed guest Strike Matrix passed through the Tailscale URL.

- [x] M6: A post-change audit records measured uplift or rejection and the next highest-impact work.
  EVIDENCE: cipher-system/docs/audits/simple_morning_brief_and_earnings_v2_2026-08-23.md

---

# Gates: reliable daily local-paper autopilot

Scope: Repair the authenticated OPRA data path, make every paper decision
observable, add bounded failure/daily notifications, and deploy without adding
broker or live-order authority.

- [x] A1: The installed continuous executor receives the same secret environment as the scheduler, and an executor-originated OPRA chain request succeeds without HTTP 401.
  EVIDENCE: installed unit reports `/etc/cipher/cipher.env`; deployed SPY OPRA canary returned HTTP 200 with 6 expirations and 2312 contracts, paper_only=true, live_execution_capability=false.

- [x] A2: Executor status exposes provider/data readiness, last successful chain time, last worker failure, entry blocker, and ledger counts without credentials.
  CHECK: ./.venv-research-py312/bin/pytest -q cipher-system/tests/test_paper_executor_health.py cipher-system/tests/test_autopilot_status.py
  EXPECT: /passed/
  EVIDENCE: focused status suite passed 5/5; deployed status reports ready/ready, the last OPRA timestamp, sanitized historical failure, no current block, and complete counts.

- [x] A3: Provider failures are classified, block fills, and retain a sanitized reason; success creates contract candidates and conservative local-paper fills.
  CHECK: ./.venv-research-py312/bin/pytest -q cipher-system/tests/test_alpaca_core_market_data.py cipher-system/tests/test_paper_executor_runtime.py
  EXPECT: /passed/
  EVIDENCE: adapter/runtime suite passed 18/18; failure path persists a block without a fill and successful path persists deterministic simulated orders.

- [x] A4: Restart, duplicate ingest, stale quote, risk lock, target, stop, time exit, and forced-close behaviors remain deterministic and paper-only.
  CHECK: ./.venv-research-py312/bin/pytest -q cipher-system/tests/test_paper_executor_runtime.py cipher-system/tests/test_paper_executor_policy.py cipher-system/tests/test_premarket_autopilot.py cipher-system/tests/test_autopilot_replay.py cipher-system/tests/test_research_only_guard.py
  EXPECT: /passed/
  EVIDENCE: runtime/policy/scheduler/replay/research-only suite passed 49/49; no broker-order surface was added.

- [x] A5: One deduplicated blocking-failure notification and one after-close combined local-paper recap are generated from ledger truth.
  CHECK: ./.venv-research-py312/bin/pytest -q cipher-system/tests/test_autopilot_notifications.py
  EXPECT: /passed/
  EVIDENCE: notification suite passed 3/3; hardened service classified the old event stale without sending; combined daily preview is 958 characters and includes autopilot plus earnings summaries.

- [x] A6: Paper Portfolios and Morning Brief distinguish healthy/no-setup, rejected, data failure, and active-position states.
  CHECK: node --test cipher-system/web/test/product-hardening.test.mjs && cd cipher-system/web && npm run typecheck >/dev/null && echo web-ok
  EXPECT: web-ok
  EVIDENCE: web suite passed 61/61, production type/build passed, and hosted headed guest audit passed 1/1.

- [x] A7: Full Python, Node, web, safety, deployment, service, and published-tree verification pass with no live-order surface.
  EVIDENCE: Python 1096 passed/1 skipped; app Node 33/33; web 61/61; web check and git diff check passed; build in sync; core/web/executor and all relevant timers active; health endpoints and OPRA canary pass.

- [x] A8: A dated post-deployment audit records the repaired root cause, canary result, exact ledger counts, and remaining Monday-only verification.
  EVIDENCE: cipher-system/docs/audits/reliable_local_paper_autopilot_2026-08-23.md

---

# Gates: close-to-next-open overnight strategy study

Scope: Test buying in the final regular-session minutes and selling at the
next regular-session open, with MU emphasized, broad equity coverage, and
captured option-bar results kept separate from stock results and from NBBO fills.

- [x] O1: The study defines point-in-time entry/exit rules, costs, sizing, option contract selection, and same-bar limitations without look-ahead.
  EVIDENCE: core/overnight_close_open_lab.py and docs/audits/overnight_close_open_study_2026-08-23.md define the protocol; a dedicated test proves missing future prints cannot change the entry-time contract selection.

- [x] O2: MU and the available broad equity universe are tested with total return, win rate, drawdown, profit factor, trade count, and period coverage.
  CHECK: ./.venv-research-py312/bin/pytest -q cipher-system/tests/test_overnight_close_open_lab.py
  EXPECT: /passed/
  EVIDENCE: focused suite passed 3/3; 39 stocks/ETFs tested with period and 0/2/5/10-bps cost sensitivity; MU has 2,673 daily and 19 exact closing-minute observations.

- [x] O3: Every captured-options dataset with eligible close/open observations is inventoried; calls and puts report observed-bar P&L separately and missing datasets are explicit.
  CHECK: ./.venv-research-py312/bin/python cipher-system/scripts/run_overnight_close_open_lab.py --check-existing
  EXPECT: /overnight-close-open-ok/
  EVIDENCE: check-existing passes; all 37 databases inventoried, 4,875 entry-time selections, 4,728 observed exits, 147 missing exits; MU option bars are explicitly unavailable.

- [x] O4: Results are persisted as reproducible JSON, CSV, and Markdown with data-quality caveats and no claim of executable NBBO fills.
  EVIDENCE: data/overnight_close_open_lab contains report JSON/Markdown, full stock/option ledgers, rankings, period splits, and friction sensitivity CSVs; every report labels option bars as non-NBBO observations.

- [x] O5: A full adversarial rerun passes its gates and the final ranking is measured from generated artifacts rather than memory.
  CHECK: git diff --check && node /home/aarav/.codex/skills/unlazy/scripts/gate-check.mjs GATES.md
  EXPECT: /ALL MET/
  EVIDENCE: final artifact validation passes; full Python 1,099 passed/1 skipped, app Node 33/33, web 62/62, research-only guard 11/11; dated audit records measured confidence and cost sensitivity.

---

# Gates: judge-ready guest showcase

Scope: Make guest mode a useful, deterministic, read-only product tour across
the full research surface, using clearly labelled MAG7 demo content wherever
private/provider-backed data is unavailable.

- [x] J1: Guest navigation exposes every safe research panel while account, provider, operational-control, and private-write actions remain locked.
  CHECK: node --test cipher-system/app/test/access-profile.test.mjs cipher-system/app/test/hosted-auth-boundary.test.mjs
  EXPECT: /fail 0/
  EVIDENCE: access/hosted-boundary suite passed 5/5; guest navigation contains six non-system sections while settings/operator are absent and private routes remain 403.

- [x] J2: Guest-safe market endpoints remain bounded while deterministic MAG7 showcase payloads render locally without credentials, private user state, or extra provider calls.
  EVIDENCE: hosted boundary test proves META guest access and private denial; 12-symbol labelled demo tape is local and the panel showcase makes no requests.

- [x] J3: Every guest panel renders a useful success, demo, or intentional locked state; no missing/stale/provider error is presented as live data.
  CHECK: node --test cipher-system/web/test/product-hardening.test.mjs
  EXPECT: /fail 0/
  EVIDENCE: product-hardening suite passed 10/10; 23 panel-specific profiles plus a labelled fallback cover the restored workflow, with explicit locked states for private actions.

- [x] J4: The hosted headed guest journey covers the restored navigation and representative panels without console or request failures.
  EVIDENCE: headed Chrome passed 1/1 across live Strike Matrix, demo Morning Brief, demo Options Terminal, and locked Holdings; guest-full-showcase.png visually inspected.

- [x] J5: Node, web type/lint/build, safety boundaries, deployed-tree synchronization, and health checks pass.
  EVIDENCE: app 33/33; web 62/62; web check passed; research-only guard 11/11; full Python 1,099/1 skipped; final deployment and health verified.

---

# Gates: Alpaca hackathon product release

Scope: Deliver a judge-ready guest product and a fail-closed Alpaca-paper
executor that reuses Cipher's audited decision engine without creating live or
browser order authority.

- [x] H1: One typed guest catalog owns all safe panels and tickers, includes an Autopilot landing panel, and every guest panel declares live, hybrid, demo, or locked behavior.
  CHECK: node --test cipher-system/web/test/guest-catalog.test.mjs
  EXPECT: /fail 0/
  EVIDENCE: Catalog suite passed 3/3; one catalog contains 28 unique panels, six sections, four explicit behavior modes, and 12 shared tickers.

- [x] H2: Every guest panel renders substantive labelled content at desktop and mobile sizes, with live market panels falling back deterministically on provider failure and no private/system controls visible.
  CHECK: cd cipher-system/web && CIPHER_E2E_URL=https://cipher-main.tail39504f.ts.net:8443 npx playwright test e2e/guest-complete-audit.spec.ts
  EXPECT: /2 passed/
  EVIDENCE: Hosted Chrome traversed all 28 panels at 1440x900 and 390x844 in 23.2 seconds; 2/2 passed with no console/page/private-request/overflow failures. The audit found and fixed the missing guest mobile hamburger.

- [x] H3: The Alpaca broker adapter rejects non-paper credentials and non-paper hosts, submits idempotent limit orders only after an approved intent, and normalizes submit/cancel/order/position/account responses.
  CHECK: ./.venv-research-py312/bin/pytest -q cipher-system/tests/test_alpaca_paper_broker.py
  EXPECT: /passed/
  EVIDENCE: Adapter suite passed 4/4; the additional safety suite proves the only endpoint exception is the hardcoded paper host and limit-only payload.

- [x] H4: Existing shadow/local-paper behavior remains unchanged, while the opt-in Alpaca-paper backend persists intent before submission and blocks on broker or reconciliation uncertainty.
  CHECK: ./.venv-research-py312/bin/pytest -q cipher-system/tests/test_paper_executor_runtime.py cipher-system/tests/test_alpaca_paper_runtime.py cipher-system/tests/test_paper_executor_recovery.py
  EXPECT: /passed/
  EVIDENCE: Focused runtime/recovery/security suite passed; filled, failed, exit, unknown-position, and explicit paper-forward-test promotion paths are covered. Deployed reconciliation passed with zero unknown positions.

- [x] H5: Authenticated status exposes the agent phase, evidence-backed decision trace, paper account readiness, broker orders, and reconciliation without exposing credentials or adding a browser order-submission route.
  CHECK: ./.venv-research-py312/bin/pytest -q cipher-system/tests/test_autopilot_status.py cipher-system/tests/test_alpaca_paper_status.py && node --test cipher-system/app/test/hosted-auth-boundary.test.mjs
  EXPECT: /passed[\s\S]*# fail 0/
  EVIDENCE: Status/auth suite passed; deployed status reports paper backend ready, ACTIVE paper account, reconciliation passed, zero unknown positions/orders, and no secret/account id. Hosted boundary remains read-only.

- [x] H6: The sanitized hackathon distribution is MIT-licensed, containerized, public-fixture-only, secret-free, and runnable from its documented commands.
  CHECK: ./.venv-research-py312/bin/python cipher-system/scripts/build_hackathon_release.py --check
  EXPECT: /hackathon-release-ok/
  EVIDENCE: Release check passes with 373 allowlisted files; public repo is https://github.com/ajtopper2412-crypto/cipher-alpaca-agent and includes MIT, Docker/Compose, CI, README, and verified guest screenshots.

- [x] H7: Python, Node, web lint/type/build, research/live-order safety, and published-tree synchronization all pass after integration.
  CHECK: ./.venv-research-py312/bin/pytest -q cipher-system/tests && node --test cipher-system/app/test/*.test.mjs && node --test cipher-system/web/test/*.test.mjs && cd cipher-system/web && npm run lint >/dev/null && npm run typecheck >/dev/null && npm run build >/dev/null && cd ../.. && ./cipher-system/scripts/sync_web_build.sh --check
  EXPECT: /In sync: app\/public matches web\/out\./
  EVIDENCE: Python 1,112 passed/1 skipped; app Node 33/33; web Node 65/65; web lint/type/build passed; release audit passed; published tree is in sync; hosted guest audit passed 2/2; public submission CI passed.

- [x] H8: A dated audit records exact guest-panel counts, paper-account canary evidence, order-boundary proof, test results, deployment health, submission artifacts, and deliberately deferred post-hackathon work.
  EVIDENCE: cipher-system/docs/audits/alpaca_hackathon_product_release_2026-08-23.md
