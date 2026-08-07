# Settings Specification

## Overview
- **Target file:** `src/components/panels/Settings.tsx`
- **Screenshot:** `desktop-settings.png`
- **Interaction model:** static informational sections, click-driven preference pills, form-based API key connect flow

## Layout (derived from screenshot, established tokens)
- Heading "Settings" + subtitle "Connect your Alpaca market-data key to power Cipher. Everything stays in your browser."
- Sections, each a `var(--panel)` bg / `var(--line)` border / `var(--radius)` card, stacked with ~16px gap, ~24px internal padding:
  1. **"Your plan"** — crown icon + "Your plan" heading (left), "CIPHER X" gold pill badge (right), body text "You're on Cipher X — every feature is unlocked. Thanks for going all in."
  2. **"Preferences"** — clock icon + heading, body copy, then two labeled controls: "TIMEZONE (INTRADAY CHARTS, FLOW TAPE TIMES)" → a select-like button "Auto · match this device"; "AUTO-REFRESH INTERVAL" → pill group (5s/15s/30s, active = filled)
  3. **"Connect API"** — key icon + heading, "CONNECTED" green-tinted status pill (right-aligned) — note this is a rare non-purple/red status color (green for "connected"/success is a plausible exception to the purple/red convention, treat as a dedicated `--success` token, e.g. `#3ecf8e`, distinct from `--accent`), followed by "How it works." bold lead-in + explanatory paragraph, then a numbered list (1-4) of setup instructions with bold key terms and one inline link ("Get the subscription here →" in accent purple), ending with a bordered callout box: "Cipher does **not** touch order generation or place any trades..."

## Data
All static/mock — no real API key handling needed (this is a UI clone, not a functional settings page). Preference pills and API-connect button can be presentational `useState` only.

## Responsive
Sections stack full-width on all breakpoints (already single-column at desktop); no special mobile treatment needed beyond standard padding reduction.
