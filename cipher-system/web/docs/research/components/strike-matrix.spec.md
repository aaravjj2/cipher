# Strike Matrix Specification

## Overview
- **Target file:** `src/components/panels/StrikeMatrix.tsx`
- **Screenshots:** `docs/design-references/accessobsidian.com/desktop-strike-matrix.png`, `mobile-strike-matrix.png`, `desktop-strike-matrix-sniper.png`, `desktop-strike-matrix-vex.png`
- **Interaction model:** click-driven toolbar (density/range/metric/mode toggles), CSS-grid data table with sticky header row + sticky first column, horizontal+vertical scroll

## DOM Structure
```
[toolbar] — Full/Compact | ±3%/±6%/±12%/All | GEX/VEX | Matrix/Sniper | refresh icon (not fully re-extracted — reuse Header's rightSlot pattern, styled as bordered pill buttons matching Header/Sidebar conventions)
div.grid (CSS Grid, gridTemplateColumns: "92px" + N × ~319px expiration columns)
├── div.h-cell.h-strike ("STRIKE" — sticky top-left corner)
├── div.h-cell (× N expiration headers, e.g. "Aug 7 / 1d" two-line — sticky top)
├── (repeating per strike row:)
│   ├── div.k-cell (strike price label, sticky left) — div.k-cell.atm variant when this row is at-the-money (brighter white text vs dimmed default)
│   └── div.cell (× N, one per expiration) — div.cell.score is the standard data cell; div.cell.score.star is the single highlighted "top pull" cell (solid gold bg)
└── div.spot-row (thin 14px-tall marker row inserted at the live spot price position — NOT a fixed table position, moves based on current price)
```

## Computed Styles (exact values from getComputedStyle)

### `.grid` (table root)
- display: grid
- gridTemplateColumns: `92px 479.135px 319.427px 319.438px` (1 fixed strike column + N flexible expiration columns — in this capture there were 3 expiration columns visible in viewport of a wider set; **implement as `92px repeat(N, minmax(200px, 1fr))`** so it works for any column count, not hardcoded to 3)
- fontFamily: var(--font-mono), fontSize: 13px base, color: var(--text)

### `.h-cell` (expiration column header)
- fontSize: 11px, fontWeight: 700, letterSpacing: 0.66px (0.06em)
- color: var(--text-dim)
- background: var(--bg) (solid, NOT panel — headers sit on the page background color to read as "floating above" the grid, confirmed rgb(7,9,15) = --bg exactly)
- padding: 12px 8px 8px
- height: 34.667px (~35px)
- position: sticky, top: 0 (stays fixed while scrolling vertically)
- textAlign: center
- Two-line content: date on top ("Aug 7"), days-to-expiry below in dimmer/smaller sub-text ("1d") — not separately measured, approximate as a flex-column with the sub-line at ~85% opacity

### `.k-cell` (strike price label, left column)
- fontSize: 11px, fontWeight: 600
- color: var(--text-dim); background: var(--bg)
- padding: 0 10px 0 4px
- height: 26px, display: flex, justifyContent: flex-end, alignItems: center (right-aligned numbers)
- position: sticky, left: 0 (stays fixed while scrolling horizontally)

### `.k-cell.atm` (at-the-money strike row label)
- Same as `.k-cell` except color: `#ffffff` (full white, not dimmed) — this is the row closest to current spot price, needs a `isAtm: boolean` prop-driven variant

### `.cell` / `.cell.score` (standard data cell)
- fontSize: 11px, fontWeight: 700, letterSpacing: 0.22px
- color: `#ffffff` (full white text regardless of background intensity)
- background: `rgba(40,40,70,0.12)` in this sample — **this is a data-driven heatmap color, not a fixed value.** The actual hue/alpha varies per cell based on the dollar value's sign and magnitude (see screenshots: strongly negative = saturated red, strongly positive = saturated purple/magenta, near-zero = barely-tinted dark). Builder must implement a color-scale function: `getCellColor(value: number, maxAbs: number): string` that interpolates opacity (and hue: purple for positive, red for negative) based on `Math.abs(value) / maxAbs`. Reference the screenshots closely for the gradient feel — this is the single most visually important detail of this component, worth extra care even though the exact formula isn't measured.
- padding: 0 8px, height: 26px, borderRadius: 3px
- display: flex, justifyContent: center, alignItems: center

### `.cell.score.star` (highlighted "top pull" cell — exactly one per matrix, the single strongest value)
- background: `var(--gold)` (solid, `rgb(247,189,41)` = exact match)
- color: `rgb(21,16,0)` (near-black, for contrast against gold) — add as a new token if needed, or use `color-mix(in srgb, var(--gold) 15%, black)` as an approximation... actually just use a near-black literal since it's a specific contrast-text value, not derived from another token
- Otherwise same dimensions/padding/radius as `.cell`

### `.spot-row`
- height: 14px, position: relative
- Renders as a dashed horizontal line + a floating label pill (`SPOT 311.56`) — visible in screenshots overlapping the grid at the row matching current price. Implement as an absolutely-positioned overlay computed from the spot price's row index, not a real grid row (matches the visual: it sits ON TOP of / between rows, not consuming its own full row height in the data flow — 14px is much shorter than the 26px data rows).

## States & Behaviors
- **Full/Compact toggle**: changes number of visible strike rows (Compact = fewer rows, tighter range around spot). Implement as a prop/filter on the strike list, not a CSS change.
- **±3%/±6%/±12%/All**: filters strike range shown, similar mechanism to Full/Compact.
- **GEX/VEX toggle**: exclusive 2-state — changes which metric populates the cells (both use the same visual cell treatment, just different underlying numbers). Confirmed via distinct screenshot diffs (`desktop-strike-matrix-vex.png` vs default) that this is a full data swap, not a visual restyle.
- **Matrix/Sniper mode**: Sniper adds a docked side panel (see `desktop-strike-matrix-sniper.png`) — treat as a separate optional child panel rendered conditionally, reuses the same cell-grid component reduced in width.
- **Scroll**: both horizontal (many expiration columns) and vertical (many strikes) — sticky header row + sticky first column must both work simultaneously (`position: sticky` on both, careful with z-index stacking: header-corner cell needs the highest z-index since it's sticky on both axes).

## Text Content
- Column header: "STRIKE" (fixed left corner label)
- Expiration headers: date + days-label pairs (dynamic, from data)
- Footer/status line (seen below grid in screenshots): "{ticker} · {N} contracts · {N}/{N} expirations · {N} strikes shown" and "updated {time} · server cache {N}s" — right-aligned, dimmed, mono font, ~11px

## Responsive Behavior
- **Desktop:** as specified, wide multi-column grid with page-level horizontal scroll if needed
- **Mobile (390px):** entire grid becomes horizontally scrollable within its own container (confirmed in `mobile-strike-matrix.png` — columns get clipped at viewport edge, scrollbar visible), sticky strike column still pins on the left during that horizontal scroll. No column count reduction observed — same data density, just scrolled.
