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

## Verification

- `pytest`: 1012 passed, 1 skipped (regression test included)
- Executor restarted with fixed config; cycle pipeline now admits cards
- Earnings timer active; one-shot run delivered both Discord messages
- Discord digest service delivered successfully
