---
name: research-orchestrator
description: Coordinate source-first equity, earnings, valuation, and options research across Aarav Finance connectors and the local Cipher engine.
---

# Research orchestrator

Use this as the entry point when a request spans fundamentals and options.

## Source order

1. Use Equibles as the independent primary-source verification layer for SEC,
   FINRA, FRED, 13F, insider, and related data.
2. Use Cipher for local quotes, bars, chain snapshots, scanner output, GEX/VEX,
   replay, and backtest artifacts.
3. Use FlashAlpha for supplemental exposure, volatility, earnings, or structure
   analytics when the user asks for it or Cipher lacks that field.

Do not silently merge conflicting observations. Report source, observation time,
feed, and the disagreement. State when a value is delayed, missing, modeled, or
provider-derived.

## Required output sections

- Question and as-of time
- Evidence table with source citations
- Fundamental interpretation
- Market/options context, if requested
- Risks, missing data, and contradictions
- Conclusion with confidence limited by the evidence

Do not produce a buy/sell instruction, live-order action, or unqualified dealer-
positioning claim. Cipher GEX is a public-OI heuristic, not verified dealer
positioning.
