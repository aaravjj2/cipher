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

## Filter-mode: the same detector, asked a weaker question

Everything above tests the detector as a **standalone entry trigger** — the hardest
question available, since the signal must beat random on timing, direction and
selection at once. Failing it cannot distinguish "carries no information" from
"carries information that is not an entry trigger", and those call for opposite
decisions.

`core/backtest_engine.py:run_filter` asks the weaker question: given trades you
were taking anyway, does the detector's state just before entry separate the good
ones from the bad? The base is deliberately dumb — a fixed-cadence long entry every
12 bars, no view on price — so any separation is attributable to the filter, not to
the base. Each partition is scored against **its own** matched random control,
because splitting a trade set enough ways always surfaces a flattering slice.

Reproduce: `python3 scripts/run_filter_backtest.py`

10 symbols, 15-minute bars, 1 year, EOD Focus, 6-bar lookback:

```
BASE (no filter)  n=12022 win=37.5% avg=-0.0534% PF=0.758

partition       n   share    win%      avg%  lift(pp)  verdict
bearish       159    1.3%    54.1    0.0471    0.1005  BEATS its own random control
bullish       138    1.1%    44.2   -0.0160    0.0374  within noise
none        11725   97.5%    37.2   -0.0552   -0.0018  within noise
```

**This is the first positive result in this document, and it is not an edge yet.**

What holds up. The effect survives lookback windows of 3, 6 and 12 bars and 8 of 9
control seeds, with lift steady at +0.09 to +0.10pp. The sign replicates in all
four disjoint time slices tried:

| slice | bear n | bear avg | lift | beats control |
|---|---|---|---|---|
| first half | 104 | +0.0587% | +0.11pp | yes |
| second half | 54 | +0.0251% | +0.08pp | no |
| train 75% | 130 | +0.0443% | +0.10pp | yes |
| holdout 25% | **27** | +0.0902% | +0.15pp | no |

It is also **coherent with the standalone finding**: the base is long-only, so
"a bearish signal preceding a long entry that then does better" is fading the
detector — the same direction as the 34.7%-follow versus 44.2%-fade result above.
An incoherent result would be more suspicious than a null one.

What does not. The control clears only where the sample is large (104, 130) and
fails where it is small (54, 27). That is low power, not a contradiction — but a
27-trade holdout supports no verdict at all. The partition is 1.3% of trades and
the effect is +0.09% per trade before any slippage beyond the modelled 2bps. Three
partitions were examined, so one clearing a control is roughly what chance allows.

Honest status: **promising, unestablished.** The useful part is not the number, it
is that the question is now separable — a standalone failure no longer collapses
"no information" and "wrong use of information" into the same verdict. The
sample needed to settle it is more sessions, not more configurations.

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

---

# Replication sweep: does the bearish partition survive being looked for elsewhere?

`scripts/sweep_filter_replication.py`, run 2026-08-08. The finding above was one
positive cell on one configuration, which is the shape a false positive takes. The
sweep moves three axes that are independent of it — a symbol set disjoint from the
original ten and drawn from other sectors, four bar timeframes, and both detector
modes — for 32 judgeable partitions.

The original configuration is included as a check on the harness, and reproduces
exactly: 10 symbols, 15Min, EOD Focus, bearish n=159, avg +0.0471%, lift +0.1005pp,
clears its control. The same numbers appear when run from the UI panel. The sweep is
therefore measuring what was reported.

## What replicated

The same configuration on ten **different** symbols also clears its control:
bearish n=153, avg +0.0164%, lift +0.0626pp. That is a genuine out-of-sample
survival on names the original never touched, and it is the strongest evidence this
repository has produced. The effect size roughly halves in the crossing.

Direction is asymmetric and the asymmetry is clean: **4 of 16 bearish partitions
clear their control, and 0 of 16 bullish ones do.** That pattern is harder to
explain by chance than any single cell, and it agrees with the fade result recorded
above.

## What did not

Nothing survives a change of timeframe. 5Min, 30Min and 1Hour all fail on both
symbol sets, in both detector modes. An edge that exists only at 15Min bars and
nowhere on either side of them is more consistent with a lucky bar alignment than
with a mechanism.

Overall 4 of 32 partitions clear, a 12.5% hit rate against the ~5% a control clears
by chance. `P(>=4 | 32, 0.05) = 0.074`, and the tests are **not** independent —
timeframes and detector modes re-slice the same underlying sessions, which inflates
the apparent hit count. So the sweep does not establish significance; it fails to
refute the finding, which is a weaker statement.

Note also that 2 of the 4 clearing partitions have a *negative* average return.
"Beats its control" there means losing less than random entry, not making money.

## The cost test, which is the decisive one

Cost applies equally to the partition and to its matched control, so it cancels in
the comparison: `beats_control_range` stays true at 2, 3, 4 and 5 bps per side, and
`lift_vs_base_pp` does not move at all. Absolute profitability is a different
question, and the modelled 2bps per side is optimistic:

```
set        cost/side     n      avg%   beats
original       2.0     159    0.0471   True
original       3.0     159    0.0271   True
original       4.0     159    0.0071   True
original       5.0     159   -0.0129   True
disjoint       2.0     153    0.0164   True
disjoint       3.0     153   -0.0036   True
disjoint       4.0     153   -0.0236   True
disjoint       5.0     153   -0.0436   True
```

Out of sample the edge is gone at 3bps per side. A one-basis-point change in the
cost assumption erases it. In sample it survives to just under 5bps.

## Verdict

Two claims, and they must not be merged:

- **There is a relative effect, and it replicated out of sample.** The bearish
  partition does better than random entry matched trade-for-trade, on symbols the
  finding was not derived from. This is not nothing.
- **There is no demonstrated tradeable edge.** It exists at one timeframe only, and
  out of sample it is smaller than the difference between a 2bp and a 3bp
  execution assumption.

Status stays **promising, unestablished** — but for a sharper reason than before.
The blocker is no longer sample size alone; it is that the effect is the same order
of magnitude as the execution cost, so no amount of additional equity-entry data
will settle it. Testing it where the size could be larger — at gamma walls once the
OI history accrues, or expressed in options where a collapse is a volatility
statement rather than a directional one — is the only path that changes the answer.

---

# Structural Fib: the published rates do not hold on the symbols it is claimed for

`scripts/backtest_structural_fib.py --symbols NVDA,AAPL --days 365`, run 2026-08-08.
Restricted to NVDA and AAPL because those are the two names the strategy is said to
work on; testing it on ten symbols would have invited the objection that the failure
was a universe problem.

```
                 n   touch%   claimed    win%      avg%
0.5 -> 1       704    80.0%       98%   74.6%   -0.050%
1   -> 2       470    49.6%       64%   44.0%   -0.050%
```

`touch%` is the charitable reading — did price ever reach the level before the close,
no stop, a whole session to wait. Even on that reading, which inflates hit rates
toward certainty, 98% measures 80% and 64% measures 49.6%.

The 1.5% pre-market rule, which is the strategy's own filter for which days to trade,
does not select better days: trending days score **77.6%** on the 0.5->1 leg against
80.0% for all signals. The filter the method is built around is not doing the work
attributed to it.

The more important number is the last column. A 74.6% win rate with a **negative**
average return means wins are small and losses are large — the profile of an
uncapped loss against a capped gain. A headline win rate that high is compatible
with losing money, and here it does.
