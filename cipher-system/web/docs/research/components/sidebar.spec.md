# Sidebar Specification

## Overview
- **Target file:** `src/components/Sidebar.tsx`
- **Screenshot:** `docs/design-references/accessobsidian.com/desktop-strike-matrix-default.png` (left edge)
- **Interaction model:** click-driven (nav item selection), click-driven (collapse toggle)

## DOM Structure
```
aside.sidebar
├── div.side-top (collapse button row, justify-content: flex-end)
│   └── button.side-collapse (24×24, chevron-left icon)
├── nav.side-nav (flex column, gap 2px)
│   ├── div.side-sec "WORKSPACE" (uppercase section label)
│   ├── button (× 5: Strike Matrix, Night Vision, Spyglass, My Watchlists, Journal)
│   ├── div.side-sec "CIPHER X"
│   ├── button (× 3: Trident, Chart Saves, Setup Scanner — each has a "CIPHER X" pill badge span)
│   ├── div.side-sec "ACCOUNT"
│   └── button (Settings)
```
Each nav button contains: `svg` icon (16×16) + `span` label text.

## Computed Styles (exact values from getComputedStyle)

### Container (`aside.sidebar`)
- display: flex, flexDirection: column
- width: 182px, minWidth: 182px, height: 100% (viewport-matched, observed 844.667px at 900px viewport)
- padding: 12px 10px
- background: `color(srgb 0.0509804 0.0666667 0.0941176 / 0.9)` → equivalent to `rgb(13,17,24)` at 90% opacity → use `color-mix(in srgb, var(--panel) 90%, transparent)`
- borderRight: 0.667px solid rgb(27,34,48) → `var(--line)`
- fontFamily: "Space Grotesk", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif
- fontSize: 13px, color: rgb(215,222,233) → `var(--text)`

### `.side-top` (collapse button row)
- display: flex, flexDirection: row, justifyContent: flex-end
- padding: 9px 9px 2px
- width: 161.333px (= sidebar width minus horizontal padding), height: 35px

### `.side-collapse` button
- width: 24px, height: 24px, display: grid, alignItems: center
- borderRadius: 7px
- border: 0.667px solid rgb(27,34,48) → `var(--line)`
- color: rgb(91,102,120) → `var(--text-mute)`
- icon: 16×16 chevron-left svg (see `icons.tsx` → `ChevronLeftIcon`)

### `nav.side-nav`
- display: flex, flexDirection: column, gap: 2px
- width: 161.333px

### `.side-sec` (section label — "WORKSPACE" / "CIPHER X" / "ACCOUNT")
- fontSize: 9px, fontWeight: 600, letterSpacing: 1.44px (= 0.16em)
- textTransform: uppercase
- color: rgb(91,102,120) → `var(--text-mute)`
- padding: 2px 11px 7px

### Nav item button (default/inactive state)
- display: flex, flexDirection: row, alignItems: center, gap: 10px
- padding: 9px 11px
- width: 161.333px, height: 34px
- borderRadius: 8px
- fontSize: 12.5px, fontWeight: 600, letterSpacing: 0.375px
- color: rgba(118,129,146,0.6) → `var(--text-dim)`
- background: transparent
- icon: 16px svg, color: rgb(91,102,120) → `var(--text-mute)` (dimmer than label text — icon is muted even when label is semi-bright)

### Nav item button (active state — e.g. "My Watchlists" observed active in desktop-watchlists.png)
- background becomes a solid rose/magenta-tinted fill (visually distinct from purple accent — sample from screenshot: roughly `#7a2140`–`#8a2848` range, treat as a dedicated `--nav-active` token, NOT the same as `--accent`)
- color becomes full-bright `var(--text)` / white
- **Action for builder:** sample exact active-state background color from `docs/design-references/accessobsidian.com/desktop-watchlists.png` (the highlighted "My Watchlists" pill) using image inspection, since computed-style capture only ran on the Strike-Matrix-active state. Do not guess — this rose/magenta tone is visually distinct from `--accent` purple and worth getting exactly right.

### "CIPHER X" badge (on Trident / Chart Saves / Setup Scanner)
- Small pill badge, inline after the label span within the same button
- Gold/amber background (`var(--gold)` family), dark text, small caps, rounded-full
- Exact padding/font-size: approximate as `text-[9px] font-semibold px-1.5 py-0.5 rounded-full` pending closer inspection — flag as approximate in the component, not exact-measured

## States & Behaviors

### Collapse toggle
- **Trigger:** click `.side-collapse` button
- **State A (expanded):** width 182px, nav items show icon + label
- **State B (collapsed):** not directly observed this session — infer icon-only rail (~52px width) based on the chevron affordance; **flag as approximate, verify against live site before finalizing**
- **Transition:** assume `transition: width 0.2s ease` (standard, not measured)

### Nav item selection
- **Trigger:** click any nav button
- **Before:** `.text-dim` colored label, muted icon, transparent background
- **After:** active nav item gets solid background fill (see above) + full-bright text; all other items return to default state
- **Transition:** not measured — assume `transition: background-color 0.15s ease, color 0.15s ease`

### Hover (not directly captured — approximate)
- Standard treatment: slight background lift (`var(--panel-2)` at low opacity) + text brightens toward `var(--text)`. Verify against live site during Phase 5 QA.

## Assets
- Icons from `src/components/icons.tsx`: `ChevronLeftIcon` (collapse), `GridIcon` (Strike Matrix), a chart/pulse icon (Night Vision — reuse `StrikeMatrixIcon` shape or add new), `SearchIcon` (Spyglass), `StarIcon` (My Watchlists), `JournalIcon` (Journal), `TridentIcon` (Trident), `BookmarkIcon` (Chart Saves), `ScannerIcon` (Setup Scanner), `SettingsIcon` (Settings).
- Note: exact icon-to-nav-item mapping should be verified against `docs/design-references/accessobsidian.com/desktop-strike-matrix-default.png` — icons.tsx names are best-guess from shape, not confirmed 1:1.

## Text Content (verbatim)
- Section labels: "WORKSPACE", "CIPHER X", "ACCOUNT"
- Nav items: "Strike Matrix", "Night Vision", "Spyglass", "My Watchlists", "Journal", "Trident", "Chart Saves", "Setup Scanner", "Settings"
- Badge text: "CIPHER X" (×3)

## Responsive Behavior
- **Desktop (1440px):** as specified above, 182px fixed width, always visible
- **Mobile (390px):** sidebar fully hidden, replaced by a hamburger icon button in the header (see `desktop-strike-matrix.png` vs `mobile-strike-matrix.png`). Hamburger presumably opens the same sidebar as an overlay/drawer — **not confirmed this session, drawer-open state not captured**. Builder should implement as a slide-in drawer (standard mobile nav pattern) and flag for QA verification against live site.
- **Breakpoint:** not pinpointed between 390 and 1440; use Tailwind's `lg:` (1024px) as a reasonable default and verify during Phase 5.
