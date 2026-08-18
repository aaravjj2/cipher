# Devpost project story — paste-ready "About the project"

Copy everything below the horizontal rule into the **About the project** field.
Markdown is supported. The `$$ ... $$` block renders as LaTeX math on Devpost.

---

## Inspiration

Individual options traders assemble every decision from disconnected tools: a
screener, a chart, an option chain, news, an exposure model, a spreadsheet,
and an AI chat. Those tools disagree about timestamps and data coverage.
Missing options data can look like zero. A backtest can silently hide the
candidates it rejected. An AI answer can sound certain without showing what
evidence was actually available at the moment of the call.

The dangerous failure is not a missing feature — it is **false confidence**.
We wanted to build the research terminal we wished existed: one that treats
data quality, provenance, and uncertainty as first-class citizens instead of
footnotes.

## What it does

Cipher is a **read-only AI research copilot** for stock and options traders.
It runs the whole research loop as one calm, traceable workflow:

- **Morning Brief** — surfaces session state and freshness before anything
  else, flagging stale or unavailable inputs instead of hiding them.
- **Setup Scanner** — ranks candidates only when the evidence is fresh and
  sufficient, and preserves every rejection reason in the audit trail.
- **Night Vision** — combines price, session levels, and public-OI exposure,
  and lets you replay the exact evidence snapshot behind a scan, verified by
  a snapshot identity checksum.
- **Options Terminal & Strike Matrix** — evaluate expirations, strikes,
  bid/ask, OI, volume, Greeks, and IV using only fields the feed actually
  provided. Unavailable data stays visibly unknown — never converted to zero.
- **Research Desk** — a bounded agent writes evidence-backed memos on large,
  liquid, volatile names, citing provider, feed, event time, and coverage.
- **Paper Portfolios & Autopilot** — six isolated simulation portfolios with
  realized, midpoint-marked, and conservative liquidation equity. The
  autopilot discovers before the open, waits for a closed-bar confirmation,
  applies hard loss/position/time limits, and writes a replayable decision
  trace. It has **no order authority** — there is no broker client anywhere
  in the product.

## How we built it

- **Backend:** a Python standard-library HTTP API with explicit response
  budgets, bounded background refreshes, and SQLite/WAL ledgers for events,
  experiments, GEX history, journals, and paper portfolios.
- **Data:** Alpaca OPRA options and SIP/IEX stock data as the primary feed.
  When no Alpaca session exists, an anonymous **yfinance fallback** still
  serves delayed quotes, bars, chains, and a degraded strike matrix — clearly
  labeled, never passed off as live OPRA.
- **Frontend:** Next.js/React/TypeScript terminal served as a static export,
  with a Node.js same-origin authenticated proxy.
- **Hosting:** Vercel (public frontend) + Supabase Auth with row-level
  security for per-user isolation + a backend VM. Login uses a secure
  `HttpOnly` cookie; the Supabase token lives only in backend memory. A
  **guest mode** lets anyone explore delayed market data without an account
  or any keys.
- **Security by construction:** Alpaca keys are session-only in memory, never
  persisted to Supabase, Vercel, browser storage, logs, or cookies. The
  autonomous components trade only the simulator.

The GEX model follows a fixed public convention — call gamma × OI × 100 ×
spot² × 0.01 — and is always labeled as a **public-open-interest heuristic,
not verified dealer positioning**:

$$ \text{call\_gex} = \gamma_c \cdot OI_c \cdot 100 \cdot S^2 \cdot 0.01 $$

## Challenges we faced

- **Response budgets.** Live flow refreshes could take seconds; we bounded
  them to ~2.5 s with deduplication and explicit `unavailable` states instead
  of timeouts disguised as data.
- **Replay integrity.** Night Vision replays had to show exactly what the
  scanner saw. We gave every snapshot a SHA-256 identity and recomputed all
  104 legacy evidence artifacts so a tampered matrix is rejected, not
  silently replayed.
- **No-fake-data discipline.** When yfinance or degraded feeds can't provide
  OPRA trades, Greeks, or flow, the UI says so explicitly — it never
  manufactures zeros.
- **Hosted deployment without key exposure.** Standing up multitenant auth
  (Supabase RLS + cookie sessions + guest mode) while keeping Alpaca and
  service secrets out of the browser and the frontend host.
- **One clean repository.** We consolidated weeks of research, an earnings
  model, EOD strategy code, and diagnostics from scattered local checkouts
  into a single public repository with bulk data gitignored.

## What we learned

Honesty is a feature. Labeling a source as delayed, a Greek as unavailable,
or an experiment as not-yet-rankable builds more trust than a confident
number ever could. We also learned that read-only is a product decision, not
a limitation: every "missing" datapoint became a UI state to design for, and
every refusal to auto-execute became a story about giving the human the final
decision.

## What's next

- A stable public HTTPS API hostname so the live demo works for everyone.
- Additional read-only provider adapters (normalized, degraded-mode aware).
- More prospective-validation cohorts so strategies earn rankability
  the slow, honest way.

---

## Built with (tags)

Python · Node.js · Next.js · React · TypeScript · Alpaca · yfinance ·
Supabase · Vercel · SQLite · Google Cloud · Tailwind CSS · FinBERT ·
Hugging Face · pytest · Playwright · systemd · Tailscale · WebSockets ·
Options Trading · Market Data

## Try it out links

- **Live demo (guest mode):** https://web-finance-dashboard.vercel.app
- **GitHub:** https://github.com/aaravjj2/cipher

## Media guidance

- **Gallery (up to 15 images, JPG/PNG/GIF, 5 MB max):** use the 7 hero
  screenshots from `cipher-system/release-artifacts/` — each is 1440×1000
  (~0.1–0.2 MB). Devpost recommends 3:2; 1440×960 is an exact 3:2 crop if
  you want it pixel-perfect. The `detailed/` folder has 34 more feature
  captures (all ≤0.2 MB) for anything you want to show in depth.
- **Video demo link:** Devpost embeds YouTube, Vimeo, Facebook, or Youku —
  it does not accept direct WebM uploads. Upload
  `cipher-release-walkthrough.webm` (2:27, 9 MB) to YouTube as **unlisted**,
  then paste that URL. The timed narration for it is in
  `docs/quantumhacks/demo-transcript.md`.
