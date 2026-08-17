# Phase V2.6a audit — options history and event context

Date: 2026-08-14 UTC

## Outcome

Cipher now accumulates one normalized OPRA surface observation per weekday for
the 26-symbol Research Desk universe. The Options Terminal exposes the real
coverage count and withholds IV rank/percentile until 20 distinct sessions exist.
Corporate actions are revisioned from Alpaca market data; upcoming earnings
remain explicitly unavailable because no authoritative earnings provider is
configured.

## Implemented

- `core/option_history.py`: SQLite/WAL history for front ATM IV, 25-delta skew,
  term slope, total known OI/volume, median spread, quote/IV/OI coverage, expiry
  metrics, source time, and raw SHA-256.
- Missing IV, OI, volume, and quotes stay unknown; totals are null when every
  input is unknown.
- Intraday observations are collapsed to one latest value per distinct session
  for rank eligibility, preventing polling frequency from inflating sample size.
- `scripts/capture_option_surface_history.py`: bounded, sequential, read-only
  OPRA capture for the Research Desk universe with per-symbol degradation.
- Daily 15:50 ET weekday timer installed and enabled.
- Existing latest chains seeded 11 symbols without network calls.
- Live bounded capture passed for MU, NVDA, SNDK, and SPY: 22,533 contracts,
  zero symbol errors, explicit coverage on every derived surface.
- `core/event_context.py`: atomic latest snapshot plus append-only revision
  ledger for provider-observed corporate actions.
- Daily 07:10 ET event timer installed and enabled; live capture returned 8
  actions across 26 symbols.
- Company Context now reads the revisioned event source and retains SEC dividend
  facts. It labels Alpaca action creation-time limitations and refuses to use the
  dataset as point-in-time backtest evidence.

## Truthfulness checks

- SNDK API: rank null, 1/20 sessions, current ATM IV/skew/term metrics and exact
  IV/OI/quote coverage visible.
- Earnings: `UNAVAILABLE`, no inferred dates or headline guesses.
- NVDA corporate actions: source hash and `point_in_time_ready: false` visible;
  no matching action was rendered as zero events, not as a fabricated event.
- Both scheduled services declare read-only behavior and contain no broker/order
  client.

## Verification

- focused option/event/terminal/workspace suite: 11 passed;
- web source suite: 51 passed;
- typecheck, lint, production build: passed;
- authenticated Options Terminal browser gate: passed;
- live API and both new systemd timers: passed.

## Remaining limitations

- Twenty distinct sessions require calendar time; Cipher correctly does not
  manufacture history from repeated same-day snapshots.
- ATM is selected using closest 0.50/-0.50 delta when deriving the stored
  surface, so low-IV-coverage chains can have no usable ATM observation.
- The action provider does not guarantee record creation time. These events are
  operational context only until a point-in-time source is added.
- Upcoming earnings remain a known gap. The solution is a separately configured
  authoritative provider adapter, not inference from news.

Phase verdict: accepted. The accumulation runway is active and the product says
exactly why IV rank and earnings may still be unavailable.
