# Night Vision Specification

## Overview
- **Target file:** `src/components/panels/NightVision.tsx`
- **Screenshots:** `desktop-night-vision-base.png`, `-SP.png`, `-SPYQQQ.png`, `-TS.png`, `-VP.png`, `-XRay.png`
- **Interaction model:** independent on/off overlay toggles (see `BEHAVIORS.md` — NOT mutually exclusive tabs), click-driven timeframe/expiration selects

## Layout
- Toolbar row: expiration-mode pills (1 Exp/Compact/Full/Leap), timeframe pills (1D/5m/More→1m/15m/1H/4H/1W/EOD), overlay toggle buttons (SP/SPY-QQQ/TS/VP/X-Ray — each independently toggleable, active state = filled/highlighted), Capture + Save chart buttons, legend dots (Top pull=gold, Above spot=purple, Below spot=red circles + labels), refresh icon + Auto-refresh toggle — all in the established pill-button convention.
- **Chart area**: this is canvas-rendered on the live site (candlesticks + horizontal support/resistance lines + a floating spot-price label + a background watermark logo). For the clone, build a **mock candlestick chart** — don't attempt real charting library integration unless one is already in the scaffold's dependencies (check `package.json` first). A reasonable approach: render an SVG with:
  - ~25-30 candlestick bars (rect + line wick), colored purple (`var(--accent)`) for up candles / red (`var(--neg)`) for down candles — confirmed from screenshots this app uses purple=up, NOT green
  - 2-3 horizontal dashed support/resistance lines in purple/gold with small floating labels showing strike ranges (e.g. "96 · 320")
  - A gold horizontal dashed spot-price line with a "SPOT {price}" floating label
  - Y-axis price labels on the right, X-axis date labels on the bottom
  - A faint centered background watermark (the Cipher logo mark, very low opacity) — matches all screenshots
- Overlay-specific additions:
  - **SP toggle**: docked right-side panel showing a Strike-Matrix-style single-column heatmap (reuse the shared heatmap cell component from the Trident/StrikeMatrix work)
  - **VP toggle**: vertical bar histogram overlaid on the right edge of the chart itself (not a side panel — bars extending from the right edge, purple, varying width by volume)
  - **SPY/QQQ, TS, X-Ray**: not visually distinguishable from base state in the captured screenshots at this zoom/detail level — implement as simple state toggles that at minimum change the toolbar button's active styling; visual chart changes for these can be a lower-fidelity approximation, mark as "approximate — verify against live site."

## Data
Mock OHLC candlestick data (~25-30 bars), mock support/resistance levels, mock spot price. No type currently exists in `cipher.ts` for this — add a simple `Candle = { date: string; open: number; high: number; low: number; close: number }` type inline in the component or extend `cipher.ts`, your call.

## Responsive
Chart area scales to container width; toolbar wraps on mobile (flex-wrap), matching the pattern established elsewhere.
