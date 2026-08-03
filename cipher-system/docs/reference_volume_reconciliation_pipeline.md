# Reference-Only Volume Reconciliation Pipeline

Cipher's current-era Alpaca price panel cannot pass the full volume-sensitive
research gate until its regular-session minute-volume sums are checked against
an independent, like-for-like source.  The previously tested free sources were
rejected, and Polygon/Massive entitlement is insufficient.  This pipeline makes
future evidence ingestion provider-neutral without weakening the gate or
allowing vendor prices into the Alpaca dataset.

## Boundary

The reference source is permitted to contribute only:

- minute timestamps;
- symbols;
- minute share volume;
- immutable source provenance.

It may not contribute prices, fill missing Alpaca bars, scale volume, provide a
daily-bar substitute, or patch Holdout C.  The allowed-use label is:

`independent_regular_session_volume_reconciliation_only`

Alpaca remains the price-data source.  The full session-and-volume gate remains
unchanged.

## Canonical session and threshold

The default frozen session is:

- timezone: `America/New_York`;
- start: `09:30`;
- end: `16:00` inclusive;
- expected minute rows: `391`;
- maximum relative volume difference: `5%`.

The comparison remains:

`abs(alpaca_regular_volume - reference_regular_volume) / reference_regular_volume`

A result at exactly `0.05` passes.  Missing, zero, incomplete, duplicated, or
otherwise invalid reference evidence fails closed.

## Raw evidence layout

Before import, place authorized raw CSV files under:

`data/reference_volume/raw/<provider>/`

The importer refuses files outside the reference-volume raw tree.  It records a
SHA-256 checksum and byte count without modifying the raw file.

## Import command

Example:

```bash
python3 scripts/import_reference_volume_csv.py \
  --provider "Authorized Reference Feed" \
  --input data/reference_volume/raw/authorized_feed/2023.csv \
  --input data/reference_volume/raw/authorized_feed/2024.csv \
  --input data/reference_volume/raw/authorized_feed/2025.csv \
  --timestamp-column timestamp \
  --symbol-column symbol \
  --volume-column volume \
  --source-timezone America/New_York \
  --timestamp-semantics minute_start \
  --start-date 2023-01-01 \
  --end-date 2025-12-31
```

The manifest contains only the selected timestamp, symbol, and volume evidence,
plus per-session quality summaries.  Any price columns in the source are
ignored.

## Reconciliation command

```bash
python3 scripts/reconcile_reference_volume_manifest.py \
  --manifest data/reference_volume/manifests/reference_volume_authorized_reference_feed_<timestamp>.json
```

The script loads the existing Alpaca SIP minute partitions, applies the same
session policy, and emits case-level reconciliation evidence under
`data/market_quality/`.

## Acceptance conditions

A reference session is valid only when:

- all required rows parse;
- timestamps are unique;
- the canonical regular session has exactly 391 minute records;
- regular-session volume is positive;
- the provider, schema, timezone, and timestamp semantics were frozen before
  reconciliation.

A reconciled Alpaca session is eligible only when:

- Alpaca has exactly 391 canonical regular-session bars;
- the independent reference session is valid;
- relative volume difference is no greater than 5%.

No reconciliation artifact automatically promotes a strategy or authorizes
paper/live trading.  It only supplies evidence to the unchanged full data gate.

## Current status

The pipeline is ready for a future authorized source, but independent reference
access remains blocked.  Databento, FirstRate, London Strategic Edge, the
Hugging Face/Finnhub-derived archive, and Polygon/Massive retain their existing
feasibility classifications.  No purchase is made by this pipeline.
