# Loop 045 plan — 30–34px chrome touch targets

**Goal:** Icon chrome that is 24–26px tall becomes 32px (inside DESIGN.md’s 30–34px dense-control band). Heatmap cells stay 26px. Toolbar text pills are a later family if they still overflow.

**Why this loop exists:** PROGRAM 045. Header nav toggle and workspace digits are 24/26px; Sidebar collapse/close are 24px; Night Vision refresh is 26px; Chart Saves delete is 24px. Strike Matrix / Trident icon buttons are already 30px.

**Architecture:**
- Header: nav-toggle `w-8 h-8`; workspace buttons `w-8 h-8`.
- Sidebar: collapse and close `w-8 h-8`.
- Night Vision refresh: `h-8 w-8`.
- Chart Saves delete: `w-8 h-8`.
- Do not change `HeatmapCell` height 26px or Night Vision geometry.

**Files:**
- Modify: Header.tsx, Sidebar.tsx, NightVision.tsx, ChartSaves.tsx
- Create: `web/test/touch-targets.test.mjs`
- Create: `gates/loop-045-touch-targets.md`

**Preserve:** Gold focus, guest catalog, heatmap overflow, no order surface.

**Anti-goals:** No heading redo. No commit. Do not enlarge heatmap cells.

**Checks:** touch-targets + header-a11y + sidebar-a11y + heatmap; full web Node; lint; typecheck; sync; hosted guest E2E (fifth UI loop since 038).
