# Journal Specification

## Overview
- **Target file:** `src/components/panels/Journal.tsx`
- **Screenshot:** `desktop-journal.png`
- **Interaction model:** click-driven month navigation, clickable day cells (no populated-day state observed)

## Layout (derived from screenshot, established tokens)
- Header row: avatar/logo circle (~40px, rounded) + "{Name}'s Trading Journal" heading (~24px bold, var(--text)) on the left; month navigator (‹ "August 2026" ›) + "Today" button + Month P&L stat badge (label "MONTH P&L" tiny/muted above, "+$0" large/bold/var(--accent) below, pill container var(--panel-2)/var(--line)) + "↑ Save image" button on the right.
- Calendar grid: 7 columns (Sun–Sat), header row of day-of-week labels (uppercase, 11px, var(--text-mute)), then day cells in a grid, `gap: ~8px`.
- Day cell: square-ish (aspect-ratio ~1), `var(--panel)` bg, `var(--line)` border, `border-radius: var(--radius)`, day number top-left (mono, var(--text-dim)). Today's cell gets a gold border highlight (`border-color: var(--gold)`, seen on "6" in screenshot). Cells outside the current month (none visible in capture, but standard pattern) would be dimmed/empty.
- Populated day cells (not captured — no trade data existed this session) should support: small P&L badge in the cell (green/red — but per this app's convention, use `var(--accent)` purple for profit / `var(--neg)` red for loss, NOT green), mark this as "approximate — no populated-day screenshot available."

## Data
Use `JournalDay[]` from `src/types/cipher.ts`. Mock the current month fully empty except maybe 2-3 cells with a small mock P&L badge to demonstrate the populated-state styling (clearly marked as illustrative since it wasn't observed live).

## Responsive
- Mobile: not separately captured for this panel — assume standard grid reflow (7 columns may compress to smaller cells or scroll; use judgment, mark approximate).
