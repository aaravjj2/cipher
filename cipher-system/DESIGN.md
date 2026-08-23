# Cipher interface system

Cipher is a dense professional research workstation for everyday stock and
options traders. It should feel calm under load: near-black surfaces, precise
typography, one amber action accent, conventional green/red market movement,
and borders used to organize—not decorate.

## Product dials

- Design variance: 4/10
- Motion: 2/10
- Information density: 8/10

## Foundations

- Background `#080a0d`; primary surface `#0e1217`; raised surface `#151a21`.
- Amber `#f0b90b` is the single interactive/selection accent.
- Positive market values are green `#20bf73`; negative values are red `#f04455`.
- Space Grotesk carries navigation and prose. JetBrains Mono carries prices,
  Greeks, times, identifiers, and tables.
- Spacing follows a 4px base. Dense controls are 30–34px tall. Primary content
  uses 12–16px gaps; no page should be a collection of floating rounded cards.
- Radius is 4px for controls, 6px for panels, and never pill-shaped unless the
  content is a status.
- Borders express hierarchy. Shadows are limited to overlays and menus.

## Interaction

- Every control has a visible focus ring and readable accessible name.
- Hover must not move layout. Motion is 120–180ms and disabled for reduced
  motion.
- A skip link reaches the main workspace. Mobile navigation is a real drawer.
- Tables scroll inside labelled regions; the page itself must not overflow.
- Loading, missing, stale, demo, and live data are distinct visual states.

## Options semantics

- GEX remains a public-OI heuristic, never asserted dealer positioning.
- Skew is positioning evidence, not prediction. Use mirrored 25-delta options.
- Raw skew (put IV minus call IV) is shown in volatility points. Normalized skew
  is secondary and mainly for comparable names.
- Earnings/event contamination, quote coverage, history depth, and aligned-price
  availability remain visible. Missing data is never zero.

## Reference choice

The Binance analysis from `voltagent/awesome-design-md` was selected only for
its dense-market hierarchy, conventional P/L color semantics, compact controls,
and restrained panel system. Cipher keeps its own identity and does not copy
Binance assets, layouts, or proprietary UI.
