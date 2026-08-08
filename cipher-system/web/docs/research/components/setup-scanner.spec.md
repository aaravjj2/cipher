# Setup Scanner Specification

## Overview
- **Target file:** `src/components/panels/SetupScanner.tsx`
- **Screenshots:** `desktop-setup-scanner.png` (empty state), `desktop-setup-scanner-progress-check.png` (scanning state), `desktop-setup-scanner-results-final.png` (populated results — the richest state, prioritize matching this one closely)
- **Interaction model:** click-driven mode/scan-type tabs, async scan with progress bar, card-grid results

## Layout (derived from screenshots, established tokens)
- Heading "Setup Scanner" + subtitle "The Cipher engine ranks the entire universe and surfaces the 30 highest-conviction setups. Powered by the Cipher proprietary model."
- Control row 1: mode tabs (Short term / Long term / LEAP — pill group, active = solid fill) + primary CTA "Cipher Model Scan" (solid `var(--accent)` purple button, bold, stands out from the pill tabs)
- Control row 2: Liq scan / Cluster scan (outline buttons) + "CLUSTER EXP" label + a "Nearest (1 Exp)" dropdown select
- Control row 3: Flash / Flash Index / Flash Agentic buttons, each with a small "BETA" pill badge (gold outline/text), Flash Agentic additionally has a small pulsing dot indicator (purple) when active
- Helper text line below controls: "Short term scans options expiring within ~15 market days." (var(--text-mute), 13px)
- **Scanning state**: warning-toned banner (amber/gold left-border or bg tint, ⚠ icon) with copy about downloading CSV since scans aren't saved; below it, a progress bar (`var(--accent)` fill on `var(--panel-2)` track, full width, ~4px tall, rounded) + status text "Scanning the universe... {n}/{total} tickers ({pct}%)"
- **Results state**: same banner area collapses to just a "Download .CSV" button once complete; results render as a responsive card grid (`repeat(auto-fill, minmax(320px,1fr))`, gap ~16px):
  - Card: `var(--panel)` bg, `var(--line)` border, radius, padding ~20px
  - Header row: "#{rank} ${TICKER}" (rank muted, ticker bold var(--font-mono)) + direction pill ("BULLISH"/"BEARISH" — bullish = purple-tinted pill, bearish = red-tinted, per app convention) + score "{n}/100" (large, bold, right-aligned, gold if top-ranked else white)
  - Data rows: MAJOR SUPPORTS / MAJOR RESISTANCES / PULL TARGET / VACUUM TARGETS — each a `label : value` row, values in var(--font-mono), colored per the purple/red/gold value convention (supports tend red-ish, resistances/targets purple or gold — see screenshot for exact per-field coloring, approximate if unsure)
  - "CIPHER READ" section: label heading (11px muted uppercase) + generated narrative paragraph (14px, var(--text-dim), 1.5 line-height) — this is the most content-rich part of the card, make sure it wraps naturally and doesn't get cut off

## Data
Use `ScannerResultCard[]` from `src/types/cipher.ts`. Mock ~9 cards. Implement the scan as a fake async flow: clicking "Cipher Model Scan" shows the progress bar counting up over ~3-4 seconds (setInterval, not the real 30-45s), then reveals the results grid — this demonstrates the real interaction pattern without requiring an actual multi-minute wait in the demo.

## Responsive
Card grid reflows to 1 column below `sm:`; control rows wrap naturally (flex-wrap).
