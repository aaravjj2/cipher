# Spyglass (+ Bio, Contract Search) Specification

## Overview
- **Target file:** `src/components/panels/Spyglass.tsx` (single file covering all 3 sub-views, switched via internal tab state)
- **Screenshots:** `desktop-spyglass.png`, `desktop-bio.png`, `desktop-contract-search.png`, plus mobile `mobile-spyglass.png`
- **Interaction model:** header-level sub-tabs (Bio / Contract Search coexist with base Spyglass — see `BEHAVIORS.md`, these don't change the active sidebar item), async "Scanning..." loading state

## Layout — Spyglass / Bio (shared table shape)
- Filter toolbar row: TRADE DATE date-input + premium-size pill group (≤$0.50/≤$0.25/≤$0.10/All px for Spyglass; ≥5% OTM/≥10%/≥25%/≥50% for Bio) + size-bucket pill group ($5k/$10k/$25k/$50k/$100k) + Calls/Puts/All pill group + Bid-Ask/Ask/Bid pill group (Spyglass only) + Moneyness/OTM/ITM pill group (Spyglass only) + Rescan button (Bio only)
- All pill groups: same convention as Strike Matrix toolbar (bordered pills, active = filled)
- Table: TICKER / TIME ET / SIZE (PREM) / CONTRACTS / PX / STRIKE / EXPIRATION / C/P / BID/ASK / %OTM columns, header row uppercase muted, `var(--font-mono)` data rows
- Loading state: centered text "Scanning {TICKER}..." (Spyglass) or "Scanning pharma, biotech & medtech... this can take a moment." (Bio) — var(--text-dim), no spinner icon observed, just text
- Use `SpyglassRow[]` from `src/types/cipher.ts` for data shape (empty array + loading=true as the default demo state, matching what was actually observed both times)

## Layout — Contract Search (static form, no scanning)
- Centered-ish form row: TICKER text input, STRIKE number input, Call/Put toggle (2-button pill), TRADE DATE date input, "Search" button (outlined, purple border/text)
- Empty-state helper text below: "Type a ticker, a strike, and pick call or put to see today's traded volume and how much was bought vs sold." (var(--text-mute), centered)
- No results table populated in any capture — build the empty state only, results table shape can mirror Spyglass's table if implemented, otherwise leave as a TODO comment

## Header sub-tabs
Spyglass's Header `rightSlot` (see `header.spec.md`) should include "Bio" and "Contract Search" as two additional pill/link buttons alongside whatever base actions exist — clicking switches the internal view state within this component, NOT the app's main panel routing. Implement via local `useState<'spyglass'|'bio'|'contractSearch'>`.

## Responsive
Filter toolbar wraps (flex-wrap); table scrolls horizontally in its own container on mobile, matching the pattern used elsewhere.
