# Options-first UI and skew audit — 2026-08-23

## Outcome

Cipher's guest experience is now a flatter, denser trading workstation with a
single amber action accent, conventional green/red price movement, clearer data
states, keyboard focus support, and a real stored-observation Skew Map. No
separate hackathon repository or submission work was performed in this phase.

## Inputs and tools

- Read the supplied Notion skew-map article in a real browser.
- Installed and read `taste-skill`, `redesign-skill`, and Vercel's
  `web-design-guidelines` skill.
- Selected the Binance `DESIGN.md` from `voltagent/awesome-design-md` for dense
  financial hierarchy only; no assets or proprietary layout were copied.
- Installed `@playwright/cli`, its project skill, and Chrome for Testing 152.
- Used Ponytail to avoid a UI framework migration and Unlazy gates to retain
  implementation and verification evidence.

## Baseline defects

Headed Chrome at 1440×900 showed:

- purple used simultaneously for AI identity, selection, and positive market
  movement;
- every ticker and metric isolated in a rounded bordered card;
- a large gradient demo hero followed by three cards and two more cards;
- redundant guest and research-only notices competing with the active task;
- no skip link to the workspace;
- inconsistent focus treatment and a visually heavy ticker-strip scrollbar;
- options skew present only as a small expiry value, without interpretation,
  provenance, or a cross-name view.

Baseline artifact: `artifacts/ui-before-guest-desktop.png`.

## Changes

- Reworked shared tokens around near-black surfaces, amber action/selection,
  green positive movement, red negative movement, 4px spacing, and 6px radius.
- Added `cipher-system/DESIGN.md` as the product-level visual/data contract.
- Added a skip link, focus-visible ring, `min-h-dvh`, tighter workspace padding,
  and a simpler guest boundary banner.
- Flattened the ticker tape and guest showcases into border-defined sections and
  rows rather than independent rounded cards.
- Corrected metadata from “pixel-perfect UI clone” to an auditable stocks and
  options research workstation.
- Added Skew Map to the primary Analyze navigation and the guest-safe catalog.
- Added `/api/skew-map`, which joins stored OPRA surface observations to aligned
  daily bars and exposes quality/freshness without order capability.
- Added raw and normalized skew, quadrant reading, coverage, expiration, history
  depth, and event/quality caveats.

## Browser evidence

The deployed headed browser loaded the Skew Map with 12 stored names, no console
messages, and zero document overflow. Final artifact:
`artifacts/ui-after-skew-desktop.png`.

The exhaustive hosted guest audit traversed all 29 guest panels at both
1440×900 and 390×844:

```text
2 passed (26.0s)
```

It asserts no console errors, page errors, failed HTTP responses, private-route
requests, or horizontal document overflow.

## Verification

- Skew unit tests: 2 passed.
- App Node tests: 33 passed.
- Web Node tests: 65 passed.
- ESLint: passed.
- Strict TypeScript: passed.
- Next.js production build: passed.
- Published static tree: synchronized.
- `cipher-core` and `cipher-web`: restarted and active.
- Headed Playwright CLI inspection: Skew Map present, overflow 0, console 0.

- Full Python suite, including the research-only guard: 1,114 passed, 1 skipped.

## Remaining limits

- The skew history has only five distinct stored sessions, so every point is
  provisional; this is not evidence of a trading edge.
- Earnings-in-expiry is a written warning, not yet an automatically joined field.
- Several older panels still use local inline styles and rounded containers;
  future changes should migrate them only when touching those panels, not via a
  risky whole-repository cosmetic rewrite.
- The ticker tape is horizontally scrollable by design on narrow screens.
