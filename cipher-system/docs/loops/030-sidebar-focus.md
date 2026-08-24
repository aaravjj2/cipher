# Loop 030 plan — Sidebar focus-visible

**Goal:** Section headers, panel items, workspace/commands, collapse, and mobile open/close must show a gold `focus-visible` ring. None currently have one.

**Why this loop exists:** PROGRAM 030. Keyboard users tab through the primary nav with no visible focus.

**Architecture:** Shared `FOCUS` class on every `<button>` in `Sidebar.tsx`. Do not change `NAV_SECTIONS` / `GUEST_NAV_SECTIONS` filtering or collapse behavior.

**Files:**
- Modify: `web/src/components/Sidebar.tsx`
- Create: `web/test/sidebar-a11y.test.mjs`
- Create: `gates/loop-030-sidebar-focus.md`

**Preserve:** `GUEST_NAV_SECTIONS` filter, `aria-label="Primary"`, Open/Close navigation, Expand/Collapse sidebar, guest SYSTEM omitted.

**Anti-goals:** No nav item adds/removes. No commit.

**Checks:** focused sidebar + product-hardening guest test; full web Node; lint; typecheck; sync (guest-visible).

## Result 2026-08-23

Shared `FOCUS` gold ring on section headers, panel items, workspace toggle, and commands. Mobile open/close and collapse use the same ring. Guest `GUEST_NAV_SECTIONS` filter unchanged.

