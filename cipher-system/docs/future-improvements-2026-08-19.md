# Cipher — Future Improvements Plan (2026-08-19)

The previous backlog (`future-plan-2026-08-18.md`) is fully executed and
verified (1027 passed, 1 skipped, `master` = `4471d44`). This document is the
next layer: what comes after the A/B/C backlog, grounded in the live state
verified on 2026-08-19. It is product work, not submission work. Same rules:
every item names the code to touch and the acceptance evidence required; no
item is "done" without its gate.

## 0. Verified state on 2026-08-19 (context for this plan)

| Area | Verified state |
|---|---|
| Test suite | 1027 passed, 1 skipped |
| Services | core/web/gex/executor/health-monitor/tradier all healthy; backup runs clean (status 0) |
| Fronttest pass | every minute; 7 portfolios; C05 registered; QQQ disabled |
| Cohort report | V6 PUT 0.5→1: 1 win + 1 open · QQQ: losses recorded · MU: 2 losses · C1/P1/C05: idle by design |
| Autopilot executor | healthy, shadow mode, 4 workers, 0 restarts; **first fill still pending** — today (Aug 19) is the first real market window since the config fix |
| Earnings book | 12 positions, expiry 08-21 (settles this week) |
| Health monitor | reports `healthy`, 30 units, only on anomaly |
| **Disk** | **87% used, 33G free** — 78G tradier_stream.sqlite, 26G events, 12G live chains, 9.8G historical options, 1.4G parquet, 131G runtime total |

**Two gaps found during this planning pass:**

1. **Fronttest signals carry no frozen config hash.** The roadmap Phase 0 gate
   ("frozen hash of each program configuration to every signal") is met in
   `core/prospective_fronttests.py` (`payload["configuration_sha256"]`) but
   **not** in `core/fronttest_portfolios.py` — the `signals` rows there store
   the raw `detect_signals` payload with no reference to the spec's
   `config_json` hash. The `portfolios.config_json` exists, so this is a small
   additive change.
2. **Disk runway is the top operational risk.** At current capture rates
   (tradier stream alone is 78G), 33G free is weeks, not months. Storage
   compaction was roadmap Phase 4; it is now a P0.

---

## P0 — correctness and runway (do first)

### P0-1. Autopilot first-fill verification (watch item — today, Aug 19)

**Context.** Executor healthy and fixed config loaded, but no qualifying card
has flowed through since. Today is the first market day with the fixed
pipeline; the pass runs 06:30 ET and the entry window is 09:35–11:30 ET.

**How.** Watch `runtime/data/paper_runtime/autopilot/cycles/2026-08-19.jsonl`
and `journalctl -u cipher-paper-autopilot-executor.service -f` during the
window. Expect `paper_confirmations_submitted` with `confirmed >= 1`, then a
row in `paper_positions`/`paper_orders`, then mark/exit events.

**Gate.** One real paper position enters `autopilot_shadow.sqlite` from a live
scan, or every rejection carries a documented skip code
(`SKIPPED_*`, not a generic error).

### P0-2. Data-plane runway: compaction plan with checksums + restore test FIRST

**Context.** Disk at 87% (33G free). The largest stores are raw capture
artifacts: `data/tradier_stream.sqlite` (78G), `data/tradier_stream_events`
(26G), `data/live_option_chains` (12G), `data/historical_options` (9.8G).
Per the roadmap's explicit exclusion, nothing gets compacted before a
checksummed manifest and a tested restore.

**How.**
1. Build a read-only **coverage catalog** script: per store, per day, row
   counts, byte sizes, and last-touched date — published to
   `runtime/data/coverage_catalog.json`.
2. Write a **manifest + restore test**: snapshot a bounded slice of each big
   store (e.g. one capture day), record SHA-256, delete the slice in a
   staging copy, restore from the manifest, and assert byte-identical.
3. Only after (2) passes, apply the same mechanism to the live stores:
   retention policy for expired-chain captures and compressed event archive.
4. Wire the catalog into the health monitor (new disk-runway check:
   `disk_free_gb < 40` → anomaly).

**Gate.** Restore test green for every store it touches; coverage catalog
published; health monitor flags disk runway; disk usage stops growing
unboundedly (retention policy documented in `docs/`).

### P0-3. Frozen config hash on every fronttest signal

**Context.** Gap found in planning: `fronttest_portfolios.py` signals don't
carry the spec's `configuration_sha256`, so a rule edit after a cohort starts
is not provably detected. The prospective programs already do this.

**How.** In `core/fronttest_portfolios.py`, at the signal insert site, compute
`_configuration_hash(spec)` from the spec's own config (the same frozen
dataclass that writes `portfolios.config_json`) and add it to the stored
payload. Backfill existing `signals` rows from their portfolio's current
`config_json` (documented as "hash as of backfill date", since the historical
payload is already frozen in `payload_json`).

**Gate.** Every `signals` row (new and backfilled) has
`configuration_sha256`; a test asserts that editing a spec config changes the
hash for new signals while old payloads remain byte-identical.

### P0-4. Cloudflare tunnel + Supabase Site URL (user dashboard actions, no code)

**Context.** VM wiring verified complete; only the Zero Trust org + token
(GCP secret `cipher-cloudflare-tunnel-token`) and the Supabase Site URL
(project `ipcsgrijatnnsbpguojl` → Authentication → URL Configuration →
`https://web-finance-dashboard.vercel.app`) are missing. Cannot be automated
from this VM.

**Gate.** `curl` to the tunnel hostname returns the core health payload with
auth enforced; email-confirm redirect resolves back to the Vercel app.

---

## P1 — daily trader value

### P1-1. Roadmap Phase 1: task-based navigation + Ticker Workbench

**Context.** The roadmap's largest UX uplift is untouched: default navigation
Today → Discover → Analyze → Plan → Review, one Ticker Workbench (chart +
key levels + Night Vision + flow + GEX + chain + alerts + notes), and a
prioritized feed replacing dense card grids. Everything else in the plan is
smaller than this.

**How.** Follow the roadmap Phase 1 deliverables in slices: (a) consistent
status vocabulary + empty/loading/stale states on the existing primary
surfaces first (bounded, testable), then (b) the Workbench layout, then
(c) the five-task navigation.

**Gate.** Browser journey: new user completes discover → validate → compare →
journal in five minutes without opening a lab; no primary page shows an
unqualified confidence % as win probability; keyboard/contrast/390 px checks
pass.

### P1-2. C05 capture verification (watch item)

**Context.** `v6_nvda_c05` registered and appears in the pass output, but the
V6 study emits C05 rarely and none since registration.

**How.** No code unless a qualifying event produces nothing. When the next C05
emits, verify a `signals` row with `portfolio_id = v6_nvda_c05` and a
`configuration_sha256` (per P0-3).

**Gate.** First prospective C05 signal recorded with frozen config hash, or a
documented zero-signal session for that day.

### P1-3. Weekly ops review cadence

**Context.** Health monitor + cohort report exist as standalone scripts but
nothing aggregates them weekly.

**How.** Add a `--weekly` flag or a small wrapper that runs
`cohort_tracking_report.py` + health-monitor state + earnings-book settlement
summary into one Discord message on Friday (the earnings timer already fires
08:15 ET Mon–Fri; piggyback the Friday run).

**Gate.** Friday digest contains cohort table + health state + settlement
summary; idempotent (rerun produces no duplicate).

---

## P2 — validation quality

### P2-1. Shared evidence contract across scanner / Night Vision / evaluator

**Context.** Roadmap Workstream A remains open: the historical evaluator,
scanner, Night Vision replay, and fronttest passes still carry separate
payload shapes, even though replay integrity (SHA-256 snapshot identity) is
done.

**How.** Follow the roadmap's five-step order: schema + frozen fixtures →
adapters around existing payloads → scanner/chart share the snapshot →
evaluator/executor share the signal record → parity report for
historical/replay/prospective.

**Gate.** A frozen fixture produces byte-equivalent signal decisions across
all three modes, or differences are explicit fill-model deltas documented in
the parity report.

### P2-2. Backtest statistical hardening (roadmap Phase 3)

**Context.** Backtest phase audits exist but statistical claims
(holdout/close-out discipline) are not yet unified into one evaluator-facing
gate.

**How.** Enumerate current backtest claims and their supporting tests; add the
missing statistical gates (sample size, holdout separation, lookahead checks)
as tests, per the roadmap Phase 3 deliverables.

**Gate.** Every backtest-derived claim in the UI/docs maps to a passing
statistical test or is labeled exploratory.

---

## P3 — scale / agent / deployment (after P0–P2)

- **Research agent golden tasks** (roadmap Phase 5): evaluate the fleet on
  golden questions, stale/provider-conflict cases, and prompt-injection
  attempts before the agent is claimed product-grade.
- **Storage compaction execution** (roadmap Phase 4): apply the P0-2
  manifest/restore mechanism to the 70 GB stores once the restore test is
  green and the retention policy is documented.
- **Deployment quality** (roadmap Phase 6): clean-machine install rehearsal
  and rollback drill, using the sanitized export builder; the public demo
  stays a consumer of the hardened product, not its foundation.

---

## Not in scope (unchanged project boundary)

- Live trading, broker adapters, order endpoints, autonomous capital actions.
- Reconstructing option quotes that were never captured.
- Treating missing OI/gamma/quotes as zero.
- Presenting public-OI GEX as known dealer inventory.
- Optimizing TSLA/radar/fronttest rules after prospective observation begins
  (the 20-closes / 4-week rule from the future plan governs).
- Publishing the private production dataset or credentials.

---

## Suggested execution order

1. **P0-1** — watch today's 09:35–11:30 ET window (no code needed).
2. **P0-3** — config hash on fronttest signals: small, self-contained,
   closes a roadmap Phase 0 gate.
3. **P0-2** — coverage catalog + restore test first, then retention; the
   disk runway is the only thing on a clock.
4. **P1-3** — weekly digest wrapper (small, improves the review cadence).
5. **P1-1** — Phase 1 UX slices, starting with status vocabulary and
   empty/loading/stale states.
6. **P2-1 / P2-2** — evidence contract and statistical gates before any new
   large strategy work.

User actions that stay open regardless: Cloudflare tunnel token (P0-4) and
Supabase Site URL (P0-4).
