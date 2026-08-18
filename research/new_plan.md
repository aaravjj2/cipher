# Cipher — Complete Repository Handover and Improvement Plan

**Document date:** 2026-08-11  
**State observed:** 2026-08-11, during this handover pass; repository state and validation claims are snapshots, not evergreen facts.  
**Purpose:** Handover to the next coding agent. This document replaces the previous
`new_plan.md` and is the current operational/research/UI plan.

> **Discoverability:** this file intentionally lives at `/home/aarav/Aarav/cipher/new_plan.md`,
> outside the canonical Git checkout. An agent starting in `cipher-github` must read it by
> absolute path. Do not create a second divergent `new_plan.md` inside `cipher-github` unless the
> user explicitly requests that migration.

> **Evidence rule:** Facts marked **VERIFIED** were checked against the current checkout or
> current validation output. Facts marked **HISTORICAL** came from earlier operational work and
> must be rechecked before being used as current status. Facts marked **UNVERIFIED** are leads,
> assumptions, or pending work. Do not turn a historical claim into a current claim without a
> fresh command, test, or live check.

---

## 1. Executive handover

Cipher is a local, read-only options research terminal. The active product is a Python market
research API plus a Node same-origin proxy/static server and a Next.js browser UI. It uses Alpaca
as the active market-data source in this checkout. The browser must never receive credentials.

The system has three related but distinct layers:

1. **Working product:** live quotes, option matrices, GEX/VEX surfaces, flow, scanners,
   watchlists, research panels, alerts, and saved local artifacts.
2. **Research infrastructure:** capture jobs, backtests, ranking/weight labs, governance,
   provenance, prospective validation, and a separate simulation/paper runtime.
3. **Operations:** local runtime state, generated build output, GCP/systemd timers, storage
   archives, backups, and the public access path.

The strongest current areas are the read-only execution boundary, server-side secret handling,
Alpaca-backed data path, local capture/research infrastructure, and frontend validation loop.The largest remaining risks are:

- the canonical repository contains uncommitted frontend work;
- the full Python suite is not currently confirmed green in this environment;
- the latest heatmap semantics have not been verified in a browser accessibility tree;
- the frontend/backend heatmap contract has just been tightened so unavailable, uncalculable, and partial-leg exposure stays unknown rather than becoming a false zero;

- several generated/static outputs are ignored and can drift unless explicitly synchronized;
- operational/GCP claims in older documents are not automatically current;
- duplicate outer/canonical/runtime paths can cause agents to inspect or edit the wrong tree.

**Immediate interpretation:** the frontend improvement work is implemented and validated at the
source/build level, but it is not yet consolidated into a clean committed checkpoint. The next
agent should first preserve and verify this checkpoint, then continue with focused UI state work.

---

## 2. Canonical paths and repository identity

### 2.1 Source-of-truth checkout

The active Git repository is:

```text
/home/aarav/Aarav/cipher/cipher-github
```

Current repository state at handover:

- **Branch:** `master`
- **HEAD:** `1fda62f` — `Correct the alerts panel, which still described the browser-only behaviour it no longer has`
- **Upstream:** `origin/master`
- **Ahead/behind:** no commits ahead were reported at the last inspection
- **Canonical `new_plan.md`:** does not exist inside `cipher-github`
- **This handover document:** `/home/aarav/Aarav/cipher/new_plan.md`

The outer path `/home/aarav/Aarav/cipher/cipher-system` is a symlink into the canonical
repository's active `cipher-system` tree. Use the canonical Git checkout for status, diffs, and
edits:

```bash
cd /home/aarav/Aarav/cipher/cipher-github
```

Do not treat the outer untracked-looking checkout, `prime-7day-worktree/`, historical backups,
or archived source trees as the active source of truth.

### 2.2 Runtime and generated paths

The active runtime uses symlinked or externally maintained state. In particular:

```text
cipher-system/.env
cipher-system/app/.env
cipher-system/data
cipher-system/logs
cipher-system/app/.scanner-ingest-token
```

may point into `/home/aarav/Aarav/cipher/runtime/`. Verify symlink targets before copying,
archiving, deleting, or calculating disk usage.

The browser build path is intentionally split:

```text
cipher-system/web/out       # Next.js build output
cipher-system/app/public    # directory actually served by app/server.mjs
```

`app/public` and `web/out` are generated/ignored output. A successful `npm run build` alone does
not update the served UI. Always run:

```bash
bash cipher-system/scripts/sync_web_build.sh
```

The script uses `rsync --delete`; do not hand-author files in `app/public`.

---

## 3. Active architecture

### 3.1 Request path

```text
Browser
  ↓
cipher-system/app/server.mjs
  - static app/public serving
  - same-origin API proxy
  - SSE proxy
  - scanner ingest route
  - authentication gate
  ↓
cipher-system/core/app.py
  - standard-library HTTP API
  - Alpaca market-data access
  - caches and rate-conscious polling
  - exposure/matrix calculations
  - scanners and research/status routes
  ↓
runtime/data
  - SQLite
  - JSON/JSONL
  - Parquet
  - GEX history
  - option-chain captures
  - governance and backtest artifacts
```

Relevant active files:

| File/path | Role |
|---|---|
| `cipher-system/core/app.py` | Read-only Python market-data/research API |
| `cipher-system/app/server.mjs` | Node same-origin proxy and static server |
| `cipher-system/app/auth.mjs` | Password gate/authentication behavior |
| `cipher-system/app/public/` | Generated browser build served by Node |
| `cipher-system/web/src/` | Next.js/React source UI |
| `cipher-system/core/scanner.py` | Setup Scanner and scoring |
| `cipher-system/core/cluster_backtest.py` | Local scan capture/forward scoring |
| `cipher-system/core/ranking_lab.py` | Rank-surrogate diagnostics |
| `cipher-system/core/weight_lab.py` | Local weight fitting/dumps |
| `cipher-system/core/gex_capture.py` | Alpaca-backed GEX history capture |
| `cipher-system/core/research_platform/` | Governance/provenance/experiment/promotion plane |
| `cipher-system/core/paper_executor/` | Shadow/paper simulation only; no broker orders |
| `cipher-system/tests/` | Python smoke and safety tests |
| `cipher-system/app/test/` | Node proxy/app tests |
| `cipher-system/web/test/` | Node web utility/contract tests |

### 3.2 Market-data boundary

Alpaca is the active source in this checkout:

- options: Alpaca OPRA where available; `indicative` only for UI/debug fallback;
- stocks: Alpaca SIP preferred, IEX fallback;
- option snapshots: quotes, trades, Greeks, IV, volume, pagination;
- option contract metadata: open interest and OI date;
- underlying quote/trade and OHLCV bars;
- latest option trades plus bid/ask for premium/side inference.

Credentials remain server-side in `cipher-system/app/.env` or environment variables. Never print,
commit, expose, or copy secrets into browser code.

GEX remains a public-open-interest heuristic, not verified dealer positioning:

```python
call_gex =  call_gamma * call_oi * 100 * spot**2 * 0.01
put_gex  = -put_gamma  * put_oi  * 100 * spot**2 * 0.01
net_gex  = call_gex + put_gex
```

Missing gamma or open interest is unknown. Do not silently convert missing inputs to zero.

### 3.3 Research/governance boundary

The research platform under `cipher-system/core/research_platform/` is additive; it does not
replace the active collectors, UI, historical archives, scanners, or paper executor.

The promotion ceiling is:

```text
LIVE_REVIEW_REQUIRED
```

That state requires human review and **does not authorize live execution**. Cloud writes are
disabled by default in the research platform. The browser/core product remains read-only.

---

## 4. Current Git and implementation state

### 4.1 Current dirty files — preserve and verify first

**State observed:** 2026-08-11, after the nullable-exposure improvement loop. Recheck with `git status`
before relying on this list.

The canonical working tree currently contains these uncommitted frontend changes:

```text
 M cipher-system/web/src/app/globals.css
 M cipher-system/web/src/components/panels/HeatmapGrid.tsx
 M cipher-system/web/src/components/panels/StrikeMatrix.tsx
 M cipher-system/web/src/components/panels/Trident.tsx
?? cipher-system/web/src/components/ui/skeleton.tsx
```

Generated `cipher-system/app/public/` output is ignored by Git but was rebuilt/synchronized in the
last completed loop. Do not conclude that a clean diff means the served build is current.

Do not reset these files before reading this document and checking the diff. They contain the
completed UI work described below. If a future agent chooses to abandon them, it must explicitly
run the validation suite and document the rollback.

### 4.2 Completed frontend work in this loop

#### Shared loading primitives

`cipher-system/web/src/components/ui/skeleton.tsx` now contains shared loading placeholders:

- `Skeleton`
- `SkeletonRegion`
- `SkeletonGrid`
- `SkeletonCards`

The loading region retains a real accessible status sentence while visual blocks are marked
`aria-hidden`. `globals.css` contains shimmer keyframes, reduced-motion behavior, and the
no-scrollbar helper used by the UI.

`SkeletonGrid` is integrated into:

- `StrikeMatrix.tsx`
- `Trident.tsx`

The previous Trident loading label referenced an undefined `ticker`; it now uses the aggregate
label `Loading live Trident exposure…`.

#### Shared heatmap legend

`HeatmapGrid.tsx` now exports `ExposureLegend`, used by Strike Matrix and Trident. It explains:

- positive exposure is purple;
- negative exposure is red;
- gold marks the largest absolute exposure;
- intensity is relative within each heatmap.

The wording intentionally avoids claiming that exposure color is bullish/bearish price
prediction or verified dealer positioning.

#### Heatmap semantics

Strike Matrix and Trident now expose read-only table semantics without changing their visual CSS
grid layout:

- table containers use `role="table"`;
- rows use `role="row"` with `display: contents` to preserve grid placement;
- headers use `role="columnheader"`;
- strike labels use `role="rowheader"`;
- exposure cells use `role="cell"`;
- accessible labels include metric, ticker, strike, expiration, DTE, formatted value, and the
  largest-absolute-exposure marker;
- Trident has a screen-reader-visible (`sr-only`) header row for Strike and exposure;
- Spot markers are represented as rows/cells with an accessible spot-price label.

These are read-only tables, not `role="grid"` widgets: no arrow-key interaction is claimed or
implemented. The `display: contents` approach preserves the existing CSS geometry but still
needs browser accessibility-tree verification in a future loop.

### 4.3 What has been validated

**Fresh validation snapshot:** 2026-08-11, after the nullable-exposure improvement loop:

- focused Python heatmap regression: **10 passed**;
- Python `compileall` for `core` and `tests`: passed;
- web TypeScript typecheck: passed;
- web ESLint: passed;
- Node app tests: **13/13 passed**;
- Node web tests: **34/34 passed** (including the heatmap accessibility/unknown-value source-contract test);
- production build through `scripts/sync_web_build.sh`: passed;
- generated output published to `cipher-system/app/public`;
- `git diff --check`: passed.

The current loop changed the matrix contract so `net_gex`/`net_vex` are nullable when their
metric-specific inputs are unavailable or only partially calculable, added per-side and
field-specific availability for GEX/VEX/OI/volume/mids, and updated Strike Matrix, Trident, Night
Vision, and the TypeScript API types accordingly. Genuine calculable zero values remain numeric
zero; a listed-but-uncalculable side remains unknown, while an absent option side does not poison a
valid one-sided net.

A prior validation also confirmed Tailwind emitted the `.sr-only` utility in generated CSS. Do not
remove the utility or replace the hidden headers with an unlabelled visual-only structure.

### 4.4 What has not been validated

The following are still pending or environment-dependent:

- a browser check of the protected public URL after the latest build; the browser agent reached `Cipher — Sign in` but could not proceed without credentials;
- an accessibility-tree check with Chrome/another screen reader for `display: contents` rows;
- a current full Python test run in this environment;
- a clean commit/checkpoint of the six frontend source/test changes;
- live GCP/systemd/timer/storage status in this current turn.

Historical documents say the Python baseline was 561 tests, but an earlier current-environment
collection attempt hit:

```text
ModuleNotFoundError: No module named 'huggingface_hub'
```

Therefore do not write “561 Python tests green” until the correct research environment has run the
suite successfully.

---

## 5. Ordered next-agent procedure

The next agent must work in small, validated loops. Do not restart the skeleton or legend work.

### Step 0 — Establish the checkpoint

Run from the canonical repository:

```bash
cd /home/aarav/Aarav/cipher/cipher-github
git status --short
git diff --check
git diff -- cipher-system/web/src/app/globals.css \
  cipher-system/web/src/components/ui/skeleton.tsx \
  cipher-system/web/src/components/panels/HeatmapGrid.tsx \
  cipher-system/web/src/components/panels/StrikeMatrix.tsx \
  cipher-system/web/src/components/panels/Trident.tsx
```

Confirm the diff matches §4. Do not discard it casually.

### Step 1 — Browser/accessibility verification of completed work

Use Chrome against the local app or the public URL only if the service is running and authenticated:

1. Load Strike Matrix and Trident.
2. Observe the loading state on a cold/reloaded panel.
3. Confirm skeleton blocks are visible and reduced-motion behavior remains respectful.
4. Confirm the legend is visible and its wording is accurate.
5. Inspect the accessibility tree for:
   - table;
   - header row/column headers;
   - strike row headers;
   - readable cell labels;
   - Trident hidden column headers.
6. Confirm sticky expiration headers remain visible during vertical scrolling.
7. Check desktop and narrow viewport behavior.

If Chrome/a11y inspection shows `display: contents` rows are omitted, replace the wrappers with
an explicit layout-preserving structure or a tested alternative. Do not claim the semantics are
complete based only on source grep.

### Step 2 — Add a focused semantic regression check

Existing web tests are Node `.mjs` contract/utility tests, not a full browser component harness.
Before adding dependencies, inspect their conventions. A low-risk regression can assert source
contracts if no renderer is available, but a browser/accessibility-tree test is preferable if the
existing environment supports it.

Minimum acceptance criteria:

- Strike Matrix has `role="table"`, a header row, and accessible cells.
- Trident has `role="table"`, two column headers, row headers, and accessible exposure cells.
- No `role="grid"` is introduced without implementing keyboard traversal.

### Step 3 — Finish loading and empty states

Audit every active panel rather than assuming the old “seven panels” count is current. Current
search candidates include:

- `FlowTape.tsx` — bare `loading…` text when no prints exist;
- `News.tsx` — inline `loading…` status;
- `GexReplay.tsx` — timeline payload `Loading…`;
- `OptionsBacktest.tsx` — `Loading…` result state;
- `Standing.tsx` — loading and empty table rows;
- `Holdings.tsx` — empty positions and insufficient-history states;
- `SetupScanner.tsx` — scanning/history/empty states;
- `NightVision.tsx`, `Spyglass.tsx`, `Alerts.tsx`, and Watchlists — inspect for stale or overly
  sparse states.

Use the shared skeleton primitive for meaningful loading regions, not for tiny inline status
labels. Every empty state should explain what would appear, why it is absent, and what the user
can do next when that is actionable. Preserve “unknown” and data-gap semantics; do not turn a
missing data condition into a false zero.

Acceptance gate:

- no major panel presents only a bare `Loading…` string for a long request;
- every empty state has a specific cause or next action;
- typecheck, lint, app tests, web tests, and build sync remain green.

### Step 4 — Audit scrollports and responsive layout

The historical Strike Matrix bug came from the CSS rule that `overflow-x: auto` forces the other
axis to become scrollable, stealing the sticky element’s intended scrollport. `FlowTape.tsx` is a
known good pattern with bounded height and both axes.

Inspect current overflow candidates:

- `Watchlists.tsx`
- `Spyglass.tsx`
- `Backtest.tsx`
- `StrategyCatalog.tsx`
- `NightVision.tsx`
- `SetupScanner.tsx`
- `StrikeMatrix.tsx`
- `Trident.tsx`

For each, identify the actual scroll owner, bounded height, `min-h-0`/`min-w-0` needs, sticky
headers, mobile behavior, and whether horizontal overflow is intentional. Do not make a broad
class replacement without reproducing the affected panel.

### Step 5 — Decide what to do with Trident’s visible controls

`Trident.tsx` contains FC, Auto, TR, and SP toggles. The source comment records that their full
semantics were not confirmed and some are currently visual/inert or only partially wired:

- FC could highlight largest upside/downside walls using existing matrix data;
- Auto should control the existing refresh interval if it is presented as a real toggle;
- SP would require a layout/split-pane decision with Night Vision;
- TR remains unconfirmed.

Do not silently invent meanings. Either wire one control against a documented behavior and test it,
or change its presentation so it is clearly unavailable/deferred. Prefer one focused control per
loop.

### Step 6 — Research integrity work, after UI stabilization

The first research question remains the wheel’s locked-window hurdle test:

> Does any parameterization clear the 4% excess-annualized-return hurdle out of sample on a
> locked evaluation window?

Run the existing study before buying new data. Use measured execution-cost provenance and keep
all quality gates intact. Report negative results as valid results. If no configuration clears the
hurdle, close out the wheel corpus and do not proceed to an unnecessary historical-control data
purchase. If one clears it, scope the next data/control work only to that configuration.

The entry-control chain download is deliberately downstream of this decision.

### Step 7 — Operations only with live access and explicit verification

The following historical operational items remain candidates, not current facts:

- GCS lifecycle rule: delete old `backups/` after the chosen retention period, but only transition
  `cold/` to a cheaper storage class; never add blanket deletion to `cold/` because it is the sole
  copy of captured live option chains.
- Password-hash durability: the app password hash was historically reported as only in
  `/etc/cipher/cipher.env` and not in backups; resolve through Secret Manager/privileged access or
  document password rotation recovery.
- Backup/restore drill: download an archived object, decompress it, and compare its hash to the
  recorded `source_sha256`.
- Timer health: inspect systemd status and recent journal output rather than trusting a document.

Do not run privileged GCP, systemd, credential, deletion, or production-impacting commands unless
the user explicitly requests the operational action and the command is reviewed first.

---

## 6. Validation gates

### 6.1 Frontend change gate

From `/home/aarav/Aarav/cipher/cipher-github`:

```bash
npm run typecheck --prefix cipher-system/web
npm run lint --prefix cipher-system/web
node --test cipher-system/app/test/*.test.mjs
node --test cipher-system/web/test/*.test.mjs
bash cipher-system/scripts/sync_web_build.sh
git diff --check
```

The web test glob is mandatory. Do not replace it with a directory path; Node resolves that
incorrectly in this project.

### 6.2 Python/source syntax gate

Use the environment that actually contains the project dependencies:

```bash
/home/aarav/Aarav/cipher/cipher-github/.venv-research-py312/bin/python -m compileall -q cipher-system/core cipher-system/tests
/home/aarav/Aarav/cipher/cipher-github/.venv-research-py312/bin/python -m pytest -q
```

If collection fails because of a missing dependency such as `huggingface_hub`, record the exact
failure and do not report the suite as green. Avoid installing packages or modifying environments
as a side effect of a UI-only loop unless explicitly requested.

### 6.3 Node syntax gate

For active server/launcher changes:

```bash
node --check cipher-system/app/server.mjs
node --check cipher-system/app/launcher.mjs
node --check cipher-system/app/public/app.js
```

### 6.4 Server/auth gate

For server or auth changes, verify in a running environment:

- unauthenticated `/` remains the login page;
- unauthenticated `/api/quote` remains `401`;
- anonymous `/api/health` exposes only the intentionally minimal health response;
- authenticated data routes still work;
- the gate fails closed if auth is enabled without a configured hash.

### 6.5 Research gate

For research changes:

- preserve locked windows and point-in-time boundaries;
- preserve `quality_approved`, POP floors, hurdle rates, and control-activity gates;
- preserve provenance strings for measured versus assumed execution cost;
- report exclusions and negative results;
- never infer live eligibility from paper eligibility;
- never add order/broker paths.

---

## 7. Hard safety rails

These rules are non-negotiable:

1. Never fabricate a number, verdict, status, source, or operational result.
2. No live orders, broker clients, order endpoints, auto-execution, or scheduled live-order runner.
3. Never lower a research gate merely to obtain a positive result.
4. Never expose or print credentials; secrets stay server-side.
5. Never delete local data before remote upload, read-back, decompression, and source-hash verification.
6. Never publish a port without the authentication gate active in the same change.
7. Preserve the GEX caveat: public OI is a heuristic, not verified dealer positioning.
8. Keep missing gamma/OI/data gaps visible as unknown; do not manufacture precision with zero.
9. `LIVE_REVIEW_REQUIRED` is a human-review state, not trading authority.
10. Do not edit stale/archive trees when the active canonical path is available.

Specific forbidden active-code concepts include:

```text
/v2/orders
submit_order
place_order
create_order
TradingClient
OrderClient
```

Paper/shadow simulation may model fills and positions, but it must not submit broker orders.

---

## 8. Known traps and accepted limitations

- **Path ambiguity:** the outer checkout, canonical checkout, symlinked `cipher-system`, runtime,
  and alternate worktree can show different Git/status behavior. Use absolute canonical paths.
- **Generated build drift:** build `web/out` is not served until `sync_web_build.sh` runs.
- **Ignored output:** `app/public` is ignored; its presence or absence is not shown in `git diff`.
- **Double symlink/data walks:** verify tar and backup tools traverse `cipher-system/data` into
  runtime state.
- **Python environments:** multiple virtual environments exist; a missing dependency does not
  prove a code failure, but it does block a green-suite claim.
- **In-memory login rate limiting:** a restart resets failed-attempt tracking; this is an accepted
  limitation tied to the long high-entropy password. If password length is reduced, revisit it.
- **Historical options limitation:** historical option bars/trades do not automatically provide
  historical Greeks and open interest; they are not a complete historical GEX replay.
- **Options backtests:** current options backtests may be bar/trade approximations where historical
  NBBO is unavailable.
- **Accessibility uncertainty:** `display: contents` is used to preserve CSS-grid layout while
  exposing rows; verify the actual accessibility tree before calling it complete.
- **UI wording:** avoid implying that purple/red means price direction; it describes signed
  exposure only.
- **Control semantics:** do not re-derive FC/TR/SP behavior from screenshots or invent behavior
  without a documented product decision.
- **Shell reliability:** use absolute paths; avoid pipelines that fail under `set -o pipefail`
  because an upstream command receives SIGPIPE.

---

## 9. Historical operational snapshot — recheck before relying

The previous plan recorded the following, but these are **HISTORICAL**, not freshly verified in
this handover:

- public access through `https://cipher-main.tail39504f.ts.net:8443` with a password gate;
- Tailscale Funnel routes for devspace/Cipher/VNC;
- approximately 101 GB free after live-chain archiving;
- successful archival of 67/67 chain files with verified decompressed hashes;
- backup, parquet-retention, chain-archive, market-alert, data-health, and cluster-alert timers;
- Telegram delivery for system/data alerts;
- Groq-backed Ask Cipher with provider fallback;
- GCS bucket growth and a blocked IAM lifecycle configuration;
- password hash stored outside the normal SQLite backup path.

Before writing any of these as current state, run the relevant live checks from
`infra/gcp-cipher-vm/README.md`, inspect systemd/journal output, and verify the public endpoint.
Do not perform destructive cleanup merely because an old document says it is needed.

---

## 10. Recommended seven-loop roadmap

Each loop should end with a green relevant suite, a diff review, and an updated handover note.

### Loop 1 — preserve and browser-verify the current checkpoint — SOURCE/BUILD COMPLETE; BROWSER CHECK PENDING

- inspect the five dirty source files;
- run the frontend gate;
- verify generated output and loading/legend/table behavior in Chrome;
- inspect the accessibility tree;
- preserve the working diff as-is unless the user explicitly requests a commit or push;
- do not commit or push as part of an autonomous improvement loop;
- if the user later requests a commit, create it only after the full relevant gate passes.

**Exit:** current work is reproducible, visible, and not accidentally lost.

### Loop 2 — regression coverage for heatmap semantics — COMPLETE AT SOURCE-CONTRACT LEVEL; BROWSER A11Y PENDING

- added `web/test/heatmap-accessibility.test.mjs` using Node's native test runner and source contracts;
- assert table/header/row/cell contracts;
- ensure no `role="grid"` appears without keyboard behavior;
- added a data-integrity contract: unavailable exposure renders `unknown`, while valid zero remains numeric;
- propagated metric-specific and per-side GEX/VEX availability plus nullable matrix types through the UI;
- added matrix-assembly regression coverage for zero, partial-leg, and absent-side cases;
- reran build sync; fresh web baseline is 34 tests and focused Python heatmap coverage is 10 tests;
- keep browser/accessibility-tree verification as the remaining follow-up because source contracts do not prove `display: contents` exposure.

**Exit:** source-level accessibility and unknown-value guards are complete; browser A11y remains open.

### Loop 3 — loading and empty-state pass — COMPLETE (SOURCE/BUILD VERIFIED; BROWSER AUTH GATE PENDING)

- converted major long waits in Flow Tape, News, GEX Replay, Options Backtest, and Standing to announced shaped loading regions using the shared skeleton primitives;
- distinguished pending, unavailable, empty, and retry/data-capture guidance rather than presenting slow or failed requests as empty data;
- guarded GEX Replay against stale ticker/snapshot responses and preserved unknown snapshot semantics;
- added source-contract coverage for the Plan 2 loading and empty-state branches.

**Verified exit:** typecheck, lint, **36/36 web tests**, **13/13 app tests**, **10 focused Python tests**, compileall, production build/sync, and `git diff --check` passed on 2026-08-11. Browser component inspection remains blocked by the protected login gate.

### Loop 4 — scrollport/responsive pass — IN PROGRESS (FIRST FIX VERIFIED)

- audited active dense-table candidates and selected GEX Replay as the concrete narrow-viewport defect;
- added a local horizontal scroll owner plus a justified minimum content width around the fixed-width strike profile, preserving its bounded vertical row scroll;
- added a source contract for the responsive scroll owner and reran the full relevant gate.

**Verified checkpoint:** GEX Replay, Spyglass Contract Search, and Strategy Catalog responsive scrollports passed typecheck, lint, **36/36 web tests**, **13/13 app tests**, **10 focused Python tests**, compileall, production build/sync, and `git diff --check` on 2026-08-11. Browser narrow-viewport verification remains pending behind the protected login gate. Continue auditing dense tables before marking this loop complete.

### Loop 5 — one confirmed Trident interaction — COMPLETE (SOURCE/BUILD VERIFIED; BROWSER PENDING)

- confirmed Auto's documented meaning against the existing Trident refresh timer;
- split the initial fetch lifecycle from the recurring timer so disabling Auto stops future
  background refreshes, re-enabling it creates one 60-second interval, and unmount cleanup clears it;
- left FC, TR, and SP unchanged because their full behavior remains outside this focused loop.

**Verified exit:** typecheck, lint, **38/38 web tests**, **13/13 app tests**, **10 focused Python tests**, compileall, production build/sync, and `git diff --check` passed on 2026-08-11. Browser interaction verification remains pending behind the protected login gate.

### Loop 6 — locked research decision — NOT STARTED

Immediately before execution, verify that `cipher-system/scripts/wheel_entry_control_study.py`,
`cipher-system/docs/backtest-findings.md`, the selected equity database, archive root, approved
universe file (if used), and locked start/end dates all exist and correspond to the intended study.
Then run the wheel first-order hurdle study with no peeking and measured costs. Record the outcome,
including a negative outcome, and decide whether the control-download work proceeds.

**Exit:** a durable research decision exists in the appropriate findings document.

### Loop 7 — operational evidence and documentation consolidation — NOT STARTED; PRIVILEGED

Only with live access and explicit authorization:

- perform a backup restore drill;
- verify timer health and disk trend;
- resolve or document GCS lifecycle/password-hash durability;
- update README and this plan so code, runtime, and claims agree.

**Exit:** no handover statement is stronger than the evidence supporting it.

---

## 11. Quick reference commands

```bash
# Enter canonical source
cd /home/aarav/Aarav/cipher/cipher-github

# Inspect state
git status --short
git diff --check
git log -3 --oneline --decorate

# Frontend validation and publish
npm run typecheck --prefix cipher-system/web
npm run lint --prefix cipher-system/web
node --test cipher-system/app/test/*.test.mjs
node --test cipher-system/web/test/*.test.mjs
bash cipher-system/scripts/sync_web_build.sh

# Python syntax/full suite (use the dependency environment that is actually installed)
/home/aarav/Aarav/cipher/cipher-github/.venv-research-py312/bin/python -m compileall -q cipher-system/core cipher-system/tests
/home/aarav/Aarav/cipher/cipher-github/.venv-research-py312/bin/python -m pytest -q
# If that venv is absent, report the environment limitation rather than claiming green.

# Active Node syntax checks
node --check cipher-system/app/server.mjs
node --check cipher-system/app/launcher.mjs
node --check cipher-system/app/public/app.js

# Research platform status
/home/aarav/.venvs/cipher/bin/python \
  cipher-system/scripts/run_research_platform.py status

# Research platform initialization/evidence import, only when appropriate
/home/aarav/.venvs/cipher/bin/python \
  cipher-system/scripts/run_research_platform.py init
/home/aarav/.venvs/cipher/bin/python \
  cipher-system/scripts/run_research_platform.py import-current-evidence

# Research study discovery (do not run without selecting locked inputs)
python3 cipher-system/scripts/wheel_entry_control_study.py --help
# Required inputs: --equity-db, --archive-root, --start, --end.
# Optional: --additional-archive-root, --universe-json, --mode, --replicates,
# --seed, --metric, --output-dir. Use an approved --universe-json; do not use
# --relax-pop or alter quality gates to force eligibility.
# Record the JSON report under the chosen --output-dir and update:
# cipher-system/docs/backtest-findings.md

# Operational inspection — read-only first
systemctl is-active cipher-core cipher-web cipher-tradier cipher-gex
systemctl list-timers 'cipher*' --no-pager
```

For Windows capture scripts and GCP deployment/restore/lifecycle commands, consult the active
README files rather than copying commands from an old report.

---

## 12. Final handover instruction

Start with the current dirty frontend checkpoint. Do not redo completed skeleton, ExposureLegend,
basic table semantics, unknown-value propagation, or partial-leg handling. The browser check is currently blocked by the
login gate; do not guess credentials. Move next to a loading/empty-state or scrollport defect that
can be demonstrated in the active UI, and revisit browser accessibility-tree verification when an
authenticated session is explicitly available.

Keep changes focused, keep the read-only boundary intact, run the validation gate after every
loop, synchronize the served build, and update this document whenever verified state changes.
