# Access Obsidian / Cipher Product Research Paper

Audit session: July 18, 2026, America/New_York  
Target: https://www.accessobsidian.com/app#SLV  
Observed title: Cipher - Strike Matrix  
Method: authenticated Chrome UI exploration, DOM/control inventory, non-destructive interaction testing, screenshots, responsive checks, and browser diagnostics.

## Abstract

Access Obsidian's Cipher app is a dense options-trading workstation centered on heatmapped dealer exposure, chart overlays, scanner workflows, watchlists, journaling, and account/API configuration. The product behaves like a professional trading terminal rather than a general website: most value is packed into a small number of high-information screens with persistent ticker context, workspace switching, and repeated controls for mode, expiration, metric, and refresh.

The core experience is strongest on desktop. Strike Matrix and Night Vision are visually distinctive and information-rich, while Setup Scanner and Settings are clearer, more spacious feature pages. The main risks are not missing functionality but operational UX hazards: crowded controls, hidden preloaded DOM panels, some controls that are present but not always interactable, mobile occlusion, and sensitive credential handling surfaces that require very careful redaction and trust messaging.

## Evidence Set

Primary screenshots captured during the audit:

- `01-baseline-fullpage.png`: initial authenticated app state.
- `02-strike-matrix.png`: Strike Matrix baseline.
- `15-night-overlays.png`: Night Vision with SPY/QQQ split, VP/X-Ray overlays, and timeframe menu.
- `19-setup-scanner-revisit.png`: Setup Scanner top-level controls.
- `12-settings.png`: Settings overview.
- `34-mobile-strike.png`: mobile Strike Matrix responsive check.
- `35-tablet-strike.png`: tablet Strike Matrix responsive check.

Additional section captures include Watchlists, Journal, Trident, Chart Saves, ticker search, scanner modes, and workspace switching. JSON inventories are saved alongside screenshots in the same folder. Credential-like field values were redacted from saved JSON artifacts and are not reproduced in this report.

## Product Model

Cipher is organized around a persistent ticker context. In this session the URL hash, search input, and header state were synchronized around `SLV`; changing the ticker to `AAPL` updated the hash to `#AAPL`, changed the header quote display, and then restored cleanly to `#SLV`.

The main navigation model is:

- Strike Matrix: multi-expiration strike heatmap for GEX/VEX and matrix/sniper modes.
- Night Vision: chart workspace with candlesticks, split SPY/QQQ view, volume profile, X-Ray overlay, trade tape affordances, timeframe controls, and chart capture/save actions.
- Spyglass: flow/scanning style view, captured as a separate workspace screen.
- My Watchlists: ticker list and compact level monitoring.
- Journal: calendar-style trading journal with P/L and note-entry surfaces.
- Cipher X: Trident, Chart Saves, and Setup Scanner.
- Settings: plan status, preferences, refresh interval, Alpaca API connection, and feedback form.

Hidden or inactive DOM surfaces were also observed for Options Watchlist, Mispricing Scanner / Convexity Edge, WO Admin, Mispricing Admin, second workspace selection, and Cipher X upgrade modal. In the current visible session these were preloaded but not active standalone screens.

## Feature Analysis

### Strike Matrix

Strike Matrix is the center of the product. It is not just a table; it is the app's primary model for translating an option chain into a map of likely pressure zones. The screen arranges strikes vertically and expirations horizontally. In the SLV audit state, the visible columns included near-dated expirations such as Jul 20, Jul 22, Jul 24, Jul 27, and Jul 29, while the rows moved through strikes around and away from spot. The footer reported a data scope of `SLV · 1,904 contracts · 5/12 expirations · 102 strikes shown`, plus update time and server cache age.

The practical reading flow is:

1. Start at spot.
   The matrix draws a horizontal spot marker, observed at `SPOT 50.78` for SLV. This divides the table into levels immediately above and below current price.

2. Look for dominant cells.
   Large values and saturated cells identify where the model sees the strongest exposure. In the observed SLV matrix, notable examples included positive cells such as `$665.1K` near the 53 strike and negative cells such as `-$1.6M` near the 50.5 strike on one expiration column. These cells visually function as magnets, walls, floors, or danger zones depending on metric and sign.

3. Compare across expirations.
   The horizontal layout makes it possible to see whether a level is isolated to one expiration or repeated across the term structure. A single bright cell may be event-specific; repeated levels across multiple expirations suggest a more structural zone.

4. Switch the metric.
   The GEX/VEX selector changes the lens. GEX appears to emphasize gamma-related dealer pressure around strikes; VEX appears to expose volatility/vega-related pressure. A user would use GEX for near-price pinning, acceleration, and support/resistance logic, while VEX is more useful when asking where volatility positioning may matter.

5. Narrow or widen the battlefield.
   The +/-3%, +/-6%, +/-12%, and All controls govern how far away from spot the matrix should show strikes. This is important because near-spot scalping and broader swing/context analysis require different strike windows. A short-term trader wants fewer rows and higher signal density; a swing trader may need the full distribution.

Core controls and inferred purpose:

- `Full` vs `Compact`: changes information density. Compact makes the screen more scannable, while Full likely expands the strike universe, labels, or row spacing.
- `+/-3%`, `+/-6%`, `+/-12%`, `All`: sets strike-distance window around spot.
- `GEX` / `VEX`: switches exposure model.
- `Matrix` / `Sniper`: changes analytical mode. Matrix is the broad heatmap; Sniper appears to focus on the most actionable or compressed set of levels.
- Refresh icon: requests a fresh matrix.
- `Auto refresh`: updates the matrix every 15 seconds.
- CSV download: exports the current matrix.
- Node Galaxy: a force-directed exposure map. This was observed as a hidden/available control; it likely turns rows and expirations into a network visualization of exposure nodes.
- `Expiration` dropdown, `Snap to spot`, `Snap to golden`, `FC`: related Trident/multi-symbol matrix controls were present in the DOM. `Snap to spot` centers columns on each ticker's spot, `Snap to golden` centers on the dominant level, and `FC` boxes the closest floor and ceiling.

What the feature is trying to answer:

- Where are the highest-pressure levels above and below spot?
- Is current price sitting inside a supportive, resistant, or unstable zone?
- Which expiration carries the dominant positioning?
- Are levels clustered tightly enough to imply pinning, or separated enough to imply a runway?
- Which strikes should be transferred into Night Vision as chart overlays or into scanner logic as targets/floors/ceilings?

Strengths:

- The heatmap compresses a large option chain into a single visual field.
- The spot marker makes the analysis immediately contextual.
- Expiration columns reveal whether levels are short-lived or repeated.
- The footer provides meaningful data provenance: contract count, expiration count, strike count, update time, and cache age.
- The matrix is fast to scan once the color language is learned.

Risks:

- The feature depends heavily on color, saturation, and small numeric labels. A trader with color-vision limitations or a dim screen could miss critical distinctions.
- There is no visible plain-language summary such as "nearest floor", "nearest ceiling", "dominant positive level", or "dominant negative level" on the main matrix itself.
- The difference between Matrix and Sniper is not explained on-screen.
- CSV and Node Galaxy are powerful, but their exact current scope should be made explicit before export or view switching.

### Night Vision

Night Vision is the chart translation layer for the Strike Matrix. Where Strike Matrix answers "where are the option-derived levels?", Night Vision answers "how is price behaving around those levels right now?" It overlays the matrix's zones on candlestick charts and adds comparison, flow, and numeric inspection tools.

Observed active state:

- Ticker context: `SLV` in the header, but the split chart displayed SPY and QQQ when `SPY/QQQ` was enabled.
- Chart mode: `daily · sniper Jul 20` was visible in the chart subtitles.
- Overlays enabled in the screenshot: `SPY/QQQ`, `VP`, and `X-Ray`.
- Timeframe menu exposed `1m`, `15m`, `1H`, `4H`, and `1W`, with primary buttons for `1D` and `5m`.
- The right panel showed `X-RAY` with GEX/VEX tabs, a legend for Top pull / Above spot / Below spot, and a strike-by-strike numeric table.

The practical reading flow is:

1. Choose chart scope.
   `1 Exp`, `Compact`, `Full`, and `Leap` determine how much expiration structure is projected onto the chart. `1 Exp` focuses on the nearest expiration, while `Leap` includes all listed expirations including LEAPs. This matters because short-dated levels may guide intraday action, while longer-dated levels provide broader structure.

2. Choose timeframe.
   The chart supports immediate and higher-timeframe views: 1D, 5m, plus 1m, 15m, 1H, 4H, and 1W under More. This lets a trader move from macro structure to execution timing.

3. Turn on overlays.
   `VP` shows volume profile, described by the app as traded volume by price with POC, HVN, LVN, and bounce edges. This gives a market-structure confirmation layer independent of options exposure. `X-Ray` overlays the strike matrix on the chart and opens the numeric side panel. `TS` toggles the live trade tape. `Ghost` is described as a projection of the next 15 minutes from the dealer hedging surface for SPY/QQQ.

4. Compare index context.
   `SPY/QQQ` splits the workspace into two index charts. This is particularly useful because the app can show whether one index is approaching a major matrix wall while the other remains in open space.

5. Inspect exact levels.
   The X-Ray panel converts the visual bands back into numbers. In the captured SPY/QQQ state, the side panel displayed large GEX values around several QQQ strikes, including very large positive and negative zones near spot. This is important because the chart overlay gives intuition, while the X-Ray table gives precision.

Core controls and inferred purpose:

- Chart view icon: toggles chart mode from the matrix/workbench context.
- `SPY/QQQ`: creates a comparative split view for the two major index ETFs.
- `TS`: live trade tape. The related controls include print-size filters such as `>5k`, `>50k`, `>100k`, an `SQ` combined SPY+QQQ stream, and a trade-date selector.
- `VP`: volume profile with POC/HVN/LVN context.
- `X-Ray`: matrix overlay and side table.
- `Capture`: saves a chart image to the computer.
- `Save chart`: stores the chart in Chart Saves with top-pull context.
- `1 Exp`, `Compact`, `Full`, `Leap`: selects the expiration/depth of overlaid levels.
- `1D`, `5m`, `More`: selects chart timeframe.
- Refresh hot strikes: refreshes the level set.
- Auto refresh: refreshes hot strikes every 15 seconds.
- `Ghost`: dealer-surface projection control. It was visible in the control inventory, but interaction timed out in the tested state, so the behavior could not be verified.
- `Expand`: expands chart to fullscreen.
- X-Ray `GEX` / `VEX`: changes the side-panel metric.

What the feature is trying to answer:

- Is price approaching a matrix-derived wall, floor, or magnet?
- Did price reject, accept, or slice through a high-exposure level?
- Does volume profile confirm the same support/resistance area as options exposure?
- Are SPY and QQQ aligned, diverging, or one leading the other?
- Is a trade setup forming near a top-pull or below/above-spot pressure zone?
- Are large live prints confirming or contradicting the chart/matrix read?

Strengths:

- It connects abstract option exposure to price behavior, which is the most important product bridge.
- The X-Ray panel prevents the chart from becoming purely visual; exact strike/GEX/VEX values remain accessible.
- Split index mode is genuinely useful for market-context work.
- Volume profile adds a second, non-options-derived confirmation layer.
- The save/capture workflow supports review and journaling.

Risks:

- Night Vision has the highest cognitive load in the app. Header controls, left nav, chart controls, split charts, overlay legends, and the X-Ray table are all visible at once.
- Active state should be made more explicit through `aria-pressed`, persistent labels, and a compact "currently showing" summary.
- Ghost projection needs clearer availability and feedback. If it only applies to SPY/QQQ or only under certain modes, the UI should explain that before click.
- Trade tape and chart save actions can affect workflow state, so they need strong status and confirmation messages.

### Setup Scanner

Setup Scanner is the discovery engine. Instead of making the user pick a ticker and manually inspect levels, it ranks the broader universe and surfaces candidates. The screen states that the Cipher engine ranks the entire universe and surfaces the 30 highest-conviction setups. This makes Setup Scanner the top-of-funnel workflow for finding what to analyze next in Strike Matrix and Night Vision.

Observed top-level structure:

- Time horizon buttons: `Short term`, `Long term`, and `LEAP`.
- Primary model: `Cipher Model Scan`.
- Specialized scans: `Liq scan` and `Cluster scan`.
- Cluster expiration selector: `Nearest (1 Exp)`, followed by listed weekly expirations such as Fri Jul 24, Fri Jul 31, Fri Aug 7, Fri Aug 14, Fri Aug 21, Fri Aug 28, Fri Sep 4, and Fri Sep 11.
- Intraday beta models: `Flash BETA`, `Flash Index BETA`, and `Flash Agentic BETA`.

The practical reading flow is:

1. Choose horizon.
   `Short term` scans options expiring within approximately 15 market days, according to the visible help text. `Long term` and `LEAP` extend the search farther out. This choice should match trade intent: day/swing setups use short term; thesis or structural convexity setups use longer terms.

2. Choose model family.
   `Cipher Model Scan` appears to be the general-purpose ranking model. It is likely the default way to surface the top 30 names without specifying a niche pattern.

3. Choose specialized pattern.
   `Liq scan` is explicitly described as a liquidity-vacuum runway: a dominant level far from spot, thin liquidity between spot and that target, and a second line stacked near it. In plain terms, this is a scan for names where price may have room to travel if it starts moving toward a strong level.

4. Inspect clusters.
   `Cluster scan` appears to look for stacked or repeated levels around a target region. The expiration dropdown lets the user select whether the cluster should be evaluated in the nearest expiration or a specific future weekly expiration.

5. Use Flash for intraday candidates.
   Flash is described as an intraday model using nearest-expiration GEX/VEX floors and ceilings, session key levels including PMH/PML/PDH/PDL/PWH/PWL, multi-timeframe volume profile, VWAP, momentum, touch/reaction, and last-5-minute flow across the 12 most-liquid names. First targets are ATR-scaled rungs, with the structural wall as the stretch target.

6. Use Flash Index for market pulse.
   Flash Index applies the same intraday model to QQQ, SPY, and IWM. The UI notes that it is not logged, suggesting it is intended as a quick live market read rather than a database-backed scan.

7. Use Flash Agentic as a monitor.
   Flash Agentic is described as a living monitor that watches every name every minute during market hours and surfaces a setup only after it triggers or arms. It tracks confirmed breaks/rejections, price arming at triggers, target progression, and greys out finished plays.

Core scan modes and inferred purpose:

- `Short term`: near-expiry opportunity search, approximately within 15 market days.
- `Long term`: broader dated opportunities where structure may matter more than intraday timing.
- `LEAP`: long-dated optionality and structural positioning.
- `Cipher Model Scan`: broad proprietary ranking model for top setups.
- `Liq scan`: finds runway setups where a dominant level is far from spot and the path between spot and target appears thin.
- `Cluster scan`: finds repeated/stacked levels around an expiration or target area.
- `Cluster Exp`: selects which expiration window should define the cluster.
- `Flash`: intraday setup engine using levels, session references, volume profile, VWAP, momentum, touch/reaction, and last-5-minute flow.
- `Flash Index`: same Flash concept restricted to SPY, QQQ, and IWM.
- `Flash Agentic`: continuous monitor that surfaces only armed/triggered setups and follows progression.
- Hidden/inactive related controls from the broader DOM included filters for All/Upside/Downside, Triple/Quad, Bullish/Bearish/Neutral, Refresh, live price refresh, Run scan, and CSV exports. These indicate that scanner results likely support direction, pattern-strength, sentiment, refresh, and export workflows once populated.

How Setup Scanner connects to the rest of Cipher:

- It should be the entry point when the user does not already know what ticker to inspect.
- A surfaced ticker should lead into Strike Matrix for structural confirmation.
- The same ticker should then move into Night Vision for chart/flow confirmation.
- If the setup is acted on, the Journal can record the trade and Chart Saves can preserve the visual thesis.
- Watchlists can hold promoted names from scanner output for ongoing monitoring.

What the feature is trying to answer:

- Which names currently have the strongest model-ranked opportunity?
- Is the opportunity directional, clustered, liquidity-vacuum based, or intraday?
- Which expiration carries the setup?
- Has a setup merely appeared, armed, triggered, progressed, or completed?
- Which candidates deserve deeper manual review in Strike Matrix and Night Vision?

Strengths:

- Setup Scanner gives the app a complete discovery workflow rather than only a ticker-inspection workflow.
- The model taxonomy is ambitious and useful: general model, liquidity runway, cluster, Flash, index Flash, and agentic monitor.
- Tooltips provide rare but valuable descriptions of proprietary concepts.
- The screen has more breathing room and clearer hierarchy than the chart-heavy views.

Risks:

- The scanner needs stronger result-state communication. After selecting modes during this audit, the UI did not clearly show whether a scan was running, completed, empty, or waiting for a separate Run action.
- The beta models are powerful but under-explained on-screen. Their tooltips are excellent, but the page should also summarize inputs, outputs, and intended use.
- If a mode requires market hours, live data, a populated result set, or a `Run scan` action, the UI should say that directly.
- Directional filters and result filters appear in hidden DOM state. When results are visible, those filters should be introduced progressively rather than all at once.

### Watchlists

The watchlist screen presents saved tickers with daily move, price, and compact level context. The Add to Watchlist control was observed but not clicked because it would mutate user data.

Strengths:

- Good quick-monitoring surface.
- Fits the app's persistent ticker workflow.

Risks:

- Needs clear success/undo affordances for adding/removing tickers.
- Watchlist mutations should have visible confirmation and be easy to reverse.

### Journal

The trading journal shows a monthly calendar, daily P/L, and fields for entering profit/loss, amount, and notes. Save day and Clear day were observed but not clicked.

Strengths:

- Integrates execution reflection into the same trading workspace.
- Calendar + daily note structure is intuitive.

Risks:

- Financial journaling actions are stateful; destructive actions such as Clear day need confirmation.
- Journal inputs should autosave cautiously or make save state unambiguous.

### Chart Saves

Chart Saves stores Night Vision snapshots with top-pull state at capture time. Existing saved chart cards were visible in the captured screen.

Strengths:

- Strong bridge between real-time analysis and later review.
- Useful for post-trade study.

Risks:

- Needs metadata clarity: capture ticker, timestamp, active overlays, timeframe, and whether data was live or cached.

### Settings And API Connection

Settings includes plan state, timezone and auto-refresh preferences, Alpaca data instructions, connected status, credential fields, and feedback form. The page states keys stay in the browser and are used for market data, not order placement.

Strengths:

- Clear plan status and connected badge.
- Good plain-language explanation of Alpaca OPRA requirements.
- Auto-refresh interval options are simple and visible.

Risks:

- A credential-like key value was visible in a text input during DOM extraction. Even if this is only a paper/public key, the UI should treat it as sensitive. Masking, copy-on-demand reveal, and redaction-friendly DOM patterns are recommended.
- Feedback Submit, API Connect/Disconnect, and credential fields are stateful; these were not tested to avoid changing account state.

## Responsive UX Findings

Desktop is the intended surface. At 1707 x 932 the Strike Matrix is usable and information-dense. At tablet width the app remains visible but cramped. At mobile width, the sidebar overlays the matrix and obscures much of the working surface. No horizontal document overflow was reported, but the visible composition is still impaired because the nav drawer covers the content.

Mobile recommendation: treat mobile as a monitoring/summary mode, not a compressed terminal. A mobile-first layout should prioritize ticker, quote, selected mode, top levels, and a simplified table, with chart/matrix details behind tabs or a fullscreen drill-in.

## Inferred Weighting And Reconstruction Notes

This section is explicitly inferential. The UI exposes outputs, labels, controls, and tooltips, but it does not expose source code, formulas, database schemas, or exact proprietary weights. The following is the most plausible reconstruction path for someone trying to build a similar system.

### Data Inputs Needed

A recreation would need these data feeds:

- Options chain by ticker, expiration, strike, side, bid, ask, mark, open interest, volume, implied volatility, greeks, and trade date.
- Underlying price data for spot, OHLC candles, VWAP, ATR, premarket high/low, prior day high/low, prior week high/low, and current session metadata.
- Live or delayed options trades for tape classification, size filters, bid/ask context, and buy/sell inference.
- Volume-by-price data or a way to derive volume profile from intraday bars.
- Universe definitions: liquid index ETFs, most-liquid names, low-IV institutional names, watchlist names, and scanner-eligible tickers.
- User account state: plan, watchlists, chart saves, journal entries, preferences, API connection status, timezone, refresh interval.

### Strike Matrix Formula Layer

The Strike Matrix likely starts with one row per option contract:

```text
contract = {
  ticker,
  expiration,
  strike,
  side: call | put,
  open_interest,
  volume,
  implied_volatility,
  gamma,
  vega,
  delta,
  bid,
  ask,
  mark,
  underlying_spot
}
```

A gamma exposure approximation for each contract can be built as:

```text
contract_gex = gamma * open_interest * contract_multiplier * spot^2 * sign
```

Where `contract_multiplier` is usually 100 for listed U.S. equity options. The sign convention is a product decision. A common implementation signs call/put or dealer-side exposure differently depending on whether the model assumes customers are net long options and dealers are short. The UI shows positive and negative cells, but not the sign convention.

A vega exposure approximation can be built as:

```text
contract_vex = vega * open_interest * contract_multiplier * volatility_move_unit * sign
```

The matrix cell value is then aggregated by strike and expiration:

```text
cell_gex[strike, expiration] = sum(contract_gex for contracts at strike/expiration)
cell_vex[strike, expiration] = sum(contract_vex for contracts at strike/expiration)
```

The visual weight of a cell appears to be based on magnitude relative to the visible matrix:

```text
visual_intensity = abs(cell_value) / max(abs(cell_value) in current view)
```

Then:

- Positive values map to purple/magenta.
- Negative values map to red.
- Dominant/top-pull values map to yellow/gold.
- Empty or tiny values map to dark/neutral cells.

The most important implementation detail is that visual intensity should be recomputed for the current viewport/range, not the entire chain, otherwise near-spot detail can disappear when far-out giant levels exist.

### Strike Matrix Derived Levels

To recreate the analytical layer, compute:

- `spot`: current underlying price.
- `nearest_floor`: largest-magnitude supportive level below spot.
- `nearest_ceiling`: largest-magnitude resistant level above spot.
- `top_pull`: dominant absolute exposure level in the selected expiration/range.
- `golden_level`: likely the dominant or highest-conviction level used by Snap to golden.
- `runway`: distance from spot to next dominant level, adjusted for how thin intermediate levels are.
- `cluster_score`: density of large levels within a strike band.
- `pin_score`: concentration of large levels near spot.

One possible scoring model:

```text
level_strength =
  0.45 * normalized_abs_gex
+ 0.20 * normalized_abs_vex
+ 0.15 * expiration_weight
+ 0.10 * open_interest_weight
+ 0.10 * recency_or_volume_weight
```

Expiration weighting should decay with time for intraday/scalp modes:

```text
expiration_weight = 1 / sqrt(days_to_expiration + 1)
```

For longer-term and LEAP views, the decay should flatten so longer expirations are not unfairly suppressed:

```text
long_term_expiration_weight = log(open_interest + 1) * sqrt(days_to_expiration)
```

The exact weights above are not observed from the app; they are a reasonable reconstruction framework based on what the UI emphasizes.

### Matrix vs Sniper

The UI does not define the exact difference, but a rebuild could treat the two modes as:

- Matrix: full exposure heatmap across selected expirations and strike range.
- Sniper: reduced/actionable level set, emphasizing nearest floors, nearest ceilings, top-pull levels, and unusually strong zones around spot.

Possible Sniper filter:

```text
include level if:
  abs(level_strength) >= percentile_85
  OR distance_to_spot <= near_spot_threshold AND abs(level_strength) >= percentile_65
  OR level is nearest_floor / nearest_ceiling / top_pull
```

This would explain why Sniper is useful for execution while Matrix is useful for full structural context.

### Night Vision Reconstruction

Night Vision can be rebuilt as a chart compositor:

1. Fetch OHLC candles for the selected ticker/timeframe.
2. Fetch matrix levels for selected expiration depth.
3. Convert each strike into chart y-coordinate.
4. Draw horizontal bands with intensity based on exposure magnitude.
5. Draw candlesticks over the bands.
6. Add volume profile to the side of the chart.
7. Add X-Ray panel with exact strike/value rows.
8. Optionally add live trade tape and flow markers.

The chart layer likely uses canvas because the audit observed 4 canvas elements. A performant recreation should use canvas or WebGL for the candle/heatmap layer and DOM/SVG only for controls, axes, legends, and panels.

Night Vision overlay weights could be:

```text
chart_band_opacity =
  0.55 * normalized_abs_exposure
+ 0.20 * proximity_to_spot
+ 0.15 * selected_expiration_relevance
+ 0.10 * recent_touch_reaction_score
```

Volume profile confirmation can be scored independently:

```text
vp_confirmation =
  1.0 if exposure_level aligns with POC
  0.7 if aligns with HVN
  0.4 if aligns with LVN edge
  0.0 otherwise
```

Then a level's chart importance can combine options exposure with volume structure:

```text
chart_level_importance =
  0.70 * exposure_level_strength
+ 0.30 * vp_confirmation
```

Ghost projection, based on the tooltip, would likely use SPY/QQQ dealer hedging pressure to project the next 15 minutes. A rebuild could model it conservatively as:

```text
ghost_direction =
  weighted_sum(
    distance_to_nearest_floor,
    distance_to_nearest_ceiling,
    net_gex_gradient_near_spot,
    recent_momentum,
    last_5_min_flow
  )
```

Where the projection is not a price prediction in the absolute sense, but a directional path estimate toward likely hedging pressure zones.

### Setup Scanner Weighting

Setup Scanner should rank tickers by turning the matrix and chart-derived features into candidate scores. A general `Cipher Model Scan` could use:

```text
cipher_score =
  0.25 * level_strength_score
+ 0.20 * runway_score
+ 0.15 * cluster_score
+ 0.15 * flow_confirmation
+ 0.10 * volume_profile_confirmation
+ 0.10 * liquidity_score
+ 0.05 * spread_quality
```

Where:

- `level_strength_score`: strength of nearest floor/ceiling/top-pull levels.
- `runway_score`: distance to target adjusted by thinness of intermediate liquidity.
- `cluster_score`: repeated levels in a tight band.
- `flow_confirmation`: recent options prints aligned with direction.
- `volume_profile_confirmation`: VWAP/POC/HVN/LVN agreement.
- `liquidity_score`: tradability of underlying and options.
- `spread_quality`: bid/ask spread, executable size, and quote stability.

For horizon modes:

```text
short_term_score =
  0.40 * nearest_expiration_exposure
+ 0.25 * intraday_momentum
+ 0.20 * last_5_min_flow
+ 0.15 * spread_quality

long_term_score =
  0.35 * multi_expiration_cluster
+ 0.25 * open_interest_persistence
+ 0.20 * distance_to_structural_wall
+ 0.20 * trend_alignment

leap_score =
  0.35 * long_dated_open_interest
+ 0.25 * long_dated_vex
+ 0.20 * institutional_liquidity
+ 0.20 * macro_trend_context
```

For `Liq scan`, the tooltip provides enough detail to define a specific formula:

```text
liq_scan_score =
  0.40 * dominant_level_distance_score
+ 0.30 * path_thinness_score
+ 0.20 * stacked_second_line_score
+ 0.10 * tradability_score
```

Definitions:

- `dominant_level_distance_score`: target far enough from spot to matter.
- `path_thinness_score`: few strong opposing levels between spot and target.
- `stacked_second_line_score`: a second meaningful exposure level near the target, suggesting reinforcement.
- `tradability_score`: options spreads and underlying liquidity are acceptable.

For `Cluster scan`:

```text
cluster_scan_score =
  0.45 * level_density
+ 0.25 * cross_expiration_agreement
+ 0.15 * proximity_to_spot
+ 0.15 * directional_cleanliness
```

For `Flash`:

```text
flash_score =
  0.18 * nearest_exp_gex_floor_ceiling
+ 0.14 * nearest_exp_vex_floor_ceiling
+ 0.12 * session_key_level_alignment
+ 0.12 * volume_profile_alignment
+ 0.12 * vwap_position
+ 0.12 * momentum
+ 0.10 * touch_reaction
+ 0.10 * last_5_min_flow
+ 0.10 * atr_target_quality
```

For `Flash Index`, use the same formula but restrict the universe to SPY, QQQ, and IWM. For `Flash Agentic`, add state transitions:

```text
candidate states:
  dormant -> arming -> triggered -> target_1_hit -> target_2_hit -> completed
                         -> failed / cooled
```

Agentic surfacing rule:

```text
show candidate only if:
  state in [arming, triggered, target_1_hit, target_2_hit]
  AND confidence_score >= threshold
```

This matches the tooltip language: it only surfaces setups once they trigger, confirm a break/reject, or arm at the trigger.

### Suggested System Architecture For Rebuild

A practical rebuild would use:

- Backend jobs for option-chain ingestion, greek normalization, exposure aggregation, scanner scoring, and cache warming.
- Realtime or near-realtime endpoints for current ticker matrix, hot strikes, live prices, and flow tape.
- A frontend state model centered on `ticker`, `workspace`, `viewMode`, `metric`, `expirationDepth`, `timeframe`, and `refreshInterval`.
- Canvas rendering for Strike Matrix and Night Vision chart overlays.
- DOM controls for filters, navigation, settings, and accessible summaries.
- Persistent storage for watchlists, journal entries, chart saves, user preferences, and API connection metadata.

Suggested API shapes:

```text
GET /api/matrix?ticker=SLV&metric=gex&depth=compact&expirations=5
GET /api/hot-strikes?ticker=SLV&metric=gex&mode=sniper
GET /api/night-vision?ticker=SLV&timeframe=1d&depth=1exp
GET /api/scanner?model=cipher&horizon=short
GET /api/scanner/flash?universe=liquid
GET /api/watchlist
POST /api/chart-saves
POST /api/journal/day
```

Recommended matrix response:

```text
{
  ticker,
  spot,
  updated_at,
  cache_seconds,
  expirations: [{ date, days_to_expiration }],
  strikes: [50, 50.5, 51, ...],
  cells: [
    { strike, expiration, gex, vex, intensity, role }
  ],
  derived: {
    nearest_floor,
    nearest_ceiling,
    top_pull,
    golden_level,
    pin_score,
    runway_score
  }
}
```

Recommended scanner result:

```text
{
  ticker,
  score,
  direction,
  model,
  horizon,
  trigger,
  target_1,
  target_2,
  structural_wall,
  nearest_floor,
  nearest_ceiling,
  expiration,
  reasons: [
    "thin runway to dominant upside level",
    "cluster confirmed on nearest weekly",
    "VWAP and flow aligned"
  ],
  state
}
```

### Key Details For Someone Recreating The UI

- Keep ticker context global. The URL hash, search input, header quote, matrix, chart, and scanner drilldowns should all share the same ticker state.
- Make every model output explainable. Each highlighted level or scanner card should show why it matters.
- Pair every heatmap with exact numeric inspection. Visual intuition is not enough for trading use.
- Add visible active states to every toggle. Use `aria-pressed` and a compact summary such as `GEX · Matrix · Compact · +/-12% · Auto off`.
- Treat mobile as a separate product surface. Do not compress the full workstation into a narrow viewport.
- Cache aggressively but disclose cache age. The existing footer's cache age is a good pattern.
- Separate observed data from model inference. If a scanner score is proprietary, still show contributing factors.
- Include safety rails for stateful actions: saving charts, clearing journal days, disconnecting data, exporting CSVs, and submitting feedback.

## Accessibility And UI Details

Observed positives:

- Many controls have useful `title` or `aria-label` values.
- Core navigation buttons are readable and consistently grouped.
- Active states are visible through magenta/purple styling.

Issues to improve:

- Some visually clear controls did not resolve cleanly through accessible-name locators.
- Hidden panels remain in the DOM with headings and controls, which can confuse automation and may confuse assistive technology unless correctly hidden with accessibility semantics.
- Color is doing heavy semantic work. Add redundant labels, patterns, or toggles for colorblind users.
- Small monospace text and low-contrast gray copy may be difficult on dim displays.
- Mobile sidebar/content layering needs refinement.

## Reliability And Diagnostics

Browser console logs captured during the final diagnostic pass contained no app errors or warnings. DOM signals showed a canvas-heavy interface:

- 4 canvas elements.
- 37 SVG elements.
- 5 images.
- 0 iframes.
- App scripts served from `www.accessobsidian.com`.
- Font assets loaded from Google Fonts hosts.

One browser-control-side analytics/network warning occurred during tab claiming, but it was not an app console error and did not block the audit.

## Safety Boundaries During Testing

The audit intentionally did not:

- Submit feedback.
- Save or clear journal entries.
- Add/remove watchlist items.
- Connect or disconnect API credentials.
- Download CSV exports.
- Save chart snapshots.
- Log out.
- Use admin, credential, or external checkout actions.

These were skipped because they could mutate account state, transmit data, download files, or change authentication/account configuration.

## Priority Recommendations

1. Harden sensitive-field handling.
   Mask API keys by default, avoid exposing credential-like values in ordinary DOM extraction, and provide explicit reveal/copy controls.

2. Improve hidden DOM accessibility.
   Ensure inactive panels are truly inert to keyboard, screen readers, and automation. Use `aria-hidden`, `inert`, or conditional rendering where appropriate.

3. Add mobile-specific layouts.
   Use summary cards, tabs, and fullscreen drill-ins rather than overlaying a sidebar over the matrix.

4. Strengthen scanner feedback.
   After mode/scan clicks, show loading, active mode, timestamp, result count, and empty-state reason.

5. Make advanced toggles self-verifying.
   Toggles like Ghost, X-Ray, VP, TS, and Node Galaxy should expose active/inactive state via `aria-pressed` and visually persistent labels.

6. Reduce color-only semantics.
   Add legends, textual signs, and optional colorblind palettes for GEX/VEX, above/below spot, and top-pull states.

7. Guard destructive/account actions.
   Clear day, Disconnect, Submit, Save chart, and Add Watchlist should provide confirmation, success feedback, and undo where practical.

## Conclusion

Access Obsidian's Cipher app is already a coherent and powerful trading workstation. Its strongest surfaces are the Strike Matrix and Night Vision, which combine options exposure, price context, and visual scanning in a way that feels purpose-built for active traders. The largest opportunities are around robustness and trust: make hidden surfaces inert, expose toggle state accessibly, protect credential fields, and design mobile as a separate monitoring mode rather than a shrunken desktop terminal.

The app's product direction is compelling: a unified command center for options structure, chart context, scanner discovery, and trade journaling. With targeted improvements to accessibility, responsive behavior, and stateful action safeguards, it can become both more professional and safer to operate under real trading pressure.
