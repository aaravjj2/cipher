# Backtest findings — Obsidian EOD detector

Result of running `core/backtest_engine.py` over the detector's signals. Recorded
here because the headline is negative, and negative results are the ones that get
quietly re-discovered six months later.

Reproduce with:

```bash
python scripts/run_obsidian_backtest.py --timeframe 15Min --years 1 --mode "EOD Focus"
python scripts/sweep_obsidian_exits.py  --timeframe 15Min --years 1 --mode "EOD Focus" --invert
```

Universe: 34 liquid optionable names across sectors. Costs 2 bps per side.
Entries fill on the next bar's open; when a bar spans both stop and target the
stop is assumed to fill first.

## Headline

**The detector's setups, traded mechanically as directional entries, do not
produce a profitable rule — in either direction, under any of 36 exit
configurations, on daily or 15-minute bars.**

This is a statement about mechanical trading of the signal. It is not a claim
that the indicator is useless as a discretionary context tool, and it is not a
claim that the port is wrong — the port was validated separately against 332
captured rows from the live product.

## What the numbers say

Profit factor. 1.0 is breakeven; the random-entry control is the bar that
matters, not 1.0.

| Timeframe | Mode | Follow labels | Fade labels | Random control |
|---|---|---|---|---|
| 1Day, 10y | Full Session | 0.957 | — | 0.976 |
| 15Min, 1y | Full Session | 0.784 | 0.776 | 0.770 |
| 15Min, 1y | **EOD Focus** | **0.627** | **0.953** | 0.782 |

Two things fall out of this table.

**Full Session mode carries no directional information at all.** Follow, fade and
random all land within 0.014 of each other over ~18,000 trades. Whatever the
detector fires on outside the closing window is noise.

**EOD Focus mode carries real directional information, pointing the wrong way.**
Over 1,628 trades, following the setup labels won 34.7% while fading them won
44.2% — a 9.5 point gap against a random control sitting at 38.9% in between. The
fade held at 44.2% on a 25% holdout that had no influence on finding it, and the
follow direction lost in all three training folds plus the holdout
(0.628 / 0.594 / 0.612 / 0.643). This is too stable and too large to be noise.

The plain reading: end-of-day momentum events in these names mean-revert. A
"floor bounce" 30 minutes before the close is, on average, followed by weakness,
not strength.

## But fading it is still not a strategy

Fading is better than random and better than following, and still loses money.
`scripts/sweep_obsidian_exits.py` searched 36 exit rules (stop 0.5–1.5 ATR,
target 1.0–3.0 ATR, hold 6–24 bars) on the training folds only:

* Fading the labels: **0 of 36** configs profitable in every training fold. Best
  worst-fold profit factor 0.959.
* Following the labels: **0 of 36**. Best worst-fold profit factor 0.625.

Because nothing cleared the training bar, **the holdout was never scored**.
Scoring it after the search failed would have turned the data reserved to keep
the search honest into a second search.

The gap between "carries directional information" and "is profitable" is
transaction costs plus the stop-before-target assumption. Both are deliberately
conservative and both are correct to keep.

## Bugs this exercise found

* **`MOMENTUM PUSH` is emitted for releases in both directions.** The backtest
  originally mapped setups to long/short by name, so roughly half of 580 daily
  trades were traded backwards. `BarState` now carries `setup_direction` and the
  engine uses it; the name table survives only as a fallback for older states.
  Fixing this moved daily `MOMENTUM PUSH` from −0.033% to −0.241% per trade — the
  mislabelled half had been cancelling out the loss.
* **A 288-bar history limit that did not exist.** An early smoke test reported
  profit factor 1.312 on 288 daily bars and was pure small-sample noise. 288 was
  the window the ad-hoc driver requested, not an Alpaca limit — the feed serves
  ~2,500 daily bars back to 2016. On the full history the same configuration
  gives 0.957.
* **`walk_forward` returned an empty report when history was too short**, which
  reads identically to "the strategy found no trades". It now warns and reduces
  the fold count.

## What is still not testable

GEX and cluster strategies. GEX is gamma × open interest, so it needs
point-in-time OI, and replaying today's OI over past prices is lookahead bias
that will manufacture edge. `data/gex_history.sqlite` holds only a handful of
universe-wide capture days. `scripts/gex_daily_capture.sh` now runs from cron at
15:00 ET on weekdays to accrue snapshots; a few months of those make cluster
strategies testable for the first time. (15:00 rather than nearer the close
because a full 546-ticker pass takes ~27 minutes, and starting at 15:50 would put
the tail of the snapshot after the bell, mixing pre- and post-close spot into one
capture.)

## If this is picked up again

Ordered by how much they could change the conclusion:

1. **Test the signal as a filter, not an entry.** Its information may live in
   "don't be long into the close" rather than "short into the close". Filters are
   evaluated by their effect on a base strategy's returns, which this engine does
   not yet support.
2. **Test it against the levels it was designed to be read with.** The live
   product shows these setups next to GEX walls. A collapse *at a gamma wall* is
   a different event from a collapse in open space, and that pairing is untestable
   until the OI history accrues.
3. **Options expression.** The detector's edge, if any, may be in volatility
   rather than direction — a collapse event is a volatility statement. Equity
   entries cannot capture that.
