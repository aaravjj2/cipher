# Loop 041 plan — Settings form labels

**Goal:** Settings form controls have explicit accessible names: auto-refresh pills labelled by a real `<label>`, provider session fields use `htmlFor`/`id` like AuthPanel.

**Why this loop exists:** PROGRAM 041. `FieldLabel` is a `<div>`. Interval pills are unlabelled `aria-pressed` buttons. Provider session inputs wrap in `<label>` but have no `htmlFor`/`id`.

**Architecture:**
- Preferences: `FieldLabel` becomes `<label id="cipher-refresh-interval-label">`. `IntervalPills` becomes a radiogroup with `aria-labelledby`, ArrowLeft/Right, gold focus (Strike Matrix pattern).
- ProviderConnectionPanel: `htmlFor`/`id` on key, secret, options feed, stock feed; keep `type="password"`, `autoComplete="off"`, session-only copy, `setKey("")`/`setSecret("")`.

**Files:**
- Modify: `web/src/components/panels/Settings.tsx`, `web/src/components/auth/ProviderConnectionPanel.tsx`
- Create: `web/test/settings-a11y.test.mjs`
- Create: `gates/loop-041-settings-labels.md`

**Preserve:** Keys never in localStorage; no order surface; research-only copy; credentials masked.

**Anti-goals:** No reduced-motion redo. No commit. Do not print env.

**Checks:** settings-a11y + auth-contract + product-hardening; full web Node; lint; typecheck; sync.
