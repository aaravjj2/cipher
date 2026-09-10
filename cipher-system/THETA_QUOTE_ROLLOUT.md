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

### Reliability follow-up

The production monitor refreshes UTC after provider requests and validates both
quote age and the entry/exit time window at completion. Existing positions are
checked before new candidates. Verified per-position session times are stored
additively in `verified_sessions`; an already-known deadline can therefore create
a persistent pending exit after a restart even when session metadata is down.
Cached times never authorize a fabricated fill or substitute for fresh quotes.
Old positions without a saved session acquire one only from verified metadata.

Snapshot responses now include `quote_coverage` totals (`passes`, `requested`,
`covered`). Data health distinguishes `no_quote_evidence`, `outside_session`,
`stale`, `unresolved`, and `current`; an idle heartbeat is not quote coverage.
Review decisions use a conditional update and transactional audit so a competing
approval cannot overwrite a rejection. Observation activation requirements and
the historical ledger are unchanged. Monitoring is still sequential: slow
provider requests can exceed ten seconds and must fail the existing gap gate.

Still pending: representative screenshot parsing improvements, broader verified
contract-class support, and the complete real-session observation/restart gate.
No historical candidates are replayed by this follow-up.

### Telegram ingestion diagnostics

Telegram polling now has its own `ingestion_health`, `last_ingestion_poll`, and
sanitized error type in status responses. Empty successful polls count as a
working connection; they do not count as quote evidence. Poll/media requests
time out after 30 seconds rather than waiting indefinitely. Logs include the
message ID, disposition, and review reason without source content.

Unresolved review candidates create one durable Discord incident while the
queue remains nonempty, not an alert per message or polling cycle. Transport
errors have a separate deduplicated incident. Neither changes execution gates.
Source-bot trade-opened/closed receipts are administrative, not new instructions.
Previously recorded candidates and their original evidence remain unchanged.

Local OCR version `theta-evidence-v3` disables CSV quote interpretation for
Tesseract TSV and retains line boundaries. Literal quotation marks no longer
swallow later TSV rows. The 85 minimum-confidence requirement remains in force;
chart images and incomplete or conflicting structures still require review.

The daily Discord recap now includes Theta's message count, ingestion health,
review backlog, validated entries and pending exits. It does not expose message
text or image evidence. Autopilot data-block counts are explicitly labeled as
daily incidents rather than current transport health. Source-date counts use
New York day boundaries, including UTC timestamps after midnight.

The recap systemd unit includes narrowly scoped ledger-directory write access
for SQLite WAL shared-memory sidecars, including all three experimental cohort
directories. Without those exceptions its read-only database queries can fail
under `ProtectHome=read-only` even when an interactive preview succeeds. Failed
delivery remains retryable; the same report date is not resent after success.

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
