# Cipher — Post-submission Future Plan (2026-08-18)

This document supersedes the hackathon-oriented roadmap. It is the product
plan for Cipher as a personal research terminal: what works today, what is
being validated, and what gets built next — with acceptance gates, not
deadlines.

## 1. Where we are (verified 2026-08-18)

Full verification ran after the last commit (`56584ed`):

| Area | State |
|---|---|
| Test suite | **1021 passed, 1 skipped** (`.venv-research-py312`) |
| Compile + node checks | Clean (`server.mjs`, `launcher.mjs`) |
| `cipher-core` (8282) | Running, read-only, market data configured per session |
| `cipher-web` (8283) | 200, serving the exported Next.js build |
| Hosted demo (Vercel) | 200 in ~0.2s |
| Fronttest pass | Every minute; 7 portfolios (C05 registered), 84 signals, 14 positions, 1137 runs |
| Paper autopilot executor | Healthy: shadow mode, 4 workers, 0 restarts, reconciliation passed |
| Earnings paper book | 12 positions across report dates 08-18→08-20, expiry 08-21 |
| Discord digests | Both delivered (portfolio digest idempotent, earnings radar+book) |
| Earnings automation | `cipher-earnings-digest.timer` Mon–Fri 08:15 ET; `radar; paper-enter; notify-discord` |
| Alerts | `cipher-cluster-alert` exits 0 with honest `suspended_provider_session` when hosted core has no session |
| Local backup | Reruns clean; Aug-17 failure was transient WAL contention |
| Git | Clean tree, `master` = `56584ed`, pushed |

Known deliberate state: the paper autopilot's first **fill** is still pending —
the config comma-truncation bug was fixed and the executor restarted, but no
new qualifying setup has fired inside the 09:35–11:30 ET window since.
Position counts in `autopilot_shadow.sqlite` are 0 by design until then.

## 2. The validation pipeline (what we are watching)

These are the live experiments generating evidence. Nothing below is tuned to
"win"; the goal is honest prospective data.

1. **Paper autopilot (shadow)** — first fill gate: next qualifying card inside
   the entry window. Watch: does it enter, does the decision trace write,
   does it respect limits?
2. **Fronttest 6+1 strategies** — `v6_nvda_p05` (1 win), QQQ (off, losses
   recorded), MU (2 losses), NVDA C05 (new, idle by design), C1/P1 (rare by
   design). Watch: 20–30 trade cohort before any parameter change.
3. **Earnings model + paper book** — 12 positions settling 08-21. Watch:
   realized vs. model-implied probability; hold `paper-enter` manual if it
   ever feels attractive to auto-enter — it stays manual.
4. **GEX capture** — daily snapshots accumulating. Watch for cross-day
   consistency before trusting any level as "sticky".

Rule: no strategy parameter changes until ≥20 closed trades or 4 weeks of
prospective data, whichever is later. Exceptions require a written note in
this repo.

## 3. Next-build backlog (prioritized by user value)

### A. Research-quality improvements (highest value per effort)

1. **Stable public API hostname via Cloudflare tunnel** — all VM wiring is
   done; the only missing piece is a Zero Trust org + token stored as GCP
   secret `cipher-cloudflare-tunnel-token`, then
   `systemctl enable --now cipher-cloudflared`. Gate: `curl` to the tunnel
   hostname returns the core health payload.
2. **Provider capability surface** — add a read-only `/api/provider-capabilities`
   endpoint that reports the active data mode (OPRA/SIP full · IEX/indicative
   degraded · yfinance anonymous · flow-history only) so the UI can label the
   current session instead of guessing. Gate: UI shows a mode chip driven by
   the endpoint.
3. **Anonymous-mode parity audit** — verify every panel's degraded state:
   Strike Matrix works off yfinance chains; Spyglass flow shows
   "unavailable" not zero; GEX caveat visible in all modes. Gate: a
   check-sheet test per panel with no provider session.
4. **C05 signal subscription test** — the V6 study emits C05 on some days;
   the new portfolio should capture the next one. Gate: a `signals` row with
   `portfolio_id = v6_nvda_c05` after the next qualifying event.

### B. Ops hardening (prevents silent drift)

5. **Journal WAL backup** — make `backup_local_state.py` retry on SQLite
   lock (the Aug-17 failure was a one-shot race; a 3× retry with backoff
   removes the class). Gate: backup succeeds even while the fronttest writer
   is mid-commit.
6. **Alert health monitor** — a single daily pass that checks every timer's
   last-run time and exit code and posts only on anomalies. Gate: a degraded
   timer produces exactly one Discord message.
7. **Secrets inventory doc** — table of which secret names exist, where they
   live (Secret Manager / `/etc/cipher/cipher.env` / session-only), and who
   consumes them. No values. Gate: doc covers every `os.environ` read in
   active files.

### C. Product depth (after A/B are stable)

8. **Paper Portfolios UI: strategy explainer** — per-portfolio "what this
   strategy trades, why it is quiet, what would make it act" derived from
   spec metadata. Kills the "is it broken?" question permanently.
9. **Earnings radar UI panel** — show the upcoming earnings calendar,
   model probability, and paper-book overlap in the Research Desk. Gate: the
   radar output already produced by `scanner.find_upcoming_earnings` is
   visible in the UI.
10. **Prospective validation cohort tracking** — one dashboard query per
    strategy: signals → entries → outcomes with expected vs. realized
    win-rate. Gate: the fronttest `signal_outcomes` table feeds a weekly
    report.

## 4. Explicitly out of scope

- Broker/order integration of any kind (unchanged project boundary).
- Tuning strategies to current PnL (they are deliberately small cohorts).
- Any "make the demo look good" work — the submission phase is closed.
- Adding providers without a normalized read-only adapter + tests.

## 5. Cadence

- Daily: `cipher-autopilot` (evidence ranking) at 10:31 UTC; fronttest every
  minute; earnings digest 08:15 ET.
- Weekly: review fronttest `signal_outcomes` + earnings book settlements;
  note anything anomalous in `docs/audits/`.
- On change: full test suite + compile + node checks before commit.

## 6. Implementation status (executed 2026-08-18)

All code items from section 3 were implemented, verified, and pushed in the
post-plan sprint. Current `master` is ahead of this plan's snapshot; the
verification suite was **1027 passed, 1 skipped** after the sprint.

| Item | Status | Evidence |
|---|---|---|
| A1 Cloudflare tunnel | user action (token) | VM wiring verified; only the Zero Trust org + GCP secret `cipher-cloudflare-tunnel-token` is missing |
| A2 provider-capability surface | **done** | `/api/provider-capabilities` existed and is consumed by the Settings card; 2 new tests assert anonymous/degraded mode labeling |
| A3 anonymous-mode parity audit | **done** | `tests/test_yfinance_fallback.py` extended with provider-capabilities tests (8 tests total cover quote/bars/chain/matrix/options/flow degraded states) |
| A4 C05 signal capture | awaiting signal | `v6_nvda_c05` registered 2026-08-18; captures the next qualifying C05 event |
| B5 backup WAL hardening | **done** | `backup_local_state.py` gained a WAL trio hot-copy fallback; `cipher-local-backup` (failed since Aug 17) ran clean at 23:31 UTC on Aug 18 |
| B6 alert health monitor | **done** | `scripts/alert_health_monitor.py` + `cipher-health-monitor.service/.timer` (22:10 ET weekdays, 14:10 ET weekends); verified run reports healthy and flags failed units; 1 test |
| B7 secrets inventory | **done** | `docs/secrets-inventory-2026-08-18.md` — names, locations, consumers; no values |
| C8 strategy explainers | **done** | `PortfolioSpec.description` surfaced through `paper_portfolio_api` and rendered in the Paper Portfolios UI; rebuilt + published |
| C9 earnings radar UI | **done** | `earnings_model radar --json-output` writes `runtime/data/earnings_radar.json`; digest service updated; `/api/earnings-radar` endpoint + Earnings Radar panel (sidebar TODAY section) shipped; verified end-to-end (40 cards); 3 tests |
| C10 cohort tracking | **done** | `scripts/cohort_tracking_report.py` reads fronttest + shadow DBs, prints per-strategy signals/entries/outcomes; verified against live DB |

## 7. Immediate next actions

1. Watch the next autopilot window (09:35–11:30 ET) for the first fill.
2. Create the Cloudflare Zero Trust org + tunnel token (user action, ~10 min)
   to complete item A1.
3. Add `alert_health_monitor` output to the cohort cadence (optional): run the
   health monitor in the weekly ops review alongside `cohort_tracking_report.py`.
