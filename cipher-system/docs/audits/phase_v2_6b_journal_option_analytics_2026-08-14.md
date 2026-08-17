# Phase V2.6b audit — option journal analytics and chart evidence

Date: 2026-08-14 UTC

## Outcome

Manual journal entries can now carry validated exact OCC option legs and replay
captured Tradier bid/mid/ask/trade marks over the recorded lifecycle. Underlying
MFE/MAE remains separate. Saved chart state receives an attached rendered SVG
preview rather than only an opaque JSON blob.

## Implemented

- Migrated the journal database in place with bounded `legs_json` and rendered
  `chart_snapshot_svg` fields; existing user rows are preserved.
- Validated side, quantity, multiplier, contract symbol, and optional entry mark
  per leg. Multi-leg records retain every leg independently.
- Added exact-contract, indexed lookup against `tradier_option_timesales`.
- Reports bid, mid, ask, and trade MFE/MAE in percent and dollars using exact
  quantity and multiplier.
- Missing contracts/windows remain `NO_CAPTURED_MARKS`; marks are never
  interpolated or replaced with zero.
- Manual entry marks and first-captured fallback marks are source-labelled.
- Added progressively disclosed exact-leg input and per-leg mark evidence to the
  journal UI, keeping the default form compact.
- Rendered chart-state SVGs use escaped user text and are labelled as schematic
  saved-state evidence, not historical price exports.

## Live bounded verification

Captured NVDA `NVDA260814C00227500`, 2026-08-13 session, one long contract,
$1.23 entry reference:

- 54,774 captured mark events;
- first/last: 13:30:15 / 19:59:58 UTC;
- bid-path MFE +61.7886%, MAE -52.8455%;
- dollar MFE +$76, MAE -$65;
- output caveat states marks are simulated valuations, not fills.

## Verification

- journal/option/workspace focused suite: 7 passed;
- web source suite: 51 passed;
- typecheck and lint: passed;
- production build and atomic publication: passed;
- authenticated Journal progressive-disclosure browser gate: passed.

## Remaining limitations

- Replay coverage is limited to contracts subscribed by the Tradier capture.
  Absence means unavailable, not a flat premium path.
- Asynchronous multi-leg events are not interpolated into a synthetic structure
  time series; each leg is reported independently to avoid false precision.
- The SVG is a durable rendered state preview, not a pixel-perfect candle export.
  A future server-side chart renderer can replace it without changing the journal
  attachment contract.

Phase verdict: accepted. Option marks are now materially useful while their
capture and fill limitations remain explicit.
