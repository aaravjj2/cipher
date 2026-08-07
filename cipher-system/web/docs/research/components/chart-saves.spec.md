# Chart Saves Specification

## Overview
- **Target file:** `src/components/panels/ChartSaves.tsx`
- **Screenshot:** `desktop-chart-saves.png`
- **Interaction model:** static grid gallery, per-card delete (×)

## Layout (derived from screenshot, established tokens)
- Page heading "Chart Saves" (~28px bold) + subtitle "Snapshots you've saved from Night Vision, with the top pull at capture time." (var(--text-mute))
- Masonry/grid gallery: `grid-template-columns: repeat(auto-fill, minmax(320px, 1fr))`, gap ~24px
- Card: `var(--panel)` bg, `var(--line)` border, `border-radius: var(--radius)`, overflow hidden
  - Top: chart snapshot image (candlestick screenshot thumbnail — for the clone, render a small mock SVG/canvas candlestick pattern reusing Night Vision's chart-drawing approach at reduced size, not a real static image), aspect-ratio ~16:10, with a small × delete button top-right overlay (semi-transparent dark circle bg)
  - Bottom metadata block, padding ~16px: rows of `label (var(--text-mute), 11px uppercase) : value (var(--font-mono), right-aligned, var(--text))` for DATE ADDED / TICKER (colored var(--accent)) / PRICE / VIEW
  - "TOP LEVELS" sub-table: 4 rows of `level : score` pairs, level colored by rank (gold for #1, then purple/red mix descending — see screenshot: 735=gold/100, 740=purple/93, 733=red/65, 728=red/64 — same purple/red positive-negative convention applied to rank coloring)

## Data
Use `ChartSaveCard[]` from `src/types/cipher.ts`. Mock ~6 cards matching the screenshot's data shape.

## Responsive
- Not separately captured; standard grid reflow to 1 column on mobile via `sm:`/`md:` breakpoints, reasonable default.
