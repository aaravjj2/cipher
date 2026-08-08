# Access Obsidian / Cipher — Design Tokens

Extracted from live computed styles at `https://www.accessobsidian.com/app` on 2026-08-06.
Site already displays "CIPHER" branding in-app — this is the user's own production deployment.

## Colors (CSS custom properties on `:root`)

| Token | Value | Usage |
|---|---|---|
| `--bg` | `#07090f` | Page background |
| `--panel` | `#0d1118` | Card/panel background |
| `--panel-2` | `#11161f` | Secondary panel background (slightly lighter) |
| `--line` | `#1b2230` | Borders |
| `--line-soft` | `#141a26` | Subtle borders/dividers |
| `--text` | `#d7dee9` | Primary text |
| `--text-dim` | `#76819299` (alpha 60%) | Secondary text |
| `--text-mute` | `#5b6678` | Muted/placeholder text |
| `--accent` | `#9a4ff5` | Primary purple accent (positive/purple candles, active states) |
| `--accent-2` | `#6c2bff` | Secondary purple/blue accent |
| `--gold` | `#f7bd29` | Spot price line, highlights, "CIPHER X" premium tag |
| `--neg` | `#f93a52` | Negative/red (down candles, losses) |
| `--radius` | `10px` | Default border radius |

`--glass-panel`: `linear-gradient(180deg, color-mix(in srgb, #11161f 80%, transparent), color-mix(in srgb, #0d1118 86%, transparent))` — glassmorphism panel background, used on floating/overlay surfaces.

Body background: `rgb(7, 9, 15)` (matches `--bg`).

## Fonts

- **Sans**: `"Space Grotesk", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif` — headings, UI labels, buttons
- **Mono**: `"JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, monospace` — tickers, prices, numeric data, tables

Both are Google Fonts — load via `next/font/google`.

## Favicon

`https://www.accessobsidian.com/static/obsidian-logo.png` (legacy filename — site is branded Cipher but favicon path retains "obsidian")

## Framework signals

Site uses Tailwind (v4-style `--tw-*` custom properties present), same stack family as this scaffold (Next.js + Tailwind + shadcn). Token mapping to `globals.css` should be close to 1:1.

## Stack of positive/negative color usage

- Purple (`--accent` / `#9a4ff5`) = up/bullish candles and highlight state
- Red (`--neg` / `#f93a52`) = down/bearish candles and negative values
- Gold (`--gold`) = spot price marker, "top pull" strike highlight, premium ("CIPHER X") badges
- This is NOT the conventional green/red — it's purple/red. Important to get exactly right, it's a distinctive brand choice.
