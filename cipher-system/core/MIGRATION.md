# Clean migration boundary

## Reused from the prior work

- The documented dollar-gamma convention: `gamma × open interest × 100 × spot² × .01`, with put contribution signed negative.
- Strike-profile wall selection and adjacent-strike zero-crossing interpolation for the displayed gamma-flip heuristic.
- The Strike Matrix / Night Vision split: a matrix of expiry-by-strike exposure and an overlay chart of selected aggregate levels.

## Deliberately excluded

- All credentials, `.env` files, databases, cached market data, browser profiles, virtual environments, and build artifacts.
- The old Tradier relay. The clean core uses the existing local Alpaca credentials only for read-only market data.
- Any executor, paper trader, scheduled order runner, or manual order endpoint.
- The old `spot²` re-scan flip calculation: with frozen Greeks/OI it rescales uniformly and does not produce a meaningful sign change.

## Data quality rule

A contract without both gamma and open interest is **unknown** for GEX; the service never treats it as zero. At the current Alpaca snapshot path, open interest may not be present, so the core keeps the visual fallback and returns no calculated GEX levels until a source with historical/current OI is connected.

This keeps the interface usable while making data gaps explicit instead of manufacturing levels.