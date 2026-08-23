# Cursor agent handoff prompt — Cipher — 2026-08-23

Copy everything below into the next Cursor agent. It is intentionally
self-contained.

---

You are taking over Cipher, a local-first stocks and options research
workstation. Work from the canonical Git checkout:

```text
/home/aarav/Aarav/cipher/cipher-github
```

The active product is:

```text
/home/aarav/Aarav/cipher/cipher-github/cipher-system
```

The compatibility symlink used by deployed services is:

```text
/home/aarav/Aarav/cipher/cipher-system -> cipher-github/cipher-system
```

## Non-negotiable instructions

1. Read `/home/aarav/Aarav/cipher/cipher-github/AGENTS.md` first. For frontend
   work also read `cipher-system/web/AGENTS.md` and the relevant Next 16 docs in
   `web/node_modules/next/dist/docs/`.
2. Preserve the safety boundary. Browser and core research routes are read-only.
   The only broker submission code allowed is the isolated
   `core/paper_executor/alpaca_paper_broker.py`, hardcoded to
   `https://paper-api.alpaca.markets`, limit-only, after persisted intent and
   reconciliation. No live broker hostname, live order, browser order route, or
   implied live authority. Governance ends at `LIVE_REVIEW_REQUIRED`.
3. Missing market data remains unknown. Never replace absent IV, Greeks, open
   interest, quotes, bars, news, or outcomes with zero.
4. GEX is a public-open-interest heuristic, not verified dealer positioning.
5. Do not create, update, promote, or submit a separate lablab/Alpaca hackathon
   repository. A premature public repository named `cipher-alpaca-agent` was
   created earlier; the user explicitly corrected that the event is not open
   and that product was never tested. Leave it untouched. Remote deletion needs
   an explicit user instruction.
6. Prefer direct focused edits and direct pushes to the main repo when requested;
   the user does not want unnecessary pull requests.
7. Never print `.env`, `/etc/cipher/cipher.env`, auth JSON, Supabase tokens,
   brokerage keys, webhook values, or secret-manager output.
8. Runtime evidence is outside Git under `/home/aarav/Aarav/cipher/runtime` and
   must not be reset, deleted, backfilled, or rewritten to manufacture results.

## Product architecture

```text
Browser
  -> Node same-origin server/proxy: cipher-system/app/server.mjs (127.0.0.1:8283)
  -> Python read-only research API: cipher-system/core/app.py (127.0.0.1:8282)
  -> provider adapters, scanners, analytics, local evidence stores

Static source: cipher-system/web (Next 16.3.1, React 19, TypeScript, Tailwind v4)
Published tree: cipher-system/app/public
Build publisher: cipher-system/scripts/sync_web_build.sh

Isolated paper runtime:
core/paper_executor/*
  -> deterministic evidence/risk gates
  -> local shadow ledger or reconciled Alpaca PAPER limit orders only
  -> no browser/core order endpoint
```

Important modules:

- `core/scanner.py`: Setup Scanner and setup scoring.
- `core/exposure.py`: Strike Matrix/GEX calculations.
- `core/gex_capture.py`: raw + normalized GEX history.
- `core/option_history.py`: daily option-surface snapshots, 25-delta skew,
  constant-30-day IV history.
- `core/skew_map.py`: cross-name skew map added 2026-08-23.
- `core/options_terminal.py` / `core/terminal_service.py`: chain and structure
  research.
- `core/market_research_agent.py`: scheduled research reports.
- `core/research_platform/`: provenance, experiments, promotion, prospective
  validation, governance.
- `core/paper_executor/`: shadow and Alpaca-paper automation.
- `app/server.mjs`: auth boundary, guest allowlist, static server, API proxy.
- `web/src/components/PanelHost.tsx`: single label-to-panel router.
- `web/src/lib/guestCatalog.ts`: one typed 29-panel guest catalog and 12-symbol
  universe.
- `web/src/components/panels/SkewMap.tsx`: live stored-data skew UI.
- `cipher-system/DESIGN.md`: current interface/data-semantics contract.

## Current hosted product

- Local UI: `http://127.0.0.1:8283`
- Core: `http://127.0.0.1:8282` (loopback only; authenticated internally)
- Persistent browser URL:
  `https://cipher-main.tail39504f.ts.net:8443`
- Tailscale Funnel is the current browser exposure. Cloudflare was deliberately
  skipped.
- Hosted mode uses Supabase Auth plus an explicit guest session. Guest mode has
  a bounded 12-name universe: SPY, QQQ, AAPL, MSFT, NVDA, AMZN, GOOGL, META,
  TSLA, AMD, MU, AVGO.
- Developer mode is resolved from trusted Supabase/operator-owned fields, never
  from client metadata. It enables diagnostics, not live authority.
- Guest mode is read-only and capability filtered. Demo, live, hybrid, and
  locked panels are explicitly labelled.

Current design language:

- near-black background, flat border-defined surfaces;
- amber is the only action/selection accent;
- conventional green positive and red negative market movement;
- Space Grotesk for navigation/prose, JetBrains Mono for market data;
- 4px spacing base, 6px general radius, dense controls;
- visible focus rings, skip link, mobile drawer, responsive internal scrollports;
- loading, missing, stale, demo, and live are separate data states.

## Skew Map implementation

The source method is documented in
`docs/skew_map_implementation_brief_2026-08-23.md`.

```text
raw skew        = mirrored 25-delta put IV - mirrored 25-delta call IV
normalized skew = raw skew / ATM IV
x-axis          = aligned one-month underlying return
y-axis          = raw skew
```

`/api/skew-map` reads the latest stored OPRA-derived observation per bounded
ticker, then requests daily bars and discards bars after the surface date. It
shows IV/quote coverage, sessions, feed, expiry, quality reasons, and missing
aligned returns. The four quadrant labels are research priority descriptions,
not predictions. Current local history is only five distinct sessions, so every
point is provisional. Do not claim edge. Next additions, after adequate data,
are earnings-in-expiry joins, week-over-week skew change, sector breadth, and
outlier contract verification.

## Data currently ingested or queried

- Alpaca stocks: SIP preferred, IEX fallback; quote/trade and intraday/daily bars.
- Alpaca options: OPRA preferred; contract metadata, latest quote/trade, Greeks,
  IV, volume, public OI and OI observation date.
- Tradier: read-only stream collector for quote/trade/flow context; active order
  and account endpoints are forbidden.
- Yahoo/yfinance: delayed, unofficial read-only fallback for underlying bars,
  quotes, limited option chains, company/earnings context. Every payload carries
  a caveat.
- Finvizfinance: delayed discovery/screener inputs only; candidates require
  Alpaca validation.
- Yahoo RSS/news and company/event-context capture.
- Browser capture imports and GCS mirror for explicitly captured scanner/GEX
  evidence.
- Supabase: authentication and RLS-scoped user state; provider credentials remain
  server-side.
- Local stores under runtime include option history, GEX history/snapshots,
  Tradier stream events, scanner history, research reports, strategy artifacts,
  paper ledgers, earnings paper state, alerts, journal/watchlist state, and
  backups. These are evidence, not source files.

Known data limits:

- Historical option quotes that were never captured do not exist and must not be
  reconstructed as if point-in-time.
- Option surface history currently has 26 names and five distinct sessions.
- Public OI is typically prior-session positioning, not verified dealer inventory.
- Some older underlying databases are stale relative to newer option snapshots;
  joins must be timestamp-aligned.
- Earnings paper fills were formerly heuristic; the scorecard is a failure signal,
  not validated option P&L. See
  `docs/audits/earnings_repair_and_repo_sweep_2026-08-22.md`.

## Deployed services and schedules

Core long-running services currently include:

- `cipher-core.service`: active, read-only API.
- `cipher-web.service`: active, hosted guest/auth UI.
- `cipher-gex.service`: active GEX capture loop.
- `cipher-tradier.service`: active read-only stream collector.
- `cipher-mcp-bridge.service`: active read-only research MCP bridge.
- `cipher-paper-autopilot-executor.service`: active fail-closed Alpaca-paper
  executor.
- `cipher-secrets.service`: materializes secrets server-side.
- `tailscaled.service`: active.

Important timers include browser sync/import, market alerts, backups, operational
metrics, data-health, event context, FinBERT context, cluster alerts, earnings
digest, paper autopilot, market research, option history, Discord portfolio
recap, fronttest portfolios, prospective fronttests, and paper training.

Inspect without leaking secrets:

```bash
systemctl status cipher-core cipher-web cipher-paper-autopilot-executor --no-pager
systemctl list-timers --all --no-pager | rg 'cipher-'
journalctl -u cipher-core -u cipher-web -n 100 --no-pager
```

Autopilot status as of this handoff:

- paper executor service is active/running;
- isolated broker adapter is Alpaca paper only;
- the latest audited state before the next session was healthy/no setup, with
  reconciliation passed and no open/unknown positions;
- the ledger retained 34 signal batches, 191 cards, 14 episodes, zero contract
  candidates/orders/positions from Aug 21 because the then-stale executor unit
  lacked its market-data environment. The system was fixed but history was not
  backfilled;
- Monday Aug 24 is the first prospective end-to-end session after the repair.

Read `docs/audits/reliable_local_paper_autopilot_2026-08-23.md` before changing
paper automation. A correct zero-trade day is acceptable. Every confirmation
must become a candidate, a classified strategy rejection, or a classified data
failure. Never force a fill.

## Installed skills and how to apply them

Always follow a named skill's full `SKILL.md`; do not infer instructions from its
name.

Project/global skills available during the prior Codex work:

- `ponytail` — use on every coding task; choose the smallest standard solution
  that works, avoid dependency/framework churn and speculative abstractions.
- `unlazy` — use for substantial autonomous work; maintain `GATES.md`, finish
  leaves against executable checks, and run its gate checker before declaring
  done.
- `taste-skill` — shell/polish/visual judgment. It is not a dashboard generator;
  use its hierarchy and craft guidance selectively.
- `redesign-skill` — inspect the existing product before changing it; preserve
  functionality while materially improving the visual system.
- `web-design-guidelines` — Vercel interface/accessibility audit rules. Re-fetch
  the latest linked rules when doing another audit.
- `playwright-cli` — installed into `.claude/skills/playwright-cli`; use headed
  browser sessions, screenshots, console/network inspection, and mobile checks.
- `ruflo` — orchestration/MCP skill at `.agents/skills/ruflo` in the active
  environment. Codex has the Ruflo MCP command registered, but `ruflo doctor`
  currently reports this checkout itself is not initialized: no local config,
  daemon, or memory DB. Do not claim otherwise; run `npx ruflo@latest init` only
  if the user wants project-level Ruflo state.
- `skill-installer`, `skill-creator`, `plugin-creator`, `plugin-management` —
  install/create/manage Codex capabilities when explicitly required.
- `imagegen` — raster asset generation/editing only, not code-native UI work.
- `openai-docs` — official guidance for Codex/OpenAI products and APIs.
- `deep-research-work:deep-research` — only when the user explicitly requests
  deep research.
- Finance plugin skills:
  `aarav-finance-agent`, `research-orchestrator`, `equity-research`,
  `earnings-dislocation`, `gex-vex`, `multi-bagger`, `options-analysis`, and
  `valuation`. Use the narrowest matching finance skill and preserve citations,
  point-in-time caveats, and no-advice language.

Installed sources:

```text
https://github.com/leonxlnx/taste-skill
https://github.com/vercel-labs/agent-skills/tree/main/skills/web-design-guidelines
https://github.com/voltagent/awesome-design-md
https://github.com/microsoft/playwright-cli
```

Global skill locations during Codex work included:

```text
/home/aarav/.codex/skills/ponytail
/home/aarav/.codex/skills/unlazy
/home/aarav/.codex/skills/taste-skill
/home/aarav/.codex/skills/redesign-skill
/home/aarav/.codex/skills/web-design-guidelines
```

## MCP status

`codex mcp list` reported:

```text
ruflo                 npx ruflo@latest mcp start       enabled
equibles              https://mcp.equibles.com/mcp    enabled, not logged in
flashalpha-earnings   https://lab.flashalpha.com/mcp/earnings enabled, not logged in
flashalpha-gex        https://lab.flashalpha.com/mcp/gex      enabled, not logged in
robinhood-trading     https://agent.robinhood.com/mcp/trading enabled, not logged in
```

Treat “enabled” as configuration, not proof of authentication or successful
calls. Do not use the Robinhood trading connector: it conflicts with Cipher's
no-live-order boundary. The FlashAlpha/Equibles connectors may be used only for
read-only research after authentication and source/freshness verification.

Cipher also runs `cipher-mcp-bridge.service`, its own read-only HTTP research
bridge. Inspect the active bridge/tool allowlist before relying on it; never add
order authority.

## Browser tooling

Playwright CLI version installed globally during this phase: `0.1.18`; Chrome
for Testing 152 was downloaded. This Linux VM has no desktop display, so headed
sessions need Xvfb:

```bash
xvfb-run -a bash -c '
  playwright-cli -s=ui open https://cipher-main.tail39504f.ts.net:8443 --headed &&
  playwright-cli -s=ui resize 1440 900 &&
  playwright-cli -s=ui snapshot &&
  playwright-cli -s=ui screenshot --filename artifacts/check.png &&
  playwright-cli -s=ui console &&
  playwright-cli -s=ui close
'
```

Do not use arbitrary sleeps when a locator or snapshot can prove readiness. Keep
sessions named and close them. Runtime `.playwright-cli` logs are disposable and
should not be committed. The project E2E suite is the authoritative full guest
traversal:

```bash
cd cipher-system/web
CIPHER_E2E_URL=https://cipher-main.tail39504f.ts.net:8443 \
  xvfb-run -a npx playwright test e2e/guest-complete-audit.spec.ts
```

The last run passed desktop and mobile, all 29 panels, with zero console/page
errors, failed responses, private requests, or document overflow. Visual evidence
is in `artifacts/ui-before-guest-desktop.png` and
`artifacts/ui-after-skew-desktop.png`.

## Verification and deployment commands

Focused:

```bash
./.venv-research-py312/bin/pytest -q cipher-system/tests/test_skew_map.py
node --test cipher-system/app/test/*.test.mjs
node --test cipher-system/web/test/*.test.mjs
cd cipher-system/web && npm run lint && npm run typecheck && npm run build
```

Full relevant gate:

```bash
./.venv-research-py312/bin/pytest -q cipher-system/tests
node --test cipher-system/app/test/*.test.mjs
node --test cipher-system/web/test/*.test.mjs
cd cipher-system/web && npm run lint && npm run typecheck && npm run build
cd ../..
./cipher-system/scripts/sync_web_build.sh
./cipher-system/scripts/sync_web_build.sh --check
git diff --check
```

Deploy after checks:

```bash
./cipher-system/scripts/sync_web_build.sh
sudo systemctl restart cipher-core.service cipher-web.service
systemctl is-active cipher-core.service cipher-web.service
curl -fsS http://127.0.0.1:8282/health
curl -fsS http://127.0.0.1:8283/api/health
```

The core's non-health API routes require the internal proxy context. Do not
bypass that boundary or expose the proxy token to test directly; use the hosted
guest/auth route or a unit-level injected provider.

Before claiming completion, also run:

```bash
node /home/aarav/.codex/skills/unlazy/scripts/gate-check.mjs GATES.md
```

## Current verification baseline

- App Node: 33/33 passed.
- Web Node: 65/65 passed after this phase.
- Skew unit tests: 2/2 passed.
- ESLint, strict TypeScript, and production build: passed.
- Hosted guest E2E: 2/2 passed across desktop/mobile and all 29 panels.
- Headed Playwright CLI: Skew Map loaded, 12 names, overflow 0, console 0.
- Full Python suite, including research-only guard: 1,114 passed, 1 skipped.
- Core and web services: active after deployment.
- Published web tree: synchronized after deployment.

Consult `docs/audits/options_ui_skew_overhaul_2026-08-23.md` for before/after
evidence and remaining UI limits.

## Git and repository state

- Main remote: `https://github.com/aaravjj2/cipher.git`
- Branch: `master`
- Baseline before this phase: `a9de350`.
- Preserve unrelated user changes and runtime symlinks.
- `.env`, databases, caches, browser logs, screenshots with private data, and
  runtime state are ignored/non-source.

At the beginning of your work run:

```bash
git status --short
git log -3 --oneline
git diff --check
```

Do not reset a dirty tree. Identify whether changes belong to the completed UI
phase or the user before editing.

## Highest-priority next work

1. Observe the Aug 24 paper session end to end. Prove each confirmed setup has a
   candidate, classified rejection, or classified data failure; reconcile any
   paper fills/exits and Discord recap exactly to the ledger.
2. Keep capturing option surfaces daily. Do not promote Skew Map interpretations
   until at least 20 homogeneous sessions (still provisional), preferably 60+.
3. Join earnings dates to each plotted expiry and expose week-over-week raw skew
   change plus sector breadth once history supports it.
4. Continue UI migration panel-by-panel: Options Terminal, Night Vision, Setup
   Scanner, and portfolio review are the highest-value dense surfaces. Avoid a
   whole-repo styling rewrite.
5. Improve prospective earnings/options scorecards only from real captured
   bid/ask/contract marks. Never retrofit missing historical quotes.
6. Add broker portability only behind the same interface and paper-only safety
   contract. Normal Alpaca market-data accounts and delayed fallbacks can power
   research; premium OPRA features must degrade honestly. Tradier/Webull order
   integration is not authorized.
7. Keep README and dated audits synchronized with actual deploy/test evidence.

End every future handoff with concrete test output, deployed service state,
uncommitted files, data caveats, and the single next operational risk. Do not
summarize “done” when prospective market-session evidence is still unavailable.

---
