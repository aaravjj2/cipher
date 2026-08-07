# My Watchlists Specification

## Overview
- **Target file:** `src/components/panels/Watchlists.tsx`
- **Screenshots:** `desktop-watchlists.png`, `mobile-watchlists.png`
- **Interaction model:** static table, click-driven add/remove (instant, no loading state observed)

## Layout (derived from screenshot — not exact getComputedStyle, use established tokens)
- Page heading "My Watchlists" (~28px, fontWeight 700, var(--text)) + subtitle "Your tickers, with today's move and the gold compact level at a glance." (~14px, var(--text-mute)), stacked, ~24px bottom margin before content.
- Add-ticker row: text input (flex-grow, placeholder "Add a ticker (e.g. NVDA)", var(--panel-2) bg, var(--line) border, 8px radius) + square "+" button (var(--accent) purple solid bg, white text/icon, matching radius).
- Table: header row (TICKER / % CHANGE / $ CHANGE / PRICE / COMPACT 100 — uppercase, 11px, var(--text-mute), letterSpacing wide) + data rows separated by `border-bottom: 1px solid var(--line-soft)`.
- Row cells: ticker in `var(--font-mono)` bold white; %/$ change colored purple (`var(--accent)`) positive / red (`var(--neg)`) negative, using the same convention as Header's quote-change; price plain mono; "compact 100" score shown as a small pill badge with gold-tinted border (`var(--gold)` at low opacity bg, gold text) — matches the "COMPACT 100" screenshot values (e.g. "250", "775").
- Empty/no-data rows (tickers with no price yet, seen as "…" in screenshot) — render as a muted ellipsis in place of the numeric columns.
- Remove (×) button per row, right-aligned, small icon button, `var(--text-mute)` default → `var(--neg)` on hover (standard destructive-hover convention).

## Data
Use `WatchlistItem[]` from `src/types/cipher.ts`. Seed with ~9 mock rows matching the screenshot's flavor (mix of populated and "…" empty rows).

## Responsive
- Mobile: table becomes horizontally scrollable within its own container (same pattern as Strike Matrix), confirmed in `mobile-watchlists.png` — columns clip at viewport edge.
