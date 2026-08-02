# Cipher Backtest Report — Corrected Scope

The previous report mixed underlying-price proxy simulations with options
terminology. It has been retired. **This is not an options backtest.**

## Scope boundary

- The v4/v5/v6 results are based on daily underlying OHLCV.
- They do not model option contracts or executable historical option quotes.
- Legacy names such as `put_write`, `covered_call`, and
  `vol_regime_selling` describe proxy signals, not actual options positions.
- Their reported returns cannot support options strategy selection or live
  deployment.

## Current options status

No credible options backtest has completed and no profitable options edge has
been validated. The old Tradier and Alpaca-snapshot outputs were purged.

See `data/OPTIONS_RESEARCH_STATUS.json` for the authoritative status and use
only the strict dataset audit and point-in-time scenario runner for future
options research.
