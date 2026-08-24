# Loop 040 plan — remaining reduced-motion transitions

**Goal:** Honor `prefers-reduced-motion` for remaining CSS transitions and Tailwind spin/pulse, without killing the skeleton reveal delay (that is a visibility delay, not motion).

**Why this loop exists:** PROGRAM 040. DESIGN.md: motion 120–180ms and disabled for reduced motion. Today the media query only stops `.cipher-skeleton` pulse. Sidebar drawer (200ms), duration-150 color transitions, `animate-spin`, and `animate-pulse` still run.

**Architecture:** Expand the existing `@media (prefers-reduced-motion: reduce)` in `globals.css` only. One place covers all Tailwind `transition-*` utilities. Keep `.cipher-skeleton-region` out of that block so heatmap flash-suppression stays.

**Files:**
- Modify: `web/src/app/globals.css`
- Create: `web/test/reduced-motion.test.mjs`
- Create: `gates/loop-040-reduced-motion.md`

**Preserve:** `cipher-skeleton-reveal` 0.25s step-end; heatmap overflow contracts; Night Vision geometry; no order surface.

**Anti-goals:** Do not sprinkle `motion-reduce:` on every panel. Do not redo skip-link. No commit.

**Checks:** reduced-motion + heatmap placeholder tests; full web Node; lint; typecheck; sync.
