# Theta quote portfolio rollout

The historical `theta_paper.sqlite` is retained without conversion. The new
`theta_quote_v1.sqlite` starts in observation mode. No brokerage order adapter
is used. Strategy and fill assumptions are versioned `theta-quote-v1`.

## Implemented behavior

- Every leg has quantity one. Entry crosses bid/ask with 10 basis points adverse
  slippage and $0.65 per leg per side. Quotes require positive executable sizes,
  an age of at most 15 seconds, and at most five seconds between legs.
- All legs resolve to exact provider metadata. Root/expiry/type/strike mismatches,
  adjusted multipliers and unresolved settlement classes block the whole entry.
  Standard physical settlement is verified from provider deliverables, exercise
  style and multiplier. SPX is never substituted with SPXW.
- Liquidation crosses the opposite side with the same costs. TP is 50% and SL
  25% of absolute entry premium, excluding entry fees from the threshold basis.
  Cash availability reserves worst-case terminal loss; unbounded short calls
  are rejected. Signal prices remain evidence, not fills.
- Telegram ingestion, monitoring and outbox delivery have independent async
  loops. Monitoring continues during Telegram delivery or startup outages.
  A close/trim with a unique reply/position identity closes the whole position;
  ambiguous textual matches remain in review. Missing quotes retain pending
  requests across restarts. Expired settlement stays unresolved.
- Tesseract extracts images locally with hashes, text, minimum word confidence
  and parser version. Every stated leg requires an explicit quantity; price
  units and holding policy must be explicit. Captions must agree. A correction
  preserves original evidence and must pass fresh contract/quote checks within
  two minutes of the original message. No historical replay during activation.
- Review requires trusted owner identity (`CIPHER_THETA_REVIEW_USER_ID`), not a
  browser-supplied identity. Configure the existing authenticated user's ID in
  the server environment. Missing owner configuration denies review access.
- Position changes and stable event IDs commit with the notification outbox.
  Discord failures retry; delivery remains at-least-once and may repeat an event
  ID following a crash after remote receipt.

## Verified session sources and limits

The default Alpaca adapter currently resolves regular/early closes for standard
SPY, QQQ and IWM options from provider calendar dates plus the published exchange
class schedule. Other classes require explicit provider `session_close` and
settlement evidence. Unsupported contracts stay blocked; no guessed close or
settlement prices are used. These reference rules require a version change if
the exchange changes its schedule.

- [Alpaca contract metadata](https://docs.alpaca.markets/us/reference/get-options-contracts)
- [Alpaca calendar including early closes](https://docs.alpaca.markets/us/v1.1/reference/getcalendar-1)
- [Nasdaq option class hours](https://www.nasdaqtrader.com/Trader.aspx?id=optionshours)
- [NYSE early closes for eligible options](https://www.nyse.com/trade/hours-calendars)

## Rollout commands

Run with the Cipher Python environment from `cipher-system/`:

```sh
python scripts/theta_rollout.py backup
python scripts/theta_rollout.py status
```

The existing Theta service now starts the observation worker and initializes its
cursor at the newest Telegram message on first use. Restart it once after
observations have accumulated, then inspect reconciliation and health again.

```sh
python scripts/theta_rollout.py activate
```

Activation refuses unless a complete 6.5-hour regular session has been recorded,
monitor gaps are no longer than 20 seconds, all requested quote sets are covered,
at least one real structure was observed, and restart reconciliation passed.
There is no bypass flag. The current rollout must accumulate that real session;
offline fixtures do not qualify it.

Earnings artifacts trained with this revision freeze a probability using unique
training events before forward scoring. Old forecast lines stay unchanged and
cannot acquire a baseline retroactively. Autopilot's frozen cohorts, rejection
counts, quote coverage and promotion gates remain in their existing scorecards.
