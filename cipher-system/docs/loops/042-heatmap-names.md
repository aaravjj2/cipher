# Loop 042 — skip (already true)

**Queued claim:** Heatmap cells accessible names; fill gaps only.

**Evidence (no code change):**
- `heatmap-accessibility.test.mjs` already locks Strike Matrix, Trident, GEX Replay cell `aria-label`s, unknown-not-zero, and legend names.
- Both `HeatmapCell` call sites always pass a full `ariaLabel` (metric, dollar or unknown, strike, expiration, largest-exposure star).
- `StrikeLabelCell` always gets `ariaLabel`. GEX Replay maps Call/Put/Net GEX plus coverage.

Skip-if is true. Do not retune heatmap colors, overflow contracts, or Night Vision geometry.
