# Header (Topbar) Specification

## Overview
- **Target file:** `src/components/Header.tsx`
- **Screenshot:** `docs/design-references/accessobsidian.com/desktop-strike-matrix-default.png` (top bar)
- **Interaction model:** static shell with a slot for panel-specific contextual actions (static/click-driven per-panel, out of scope for this component)

## Architecture note
The header's LEFT side (mobile nav-toggle, brand, ticker search, quote) is identical across all 9 panels. The RIGHT side varies per panel (Night Vision has chart toggles, Spyglass has Bio/Contract Search links, Strike Matrix has Full/Compact/GEX/VEX, etc. — see `PAGE_TOPOLOGY.md`). Build `Header` as a shell component accepting a `panelName: string` prop (for `.brand-sub`) and a `rightSlot?: React.ReactNode` prop for panel-specific actions, rather than hardcoding every panel's contextual buttons here. Downstream panel components will pass their own action bars into `rightSlot`.

## DOM Structure
```
header.topbar
├── button.nav-toggle (mobile-only hamburger — see note below)
├── div.brand
│   ├── span.brand-mark (26×26 logo image)
│   ├── span.brand-name "CIPHER"
│   └── span.brand-sub (dynamic — current panel name, e.g. "SETUP SCANNER")
├── div.search
│   ├── svg (search icon, 14×14)
│   ├── input (ticker search, uppercase mono)
│   ├── kbd.search-kbd "/" (keyboard shortcut hint)
│   └── div.suggestions (absolute-positioned dropdown, hidden by default)
├── div.quote
│   ├── span.quote-ticker "AAPL"
│   ├── span.quote-price "$311.56"
│   └── span.quote-change "+0.18%" (color varies: positive/negative)
├── {rightSlot} — panel-specific action buttons render here
├── span "Welcome {name}! 🚀" (dimmed, mono-ish casual text)
└── a/button "Logout"
```

## Computed Styles (exact values from getComputedStyle)

### Container (`header.topbar`)
- display: flex, flexDirection: row, alignItems: center, gap: 14px
- height: 55.333px (~56px)
- padding: 10px 16px
- background: 85%-opacity panel → `color-mix(in srgb, var(--panel) 85%, transparent)`
- borderBottom: 1px solid var(--line)
- position: sticky (implied by zIndex 30 + persistent-on-scroll behavior observed) top: 0
- zIndex: 30
- fontSize: 13px, fontFamily: var(--font-sans)

### `.nav-toggle` (mobile hamburger button)
- **Note:** captured styles show unstyled native-button defaults (gray bg, outset border, black text) — this appears to be a currently-unstyled/placeholder element in the source, NOT an intentional design choice. Builder should style it consistently with the rest of the UI instead of replicating the raw native-button look: use `MenuIcon` from icons.tsx, transparent background, `var(--text)` color, ~24×24, visible only below `lg:` breakpoint (matches Sidebar's mobile drawer trigger — coordinate with `Sidebar.tsx`, don't duplicate the hamburger, reuse the same trigger).

### `.brand`
- display: flex, flexDirection: row, alignItems: center, gap: 10px
- width: ~209px, height: 26px

### `.brand-mark`
- width: 26px, height: 26px, borderRadius: 7px
- background: `url(logo) no-repeat center/cover` — use the downloaded asset at `public/seo/cipher-logo.jpg`
- boxShadow: `inset 0 0 0 1px rgba(255,255,255,0.063), 0 4px 14px rgba(154,79,245,0.25)` — a subtle inset ring + purple glow. Use `box-shadow: inset 0 0 0 1px rgba(255,255,255,0.06), 0 4px 14px color-mix(in srgb, var(--accent) 25%, transparent)`

### `.brand-name` ("CIPHER")
- fontSize: 13px, fontWeight: 700, letterSpacing: 2.34px (~0.18em)
- color: var(--text)

### `.brand-sub` (dynamic panel name, e.g. "SETUP SCANNER")
- fontSize: 10px, letterSpacing: 2.2px (~0.22em)
- color: var(--text-mute)
- **Dynamic content** — pass as `panelName` prop, render uppercase

### `.search`
- display: flex, flexDirection: row, alignItems: center
- width: 150px, height: 34.667px (~35px)
- padding: 0 9px
- background: var(--panel-2) (rgb(17,22,31) matches --panel-2 exactly)
- borderRadius: 8px
- border: 1px solid var(--line)
- position: relative (for the suggestions dropdown)

### `.search input`
- fontSize: 13px, fontWeight: 600, fontFamily: var(--font-mono)
- letterSpacing: 0.78px, textTransform: uppercase
- color: var(--text)
- padding: 8px, background: transparent, border: none

### `.search-kbd` ("/" hint)
- fontSize: 10px, fontFamily: var(--font-mono)
- color: var(--text-mute)
- background: var(--panel-2)
- padding: 1px 5px, borderRadius: 4px
- border: 1px solid var(--line), borderBottom: 2px solid var(--line) (slightly thicker bottom border — subtle "keycap" effect)

### `.suggestions` (dropdown, hidden until search has results)
- position: absolute, top: calc(100% + 6px), zIndex: 50
- background: var(--panel-2), borderRadius: 8px, border: 1px solid var(--line)
- boxShadow: 0 14px 38px rgba(0,0,0,0.6)
- **Not populated in any capture this session** — build empty-state container only, mark "content unknown — verify against live site"

### `.quote`
- display: flex, flexDirection: row, alignItems: baseline, gap: 9px
- width: ~173px, height: 28.667px
- padding: 5px 12px
- background: var(--panel-2), borderRadius: 8px, border: 1px solid var(--line)

### `.quote-ticker`
- fontSize: 13px, fontWeight: 700, fontFamily: var(--font-mono), letterSpacing: 1.04px
- color: var(--text)

### `.quote-price` / `.quote-change`
- fontFamily: var(--font-mono)
- `.quote-change` color: `var(--accent)` (purple) when positive, `var(--neg)` (red) when negative — confirmed from screenshots: "+0.18%" rendered in purple, matching the app's non-standard purple=up/red=down convention (see DESIGN_TOKENS.md)

### "Welcome {name}!" text + Logout
- Casual dimmed text, `var(--text-mute)`, includes an emoji suffix (rocket 🚀 observed) — treat emoji as user-configurable/dynamic, not a fixed asset
- Logout: styled as a bordered pill button, same treatment family as `.search-kbd`/nav buttons — `padding: 8px 14px, border: 1px solid var(--line), borderRadius: 8px, color: var(--text-dim)`, approximate (not exactly measured)

## States & Behaviors
- Search input: typing presumably filters `.suggestions` dropdown (not captured — build as controlled input with empty dropdown, mark TODO)
- Quote updates live (real-time price ticking) in the source — for the clone, static display is fine (mock data), no polling needed
- Header stays fixed/sticky during scroll (inferred from zIndex 30 + app being a fixed-viewport dashboard, not confirmed via actual scroll test)

## Text Content (verbatim)
- Brand: "CIPHER" + dynamic subtitle (current panel name, uppercase)
- Search placeholder/kbd: "/" shortcut hint
- "Welcome {displayName}! 🚀" — dynamic per user
- "Logout"

## Responsive Behavior
- **Desktop (≥1024px):** full layout as specified, ~1440px container width observed
- **Mobile (<1024px):** `.nav-toggle` hamburger becomes visible (shared trigger with Sidebar drawer); `.brand-sub`, `.quote` width, and possibly `.search` width likely compress or hide — **not directly captured, only compared via full-header mobile screenshots** (see `mobile-strike-matrix.png` — header restructures to 2 stacked rows: row 1 = hamburger + logo + search, row 2 = quote + workspace tabs + logout). Implement as a `flex-col` stack below `lg:`, mark as "approximate — cross-check against mobile screenshots during Phase 5."
