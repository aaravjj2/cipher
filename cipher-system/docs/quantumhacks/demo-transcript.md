# Demo walkthrough — timed transcript

Companion to `cipher-system/release-artifacts/cipher-release-walkthrough.webm`
(2:27, 1440×1000, silent screen recording). This transcript doubles as the
voiceover script: narrate each segment while the recording is playing, or paste
the segments as captions. Timestamps are approximate and match the capture
workflow order.

All footage is from a signed-in session on a local server. No password,
Supabase token, Alpaca key, or other secret appears in the recording.

## 00:00–00:05 — signed-in session loads

The Cipher terminal opens already authenticated. The sidebar shows the full
research workspace: TODAY (Morning Brief, Research Desk), DISCOVER (Setup
Scanner, Watchlists, News), ANALYZE (Ticker Workbench, Night Vision, Options
Terminal, Strike Matrix, Spyglass), REVIEW (Paper Portfolios), SYSTEM
(Settings).

> “Cipher is a read-only options-research copilot. Everything you are about to
> see is evidence-backed, replayable, and paper-only — there is no broker order
> authority anywhere in the product.”

## 00:05–00:22 — Morning Brief

The brief opens with session state and freshness for the broad market, then
highlights the names that passed their data-quality gate today. Stale or
missing inputs are flagged explicitly rather than hidden.

> “The Morning Brief starts with honesty: it tells you what data is fresh,
> what is stale, and what is simply unavailable. Missing data is never shown
> as zero.”

## 00:22–00:41 — Research Desk

The research desk shows bounded, evidence-backed memos on large, liquid,
volatile names. Each memo cites the evidence envelope — provider, feed, event
time, coverage — and flags contradictions or missing inputs instead of
bluffing through them.

> “An agent analyzes names on a schedule and writes short memos with cited
> evidence. The reasoning is visible and bounded by read-only tools, so the
> human can challenge any conclusion.”

## 00:41–01:03 — Setup Scanner

The scanner runs the full funnel: universe → data-qualified →
liquidity-qualified → setup-qualified → ranked. Rejected candidates stay in
the audit trail with their rejection reason.

> “Cipher does not hide the denominator. Every candidate that failed a
> freshness or liquidity check is still visible, with its evidence ID, so you
> know exactly what the screen was looking at.”

## 01:03–01:18 — Night Vision

Night Vision opens on a candidate with the price-first chart, session levels,
and exposure bands. The header shows the frozen replay state.

> “This is the same evidence snapshot the scanner used — not a chart
> recomputed after I clicked. The replay is verified by a snapshot identity
> checksum, so what you see is what was ranked.”

## 01:18–01:25 — Evidence details

The evidence drawer lists the exact inputs behind the view: capture time,
event time, coverage, and the GEX caveat.

> “GEX here is explicitly a public-open-interest heuristic — never claimed as
> verified dealer positioning.”

## 01:25–01:40 — Options Terminal

The terminal shows expirations, strikes, bid/ask spreads, OI and OI date,
volume, Greeks, and IV for the candidate — and only where the feed actually
provided them. Unavailable fields stay visibly unknown.

> “The terminal never manufactures a bid, a Greek, or an open-interest date.
> You see exactly the liquidity that exists — nothing invented.”

## 01:40–01:52 — Paper Portfolios

Six isolated paper portfolios record fills, outcomes, and skipped signals.
Equity is reported three ways: realized, midpoint-marked, and conservative
liquidation value.

> “The paper autopilot discovers before the open, waits for a closed-bar
> confirmation, applies hard loss and time limits, and trades only this
> simulator. It physically cannot reach a broker.”

## 01:52–02:02 — Settings / security

Settings shows the provider surface and session behavior: Alpaca credentials
are session-only and never stored, login uses a secure HttpOnly cookie, and
anonymous guests get a delayed Yahoo-data research mode without saved state.

> “Your Alpaca key is never saved — it lives in memory for the session and
> disappears on disconnect. Even without any key, guests can still explore
> delayed quotes, bars, chains, and the strike matrix.”

## 02:02–02:27 — close

The terminal returns to the workspace. Final message:

> “Cipher is not an AI stock picker. It is an auditable decision system for
> the person who still owns the decision — every scan replayable, every
> uncertainty visible, every position paper-only.”

---

## Elevator pitch (Devpost tagline, ≤200 characters)

Recommended:

> **An auditable AI research copilot for options traders — evidence-backed
> scans, replayable analysis, and a paper-only autopilot, with no broker
> order authority.**

Alternatives:

1. **A read-only AI copilot that makes options research auditable — every
   scan replayable, every missing datapoint visible, every position
   paper-only.**
2. **Stop trusting screenshots: an AI research copilot where every options
   signal carries its evidence, and every trade is a paper trade.**
