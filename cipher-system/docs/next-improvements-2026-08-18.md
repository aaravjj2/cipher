# Next improvements — implementation plan

Date: 2026-08-18 · Priority system: P0 safety/correctness → P1 daily value →
P2 validation → P3 scale/polish → P4 exploratory (from the roadmap).

This plan is grounded in the current state after the 2026-08-18 ops session:
autopilot setup-filter fixed, earnings automation added, Discord digest
restored, QQQ fronttests disabled, NVDA continuation capture verified.
Each item names the code to touch and the acceptance evidence required —
no item is "done" without its gate.

---

## P0 — correctness and operations (do first)

### P0-1. Verify the autopilot's first real paper fill (watch item)

**Context.** The YAML comma-truncation fix is deployed and the executor
restarted, but no qualifying signal has flowed through since (market was
closed). The pipeline is proven only to the confirmation stage.

**How.**
- Tomorrow (or next market day), watch the 09:35–11:30 ET window:
  `tail -f data/paper_runtime/autopilot/cycles/<date>.jsonl` and
  `journalctl -u cipher-paper-autopilot-executor.service -f`.
- Expect: `paper_confirmations_submitted` with `confirmed >= 1`, then a row in
  `paper_positions` / `paper_orders`, then mark/exit events.

**Gate.** One real paper position enters `autopilot_shadow.sqlite` from a live
scan; if a candidate is rejected, the reason is a documented skip code, not
`SKIPPED_SETUP_DISABLED`.

### P0-2. Stable public API hostname via the existing Cloudflare tunnel

**Context.** Vercel serves the public frontend, but the API origin is the
Tailscale Funnel pilot (`cipher-main.tail39504f.ts.net`). The Cloudflare
infra already exists and is documented (`infra/gcp-cipher-vm/CLOUDFLARE.md`,
`cipher-cloudflared.service` unit, `verify-cloudflare-access.py`) but the
service is **inactive** — no tunnel token has been provisioned.

**How.**
1. Create the remote-managed `cipher-main` tunnel in Cloudflare Zero Trust;
   route `api.cipher.<domain>` → `http://127.0.0.1:8283`.
2. Store the tunnel token as the GCP secret `cipher-cloudflare-tunnel-token`
   (never in shell history or the repo).
3. `sudo systemctl enable --now cipher-cloudflared.service`; confirm
   `verify-cloudflare-access.py --hostname api.cipher.<domain>` blocks
   unauthenticated `/api/quote`.
4. Point Vercel at the new origin: `vercel env rm NEXT_PUBLIC_CIPHER_API_ORIGIN`
   then re-add with the Cloudflare hostname, redeploy.
5. Keep the Tailscale `:8443` route until Access-protected verification passes,
   per CLOUDFLARE.md; the password gate stays as defense in depth.

**Status 2026-08-18.** VM side is fully wired and verified: `sync-secrets.py`
materializes `/etc/cipher/cloudflare-tunnel.token` (mode 0600) from GCP secret
`cipher-cloudflare-tunnel-token`, and `cipher-cloudflared.service` reads it via
systemd `LoadCredential` — the token never touches a process command line,
shared env file, or journal. The service is inactive solely because the GCP
secret does not exist yet. This is a user dashboard action: create the
Zero Trust tunnel + Access policy and store the token as
`cipher-cloudflare-tunnel-token` (the VM's own service account lacks
Secret Manager read, so a human/owner account must create it). After that,
`sudo systemctl enable --now cipher-cloudflared.service` and run the verifier.

**Gate.** Anonymous guest `GET /api/quote` returns `401`/guest-yahoo data
through the public hostname, CORS preflight passes from the Vercel origin, and
no credential is exposed in the tunnel config.

### P0-3. Fix `cipher-cluster-alert` Telegram exit-1

**Context.** The pass prints `Sent to telegram home channel` then exits 1
every run (`scripts/hermes_scan_alerts.py` — `run["ok"]` stays false when the
hermes CLI returns non-zero after a successful send).

**How.** Reproduce with `--dry-run`/direct invocation, then either treat a
confirmed send as success (return 0 when the message was delivered) or surface
the actual hermes error instead of a generic failure. Add a test asserting the
pass exits 0 after a successful send.

**Gate.** `systemctl start cipher-cluster-alert` succeeds; journal shows the
delivery line and `Deactivated successfully`.

### P0-4. Make `paper-enter` schedulable (dynamic earnings calendar)

**Context.** `earnings_model/paper_portfolio.py::enter_this_week_paper_book`
has a **hardcoded week list** (`2026-08-18…20`) and deletes open positions on
every run — unsafe to automate. The new earnings timer only sends digests.

**How.**
1. Replace the hardcoded schedule with the radar-derived calendar
   (`earnings_model` `radar` output / `earnings.sqlite` upcoming events).
2. Make entry idempotent: skip symbols already entered this week instead of
   deleting open positions (or scope the delete to expired-week rows only).
3. Only then add `paper-enter` to `cipher-earnings-digest.service` (or a
   weekly `Mon` timer), with the webhook/digest after it.

**Gate.** Running the command twice in one week produces the same book; next
week's run picks up the new calendar without code edits.

### P0-5. Complete Supabase Auth URL configuration

**Context.** Manual step from the hosted rollout: `Site URL` should be
`https://web-finance-dashboard.vercel.app` so email-confirm redirects work.

**How.** Set it in Supabase → Authentication → URL Configuration (or via the
management API with the service-role key, server-side only). Verify a
sign-in/confirm round-trip in a fresh browser.

**Status 2026-08-18.** Cannot be automated from this VM: no Supabase
management token or service-role key is present (only `SUPABASE_URL` +
`SUPABASE_ANON_KEY`). Project ref is `ipcsgrijatnnsbpguojl` (from
`SUPABASE_URL`). Remains a one-click dashboard action: Supabase →
Authentication → URL Configuration → Site URL =
`https://web-finance-dashboard.vercel.app`, then Save.

**Gate.** Email confirmation and password reset resolve back to the Vercel app.

---

## P1 — daily trader value

### P1-1. Paper Portfolios UI: show turned-off portfolios

**Context.** The API now returns `enabled: false` (spec config + status) but
the UI still renders qqq_early/qqq_validated like live portfolios.

**How.** In `web/src/components/panels/PaperPortfolios.tsx`, render a muted
"disabled / turned off" badge for `enabled === false`, keep their frozen
equity/history visible, and exclude them from any "active positions" totals.
Mirror the state in the daily digest copy already (excluded).

**Gate.** Browser test asserts the two QQQ cards show the disabled badge and
contribute nothing to combined-equity totals.

### P1-2. Morning Brief / first-run polish (roadmap Phase 1)

**Context.** Roadmap Phase 1 (task-based navigation, one ticker workspace,
prioritized feed) is the largest UX uplift. Start with the bounded slice:
Morning Brief ordering + data-health language, then the five-minute workflow
test (Today → Discover → Analyze → Options → Plan/Review).

**How.** Follow the roadmap Phase 1 deliverables; gate on the five-minute
workflow browser journey plus the 390 px/keyboard pass.

---

## P2 — validation quality

### P2-1. Register a C05 fronttest portfolio (or explicitly exclude it)

**Context.** The V6 study emits `C05` signals regularly (3 in 12 days) but no
portfolio subscribes — those observations are currently invisible.

**How.** Preregister a `v6_nvda_c05` spec (or document the deliberate
exclusion in the study). If added, it must follow the same immutable-rules
discipline: no rule edits within a cohort, and it inherits the P05/C1/P1
contract/policy config.

**Gate.** The new portfolio appears in Paper Portfolios with a frozen config
hash and records its first prospective signal (or a documented zero-signal
session) before any discussion of its performance.

### P2-2. One evidence contract and evaluator parity (roadmap Workstream A)

**Context.** Scanner, Night Vision, the historical evaluator, and the paper
fronttests still carry separate payloads. Roadmap Workstream A defines the
canonical `EvidenceSnapshot`/`SignalRecord` with adapters around existing
payloads — no engine rewrite in one pass.

**How.** Follow the roadmap's five-step order: schema + fixtures → adapters →
scanner/chart share the snapshot → evaluator/executor share the signal record
→ parity report for historical/replay/prospective.

**Gate.** A frozen fixture produces byte-equivalent signal decisions across
all three modes (or differences are explicit fill-model deltas).

---

## P3 / P4 — scale, polish, exploratory

- **Coverage catalog + storage compaction** (roadmap Phase 4): publish per-
  session/ticker/contract coverage; compact the large captured stores behind
  checksummed manifests and a tested restore before touching the 70 GB
  Tradier/chain archives.
- **Research agent golden tasks** (roadmap Phase 5): evaluate the fleet on
  golden questions, stale/provider-conflict cases, and prompt-injection
  attempts before claiming the agent is product-grade.
- **Demo packaging** (roadmap Phase 6): frozen-fixture demo profile with a
  visible "demo data" badge, clean-machine install/rollback rehearsal, and a
  final Devpost submission with the regenerated media and transcript.

---

## Suggested execution order

1. **P0-1** is a watch item — it needs no code, just tomorrow's session.
2. **P0-3** and **P0-4** are small, self-contained code fixes; do these next.
3. **P0-2** (Cloudflare hostname) unblocks the "real public deployment" story
   and is mostly provisioning + config, not code.
4. **P1-1** is a small frontend change that makes the QQQ shutdown visible.
5. **P2-1** decides the C05 question before any more NVDA study discussion.

Not in scope, per the roadmap exclusions: live trading, broker adapters,
fabricating unobserved quotes, treating GEX as verified dealer inventory, or
editing pre-registered cohort rules.
