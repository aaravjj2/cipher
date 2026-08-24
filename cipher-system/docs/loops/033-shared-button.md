# Loop 033 plan — Shared ui/button Cipher gold focus

**Goal:** Replace shadcn `focus-visible:ring-ring` on the shared Button primitive with Cipher gold so any consumer matches DESIGN.md.

**Why this loop exists:** PROGRAM 033. Default focus uses generic `--ring`, not `--gold`.

**Architecture:** One class change in `buttonVariants` base string. Keep destructive variant’s own focus colors. Do not migrate panels onto this primitive.

**Files:**
- Modify: `web/src/components/ui/button.tsx`
- Create: `web/test/shared-button-a11y.test.mjs`
- Create: `gates/loop-033-shared-button.md`

**Preserve:** CVA variants/sizes, `data-slot="button"`, disabled opacity.

**Anti-goals:** No new Button usages. No commit.

**Checks:** focused test; full web Node; lint; typecheck; sync.

## Result 2026-08-23

Base `buttonVariants` focus is `focus-visible:ring-2 focus-visible:ring-[var(--gold)]`. Destructive variant still uses destructive rings. No new Button consumers.

