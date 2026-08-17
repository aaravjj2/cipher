# Trader Terminal V2.4 audit — Night Vision

Date: 2026-08-14

## Outcome

Night Vision is now a coherent price/exposure chart rather than a collection of independent
overlays. Candles, spot, exposure bands, gamma flip, session levels, selected strikes, and
crosshair price all use one bounded mapping. A dedicated time-volume panel shares the candle
x slots, while strike/volume-profile overlays remain on the price domain without distorting it.

## Interaction and presentation changes

- Added 30/60/120-bar range controls.
- Added explicit RTH versus all-sessions filtering, evaluated in America/New_York rather than
  the browser/server timezone.
- Default intraday view is RTH, avoiding an after-hours-only chart after the close.
- Crosshair snaps to the nearest visible bar and reports timestamp, OHLC, and volume.
- Added horizontal price grid and a separate true time-volume panel.
- Added a compact regime strip: gamma sign interpretation, flip, top pull, PM range, and
  calculable-cell coverage.
- Renamed terse overlay controls to Exposure, SPY/QQQ, Flow, Profile, and X-Ray.
- Missing X-Ray/exposure data now displays an unavailable state; it is never rendered as zero.
- Added a plain-language legend distinguishing public-OI exposure, traded session levels, and
  the Ghost hedge-surface heuristic.
- Kept the side exposure ladder and flow tape optional so they do not crush the default chart.

## Geometry/data audit

- Distant exposure strikes may widen the price domain only within a capped multiple of the
  actual candle range, preventing flattened candles.
- All price marks consume the same monotonic `priceToY` function.
- Volume consumes its own vertical domain and the same bar x geometry.
- Benchmark paths are normalized by percent change and respect the selected session/range.
- ET labels are explicitly timezone-qualified.
- Gamma-flip ambiguity and stale OI/calculable-cell coverage remain visible.

## Verification

- New pure geometry tests cover monotonic mapping, level-domain bounds, crosshair snapping,
  visible tails, and ET RTH boundaries.
- Web typecheck and lint: pass.
- Web source/geometry/accessibility suite: 51 passed.
- Dependency audit: zero vulnerabilities.
- Production build, atomic publication, and build-sync check: pass.
- Authenticated Chromium: 7/7 journeys passed, including desktop OHLC crosshair/volume and a
  390 px Night Vision mobile boundary test.
- Desktop and mobile screenshots were captured; desktop was visually reviewed at
  `web/test-results/night-vision-v2-desktop.png`.
- Full Python suite remained 877 passed, 2 skipped; no Python runtime was changed in this phase.

## Residual gaps / next phase

- Mouse-wheel/pan zoom is intentionally absent; fixed reproducible range buttons are less
  error-prone for saved/reviewed charts. A real viewport state model would be needed before
  adding free pan.
- Chart Save still stores structural metadata rather than a rendered bitmap.
- Flow overlay remains a docked evidence panel instead of trying to force premium prints into
  the price coordinate system.
- The next phase upgrades the reproducibility and realism guarantees of backtesting.
