# Trident Specification

## Overview
- **Target file:** `src/components/panels/Trident.tsx`
- **Screenshot:** `desktop-trident.png`
- **Interaction model:** same as Strike Matrix, ×3 side-by-side instances

## Layout
This panel is **3 independent Strike-Matrix-style heatmap tables side by side** (SPY / QQQ / IWM), each with its own header (ticker, price, change %, expiration label), independent vertical scroll, and its own spot-price marker row.

**Reuse `src/components/panels/StrikeMatrix.tsx`'s cell-rendering and color-scale logic** — extract the heatmap grid + color-scale function into a shared sub-component (e.g. `src/components/panels/HeatmapGrid.tsx`) if it isn't already reusable, so both Strike Matrix and Trident stay visually consistent and you're not duplicating the color-scale formula. Each of the 3 columns here is narrower (~1/3 viewport width) and single-expiration (no multi-column expiration grid — just one strike column + one value column per instrument, based on the screenshot).

Toolbar above the 3-column layout: GEX/VEX, expiration selector, "Snap to spot"/"Snap to golden" buttons, FC/Auto/TR/SP toggle buttons — same pill-button visual convention as Strike Matrix's toolbar.

Each column header: ticker (bold, e.g. "SPY"), price (mono), change% (purple/red per convention), date/days label — right-aligned "Aug 6 · 0d" style.

## Data
Reuse `StrikeMatrixCell[]` type, 3 independent mock datasets (one per SPY/QQQ/IWM) with different price scales (SPY ~$768, QQQ ~$715, IWM ~$299 per screenshot).

## Responsive
Not separately captured. 3-column layout should stack to 1 column below `lg:` (reasonable default — 3 wide data tables can't reasonably fit mobile width side by side).
