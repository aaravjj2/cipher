# Phase V.6 — Operations recovery: autopilot filter, earnings automation, Discord digest

Date: 2026-08-18

## 1. Paper-autopilot executor never entered positions (fixed)

**Symptom.** `cipher-paper-autopilot-executor.service` ran every 5 minutes
through the entry window and produced confirmation batches, but every signal
card was rejected with `SKIPPED_SETUP_DISABLED` and `paper_positions` /
`paper_orders` stayed empty since the service was introduced.

**Root cause.** `config/paper_autopilot_shadow.yaml` declared the allowed
setups as YAML flow mappings:

```yaml
- {scanner_type: flash_agentic, setup: triple cluster (3 peaks, above), direction: bullish}
```

YAML flow mappings split values at commas, so `setup` loaded as
`"triple cluster (3 peaks"`. The cards carry the full
`"triple cluster (3 peaks, above)"`, so no pattern ever matched and the
filter rejected everything.

**Fix.** Quoted the comma-containing setup values inside the flow mappings
and documented why in the config. Verified against the six real cards from
today's run: all now evaluate `allowed=True`.

**Regression guard.** `tests/test_paper_executor_policy.py` loads the live
shadow config and asserts the comma setups are not truncated and are
accepted by `setup_allowed`.

## 2. Earnings automation did not exist (added)

`earnings_model` had no cron or systemd schedule; the Discord digests were
manual-only. Added:

- `infra/gcp-cipher-vm/systemd/cipher-earnings-digest.service` — runs
  `earnings_model radar` then `notify-discord --type all` (weekly preview +
  active paper book) under the research venv, chained with `;` so a radar
  failure never blocks delivery.
- `cipher-earnings-digest.timer` — `Mon..Fri 08:15 America/New_York`,
  `Persistent=true`.

`paper-enter` was deliberately **not** scheduled: it deletes open paper
positions and carries a hardcoded weekly schedule, so it stays manual.

First scheduled-style run delivered both Discord notifications
(`Weekly Preview: delivered`, `Paper Portfolio: delivered`).

## 3. Discord daily digest was dead since Aug 17 (restored)

**Root cause.** The digest unit loaded
`EnvironmentFile=/home/aarav/Aarav/agent-stack/.env` and posted through
`/home/aarav/Aarav/agent-stack/discord-notify.sh`; the whole directory was
deleted, so the service failed with `Failed to load environment files`.

**Fix.**
- `scripts/send_portfolio_daily_discord.py` now posts directly to the
  webhook via `urllib` — no external notifier.
- Unit loads `-/etc/cipher/cipher.env`; webhook value restored from
  `cipher-system/app/.env` (same working webhook the earnings bot uses).
- Added `ReadWritePaths` for the prospective-fronttests directory so WAL
  reads never race the concurrent writer (first retry hit a transient
  lock).
- Verified end-to-end: digest `delivered` for report day 2026-08-18.

## 4. QQQ systems turned off; NVDA zero-signal portfolios verified (2026-08-18)

**QQQ.** `qqq_early` and `qqq_validated` are disabled via a new `enabled`
flag on `PortfolioSpec` (both set `False`). Disabled portfolios:

- receive no signals in `detect_signals` (routing iterates `ACTIVE_SPECS`),
- are excluded from the daily Discord digest and its combined equity,
- remain visible in status/API with `enabled: false` so the UI shows the
  turned-off state instead of pretending they never existed.

Flip `enabled=True` on the two QQQ specs to restart them.

**NVDA (v6_nvda_c1 / v6_nvda_p1).** The two continuation portfolios have
never recorded a signal. Investigation shows the capture path is correct:

- The V6 study emits C1/P1 rarely by design (continuation setups requiring a
  half-level cross, a stopped-out first leg, and a fresh 1% re-cross within
  5 bars). Over 12 days of NVDA 5-minute bars the study emitted P1 twice
  (Aug 10, Aug 13) and C1 zero times.
- The fronttest only began running on 2026-08-14, so both P1 emissions
  predate it; on the days it has run (Aug 14/17/18) the study emitted only
  P05 (recorded) and C05 (emitted but subscribed by no portfolio).
- A new regression test crafts an oscillating session that produces a real
  P1 signal on the latest closed bar and asserts `detect_signals` routes it
  to `v6_nvda_p1` (and never backfills the earlier P05 bar).

No backfill was performed — the fronttest is strictly prospective.

## 5. Improvement-sprint execution (2026-08-18 evening)

**P0-4 — `paper-enter` made dynamic and idempotent, now scheduled.**
- `earnings_model/paper_portfolio.py`: schedule comes from the live radar
  (`upcoming_week_schedule` -> `scanner.find_upcoming_earnings`), entry date
  is `date.today()` (no more hardcoded `2026-08-17` / `2026-08-21`), and
  `enter_this_week_paper_book` is idempotent per (symbol, report-date) —
  re-runs skip existing entries and never delete open positions.
- `cipher-earnings-digest.service` now chains `radar; paper-enter;
  notify-discord` so the daily 08:15 ET run books new paper positions.
- Regression tests: `tests/test_earnings_paper_portfolio.py` (4 tests,
  joblib-guarded) cover Friday roll, schedule dedupe/sort, idempotency,
  and live-date + Iron Condor fallback.

**P2-1 — `v6_nvda_c05` registered.** The V6 study emits C05 that no
portfolio subscribed to; added `PortfolioSpec("v6_nvda_c05", ...)` to
`fronttest_portfolios.py`. Verified live in the pass output (registered,
`enabled: true`).

**P1-1 — Paper Portfolios UI disabled badge.** API already exposed
`enabled`; the frontend now renders a `DISABLED` badge and a
"turned off, not receiving signals" note, and the loading copy no longer
says "six". Published via `sync_web_build.sh`.

**P0-3 — cluster-alert exit code.** `hermes_scan_alerts.py` now
authenticates to the hosted core with `CIPHER_INTERNAL_PROXY_TOKEN` and
treats the provider-session suspension (422) as an honest exit-0 state
instead of an error alert; verified exit 0 via systemd.

**P0-1 — autopilot first-fill readiness.** Executor healthy with the fixed
config: `mode: shadow`, 4 workers running, 0 restarts, no exceptions,
`reconciliation_passed: true`. Scheduler pass fires 08:45 ET; the next
qualifying card in the 09:35–11:30 ET window is the live first-fill gate.

**P0-2 / P0-5 — user dashboard actions (documented, not automatable from
this VM).** Cloudflare tunnel VM wiring is complete and verified
(`sync-secrets.py` -> `/etc/cipher/cloudflare-tunnel.token` via systemd
`LoadCredential`); only the GCP secret `cipher-cloudflare-tunnel-token`
(from the Zero Trust dashboard) is missing. Supabase Site URL needs a
one-click dashboard edit (project `ipcsgrijatnnsbpguojl` -> Site URL
`https://web-finance-dashboard.vercel.app`); no management/service-role
token exists server-side. Both updated in
`docs/next-improvements-2026-08-18.md` with exact steps.

## Verification

- `pytest`: 1016 passed, 2 skipped (includes QQQ-disable + P1-capture tests)
- Executor restarted with fixed config; cycle pipeline now admits cards
- Earnings timer active; one-shot run delivered both Discord messages
- Discord digest service delivered successfully
- Fronttest pass and paper-portfolio API report `enabled` state live
- Daily digest idempotent after QQQ removal (no duplicate posts)
